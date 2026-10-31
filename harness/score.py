"""Scores, each 0..1 (None when it does not apply to the document), and how they make a total."""

import collections
import difflib

# Raised whenever the formula changes: scores made under different formulas are not compared, and
# `suite.py rescore` brings the kept runs up to date.
VERSION = 2

# How much each part counts within its half. A part that does not apply (no text in the PDF, no
# images on either side, ...) is left out and the others in its half are scaled up.
WEIGHTS = {"words": 0.30, "order": 0.10, "format": 0.15, "visual": 0.30, "structure": 0.15}
# The two questions a reader asks: does it look like the original, and is the text all there? The
# total is their geometric mean, so a document scores well only when both are right: text that is
# all present cannot make up for pages that look nothing like the PDF.
HALVES = {"looks": ("visual", "structure"), "text": ("words", "order", "format")}
# A page's look counts from here. Pages of unrelated corpus documents score 0.22 (lower quartile)
# to 0.39 (median) on render.page_similarity just by having text in similar places; 0.3 matched
# by-eye ratings of ten exports best.
CHANCE = 0.3

SIZE_TOLERANCE = 1.0      # points, or 10 % when that is more (Poppler rounds font sizes)
COLOR_TOLERANCE = 48      # per 8-bit channel
PAGE_SIZE_TOLERANCE = 2.0  # points (Word stores whole twips)


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def ratio(a, b):
    """min/max of two counts; None when both are zero."""
    return None if a == b == 0 else min(a, b) / max(a, b)


def align(a_words, b_words):
    """Index pairs of words that line up when both are read in order."""
    a = [x.text for x in a_words]
    b = [x.text for x in b_words]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=len(a) + len(b) > 20000)
    return [(blk.a + k, blk.b + k) for blk in matcher.get_matching_blocks() for k in range(blk.size)]


def counter(words):
    return collections.Counter(x.text for x in words)


def order_score(ref_pages, docx_pages):
    """Of the words both sides share, the share that also lines up in reading order (per page
    when the DOCX has one section per PDF page), and the pairs of words that line up."""
    if len(ref_pages) == len(docx_pages):
        segments = list(zip(ref_pages, docx_pages))
    else:
        segments = [([x for p in ref_pages for x in p], [x for p in docx_pages for x in p])]
    matched = expected = 0
    pairs = []
    for a, b in segments:
        aligned = align(a, b)
        matched += len(aligned)
        expected += sum((counter(a) & counter(b)).values())
        pairs.extend((a[i], b[j]) for i, j in aligned)
    return (matched / expected if expected else None), pairs


def text_scores(ref_pages, docx_pages, raw_pages=None):
    """Words: F1 of the words present on both sides (as multisets, wherever they are).
    Order: see order_score; Poppler's layout order and its content-stream order (`raw_pages`)
    are both plausible readings, and the DOCX is held to the one it matches better.
    Returns (scores, word pairs that line up, missing words, extra words)."""
    ref_all = [x for p in ref_pages for x in p]
    docx_all = [x for p in docx_pages for x in p]
    ca, cb = counter(ref_all), counter(docx_all)
    common = sum((ca & cb).values())
    recall = common / len(ref_all) if ref_all else None
    precision = common / len(docx_all) if docx_all else None
    if not ref_all and not docx_all:
        words = None
    elif not common:
        words = 0.0
    else:
        words = 2 * recall * precision / (recall + precision)
    order, pairs = order_score(ref_pages, docx_pages)
    order_raw = order_score(raw_pages, docx_pages)[0] if raw_pages is not None else None
    scores = {
        "words": words,
        "order": max((o for o in (order, order_raw) if o is not None), default=None),
        "recall": recall,
        "precision": precision,
        "order_layout": order,
        "order_raw": order_raw,
    }
    missing = (ca - cb).most_common(15)
    extra = (cb - ca).most_common(15)
    return scores, pairs, missing, extra


def flag_f1(pairs, attr):
    tp = sum(1 for a, b in pairs if getattr(a, attr) and getattr(b, attr))
    fp = sum(1 for a, b in pairs if not getattr(a, attr) and getattr(b, attr))
    fn = sum(1 for a, b in pairs if getattr(a, attr) and not getattr(b, attr))
    return None if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)


def color_close(a, b):
    a, b = a or (0, 0, 0), b or (0, 0, 0)
    return max(abs(x - y) for x, y in zip(a, b)) <= COLOR_TOLERANCE


def format_scores(pairs):
    """Formatting of the words that line up: bold and italic as F1 (so the few bold words count,
    not the many plain ones); size, colour and font family as the share that agrees (a DOCX
    that names no font gets whatever the word processor defaults to, which counts as a miss)."""
    pairs = [(a, b) for a, b in pairs if a.bold is not None]
    sized = [(a.size, b.size) for a, b in pairs if a.size and b.size]
    named = [(a.font, b.font) for a, b in pairs if a.font]
    parts = {
        "bold": flag_f1(pairs, "bold"),
        "italic": flag_f1(pairs, "italic"),
        "size": (sum(1 for x, y in sized if abs(x - y) <= max(SIZE_TOLERANCE, 0.1 * x)) / len(sized)) if sized else None,
        "color": (sum(1 for a, b in pairs if color_close(a.color, b.color)) / len(pairs)) if pairs else None,
        "font": (sum(1 for x, y in named if x == y) / len(named)) if named else None,
    }
    return mean(parts.values()), parts


def size_matches(a, b):
    return a is not None and b is not None and all(abs(x - y) <= PAGE_SIZE_TOLERANCE for x, y in zip(a, b))


def structure_scores(ref_sizes, ref_images, docx_sections, docx_images, rendered_sizes):
    """Page count (as LibreOffice lays the DOCX out), page sizes (from the DOCX sections when
    there is one per page, else from the rendering) and the number of images."""
    n_ref = len(ref_sizes)
    n_out = len(rendered_sizes) if rendered_sizes is not None else 0
    out_sizes = [s.size for s in docx_sections] if len(docx_sections) == n_ref else (rendered_sizes or [])
    parts = {
        "pages": ratio(n_ref, n_out),
        "page_size": sum(1 for a, b in zip(ref_sizes, out_sizes) if size_matches(a, b)) / max(n_ref, len(out_sizes), 1),
        "images": ratio(ref_images, docx_images),
    }
    return mean(parts.values()), parts


def above_chance(similarity):
    """A page's look: its similarity above chance, rescaled to 0..1."""
    return max(0.0, (similarity - CHANCE) / (1 - CHANCE))


def look(similarities):
    """The look part: the pages' looks averaged; a page with nothing to match counts 0."""
    return sum(above_chance(s) for s in similarities) / len(similarities) if similarities else None


def halves(components):
    """{"looks": .., "text": ..}, each the weighted mean of its parts (None when none apply)."""
    out = {}
    for half, parts in HALVES.items():
        used = {k: components[k] for k in parts if components.get(k) is not None}
        weight = sum(WEIGHTS[k] for k in used)
        out[half] = sum(WEIGHTS[k] * v for k, v in used.items()) / weight if weight else None
    return out


def total(components):
    """The geometric mean of the halves that apply (a scan has no text, so only its looks count)."""
    values = [v for v in halves(components).values() if v is not None]
    if not values:
        return 0.0
    product = 1.0
    for v in values:
        product *= v
    return product ** (1 / len(values))
