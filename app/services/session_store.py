import logging
from datetime import datetime,timezone
from app.services.db import pool

logger = logging.getLogger(__name__)

def init_tables():
    logger.info("Ensuring chat_sessions, chat_messages, session_documents tables exist...")
    with pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                document_id TEXT,
                filename TEXT,
                title TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages(
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources JSONB,
                created_at TIMESTAMPTZ NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_documents (
                session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                document_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'document',
                attached_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (session_id, document_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_documents (
                document_id TEXT PRIMARY KEY,
                file_path   TEXT NOT NULL,
                filename    TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'document'
            )
        """)
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS document_focus JSONB NOT NULL DEFAULT '[]'::jsonb")
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS pending_document_request JSONB")
    logger.info("Tables ready")

def upsert_session(session_id: str, title: str):
    now = datetime.now(timezone.utc)
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO chat_sessions (session_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
            """, (session_id, title, now, now))
    except Exception:
        logger.exception("session=%s upsert_session FAILED (title=%r)", session_id, title)
        raise
    logger.debug("session=%s upserted (title=%r if new)", session_id, title)

def attach_document(session_id: str, document_id: str, filename: str, content_type: str = "document"):
    now = datetime.now(timezone.utc)
    try:
        with pool.connection() as conn:
            conn.execute("""
                INSERT INTO session_documents (session_id, document_id, filename, content_type, attached_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id, document_id) DO NOTHING
            """, (session_id, document_id, filename, content_type, now))
            import json
            conn.execute("UPDATE chat_sessions SET updated_at = %s, document_focus = %s::jsonb WHERE session_id = %s",
                         (now, json.dumps([document_id]), session_id))
    except Exception:
        logger.exception("session=%s attach_document FAILED for document_id=%s filename=%r", session_id, document_id, filename)
        raise
    logger.info("PDF/file attached: %s", filename, extra={"chat": session_id})

def remove_document(session_id: str, document_id: str):
    now = datetime.now(timezone.utc)
    try:
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM session_documents WHERE session_id = %s AND document_id = %s",
                (session_id, document_id),
            )
            conn.execute("UPDATE chat_sessions SET updated_at = %s, document_focus = document_focus - %s WHERE session_id = %s",
                         (now, document_id, session_id))
    except Exception:
        logger.exception("session=%s remove_document FAILED for document_id=%s", session_id, document_id)
        raise
    logger.info("session=%s removed document_id=%s", session_id, document_id)

def list_session_documents(session_id: str) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT document_id, filename, content_type FROM session_documents WHERE session_id = %s ORDER BY attached_at",
            (session_id,),
        ).fetchall()
    return [{"document_id": r[0], "filename": r[1], "content_type": r[2]} for r in rows]


def get_document_focus(session_id):
    with pool.connection() as conn:
        row = conn.execute("SELECT document_focus FROM chat_sessions WHERE session_id = %s", (session_id,)).fetchone()
    return row[0] if row else []


def get_pending_document_request(session_id):
    with pool.connection() as conn:
        row = conn.execute("SELECT pending_document_request FROM chat_sessions WHERE session_id = %s", (session_id,)).fetchone()
    return row[0] if row else None


def set_pending_document_request(session_id, value):
    import json
    with pool.connection() as conn:
        conn.execute("UPDATE chat_sessions SET pending_document_request = %s::jsonb WHERE session_id = %s",
                     (json.dumps(value) if value is not None else None, session_id))


def set_document_focus(session_id, document_ids):
    import json
    attached = {d["document_id"] for d in list_session_documents(session_id)}
    if not set(document_ids) <= attached:
        raise ValueError("Document selection contains a file outside this chat")
    with pool.connection() as conn:
        conn.execute("UPDATE chat_sessions SET document_focus = %s::jsonb WHERE session_id = %s",
                     (json.dumps(document_ids), session_id))

def add_message(session_id: str, role: str, content: str, sources: list[dict] | None = None):
    import json
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, sources, created_at) VALUES (%s, %s, %s, %s, %s)",
                (session_id, role, content, json.dumps(sources) if sources else None, datetime.now(timezone.utc)),
            )
    except Exception:
        logger.exception("session=%s add_message FAILED (role=%s, %d source(s))", session_id, role, len(sources or []))
        raise
    logger.debug("session=%s message added (role=%s, %d char(s), %d source(s))", session_id, role, len(content), len(sources or []))

def list_sessions(prefix: str = "") -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute("""
            SELECT s.session_id, s.title, s.created_at, s.updated_at,
                   COUNT(DISTINCT m.id) FILTER (WHERE m.role = 'user') AS message_count,
                   COUNT(DISTINCT sd.document_id) AS document_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id = s.session_id
            LEFT JOIN session_documents sd ON sd.session_id = s.session_id
            WHERE s.session_id LIKE %s
            GROUP BY s.session_id ORDER BY s.updated_at DESC
        """, (prefix + "%",)).fetchall()
    cols = ["session_id", "title", "created_at", "updated_at", "message_count", "document_count"]
    logger.debug("list_sessions: %d session(s)", len(rows))
    return [dict(zip(cols, row)) for row in rows]

def get_session(session_id: str) -> dict | None:
    with pool.connection() as conn:
        session_row = conn.execute(
            "SELECT session_id, title, created_at, updated_at FROM chat_sessions WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        if session_row is None:
            logger.warning("session=%s not found", session_id)
            return None
        message_rows = conn.execute(
            "SELECT role, content, sources, created_at FROM chat_messages WHERE session_id = %s ORDER BY id",
            (session_id,),
        ).fetchall()

    cols = ["session_id", "title", "created_at", "updated_at"]
    session = dict(zip(cols, session_row))
    session["messages"] = [
        {"role": r[0], "content": r[1], "sources": r[2], "created_at": r[3]} for r in message_rows
    ]
    session["message_count"] = sum(1 for m in session["messages"] if m["role"] == "user")
    session["documents"] = list_session_documents(session_id)
    session["document_count"] = len(session["documents"])
    logger.debug("session=%s loaded: %d message(s), %d document(s)", session_id, len(session["messages"]), session["document_count"])
    return session

def delete_session(session_id: str):
    try:
        with pool.connection() as conn:
            conn.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))  # cascades to chat_messages, session_documents
    except Exception:
        logger.exception("session=%s delete_session FAILED", session_id)
        raise
    logger.info("session=%s deleted", session_id)
