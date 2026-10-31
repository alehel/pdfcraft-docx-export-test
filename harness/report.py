"""Baseline comparison and the run's reports (report.md, report.html)."""

import datetime as dt
import html
import re
import struct
from pathlib import Path
from urllib.parse import quote

from .pipeline import safe_name
from .score import WEIGHTS

THRESHOLD = 0.02           # a score change of 2 points or more is a regression / improvement
COMPONENT_THRESHOLD = 0.05  # component changes worth mentioning
FAILED = ("export-failed", "invalid-docx", "error")

CLASSES = {
    "regression": ("🔴", "regression"),
    "failed": ("🔴", "now failing"),
    "still-failing": ("🟠", "still failing"),
    "improvement": ("🟢", "improvement"),
    "fixed": ("🟢", "fixed"),
    "new": ("🆕", "new in the corpus"),
    "corpus-changed": ("🧩", "PDF changed since the baseline"),
    "same": ("⚪", "unchanged"),
}
ORDER = list(CLASSES)


def classify(records, baseline):
    """Set rec["class"], rec["delta"] and rec["changes"] against the baseline's documents."""
    base_docs = (baseline or {}).get("docs", {})
    for rec in records:
        base = base_docs.get(rec["name"])
        rec["changes"] = []
        rec["delta"] = None
        failed = rec["status"] in FAILED
        if base is None:
            rec["class"] = "failed" if failed else "new"
            continue
        if base["sha256"] != rec["sha256"]:
            rec["class"] = "corpus-changed"
            continue
        rec["delta"] = rec["score"] - base["score"]
        was_failed = base["status"] in FAILED
        if failed:
            rec["class"] = "still-failing" if was_failed else "failed"
        elif was_failed:
            rec["class"] = "fixed"
        elif rec["delta"] <= -THRESHOLD:
            rec["class"] = "regression"
        elif rec["delta"] >= THRESHOLD:
            rec["class"] = "improvement"
        else:
            rec["class"] = "same"
        for group in ("components", "details"):
            now, before = rec.get(group) or {}, base.get(group) or {}
            for k, v in now.items():
                b = before.get(k)
                if v is not None and b is not None and abs(v - b) >= COMPONENT_THRESHOLD:
                    rec["changes"].append((k, b, v))
    return records


def pct(x):
    return "–" if x is None else f"{100 * x:.1f}"


def signed(d):
    return "" if d is None else f"{100 * d:+.1f}"


def sort_key(rec):
    return ORDER.index(rec.get("class", "same")), rec["name"]


def summary_counts(records):
    counts = {}
    for rec in records:
        counts[rec.get("class", "same")] = counts.get(rec.get("class", "same"), 0) + 1
    return counts


def mean_score(records):
    return sum(r["score"] for r in records) / len(records) if records else 0.0


def header_lines(meta, baseline):
    pc = meta["pdfcraft"]
    tags = f" (tag {', '.join(pc['tags'])})" if pc.get("tags") else (f" ({pc['describe']})" if pc.get("describe") else "")
    lines = [f"- PdfCraft: `{pc.get('sha', '')[:10]}`{tags} {pc.get('subject', '')} ({pc.get('cli')})",
             f"- Oracles: Poppler {meta['oracles'].get('poppler')}, LibreOffice {meta['oracles'].get('libreoffice')}",
             f"- Environment: {meta.get('environment', '?')}"]
    if baseline:
        bm = baseline["meta"]
        lines.append(f"- Baseline: run `{bm.get('run')}`, PdfCraft `{bm['pdfcraft'].get('sha', '')[:10]}` "
                     f"{bm['pdfcraft'].get('subject', '')}")
        if bm.get("environment") != meta.get("environment"):
            lines.append(f"- ⚠️ The baseline was made elsewhere ({bm.get('environment', '?')}): fonts and tools differ, "
                         "so score changes may come from them, not from PdfCraft")
        elif bm.get("oracles") != meta["oracles"]:
            lines.append(f"- ⚠️ The oracles changed since the baseline ({bm.get('oracles')}): "
                         "score changes may come from them, not from PdfCraft")
    else:
        lines.append("- Baseline: none yet (`suite.py accept` makes one)")
    if meta.get("only"):
        lines.append(f"- Subset: `--only {meta['only']}`")
    return lines


def markdown(meta, records, baseline):
    out = [f"# {SITE}: Word (DOCX), run {meta['id']}", ""]
    out += header_lines(meta, baseline)
    counts = summary_counts(records)
    out += ["", f"**{len(records)} PDFs, mean score {pct(mean_score(records))}**  " +
            " · ".join(f"{CLASSES[c][0]} {n} {CLASSES[c][1]}" for c, n in sorted(counts.items(), key=lambda x: ORDER.index(x[0]))),
            "", "Scores are 0-100. Weights: " + ", ".join(f"{k} {int(v * 100)} %" for k, v in WEIGHTS.items()) + ".", "",
            "| | PDF | score | Δ | words | order | format | visual | structure | pages | status |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for rec in sorted(records, key=sort_key):
        c = rec.get("components") or {}
        counts_ = rec.get("counts") or {}
        pages = f"{counts_.get('pages_ref', '?')}→{counts_.get('pages_docx') if counts_.get('pages_docx') is not None else '?'}" if counts_ else ""
        out.append(f"| {CLASSES[rec['class']][0]} | {rec['name']} | {pct(rec['score'])} | {signed(rec['delta'])} | "
                   f"{pct(c.get('words'))} | {pct(c.get('order'))} | {pct(c.get('format'))} | {pct(c.get('visual'))} | "
                   f"{pct(c.get('structure'))} | {pages} | {rec['status']} |")
    out += ["", "## Details", ""]
    for rec in sorted(records, key=sort_key):
        out += detail_markdown(rec)
    return "\n".join(out) + "\n"


def detail_markdown(rec):
    out = [f"### {CLASSES[rec['class']][0]} {rec['name']} — {pct(rec['score'])}"
           + (f" ({signed(rec['delta'])})" if rec["delta"] is not None else ""), ""]
    if rec.get("error"):
        out.append(f"- **{rec['status']}**: {rec['error']}")
    for k, before, now in rec.get("changes", []):
        out.append(f"- {k}: {pct(before)} → {pct(now)}")
    d = rec.get("details") or {}
    if d:
        out.append("- " + ", ".join(f"{k} {pct(v)}" for k, v in d.items() if v is not None))
    counts = rec.get("counts") or {}
    if counts:
        out.append(f"- words {counts['words_ref']} → {counts['words_docx']}, images {counts['images_ref']} → "
                   f"{counts['images_docx']}, positioned pages {counts['positioned_pages']}, "
                   f"export {rec['export']['seconds']} s")
    docx = rec.get("docx") or {}
    for w in docx.get("warnings", []):
        out.append(f"- Word lint: {w}")
    for e in docx.get("errors", [])[1:]:
        out.append(f"- package error: {e}")
    truncated = ((rec.get("export") or {}).get("result") or {}).get("truncated_pages")
    if truncated:
        out.append(f"- truncated pages: {truncated}")
    if rec.get("missing"):
        out.append("- missing words: " + ", ".join(f"`{w}`×{n}" if n > 1 else f"`{w}`" for w, n in rec["missing"]))
    if rec.get("extra"):
        out.append("- extra words: " + ", ".join(f"`{w}`×{n}" if n > 1 else f"`{w}`" for w, n in rec["extra"]))
    worst = sorted((p for p in rec.get("pages", []) if p["visual"] is not None), key=lambda p: p["visual"])[:3]
    if len(rec.get("pages", [])) > 1 and worst:
        out.append("- weakest pages (visual): " + ", ".join(f"p{p['page']} {pct(p['visual'])}" for p in worst))
    for note in rec.get("notes", []):
        out.append(f"- note: {note}")
    out.append("")
    return out


# -- HTML -----------------------------------------------------------------------------------------
# Colours are the roles of the dataviz reference palette, with dark-mode steps of their own. Scores
# are plain ink; a change also carries its sign and an arrow, so colour never says it alone.

LIGHT = """color-scheme: light; --surface: #fcfcfb; --plane: #f9f9f7; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --series: #2a78d6;
  --track: #cde2fb; --warn: #fab219; --up: #006300; --down: #d03b3b; --up-wash: rgba(42,120,214,0.18);
  --up-wash-2: rgba(42,120,214,0.07); --down-wash: rgba(227,73,72,0.18); --down-wash-2: rgba(227,73,72,0.07);"""
DARK = """color-scheme: dark; --surface: #1a1a19; --plane: #0d0d0d; --ink: #f0efec; --ink-2: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --series: #3987e5;
  --track: #184f95; --warn: #fab219; --up: #0ca30c; --down: #e66767; --up-wash: rgba(57,135,229,0.26);
  --up-wash-2: rgba(57,135,229,0.10); --down-wash: rgba(230,103,103,0.26); --down-wash-2: rgba(230,103,103,0.10);"""

CSS = f"""
:root {{ {LIGHT} }}
@media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) {{ {DARK} }} }}
:root[data-theme="dark"] {{ {DARK} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--plane); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1240px; margin: 0 auto; padding: 18px 16px 64px; }}
a {{ color: inherit; text-decoration-color: var(--axis); text-underline-offset: 2px; }}
a:hover {{ text-decoration-color: currentColor; }}
h1 {{ font-size: 24px; margin: 2px 0 4px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
h2 {{ font-size: 17px; margin: 36px 0 10px; }}
h3 {{ font-size: 15px; margin: 0; }}
p {{ margin: 0 0 6px; }}
.muted {{ color: var(--muted); }} .ink-2 {{ color: var(--ink-2); }}
code, .sha {{ font: 12.5px ui-monospace, SFMono-Regular, Menlo, monospace; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
.pill {{ display: inline-block; padding: 0 8px; border: 1px solid var(--border); border-radius: 999px;
  font-size: 12px; line-height: 19px; color: var(--ink-2); white-space: nowrap; text-decoration: none;
  font-weight: 500; vertical-align: 1px; }}
.pill.tag {{ color: var(--ink); font-weight: 600; }}
.pill.tag::before {{ content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--series); margin-right: 5px; vertical-align: 0; }}
.up {{ color: var(--up); font-weight: 600; }} .down {{ color: var(--down); font-weight: 600; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 16px 0; }}
.tile .label {{ color: var(--ink-2); font-size: 13px; }}
.tile .value {{ font-size: 28px; font-weight: 600; line-height: 1.25; }}
.tile .value.hero {{ font-size: 48px; line-height: 1.1; }}
.tile .sub {{ font-size: 13px; color: var(--ink-2); }}
ul.meta {{ color: var(--ink-2); margin: 0; padding-left: 18px; }}
.wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }}
table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; background: var(--surface); }}
th, td {{ padding: 6px 9px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }}
tbody tr:last-child td {{ border-bottom: 0; }}
th {{ color: var(--ink-2); font-weight: 600; font-size: 13px; position: sticky; top: 0; background: var(--surface); }}
th.l, td.l {{ text-align: left; }}
td.subject {{ max-width: 200px; overflow: hidden; text-overflow: ellipsis; }}
.toolbar {{ position: sticky; top: 0; z-index: 3; background: var(--plane); border-bottom: 1px solid var(--grid); }}
.toolbar > div {{ max-width: 1240px; margin: 0 auto; padding: 8px 16px; display: flex; gap: 8px 20px;
  align-items: center; flex-wrap: wrap; }}
.toolbar label {{ display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }}
.doc {{ margin: 14px 0; scroll-margin-top: 52px; }}
.doc-head {{ display: flex; justify-content: space-between; gap: 8px 24px; align-items: flex-start; flex-wrap: wrap; }}
.doc-head .desc {{ color: var(--ink-2); margin: 3px 0 0; max-width: 720px; }}
.score {{ text-align: right; }}
.score .num {{ font-size: 30px; font-weight: 600; line-height: 1.1; }}
.score .num small {{ font-size: 14px; color: var(--muted); font-weight: 400; }}
.meter {{ width: 150px; height: 6px; border-radius: 3px; background: var(--track); margin: 6px 0 4px auto; overflow: hidden; }}
.meter span {{ display: block; height: 100%; background: var(--series); border-radius: 3px; }}
.parts {{ display: flex; flex-wrap: wrap; gap: 4px 18px; margin: 10px 0 12px; padding: 0; list-style: none;
  color: var(--ink-2); font-size: 13px; }}
.parts b {{ color: var(--ink); font-variant-numeric: tabular-nums; margin-left: 4px; }}
.page {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-bottom: 14px; }}
.page figure {{ margin: 0; display: flex; flex-direction: column; }}
.page img {{ display: block; width: 100%; height: auto; border: 1px solid var(--border); background: #fff; }}
.gap {{ flex: 1; min-height: 180px; display: flex; flex-direction: column; justify-content: center; align-items: center;
  gap: 6px; padding: 24px; text-align: center; color: var(--ink-2); border: 1px dashed var(--axis); border-radius: 4px;
  background: repeating-linear-gradient(135deg, transparent 0 9px, var(--grid) 9px 10px); }}
.gap b {{ color: var(--ink); font-size: 15px; }}
.gap span {{ max-width: 260px; }}
.note {{ margin: 0 0 12px; padding: 7px 12px; border-left: 3px solid var(--warn); border-radius: 0 6px 6px 0;
  background: var(--plane); color: var(--ink-2); }}
.note b {{ color: var(--ink); margin-right: 6px; }}
header.site {{ background: var(--surface); border-bottom: 1px solid var(--border); }}
header.site > div {{ max-width: 1240px; margin: 0 auto; padding: 10px 16px; display: flex; gap: 8px 24px;
  align-items: center; flex-wrap: wrap; }}
.brand {{ display: inline-flex; gap: 10px; align-items: center; text-decoration: none; font-size: 16px; color: var(--ink); }}
.brand b {{ font-weight: 700; }}
.mark {{ width: 26px; height: 26px; flex: none; }}
nav.formats {{ display: flex; gap: 6px 12px; align-items: center; flex-wrap: wrap; margin-left: auto; font-size: 13px; }}
.fmt {{ padding: 2px 11px; border-radius: 999px; border: 1px solid var(--border); text-decoration: none; color: var(--ink-2); }}
.fmt.on {{ background: var(--ink); border-color: var(--ink); color: var(--surface); font-weight: 600; }}
.fmt small {{ opacity: 0.75; margin-left: 2px; }}
.soon {{ color: var(--muted); }}
figcaption {{ color: var(--ink-2); font-size: 12px; margin-top: 4px; }}
.page figure.diff {{ display: none; }}
body:has(#overlays:checked) .page {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
body:has(#overlays:checked) .page figure.diff {{ display: flex; }}
body:has(#changed:checked) [data-quiet] {{ display: none; }}
details.more {{ margin-top: 4px; }}
details.more summary {{ cursor: pointer; color: var(--ink-2); }}
details.more ul {{ margin: 6px 0 0; padding-left: 18px; }}
@media (max-width: 720px) {{ .page, body:has(#overlays:checked) .page {{ grid-template-columns: 1fr; }}
  .score {{ text-align: left; }} .meter {{ margin-left: 0; }} }}
"""

QUIET = ("same", "new", "corpus-changed")  # hidden by "Only changed or failing"

SITE = "PdfCraft Export Quality Measure"
# What PdfCraft's doc_export_office writes, and whether the suite measures it yet. A measured
# format has a section on the index (index.html#<key>).
FORMATS = [("docx", "Word", "DOCX", True), ("pptx", "PowerPoint", "PPTX", False), ("html", "HTML", "HTML", False),
           ("rtf", "Rich Text", "RTF", False), ("md", "Markdown", "MD", False)]
MARK = ("<svg class='mark' viewBox='0 0 26 26' aria-hidden='true'><rect x='2' y='2' width='13' height='17' rx='2' "
        "fill='var(--series)'/><rect x='10' y='7' width='13' height='17' rx='2' fill='var(--surface)' stroke='var(--series)' "
        "stroke-width='2'/><path d='M13.5 13h6M13.5 16.5h6M13.5 20h4' stroke='var(--series)' stroke-width='1.6' "
        "stroke-linecap='round'/></svg>")


def site_header(root="", active="docx"):
    """The bar on every page: the site's name, the measured formats, and the ones still to come."""
    live = ""
    for key, name, ext, measured in FORMATS:
        if measured:
            current = " on' aria-current='page" if key == active else ""
            live += f"<a class='fmt{current}' href='{root}index.html#{key}'>{name} <small>{ext}</small></a>"
    soon = " · ".join(name for _, name, _, measured in FORMATS if not measured)
    return (f"<header class='site'><div><a class='brand' href='{root}index.html'>{MARK}"
            f"<span><b>PdfCraft</b> Export Quality Measure</span></a>"
            f"<nav class='formats' aria-label='Export formats'>{live}"
            f"<span class='soon'>Coming soon: {soon}</span></nav></div></header>")


def page_html(title, body, script="", css=""):
    return (f"<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}{css}</style></head><body>{body}{script}</body></html>\n")


def delta_html(d, suffix=""):
    """A score change: arrow and colour from 2 points (a regression or an improvement), plain below."""
    if d is None:
        return ""
    if abs(d) < THRESHOLD:
        return f"<span class='muted'>{100 * d:+.1f}{suffix}</span>"
    return f"<span class='{'up' if d > 0 else 'down'}'>{'▲' if d > 0 else '▼'} {abs(100 * d):.1f}{suffix}</span>"


def when(iso, with_time=True):
    """An ISO time as UTC, e.g. 2026-10-31 05:26 UTC."""
    if not iso:
        return ""
    t = dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)
    return f"{t:%Y-%m-%d %H:%M} UTC" if with_time else f"{t:%Y-%m-%d}"


def commit_html(pc, short=10):
    """The tested PdfCraft commit as links: the commit, its tags and the pull request it came from."""
    e = html.escape
    sha, url = pc.get("sha") or "", pc.get("url")
    if not sha:
        return f"<span class='ink-2'>{e(pc.get('subject') or pc.get('cli') or 'unknown build')}</span>"
    out = (f"<a class='sha' href='{e(url)}/commit/{sha}'>{sha[:short]}</a>" if url
           else f"<span class='sha'>{sha[:short]}</span>")
    for tag in pc.get("tags") or []:
        out += (f" <a class='pill tag' href='{e(url)}/releases/tag/{e(quote(tag))}'>{e(tag)}</a>" if url
                else f" <span class='pill tag'>{e(tag)}</span>")
    pr = re.fullmatch(r"pull/(\d+)", pc.get("ref") or "")
    if pr:
        out += (f" <a class='pill' href='{e(url)}/pull/{pr[1]}'>PR #{pr[1]}</a>" if url
                else f" <span class='pill'>PR #{pr[1]}</span>")
    return out


def commit_line(pc):
    """Subject, commit date and where the commit sits, for under a commit_html()."""
    bits = [html.escape(pc.get("subject") or "")]
    if pc.get("date"):
        bits.append(f"committed {when(pc['date'])}")
    if pc.get("describe") and not pc.get("tags"):
        bits.append(f"no tag ({html.escape(pc['describe'])})")
    if pc.get("ref") and not re.fullmatch(r"pull/\d+", pc["ref"]):
        bits.append(f"built from <code>{html.escape(pc['ref'])}</code>")
    if pc.get("on_main") is False:
        bits.append("not on main")
    return " · ".join(b for b in bits if b)


def html_report(meta, records, baseline, descriptions=None, run_dir=None):
    e = html.escape
    descriptions = descriptions or {}
    pc = meta["pdfcraft"]
    counts = summary_counts(records)
    deltas = [r["delta"] for r in records if r["delta"] is not None]
    changed = sum(counts.get(c, 0) for c in ("regression", "failed", "still-failing", "improvement", "fixed"))
    tiles = [
        ("Mean score", f"<div class='value hero'>{pct(mean_score(records))}</div>",
         delta_html(sum(deltas) / len(deltas), " vs baseline") if deltas else "no baseline to compare with"),
        ("PDFs", f"<div class='value'>{len(records)}</div>",
         f"{sum(1 for r in records if r['status'] in FAILED)} failed to export or score"),
        ("Against the baseline", f"<div class='value'>{changed}</div>",
         " · ".join(f"{CLASSES[c][0]} {n} {CLASSES[c][1]}" for c, n in
                    sorted(counts.items(), key=lambda x: ORDER.index(x[0])))),
    ]
    tiles_html = "".join(f"<div class='tile card'><div class='label'>{label}</div>{value}<div class='sub'>{sub}</div></div>"
                         for label, value, sub in tiles)
    meta_lines = [line[2:] for line in header_lines(meta, baseline)[1:]]
    suite = meta.get("suite")
    if suite:
        meta_lines.append(f"Suite: `{suite['sha'][:10]}`" + (" with uncommitted changes" if suite.get("dirty") else ""))
    meta_lines.append("Scores 0-100; weights " + ", ".join(f"{k} {int(v * 100)} %" for k, v in WEIGHTS.items()))
    rows = []
    for rec in sorted(records, key=sort_key):
        c = rec.get("components") or {}
        n = rec.get("counts") or {}
        pages = f"{n.get('pages_ref', '?')}→{n['pages_docx'] if n.get('pages_docx') is not None else '?'}" if n else ""
        quiet = " data-quiet" if rec["class"] in QUIET else ""
        rows.append(
            f"<tr{quiet}><td class='l'>{CLASSES[rec['class']][0]}</td><td class='l'><a href='#{e(rec['name'])}'>{e(rec['name'])}</a></td>"
            f"<td>{pct(rec['score'])}</td><td>{delta_html(rec['delta'])}</td>"
            + "".join(f"<td>{pct(c.get(k))}</td>" for k in WEIGHTS)
            + f"<td>{pages}</td><td class='l'>{e(rec['status'])}</td></tr>")
    title = (pc.get("tags") or [None])[0] or (pc.get("sha") or "")[:10] or "custom build"
    body = f"""
{site_header("../")}
<div class="toolbar"><div>
<a href="../index.html#docx">← All DOCX reports</a>
<label><input type="checkbox" id="overlays"> Overlays</label>
<label><input type="checkbox" id="changed"> Only changed or failing</label>
<span class="muted">Overlay: red is ink only in the PDF, blue only in the DOCX</span>
</div></div>
<main>
<p class="muted">Word (DOCX) export report · run {e(meta['id'])} · {when(meta.get('started'))} · {meta.get('seconds', 0):.0f} s</p>
<h1>PdfCraft {commit_html(pc)}</h1>
<p class="ink-2">{commit_line(pc)}</p>
<section class="tiles">{tiles_html}</section>
<ul class="meta">{''.join(f'<li>{inline_md(line)}</li>' for line in meta_lines)}</ul>
<h2>Summary</h2>
<div class="wrap"><table><thead><tr><th class="l"></th><th class="l">PDF</th><th>score</th><th>Δ</th>
{''.join(f'<th>{k}</th>' for k in WEIGHTS)}<th>pages</th><th class="l">status</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Documents</h2>
{''.join(doc_html(rec, descriptions.get(rec['name']), run_dir, pc.get('sha')) for rec in sorted(records, key=sort_key))}
</main>"""
    return page_html(f"{title} · DOCX report · {SITE}", body)


def inline_md(text):
    """`code` spans of a markdown line as <code>."""
    parts = html.escape(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


def doc_html(rec, description, run_dir=None, sha=None):
    """One document: score, parts, then each page as PDF | DOCX (| overlay), then the details."""
    e = html.escape
    c = rec.get("components") or {}
    if rec["class"] == "corpus-changed":
        change = "<span class='muted'>PDF changed since the baseline</span>"
    elif rec["class"] == "new":
        change = "<span class='muted'>new since the baseline</span>"
    else:
        change = delta_html(rec["delta"], " vs baseline")
    items = "".join(f"<li>{inline_md(line[2:])}</li>" for line in detail_markdown(rec)[2:] if line.startswith("- "))
    links = [f"<a href='{e(rec[k])}'>{label}</a>" for k, label in (("docx_file", "DOCX"), ("rendered_file", "LibreOffice PDF"))
             if rec.get(k)]
    if links:
        items += f"<li>{' · '.join(links)}</li>"
    error = f"<p class='down'>{e(rec['status'])}: {e(rec['error'] or '')}</p>" if rec.get("error") else ""
    quiet = " data-quiet" if rec["class"] in QUIET else ""
    return f"""<article class="doc card" id="{e(rec['name'])}"{quiet}>
<div class="doc-head"><div><h3>{CLASSES[rec['class']][0]} {e(rec['name'])}</h3>
<p class="desc">{e(description or '')}</p>
<p class="desc"><a href="../docs/{e(safe_name(rec['name']))}.html{'#to=' + sha[:10] if sha else ''}">History and comparison with earlier commits →</a></p></div>
<div class="score"><div class="num">{pct(rec['score'])}<small> / 100</small></div>
<div class="meter"><span style="width:{100 * rec['score']:.1f}%"></span></div><div>{change}</div></div></div>
{error}<ul class="parts">{''.join(f'<li>{k}<b>{pct(c.get(k))}</b></li>' for k in WEIGHTS)}</ul>
{pages_html(rec, run_dir)}
<details class="more"><summary>Details</summary><ul>{items}</ul></details>
</article>"""


def pages_html(rec, run_dir=None):
    """Each page as PDF | DOCX (| overlay). Where the page counts differ, a note says so, and the
    side without a page gets a page-sized placeholder that explains why, rather than looking like a
    missing file."""
    e = html.escape
    counts = rec.get("counts") or {}
    n_pdf, n_docx = counts.get("pages_ref"), counts.get("pages_docx")

    def figure(src, caption, cls=""):
        size = png_size(Path(run_dir) / src) if run_dir else None
        dims = f" width='{size[0]}' height='{size[1]}'" if size else ""
        return (f"<figure{cls}><a href='{e(src)}'><img loading='lazy' src='{e(src)}'{dims} alt='{e(caption)}'></a>"
                f"<figcaption>{caption}</figcaption></figure>")

    def gap(title, text, caption="", cls=""):
        return f"<figure{cls}><div class='gap'><b>{title}</b><span>{text}</span></div><figcaption>{caption}</figcaption></figure>"

    out = []
    if n_pdf and n_docx and n_pdf != n_docx:
        first, last = min(n_pdf, n_docx) + 1, max(n_pdf, n_docx)
        span = f"page {first}" if first == last else f"pages {first}–{last}"
        verb = "has" if first == last else "have"
        if n_docx > n_pdf:
            what = f"DOCX {span} {verb} no PDF page to match: content pushed past the PDF's last page. "
        else:
            what = f"PDF {span} {verb} no DOCX page to match. "
        out.append(f"<p class='note'><b>⚠ Page count</b> The PDF has {n_pdf} page{'s' * (n_pdf != 1)}; the DOCX runs to "
                   f"{n_docx} in LibreOffice. {what}Unmatched pages count 0 towards the visual score.</p>")
    pages = {p["page"]: p for p in rec.get("pages", [])}
    for shot in rec.get("images", []):
        k = shot["page"]
        pdf, docx, diff = shot.get("pdf") or shot.get("ref"), shot.get("docx"), shot.get("diff")
        p = pages.get(k, {})
        about = [f"visual {pct(p.get('visual'))}"] + ([f"words {pct(p['text'])}"] if p.get("text") is not None else [])
        if p.get("positioned"):
            about.append("positioned text")
        if pdf:
            left = figure(pdf, f"PDF · page {k}" + ("" if docx else " · unmatched, counts 0 towards visual"))
        else:
            left = gap(f"No page {k} in the PDF", f"The PDF ends on page {n_pdf}. The DOCX page beside this holds "
                                                  "content pushed past it.", f"PDF · ends on page {n_pdf}")
        if docx:
            right = figure(docx, f"DOCX in LibreOffice · page {k} · "
                                 + (", ".join(about) if pdf else "unmatched, counts 0 towards visual"))
        else:
            right = gap(f"No page {k} in the DOCX", f"The DOCX ends on page {n_docx}, so this PDF page has nothing to "
                                                    "compare with.", f"DOCX · ends on page {n_docx}")
        overlay = (figure(diff, "Overlay", " class='diff'") if diff
                   else gap("No overlay", "Only one side has this page.", "Overlay", " class='diff'"))
        out.append(f"<div class='page'>{left}{right}{overlay}</div>")
    more = max(n_pdf or 0, n_docx or 0) - len(rec.get("images", []))
    if more > 0:
        out.append(f"<p class='muted'>{more} more pages not shown (run with --image-pages)</p>")
    return "".join(out)


def png_size(path):
    """A PNG's (width, height) from its header, so the report holds each page's place before the
    image loads."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None
