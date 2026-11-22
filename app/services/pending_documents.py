import logging
from app.services.db import pool

logger = logging.getLogger(__name__)


def peek_pending_upload(document_id):
    """Read the file location without consuming the later search-indexing job."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT file_path, filename, content_type FROM pending_documents WHERE document_id = %s",
            (document_id,),
        ).fetchone()
    return dict(zip(("file_path", "filename", "content_type"), row)) if row else None

def register_upload(document_id, file_path, filename, content_type="document"):
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO pending_documents (document_id, file_path, filename, content_type) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (document_id) DO UPDATE "
            "SET file_path=EXCLUDED.file_path, filename=EXCLUDED.filename, content_type=EXCLUDED.content_type",
            (document_id, str(file_path), filename, content_type),  # ← str() goes HERE
        )
    logger.debug("document_id=%s registered as pending (%s)", document_id, content_type)

def pop_pending_upload(document_id):
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM pending_documents WHERE document_id = %s "
            "RETURNING file_path, filename, content_type",
            (document_id,),
        ).fetchone()
    if row:
        logger.debug("document_id=%s popped from pending for processing", document_id)
        return {"file_path": row[0], "filename": row[1], "content_type": row[2]}
    return None

def discard_document(document_id):
    with pool.connection() as conn:
        result = conn.execute(
            "DELETE FROM pending_documents WHERE document_id = %s RETURNING document_id",
            (document_id,),
        ).fetchone()
    logger.debug("document_id=%s discarded (was_pending=%s)", document_id, result is not None)
