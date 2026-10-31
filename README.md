# PdfCraft DOCX export test suite

Measures how faithfully PdfCraft exports PDFs to Word. Every PDF in `corpus/` is converted to
DOCX through PdfCraft's MCP server (`pdfcraft-cli mcp`: `doc_open`, then `doc_export_office`
with a `.docx` path), and the DOCX is compared with the PDF it came from. Each PDF gets a score
from 0 to 100. Scores are compared with an accepted baseline, so a PdfCraft change that makes the
export worse (or better) shows up as a regression (or improvement) per document.

Plain Python 3 with no packages; it calls Poppler and LibreOffice as external processes.

## Setup

- Poppler (`pdftotext`, `pdftohtml`, `pdftoppm`, `pdfinfo`, `pdfimages`): `brew install poppler`
- LibreOffice (renders the DOCX for the visual comparison): `/Applications/LibreOffice.app`, or
  set `SOFFICE` to its `soffice` binary
- A Rust toolchain and a pdfcraft clone at `~/dev/craft/pdfcraft` (or set `PDFCRAFT_REPO`) for
  `suite.py build`; or point `--cli` / `PDFCRAFT_CLI` at any `pdfcraft-cli` binary
- Google Chrome only for regenerating the corpus

## Commands

```bash
python3 -I suite.py build                  # git worktree of pdfcraft in src/ at origin/main, built into target/
python3 -I suite.py build --pr 1234        # or a pull request, or --ref <ref>
python3 -I suite.py run                    # export + score the whole corpus (~20 s for 37 PDFs), compare with the baseline
python3 -I suite.py run --only 'chrome/'   # a subset (regex on corpus names)
python3 -I suite.py run --cli /path/to/pdfcraft-cli --label other-build
python3 -I suite.py compare RUN_A RUN_B    # score changes between two runs (same build twice = flake check)
python3 -I suite.py accept [RUN]           # make RUN (default: the latest) the baseline; then commit baseline/
python3 -I suite.py report [RUN]           # rebuild a run's reports from its scores.json
python3 -I suite.py status                 # build, origin/main, baseline, recent runs
python3 -I tools/make_corpus.py            # (re)make missing corpus files; --force NAME or --force all
```

`run` exits with 1 when a document regressed or newly fails, 0 otherwise. Each run writes
`runs/<id>/` with `report.html` (summary table, then per document: the scores, missing and extra
words, Word lint, and page images: original | DOCX as LibreOffice renders it | overlay with red
for ink only in the PDF and blue for ink only in the DOCX), `report.md`, `scores.json`, the
exported `docx/` and LibreOffice's `rendered/` PDFs. The newest 10 runs are kept.

`build` uses its own worktree (`src/`) and Cargo target folder (`target/`, several GB), so it
never touches your pdfcraft checkout.

## The score

| Part | Weight | What it measures |
|---|---:|---|
| words | 30 % | F1 of the words in the PDF and in the DOCX, as multisets: text that went missing or was added |
| order | 10 % | of the words both share, the share that lines up in reading order (per page) |
| format | 15 % | for words that line up: bold and italic (F1), font size (±1 pt or 10 %), colour, font family |
| visual | 30 % | the DOCX rendered by LibreOffice against the PDF, page by page: where the ink is (below) |
| structure | 15 % | page count (as LibreOffice lays the DOCX out), page sizes, number of images |

A part that does not apply (no text in a scanned page, no images on either side) is left out and
the rest are scaled up. A DOCX with package errors (broken zip, XML that does not parse, missing
content types or relationship targets) scores 0, as does a failed export.

**Words and order.** The PDF's words come from Poppler's `pdftotext` (logical order for
right-to-left text), the DOCX's from `word/document.xml`: paragraphs, tables, text boxes (VML and
DrawingML), with `mc:Fallback` skipped. Both are NFKC-normalised, with soft hyphens, zero-width
and bidi control characters removed. PdfCraft writes one section per PDF page, so order is
compared page by page whenever the section count matches. Poppler offers two plausible reading
orders (its layout analysis and the content-stream order of `pdftotext -raw`); the DOCX is held
to whichever it matches better, so Poppler's own mistakes with rotated text or tables do not
count against PdfCraft.

**Format.** The PDF side comes from `pdftohtml -xml`: bold and italic (judged from the font),
font, size and colour per text fragment, given to each word by position. The DOCX side resolves
document defaults, paragraph style (`basedOn` chains), character style and direct formatting.
A DOCX that names no font gets the word processor's default, which counts as a miss.

**Visual.** Both are rendered at 50 dpi in grayscale. The ink on each page is summed over a grid
(8, 16, 32 and 64 columns), blurred by one cell, and normalised to a distribution; the page
score is one minus the total variation distance, averaged over the four grids. It measures where
the ink is, not how dark it is, so a different font weight costs little; content that moved a
little still overlaps on the coarse grids. Calibration: the same page is 1.0, the page shifted
down by 14 pt about 0.86, an unrelated page of body text about 0.5, a blank page against content
0. Extra or missing pages count 0.

**Word lint** (reported, not scored): things Word refuses or complains about that other readers
accept, such as table cells not ending in a paragraph, rows without cells, rows wider than the
grid, duplicate drawing ids, page sizes outside 0.1-22 inches, and a body not ending in `sectPr`.

## Regressions

Against the baseline each document is 🔴 regression (score down 2 points or more) or now failing,
🟠 still failing, 🟢 improvement (up 2 or more) or fixed, ⚪ unchanged, 🆕 new in the corpus, or
🧩 corpus changed (the PDF's sha256 differs from the baseline's, so the scores are not
comparable). Component changes of 5 points or more are listed under each document. The baseline
(`baseline/scores.json`, plus the baseline's DOCX files and DOCX page renders) is in git.

Same build, same machine: identical scores (checked with `compare`). The visual and page-count
scores depend on the fonts installed and the LibreOffice version, so keep the baseline and the
runs on one machine. The report warns when the Poppler or LibreOffice version differs from the
baseline's.

## Corpus

`corpus/corpus.json` describes each file. `tools/make_corpus.py` made them:

- `chrome/`: 22 documents printed by headless Chrome from HTML in the script: prose, headings and
  emphasis, lists, ruled/spanned/borderless tables, two- and three-column layouts, images (PNG,
  JPEG, a logo with transparency), an 8-page report with running headers, an invoice with a
  rotated stamp, 16:9 slides with text over a photo, a diagonal watermark, rotated table headers,
  a dozen scripts including Arabic and Hebrew, code listings, an academic paper, a printed form,
  7 pt small print, mixed page sizes, highlights and callout boxes, a business letter
- `libreoffice/`: two Writer documents (HTML imported into Writer, exported to PDF)
- `handmade/`: base-14 fonts, a `/Rotate 90` page, a timetable of positioned text without rules,
  an image-only "scan"
- `mcp-suite/`: nine fixtures copied from the MCP regression suite (two columns and small caps,
  scripts, Turkish, a Writer file with a comment, tagged PDF, Type 3 font, inherited
  MediaBox/Rotate/UserUnit, split content streams, an image)

Add real-world PDFs anywhere under `corpus/` (e.g. `corpus/real/`); they need no entry in
`corpus.json`. Encrypted PDFs are not supported. Accept a new baseline after changing the corpus.

## Oracle limits

The reference is what Poppler reads and how LibreOffice renders, and both have blind spots. A low
part score is a lead, not a verdict: check the page images and the DOCX before calling it a
PdfCraft bug (`FINDINGS.md` lists what has been checked).

- `pdftotext` splits vertical CJK text, some Devanagari clusters and subscripts differently from
  PdfCraft, and drops one copy of text drawn twice on the same spot (e.g. a text shadow). On small
  documents a single word changes the score a lot.
- `pdftohtml` decides bold and italic from the font name, so synthetic bold (stroked text) is not
  seen. Formatting of rotated pages often cannot be matched (format shows "–").
- `pdfinfo` ignores `/UserUnit`, which PdfCraft applies (page sizes on `mcp-suite/inherited`).
- LibreOffice is not Word: shape rotation, some positioning and fonts may render differently.
  Opening a DOCX in Word is still the final check.
