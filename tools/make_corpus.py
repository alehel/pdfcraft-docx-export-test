#!/usr/bin/env python3
"""Make the synthetic corpus in corpus/ (only files that are missing, unless --force).

  python3 -I tools/make_corpus.py                 make what is missing
  python3 -I tools/make_corpus.py --force NAME    remake one (e.g. chrome/invoice), or --force all

Sources: Chrome headless (HTML below, real fonts, CSS layout), LibreOffice Writer (HTML import),
hand-built PDFs (base-14 fonts, /Rotate, positioned text, an image-only "scan") and a few files
copied from the MCP regression suite's fixtures. corpus/corpus.json describes every file; PDFs
added by hand (e.g. corpus/real/*.pdf) need no entry."""

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

SUITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUITE))

from harness.render import find_soffice, rasterize, read_pgm, write_png  # noqa: E402

CORPUS = SUITE / "corpus"
# The container sets CHROME to its pinned Chromium.
CHROME = (os.environ.get("CHROME") or shutil.which("chromium")
          or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MCP_FIXTURES = Path.home() / "dev" / "craft" / "pdfcraft-mcp-suite" / "fixtures"

# -- text ------------------------------------------------------------------------------------------

PROSE = [
    "The lighthouse at Skarvik was lit for the first time on a wet October evening in 1871, and for "
    "almost a century afterwards the same family kept its lamp burning. Their log books survive in the "
    "town archive: eleven volumes of weather, ships, repairs and the occasional complaint about the "
    "price of paraffin.",
    "Most entries are short. Wind from the south-west, fresh; visibility good; two steamers northbound "
    "before midnight. But every few pages the keeper allowed himself a sentence more, and those sentences "
    "are the reason people still read the logs today.",
    "In the winter of 1893 the ice reached the outer skerries, something no one in the village had seen "
    "before. The keeper walked across to the nearest island with his son, measured the ice with an auger "
    "and wrote that it was thicker than the kitchen table and twice as level.",
    "The station was modernised in stages. A clockwork rotation mechanism replaced the hand-cranked one in "
    "1902, an acetylene burner arrived in 1923, and electricity followed in 1951, carried across the sound "
    "on a cable that the fishermen swore would never hold.",
    "Daily life followed the light. The lamp was trimmed at dusk and dawn, the lens polished every morning, "
    "and the brass fittings once a week whether they needed it or not. Supplies came by boat every second "
    "Thursday, weather permitting, which in February it rarely did.",
    "Visitors were rare and were recorded with care: a priest in 1880, a painter who stayed for three weeks "
    "in 1911 and left a small oil sketch of the tower, and a group of surveyors whose instruments, the log "
    "notes drily, were less reliable than the keeper's barometer.",
    "When the light was automated in 1968 the last keeper moved to a house in the village from which, on "
    "clear nights, he could see the beam sweep past his window every twelve seconds. He kept writing the "
    "log for another two years, out of habit.",
    "Today the tower belongs to a foundation that opens it to the public in summer. The log books have been "
    "scanned, transcribed and indexed, and a small exhibition in the old oil store shows the lamp, the "
    "auger and a page from the winter of 1893.",
    "The transcription project took four years and relied almost entirely on volunteers. Handwriting "
    "changed from keeper to keeper, abbreviations drifted, and the ink of the earliest volumes had faded "
    "to the colour of weak tea, so every page was read twice by different people.",
    "What emerges from the logs is not a story of heroism but of attention: a century of people who looked "
    "at the same stretch of sea every day and wrote down what they saw, so that others could find their "
    "way home in the dark.",
]

TABLE_ROWS = [
    ("North pier", "Concrete", 1954, 212, 18.4, "Good"),
    ("South pier", "Timber", 1931, 148, 9.7, "Fair"),
    ("Ferry berth", "Steel", 1988, 96, 31.2, "Good"),
    ("Fish market quay", "Concrete", 1967, 175, 22.9, "Poor"),
    ("Boatyard slip", "Concrete", 1972, 64, 4.1, "Fair"),
    ("Marina pontoon A", "Composite", 2009, 120, 12.6, "Good"),
    ("Marina pontoon B", "Composite", 2011, 120, 12.6, "Good"),
    ("Old customs steps", "Granite", 1889, 22, 0.8, "Fair"),
    ("Fuel jetty", "Steel", 1995, 48, 6.5, "Good"),
    ("Lifeboat ramp", "Concrete", 1979, 38, 3.3, "Poor"),
    ("East breakwater", "Rock armour", 1926, 410, 54.0, "Fair"),
    ("West breakwater", "Rock armour", 1926, 355, 47.8, "Good"),
]


def page(css, body, lang="en", title="Corpus document"):
    return (f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><title>{title}</title>"
            f"<style>{css}</style></head><body>{body}</body></html>")


def paras(*indexes):
    return "".join(f"<p>{PROSE[i % len(PROSE)]}</p>" for i in indexes)


def table_html(rows=TABLE_ROWS, cls=""):
    head = "<tr><th>Structure</th><th>Material</th><th>Built</th><th>Length (m)</th><th>Cost (MNOK)</th><th>Condition</th></tr>"
    body = "".join(f"<tr><td>{a}</td><td>{b}</td><td class='n'>{c}</td><td class='n'>{d}</td><td class='n'>{e:.1f}</td><td>{f}</td></tr>"
                   for a, b, c, d, e, f in rows)
    total = (f"<tr class='total'><td>Total</td><td></td><td></td><td class='n'>{sum(r[3] for r in rows)}</td>"
             f"<td class='n'>{sum(r[4] for r in rows):.1f}</td><td></td></tr>")
    return f"<table class='{cls}'><thead>{head}</thead><tbody>{body}{total}</tbody></table>"


BASE = "body { margin: 0; } p { margin: 0 0 0.7em; }"

# -- Chrome documents -------------------------------------------------------------------------------

CHROME_DOCS = {}


def chrome(name, description, tags):
    def register(fn):
        CHROME_DOCS[name] = (description, tags, fn)
        return fn
    return register


@chrome("prose-letter", "Plain single-column prose in Georgia, US Letter, one heading.", ["text"])
def _():
    css = BASE + "@page { size: letter; margin: 1in; } body { font: 11pt/1.5 Georgia, serif; } h1 { font-size: 20pt; margin: 0 0 14pt; }"
    return page(css, "<h1>The Keeper's Log</h1>" + paras(*range(8)))


@chrome("headings-emphasis", "Three heading levels, bold, italic, bold italic, underline, strike, colours, sizes, super/subscript, a block quote.", ["format"])
def _():
    css = BASE + """@page { size: A4; margin: 20mm; } body { font: 10.5pt/1.45 'Helvetica Neue', Helvetica, sans-serif; }
h1 { font-size: 24pt; margin: 0 0 8pt; } h2 { font-size: 16pt; margin: 16pt 0 6pt; } h3 { font-size: 12.5pt; margin: 12pt 0 4pt; }
blockquote { margin: 10pt 24pt; font-style: italic; color: #444; } .red { color: #c0392b; } .blue { color: #1f5fbf; } .green { color: #1e7b34; }"""
    body = f"""<h1>Annual Field Notes</h1>
<p>This report summarises the <b>observations of the 2026 season</b>. Some terms are <i>emphasised</i>, a few are
<b><i>both bold and italic</i></b>, and one is <u>underlined for no good reason</u>. A <s>struck-out</s> figure was corrected.</p>
<h2>Weather</h2>
<p>The season opened <span class="blue">cold and clear</span>, turned <span class="red">unusually warm</span> in July and ended in
<span class="green">steady rain</span>. Peak water temperature reached 19.4 °C, about 2.1 °C above the long-term mean.</p>
<h3>Measurements</h3>
<p>Readings used the formula T<sub>adj</sub> = T<sub>raw</sub> − 0.12·h, where h is the depth in metres; salinity is in g/kg
(10<sup>−3</sup>). <span style="font-size: 14pt">Larger text</span> and <span style="font-size: 8pt">smaller text</span> appear in the same paragraph.</p>
<blockquote>“The sea was the colour of slate and just as still,” wrote the observer on 3 September.</blockquote>
<h2>Birds</h2>{paras(2, 5)}
<h3>Notable sightings</h3><p><b>White-tailed eagle:</b> two adults, regularly. <b>Puffin:</b> a single bird on 14 June. <i>Arctic tern</i> colonies returned to the outer skerries.</p>
<h2>Next steps</h2>{paras(7)}"""
    return page(css, body)


@chrome("lists", "Bulleted, numbered, nested (three levels), lettered and roman lists, and a checklist.", ["lists"])
def _():
    css = BASE + "@page { size: A4; margin: 22mm; } body { font: 11pt/1.4 Arial, sans-serif; } h2 { font-size: 15pt; margin: 14pt 0 6pt; } li { margin: 2pt 0; }"
    body = """<h2>Packing list</h2><ul><li>Waterproof jacket and trousers</li><li>Thermal layers<ul style="list-style: circle">
<li>Merino base layer</li><li>Fleece mid layer<ul style="list-style: square"><li>Spare gloves</li><li>Hat</li></ul></li></ul></li><li>Head torch and spare batteries</li></ul>
<h2>Procedure</h2><ol><li>Check the forecast and tide tables.</li><li>Notify the harbour office of the route.<ol style="list-style: lower-alpha">
<li>Expected departure time</li><li>Expected return time</li><li>Number of people on board</li></ol></li><li>Inspect the boat, engine and safety equipment.</li>
<li>Log the departure.</li></ol><h2>Appendices</h2><ol style="list-style: upper-roman"><li>Radio channels</li><li>Emergency contacts</li><li>Chart references</li></ol>
<h2>Before leaving</h2><p>☐ Fuel checked<br>☑ Life jackets on board<br>☐ VHF radio tested<br>☑ First-aid kit stocked</p>"""
    return page(css, body)


@chrome("table-bordered", "A ruled data table with a shaded header row, zebra rows, right-aligned numbers and a total row.", ["tables"])
def _():
    css = BASE + """@page { size: A4; margin: 18mm; } body { font: 10pt/1.35 Arial, sans-serif; } h1 { font-size: 18pt; margin: 0 0 10pt; }
table { border-collapse: collapse; width: 100%; } th, td { border: 0.75pt solid #777; padding: 4pt 6pt; text-align: left; }
th { background: #1f3b5a; color: #fff; } tbody tr:nth-child(even) { background: #eef2f6; } .n { text-align: right; } .total td { font-weight: bold; border-top: 1.5pt solid #000; }"""
    return page(css, f"<h1>Harbour structures</h1><p>{PROSE[3]}</p>{table_html()}<p style='margin-top:10pt'>{PROSE[4]}</p>")


@chrome("table-spans", "A table with column and row spans, a two-level header and cells whose text wraps.", ["tables"])
def _():
    css = BASE + """@page { size: A4; margin: 18mm; } body { font: 10pt/1.35 'Helvetica Neue', sans-serif; } h1 { font-size: 18pt; }
table { border-collapse: collapse; width: 100%; } th, td { border: 0.75pt solid #555; padding: 4pt 6pt; vertical-align: top; } th { background: #e8e8e8; }"""
    body = """<h1>Maintenance plan</h1><table>
<tr><th rowspan="2">Structure</th><th colspan="2">2026</th><th colspan="2">2027</th><th rowspan="2">Notes</th></tr>
<tr><th>Spring</th><th>Autumn</th><th>Spring</th><th>Autumn</th></tr>
<tr><td>North pier</td><td colspan="2">Resurface deck and replace bollards along the full length</td><td>Inspect</td><td>—</td><td>Coordinate with the ferry timetable.</td></tr>
<tr><td rowspan="2">Breakwaters</td><td>Survey</td><td>Survey</td><td colspan="2">Rock armour top-up on the seaward side</td><td>Depends on winter storm damage.</td></tr>
<tr><td colspan="4">Divers to check the toe of the east breakwater after every storm over force 9</td><td>Report within a week.</td></tr>
<tr><td>Fish market quay</td><td>Repair</td><td>Repair</td><td>Repair</td><td>Repair</td><td>Phased so that two berths stay open at all times while the concrete cures.</td></tr>
</table>""" + paras(5)
    return page(css, body)


@chrome("table-borderless", "A financial statement: no rules, indented labels, right-aligned figures, bold subtotals.", ["tables"])
def _():
    css = BASE + """@page { size: letter; margin: 0.9in; } body { font: 10.5pt/1.5 'Times New Roman', serif; } h1 { font-size: 17pt; margin: 0 0 4pt; }
table { width: 100%; border-collapse: collapse; } td { padding: 1.5pt 4pt; } td.n { text-align: right; width: 18%; } .ind { padding-left: 18pt; } .sub td { font-weight: bold; } th { text-align: right; font-weight: bold; }"""
    rows = [("Revenue", None), ("Harbour dues", (412, 398, 371)), ("Berth rental", (233, 219, 204)), ("Fuel sales", (518, 561, 490)),
            ("Total revenue", (1163, 1178, 1065)), ("Expenses", None), ("Salaries", (402, 389, 371)), ("Maintenance", (215, 260, 188)),
            ("Insurance", (61, 58, 55)), ("Fuel purchases", (447, 489, 425)), ("Total expenses", (1125, 1196, 1039)), ("Result", (38, -18, 26))]
    body = "<h1>Statement of income</h1><p>Figures in thousands of NOK.</p><table><tr><td></td><th>2026</th><th>2025</th><th>2024</th></tr>"
    for label, values in rows:
        if values is None:
            body += f"<tr><td colspan='4' style='padding-top:8pt'><i>{label}</i></td></tr>"
        else:
            sub = label.startswith(("Total", "Result"))
            cells = "".join(f"<td class='n'>{v:,}</td>".replace("-", "−") for v in values)
            body += f"<tr class='{'sub' if sub else ''}'><td class='{'' if sub else 'ind'}'>{label}</td>{cells}</tr>"
    return page(css, body + "</table>" + paras(9))


@chrome("two-column-article", "A two-column article with a spanning title, justified hyphenated text and a drop cap.", ["columns", "layout"])
def _():
    css = BASE + """@page { size: A4; margin: 16mm; } body { font: 10pt/1.42 Georgia, serif; } .cols { column-count: 2; column-gap: 8mm; text-align: justify; hyphens: auto; }
h1 { font-size: 26pt; margin: 0 0 4pt; } .sub { font-style: italic; color: #555; margin-bottom: 12pt; }
.cols p:first-child::first-letter { float: left; font-size: 38pt; line-height: 0.9; padding: 2pt 4pt 0 0; font-weight: bold; }"""
    return page(css, "<h1>A Century of Light</h1><div class='sub'>How one family kept a lighthouse burning</div><div class='cols'>" + paras(*range(10)) + "</div>")


@chrome("three-column-newsletter", "A newsletter: coloured masthead, three columns, an image inside a column, a pull quote.", ["columns", "images", "layout"])
def _():
    css = BASE + """@page { size: letter; margin: 0.5in; } body { font: 9.5pt/1.4 'Helvetica Neue', sans-serif; }
.mast { background: #0b4f6c; color: #fff; padding: 10pt 14pt; margin-bottom: 10pt; } .mast h1 { margin: 0; font-size: 28pt; } .mast div { font-size: 10pt; }
.cols { column-count: 3; column-gap: 0.25in; } h2 { font-size: 12pt; margin: 0 0 4pt; color: #0b4f6c; } img { width: 100%; }
.quote { font-size: 13pt; font-style: italic; border-left: 3pt solid #0b4f6c; padding-left: 6pt; margin: 6pt 0; }"""
    body = (f"<div class='mast'><h1>Skarvik Harbour News</h1><div>Issue 42 · Autumn 2026</div></div><div class='cols'>"
            f"<h2>Ice on the skerries</h2>{paras(2, 3)}<img src='photo.jpg'><h2>Volunteers wanted</h2>{paras(8)}"
            f"<div class='quote'>“A century of people who looked at the same stretch of sea.”</div>{paras(9, 4)}<h2>Opening hours</h2>{paras(7, 5)}</div>")
    return page(css, body)


@chrome("images-figures", "A chart, a JPEG photo floated right with text wrapping round it, and a logo with transparency.", ["images"])
def _():
    css = BASE + """@page { size: A4; margin: 20mm; } body { font: 10.5pt/1.45 Arial, sans-serif; } h1 { font-size: 20pt; margin: 6pt 0 10pt; }
.logo { width: 22mm; } .right { float: right; width: 70mm; margin: 0 0 6pt 10pt; } figure { margin: 10pt 0; } figcaption { font-size: 9pt; color: #555; }
.chart { width: 100%; }"""
    body = (f"<img class='logo' src='logo.png'><h1>Visitor numbers</h1><img class='right' src='photo.jpg'>{paras(7, 0)}"
            f"<figure><img class='chart' src='chart.png'><figcaption>Figure 1. Visitors per month, 2026 season.</figcaption></figure>{paras(9)}")
    return page(css, body)


@chrome("multipage-report", "An eight-page report: title page, contents, sections on new pages, running header and page numbers.", ["multipage", "layout"])
def _():
    css = BASE + """@page { size: A4; margin: 25mm 22mm; @top-right { content: "Harbour Survey 2026"; font: 8pt Arial; color: #777; }
@bottom-center { content: "Page " counter(page) " of " counter(pages); font: 8pt Arial; } }
@page:first { @top-right { content: none; } @bottom-center { content: none; } }
body { font: 10.5pt/1.5 'Times New Roman', serif; } h1 { font-size: 30pt; margin: 60mm 0 6pt; } h2 { font-size: 16pt; break-before: page; margin: 0 0 8pt; }
.title-sub { font-size: 14pt; color: #444; } .toc p { margin: 2pt 0; }"""
    sections = ["Background", "Method", "Structures", "Findings", "Recommendations"]
    body = "<h1>Harbour Survey 2026</h1><div class='title-sub'>Condition of the piers, quays and breakwaters</div><p style='margin-top:40mm'>Skarvik Harbour Board<br>October 2026</p>"
    body += "<h2>Contents</h2><div class='toc'>" + "".join(f"<p>{i + 1}. {s}</p>" for i, s in enumerate(sections)) + "</div>"
    for i, s in enumerate(sections):
        body += f"<h2>{i + 1}. {s}</h2>" + paras(i, i + 3, i + 6, i + 1)
        if s == "Structures":
            body += table_html(TABLE_ROWS[:8]).replace("<table", "<table border='1' cellpadding='3' style='border-collapse:collapse;font-size:9pt;width:100%'")
    return page(css, body)


@chrome("invoice", "An invoice: logo, address blocks, item table, totals, and a rotated PAID stamp over the totals.", ["business", "tables", "rotation"])
def _():
    css = BASE + """@page { size: A4; margin: 15mm; } body { font: 9.5pt/1.4 'Helvetica Neue', sans-serif; position: relative; }
.top { display: flex; justify-content: space-between; align-items: flex-start; } .logo { width: 20mm; } .title { font-size: 26pt; font-weight: bold; color: #0b4f6c; text-align: right; }
.addr { display: flex; justify-content: space-between; margin: 12mm 0 8mm; } .addr div { width: 45%; } table { width: 100%; border-collapse: collapse; }
th { text-align: left; border-bottom: 1.5pt solid #0b4f6c; padding: 4pt; } td { padding: 4pt; border-bottom: 0.5pt solid #ccc; } .n { text-align: right; }
.totals { width: 45%; margin-left: auto; margin-top: 6mm; } .totals td { border: none; } .grand td { font-weight: bold; font-size: 12pt; border-top: 1pt solid #000; }
.stamp { position: absolute; top: 150mm; left: 40mm; transform: rotate(-15deg); font-size: 40pt; font-weight: bold; color: rgba(200,30,30,0.75); border: 4pt solid rgba(200,30,30,0.75); padding: 2mm 6mm; }
.foot { margin-top: 20mm; font-size: 8.5pt; color: #555; }"""
    items = [("Berth rental, pontoon A, 14 nights", 14, 320.0), ("Shore power (kWh)", 212, 2.4), ("Fresh water (m³)", 6, 45.0),
             ("Harbour dues", 1, 850.0), ("Waste handling", 1, 150.0), ("Crane lift, 4 t", 2, 1200.0)]
    rows = "".join(f"<tr><td>{d}</td><td class='n'>{q}</td><td class='n'>{p:,.2f}</td><td class='n'>{q * p:,.2f}</td></tr>" for d, q, p in items)
    sub = sum(q * p for _, q, p in items)
    body = f"""<div class='top'><img class='logo' src='logo.png'><div class='title'>INVOICE<div style='font-size:10pt;color:#333;font-weight:normal'>No. 2026-0417 · 30 October 2026</div></div></div>
<div class='addr'><div><b>Skarvik Harbour Board</b><br>Kaigata 1<br>8390 Skarvik<br>Norway<br>Org. no. 912 345 678</div>
<div><b>Bill to</b><br>M/S Havørn AS<br>Postboks 77<br>9008 Tromsø<br>Norway</div></div>
<table><tr><th>Description</th><th class='n'>Qty</th><th class='n'>Unit price</th><th class='n'>Amount</th></tr>{rows}</table>
<table class='totals'><tr><td>Subtotal</td><td class='n'>{sub:,.2f}</td></tr><tr><td>VAT 25 %</td><td class='n'>{sub * 0.25:,.2f}</td></tr>
<tr class='grand'><td>Total NOK</td><td class='n'>{sub * 1.25:,.2f}</td></tr></table>
<div class='stamp'>PAID</div>
<div class='foot'>Payment terms: 14 days. Account 1503.12.34567. Please quote the invoice number with your payment.</div>"""
    return page(css, body)


@chrome("slide-deck", "Three 16:9 slides: a full-bleed coloured title slide, a bullet slide, text over a full-page photo.", ["slides", "images", "layout"])
def _():
    css = BASE + """@page { size: 10in 5.625in; margin: 0; } body { font-family: 'Helvetica Neue', sans-serif; }
.slide { width: 10in; height: 5.625in; position: relative; overflow: hidden; break-after: page; box-sizing: border-box; padding: 0.6in 0.8in; }
.s1 { background: #0b4f6c; color: #fff; } .s1 h1 { font-size: 44pt; margin: 1.1in 0 0.15in; } .s1 p { font-size: 18pt; color: #cfe3ec; }
.s2 h2 { font-size: 30pt; margin: 0 0 0.3in; color: #0b4f6c; } .s2 li { font-size: 18pt; margin: 0 0 10pt; }
.s3 { padding: 0; } .s3 img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.s3 .cap { position: absolute; left: 0.6in; bottom: 0.6in; color: #fff; font-size: 30pt; font-weight: bold; text-shadow: 0 0 6px #000; }"""
    body = """<div class='slide s1'><h1>Skarvik Lighthouse</h1><p>150 years of light · Foundation board meeting</p></div>
<div class='slide s2'><h2>This year</h2><ul><li>11,240 visitors, up 14 %</li><li>Log transcription finished</li><li>New roof on the oil store</li><li>Two new volunteer guides</li></ul></div>
<div class='slide s3'><img src='photo.jpg'><div class='cap'>Next: the 2027 season</div></div>"""
    return page(css, body)


@chrome("watermark-draft", "A memo with a large diagonal DRAFT watermark behind the text.", ["rotation", "layout"])
def _():
    css = BASE + """@page { size: A4; margin: 25mm; } body { font: 11pt/1.5 Arial, sans-serif; }
.wm { position: absolute; top: 70mm; left: -5mm; transform: rotate(-35deg); font-size: 110pt; font-weight: bold; color: #dddddd; z-index: -1; }
h1 { font-size: 18pt; } .hdr td { padding: 1pt 10pt 1pt 0; }"""
    body = (f"<div class='wm'>DRAFT</div><h1>Memo</h1><table class='hdr'><tr><td><b>To</b></td><td>Foundation board</td></tr>"
            f"<tr><td><b>From</b></td><td>Exhibition committee</td></tr><tr><td><b>Date</b></td><td>30 October 2026</td></tr>"
            f"<tr><td><b>Subject</b></td><td>Winter exhibition</td></tr></table><hr>{paras(7, 8, 5, 9)}")
    return page(css, body)


@chrome("rotated-labels", "A table with column headers rotated 90 degrees and a vertical margin label.", ["rotation", "tables"])
def _():
    css = BASE + """@page { size: A4; margin: 20mm; } body { font: 10pt/1.4 Arial, sans-serif; } h1 { font-size: 18pt; }
table { border-collapse: collapse; } td, th { border: 0.75pt solid #666; padding: 3pt 6pt; } th.r { height: 32mm; width: 9mm; vertical-align: bottom; }
th.r div { transform: rotate(-90deg); transform-origin: left bottom; white-space: nowrap; width: 9mm; position: relative; left: 7mm; }
.side { position: absolute; left: 4mm; top: 120mm; writing-mode: vertical-rl; transform: rotate(180deg); font-size: 9pt; color: #777; letter-spacing: 1pt; }"""
    months = ["January", "February", "March", "April", "May", "June"]
    head = "<tr><th>Volunteer</th>" + "".join(f"<th class='r'><div>{m}</div></th>" for m in months) + "</tr>"
    people = ["Ingrid", "Ola", "Fatima", "Jonas", "Mei", "Arvid"]
    body = "".join(f"<tr><td>{p}</td>" + "".join(f"<td>{'✓' if (i + j) % 3 else ''}</td>" for j in range(6)) + "</tr>" for i, p in enumerate(people))
    return page(css, f"<div class='side'>Internal · not for publication</div><h1>Guide rota</h1><table>{head}{body}</table>" + paras(8))


@chrome("international", "Paragraphs in a dozen scripts, including right-to-left Arabic and Hebrew, CJK, Devanagari and Thai.", ["scripts", "rtl"])
def _():
    css = BASE + "@page { size: A4; margin: 20mm; } body { font: 11pt/1.6 'Helvetica Neue', 'Noto Sans', sans-serif; } h3 { font-size: 11pt; margin: 10pt 0 2pt; color: #555; } [dir=rtl] { text-align: right; }"
    samples = [
        ("Türkçe", "İstanbul'da ılık bir sonbahar günü; Şişli'de çay içip Boğaz'ı seyrettik.", "tr", "ltr"),
        ("Deutsch", "Größere Straßen führen zum Hafen; die Brücke über die Förde ist schön.", "de", "ltr"),
        ("Polski", "Zażółć gęślą jaźń — latarnia morska świeci co dwanaście sekund.", "pl", "ltr"),
        ("Ελληνικά", "Ο φάρος ανάβει κάθε βράδυ και οι ψαράδες βρίσκουν τον δρόμο τους.", "el", "ltr"),
        ("Русский", "Маяк зажигается каждый вечер, и рыбаки находят дорогу домой.", "ru", "ltr"),
        ("العربية", "تضيء المنارة كل مساء ويجد الصيادون طريقهم إلى البيت.", "ar", "rtl"),
        ("עברית", "המגדלור נדלק בכל ערב והדייגים מוצאים את דרכם הביתה.", "he", "rtl"),
        ("हिन्दी", "प्रकाशस्तंभ हर शाम जलता है और मछुआरे घर का रास्ता पाते हैं।", "hi", "ltr"),
        ("中文", "灯塔每晚都会点亮，渔民们因此找到回家的路。", "zh", "ltr"),
        ("日本語", "灯台は毎晩点灯し、漁師たちは家路を見つけます。", "ja", "ltr"),
        ("한국어", "등대는 매일 밤 불을 밝히고 어부들은 집으로 가는 길을 찾습니다.", "ko", "ltr"),
        ("ไทย", "ประภาคารเปิดไฟทุกคืนและชาวประมงก็หาทางกลับบ้านได้", "th", "ltr"),
    ]
    body = "<h1 style='font-size:18pt'>One sentence, many scripts</h1>" + "".join(
        f"<h3>{label}</h3><p lang='{lang}' dir='{d}'>{text}</p>" for label, text, lang, d in samples)
    return page(css, body)


@chrome("code-listing", "Monospaced code blocks with indentation, inline code, and prose around them.", ["monospace"])
def _():
    css = BASE + """@page { size: letter; margin: 0.8in; } body { font: 10.5pt/1.5 Georgia, serif; } h2 { font: bold 14pt 'Helvetica Neue', sans-serif; }
pre { font: 8.5pt/1.35 Menlo, 'Courier New', monospace; background: #f4f4f4; border: 0.5pt solid #ccc; padding: 6pt 8pt; white-space: pre; }
code { font: 9pt Menlo, monospace; background: #f0f0f0; }"""
    py = """def beam_interval(rotation_seconds, flashes):
    \"\"\"Seconds between flashes as seen from the sea.\"\"\"
    if flashes <= 0:
        raise ValueError("a light needs at least one flash")
    return rotation_seconds / flashes


for name, (period, n) in LIGHTS.items():
    print(f"{name:<12} {beam_interval(period, n):5.1f} s")"""
    js = """{
  "station": "Skarvik",
  "position": {"lat": 68.1234, "lon": 14.5678},
  "character": "Fl W 12s",
  "range_nm": 17
}"""
    sh = "$ curl -s https://example.org/lights.json | jq '.[] | select(.range_nm > 15) | .station'"
    py, js, sh = (html.escape(code) for code in (py, js, sh))
    body = (f"<h2>Computing the light character</h2><p>The function <code>beam_interval</code> divides the rotation period by the "
            f"number of flashes:</p><pre>{py}</pre><p>Each station is described by a small JSON record:</p><pre>{js}</pre>"
            f"<p>and the list can be filtered on the command line:</p><pre>{sh}</pre>{paras(3)}")
    return page(css, body)


@chrome("academic-paper", "A two-column paper: full-width title and abstract, numbered sections, citations, a formula, a figure, footnotes, references.", ["columns", "format", "images"])
def _():
    css = BASE + """@page { size: letter; margin: 0.75in; } body { font: 9.5pt/1.38 'Times New Roman', serif; } h1 { font-size: 17pt; text-align: center; margin: 0 0 4pt; }
.auth { text-align: center; margin-bottom: 8pt; } .abs { margin: 0 0.4in 10pt; text-align: justify; } .cols { column-count: 2; column-gap: 0.3in; text-align: justify; }
h2 { font-size: 11pt; margin: 8pt 0 4pt; } .eq { text-align: center; margin: 6pt 0; font-style: italic; } .fig img { width: 100%; } .fig div { font-size: 8pt; }
.refs p { font-size: 8pt; padding-left: 12pt; text-indent: -12pt; margin: 0 0 3pt; } .fn { font-size: 7.5pt; border-top: 0.5pt solid #000; margin-top: 8pt; padding-top: 3pt; }"""
    body = f"""<h1>Attention at Sea: A Century of Lighthouse Logs</h1><div class='auth'>I. Nordvik<sup>1</sup>, A. Berg<sup>2</sup><br><i><sup>1</sup>Skarvik Foundation, <sup>2</sup>University of Tromsø</i></div>
<div class='abs'><b>Abstract.</b> {PROSE[9]} We transcribe 11 volumes (1871–1970) and show that weather entries agree with instrumental records to within 0.3 hPa on average [1].</div>
<div class='cols'><h2>1 Introduction</h2>{paras(0, 1)}<h2>2 Data</h2><p>{PROSE[8]}<sup>a</sup></p>
<div class='eq'>p̂<sub>t</sub> = p<sub>t</sub> + β·(T<sub>t</sub> − 15) + ε<sub>t</sub>, &nbsp; ε<sub>t</sub> ~ N(0, σ²)</div>
<p>{PROSE[3]} [2, 3]</p><div class='fig'><img src='chart.png'><div>Figure 1: Entries per month.</div></div><h2>3 Results</h2>{paras(2, 5)}
<h2>4 Conclusion</h2>{paras(9)}<div class='refs'><h2>References</h2><p>[1] Hansen, K. (1999). Barometers of the north. <i>Polar Record</i> 35, 1–12.</p>
<p>[2] Larsen, E. and Moe, P. (2012). Transcribing handwritten logs. <i>Journal of Archival Methods</i> 8(2), 44–61.</p>
<p>[3] Nordvik, I. (2020). <i>The Keepers of Skarvik</i>. Bodø: Kystforlaget.</p></div>
<div class='fn'><sup>a</sup> Volunteers worked in pairs; disagreements were settled by a third reader.</div></div>"""
    return page(css, body)


@chrome("form-fields", "A printed application form: labels with fill-in lines, tick boxes, date boxes and a signature line.", ["forms", "layout"])
def _():
    css = BASE + """@page { size: A4; margin: 20mm; } body { font: 10.5pt/1.4 Arial, sans-serif; } h1 { font-size: 18pt; margin: 0 0 4pt; }
.row { display: flex; gap: 8mm; margin: 7mm 0 0; } .f { flex: 1; } .line { border-bottom: 0.75pt solid #000; height: 6mm; } .lbl { font-size: 8.5pt; color: #333; }
.box { display: inline-block; width: 4mm; height: 4mm; border: 0.75pt solid #000; vertical-align: -0.5mm; margin-right: 2mm; } .opt { margin-right: 8mm; }
.date span { display: inline-block; width: 6mm; height: 7mm; border: 0.75pt solid #000; margin-right: 1mm; }"""
    def field(label):
        return f"<div class='f'><div class='line'></div><div class='lbl'>{label}</div></div>"
    body = (f"<h1>Volunteer application</h1><p>Please write in block capitals.</p><div class='row'>{field('First name')}{field('Surname')}</div>"
            f"<div class='row'>{field('Address')}</div><div class='row'>{field('Postcode')}{field('Town')}{field('Telephone')}</div>"
            "<p style='margin-top:8mm'><b>I would like to help with</b></p><p><span class='opt'><span class='box'></span>Guided tours</span>"
            "<span class='opt'><span class='box'></span>Transcription</span><span class='opt'><span class='box'></span>Maintenance</span>"
            "<span class='opt'><span class='box'></span>Café</span></p><p style='margin-top:6mm'><b>Date available from</b></p>"
            "<div class='date'><span></span><span></span> / <span></span><span></span> / <span></span><span></span><span></span><span></span></div>"
            f"<p style='margin-top:8mm'>{PROSE[5]}</p><div class='row' style='margin-top:16mm'>{field('Signature')}{field('Date')}</div>")
    return page(css, body)


@chrome("small-print", "Dense 7 pt justified terms and conditions with numbered clauses over two pages.", ["text", "format"])
def _():
    css = BASE + "@page { size: A4; margin: 14mm; } body { font: 7pt/1.3 Arial, sans-serif; text-align: justify; } h1 { font-size: 11pt; } h2 { font-size: 8pt; margin: 5pt 0 2pt; } p { margin: 0 0 3pt; }"
    body = "<h1>Terms and conditions for berth rental</h1>"
    for i in range(1, 15):
        body += f"<h2>{i}. Clause {i}</h2>" + "".join(f"<p>{i}.{j} {PROSE[(i * 3 + j) % len(PROSE)]}</p>" for j in range(1, 4))
    return page(css, body)


@chrome("mixed-page-sizes", "Pages of different sizes in one file: A4 portrait, A4 landscape, A5.", ["page-size"])
def _():
    css = BASE + """@page { size: A4; margin: 20mm; } @page wide { size: A4 landscape; margin: 15mm; } @page small { size: A5; margin: 12mm; }
body { font: 10.5pt/1.45 Arial, sans-serif; } .wide { page: wide; } .small { page: small; } h1 { font-size: 16pt; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; } td, th { border: 0.5pt solid #888; padding: 3pt; } .n { text-align: right; }"""
    body = (f"<div><h1>Portrait A4</h1>{paras(0, 1)}</div><div class='wide'><h1>Landscape A4</h1>{table_html()}</div>"
            f"<div class='small'><h1>A5</h1>{paras(2)}</div>")
    return page(css, body)


@chrome("highlights-callouts", "Highlighted text, coloured callout boxes, white text on a dark box, a bordered quote.", ["format", "layout"])
def _():
    css = BASE + """@page { size: letter; margin: 0.9in; } body { font: 10.5pt/1.5 'Helvetica Neue', sans-serif; } mark { background: #fff1a8; }
.note { background: #e7f3ff; border-left: 4pt solid #1f6feb; padding: 6pt 10pt; margin: 10pt 0; } .warn { background: #fff4e5; border: 1pt solid #d97706; padding: 6pt 10pt; margin: 10pt 0; }
.dark { background: #1f2937; color: #f9fafb; padding: 10pt 12pt; margin: 10pt 0; } .quote { border-left: 3pt solid #999; padding-left: 10pt; color: #444; }"""
    body = (f"<h1 style='font-size:19pt'>Visiting the tower</h1><p>{PROSE[7].replace('open', '<mark>open</mark>', 1)} <mark>Tickets are sold at the oil store.</mark></p>"
            f"<div class='note'><b>Note:</b> The stairs are steep and narrow; there are 132 steps to the lamp room.</div>{paras(4)}"
            f"<div class='warn'><b>Warning:</b> The gallery is closed in winds above 15 m/s.</div><div class='dark'><b>Night tours</b><br>{PROSE[6]}</div>"
            f"<p class='quote'>{PROSE[1]}</p>")
    return page(css, body)


@chrome("letter-a4", "A business letter: sender block, date, recipient, bold subject line, signature.", ["business", "text"])
def _():
    css = BASE + """@page { size: A4; margin: 25mm 25mm 20mm; } body { font: 11pt/1.45 'Times New Roman', serif; } .from { text-align: right; margin-bottom: 16mm; }
.to { margin-bottom: 12mm; } .date { text-align: right; margin-bottom: 8mm; } .subj { font-weight: bold; margin-bottom: 6mm; } .sig { margin-top: 18mm; }"""
    body = (f"<div class='from'>Skarvik Lighthouse Foundation<br>Fyrveien 3<br>8390 Skarvik</div><div class='to'>Kystverket Nordland<br>Postboks 1502<br>6025 Ålesund</div>"
            f"<div class='date'>Skarvik, 30 October 2026</div><div class='subj'>Re: Application for a grant towards the restoration of the lens</div>"
            f"<p>Dear Sir or Madam,</p>{paras(3, 7)}<p>We would be grateful for your support and are happy to provide any further information.</p>"
            "<p>Yours faithfully,</p><div class='sig'>Ingrid Nordvik<br><i>Chair of the board</i></div>")
    return page(css, body)


# -- LibreOffice documents --------------------------------------------------------------------------

LO_DOCS = {
    "lo-report": ("A Writer report: headings, paragraphs, bulleted and numbered lists, a ruled table (LibreOffice fonts and layout).", ["text", "lists", "tables"],
                  "<h1>Annual report</h1>" + paras(0) + "<h2>Highlights</h2><ul><li>Record visitor numbers</li><li>Log transcription completed</li>"
                  "<li>New roof on the oil store</li></ul><h2>Structures</h2>" + table_html(TABLE_ROWS[:6]).replace("<table", "<table border='1' cellpadding='4'")
                  + "<h2>Plans</h2><ol><li>Restore the lens</li><li>Extend opening hours</li><li>Recruit guides</li></ol>" + paras(9)),
    "lo-memo": ("A Writer memo with bold labels, a horizontal rule, emphasis and a long multi-paragraph body.", ["text", "format"],
                "<p><b>MEMO</b></p><p><b>To:</b> All volunteers<br><b>From:</b> The board<br><b>Date:</b> 30 October 2026</p><hr>"
                + paras(1, 2) + "<p><i>Please note</i> that the <b>winter schedule</b> starts on 1 November.</p>" + paras(4, 6, 8)),
}

# -- hand-built PDFs --------------------------------------------------------------------------------


class RawPdf:
    """A minimal PDF writer: base-14 fonts, uncompressed content, optional Flate gray images."""

    def __init__(self):
        self.objects = []

    def add(self, body):
        self.objects.append(body if isinstance(body, bytes) else body.encode("latin-1"))
        return len(self.objects)

    def stream(self, data, extra=""):
        return self.add(f"<< /Length {len(data)} {extra}>>\nstream\n".encode("latin-1") + data + b"\nendstream")

    def save(self, path, pages):
        """pages: [{"size": (w, h), "content": str, "fonts": {"F1": "Helvetica"}, "rotate": 0, "images": {"Im1": (w, h, gray)}}]"""
        self.objects = [b"", b""]  # 1 catalog, 2 page tree
        kids = []
        font_ids = {}
        for pg in pages:
            fonts = []
            for res, base in pg.get("fonts", {}).items():
                if base not in font_ids:
                    font_ids[base] = self.add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>")
                fonts.append(f"/{res} {font_ids[base]} 0 R")
            images = []
            for res, (w, h, gray) in pg.get("images", {}).items():
                oid = self.stream(zlib.compress(gray, 9), f"/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode ")
                images.append(f"/{res} {oid} 0 R")
            content = self.stream(pg["content"].encode("cp1252"))
            w, h = pg["size"]
            res = f"/Resources << /Font << {' '.join(fonts)} >> /XObject << {' '.join(images)} >> >>"
            rotate = f" /Rotate {pg['rotate']}" if pg.get("rotate") else ""
            kids.append(self.add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}]{rotate} {res} /Contents {content} 0 R >>"))
        self.objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        self.objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += f"trailer\n<< /Size {len(self.objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        Path(path).write_bytes(bytes(out))


def pdf_str(s):
    return "(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def text_line(font, size, x, y, s, rgb=None):
    color = f"{rgb[0]} {rgb[1]} {rgb[2]} rg " if rgb else "0 g "
    return f"BT {color}/{font} {size} Tf {x} {y} Td {pdf_str(s)} Tj ET\n"


def wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line] if line else lines


def make_raw_base14(path):
    c = text_line("HB", 20, 72, 730, "Base-14 fonts")
    c += text_line("H", 11, 72, 704, "Helvetica 11 pt, the default sans serif of every PDF reader.")
    c += text_line("HB", 11, 72, 688, "Helvetica-Bold 11 pt for emphasis.")
    c += text_line("HO", 11, 72, 672, "Helvetica-Oblique 11 pt for a slanted voice.")
    y = 640
    for line in wrap(PROSE[0], 88):
        c += text_line("T", 11, 72, y, line)
        y -= 14
    c += text_line("TI", 11, 72, y - 6, "Times-Italic: a quotation set in italics, as printers have done for centuries.")
    c += text_line("TB", 14, 72, y - 34, "Times-Bold 14 pt heading")
    c += text_line("C", 9, 72, y - 56, "Courier 9 pt: for (i = 0; i < n; i++) total += x[i];")
    c += text_line("H", 11, 72, y - 84, "Red text,", (0.8, 0.1, 0.1)) + text_line("H", 11, 130, y - 84, "blue text", (0.1, 0.3, 0.8))
    c += text_line("H", 11, 188, y - 84, "and green text on one line.", (0.1, 0.5, 0.2))
    for i, size in enumerate((8, 10, 14, 18, 24)):
        c += text_line("H", size, 72, y - 120 - sum((8, 10, 14, 18, 24)[:i]) * 1.4, f"Helvetica at {size} points")
    fonts = {"H": "Helvetica", "HB": "Helvetica-Bold", "HO": "Helvetica-Oblique", "T": "Times-Roman", "TI": "Times-Italic",
             "TB": "Times-Bold", "C": "Courier"}
    RawPdf().save(path, [{"size": (612, 792), "content": c, "fonts": fonts}])


def make_raw_rotate90(path):
    c = text_line("HB", 18, 72, 530, "A page with /Rotate 90")
    y = 500
    for line in wrap(PROSE[2] + " " + PROSE[3], 100):
        c += text_line("H", 11, 72, y, line)
        y -= 15
    # Written on a landscape MediaBox and turned a quarter: viewers show it portrait.
    RawPdf().save(path, [{"size": (792, 612), "content": c, "fonts": {"H": "Helvetica", "HB": "Helvetica-Bold"}, "rotate": 90}])


def make_raw_text_grid(path):
    c = text_line("HB", 16, 72, 720, "Ferry timetable, winter 2026")
    cols = [72, 170, 260, 350, 450]
    head = ["Day", "Departs", "Arrives", "Vessel", "Note"]
    rows = [("Monday", "07:15", "08:05", "Havørn", ""), ("Monday", "15:40", "16:30", "Havørn", "Cargo"), ("Tuesday", "07:15", "08:05", "Skarv", ""),
            ("Wednesday", "07:15", "08:05", "Havørn", ""), ("Wednesday", "12:00", "12:50", "Skarv", "Extra"), ("Thursday", "07:15", "08:05", "Havørn", "Mail"),
            ("Friday", "07:15", "08:05", "Skarv", ""), ("Friday", "18:20", "19:10", "Havørn", "Weekend"), ("Saturday", "10:00", "10:50", "Skarv", ""),
            ("Sunday", "16:00", "16:50", "Havørn", "")]
    for x, h in zip(cols, head):
        c += text_line("HB", 10, x, 680, h)
    for i, row in enumerate(rows):
        for x, v in zip(cols, row):
            if v:
                c += text_line("H", 10, x, 660 - 16 * i, v)
    y = 470
    for line in wrap(PROSE[4], 95):
        c += text_line("H", 10, 72, y, line)
        y -= 13
    RawPdf().save(path, [{"size": (612, 792), "content": c, "fonts": {"H": "Helvetica", "HB": "Helvetica-Bold"}}])


def make_scanned(path, source):
    """An image-only page (no text layer), like a scan: page 1 of `source` at 100 dpi."""
    with tempfile.TemporaryDirectory() as tmp:
        w, h, gray = read_pgm(rasterize(source, tmp, "p", 100)[0])
    pw, ph = w * 72 / 100, h * 72 / 100
    RawPdf().save(path, [{"size": (round(pw, 2), round(ph, 2)), "content": f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im1 Do Q\n",
                          "images": {"Im1": (w, h, gray)}}])


# -- images for the HTML documents ------------------------------------------------------------------

def make_images(folder):
    folder = Path(folder)
    # A bar chart.
    w, h = 640, 340
    px = bytearray(b"\xff" * (w * h * 3))

    def rect(x0, y0, x1, y1, rgb):
        for y in range(max(0, y0), min(h, y1)):
            px[(y * w + x0) * 3:(y * w + x1) * 3] = bytes(rgb) * (x1 - x0)

    for gy in range(40, 300, 52):
        rect(50, gy, 620, gy + 1, (220, 220, 220))
    values = [0.35, 0.48, 0.66, 0.92, 1.0, 0.71, 0.4]
    colors = [(31, 95, 191), (31, 95, 191), (31, 95, 191), (11, 79, 108), (11, 79, 108), (31, 95, 191), (31, 95, 191)]
    for i, (v, col) in enumerate(zip(values, colors)):
        x = 70 + i * 78
        rect(x, 300 - int(250 * v), x + 52, 300, col)
    rect(50, 300, 620, 302, (60, 60, 60))
    rect(50, 30, 52, 302, (60, 60, 60))
    write_png(folder / "chart.png", (w, h, bytes(px)), channels=3)
    # A "photo": sky, sun, sea.
    w, h = 600, 400
    px = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            if y < 250 + 12 * math.sin(x / 40):
                t = y / 250
                rgb = (int(250 - 120 * t), int(200 - 60 * t), int(150 + 80 * t))
                if (x - 430) ** 2 + (y - 120) ** 2 < 45 ** 2:
                    rgb = (255, 236, 170)
            else:
                t = (y - 250) / 150
                rgb = (int(30 + 20 * math.sin(x / 7 + y)), int(70 + 30 * t), int(110 + 40 * t))
            px[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb)
    write_png(folder / "photo.png", (w, h, bytes(px)), channels=3)
    if shutil.which("cjpeg"):  # libjpeg-turbo, in the container
        (folder / "photo.ppm").write_bytes(b"P6\n%d %d\n255\n" % (w, h) + bytes(px))
        subprocess.run(["cjpeg", "-quality", "80", "-outfile", str(folder / "photo.jpg"), str(folder / "photo.ppm")],
                       check=True, capture_output=True)
    else:  # macOS
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", str(folder / "photo.png"), "--out", str(folder / "photo.jpg")],
                       check=True, capture_output=True)
    # A round logo with a transparent background.
    w = h = 240
    px = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            r = math.hypot(x - 120, y - 120)
            if r < 110:
                rgb = (255, 255, 255) if 62 < r < 76 or (abs(x - 120) < 10 and y < 120) else (11, 79, 108)
                px[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((*rgb, 255))
    write_png(folder / "logo.png", (w, h, bytes(px)), channels=4)


# -- driver ------------------------------------------------------------------------------------------

def chrome_pdf(html_file, out_pdf):
    with tempfile.TemporaryDirectory() as profile:
        # Chrome will not start as root with its sandbox on (the container runs as root).
        sandbox = ["--no-sandbox"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                        *sandbox, f"--user-data-dir={profile}", "--no-pdf-header-footer", "--virtual-time-budget=5000",
                        f"--print-to-pdf={out_pdf}", Path(html_file).as_uri()], check=True, capture_output=True, timeout=180)
    reproducible(out_pdf)


def lo_pdf(soffice, html_file, outdir):
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--norestore",
                        "--infilter=HTML (StarWriter)", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(outdir), str(html_file)],
                       check=True, capture_output=True, timeout=180)
    reproducible(Path(outdir) / f"{Path(html_file).stem}.pdf")


_ID = re.compile(rb"/ID ?\[[^\]]*\]|/DocChecksum ?/[0-9A-F]{32}")


def reproducible(pdf):
    """Chrome and LibreOffice stamp the time of printing, and LibreOffice an /ID and /DocChecksum
    made from it: the only bytes that differ from one run to the next. A fixed date, and a digest
    of the rest of the file in place of the ID, keep the file reproducible; both are the same
    length, so the xref offsets stay right."""
    data = re.sub(rb"(/(?:CreationDate|ModDate) ?\(D:)\d{14}", rb"\g<1>20260101000000", Path(pdf).read_bytes())
    digest = hashlib.md5(_ID.sub(b"", data)).hexdigest().upper().encode()
    data = _ID.sub(lambda m: re.sub(rb"[0-9A-Fa-f]{32}", digest, m.group()), data)
    Path(pdf).write_bytes(data)


COPIES = {
    "chrome-layout": ("Two columns, rotated text, small caps, letter spacing, sub- and superscript (Chrome).", ["columns", "rotation"]),
    "chrome-scripts": ("Six pages of scripts: Latin with diacritics, Greek, Cyrillic, Arabic, Hebrew, Indic, CJK (Chrome).", ["scripts", "rtl", "multipage"]),
    "chrome-turkish": ("Two pages of Turkish prose with dotted and dotless i (Chrome).", ["scripts"]),
    "lo-writer-turkish": ("A Turkish Writer document with headings and a table (LibreOffice).", ["scripts", "tables"]),
    "tagged": ("A tagged PDF: heading, paragraph, footer artifact.", ["text"]),
    "type3": ("Text drawn with a Type 3 font.", ["fonts"]),
    "inherited": ("Pages that inherit MediaBox 400x300 and Rotate 270 from the page tree.", ["page-size", "rotation"]),
    "content-array": ("Text split across content streams, invisible text, clipping, word and character spacing.", ["text"]),
    "image": ("One image with a caption.", ["images"]),
}


# Short plain titles for the report site, which shows them before the technical descriptions.
TITLES = {
    "chrome/academic-paper": "Academic paper",
    "chrome/code-listing": "Code listing",
    "chrome/form-fields": "Printed form",
    "chrome/headings-emphasis": "Headings and text styles",
    "chrome/highlights-callouts": "Highlights and callout boxes",
    "chrome/images-figures": "Images and figures",
    "chrome/international": "Many languages",
    "chrome/invoice": "Invoice",
    "chrome/letter-a4": "Business letter",
    "chrome/lists": "Lists",
    "chrome/mixed-page-sizes": "Mixed page sizes",
    "chrome/multipage-report": "Eight-page report",
    "chrome/prose-letter": "Plain prose",
    "chrome/rotated-labels": "Rotated table headers",
    "chrome/slide-deck": "Slide deck",
    "chrome/small-print": "Small print",
    "chrome/table-bordered": "Table with borders",
    "chrome/table-borderless": "Table without borders",
    "chrome/table-spans": "Table with merged cells",
    "chrome/three-column-newsletter": "Three-column newsletter",
    "chrome/two-column-article": "Two-column article",
    "chrome/watermark-draft": "Memo with a watermark",
    "handmade/raw-base14": "Standard PDF fonts",
    "handmade/raw-rotate90": "Rotated page",
    "handmade/raw-text-grid": "Timetable without lines",
    "handmade/scanned-page": "Scanned page",
    "libreoffice/lo-memo": "Memo from LibreOffice",
    "libreoffice/lo-report": "Report from LibreOffice",
    "mcp-suite/chrome-layout": "Columns and rotated text",
    "mcp-suite/chrome-scripts": "Six pages of scripts",
    "mcp-suite/chrome-turkish": "Turkish text",
    "mcp-suite/content-array": "Tricky text placement",
    "mcp-suite/image": "Image with a caption",
    "mcp-suite/inherited": "Inherited page settings",
    "mcp-suite/lo-writer-turkish": "Turkish document from LibreOffice",
    "mcp-suite/tagged": "Tagged PDF",
    "mcp-suite/type3": "Hand-drawn font glyphs",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="append", default=[], help="remake NAME (e.g. chrome/invoice), or 'all'")
    args = parser.parse_args()
    meta_path = CORPUS / "corpus.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    def wanted(name):
        return "all" in args.force or name in args.force or not (CORPUS / f"{name}.pdf").exists()

    made = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        make_images(tmp)
        (CORPUS / "chrome").mkdir(parents=True, exist_ok=True)
        for short, (description, tags, fn) in CHROME_DOCS.items():
            name = f"chrome/{short}"
            meta[name] = {"description": description, "tags": tags, "source": "Chrome headless, tools/make_corpus.py"}
            if wanted(name):
                (tmp / f"{short}.html").write_text(fn())
                chrome_pdf(tmp / f"{short}.html", CORPUS / f"{name}.pdf")
                made.append(name)
        soffice = find_soffice()
        (CORPUS / "libreoffice").mkdir(exist_ok=True)
        for short, (description, tags, body) in LO_DOCS.items():
            name = f"libreoffice/{short}"
            meta[name] = {"description": description, "tags": tags, "source": "LibreOffice Writer (HTML import), tools/make_corpus.py"}
            if wanted(name):
                if not soffice:
                    print(f"skipped {name}: no LibreOffice")
                    continue
                (tmp / f"{short}.html").write_text(page("body { font-family: 'Liberation Serif', serif; }", body))
                lo_pdf(soffice, tmp / f"{short}.html", CORPUS / "libreoffice")
                made.append(name)
        (CORPUS / "handmade").mkdir(exist_ok=True)
        raw = {
            "raw-base14": ("Base-14 fonts only (Helvetica, Times, Courier and their bold/italic), colours and sizes 8-24 pt.", ["fonts", "format"], make_raw_base14),
            "raw-rotate90": ("A landscape MediaBox with /Rotate 90, shown portrait.", ["rotation", "page-size"], make_raw_rotate90),
            "raw-text-grid": ("A timetable made of positioned text only: columns but no rules.", ["tables"], make_raw_text_grid),
            "scanned-page": ("An image-only page with no text layer (a rendered page, like a scan).", ["images", "scan"],
                             lambda p: make_scanned(p, CORPUS / "chrome" / "prose-letter.pdf")),
        }
        for short, (description, tags, fn) in raw.items():
            name = f"handmade/{short}"
            meta[name] = {"description": description, "tags": tags, "source": "hand-built, tools/make_corpus.py"}
            if wanted(name):
                fn(CORPUS / f"{name}.pdf")
                made.append(name)
        (CORPUS / "mcp-suite").mkdir(exist_ok=True)
        for short, (description, tags) in COPIES.items():
            name = f"mcp-suite/{short}"
            meta[name] = {"description": description, "tags": tags, "source": "copied from pdfcraft-mcp-suite/fixtures"}
            if wanted(name):
                if not (MCP_FIXTURES / f"{short}.pdf").exists():  # e.g. in the container: keep the copy
                    if not (CORPUS / f"{name}.pdf").exists():
                        print(f"skipped {name}: {MCP_FIXTURES} not found")
                    continue
                shutil.copyfile(MCP_FIXTURES / f"{short}.pdf", CORPUS / f"{name}.pdf")
                made.append(name)
    for name, title in TITLES.items():
        if name in meta:
            meta[name] = {"title": title, **meta[name]}
    meta_path.write_text(json.dumps(dict(sorted(meta.items())), indent=1, ensure_ascii=False) + "\n")
    print(f"made {len(made)}: {', '.join(made) if made else 'nothing (all present; --force NAME to remake)'}")


if __name__ == "__main__":
    main()
