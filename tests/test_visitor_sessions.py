from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.api import chat, sessions, upload, visitor
from app.services import visitor_scope
from app.services.pending_documents import discard_document
from app.services.session_store import init_tables


def test_demo_visitors_cannot_access_each_others_sessions_or_uploads(monkeypatch):
    init_tables()
    monkeypatch.setattr(visitor_scope, "DEMO_MODE", True)
    monkeypatch.setattr(visitor, "DEMO_MODE", True)
    app = FastAPI()
    app.include_router(visitor.router)
    app.include_router(sessions.router)
    app.include_router(upload.router)
    app.include_router(chat.router)
    app.state.checkpointer = AsyncMock()
    app.state.agent = AsyncMock()
    app.state.agent.ainvoke.return_value = {"messages": [AIMessage(content="Demo answer")]}
    session_id = str(uuid4())

    with TestClient(app, base_url="https://demo.example") as first, TestClient(
        app, base_url="https://demo.example"
    ) as second:
        assert first.get("/sessions").status_code == 401
        response = first.get("/visitor")
        assert response.status_code == 204
        assert {"Secure", "HttpOnly", "SameSite=lax", "Path=/"} <= {
            part.strip() for part in response.headers["set-cookie"].split(";")
        }
        assert "set-cookie" not in first.get("/visitor").headers
        assert second.get("/visitor").status_code == 204
        first_token = first.cookies.get(visitor_scope.COOKIE_NAME)
        second_token = second.cookies.get(visitor_scope.COOKIE_NAME)
        assert first_token != second_token
        first_prefix = visitor_scope.visitor_namespace(first_token)
        second_prefix = visitor_scope.visitor_namespace(second_token)
        image = b"\x89PNG\r\n\x1a\n"
        result = first.post("/upload", files={"file": ("example.png", image, "image/png")})
        assert result.status_code == 200
        document_id = result.json()["document_id"]
        assert document_id.startswith(first_prefix)
        asset_url = f"/data/uploads/{document_id}.png"
        attachment = {
            "document_id": document_id,
            "filename": "spoofed.pdf",
            "content_type": "document",
        }
        url = f"/sessions/{session_id}"
        try:
            result = first.post(f"{url}/documents", json=attachment)
            assert result.status_code == 200
            assert result.json()[0]["filename"] == "example.png"
            assert [row["session_id"] for row in first.get("/sessions").json()] == [session_id]
            assert first.get(url).json()["session_id"] == session_id
            assert second.get("/sessions").json() == []
            assert second.get(url).status_code == 404
            assert second.post(f"{url}/documents", json=attachment).status_code == 404
            assert second.delete(f"{url}/documents/{document_id}").status_code == 404
            assert first.get(asset_url).content == image
            assert second.get(asset_url).status_code == 404
            assert second.get(f"/data/figures/{document_id}_figure.png").status_code == 404
            assert second.delete(url).status_code == 204
            app.state.checkpointer.adelete_thread.assert_awaited_once_with(second_prefix + session_id)
            assert first.get(url).status_code == 200

            for client, prefix, question in (
                (first, first_prefix, "Hello from the first visitor"),
                (second, second_prefix, "Hello from the second visitor"),
            ):
                answer = client.post("/ask", json={"session_id": session_id, "question": question})
                assert answer.status_code == 200
                assert answer.json()["session_id"] == session_id
                config = app.state.agent.ainvoke.await_args.kwargs["config"]
                assert config["configurable"]["thread_id"] == prefix + session_id
                assert [message["content"] for message in client.get(url).json()["messages"]] == [
                    question, "Demo answer",
                ]
            assert first.get(url).json()["documents"][0]["document_id"] == document_id
            assert second.get(url).json()["documents"] == []
        finally:
            first.delete(url)
            second.delete(url)
            discard_document(document_id)
            (upload.UPLOAD_DIR / f"{document_id}.png").unlink(missing_ok=True)
