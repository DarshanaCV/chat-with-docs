# Read PDF text and caption locations in one text extraction pass.
import logging
import re
import pymupdf
from app.services.ingestion.figure_captions import captions_from_blocks, CAPTION_INDEX_VERSION

logger = logging.getLogger(__name__)

def read_pdf(file_path):
    pages = []
    with pymupdf.open(file_path) as doc:
        for page_number, page in enumerate(doc, start=1):
            text_page = page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT)
            blocks = text_page.extractBLOCKS()
            captions = captions_from_blocks(blocks)
            # Preserve short standalone text blocks as heading candidates, not facts.
            headings = [{"title": b[4].strip(), "bbox": list(b[:4])} for b in blocks
                        if b[6] == 0 and len(b[4].strip()) <= 100
                        and len(b[4].strip().splitlines()) == 1
                        and not re.match(r"^(?:figure|fig\.|table)\s", b[4].strip(), re.I)
                        and not b[4].strip().isdigit()]
            tables = []
            for block in blocks:
                match = re.match(r"^\s*Table\s+(S?\d+(?:\.\d+)*)(?!\w)[.:]\s*", block[4], re.I)
                if match:
                    tables.append({"table_number": match[1], "caption": block[4].strip(), "caption_bbox": list(block[:4])})
            pages.append({"page_number": page_number,
                          "text": text_page.extractText(sort=True),
                          "figure_captions": captions, "table_captions": tables,
                          "heading_candidates": headings, "caption_index_version": CAPTION_INDEX_VERSION})
    logger.info("Saved caption locations | %d captions across %d pages | no image analysis",
                sum(len(p["figure_captions"]) for p in pages), len(pages))
    return pages
