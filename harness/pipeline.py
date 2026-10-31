"""One document through the whole pipeline: export over MCP, read both sides, render, score."""

import hashlib
import shutil
import time
import traceback
from pathlib import Path

from . import docx as docxmod
from . import pdfref, render, score
from .export import export_docx

DPI = 50  # renders for the visual score and the report's page images


def safe_name(name):
    return name.replace("/", "__")


def evaluate(doc, ctx):
    """Score one corpus PDF. `doc` is {"name", "path"}; `ctx` has cli, run_dir, scratch (a folder on
    a local disk for the sandbox and page bitmaps), soffice, profiles (a queue of LibreOffice
    profile folders), image_pages, timeout and keep_work (scratch is then inside run_dir)."""
    started = time.monotonic()
    pdf = Path(doc["path"])
    data = pdf.read_bytes()
    rec = {"name": doc["name"], "file": doc["rel"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
           "status": "ok", "error": None, "score": 0.0, "notes": []}
    work = Path(ctx["scratch"]) / safe_name(doc["name"])
    try:
        _evaluate(rec, pdf, work, ctx)
    except Exception as e:  # a harness failure: record it and carry on with the other documents
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["traceback"] = traceback.format_exc()
        rec["score"] = 0.0
    finally:
        if not ctx.get("keep_work"):
            shutil.rmtree(work, ignore_errors=True)
    rec["seconds"] = round(time.monotonic() - started, 2)
    return rec


def _evaluate(rec, pdf, work, ctx):
    run_dir = ctx["run_dir"]
    key = safe_name(rec["name"])
    out_docx = run_dir / "docx" / f"{key}.docx"
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    exp = export_docx(ctx["cli"], pdf, work / "export", out_docx, ctx["timeout"])
    rec["export"] = {k: exp[k] for k in ("seconds", "result", "panic", "stage")}
    if not exp["ok"]:
        rec["status"] = "export-failed"
        rec["error"] = exp["error"]
        return
    rec["docx_file"] = str(out_docx.relative_to(run_dir))

    info = docxmod.read_docx(out_docx)
    sections = info["sections"]
    rec["docx"] = {"bytes": out_docx.stat().st_size, "errors": info["errors"], "warnings": info["warnings"],
                   "sections": len(sections), "media": info["media"], **info["totals"]}
    if info["errors"]:
        rec["status"] = "invalid-docx"
        rec["error"] = info["errors"][0] + (f" (+{len(info['errors']) - 1} more)" if len(info["errors"]) > 1 else "")

    ref = pdfref.read_reference(pdf)
    n_ref = len(ref["page_sizes"])

    rendered = None
    profile = ctx["profiles"].get()
    try:
        rendered = render.docx_to_pdf(ctx["soffice"], out_docx, run_dir / "rendered", profile)
        rec["rendered_file"] = str(rendered.relative_to(run_dir))
    except pdfref.ToolFailed as e:
        rec["notes"].append(f"LibreOffice: {e}")
        if rec["status"] == "ok":
            rec["status"] = "render-failed"
            rec["error"] = str(e)
    finally:
        ctx["profiles"].put(profile)
    rendered_sizes = pdfref.page_sizes(rendered) if rendered else None

    ref_pages = render.rasterize(pdf, work / "ref", "p", DPI)
    out_pages = render.rasterize(rendered, work / "docx", "p", DPI) if rendered else []
    # A missing bitmap would score its page 0 as if it were blank: fail loudly instead.
    for what, pages, expected in (("PDF", ref_pages, n_ref),
                                  ("rendered DOCX", out_pages, len(rendered_sizes) if rendered else 0)):
        if len(pages) != expected:
            raise pdfref.ToolFailed(f"pdftoppm left {len(pages)} page bitmaps of the {what}'s {expected} pages")
    image_dir = run_dir / "images" / key
    image_dir.mkdir(parents=True, exist_ok=True)
    similarities, images = [], []
    for i in range(max(len(ref_pages), len(out_pages))):
        a = render.read_pgm(ref_pages[i]) if i < len(ref_pages) else None
        b = render.read_pgm(out_pages[i]) if i < len(out_pages) else None
        similarities.append(render.page_similarity(a, b) if a and b else 0.0)
        if i < ctx["image_pages"]:
            shot = {"page": i + 1}
            for kind, img in (("ref", a), ("docx", b)):
                if img:
                    render.write_png(image_dir / f"p{i + 1:03}-{kind}.png", img)
                    shot[kind] = f"images/{key}/p{i + 1:03}-{kind}.png"
            if a and b:
                render.write_png(image_dir / f"p{i + 1:03}-diff.png", render.overlay(a, b), channels=3)
                shot["diff"] = f"images/{key}/p{i + 1:03}-diff.png"
            images.append(shot)
    rec["images"] = images
    visual = score.mean(similarities) if similarities else None

    docx_pages = [s.words for s in sections]
    text, pairs, missing, extra = score.text_scores(ref["words"], docx_pages, ref["raw_words"])
    fmt, fmt_parts = score.format_scores(pairs)
    images_ref = sum(ref["images"])
    images_docx = info["totals"].get("images", 0)
    structure, structure_parts = score.structure_scores(ref["page_sizes"], images_ref, sections, images_docx,
                                                         rendered_sizes)
    rec["components"] = {"words": text["words"], "order": text["order"], "format": fmt, "visual": visual,
                         "structure": structure}
    rec["details"] = {"recall": text["recall"], "precision": text["precision"], "order_layout": text["order_layout"],
                      "order_raw": text["order_raw"], **fmt_parts, **structure_parts}
    rec["score"] = 0.0 if rec["status"] == "invalid-docx" else score.total(rec["components"])
    rec["missing"] = missing
    rec["extra"] = extra
    rec["counts"] = {
        "pages_ref": n_ref, "pages_docx": len(rendered_sizes) if rendered_sizes is not None else None,
        "words_ref": sum(len(p) for p in ref["words"]), "words_docx": sum(len(p) for p in docx_pages),
        "images_ref": images_ref, "images_docx": images_docx,
        "positioned_pages": sum(1 for s in sections if s.textboxes),
    }
    per_page = len(sections) == n_ref
    rec["pages"] = [{
        "page": i + 1,
        "visual": round(similarities[i], 4) if i < len(similarities) else None,
        "text": score.text_scores([ref["words"][i]], [sections[i].words])[0]["words"] if per_page else None,
        "words_ref": len(ref["words"][i]),
        "words_docx": len(sections[i].words) if per_page else None,
        "images_ref": ref["images"][i],
        "images_docx": sections[i].images if per_page else None,
        "positioned": bool(sections[i].textboxes) if per_page else None,
    } for i in range(n_ref)]
    if not per_page:
        rec["notes"].append(f"the DOCX has {len(sections)} sections for {n_ref} pages, so text is compared as one block")
