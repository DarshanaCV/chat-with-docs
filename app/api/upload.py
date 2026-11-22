import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from app.services.pending_documents import register_upload
from app.services.visitor_scope import visitor_prefix

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@router.post("/upload")
def upload_file(file: UploadFile = File(...), prefix: str = Depends(visitor_prefix)):
    document_id = prefix + str(uuid.uuid4())
    logger.debug("Upload received: filename=%r content_type=%r -> document_id=%s", file.filename, file.content_type, document_id)

    # Different uploads with the same filename must not overwrite each other.
    suffix = Path(file.filename or "").suffix.lower()
    if prefix and suffix not in IMAGE_EXTENSIONS | {".pdf"}:
        raise HTTPException(status_code=415, detail="Upload a PDF or a supported image")
    file_path = UPLOAD_DIR / f"{document_id}{suffix}"
    content_type = "image" if suffix in IMAGE_EXTENSIONS else "document"
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        size_bytes = file_path.stat().st_size
        register_upload(document_id, file_path, file.filename, content_type=content_type)
    except Exception:
        logger.exception("Failed to save uploaded file %r to %s", file.filename, file_path)
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not clean up incomplete upload %s", document_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not save the uploaded file")

    logger.debug("Saved %s (%d bytes) as %s", file_path, size_bytes, content_type)
    logger.info("Upload saved: %s | text processing starts with your first question", file.filename)

    return {
        "message": "File uploaded successfully",
        "filename": file.filename,
        "document_id": document_id,
        "content_type": content_type,
    }
