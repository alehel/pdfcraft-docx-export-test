"""Baseline comparison and the run's reports (report.md, report.html)."""

import html

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
    lines = [f"- PdfCraft: `{pc.get('sha', '')[:10]}` {pc.get('subject', '')} ({pc.get('cli')})",
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
    out = [f"# DOCX export: run {meta['id']}", ""]
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


CSS = """
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --card: #f5f5f7;
  --good: #1a7f37; --ok: #9a6700; --bad: #cf222e; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #f2f2f2; --muted: #a1a1a6;
  --line: #3a3a3c; --card: #222225; --good: #3fb950; --ok: #d29922; --bad: #f85149; } }
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 1400px; padding: 24px 16px 64px; background: var(--bg); color: var(--fg);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
h1 { font-size: 22px; margin: 0 0 8px; } h2 { font-size: 17px; margin: 32px 0 8px; }
.meta { color: var(--muted); margin: 0 0 16px; padding-left: 18px; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th:nth-child(2), td:nth-child(2), td:last-child, th:last-child { text-align: left; }
th { color: var(--muted); font-weight: 600; position: sticky; top: 0; background: var(--bg); }
td a { color: inherit; }
.good { color: var(--good); } .ok { color: var(--ok); } .bad { color: var(--bad); }
details { border: 1px solid var(--line); border-radius: 8px; margin: 10px 0; background: var(--card); }
summary { cursor: pointer; padding: 10px 12px; font-weight: 600; }
.body { padding: 0 12px 12px; }
.body ul { margin: 4px 0 12px; padding-left: 18px; }
code { font-size: 12px; }
.pages { display: grid; gap: 14px; }
.page { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.page figure { margin: 0; } .page img { width: 100%; border: 1px solid var(--line); background: #fff; }
.page figcaption { color: var(--muted); font-size: 12px; }
@media (max-width: 700px) { .page { grid-template-columns: 1fr; } }
"""


def tone(x):
    if x is None:
        return ""
    return "good" if x >= 0.9 else "ok" if x >= 0.7 else "bad"


def cell(x):
    return f'<td class="{tone(x)}">{pct(x)}</td>'


def html_report(meta, records, baseline):
    e = html.escape
    counts = summary_counts(records)
    rows = []
    for rec in sorted(records, key=sort_key):
        c = rec.get("components") or {}
        n = rec.get("counts") or {}
        pages = f"{n.get('pages_ref', '?')}→{n.get('pages_docx') if n.get('pages_docx') is not None else '?'}" if n else ""
        delta = rec["delta"]
        dclass = "" if delta is None or abs(delta) < THRESHOLD else ("good" if delta > 0 else "bad")
        rows.append(
            f"<tr><td>{CLASSES[rec['class']][0]}</td><td><a href='#{e(rec['name'])}'>{e(rec['name'])}</a></td>"
            f"{cell(rec['score'])}<td class='{dclass}'>{signed(delta)}</td>"
            + "".join(cell(c.get(k)) for k in WEIGHTS)
            + f"<td>{pages}</td><td>{e(rec['status'])}</td></tr>")
    details = [detail_html(rec) for rec in sorted(records, key=sort_key)]
    head = "".join(f"<li>{inline_md(line[2:])}</li>" for line in header_lines(meta, baseline))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOCX export {e(meta['id'])}</title><style>{CSS}</style></head><body>
<h1>DOCX export: {len(records)} PDFs, mean score {pct(mean_score(records))}</h1>
<ul class="meta"><li>Run {e(meta['id'])}, {meta.get('seconds', 0):.0f} s</li>{head}
<li>{' · '.join(f"{CLASSES[c][0]} {n} {CLASSES[c][1]}" for c, n in sorted(counts.items(), key=lambda x: ORDER.index(x[0])))}</li>
<li>Scores 0-100; weights {', '.join(f'{k} {int(v * 100)} %' for k, v in WEIGHTS.items())}. Page images: original | DOCX in LibreOffice | overlay (red only in the PDF, blue only in the DOCX).</li></ul>
<div class="wrap"><table><thead><tr><th></th><th>PDF</th><th>score</th><th>Δ</th>{''.join(f'<th>{k}</th>' for k in WEIGHTS)}<th>pages</th><th>status</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<h2>Documents</h2>
{''.join(details)}
</body></html>
"""


def inline_md(text):
    """`code` spans of a markdown line as <code>."""
    parts = html.escape(text).split("`")
    return "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))


def detail_html(rec):
    e = html.escape
    lines = detail_markdown(rec)[2:]
    items = "".join(f"<li>{inline_md(line[2:])}</li>" for line in lines if line.startswith("- "))
    links = []
    for key, label in (("docx_file", "DOCX"), ("rendered_file", "LibreOffice PDF")):
        if rec.get(key):
            links.append(f"<a href='{e(rec[key])}'>{label}</a>")
    pages = {p["page"]: p for p in rec.get("pages", [])}
    shots = []
    for shot in rec.get("images", []):
        p = pages.get(shot["page"], {})
        caption = f"page {shot['page']}: visual {pct(p.get('visual'))}"
        if p.get("text") is not None:
            caption += f", words {pct(p['text'])}"
        if p.get("positioned"):
            caption += ", positioned"
        figs = "".join(
            f"<figure><img loading='lazy' src='{e(shot[k])}' alt='{label}'><figcaption>{label}</figcaption></figure>"
            if shot.get(k) else "<figure></figure>"
            for k, label in (("ref", f"PDF · {caption}"), ("docx", "DOCX"), ("diff", "overlay")))
        shots.append(f"<div class='page'>{figs}</div>")
    more = len(rec.get("pages", [])) - len(rec.get("images", []))
    if more > 0:
        shots.append(f"<p class='meta'>{more} more pages not shown (--image-pages)</p>")
    open_ = " open" if rec["class"] in ("regression", "failed", "improvement", "fixed") else ""
    return (f"<details id='{e(rec['name'])}'{open_}><summary>{CLASSES[rec['class']][0]} {e(rec['name'])} — "
            f"{pct(rec['score'])}{' (' + signed(rec['delta']) + ')' if rec['delta'] is not None else ''}</summary>"
            f"<div class='body'><ul>{items}{'<li>' + ' · '.join(links) + '</li>' if links else ''}</ul>"
            f"<div class='pages'>{''.join(shots)}</div></div></details>")
