"""Tests for the /sessions endpoints via FastAPI's TestClient.

These hit the real local Postgres the app already uses (same requirement as
running the app itself) — they never touch Groq or Chroma. Each test creates
its own uuid4 session/document ids and deletes the session in a finally
block, so nothing lingers in your real session list.
"""
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_attach_document_creates_the_session_and_lists_it():
    session_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    try:
        res = client.post(f"/sessions/{session_id}/documents", json={
            "document_id": document_id,
            "filename": "example.pdf",
            "content_type": "document",
        })
        assert res.status_code == 200
        documents = res.json()
        assert documents == [{"document_id": document_id, "filename": "example.pdf", "content_type": "document"}]

        res = client.get(f"/sessions/{session_id}")
        assert res.status_code == 200
        detail = res.json()
        assert detail["document_count"] == 1
        assert detail["documents"][0]["filename"] == "example.pdf"
        assert detail["messages"] == []

        res = client.get("/sessions")
        assert res.status_code == 200
        assert any(s["session_id"] == session_id for s in res.json())
    finally:
        client.delete(f"/sessions/{session_id}")


def test_attach_two_documents_pools_them_under_one_session():
    session_id = str(uuid.uuid4())
    try:
        for filename in ("a.pdf", "b.png"):
            client.post(f"/sessions/{session_id}/documents", json={
                "document_id": str(uuid.uuid4()),
                "filename": filename,
                "content_type": "image" if filename.endswith(".png") else "document",
            })

        res = client.get(f"/sessions/{session_id}")
        assert res.json()["document_count"] == 2
    finally:
        client.delete(f"/sessions/{session_id}")


def test_remove_attached_document_clears_it():
    session_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    try:
        client.post(f"/sessions/{session_id}/documents", json={"document_id": document_id, "filename": "a.pdf"})

        res = client.delete(f"/sessions/{session_id}/documents/{document_id}")
        assert res.status_code == 200
        assert res.json() == []

        assert client.get(f"/sessions/{session_id}").json()["document_count"] == 0
    finally:
        client.delete(f"/sessions/{session_id}")


def test_remove_a_document_never_attached_returns_404():
    session_id = str(uuid.uuid4())
    try:
        client.post(f"/sessions/{session_id}/documents", json={"document_id": str(uuid.uuid4()), "filename": "a.pdf"})
        res = client.delete(f"/sessions/{session_id}/documents/{uuid.uuid4()}")
        assert res.status_code == 404
    finally:
        client.delete(f"/sessions/{session_id}")


def test_get_unknown_session_returns_404():
    res = client.get(f"/sessions/{uuid.uuid4()}")
    assert res.status_code == 404


def test_delete_session_removes_it_from_the_list():
    session_id = str(uuid.uuid4())
    client.post(f"/sessions/{session_id}/documents", json={"document_id": str(uuid.uuid4()), "filename": "a.pdf"})

    res = client.delete(f"/sessions/{session_id}")
    assert res.status_code == 204

    assert client.get(f"/sessions/{session_id}").status_code == 404
