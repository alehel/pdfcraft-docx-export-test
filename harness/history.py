"""The run history (public/history.jsonl): one line per run with its scores, kept when old run folders
are pruned, so the index can chart accuracy over every run ever made."""

import json
from pathlib import Path

PARTS = ("words", "order", "format", "visual", "structure")


def summarize(meta, records):
    """A run's history line: who was tested where, the mean and part means, and every document's
    score, status, PDF hash (to tell a changed corpus from a changed PdfCraft) and parts (in PARTS
    order; lines from before they were kept have three fields)."""
    parts = {}
    for k in PARTS:
        values = [r["components"][k] for r in records if (r.get("components") or {}).get(k) is not None]
        parts[k] = round(sum(values) / len(values), 4) if values else None
    halves = {}
    for half in ("looks", "text"):
        values = [r["halves"][half] for r in records if (r.get("halves") or {}).get(half) is not None]
        halves[half] = round(sum(values) / len(values), 4) if values else None
    classes = None
    if all("class" in r for r in records):
        classes = {}
        for r in records:
            classes[r["class"]] = classes.get(r["class"], 0) + 1
    return {
        "id": meta["id"], "started": meta["started"], "seconds": meta.get("seconds"), "scoring": meta.get("scoring", 1),
        "label": meta.get("label"), "only": meta.get("only"), "environment": meta.get("environment"),
        "oracles": meta.get("oracles"), "pdfcraft": meta.get("pdfcraft", {}), "suite": meta.get("suite"),
        "baseline": meta.get("baseline"),
        "mean": round(sum(r["score"] for r in records) / len(records), 4) if records else 0.0,
        "parts": parts, "halves": halves, "classes": classes,
        "docs": {r["name"]: [round(r["score"], 4), r["status"], r["sha256"][:12],
                             [None if (r.get("components") or {}).get(k) is None else round(r["components"][k], 4)
                              for k in PARTS]] for r in records},
    }


def load(path):
    entries = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                entries[entry["id"]] = entry
    return list(entries.values())


def save(path, entries):
    """Write the whole history again (after a rescore), oldest first."""
    Path(path).write_text("".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
                                  for e in sorted(entries, key=lambda e: e["started"])))


def append(path, entry):
    with open(path, "a") as out:
        out.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def backfill(path, runs_dir):
    """Add run folders made before the history existed (or before a line got written)."""
    known = {e["id"] for e in load(path)}
    for run_dir in sorted(p for p in Path(runs_dir).iterdir() if p.is_dir()):
        if run_dir.name in known or not (run_dir / "run.json").exists() or not (run_dir / "scores.json").exists():
            continue
        meta = json.loads((run_dir / "run.json").read_text())
        records = json.loads((run_dir / "scores.json").read_text())
        if meta.get("id") == run_dir.name and records:
            append(path, summarize(meta, records))
    return load(path)
