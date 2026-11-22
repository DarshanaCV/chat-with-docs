import logging
import time
from app.logging_setup import progress

from app.services.ingestion.loader import read_pdf
from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.embeddings import create_embeddings
from app.services.ingestion.image_extractor import process_images, _generate_description
from app.services.vector_store import store_chunks

logger = logging.getLogger(__name__)


def process_and_store_document(document_id: str, file_path, filename: str, *, pages=None, include_visuals=False, visuals_only=False) -> dict:
    """Runs the full extraction pipeline (text, tables, figures, embeddings)
    and writes the result straight into Chroma. Deliberately not called from
    /upload — it only runs the first time a question actually needs this
    document, so attaching a file stays instant.
    """
    logger.info("Preparing PDF: %s | document=%s", filename, document_id)
    t_start = time.time()

    try:
        if pages is None:
            with progress(logger, "Reading PDF text and caption positions"):
                pages = read_pdf(file_path)
        else:
            logger.debug("Using text already extracted during document preparation")
    except Exception:
        logger.exception("document_id=%s read_pdf FAILED for %s", document_id, file_path)
        raise
    chunks = []
    page_numbers = []
    for page in ([] if visuals_only else pages):
        page_chunks = chunk_text(page["text"])
        chunks.extend(page_chunks)
        page_numbers.extend([page["page_number"]] * len(page_chunks))
    logger.info("PDF text ready | %d pages | %d passages | %.1fs", len(pages), len(chunks), time.time() - t_start)

    t = time.time()
    try:
        if include_visuals:
            with progress(logger, "Extracting figures and tables"):
                figures, tables = process_images(file_path, document_id=document_id)
        else:
            figures, tables = [], []
            logger.info("Image and table analysis skipped; runs only when a question needs it")
    except Exception:
        logger.exception("document_id=%s process_images (tables/figures) FAILED", document_id)
        raise
    image_chunks = [fig["text"] for fig in figures]
    image_metadata = [{k: v for k, v in fig.items() if k != "text"} for fig in figures]
    table_chunks = [t_["text"] for t_ in tables]
    table_pages = [t_["page_number"] for t_ in tables]
    table_numbers = [t_["table_number"] for t_ in tables]
    table_captions = [t_["caption"] for t_ in tables]
    table_rows = [t_["rows"] for t_ in tables]
    if include_visuals:
        logger.info("Found %d figures and %d tables | %.1fs", len(figures), len(tables), time.time() - t)

    t = time.time()
    all_chunks = chunks + table_chunks + image_chunks
    if not all_chunks:
        if visuals_only:
            return {"number_of_chunks": 0, "number_of_images": 0}
        raise ValueError("PDF has no readable text; OCR is required")
    try:
        with progress(logger, f"Creating searchable embeddings for {len(all_chunks)} passages"):
            embeddings = create_embeddings(all_chunks)
    except Exception:
        logger.exception("document_id=%s create_embeddings FAILED for %d chunks", document_id, len(all_chunks))
        raise
    logger.debug("Created embeddings for %d passages in %.1fs", len(all_chunks), time.time() - t)

    t = time.time()
    logger.info("Saving document passages to the search database")
    try:
        store_chunks(
            table_chunks=table_chunks,
            chunks=chunks,
            embeddings=embeddings,
            document_id=document_id,
            filename=filename,
            page_numbers=page_numbers,
            table_pages=table_pages,
            table_numbers=table_numbers,
            table_captions=table_captions,
            table_rows=table_rows,
            image_chunks=image_chunks,
            image_metadata=image_metadata,
        )
    except Exception:
        logger.exception("document_id=%s store_chunks (Chroma write) FAILED", document_id)
        raise
    logger.info("PDF ready for questions | total preparation %.1fs", time.time() - t_start)

    return {"number_of_chunks": len(all_chunks), "number_of_images": len(figures)}


def process_and_store_image(document_id: str, file_path, filename: str) -> dict:
    """A standalone uploaded image has no PDF structure to walk — it's
    captioned directly, the same way a PDF figure is described, and stored
    as a single searchable chunk.
    """
    logger.info("document_id=%s starting image pipeline for %r", document_id, filename)
    t_start = time.time()
    try:
        with progress(logger, "Reading the uploaded image"):
            description = _generate_description(file_path, caption=None) or f"Image: {filename} (no description available)."
    except Exception:
        logger.exception("document_id=%s image captioning FAILED for %s — storing a placeholder instead", document_id, file_path)
        description = f"Image: {filename} (no description available)."
    logger.debug("document_id=%s caption: %r", document_id, description)

    try:
        embeddings = create_embeddings([description])
        store_chunks(
            table_chunks=[],
            chunks=[],
            embeddings=embeddings,
            document_id=document_id,
            filename=filename,
            page_numbers=[],
            table_pages=[],
            table_numbers=[],
            table_captions=[],
            table_rows=[],
            image_chunks=[description],
            image_metadata=[{"image_path": str(file_path), "page_number": 1, "caption": "", "bbox": []}],
        )
    except Exception:
        logger.exception("document_id=%s failed to embed/store the image chunk", document_id)
        raise
    logger.info("document_id=%s process_and_store_image done in %.2fs", document_id, time.time() - t_start)
    return {"number_of_chunks": 1, "number_of_images": 1}
