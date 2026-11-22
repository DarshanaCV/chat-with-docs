import base64
import logging
import os
import time
import uuid
from pathlib import Path

import pymupdf
import pdfplumber
from huggingface_hub import hf_hub_download
from doclayout_yolo import YOLOv10
from app.services.gemini_gateway import complete, model_id
from app.services.visitor_scope import DEMO_MODE
from dotenv import load_dotenv

from app.services.ingestion.table_extractor import table_to_text
from app.logging_setup import progress

logger = logging.getLogger(__name__)

load_dotenv()


_layout_model = None
_layout_lock = __import__("threading").Lock()

def _get_layout_model():
    global _layout_model
    with _layout_lock:
        if _layout_model is None:
            with progress(logger, "Loading the PDF layout model for visual analysis"):
                path = hf_hub_download(repo_id="juliozhao/DocLayout-YOLO-DocStructBench",
                    filename="doclayout_yolo_docstructbench_imgsz1024.pt")
                _layout_model = YOLOv10(path)
    return _layout_model


def _render_page(page, output_path, dpi=150):  # was 300 — YOLO resizes to 1024px anyway
    pix = page.get_pixmap(dpi=dpi)
    pix.save(output_path)
    return pix

def _detect_layout(image_path):
    results = _get_layout_model().predict(str(image_path), imgsz=1024, conf=0.3, verbose=False)
    return results[0]

def _get_figure_regions(results):
    figures = []
    for i in range(len(results.boxes)):
        class_id = results.boxes.cls[i].item()
        class_name = results.names[class_id]
        confidence = results.boxes.conf[i].item()
        if class_name.lower() in ("picture", "figure"):
            figures.append({
                "bbox": results.boxes.xyxy[i].tolist(),
                "confidence": confidence,
            })
    return figures

def _get_table_regions(results):
    """Splits table-related detections into table bodies and their captions,
    reusing the layout model's own "table"/"table_caption" classes instead of
    relying on pdfplumber's line-based table detection (which misses
    booktabs-style tables that have no vertical grid lines).
    """
    tables, captions = [], []
    for i in range(len(results.boxes)):
        class_id = results.boxes.cls[i].item()
        class_name = results.names[class_id]
        confidence = results.boxes.conf[i].item()
        entry = {"bbox": results.boxes.xyxy[i].tolist(), "confidence": confidence}
        if class_name == "table":
            tables.append(entry)
        elif class_name == "table_caption":
            captions.append(entry)
    return tables, captions

def _to_pdf_rect(page, bbox, page_image_size):
    x1, y1, x2, y2 = bbox
    image_width, image_height = page_image_size
    scale_x = page.rect.width / image_width
    scale_y = page.rect.height / image_height
    return pymupdf.Rect(x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y)

def _match_table_caption(page, table_rect, caption_rects, max_gap=100):
    best_rect, best_gap = None, max_gap
    for caption_rect in caption_rects:
        gap = table_rect.y0 - caption_rect.y1
        if 0 <= gap < best_gap:
            best_gap = gap
            best_rect = caption_rect
    if best_rect is None:
        return None
    return page.get_textbox(best_rect).strip() or None

def _extract_table_text(pdf_page, rect):
    """Tries a grid extraction first, falling back through looser strategies
    since booktabs-style tables (horizontal rules only, no vertical lines)
    need vertical_strategy="text" to have their columns detected at all.

    Returns (text, rows): `text` is the flattened pipe-joined form used for
    embedding/search either way; `rows` is the original list[list[str]] grid
    when one was found, so the frontend can render a real <table> instead of
    just the flattened text — None when only the raw-text fallback applied.
    """
    x0, y0 = max(0, rect.x0), max(0, rect.y0)
    x1, y1 = min(pdf_page.width, rect.x1), min(pdf_page.height, rect.y1)
    if x1 <= x0 or y1 <= y0:
        return None, None
    cropped = pdf_page.within_bbox((x0, y0, x1, y1))
    for settings in ({}, {"vertical_strategy": "text", "horizontal_strategy": "lines"}, {"vertical_strategy": "text", "horizontal_strategy": "text"}):
        table = cropped.extract_table(table_settings=settings)
        if table and len(table) >= 2 and any(any(cell for cell in row) for row in table):
            rows = [[str(cell).strip() if cell else "" for cell in row] for row in table]
            return table_to_text(table), rows
    text = cropped.extract_text()
    return (text.strip() if text else None), None

def _merge_nearby_boxes(figures, x_gap=30, y_gap=60):
    """
    Merges figure boxes that are close together on the same page into one
    logical figure. Fixes both duplicate captions (same figure detected as
    multiple boxes) and missed captions (a caption gets attached to only
    one of several fragments instead of the merged whole).
    """
    if not figures:
        return figures

    boxes = [f["bbox"] for f in figures]
    confidences = [f["confidence"] for f in figures]
    used = [False] * len(boxes)
    merged = []

    for i in range(len(boxes)):
        if used[i]:
            continue
        x1, y1, x2, y2 = boxes[i]
        conf = confidences[i]
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j in range(len(boxes)):
                if used[j]:
                    continue
                bx1, by1, bx2, by2 = boxes[j]
                close_x = bx1 <= x2 + x_gap and x1 <= bx2 + x_gap
                close_y = by1 <= y2 + y_gap and y1 <= by2 + y_gap
                if close_x and close_y:
                    x1, y1 = min(x1, bx1), min(y1, by1)
                    x2, y2 = max(x2, bx2), max(y2, by2)
                    conf = max(conf, confidences[j])
                    used[j] = True
                    changed = True
        merged.append({"bbox": [x1, y1, x2, y2], "confidence": conf})
    return merged

def _validate_figure(bbox, confidence, threshold=0.3):
    if confidence < threshold:
        return False
    x1, y1, x2, y2 = bbox
    return (x2 - x1) >= 50 and (y2 - y1) >= 50

def _crop_figure(page, bbox, page_image_size, output_path, zoom=2):
    x1, y1, x2, y2 = bbox
    image_width, image_height = page_image_size
    scale_x = page.rect.width / image_width
    scale_y = page.rect.height / image_height
    pdf_bbox = pymupdf.Rect(x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pdf_bbox)
    pix.save(output_path)
    return pdf_bbox

def _get_caption(page, figure_bbox):
    blocks = page.get_text("blocks")
    x1, y1, x2, y2 = figure_bbox
    captions = []
    for block in blocks:
        bx1, by1, bx2, by2, text = block[:5]
        text_lower = text.lower()
        if "figure" not in text_lower and "fig." not in text_lower:
            continue
        if by1 >= y2:
            distance = by1 - y2
            if distance < 150:
                captions.append({"text": text.strip(), "distance": distance})
    if captions:
        captions.sort(key=lambda x: x["distance"])
        return captions[0]["text"]
    return None

def _encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def _generate_description(image_path, caption=None, question=None, model=None, max_retries=1):
    if question:
        prompt = (
            f"You are analyzing a figure from a research paper.\n"
            f"Answer this specific question about the figure: {question}\n"
            f"Use only what is visible in the image."
            f"{f' Caption: {caption}' if caption else ''}"
        )
    else:
        prompt = (
            "You are analyzing a figure from a research paper for a RAG system.\n"
            "Describe the figure using ONLY what is visible in the image or given in the caption.\n\n"
            "Include:\n- What the figure represents\n- Key components, labels, and relationships\n"
            "- Any numbers or values visible\n- Comparisons or trends shown\n- The main takeaway\n\n"
            "Do NOT invent information.\n\n"
            f"Figure caption: {caption or 'No caption available.'}"
        )
    response = complete({
        "model": model or os.getenv("GEMINI_VISION_MODEL") or model_id(),
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_encode_image(image_path)}"}},
        ]}],
        "temperature": 0.2, "max_completion_tokens": 2048,
    })
    answer = response.choices[0].message.content
    if response.choices[0].finish_reason == "length" or not answer or not answer.strip():
        raise ValueError("Gemini returned an incomplete figure explanation. Please retry.")
    return answer.strip()


def process_images(file_path, document_id, output_dir="data/figures"):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(file_path)
    pdf_doc = pdfplumber.open(file_path)
    t_detect = time.time()
    crop_jobs = []  # (crop_path, caption, page_number, bbox) — VLM calls deferred to run in parallel
    table_results = []

    for page_number in range(len(doc)):
        if page_number == 0 or (page_number + 1) % 5 == 0 or page_number + 1 == len(doc):
            logger.info("Scanning figures and tables | page %d/%d", page_number + 1, len(doc))
        page = doc[page_number]
        actual_page_number = page_number + 1

        page_render_path = output_dir / f"{document_id}_page{actual_page_number}.png"
        pix = _render_page(page, page_render_path)
        page_image_size = (pix.width, pix.height)

        layout = _detect_layout(page_render_path)
        candidates = _get_figure_regions(layout)
        candidates = _merge_nearby_boxes(candidates)  # fixes duplicate/split figure boxes

        for fig in candidates:
            try:
                if not _validate_figure(fig["bbox"], fig["confidence"]):
                    continue
                fig_id = uuid.uuid4().hex[:8]
                crop_path = output_dir / f"{document_id}_p{actual_page_number}_{fig_id}.png"
                pdf_bbox = _crop_figure(page, fig["bbox"], page_image_size, crop_path)
                caption = _get_caption(page, pdf_bbox)
                crop_jobs.append({
                    "crop_path": crop_path,
                    "caption": caption,
                    "page_number": actual_page_number,
                    "bbox": list(pdf_bbox),
                })
            except Exception:
                logger.exception("skipped a figure on page %d (document_id=%s)", actual_page_number, document_id)
                continue

        table_boxes, caption_boxes = _get_table_regions(layout)
        table_rects = [
            _to_pdf_rect(page, t["bbox"], page_image_size)
            for t in table_boxes if _validate_figure(t["bbox"], t["confidence"])
        ]
        caption_rects = [_to_pdf_rect(page, c["bbox"], page_image_size) for c in caption_boxes]
        pdf_page = pdf_doc.pages[page_number]
        for table_index, table_rect in enumerate(table_rects):
            try:
                caption = _match_table_caption(page, table_rect, caption_rects)
                text, rows = _extract_table_text(pdf_page, table_rect)
                if not text:
                    continue
                table_results.append({
                    "text": f"{caption + '. ' if caption else ''}{text}",
                    "page_number": actual_page_number,
                    "table_number": table_index,
                    "caption": caption or "",
                    "bbox": list(table_rect),
                    "document_id": document_id,
                    "content_type": "table",
                    "rows": rows,
                })
            except Exception:
                logger.exception("skipped a table on page %d (document_id=%s)", actual_page_number, document_id)
                continue

        page_render_path.unlink(missing_ok=True)
    doc.close()
    pdf_doc.close()
    logger.info("document_id=%s detect+crop: %d figure(s), %d table(s) across %d page(s) in %.2fs", document_id, len(crop_jobs), len(table_results), page_number + 1, time.time() - t_detect)
    results = []
    for job in crop_jobs:
        text = job["caption"] or f"Figure on page {job['page_number']}"
        results.append({
            "text": text,
            "image_path": str(job["crop_path"]),
            "page_number": job["page_number"],
            "bbox": job["bbox"],
            "caption": job["caption"] or "",
            "document_id": document_id,
            "content_type": "figure",
        })
    logger.info("document_id=%s stored %d figure(s) (metadata only, no LLM)", document_id, len(results))

    return results, table_results


def crop_numbered_figure(file_path, document_id, page_number, caption, captions):
    """Detect only the requested page; match before cropping or calling Gemini."""
    import hashlib
    from app.services.ingestion.figure_captions import match_figure
    from app.services.summary_cache import SummaryCache
    output = Path("data/figures")
    output.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{document_id}:{page_number}:{caption}".encode()).hexdigest()[:24]
    if DEMO_MODE:
        name = f"{document_id}_{name}"
    rendered = output / f"{name}_page.png"
    target = output / f"{name}_figure.png"
    logger.info("Locating Figure %s | page %d only", caption["figure_number"], page_number)
    with pymupdf.open(file_path) as doc:
        page = doc[page_number - 1]
        stat = Path(file_path).stat()
        layout_key = [document_id, page_number, stat.st_size, stat.st_mtime_ns]
        cache = SummaryCache()
        boxes = cache.get("figure-page-layout-v1", layout_key)
        try:
            if boxes is None:
                pix = _render_page(page, rendered)
                regions = _get_figure_regions(_detect_layout(rendered))
                boxes = [list(_to_pdf_rect(page, r["bbox"], (pix.width, pix.height)))
                         for r in regions if _validate_figure(r["bbox"], r["confidence"])]
                cache.put("figure-page-layout-v1", layout_key, boxes)
            else:
                logger.info("Reusing saved image locations | page %d", page_number)
            bbox = match_figure(caption, captions, boxes)
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(bbox)).save(target)
        finally:
            rendered.unlink(missing_ok=True)
    return {"image_path": str(target), "bbox": bbox}
