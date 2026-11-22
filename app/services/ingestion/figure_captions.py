"""Lightweight caption indexing and conservative figure/caption matching."""
import re

LABEL = re.compile(r"^\s*(?:figure|fig\.)\s*(S?\d+(?:\.\d+)*)(?!\w)[.:]\s*", re.I)
CAPTION_INDEX_VERSION = 2

REFERENCE = re.compile(r"\b(?:figures?|figs?\.?)\s*(S?\d+(?:\.\d+)*)(?!\w)", re.I)

def requested_figures(question):
    numbers = []
    for match in REFERENCE.finditer(question):
        numbers.append(match[1].upper())
        tail = question[match.end():]
        while True:
            extra = re.match(r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)(S?\d+(?:\.\d+)*)(?!\w)", tail, re.I)
            if not extra:
                break
            numbers.append(extra[1].upper())
            tail = tail[extra.end():]
        if re.match(r"\s*(?:-|to\b|through\b)\s*\d", tail, re.I):
            raise ValueError("Please list figure numbers separately, for example Figure 1 and Figure 2.")
    return list(dict.fromkeys(numbers))

def captions_from_blocks(blocks):
    captions = []
    for block in blocks:
        if len(block) > 6 and block[6] != 0:
            continue
        text = block[4].strip()
        match = LABEL.match(text)
        if match:
            captions.append({"figure_number": match[1].upper(), "caption": text,
                             "caption_bbox": list(block[:4])})
    return captions

def match_figure(caption, captions, boxes):
    """Require a unique spatial match; refuse ambiguous or fragmented layouts."""
    def distance(cap, box):
        x0, y0, x1, y1 = cap["caption_bbox"]
        bx0, by0, bx1, by1 = box
        overlap = max(0, min(x1, bx1) - max(x0, bx0))
        if overlap / max(1, min(x1-x0, bx1-bx0)) < .5:
            return None
        gap = y0-by1 if by1 <= y0 else by0-y1 if y1 <= by0 else -1
        return gap if 0 <= gap <= 100 else None
    def area(box):
        return max(0, box[2] - box[0]) * max(0, box[3] - box[1])

    def intersection(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
            0, min(a[3], b[3]) - max(a[1], b[1]))

    candidates = []
    for box in boxes:
        # A whole-figure detection must not swallow a different figure's caption.
        if any(intersection(box, c["caption_bbox"]) > 0
               for c in captions if c is not caption):
            continue
        gap = distance(caption, box)
        if gap is None:
            continue
        other = [distance(c, box) for c in captions if c is not caption]
        if any(d is not None and d <= gap + 10 for d in other):
            continue
        candidates.append((gap, box))
    # Prefer an existing enclosing detection, never merge unrelated boxes.
    # Allow small detector boundary errors, but leave near-equal boxes ambiguous.
    candidates = [(gap, box) for gap, box in candidates if not any(
        area(parent) >= 1.25 * area(box) and area(box) > 0
        and intersection(parent, box) >= .95 * area(box)
        for _, parent in candidates if parent is not box)]
    candidates.sort(key=lambda item: item[0])
    if not candidates or (len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 20):
        raise ValueError("Could not confidently match this caption to one complete figure. Please specify a page or upload the figure image.")
    return candidates[0][1]
