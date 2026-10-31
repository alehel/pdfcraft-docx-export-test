"""Rendering: DOCX to PDF with LibreOffice (headless), PDF pages to grayscale bitmaps with
pdftoppm, and the page-image comparison. Pure Python: images are kept as (width, height, bytes)."""

import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

from .pdfref import ToolFailed, run


def find_soffice():
    for candidate in (os.environ.get("SOFFICE"), "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                      shutil.which("soffice"), shutil.which("libreoffice")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def docx_to_pdf(soffice, docx, outdir, profile, timeout=240):
    """Convert with LibreOffice Writer; `profile` is a user-installation folder of its own, so
    several conversions can run at once."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [soffice, f"-env:UserInstallation={Path(profile).resolve().as_uri()}", "--headless", "--norestore",
           "--nolockcheck", "--nodefault", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(outdir), str(docx)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolFailed(f"LibreOffice timed out after {timeout}s")
    produced = outdir / (Path(docx).stem + ".pdf")
    if not produced.exists():
        raise ToolFailed(f"LibreOffice could not convert it (exit {p.returncode}): "
                         f"{p.stderr.decode(errors='replace').strip()[-300:]}")
    return produced


def rasterize(pdf, outdir, prefix, dpi):
    """Grayscale PGM per page; returns the paths in page order."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    run(["pdftoppm", "-gray", "-r", str(dpi), pdf, outdir / prefix], timeout=600)
    pages = list(outdir.glob(f"{prefix}-*.pgm"))
    return sorted(pages, key=lambda p: int(p.stem.rsplit("-", 1)[1]))


SHOT_DPI = 80  # report screenshots: sharp at the report's width, 50-110 KB a page


def screenshots(pdf, outdir, prefix, pages, dpi=SHOT_DPI):
    """Colour PNGs of the first `pages` pages, for people to look at (scores use rasterize)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    run(["pdftoppm", "-png", "-r", str(dpi), "-f", "1", "-l", str(pages), pdf, outdir / prefix], timeout=600)
    return sorted(outdir.glob(f"{prefix}-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))


_PGM = re.compile(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s")


def read_pgm(path):
    data = Path(path).read_bytes()
    m = _PGM.match(data)
    if not m or int(m.group(3)) != 255:
        raise ToolFailed(f"unexpected PGM header in {path}")
    w, h = int(m.group(1)), int(m.group(2))
    return w, h, data[m.end(): m.end() + w * h]


def resize(img, width, height):
    """Nearest-neighbour resize (only used to put a page on the reference page's pixel grid)."""
    w, h, data = img
    if (w, h) == (width, height):
        return img
    xs = [min(w - 1, x * w // width) for x in range(width)]
    out = bytearray()
    for y in range(height):
        row = data[min(h - 1, y * h // height) * w:][:w]
        out += bytes(map(row.__getitem__, xs))
    return width, height, bytes(out)


GRID_COLUMNS = (8, 16, 32, 64)


def ink_grid(img, columns, rows):
    """Darkness per cell of a columns x rows grid laid over the page (0 = white)."""
    w, h, data = img
    xs = [round(i * w / columns) for i in range(columns + 1)]
    ys = [round(j * h / rows) for j in range(rows + 1)]
    grid = []
    for j in range(rows):
        sums = [0] * columns
        for y in range(ys[j], ys[j + 1]):
            row = data[y * w:(y + 1) * w]
            for i in range(columns):
                x0, x1 = xs[i], xs[i + 1]
                sums[i] += 255 * (x1 - x0) - sum(row[x0:x1])
        grid.extend(sums)
    return grid


def blur(grid, columns, rows):
    """3x3 box blur, so content shifted by a cell still mostly overlaps."""
    out = [0.0] * len(grid)
    for j in range(rows):
        for i in range(columns):
            total = n = 0
            for dj in (-1, 0, 1):
                jj = j + dj
                if 0 <= jj < rows:
                    for di in (-1, 0, 1):
                        ii = i + di
                        if 0 <= ii < columns:
                            total += grid[jj * columns + ii]
                            n += 1
            out[j * columns + i] = total / n
    return out


def page_similarity(ref, other):
    """How alike two page images are, 0..1: one minus the total variation distance between their
    (blurred) ink distributions, averaged over grids from 8 to 64 columns. Where the ink is
    counts, not how dark it is overall, so a different font weight costs little; content that
    moved a little still overlaps on the coarse grids, while missing, extra or far-moved content
    costs on all of them. The other page is stretched onto the reference page's grid when the
    sizes differ."""
    w, h, _ = ref
    # Under a 1/1000 of a page of solid black counts as a blank page.
    blank = 255 * w * h / 1000
    scores = []
    for columns in GRID_COLUMNS:
        rows = max(1, round(columns * h / w))
        a = blur(ink_grid(ref, columns, rows), columns, rows)
        b = blur(ink_grid(other, columns, rows), columns, rows)
        ta, tb = sum(a), sum(b)
        if ta < blank and tb < blank:
            return 1.0
        if ta < blank or tb < blank:
            return 0.0
        scores.append(1.0 - 0.5 * sum(abs(x / ta - y / tb) for x, y in zip(a, b)))
    return sum(scores) / len(scores)


def overlay(ref, other):
    """An RGB image of both pages: black where both have ink, red where only the original has,
    blue where only the DOCX has."""
    w, h, a = ref
    _, _, b = resize(other, w, h)
    out = bytearray(w * h * 3)
    out[0::3] = b
    out[1::3] = bytes(map(min, a, b))
    out[2::3] = a
    return w, h, bytes(out)


def write_png(path, img, channels=1):
    """Gray (1), RGB (3) or RGBA (4) 8-bit pixels as a PNG."""
    w, h, data = img
    stride = w * channels
    raw = b"".join(b"\x00" + data[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, {1: 0, 3: 2, 4: 6}[channels], 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
