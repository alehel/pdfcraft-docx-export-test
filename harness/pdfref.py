"""What the source PDF contains, as Poppler reads it (external oracle processes).

The words, in reading order, come from `pdftotext -bbox` (it separates words, orders bidi text
and keeps combining marks far better than pdftohtml). Their formatting comes from the text
fragments of `pdftohtml -xml`, which marks bold and italic and gives a font, size and colour;
each word takes the formatting of the fragment characters that fall inside its box. Page sizes
come from `pdfinfo`, image placements from `pdfimages -list`."""

import bisect
import html
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(slots=True)
class Word:
    text: str
    bold: bool | None = False      # None: formatting unknown (no pdftohtml fragment matched)
    italic: bool | None = False
    size: float | None = None      # points
    color: tuple | None = None     # (r, g, b) 0-255; None is black/automatic
    font: str | None = None        # font_family() of the font name; None when unknown


# Names that are the same family for our purposes (base-14 names and their usual stand-ins).
_FONT_ALIASES = {"times": "timesnewroman", "timesroman": "timesnewroman", "courier": "couriernew"}


def font_family(name):
    """A comparable family key: 'BAAAAA+TimesNewRomanPSMT' and 'Times New Roman' are both
    'timesnewroman', 'Helvetica-BoldOblique' is 'helvetica'."""
    if not name:
        return None
    name = re.sub(r"^[A-Z]{6}\+", "", name)
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(PSMT|PS|MT)$", "", name)
    key = re.sub(r"[^0-9a-z]", "", name.lower())
    return _FONT_ALIASES.get(key, key) or None


class ToolFailed(Exception):
    pass


def run(cmd, timeout=180):
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolFailed(f"{cmd[0]} timed out after {timeout}s")
    if p.returncode != 0:
        raise ToolFailed(f"{cmd[0]} exited {p.returncode}: {p.stderr.decode(errors='replace').strip()[:300]}")
    return p.stdout.decode("utf-8", errors="replace")


_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"
                                       "‎‏‪‫‬‭‮⁦⁧⁨⁩"))


def normalize(text):
    """NFKC (ligatures, presentation forms, full-width), without soft hyphens, zero-width and
    bidi control marks."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def parse_color(value):
    """'#RRGGBB' or 'RRGGBB' as (r, g, b); black and unknown values are None."""
    if not value:
        return None
    value = value.lstrip("#")
    if len(value) != 6:
        return None
    try:
        rgb = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return None if rgb == (0, 0, 0) else rgb


_PAGE_SIZE = re.compile(r"^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", re.M)
_PAGE_ROT = re.compile(r"^Page\s+(\d+) rot:\s+(\d+)", re.M)


def page_sizes(pdf):
    """[(width, height)] in points per page, as displayed (the page rotation applied)."""
    out = run(["pdfinfo", "-f", "1", "-l", "100000", pdf])
    sizes = {int(n): (float(w), float(h)) for n, w, h in _PAGE_SIZE.findall(out)}
    rots = {int(n): int(r) % 360 for n, r in _PAGE_ROT.findall(out)}
    pages = []
    for n in sorted(sizes):
        w, h = sizes[n]
        pages.append((h, w) if rots.get(n) in (90, 270) else (w, h))
    return pages


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(slots=True)
class Fragment:
    """A pdftohtml text fragment: its box (points, top-left origin) and its characters."""
    left: float
    top: float
    right: float
    bottom: float
    chars: list            # [(char, bold, italic)]
    size: float | None
    color: tuple | None
    font: str | None


def fragments_by_page(pdf, npages):
    xml = run(["pdftohtml", "-xml", "-i", "-q", "-stdout", "-zoom", "1", "-nodrm", pdf])
    root = ET.fromstring(_CONTROL.sub("", xml).encode("utf-8"))
    pages = [[] for _ in range(npages)]
    fonts = {}
    for page_el in root.iter("page"):
        index = int(page_el.get("number", "0")) - 1
        if not 0 <= index < npages:
            continue
        for el in page_el:
            if el.tag == "fontspec":
                try:
                    size = float(el.get("size"))
                except (TypeError, ValueError):
                    size = None
                fonts[el.get("id")] = (size, parse_color(el.get("color")), font_family(el.get("family")))
            elif el.tag == "text":
                size, color, family = fonts.get(el.get("font"), (None, None, None))
                chars = []
                _collect(el, False, False, chars)
                try:
                    left, top = float(el.get("left")), float(el.get("top"))
                    right, bottom = left + float(el.get("width")), top + float(el.get("height"))
                except (TypeError, ValueError):
                    continue
                if chars:
                    pages[index].append(Fragment(left, top, right, bottom, chars, size, color, family))
    return pages


_PAGE = re.compile(r"<page\b")
_WORD = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>', re.S)


_RTL = re.compile("[֐-ࣿיִ-﷿ﹰ-﻿]")


def words_by_page(pdf, npages):
    """[[Word]] per page, in Poppler's reading order, each with the formatting of the pdftohtml
    characters inside its box.

    The words come from plain `pdftotext`, which puts right-to-left text in logical order; its
    -bbox mode (which has the boxes) keeps such words in visual order, reversed. So each plain
    word takes the style of the next bbox word with the same text, or the same text reversed."""
    fragments = fragments_by_page(pdf, npages)
    chunks = _PAGE.split(run(["pdftotext", "-bbox", "-enc", "UTF-8", pdf, "-"]))[1:]
    plain = run(["pdftotext", "-enc", "UTF-8", pdf, "-"]).split("\f")
    pages = []
    for index in range(npages):
        styler = _Styler(fragments[index])
        styles = {}
        for x0, y0, x1, y1, text in _WORD.findall(chunks[index] if index < len(chunks) else ""):
            style = styler.style((float(x0), float(y0), float(x1), float(y1)))
            for part in normalize(html.unescape(text)).split():
                styles.setdefault(part, []).append(style)
        words = []
        for part in normalize(plain[index] if index < len(plain) else "").split():
            queue = styles.get(part) or (styles.get(part[::-1]) if _RTL.search(part) else None)
            words.append(Word(part, *(queue.pop(0) if queue else (None,) * 5)))
        pages.append(words)
    return pages


class _Styler:
    """Finds the formatting for a word box among one page's fragments."""

    def __init__(self, fragments):
        self.fragments = sorted(fragments, key=lambda f: f.top)
        self.tops = [f.top for f in self.fragments]
        self.tallest = max((f.bottom - f.top for f in self.fragments), default=0)

    def style(self, box):
        x0, y0, x1, y1 = box
        votes = {}
        start = bisect.bisect_left(self.tops, y0 - self.tallest - 1)
        for f in self.fragments[start:bisect.bisect_right(self.tops, y1 + 1)]:
            overlap_y = min(y1, f.bottom) - max(y0, f.top)
            if overlap_y <= 0.3 * min(y1 - y0, f.bottom - f.top) or min(x1, f.right) - max(x0, f.left) <= 0:
                continue
            # Characters are spread evenly over the fragment's width: good enough to tell which
            # of them make up this word.
            step = (f.right - f.left) / len(f.chars)
            inside = [c for k, c in enumerate(f.chars)
                      if not c[0].isspace() and x0 - 1 <= f.left + (k + 0.5) * step <= x1 + 1]
            if inside:
                votes[id(f)] = (f, inside)
        if not votes:
            return None, None, None, None, None
        chars = [c for _, inside in votes.values() for c in inside]
        main = max(votes.values(), key=lambda v: len(v[1]))[0]
        bold = sum(1 for c in chars if c[1]) * 2 >= len(chars)
        italic = sum(1 for c in chars if c[2]) * 2 >= len(chars)
        return bold, italic, main.size, main.color, main.font


def _collect(el, bold, italic, out):
    bold = bold or el.tag == "b"
    italic = italic or el.tag == "i"
    if el.text:
        out.extend((c, bold, italic) for c in el.text)
    for child in el:
        _collect(child, bold, italic, out)
        if child.tail:
            out.extend((c, bold, italic) for c in child.tail)


def images_by_page(pdf, npages):
    """How many images each page draws (`pdfimages -list` rows of type image; masks left out)."""
    counts = [0] * npages
    for line in run(["pdfimages", "-list", pdf]).splitlines()[2:]:
        cols = line.split()
        if len(cols) > 2 and cols[0].isdigit() and cols[2] == "image":
            page = int(cols[0]) - 1
            if 0 <= page < npages:
                counts[page] += 1
    return counts


def raw_words_by_page(pdf, npages):
    """[[Word]] per page in content-stream order (`pdftotext -raw`): a second plausible reading
    order, often the better one where the layout analysis scrambles rotated text or tables."""
    pages = run(["pdftotext", "-raw", "-enc", "UTF-8", pdf, "-"]).split("\f")
    return [[Word(t, None, None) for t in normalize(pages[i]).split()] if i < len(pages) else [] for i in range(npages)]


def read_reference(pdf):
    sizes = page_sizes(pdf)
    return {
        "page_sizes": sizes,
        "words": words_by_page(pdf, len(sizes)),
        "raw_words": raw_words_by_page(pdf, len(sizes)),
        "images": images_by_page(pdf, len(sizes)),
    }
