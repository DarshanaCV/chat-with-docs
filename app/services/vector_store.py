import json
import logging
from app.logging_setup import progress
import chromadb
from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

client = chromadb.PersistentClient(path='data/chroma')
collection = client.get_or_create_collection(name='documents')


def store_chunks(table_chunks,chunks,embeddings,document_id,filename,page_numbers,table_pages,table_numbers,image_chunks,image_metadata,table_captions=None,table_rows=None,):
    image_chunks = image_chunks or []
    image_metadata = image_metadata or []

    if len(image_chunks) != len(image_metadata):
        raise ValueError("image_chunks and image_metadata must be the same length")

    all_chunks = chunks + table_chunks + image_chunks
    logger.debug("document_id=%s store_chunks: %d text, %d table, %d figure/image chunk(s)", document_id, len(chunks), len(table_chunks), len(image_chunks))

    Ids = (
        [f"{document_id}_chunk_{i}" for i in range(len(chunks))]
        + [f"{document_id}_table_{i}" for i in range(len(table_chunks))]
        + [f"{document_id}_figure_{i}" for i in range(len(image_chunks))]
    )

    text_metadatas = [
        {"document_id": document_id, "filename": filename, "chunk_index": i, "chunk_id": f"{document_id}_chunk_{i}","content_type": "text", "page_number":page_numbers[i]}
        for i in range(len(chunks))
    ]

    table_captions = table_captions or [""] * len(table_chunks)
    table_rows = table_rows or [None] * len(table_chunks)
    table_metadatas = [
        {"document_id": document_id, "filename": filename, "chunk_index": f"table_{i}","chunk_id": f"{document_id}_table_{i}", "content_type": "table", "page_number": table_pages[i],"table_number": table_numbers[i], "caption": table_captions[i], "rows_json": json.dumps(table_rows[i]) if table_rows[i] else ""}
        for i in range(len(table_chunks))
    ]

    figure_metadatas = []
    for i, meta in enumerate(image_metadata):
        entry = {"document_id": document_id,"filename": filename,"chunk_index": f"figure_{i}","chunk_id": f"{document_id}_figure_{i}","content_type": "figure",
            "image_path": meta.get("image_path", ""),"page_number": meta.get("page_number", -1),"caption": meta.get("caption", ""),"bbox": str(meta.get("bbox", []))
        }
        figure_metadatas.append(entry)

    metadatas = text_metadatas + table_metadatas + figure_metadatas

    try:
        collection.upsert(
            ids=Ids,
            documents=all_chunks,
            embeddings=embeddings.tolist(),
            metadatas=metadatas,
        )
    except Exception:
        logger.exception("document_id=%s Chroma collection.add FAILED for %d chunk(s)", document_id, len(all_chunks))
        raise
    logger.info("document_id=%s stored %d chunk(s) in Chroma", document_id, len(all_chunks))


def delete_document(document_id):
    try:
        collection.delete(where={"document_id": document_id})
        logger.info("document_id=%s deleted from Chroma (no-op if it was never stored)", document_id)
    except Exception:
        logger.exception("document_id=%s Chroma delete FAILED", document_id)
        raise


def search_chunks(query_embedding, document_ids, n_results=10):
    """document_ids is a list — one pooled Chroma query across every document
    attached to the session, so reranking (not per-document quotas) decides
    which document's chunks actually surface."""
    logger.debug("search_chunks: document_ids=%s n_results=%d", document_ids, n_results)
    results = collection.query(
        query_embeddings=query_embedding.tolist(),
        n_results=n_results,
        where={"document_id": {"$in": document_ids}},
    )
    logger.debug("search_chunks: got %d result(s)", len(results["documents"][0]) if results["documents"] else 0)
    return results


with progress(logger, "Loading the passage ranking model"):
    reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')

def rerank_chunks(question, chunks, metadatas, top_k=5):
    pairs = [[question, chunk] for chunk in chunks]
    scores = reranker.predict(pairs)
    ranked = sorted(zip(chunks,metadatas, scores), key=lambda x: x[2], reverse=True)
    top = ranked[:top_k]
    logger.debug("rerank_chunks: %d candidate(s) -> top %d, scores=%s", len(chunks), len(top), [round(float(s), 3) for _, _, s in top])
    return [{"chunk": r[0], "metadata": r[1], "score": r[2]} for r in top]
