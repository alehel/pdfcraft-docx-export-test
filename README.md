# PdfCraft Export Quality Measure

Measures how faithfully PdfCraft exports PDFs to other formats, and publishes the results as a
static site (`public/`). Word (DOCX) is measured today. PdfCraft's `doc_export_office` also
writes PowerPoint (PPTX), HTML, Rich Text (RTF) and Markdown; the site lists them as coming soon.

For DOCX, every PDF in `corpus/` is converted to
DOCX through PdfCraft's MCP server (`pdfcraft-cli mcp`: `doc_open`, then `doc_export_office`
with a `.docx` path), and the DOCX is compared with the PDF it came from. Each PDF gets a score
from 0 to 100. Scores are compared with an accepted baseline, so a PdfCraft change that makes the
export worse (or better) shows up as a regression (or improvement) per document.

Plain Python 3 with no packages; it calls Poppler, LibreOffice and Chromium as external processes.
They run in a container image that pins each of them, so scores are the same on every machine.

## Setup

Docker or Podman, with compose (`docker compose` or `podman compose`). The first command builds
the image (about 2.6 GB, a few minutes).

```bash
docker compose run --rm suite build        # clone pdfcraft and build pdfcraft-cli for Linux (first time ~4 min)
docker compose run --rm suite run          # export + score the whole corpus, compare with the baseline
```

With Podman, write `podman compose` instead of `docker compose`.

The image ([Dockerfile](Dockerfile)) is only the environment. compose mounts this checkout at
`/suite`, so reports (`public/`) and baselines land here, and keeps the pdfcraft clone, its Cargo build and the
LibreOffice profiles in the `work` volume. The image pins:

| Tool | Version | Used for |
|---|---|---|
| Chromium | 156.0.8078.73 (Debian) | making the corpus |
| LibreOffice | 25.8.2.2 (The Document Foundation's build, checksum-verified) | rendering the DOCX for the visual score |
| Poppler | 25.03.0 (Debian) | reading the PDF: words, formatting, page sizes, images, page bitmaps |
| Fonts | DejaVu, Liberation, URW base 35, Carlito, Caladea, Noto, Noto CJK (Debian) | everything above |
| Python | 3.13 (Debian) | the suite |
| Rust | 1.99.1 | building pdfcraft-cli |

Every Debian package comes from snapshot.debian.org at one moment (`DEBIAN_SNAPSHOT`), and both
base images are pinned by digest. To move a pin, edit the Dockerfile and run
`docker compose build`. If Chromium or the fonts changed, run `corpus --force all`. Then accept
a new baseline. If pdfcraft raises its minimum Rust version, the build says so: move the Rust pin.

## Commands

Each command goes after `docker compose run --rm suite`:

```bash
build                      # pdfcraft at origin/main (github.com/storytold/pdfcraft), built into the work volume
build --pr 1234            # or a pull request, or --ref <ref>
run                        # export + score the whole corpus (~10 s for 37 PDFs), compare with the baseline
run --only 'chrome/'       # a subset (regex on corpus names)
compare RUN_A RUN_B        # score changes between two runs (same build twice = flake check)
accept [RUN]               # make RUN (default: the latest) the baseline; then commit baseline/
report [RUN]               # rebuild a run's reports from its scores.json
index                      # rebuild public/index.html
status                     # build, origin/main, baseline, environment, recent runs
corpus [--force NAME|all]  # (re)make missing corpus files with the pinned Chromium and LibreOffice
```

`run` exits with 1 when a document regressed or newly fails, 0 otherwise.

## Reports

Open `public/index.html` in a browser. The site is written for people who do not know the
suite: plain titles and verdicts come first, and the technical detail is folded into sections
that open on a click ("How is the score worked out?", "For developers", "Technical details").
Every page carries the site's header: "PdfCraft Export Quality Measure", the measured formats,
and the formats still to come.

`public/` holds everything the reports show: `index.html`, `history.jsonl`, a history page per
test file in `docs/`, and a folder per run. It is not in git. Every link inside is relative, so
any static web server or static host can serve the folder as it is.

**Verdicts.** Next to every score is what it means: 90 or more is *very close* to the original,
80 to 89 *close*, 70 to 79 *noticeable differences*, below 70 *big differences*. Each test file
has a short plain title (`title` in `corpus/corpus.json`, set in `tools/make_corpus.py`).

**The index** (`public/index.html`) is rebuilt by `run`, `report`, `accept` and `index`:

- **Intro and explainer:** what the site measures, and, folded, how the five score parts make
  the score.
- **A card per format:** Word (DOCX) with its average score, verdict, change and trend, and one
  card for the formats coming next.
- **Word (DOCX) section:** a plain summary ("On average, the Word files PdfCraft makes are
  close to the originals…"), the average score across PdfCraft versions (hover for details,
  click for that version's report), and a gallery of the test documents. Each card shows the
  first page as a PDF and as a Word file, with its score and verdict; it opens the document's
  history page. The gallery sorts by lowest score or A to Z.
- **For developers** (folded): tested commits, reports and baseline, a chart per score part, each
  document's score at the last 10 tested commits (a score opens that commit's comparison with
  the one before), and every run, newest first.

**Each run** writes `public/<id>/report.html`, about 10 MB with its images:

- **Top:** the PdfCraft commit (links to GitHub, its tag or `git describe`, the pull request for
  `build --pr`), a sentence saying how it did, and tiles for the average, the documents and the
  changes since the baseline. Tool versions, the environment and the score table are folded.
- **A card per document:** title, description, score and verdict, and each page as screenshots
  of the PDF and of the Word file as LibreOffice lays it out (80 dpi, up to `--image-pages`,
  default 12). The score parts, missing and extra words, Word lint and the files are folded
  under each card, which also links to the document's history page.
- **Toolbar:** "Show differences" adds a third picture per page, red for ink only in the PDF
  and blue for ink only in the Word file. "Only documents that changed" hides the rest.

The run folder also has `report.md`, `scores.json`, the exported `docx/` and LibreOffice's
`rendered/` PDFs.

**A history page per test document** (`public/docs/<file>.html`) shows its score across
PdfCraft versions as a chart, and compares any two versions, picked with From and To: each page
appears three times side by side (the PDF, the Word file at From, the Word file at To), with the
score changes and a note when the page count or the PDF itself differs. A table of every version
tested is folded below. Clicking a point in the chart compares that version with the one before.
The pair is kept in the address (`#from=<sha>&to=<sha>`), so a comparison can be shared as a
link. Without a pair, the page opens on the latest change of 2 points or more. Screenshots exist
only for runs whose folder is kept; for pruned runs the comparison still shows the scores.

A *tested commit* is a full-corpus run of a commit on main (pull-request builds are listed but
not plotted), in the same environment as the latest run; the latest run of each commit counts.
A vertical rule marks a change of corpus, since the means either side are of different PDFs.

**Keeping history.** Every run adds a line to `public/history.jsonl`, which the charts and
tables are built from and which is never pruned. Run folders are pruned to the newest 10 (set
`SUITE_KEEP_RUNS`), plus the baseline's run and the latest run of every tagged commit. The
index still lists pruned runs, without a link. To chart a range of releases:

```bash
for ref in v0.14.0 v0.15.0 v0.16.1 origin/main; do
  docker compose run --rm suite build --ref $ref && docker compose run --rm suite run
done
```

### Without the container

The same commands also run directly, as `python3 -I suite.py <command>`. This needs Poppler
(`brew install poppler`), LibreOffice (`/Applications/LibreOffice.app`, or set `SOFFICE`), a
Rust toolchain and a pdfcraft clone at `~/dev/craft/pdfcraft` (or set `PDFCRAFT_REPO` to a clone
or a URL). You can also point `--cli` / `PDFCRAFT_CLI` at any `pdfcraft-cli` binary. `build` then
makes a worktree in `src/` and builds into `target/` (several GB), so it never touches your
checkout.

The fonts and tool versions differ from the image's, so scores can differ from the baseline's.
On macOS they came within a point or so. `run` and the report warn when the environment differs
from the baseline's.

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
(`baseline/scores.json`, plus the baseline's DOCX files) is in git. The baseline run's folder,
with its screenshots, is kept when older runs are pruned.

The same build in the same image gives identical scores (checked with `compare`). The visual and
page-count scores depend on the fonts and the LibreOffice version. That is why the baseline is
made in the image. The report warns when a run's environment or oracle versions differ from the
baseline's.

## Corpus

`corpus/corpus.json` describes each file. `tools/make_corpus.py` (`suite.py corpus`) made them
in the image, so the fonts are the Linux ones listed above. CSS asking for Arial, Times New Roman
or Courier New gets their metric-compatible Liberation fonts, and Georgia and Helvetica Neue fall
back to Liberation Serif and Liberation Sans. Menlo falls back to DejaVu Sans Mono. Arabic and
Hebrew come from DejaVu Sans, and the other scripts from Noto. Running the generator twice gives
byte-identical files: the creation date that Chromium and LibreOffice stamp is replaced by a
fixed one.

- `chrome/`: 22 documents printed by headless Chromium from HTML in the script: prose, headings and
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
