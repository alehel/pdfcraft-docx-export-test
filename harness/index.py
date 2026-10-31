"""public/index.html: every report, and accuracy over the PdfCraft commits that were tested.

A tested commit is a full-corpus run (no --only) of a commit on main, made in the same environment
as the latest full run (scores from elsewhere are not comparable); when a commit was run more than
once, its latest run counts. The charts are inline SVG with a small script for the hover readout;
the tables below them hold every value, so nothing depends on hovering."""

import hashlib
import html
import json
import math
from pathlib import Path

from .history import PARTS
from .pipeline import safe_name
from .report import (CLASSES, FORMATS, SITE, commit_html, delta_html, halves_line, page_html, pct, png_size,
                     site_header,
                     title_of, verdict, version_label, when)
from .score import HALVES, VERSION, WEIGHTS, halves

MAX_COLUMNS = 10  # tested commits in the per-document table

CSS = """
figure.chart { position: relative; margin: 0; }
figure.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.scroll { overflow-x: auto; }
.scroll svg { min-width: 640px; }
figure.chart figcaption { margin-top: 6px; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
.charts h3 { font-size: 13px; color: var(--ink-2); font-weight: 600; margin: 0 0 4px; }
.tip { position: absolute; top: 4px; transform: translateX(-50%); min-width: 160px; max-width: 300px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.14); pointer-events: none; font-size: 12px; z-index: 2; }
.tip strong { display: block; font-size: 16px; }
.tip .s { color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tip .m { color: var(--muted); }
svg text { font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--muted); }
svg text.tag { fill: var(--ink-2); font-weight: 600; }
svg text.end { fill: var(--ink); font-weight: 600; font-size: 12px; }
svg .grid { stroke: var(--grid); stroke-width: 1; }
svg .axis { stroke: var(--axis); stroke-width: 1; }
svg .line { fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
svg .dot { fill: var(--series); stroke: var(--surface); stroke-width: 2; }
svg a.pt:focus { outline: none; } svg a.pt:focus .dot { stroke: var(--ink); }
svg .cross { stroke: var(--axis); stroke-width: 1; visibility: hidden; }
td.cell { min-width: 64px; }
td.cell a { text-decoration: none; display: block; }
td.cell a:hover { text-decoration: underline; }
td.u1 { background: var(--up-wash); } td.u2 { background: var(--up-wash-2); }
td.d1 { background: var(--down-wash); } td.d2 { background: var(--down-wash-2); }
th small { display: block; font-weight: 400; color: var(--muted); }
.empty { padding: 28px; text-align: center; color: var(--ink-2); }
.intro { margin: 18px 0 18px; max-width: 820px; }
.intro h1 { font-size: 30px; line-height: 1.2; margin: 0 0 8px; }
.lead { font-size: 16px; color: var(--ink-2); margin: 0; }
.formats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; margin: 0 0 8px; }
.fcard { display: flex; flex-direction: column; gap: 4px; text-decoration: none; color: inherit; }
.fcard .fname { font-weight: 600; font-size: 15px; display: flex; gap: 8px; align-items: center; }
.fcard.live:hover { border-color: var(--series); }
.fcard .go { margin-top: auto; padding-top: 6px; color: var(--ink-2); font-size: 13px; }
.fcard.live:hover .go { color: var(--ink); text-decoration: underline; }
.fcard .value.hero { font-size: 48px; font-weight: 600; line-height: 1.1; margin-top: 6px; }
.fcard .sub { font-size: 13px; color: var(--ink-2); }
.soon-card { background: transparent; border-style: dashed; }
.soon-card p { margin: 0; font-size: 13px; }
ul.coming { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 8px; }
ul.coming li { display: flex; justify-content: space-between; align-items: center; color: var(--ink-2);
  padding-bottom: 8px; border-bottom: 1px solid var(--grid); }
ul.coming li:last-child { border-bottom: 0; padding-bottom: 0; }
.spark { width: 100%; max-width: 260px; height: 44px; margin: 4px 0; }
.spark polyline { fill: none; stroke: var(--muted); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.spark circle { fill: var(--series); stroke: var(--surface); stroke-width: 2; }
.format { scroll-margin-top: 12px; }
.format > h2 { font-size: 22px; margin: 36px 0 4px; display: flex; gap: 10px; align-items: center; }
.format > h2 .pill { font-size: 13px; }
h3.section { font-size: 18px; margin: 40px 0 10px; }
.intro .lead { margin-bottom: 4px; }
details.explain { max-width: 760px; }
details.explain p { color: var(--ink-2); }
details.explain h4 { margin: 16px 0 0; font-size: 15px; }
ul.weights { list-style: none; padding: 0; margin: 8px 0 12px; display: grid; gap: 8px; }
.halves b { color: var(--ink); font-weight: 600; }
ul.weights li { display: grid; grid-template-columns: 120px 48px 1fr; gap: 10px; color: var(--ink-2); }
ul.weights b { color: var(--ink); }
.fcard .verdict { font-size: 15px; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.tcard { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: inherit; padding: 16px; }
.tcard:hover { border-color: var(--series); }
.thumbs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; align-items: start; margin-bottom: 4px; }
.thumbs figure { margin: 0; }
.thumbs .frame { height: 150px; display: flex; align-items: center; justify-content: center; }
.thumbs img { display: block; max-width: 100%; max-height: 150px; width: auto; height: auto;
  border: 1px solid var(--border); background: #fff; }
.thumbs .gap { min-height: 0; height: 100%; width: 100%; padding: 8px; }
.thumbs figcaption { font-size: 11px; color: var(--muted); margin-top: 3px; text-align: center; }
.ttitle { font-size: 16px; }
.tdesc { font-size: 13px; color: var(--ink-2); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; }
.tscore { display: flex; align-items: baseline; gap: 10px; margin-top: auto; padding-top: 4px; }
.tscore .num { font-size: 24px; font-weight: 600; }
.tscore .verdict { font-size: 14px; }
.tcard .meter { width: 100%; margin: 0; }
.tchange { font-size: 13px; }
.sorter { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
.sorter button { font: inherit; font-size: 13px; padding: 3px 12px; border-radius: 999px; border: 1px solid var(--axis);
  background: var(--surface); color: var(--ink-2); cursor: pointer; }
.sorter button[aria-pressed="true"] { background: var(--ink); border-color: var(--ink); color: var(--surface); }
details.dev { margin-top: 48px; padding-top: 8px; border-top: 1px solid var(--grid); }
"""

SORT_SCRIPT = """<script>
(() => {
  const gallery = document.getElementById("gallery");
  if (!gallery) return;
  const buttons = document.querySelectorAll("[data-sort]");
  for (const button of buttons) button.addEventListener("click", () => {
    const by = button.dataset.sort, cards = [...gallery.children];
    cards.sort((a, b) => by === "score" ? a.dataset.score - b.dataset.score : a.dataset.title.localeCompare(b.dataset.title));
    gallery.append(...cards);
    for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
  });
})();
</script>"""

SCRIPT = """<script>
(() => {
  const data = JSON.parse(document.getElementById("points").textContent);
  for (const fig of document.querySelectorAll("figure.chart")) {
    const svg = fig.querySelector("svg"), tip = fig.querySelector(".tip"), cross = svg.querySelector(".cross");
    const xs = JSON.parse(svg.dataset.xs), key = svg.dataset.key, width = svg.viewBox.baseVal.width;
    let current = -1;
    const row = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; return d; };
    function show(i) {
      current = i;
      const p = data[i], v = key in p ? p[key] : p.parts[key];
      cross.setAttribute("x1", xs[i]); cross.setAttribute("x2", xs[i]); cross.style.visibility = "visible";
      const strong = document.createElement("strong");
      strong.textContent = (v == null ? "–" : v.toFixed(1)) + (key === "mean" ? " mean score" : " " + key);
      tip.replaceChildren(strong, row("", (p.tags.length ? p.tags.join(", ") + " · " : "") + p.sha + " · " + p.date),
                          row("s", p.subject), row("m", p.hint || (p.href ? "Click to open the report" : "Report pruned")));
      tip.hidden = false;
      const box = svg.getBoundingClientRect(), frame = fig.getBoundingClientRect();
      const x = box.left - frame.left + xs[i] / width * box.width;
      tip.style.left = Math.max(90, Math.min(frame.width - 90, x)) + "px";
    }
    function hide() { current = -1; cross.style.visibility = "hidden"; tip.hidden = true; }
    svg.addEventListener("pointermove", (ev) => {
      const box = svg.getBoundingClientRect(), x = (ev.clientX - box.left) / box.width * width;
      let best = 0;
      xs.forEach((v, i) => { if (Math.abs(v - x) < Math.abs(xs[best] - x)) best = i; });
      if (best !== current) show(best);
    });
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("click", () => { if (current >= 0 && data[current].href) location.href = data[current].href; });
    for (const a of svg.querySelectorAll("a.pt")) {
      a.addEventListener("focus", () => show(+a.dataset.i));
      a.addEventListener("blur", hide);
    }
  }
})();
</script>"""


def fingerprint(entry):
    """The corpus a run scored: which PDFs, by content."""
    return hashlib.sha1(json.dumps(sorted((n, d[2]) for n, d in entry["docs"].items())).encode()).hexdigest()


def tested_commits(entries):
    # Only runs scored with the current formula are comparable (suite.py rescore updates kept runs).
    full = sorted((e for e in entries if not e.get("only") and e.get("scoring", 1) == VERSION), key=lambda e: e["started"])
    if not full:
        return [], None
    env = full[-1].get("environment")
    latest = {}
    for e in full:
        pc = e.get("pdfcraft") or {}
        if pc.get("sha") and e.get("environment") == env and pc.get("on_main") is not False:
            latest[pc["sha"]] = e
    points = sorted(latest.values(), key=lambda e: (e["pdfcraft"].get("date") or e["started"], e["started"]))
    return points, env


def domain(values, pad=3, step=5, span=10):
    lo = max(0, math.floor((min(values) - pad) / step) * step)
    hi = min(100, math.ceil((max(values) + pad) / step) * step)
    if hi - lo < span:
        hi = min(100, lo + span)
        lo = max(0, hi - span)
    return lo, hi


def ticks(lo, hi, most):
    step = next(s for s in (5, 10, 20, 25, 50) if (hi - lo) / s <= most)
    return range(math.ceil(lo / step) * step, hi + 1, step)


def line_svg(values, key, *, width, height, margins, most_ticks, xlabels=None, tags=None, breaks=(), meta=None):
    """One series over the tested commits: hairline grid, 2px line, 8px ringed dots, the latest value at
    the end, commits named on the x axis (tags in bold). Returns the SVG; its data-xs drives the hover
    readout."""
    ml, mr, mt, mb = margins
    pw, ph = width - ml - mr, height - mt - mb
    n = len(values)
    have = [100 * v for v in values if v is not None]
    lo, hi = domain(have) if have else (0, 100)
    xs = [round(ml + (pw / 2 if n == 1 else i * pw / (n - 1)), 1) for i in range(n)]

    def y(v):
        return round(mt + ph * (1 - (100 * v - lo) / (hi - lo)), 1)

    out = [f"<svg viewBox='0 0 {width} {height}' data-key='{key}' data-xs='{json.dumps(xs)}' role='img' "
           f"aria-label='{html.escape(key)} over {n} tested commits'>",
           f"<rect x='{ml}' y='{mt}' width='{pw}' height='{ph}' fill='transparent'/>"]
    for t in ticks(lo, hi, most_ticks):
        ty = y(t / 100)
        out.append(f"<line class='grid' x1='{ml}' x2='{ml + pw}' y1='{ty}' y2='{ty}'/>"
                   f"<text x='{ml - 8}' y='{ty + 4}' text-anchor='end'>{t}</text>")
    out.append(f"<line class='axis' x1='{ml}' x2='{ml + pw}' y1='{mt + ph}' y2='{mt + ph}'/>")
    for i in breaks:
        bx = round((xs[i - 1] + xs[i]) / 2, 1)
        out.append(f"<line class='axis' x1='{bx}' x2='{bx}' y1='{mt}' y2='{mt + ph}'/>"
                   f"<text x='{bx + 4}' y='{mt + ph - 6}'>corpus changed</text>")
    run = []
    for i, v in enumerate(values + [None]):
        if v is not None:
            run.append(f"{xs[i]},{y(v)}")
        elif run:
            if len(run) > 1:
                out.append(f"<polyline class='line' points='{' '.join(run)}'/>")
            run = []
    out.append(f"<line class='cross' x1='0' x2='0' y1='{mt}' y2='{mt + ph}'/>")
    for i, v in enumerate(values):
        if v is None:
            continue
        m = (meta or [{}] * n)[i]
        dot = f"<circle class='dot' cx='{xs[i]}' cy='{y(v)}' r='4'/>"
        if m.get("href"):
            out.append(f"<a class='pt' href='{html.escape(m['href'])}' data-i='{i}' "
                       f"aria-label='{html.escape(m.get('aria', ''))}'>{dot}</a>")
        else:
            out.append(dot)
    last = max((i for i, v in enumerate(values) if v is not None), default=None)
    if last is not None:
        out.append(f"<text class='end' x='{xs[last] + 9}' y='{y(values[last]) + 4}'>{100 * values[last]:.1f}</text>")
    if xlabels:
        # Where they would collide, the latest commit's label wins, then tags, then the oldest.
        placed = []
        for i in sorted(range(n), key=lambda i: (i != n - 1, not (tags and tags[i]), i)):
            if xlabels[i] and all(abs(xs[i] - x) > 56 for x in placed):
                placed.append(xs[i])
                cls = " class='tag'" if tags and tags[i] else ""
                out.append(f"<text{cls} x='{xs[i]}' y='{mt + ph + 18}' text-anchor='middle'>{html.escape(xlabels[i])}</text>")
    out.append("</svg>")
    return "".join(out)


def html_index(entries, available, baseline_run, public, corpus):
    e = html.escape
    entries = sorted(entries, key=lambda x: x["started"], reverse=True)
    points, env = tested_commits(entries)

    def href(entry, anchor=""):
        return f"{entry['id']}/report.html{anchor}" if entry["id"] in available else None

    def name(entry):
        pc = entry["pdfcraft"]
        return (pc.get("tags") or [None])[0] or (pc.get("sha") or "")[:7] or "custom"

    # -- the format's own tiles (its headline number is on its card at the top)
    tiles = []
    if points:
        now = points[-1]
        tiles.append(("Tested commits", f"<div class='value'>{len(points)}</div>",
                      f"from {when(points[0]['pdfcraft'].get('date') or points[0]['started'], False)} "
                      f"to {when(now['pdfcraft'].get('date') or now['started'], False)}"))
        tiles.append(("Test PDFs", f"<div class='value'>{len(now['docs'])}</div>", "in the latest tested commit's run"))
    tiles.append(("Reports", f"<div class='value'>{len(available)}</div>",
                  f"of {len(entries)} runs; older ones are pruned, their scores kept"))
    base = next((x for x in entries if x["id"] == baseline_run), None)
    if base:
        tiles.append(("Baseline", f"<div class='value'>{pct(base['mean'])}</div>",
                      f"PdfCraft {commit_html(base['pdfcraft'], 8)}"))
    tiles_html = "".join(f"<div class='tile card'><div class='label'>{label}</div>{value}<div class='sub'>{sub}</div></div>"
                         for label, value, sub in tiles)

    # -- charts
    if points:
        fps = [fingerprint(p) for p in points]
        breaks = [i for i in range(1, len(points)) if fps[i] != fps[i - 1]]
        tags = [(p["pdfcraft"].get("tags") or [None])[0] for p in points]
        xlabels = [t or p["pdfcraft"]["sha"][:7] for t, p in zip(tags, points)]
        meta = [{"href": href(p), "aria": f"{name(p)}: mean {pct(p['mean'])}"} for p in points]
        main = line_svg([p["mean"] for p in points], "mean", width=960, height=280, margins=(40, 52, 14, 30),
                        most_ticks=6, xlabels=xlabels, tags=tags, breaks=breaks, meta=meta)
        minis = "".join(
            f"<figure class='chart card'><h3>{k}</h3>"
            + line_svg([({**(p.get("parts") or {}), **(p.get("halves") or {})}).get(k) for p in points], k, width=320,
                       height=150, margins=(30, 40, 12, 10), most_ticks=3, breaks=breaks, meta=meta)
            + "<div class='tip' hidden></div></figure>" for k in ("looks", "text", *PARTS))
        data = [{"run": p["id"], "href": href(p), "sha": p["pdfcraft"]["sha"][:10], "tags": p["pdfcraft"].get("tags") or [],
                 "date": when(p["pdfcraft"].get("date") or p["started"], False), "subject": p["pdfcraft"].get("subject") or "",
                 "mean": round(100 * p["mean"], 1),
                 "parts": {k: (None if v is None else round(100 * v, 1))
                           for k, v in {**(p.get("parts") or {}), **(p.get("halves") or {})}.items()}}
                for p in points]
        caption = ("The average score of all the test documents at each PdfCraft version tested, oldest first. Hover "
                   "over a point for details, or click it to open that version's report.")
        if breaks:
            caption += " A vertical rule marks where the corpus changed, so the means either side are of different PDFs."
        others = sum(1 for x in entries if not x.get("only") and x.get("environment") != env)
        if others:
            caption += f" {others} full runs from other environments are listed below but not plotted."
        charts = f"""<figure class="chart card"><div class="scroll">{main}</div><div class="tip" hidden></div>
<figcaption class="ink-2">{e(caption)}</figcaption></figure>"""
        parts_html = f"""<h3 class="section">Score parts</h3><div class="charts">{minis}</div>"""
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        script = f"<script type='application/json' id='points'>{payload}</script>{SCRIPT}"
    else:
        charts = "<div class='card empty'>No full-corpus runs yet: <code>suite.py run</code> makes the first.</div>"
        parts_html = ""
        script = ""

    latest = href(entries[0]) if entries else None
    script += SORT_SCRIPT
    return page_html(SITE, f"""
{site_header()}
<main>
<section class="intro">
<h1>How well does PdfCraft turn PDFs into other formats?</h1>
<p class="lead">We take a set of test documents, convert each one with PdfCraft, and check how close the result is to
the original PDF. Every document gets a score out of 100.</p>
{explainer()}
</section>
<section class="formats-grid">{format_cards(points)}</section>
<section class="format" id="docx">
<h2>Word <span class="pill">DOCX</span></h2>
<p class="lead">{takeaway(points)}</p>
<h3 class="section">Average score across PdfCraft versions</h3>
{charts}
<h3 class="section">The test documents</h3>
<p class="ink-2">Each card shows a test document's first page as a PDF and as the Word file PdfCraft made. Click one to
see every page side by side, and how it changed from version to version.</p>
<div class="sorter" role="group" aria-label="Sort the documents"><span class="ink-2">Sort:</span>
<button type="button" data-sort="score" aria-pressed="true">Lowest score first</button>
<button type="button" data-sort="title" aria-pressed="false">A to Z</button></div>
<div class="gallery" id="gallery">{gallery_html(points, public, corpus)}</div>
<details class="tech dev"><summary>For developers: score parts, every score and every run</summary>
<p class="ink-2">{len(entries)} runs so far · environment:
<span title="{e(env or '')}">{e((env or '?').split(':')[0])}</span>
{f' · <a href="{e(latest)}">latest report</a>' if latest else ''}</p>
<section class="tiles">{tiles_html}</section>
{parts_html}
{documents_table(points, href)}
<h3 class="section">All reports</h3>
{reports_table(entries, points, available, baseline_run)}
</details>
</section>
</main>""", script, CSS)


PART_NAMES = {"visual": ("Pages", "Each page next to the original's. A page that resembles it no more than an unrelated "
                                "page would counts 0."),
              "structure": ("Structure", "The same number of pages, page sizes and pictures."),
              "words": ("Words", "All the text there, with nothing extra."),
              "order": ("Reading order", "The text in the same order."),
              "format": ("Formatting", "The same bold, italic, size, colour and font.")}
HALF_NAMES = {"looks": "Does it look like the original?", "text": "Is the text all there?"}


def explainer():
    groups = []
    for half, parts in HALVES.items():
        weight = sum(WEIGHTS[k] for k in parts)
        items = "".join(f"<li><b>{PART_NAMES[k][0]}</b><span>{round(100 * WEIGHTS[k] / weight)} %</span>"
                        f"<span>{PART_NAMES[k][1]}</span></li>" for k in parts)
        groups.append(f"<h4>{HALF_NAMES[half]}</h4><ul class='weights'>{items}</ul>")
    return f"""<details class="tech explain"><summary>How is the score worked out?</summary>
<p>PdfCraft converts each document, and the result is opened in LibreOffice so its pages can be laid next to the
original's. Each document gets two scores out of 100, one for each question:</p>
{''.join(groups)}
<p>The overall score combines the two (as their geometric mean), so a document only scores well when it both looks
like the original and keeps its text: text that is all there cannot make up for pages that look nothing like it.</p>
<p>What a score means: 90 or more is <b>very close</b> to the original, 80 to 89 <b>close</b>, 70 to 79 has
<b>noticeable differences</b>, and below 70 <b>big differences</b>.</p>
<p>The tests always run with the same versions of every tool and font, so a score means the same from one PdfCraft
version to the next.</p></details>"""


def takeaway(points):
    """The Word results in two or three plain sentences."""
    if not points:
        return "There are no results yet."
    now = points[-1]
    text = (f"On average, the Word files PdfCraft makes are {verdict(now['mean'], sentence=True)}: "
            f"{pct(now['mean'])} out of 100 at {html.escape(version_label(now['pdfcraft']))}.")
    h = now.get("halves") or {}
    if h.get("looks") is not None and h.get("text") is not None:
        text += (f" The text comes through better than the look: {pct(h['text'])} for the text, {pct(h['looks'])} for "
                 "how the pages look." if h["text"] > h["looks"] else
                 f" The pages look better than the text comes through: {pct(h['looks'])} for the look, {pct(h['text'])} "
                 "for the text.")
    if len(points) > 1:
        d = now["mean"] - points[0]["mean"]
        first = html.escape(version_label(points[0]["pdfcraft"]))
        text += (f" That is {'up' if d > 0 else 'down'} {abs(100 * d):.1f} points since {first}, the first version tested."
                 if abs(d) >= 0.0005 else f" That is the same as at {first}, the first version tested.")
    bands = {}
    for score, status, *_ in now["docs"].values():
        label = verdict(score, status)
        bands[label] = bands.get(label, 0) + 1
    words = {"Very close": "are very close", "Close": "close", "Noticeable differences": "show noticeable differences",
             "Big differences": "show big differences", "Export failed": "could not be converted"}
    said = [f"{bands[k]} {words[k]}" for k in words if bands.get(k)]
    if said:
        text += f" Of the {len(now['docs'])} test documents, " + (", ".join(said[:-1]) + " and " + said[-1]
                                                                   if len(said) > 1 else said[0]) + "."
    return text


def doc_halves(rec, doc):
    """A document's halves: from its run's scores, or worked out from the parts in the history."""
    if rec and rec.get("halves"):
        return rec["halves"]
    return halves(dict(zip(PARTS, doc[3]))) if len(doc) > 3 else None


def gallery_html(points, public, corpus):
    """A card per test document of the latest tested commit: thumbnails of its first page as a PDF and
    as a Word file, its score and verdict, and how far it moved since the first version tested."""
    if not points:
        return ""
    e = html.escape
    now = points[-1]
    scores = Path(public) / now["id"] / "scores.json"
    recs = {r["name"]: r for r in json.loads(scores.read_text())} if scores.exists() else {}
    cards = []
    for name, (score, status, sha, *_) in sorted(now["docs"].items(), key=lambda x: x[1][0]):
        # The change since the first version tested with this same PDF.
        since = next(p for p in points if name in p["docs"] and p["docs"][name][2] == sha)
        change = score - since["docs"][name][0]
        moved = delta_html(change, f" since {e(version_label(since['pdfcraft']))}") if abs(change) >= 0.02 else ""
        thumbs = []
        shots = (recs.get(name) or {}).get("images") or [{}]
        for side, caption in (("pdf", "PDF"), ("docx", "Word")):
            src = shots[0].get(side) or (shots[0].get("ref") if side == "pdf" else None)
            if src:
                size = png_size(Path(public) / now["id"] / src)
                dims = f" width='{size[0]}' height='{size[1]}'" if size else ""
                thumbs.append(f"<figure><div class='frame'><img loading='lazy' src='{e(now['id'])}/{e(src)}'{dims} alt=''>"
                              f"</div><figcaption>{caption}</figcaption></figure>")
            else:
                thumbs.append(f"<figure><div class='frame'><div class='gap'><span>no page</span></div></div>"
                              f"<figcaption>{caption}</figcaption></figure>")
        title = title_of(name, corpus)
        cards.append(
            f"<a class='tcard card' href='docs/{e(safe_name(name))}.html' data-score='{100 * score:.2f}' data-title='{e(title)}'>"
            f"<div class='thumbs'>{''.join(thumbs)}</div><b class='ttitle'>{e(title)}</b>"
            f"<span class='tdesc'>{e((corpus.get(name) or {}).get('description') or '')}</span>"
            f"<div class='tscore'><span class='num'>{pct(score)}</span><span class='verdict'>{verdict(score, status)}</span></div>"
            f"<div class='meter'><span style='width:{100 * score:.1f}%'></span></div>"
            f"<span class='sub halves'>{halves_line(doc_halves(recs.get(name), now['docs'][name]))}</span>"
            f"{f'<span class=tchange>{moved}</span>' if moved else ''}</a>")
    return "".join(cards)


def sparkline(values, width=220, height=44):
    """The trend behind a headline number: a thin line, the latest point marked."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    xs = [round(4 + i * (width - 8) / (len(values) - 1), 1) for i in range(len(values))]
    ys = [round(height - 4 - (v - lo) / span * (height - 8), 1) for v in values]
    points = " ".join(f"{x},{y}" for x, y in zip(xs, ys))
    return (f"<svg class='spark' viewBox='0 0 {width} {height}' aria-hidden='true'><polyline points='{points}'/>"
            f"<circle cx='{xs[-1]}' cy='{ys[-1]}' r='4'/></svg>")


def format_cards(points):
    """A card per measured export format, with its latest mean score and trend, and one card listing
    the formats PdfCraft exports that are not measured yet."""
    cards = []
    for key, name, ext, measured in FORMATS:
        if not measured:
            continue
        if points:
            now = points[-1]
            change = (delta_html(now["mean"] - points[0]["mean"], f" since {html.escape(version_label(points[0]['pdfcraft']))}")
                      if len(points) > 1 else "the first version tested")
            body = (f"<div class='value hero'>{pct(now['mean'])}</div><div class='verdict'>{verdict(now['mean'])}</div>"
                    f"<div class='sub halves'>{halves_line(now.get('halves'))}</div>"
                    f"<div class='sub'>average score at {html.escape(version_label(now['pdfcraft']))} · {change}</div>"
                    f"{sparkline([p['mean'] for p in points[-12:]])}"
                    f"<div class='sub'>{len(now['docs'])} test documents · {len(points)} PdfCraft versions tested</div>")
        else:
            body = "<p class='ink-2'>No full-corpus run yet.</p>"
        cards.append(f"<a class='fcard card live' href='#{key}'><div class='fname'>{name} <span class='pill'>{ext}</span>"
                     f"</div>{body}<span class='go'>See the {name} results ↓</span></a>")
    coming = "".join(f"<li>{name} <span class='pill'>{ext}</span></li>" for _, name, ext, measured in FORMATS if not measured)
    if coming:
        cards.append(f"<div class='fcard card soon-card'><div class='fname'>Coming soon</div><p class='ink-2'>PdfCraft "
                     f"exports these too. They will be measured the same way.</p><ul class='coming'>{coming}</ul></div>")
    return "".join(cards)


def documents_table(points, href):
    """Every document of the latest tested commit, scored at the last MAX_COLUMNS tested commits.
    A tint marks a change from the previous column: blue up, red down; stronger from 2 points."""
    if not points:
        return ""
    e = html.escape
    cols = points[-MAX_COLUMNS:]
    head = []
    for c in cols:
        label = e((c["pdfcraft"].get("tags") or [None])[0] or c["pdfcraft"]["sha"][:7])
        if href(c):
            label = f"<a href='{e(href(c))}'>{label}</a>"
        head.append(f"<th>{label}<small>{when(c['pdfcraft'].get('date') or c['started'], False)}</small></th>")
    head = "".join(head)
    rows = []
    for doc in sorted(cols[-1]["docs"]):
        cells, first = [], None
        for j, col in enumerate(cols):
            now = col["docs"].get(doc)
            if now is None:
                cells.append("<td class='cell muted'>–</td>")
                continue
            prev = cols[j - 1]["docs"].get(doc) if j else None
            tint, mark, title = "", "", now[1]
            if prev and prev[2] != now[2]:
                mark, title = " ◆", "the PDF changed since the previous column"
            elif prev:
                d = now[0] - prev[0]
                if abs(d) >= 0.02:
                    tint, mark = ("u1", " ▲") if d > 0 else ("d1", " ▼")
                elif abs(d) >= 0.005:
                    tint = "u2" if d > 0 else "d2"
            if first is None or first[2] != now[2]:
                first = now
            page = f"docs/{safe_name(doc)}.html#to={col['pdfcraft']['sha'][:10]}"
            cells.append(f"<td class='cell {tint}' title='{e(title)}'><a href='{e(page)}'>{pct(now[0])}{mark}</a></td>")
        latest = cols[-1]["docs"][doc]
        label = f"<a href='docs/{e(safe_name(doc))}.html'>{e(doc)}</a>"
        rows.append(f"<tr><td class='l'>{label}</td>{''.join(cells)}<td>{delta_html(latest[0] - first[0])}</td></tr>")
    return f"""<h3 class="section">Documents</h3>
<p class="ink-2">Each PDF's score at the last {len(cols)} tested commits. A tint marks a change from the column before: blue
up, red down, darker and with an arrow from 2 points; ◆ means the PDF itself changed. The last column is the change
since the first column with the same PDF. Click a name for that file's history, or a score to see that commit's DOCX
next to the one before it.</p>
<div class="wrap"><table><thead><tr><th class="l">PDF</th>{head}<th>change</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>"""


def reports_table(entries, points, available, baseline_run):
    e = html.escape
    # Δ: a tested commit's change from the tested commit before it.
    steps = {cur["id"]: delta_html(cur["mean"] - prev["mean"]) + (" 🧩" if fingerprint(prev) != fingerprint(cur) else "")
             for prev, cur in zip(points, points[1:])}
    rows = []
    for entry in entries:
        pc = entry["pdfcraft"]
        change = steps.get(entry["id"], "")
        classes = entry.get("classes") or {}
        worse = classes.get("regression", 0) + classes.get("failed", 0)
        better = classes.get("improvement", 0) + classes.get("fixed", 0)
        against = " ".join(f"{CLASSES[c][0]} {n}" for c, n in (("regression", worse), ("improvement", better)) if n)
        if not against:
            against = "<span class='muted'>none</span>" if entry.get("classes") else "<span class='muted'>–</span>"
        report = (f"<a href='{e(entry['id'])}/report.html'>{when(entry['started'])}</a>" if entry["id"] in available
                  else f"<span class='muted' title='pruned'>{when(entry['started'])}</span>")
        flags = " <span class='pill'>baseline</span>" if entry["id"] == baseline_run else ""
        if entry.get("label"):
            flags += f" <span class='pill'>{e(entry['label'])}</span>"
        scope = f"<code>--only {e(entry['only'])}</code>" if entry.get("only") else "all"
        env = (entry.get("environment") or "?").split(":")[0]
        mean = pct(entry["mean"])
        if entry.get("scoring", 1) != VERSION:
            mean = f"<span class='muted' title='scored with an older formula'>{mean}*</span>"
        rows.append(f"<tr><td class='l'>{report}{flags}</td><td class='l'>{commit_html(pc, 8)}</td>"
                    f"<td class='l subject' title='{e(pc.get('subject') or '')}'>{e(pc.get('subject') or '')}</td>"
                    f"<td>{len(entry['docs'])}</td><td>{mean}</td><td>{change}</td><td>{against}</td>"
                    f"<td class='l'>{scope} <span class='muted'>· {e(env)}</span></td></tr>")
    return f"""<p class="ink-2">Newest first. Δ is a tested commit's change in mean score from the tested commit before it
(🧩: a different corpus). "vs baseline" counts the documents that got worse (🔴) or better (🟢) by 2 points or
more. Runs without a link have been pruned; their scores are still in the charts. * scored with an older formula
and no longer kept, so not comparable and not charted.</p>
<div class="wrap"><table><thead><tr><th class="l">Run</th><th class="l">PdfCraft</th><th class="l">Commit</th>
<th>PDFs</th><th>mean</th><th>Δ</th><th>vs baseline</th><th class="l">scope · where</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>"""
