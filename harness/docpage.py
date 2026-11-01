"""public/docs/<name>.html: one test file's history. Its score at every tested commit, and any two
of those commits side by side: the PDF, the DOCX at one commit and the DOCX at the other, page by
page. The pair is in the address (#from=<sha>&to=<sha>), so a comparison can be linked to; the
index links each score change to the comparison that shows it."""

import hashlib
import html
import json
from pathlib import Path

from .history import PARTS
from .score import HALVES, halves
from .index import CSS as CHART_CSS
from .index import SCRIPT as CHART_SCRIPT
from .index import line_svg
from .pipeline import safe_name
from .report import (SITE, THRESHOLD, commit_html, delta_html, halves_line, page_html, pct, png_size, site_header,
                     title_of,
                     verdict, when)

CSS = CHART_CSS + """
.pickers { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin-bottom: 6px; }
.pickers label { display: inline-flex; gap: 6px; align-items: center; color: var(--ink-2); }
.pickers select, .pickers button { font: inherit; color: var(--ink); background: var(--surface); padding: 4px 8px;
  border: 1px solid var(--axis); border-radius: 6px; }
.pickers button { cursor: pointer; }
#change { font-size: 22px; font-weight: 600; margin-left: auto; }
.cmp { scroll-margin-top: 52px; }
.cmp-head, .cmp .page { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.cmp-head { font-weight: 600; margin: 10px 0 6px; }
.cmp-head span { color: var(--ink-2); font-weight: 400; }
@media (max-width: 720px) { .cmp-head { display: none; } .cmp .page { grid-template-columns: 1fr; } }
.strip-card { padding: 14px 0 6px; }
.strip-controls { display: flex; gap: 10px; align-items: center; padding: 0 18px 12px; flex-wrap: wrap; }
.strip-controls input { flex: 1; min-width: 140px; max-width: 420px; accent-color: var(--series); }
.strip-controls button { font: inherit; color: var(--ink); background: var(--surface); padding: 2px 10px;
  border: 1px solid var(--axis); border-radius: 6px; cursor: pointer; }
.strip { --colw: clamp(150px, 29vw, 340px); display: grid; grid-auto-flow: column; grid-auto-columns: var(--colw);
  row-gap: 12px; overflow-x: auto; position: relative; scroll-snap-type: x mandatory;
  scroll-padding-left: var(--colw); padding-bottom: 12px; overscroll-behavior-x: contain; }
.strip > * { margin: 0; padding: 0 9px; min-width: 0; }
.strip .head { display: flex; flex-direction: column; justify-content: flex-end; gap: 1px; font-size: 13px; }
.strip .head b { font-size: 15px; }
.strip .head .num { font-size: 20px; }
.strip .head[data-v] { scroll-snap-align: start; }
.strip .pdf { position: sticky; left: 0; z-index: 2; background: var(--surface); padding-left: 18px;
  border-right: 1px solid var(--grid); box-shadow: 10px 0 12px -12px rgba(0, 0, 0, 0.35); }
.strip img { display: block; width: 100%; height: auto; border: 1px solid var(--border); background: #fff; }
.strip figcaption { font-size: 12px; color: var(--muted); margin-top: 3px; }
.strip .gap { min-height: 120px; height: calc(100% - 20px); }
.same { color: var(--muted); }
.changed { color: var(--ink); font-weight: 600; }
"""

SCRIPT = """<script>
(() => {
  const P = JSON.parse(document.getElementById("doc").textContent).points;
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const fmt = (v) => v == null ? "–" : v.toFixed(1);
  for (const sel of [$("from"), $("to")]) {
    for (const p of P) { const o = el("option", "", `${p.label} · ${p.date} · ${fmt(p.score)}`); o.value = p.sha; sel.append(o); }
  }
  // Without a pair in the address: the latest change of 2 points or more, else the latest change
  // at all, else the last two commits.
  function defaults() {
    for (const min of [2, 0.05])
      for (let i = P.length - 1; i > 0; i--) if (Math.abs(P[i].score - P[i - 1].score) >= min) return [i - 1, i];
    return [Math.max(0, P.length - 2), P.length - 1];
  }
  function pick() {
    const h = new URLSearchParams(location.hash.slice(1)), at = (s) => s ? P.findIndex((p) => p.sha.startsWith(s)) : -1;
    let f = at(h.get("from")), t = at(h.get("to"));
    if (f < 0 && t < 0) return defaults();
    if (t < 0) t = P.length - 1;
    if (f < 0) f = Math.max(0, t - 1);
    return [f, t];
  }
  function change(d, threshold, suffix) {
    if (Math.abs(d) < threshold) return el("span", "muted", (d >= 0 ? "+" : "") + d.toFixed(1) + suffix);
    return el("span", d > 0 ? "up" : "down", (d > 0 ? "▲ " : "▼ ") + Math.abs(d).toFixed(1) + suffix);
  }
  function gap(title, text, caption) {
    const f = el("figure"), g = el("div", "gap");
    g.append(el("b", "", title), el("span", "", text));
    f.append(g, el("figcaption", "", caption));
    return f;
  }
  function shot(src, size, caption) {
    const f = el("figure"), a = el("a"), img = el("img");
    a.href = src; img.src = src; img.loading = "lazy"; img.alt = caption;
    if (size) { img.width = size[0]; img.height = size[1]; }
    a.append(img); f.append(a, el("figcaption", "", caption));
    return f;
  }
  function docxSide(p, k) {
    const s = p.shots && p.shots.find((x) => x.page === k);
    const cap = `Word · page ${k} · ${p.label}`;
    if (s && s.docx) return shot(s.docx, s.docx_size, cap);
    if (p.status !== "ok" && p.status !== "render-failed") return gap("No Word file", `The export failed at ${p.label} (${p.status}).`, cap);
    if (!p.shots) return gap("Screenshots pruned", "Only the newest runs, the baseline's and tagged commits keep theirs.", cap);
    if (p.pages && k > p.pages[1]) return gap(`No page ${k} in this Word file`, `At ${p.label} the Word file ends on page ${p.pages[1]}.`, cap);
    return gap(`Page ${k} not shown`, "Past the report's --image-pages.", cap);
  }
  function render() {
    const [f, t] = pick(), a = P[f], b = P[t];
    $("from").value = a.sha; $("to").value = b.sha;
    $("change").replaceChildren(change(b.score - a.score, 2, ""));
    const parts = $("parts");
    parts.replaceChildren(...Object.keys(b.parts || a.parts || {}).map((k) => {
      const li = el("li", "", k), x = (a.parts || {})[k], y = (b.parts || {})[k];
      li.append(el("b", "", `${fmt(x)} → ${fmt(y)}`));
      if (x != null && y != null) { li.append(" "); li.append(change(y - x, 5, "")); }
      return li;
    }));
    const notes = [];
    if (a.pdf !== b.pdf) notes.push("The PDF itself changed between these commits (the corpus was regenerated), so part of the difference is the input.");
    if (a.pages && b.pages && a.pages[1] !== b.pages[1]) notes.push(`The Word file runs to ${a.pages[1]} page${a.pages[1] === 1 ? "" : "s"} at ${a.label} and ${b.pages[1]} at ${b.label}; the PDF has ${b.pages[0]}.`);
    $("note").hidden = !notes.length;
    $("note").replaceChildren(el("b", "", "⚠ Note"), notes.join(" "));
    const head = $("head");
    head.replaceChildren(el("div", "", "PDF"));
    for (const p of [a, b]) { const d = el("div", "", `Word at ${p.label} `); d.append(el("span", "", `· ${fmt(p.score)}`)); head.append(d); }
    const pdfOf = (k) => { for (const p of [b, a]) { const s = p.shots && p.shots.find((x) => x.page === k); if (s && s.pdf) return s; } return null; };
    const counts = [a, b].flatMap((p) => p.pages ? p.pages : []).concat([a, b].flatMap((p) => (p.shots || []).map((s) => s.page)));
    const n = Math.max(1, ...counts), rows = [];
    for (let k = 1; k <= n; k++) {
      const row = el("div", "page"), s = pdfOf(k), pdfPages = (b.pages || a.pages || [null])[0];
      row.append(s ? shot(s.pdf, s.pdf_size, `PDF · page ${k}`)
                   : pdfPages && k > pdfPages ? gap(`No page ${k} in the PDF`, `The PDF ends on page ${pdfPages}.`, `PDF · page ${k}`)
                   : gap("No screenshot", "Neither run kept this page.", `PDF · page ${k}`),
                 docxSide(a, k), docxSide(b, k));
      rows.push(row);
    }
    $("pages").replaceChildren(...rows);
  }
  $("from").addEventListener("change", () => { location.hash = `from=${$("from").value}&to=${$("to").value}`; });
  $("to").addEventListener("change", () => { location.hash = `from=${$("from").value}&to=${$("to").value}`; });
  $("swap").addEventListener("click", () => { location.hash = `from=${$("to").value}&to=${$("from").value}`; });
  window.addEventListener("hashchange", () => {
    render();
    const box = $("compare").getBoundingClientRect();  // a chart click: bring the comparison into view
    if (box.top < 0 || box.top > innerHeight * 0.6) $("compare").scrollIntoView({ behavior: "smooth" });
  });
  render();
  if (/(^|[#&])(from|to)=/.test(location.hash)) $("compare").scrollIntoView();  // a link to a comparison
})();
</script>"""


def strip_html(items):
    """The PDF's pages in a column pinned on the left, then one column per tested version, oldest
    first, scrolling sideways; pages line up in rows. Each column says how the version scored and
    whether its Word file changed from the version before."""
    e = html.escape
    pdf_from = next((it for it in reversed(items) if it["shots"]), None)
    n = max([len(it["shots"]) for it in items if it["shots"]] + [1])
    cells = [f"<div class='head pdf'><b>Original PDF</b><span class='muted'>what every version is compared with</span></div>"]
    pdf_shots = {s["page"]: s for s in (pdf_from or {}).get("shots") or []}
    for k in range(1, n + 1):
        s = pdf_shots.get(k)
        cells.append(f"<figure class='pdf'>{strip_image(s['pdf'], s.get('pdf_size'), f'PDF page {k}')}<figcaption>Page {k}"
                     f"</figcaption></figure>" if s and s.get("pdf") else
                     f"<figure class='pdf'><div class='gap'><span>No page {k}</span></div></figure>")
    previous = None
    for i, it in enumerate(items):
        hashes = tuple(s.get("docx_hash") for s in it["shots"]) if it["shots"] else None
        if hashes is None:
            mark = "<span class='muted'>screenshots pruned</span>"
        elif previous is None:
            mark = "<span class='muted'>first version tested</span>"
        elif hashes == previous:
            mark = "<span class='same'>same as before</span>"
        else:
            mark = "<span class='changed'>● changed</span>"
        previous = hashes
        change = delta_html((it["score"] - items[i - 1]["score"]) / 100) if i else ""
        halves_ = {k: it["parts"][k] / 100 for k in HALVES if (it["parts"] or {}).get(k) is not None}
        report = f" · <a href='{e(it['report'])}'>report</a>" if it["report"] else ""
        note = " · <span class='muted'>another PDF</span>" if pdf_from and it["pdf"] != pdf_from["pdf"] else ""
        cells.append(f"<div class='head' data-v='{i}' data-label='{e(it['label'])} · {it['score']:.1f}'>"
                     f"<b>{e(it['label'])}</b><span class='muted'>{it['date']}{report}{note}</span>"
                     f"<span><b class='num'>{it['score']:.1f}</b> {verdict(it['score'] / 100, it['status'])} {change}</span>"
                     f"<span class='halves'>{halves_line(halves_)}</span><span>{mark}</span></div>")
        shots = {s["page"]: s for s in it["shots"] or []}
        for k in range(1, n + 1):
            s = shots.get(k)
            if s and s.get("docx"):
                image = strip_image(s["docx"], s.get("docx_size"), f"Word page {k}, {it['label']}")
                cells.append(f"<figure>{image}<figcaption>Page {k}</figcaption></figure>")
            else:
                why = ("pruned" if it["shots"] is None else f"no page {k}" if it["pages"] and k > (it["pages"][1] or 0)
                       else "not shown")
                cells.append(f"<figure><div class='gap'><span>{why}</span></div></figure>")
    # The spacer after the last column lets every version scroll up to the pinned PDF.
    return f"""<section class="card strip-card">
<div class="strip-controls"><button type="button" id="strip-prev" aria-label="Older version">◀</button>
<input type="range" id="strip-slider" min="0" max="{len(items) - 1}" value="{len(items) - 1}" aria-label="Version">
<button type="button" id="strip-next" aria-label="Newer version">▶</button>
<span id="strip-at" class="ink-2"></span></div>
<div class="strip" id="strip" style="grid-template-rows: auto repeat({n}, auto);
grid-template-columns: repeat({len(items) + 1}, var(--colw)) max(0px, calc(100% - 2 * var(--colw)))">{''.join(cells)}
<div class="spacer" style="grid-row: 1 / -1" aria-hidden="true"></div></div>
</section>"""


def strip_image(src, size, alt):
    e = html.escape
    dims = f" width='{size[0]}' height='{size[1]}'" if size else ""
    return f"<a href='{e(src)}'><img loading='lazy' src='{e(src)}'{dims} alt='{e(alt)}'></a>"


STRIP_SCRIPT = """<script>
(() => {
  const strip = document.getElementById("strip");
  if (!strip) return;
  const heads = [...strip.querySelectorAll(".head[data-v]")];
  const slider = document.getElementById("strip-slider"), at = document.getElementById("strip-at");
  const pinned = () => strip.querySelector(".head.pdf").getBoundingClientRect().width;
  // The version whose column sits just right of the pinned PDF.
  const current = () => {
    const x = strip.scrollLeft + pinned() + 8;
    let i = 0;
    heads.forEach((h, k) => { if (h.offsetLeft <= x) i = k; });
    return i;
  };
  const go = (i) => strip.scrollTo({ left: heads[i].offsetLeft - pinned(), behavior: "smooth" });
  const show = () => { const i = current(); slider.value = i; at.textContent = "Showing from " + heads[i].dataset.label; };
  strip.addEventListener("scroll", show, { passive: true });
  slider.addEventListener("input", () => go(+slider.value));
  document.getElementById("strip-prev").addEventListener("click", () => go(Math.max(0, current() - 1)));
  document.getElementById("strip-next").addEventListener("click", () => go(Math.min(heads.length - 1, current() + 1)));
  // Open on the latest version, the one most readers come for.
  strip.scrollLeft = heads[heads.length - 1].offsetLeft - pinned();
  show();
})();
</script>"""


def build(points, public, corpus):
    """{file name: page} for every test file of the tested commits."""
    public = Path(public)
    kept = {}
    for p in points:
        scores = public / p["id"] / "scores.json"
        if scores.exists():
            kept[p["id"]] = {r["name"]: r for r in json.loads(scores.read_text())}
    names = sorted({name for p in points for name in p["docs"]})
    return {f"{safe_name(name)}.html": doc_page(name, points, kept, public, corpus) for name in names}


def series(name, points, kept, public):
    """The file at each tested commit that scored it, with its screenshots where the run is kept."""
    out = []
    for p in (p for p in points if name in p["docs"]):
        score, status, sha, *rest = p["docs"][name]
        rec = kept.get(p["id"], {}).get(name)
        shots = None
        if rec is not None:
            shots = []
            for s in rec.get("images", []):
                item = {"page": s["page"]}
                for side, src in (("pdf", s.get("pdf") or s.get("ref")), ("docx", s.get("docx"))):
                    if src:
                        item[side] = f"../{p['id']}/{src}"
                        item[f"{side}_size"] = png_size(public / p["id"] / src)
                if s.get("docx") and (public / p["id"] / s["docx"]).exists():
                    item["docx_hash"] = hashlib.md5((public / p["id"] / s["docx"]).read_bytes()).hexdigest()
                shots.append(item)
        parts = (rec or {}).get("components") or (dict(zip(PARTS, rest[0])) if rest else None)
        if parts:  # the halves first, then the parts they are made of
            parts = {**((rec or {}).get("halves") or halves(parts)), **parts}
        counts = (rec or {}).get("counts") or {}
        pc = p["pdfcraft"]
        out.append({
            "entry": p, "run": p["id"], "sha": pc["sha"][:10], "tags": pc.get("tags") or [],
            "label": (pc.get("tags") or [None])[0] or pc["sha"][:7],
            "date": when(pc.get("date") or p["started"], False), "subject": pc.get("subject") or "",
            "score": round(100 * score, 1), "status": status, "pdf": sha,
            "parts": {k: (None if v is None else round(100 * v, 1)) for k, v in parts.items()} if parts else None,
            "pages": [counts["pages_ref"], counts.get("pages_docx")] if counts.get("pages_ref") else None,
            "shots": shots, "report": f"../{p['id']}/report.html#{name}" if rec is not None else None,
        })
    return out


def doc_page(name, points, kept, public, corpus):
    e = html.escape
    items = series(name, points, kept, public)
    for i, it in enumerate(items):
        prev = items[i - 1]["sha"] if i else it["sha"]
        it["href"] = f"#from={prev}&to={it['sha']}"
        it["hint"] = "Click to compare with the version before" if i else "Click to show this version"
    latest, first = items[-1], items[0]
    moves = sum(1 for a, b in zip(items, items[1:]) if abs(b["score"] - a["score"]) >= 100 * THRESHOLD)
    latest_halves = {k: latest["parts"][k] / 100 for k in HALVES if (latest["parts"] or {}).get(k) is not None}
    tiles = [
        ("Latest score", f"<div class='value hero'>{latest['score']:.1f}</div>",
         f"<b>{verdict(latest['score'] / 100, latest['status'])}</b> · "
         + (f"<span class='halves'>{halves_line(latest_halves)}</span><br>" if latest_halves else "")
         + (delta_html((latest["score"] - first["score"]) / 100, f" since {e(first['label'])}") if len(items) > 1
            else "one version tested") + f"<br>PdfCraft {commit_html(latest['entry']['pdfcraft'], 8)}"),
        ("Lowest and highest", f"<div class='value'>{min(i['score'] for i in items):.1f}–{max(i['score'] for i in items):.1f}</div>",
         f"over {len(items)} PdfCraft version{'s' * (len(items) != 1)}"),
        ("Big changes", f"<div class='value'>{moves}</div>", "of 2 points or more from one version to the next"),
    ]
    tiles_html = "".join(f"<div class='tile card'><div class='label'>{label}</div>{value}<div class='sub'>{sub}</div></div>"
                         for label, value, sub in tiles)
    svg = line_svg([it["score"] / 100 for it in items], "score", width=960, height=240, margins=(40, 52, 14, 30),
                   most_ticks=5, xlabels=[it["label"] for it in items], tags=[(it["tags"] or [None])[0] for it in items],
                   meta=[{"href": it["href"], "aria": f"{it['label']}: {it['score']:.1f}"} for it in items])
    chart_data = [{"sha": it["sha"], "tags": it["tags"], "date": it["date"], "subject": it["subject"], "score": it["score"],
                   "parts": it["parts"] or {}, "href": it["href"], "hint": it["hint"]} for it in items]
    doc_data = {"points": [{k: it[k] for k in ("sha", "label", "date", "score", "status", "pdf", "parts", "pages", "shots")}
                           for it in items]}
    rows = []
    for i, it in enumerate(items):
        parts = it["parts"] or {}
        pages = f"{it['pages'][0]}→{it['pages'][1] if it['pages'][1] is not None else '?'}" if it["pages"] else "–"
        report = f"<a href='{e(it['report'])}'>report</a>" if it["report"] else "<span class='muted'>pruned</span>"
        compare = f"<a href='{e(it['href'])}'>compare</a>" if i else ""
        rows.append(f"<tr><td class='l'>{commit_html(it['entry']['pdfcraft'], 8)}</td><td class='l'>{it['date']}</td>"
                    f"<td>{it['score']:.1f}</td><td>{delta_html((it['score'] - items[i - 1]['score']) / 100) if i else ''}</td>"
                    + "".join(f"<td>{'–' if parts.get(k) is None else f'{parts[k]:.1f}'}</td>" for k in (*HALVES, *PARTS))
                    + f"<td>{pages}</td><td class='l'>{report} {compare}</td></tr>")
    payload = json.dumps(chart_data, ensure_ascii=False).replace("</", "<\\/")
    doc_payload = json.dumps(doc_data, ensure_ascii=False).replace("</", "<\\/")
    body = f"""
{site_header("../")}
<div class="toolbar"><div><a href="../index.html#docx">← All Word results</a>
<span class="muted">One test document over time</span></div></div>
<main>
<p class="muted">Word (DOCX) · test document</p>
<h1>{e(title_of(name, corpus))}</h1>
<p class="name">{e(name)}</p>
<p class="lead">{e((corpus.get(name) or {}).get('description') or '')}</p>
<section class="tiles">{tiles_html}</section>
<h2>Score across PdfCraft versions</h2>
<figure class="chart card"><div class="scroll">{svg}</div><div class="tip" hidden></div>
<figcaption class="ink-2">Click a point to compare that version with the one before it.</figcaption></figure>
<h2>Every version, side by side</h2>
<p class="ink-2">The original PDF stays on the left, next to the latest version. Scroll sideways, or use the slider,
to go back through earlier versions; a version marked "changed" made a different Word file from the one before it.</p>
{strip_html(items)}
<h2>Compare two versions</h2>
<p class="ink-2">Pick two PdfCraft versions to see the PDF next to the Word file each one made, page by page.</p>
<section class="card cmp" id="compare">
<div class="pickers"><label>From <select id="from"></select></label>
<button id="swap" type="button" aria-label="Swap from and to">⇄</button>
<label>To <select id="to"></select></label><span id="change"></span></div>
<ul class="parts" id="parts"></ul>
<p class="note" id="note" hidden></p>
<div class="cmp-head" id="head"></div>
<div id="pages"></div>
</section>
<details class="tech"><summary>Every version tested, in a table</summary>
<div class="wrap"><table><thead><tr><th class="l">PdfCraft</th><th class="l">committed</th><th>score</th><th>Δ</th>
{''.join(f'<th>{k}</th>' for k in (*HALVES, *PARTS))}<th>pages</th><th class="l"></th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></details>
</main>"""
    script = (f"<script type='application/json' id='points'>{payload}</script>"
              f"<script type='application/json' id='doc'>{doc_payload}</script>{CHART_SCRIPT}{SCRIPT}{STRIP_SCRIPT}")
    return page_html(f"{title_of(name, corpus)} · Word history · {SITE}", body, script, CSS)
