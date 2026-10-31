"""public/index.html: every report, and accuracy over the PdfCraft commits that were tested.

A tested commit is a full-corpus run (no --only) of a commit on main, made in the same environment
as the latest full run (scores from elsewhere are not comparable); when a commit was run more than
once, its latest run counts. The charts are inline SVG with a small script for the hover readout;
the tables below them hold every value, so nothing depends on hovering."""

import hashlib
import html
import json
import math

from .history import PARTS
from .report import CLASSES, commit_html, delta_html, page_html, pct, when

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
td.u1 { background: var(--up-wash); } td.u2 { background: var(--up-wash-2); }
td.d1 { background: var(--down-wash); } td.d2 { background: var(--down-wash-2); }
th small { display: block; font-weight: 400; color: var(--muted); }
.empty { padding: 28px; text-align: center; color: var(--ink-2); }
"""

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
      const p = data[i], v = key === "mean" ? p.mean : p.parts[key];
      cross.setAttribute("x1", xs[i]); cross.setAttribute("x2", xs[i]); cross.style.visibility = "visible";
      const strong = document.createElement("strong");
      strong.textContent = (v == null ? "–" : v.toFixed(1)) + (key === "mean" ? " mean score" : " " + key);
      tip.replaceChildren(strong, row("", (p.tags.length ? p.tags.join(", ") + " · " : "") + p.sha + " · " + p.date),
                          row("s", p.subject), row("m", p.href ? "Click to open the report" : "Report pruned"));
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
    full = sorted((e for e in entries if not e.get("only")), key=lambda e: e["started"])
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


def html_index(entries, available, baseline_run):
    e = html.escape
    entries = sorted(entries, key=lambda x: x["started"], reverse=True)
    points, env = tested_commits(entries)

    def href(entry, anchor=""):
        return f"{entry['id']}/report.html{anchor}" if entry["id"] in available else None

    def name(entry):
        pc = entry["pdfcraft"]
        return (pc.get("tags") or [None])[0] or (pc.get("sha") or "")[:7] or "custom"

    # -- tiles
    tiles = []
    if points:
        now = points[-1]
        sub = (delta_html(now["mean"] - points[-2]["mean"], f" since {e(name(points[-2]))}") if len(points) > 1
               else "the first tested commit")
        tiles.append(("Latest mean score", f"<div class='value hero'>{pct(now['mean'])}</div>",
                      f"{sub}<br>PdfCraft {commit_html(now['pdfcraft'], 8)}"))
        tiles.append(("Tested commits", f"<div class='value'>{len(points)}</div>",
                      f"from {when(points[0]['pdfcraft'].get('date') or points[0]['started'], False)} "
                      f"to {when(now['pdfcraft'].get('date') or now['started'], False)}"))
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
            + line_svg([(p.get("parts") or {}).get(k) for p in points], k, width=320, height=150,
                       margins=(30, 40, 12, 10), most_ticks=3, breaks=breaks, meta=meta)
            + "<div class='tip' hidden></div></figure>" for k in PARTS)
        data = [{"run": p["id"], "href": href(p), "sha": p["pdfcraft"]["sha"][:10], "tags": p["pdfcraft"].get("tags") or [],
                 "date": when(p["pdfcraft"].get("date") or p["started"], False), "subject": p["pdfcraft"].get("subject") or "",
                 "mean": round(100 * p["mean"], 2),
                 "parts": {k: (None if v is None else round(100 * v, 2)) for k, v in (p.get("parts") or {}).items()}}
                for p in points]
        caption = ("Mean score of the corpus for each tested PdfCraft commit on main, oldest first; hover for details, "
                   "click a point for its report.")
        if breaks:
            caption += " A vertical rule marks where the corpus changed, so the means either side are of different PDFs."
        others = sum(1 for x in entries if not x.get("only") and x.get("environment") != env)
        if others:
            caption += f" {others} full runs from other environments are listed below but not plotted."
        charts = f"""<figure class="chart card"><div class="scroll">{main}</div><div class="tip" hidden></div>
<figcaption class="ink-2">{e(caption)}</figcaption></figure>
<h2>Score parts</h2><div class="charts">{minis}</div>"""
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        script = f"<script type='application/json' id='points'>{payload}</script>{SCRIPT}"
    else:
        charts = "<div class='card empty'>No full-corpus runs yet: <code>suite.py run</code> makes the first.</div>"
        script = ""

    return page_html("DOCX export reports", f"""
<main>
<p class="muted">PdfCraft DOCX export test suite · {len(entries)} runs ·
<span title="{e(env or '')}">{e((env or '?').split(':')[0])}</span></p>
<h1>DOCX export accuracy</h1>
{f'<p><a href="{e(href(entries[0]))}">Latest report →</a></p>' if entries and href(entries[0]) else ''}
<section class="tiles">{tiles_html}</section>
<h2>Mean score over PdfCraft commits</h2>
{charts}
{documents_table(points, href)}
<h2>All reports</h2>
{reports_table(entries, points, available, baseline_run)}
</main>""", script, CSS)


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
            cells.append(f"<td class='cell {tint}' title='{e(title)}'>{pct(now[0])}{mark}</td>")
        latest = cols[-1]["docs"][doc]
        link = href(cols[-1], f"#{doc}")
        label = f"<a href='{e(link)}'>{e(doc)}</a>" if link else e(doc)
        rows.append(f"<tr><td class='l'>{label}</td>{''.join(cells)}<td>{delta_html(latest[0] - first[0])}</td></tr>")
    return f"""<h2>Documents</h2>
<p class="ink-2">Each PDF's score at the last {len(cols)} tested commits. A tint marks a change from the column before: blue
up, red down, darker and with an arrow from 2 points; ◆ means the PDF itself changed. The last column is the change
since the first column with the same PDF.</p>
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
        rows.append(f"<tr><td class='l'>{report}{flags}</td><td class='l'>{commit_html(pc, 8)}</td>"
                    f"<td class='l subject' title='{e(pc.get('subject') or '')}'>{e(pc.get('subject') or '')}</td>"
                    f"<td>{len(entry['docs'])}</td><td>{pct(entry['mean'])}</td><td>{change}</td><td>{against}</td>"
                    f"<td class='l'>{scope} <span class='muted'>· {e(env)}</span></td></tr>")
    return f"""<p class="ink-2">Newest first. Δ is a tested commit's change in mean score from the tested commit before it
(🧩: a different corpus). "vs baseline" counts the documents that got worse (🔴) or better (🟢) by 2 points or
more. Runs without a link have been pruned; their scores are still in the charts.</p>
<div class="wrap"><table><thead><tr><th class="l">Run</th><th class="l">PdfCraft</th><th class="l">Commit</th>
<th>PDFs</th><th>mean</th><th>Δ</th><th>vs baseline</th><th class="l">scope · where</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>"""
