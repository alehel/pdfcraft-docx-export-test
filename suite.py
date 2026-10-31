#!/usr/bin/env python3
"""PdfCraft DOCX export test suite (see README.md).

  suite.py build [--ref REF | --pr N] [--jobs N]   check out and build pdfcraft-cli
  suite.py run [--only REGEX] [--workers N]        export every corpus PDF over MCP, score it, compare with the baseline
  suite.py report [RUN]                            rebuild a run's reports from its scores.json
  suite.py compare RUN_A RUN_B                     score changes from run A to run B (same build twice = flake check)
  suite.py accept [RUN]                            make RUN (default: the latest) the baseline
  suite.py status                                  build, baseline and recent runs
  suite.py corpus [--force NAME|all]               make missing corpus files (tools/make_corpus.py)
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SUITE = Path(__file__).resolve().parent
sys.path.insert(0, str(SUITE))

from harness import pipeline, render, report  # noqa: E402
from harness.pdfref import ToolFailed  # noqa: E402

# The pdfcraft checkout, its build and the LibreOffice profiles; the container keeps them in a
# volume of their own ($SUITE_WORK), apart from the macOS ones in the checkout.
WORK = Path(os.environ.get("SUITE_WORK", SUITE))
SRC = WORK / "src"
TARGET = WORK / "target"
BIN = TARGET / "debug" / "pdfcraft-cli"
CACHE = WORK / "cache"
CORPUS = SUITE / "corpus"
RUNS = SUITE / "runs"
BASELINE = SUITE / "baseline"
# A local clone (src/ becomes a worktree of it) or a URL (src/ becomes a clone).
PDFCRAFT_REPO = os.environ.get("PDFCRAFT_REPO", str(Path.home() / "dev" / "craft" / "pdfcraft"))
# Where the scores were made: set by the Dockerfile to the image's pins.
ENVIRONMENT = os.environ.get("SUITE_ENVIRONMENT") or f"host: {platform.system()} {platform.machine()}"
TOOLCHAIN_PATH = f"{Path.home()}/.cargo/bin:{Path.home()}/.rustup/toolchains/stable-aarch64-apple-darwin/bin:/opt/homebrew/bin"
KEEP_RUNS = 10


def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def dump_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")


def git(*args, cwd=SRC):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def fmt_duration(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


# -- build ------------------------------------------------------------------------------------------

def cmd_build(args):
    if not (SRC / ".git").exists():
        if "://" in PDFCRAFT_REPO:
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", PDFCRAFT_REPO, str(SRC)],
                           check=True)
        elif not (Path(PDFCRAFT_REPO) / ".git").exists():
            sys.exit(f"no pdfcraft clone at {PDFCRAFT_REPO} (set PDFCRAFT_REPO to a clone or a URL)")
        else:
            subprocess.run(["git", "-C", PDFCRAFT_REPO, "fetch", "-q", "origin", "main"], check=True)
            subprocess.run(["git", "-C", PDFCRAFT_REPO, "worktree", "add", "-q", "--detach", str(SRC), "origin/main"],
                           check=True)
    subprocess.run(["git", "-C", str(SRC), "fetch", "-q", "origin", "main"], check=True)
    if args.pr:
        subprocess.run(["git", "-C", str(SRC), "fetch", "-q", "origin", f"pull/{args.pr}/head"], check=True)
        ref = "FETCH_HEAD"
    else:
        ref = args.ref
    sha = git("rev-parse", ref)
    if not sha:
        sys.exit(f"unknown ref {ref}")
    subprocess.run(["git", "-C", str(SRC), "checkout", "-q", "--detach", sha], check=True)
    print(f"building {sha[:10]} {git('log', '-1', '--format=%s', sha)}")
    log = SUITE / "logs" / f"build-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    log.parent.mkdir(exist_ok=True)
    env = dict(os.environ, PATH=f"{TOOLCHAIN_PATH}:{os.environ.get('PATH', '')}", CARGO_TARGET_DIR=str(TARGET))
    started = time.monotonic()
    with open(log, "w") as out:
        code = subprocess.run(
            ["cargo", "build", "--manifest-path", str(SRC / "Cargo.toml"), "-p", "pdfcraft-cli", "-j", str(args.jobs)],
            stdout=out, stderr=subprocess.STDOUT, env=env, check=False,
        ).returncode
    print("\n".join(log.read_text().splitlines()[-3:]))
    if code != 0:
        sys.exit(f"build failed (exit {code}) after {fmt_duration(time.monotonic() - started)}; log: {log}")
    (TARGET / ".built-sha").write_text(sha + "\n")
    print(f"built {sha[:10]} in {fmt_duration(time.monotonic() - started)}")


# -- run --------------------------------------------------------------------------------------------

def corpus_docs(only=None):
    docs = []
    for path in sorted(CORPUS.rglob("*.pdf")):
        rel = path.relative_to(CORPUS).as_posix()
        name = rel[: -len(".pdf")]
        if only and not re.search(only, name):
            continue
        docs.append({"name": name, "path": str(path), "rel": f"corpus/{rel}"})
    return docs


def pdfcraft_identity(cli):
    if Path(cli).resolve() == BIN.resolve():
        built = (TARGET / ".built-sha").read_text().strip() if (TARGET / ".built-sha").exists() else None
        head = git("rev-parse", "HEAD")
        if built and head and built != head:
            sys.exit(f"the binary was built from {built[:10]} but src/ is at {head[:10]}: run `suite.py build`")
        sha = built or head or ""
        return {"cli": "target/debug/pdfcraft-cli", "sha": sha, "subject": git("log", "-1", "--format=%s", sha) or ""}
    p = subprocess.run([str(cli), "--version"], capture_output=True, text=True)
    return {"cli": str(cli), "sha": "", "subject": (p.stdout.splitlines() or ["?"])[0]}


def oracle_versions(soffice):
    p = subprocess.run(["pdftoppm", "-v"], capture_output=True, text=True)
    poppler = (re.search(r"version ([\d.]+)", p.stderr + p.stdout) or [None, "?"])[1]
    p = subprocess.run([soffice, "--version"], capture_output=True, text=True)
    lo = (re.search(r"LibreOffice ([\d.]+)", p.stdout) or [None, "?"])[1]
    return {"poppler": poppler, "libreoffice": lo}


def check_tools(soffice):
    missing = [t for t in ("pdftohtml", "pdftoppm", "pdfinfo", "pdfimages") if not shutil.which(t)]
    if missing:
        sys.exit(f"missing Poppler tools: {', '.join(missing)} (brew install poppler)")
    if not soffice:
        sys.exit("LibreOffice not found (install it, or set SOFFICE to its soffice binary)")


def cmd_run(args):
    cli = Path(args.cli or os.environ.get("PDFCRAFT_CLI") or BIN)
    if not cli.exists():
        sys.exit(f"no pdfcraft-cli at {cli}: run `suite.py build`, or pass --cli")
    soffice = render.find_soffice()
    check_tools(soffice)
    docs = corpus_docs(args.only)
    if not docs:
        sys.exit("no PDFs in corpus/ match" + (f" --only {args.only}" if args.only else ""))
    identity = pdfcraft_identity(cli)
    now = dt.datetime.now(dt.timezone.utc)
    run_id = f"{now:%Y%m%d-%H%M%S}-{identity['sha'][:8] or 'custom'}" + (f"-{args.label}" if args.label else "")
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True)
    meta = {"id": run_id, "started": now.isoformat(timespec="seconds"), "pdfcraft": identity,
            "environment": ENVIRONMENT, "oracles": oracle_versions(soffice), "workers": args.workers,
            "only": args.only}
    baseline = None if args.no_baseline else load_json(BASELINE / "scores.json")
    base_docs = (baseline or {}).get("docs", {})
    print(f"run {run_id}: {len(docs)} PDFs, PdfCraft {identity['sha'][:10] or identity['subject']}, {args.workers} workers")
    if baseline and baseline["meta"].get("environment") != ENVIRONMENT:
        print(f"warning: the baseline was made in another environment ({baseline['meta'].get('environment', '?')}),"
              " so differences are not only PdfCraft's")

    profiles = queue.Queue()
    for i in range(args.workers):
        profiles.put(CACHE / f"lo-profile-{i}")
    # Sandboxes and page bitmaps go on a local disk: in the container, run_dir is a folder shared
    # with the host, and under load its listing of just-written bitmaps has come back short.
    scratch = run_dir / "work" if args.keep_work else Path(tempfile.mkdtemp(prefix="docx-suite-"))
    ctx = {"cli": cli.resolve(), "run_dir": run_dir, "scratch": scratch, "soffice": soffice, "profiles": profiles,
           "image_pages": args.image_pages, "timeout": args.timeout, "keep_work": args.keep_work}
    started = time.monotonic()
    records = []
    width = max(len(d["name"]) for d in docs)
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(pipeline.evaluate, d, ctx): d for d in docs}
        for fut in concurrent.futures.as_completed(futures):
            rec = fut.result()
            records.append(rec)
            base = base_docs.get(rec["name"])
            delta = f" ({100 * (rec['score'] - base['score']):+.1f})" if base and base["sha256"] == rec["sha256"] else ""
            flag = "" if rec["status"] == "ok" else f"  {rec['status']}: {rec['error']}"
            print(f"  [{len(records):>{len(str(len(docs)))}}/{len(docs)}] {rec['name']:<{width}} "
                  f"{100 * rec['score']:5.1f}{delta}  {rec['seconds']:.1f}s{flag}", flush=True)
    meta["seconds"] = round(time.monotonic() - started, 1)
    if not args.keep_work:
        shutil.rmtree(scratch, ignore_errors=True)
    records.sort(key=lambda r: r["name"])
    dump_json(run_dir / "run.json", meta)
    dump_json(run_dir / "scores.json", records)
    write_reports(run_dir, meta, records, baseline)
    prune_runs()
    return summarize(run_dir, meta, records)


def write_reports(run_dir, meta, records, baseline):
    report.classify(records, baseline)
    (run_dir / "report.md").write_text(report.markdown(meta, records, baseline))
    (run_dir / "report.html").write_text(report.html_report(meta, records, baseline))


def summarize(run_dir, meta, records):
    counts = report.summary_counts(records)
    print(f"\nmean score {100 * report.mean_score(records):.1f} over {len(records)} PDFs in {fmt_duration(meta.get('seconds', 0))}: "
          + ", ".join(f"{report.CLASSES[c][0]} {n} {report.CLASSES[c][1]}" for c, n in
                      sorted(counts.items(), key=lambda x: report.ORDER.index(x[0]))))
    for rec in sorted(records, key=report.sort_key):
        if rec["class"] in ("regression", "failed", "still-failing", "improvement", "fixed"):
            delta = f" {100 * rec['delta']:+.1f}" if rec["delta"] is not None else ""
            why = f" — {rec['error']}" if rec.get("error") else ""
            print(f"  {report.CLASSES[rec['class']][0]} {rec['name']}: {100 * rec['score']:.1f}{delta}{why}")
    # In the container, /suite is the checkout: show the path as the host sees it, relative to it.
    shown = run_dir.relative_to(SUITE) if os.environ.get("SUITE_ENVIRONMENT") else run_dir
    print(f"\nreport: {shown / 'report.html'}\n        {shown / 'report.md'}")
    return 1 if counts.get("regression") or counts.get("failed") else 0


def prune_runs():
    runs = sorted((p for p in RUNS.iterdir() if p.is_dir()), key=lambda p: p.name)
    for old in runs[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)


def resolve_run(name):
    runs = sorted((p for p in RUNS.iterdir() if p.is_dir()), key=lambda p: p.name) if RUNS.exists() else []
    if not runs:
        sys.exit("no runs yet")
    if name in (None, "latest"):
        return runs[-1]
    matches = [p for p in runs if p.name == name or p.name.startswith(name)]
    if len(matches) != 1:
        sys.exit(f"no single run matches {name}")
    return matches[0]


def cmd_report(args):
    run_dir = resolve_run(args.run)
    meta, records = load_json(run_dir / "run.json"), load_json(run_dir / "scores.json")
    write_reports(run_dir, meta, records, load_json(BASELINE / "scores.json"))
    return summarize(run_dir, meta, records)


def cmd_compare(args):
    a, b = resolve_run(args.run_a), resolve_run(args.run_b)
    base = {"meta": {**load_json(a / "run.json"), "run": a.name},
            "docs": {r["name"]: r for r in load_json(a / "scores.json")}}
    meta, records = load_json(b / "run.json"), load_json(b / "scores.json")
    report.classify(records, base)
    changed = [r for r in records if r["delta"] or r["changes"] or r["class"] not in ("same",)]
    print(f"{a.name} → {b.name}: {len(records)} PDFs, mean {100 * report.mean_score(records):.1f}")
    if not changed:
        print("identical scores")
    for rec in sorted(changed, key=report.sort_key):
        delta = f" {100 * rec['delta']:+.2f}" if rec["delta"] is not None else ""
        print(f"  {report.CLASSES[rec['class']][0]} {rec['name']}{delta} "
              + ", ".join(f"{k} {100 * x:.1f}→{100 * y:.1f}" for k, x, y in rec["changes"]))
    return 0


def cmd_accept(args):
    run_dir = resolve_run(args.run)
    meta, records = load_json(run_dir / "run.json"), load_json(run_dir / "scores.json")
    if meta.get("only") and not args.force:
        sys.exit("this run covered a subset (--only); pass --force to accept it anyway")
    keep = ("sha256", "status", "score", "components", "details", "counts")
    docs = {r["name"]: {k: r.get(k) for k in keep} for r in records}
    if meta.get("only"):
        docs = {**(load_json(BASELINE / "scores.json") or {}).get("docs", {}), **docs}
    for sub in ("images", "docx"):
        shutil.rmtree(BASELINE / sub, ignore_errors=True)
    for rec in records:
        key = pipeline.safe_name(rec["name"])
        for shot in rec.get("images", []):
            if shot.get("docx"):
                dest = BASELINE / shot["docx"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(run_dir / shot["docx"], dest)
        if rec.get("docx_file"):
            (BASELINE / "docx").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run_dir / rec["docx_file"], BASELINE / "docx" / f"{key}.docx")
    dump_json(BASELINE / "scores.json", {"meta": {**meta, "run": run_dir.name}, "docs": docs})
    print(f"baseline is now {run_dir.name}: {len(docs)} PDFs, mean {100 * report.mean_score(list(docs.values())):.1f}")
    print("commit it with: git add baseline && git commit -m 'Accept baseline " + run_dir.name + "'")
    return 0


def cmd_status(args):
    built = (TARGET / ".built-sha").read_text().strip() if (TARGET / ".built-sha").exists() else None
    print(f"build:    {built[:10] + ' ' + (git('log', '-1', '--format=%s', built) or '') if built else 'none (suite.py build)'}")
    git("fetch", "-q", "origin", "main")
    main = git("rev-parse", "origin/main")
    if main and built:
        behind = git("rev-list", "--count", f"{built}..{main}")
        print(f"origin/main: {main[:10]} ({behind} commits ahead of the build)")
    baseline = load_json(BASELINE / "scores.json")
    if baseline:
        m = baseline["meta"]
        print(f"baseline: {m['run']} (PdfCraft {m['pdfcraft'].get('sha', '')[:10]}), {len(baseline['docs'])} PDFs, "
              f"mean {100 * report.mean_score(list(baseline['docs'].values())):.1f}")
    else:
        print("baseline: none")
    print(f"corpus:   {len(corpus_docs())} PDFs")
    print(f"environment: {ENVIRONMENT}")
    if RUNS.exists():
        for run_dir in sorted(RUNS.iterdir())[-5:]:
            records = load_json(run_dir / "scores.json")
            if records:
                print(f"  run {run_dir.name}: {len(records)} PDFs, mean {100 * report.mean_score(records):.1f}")
    return 0


def cmd_corpus(args):
    force = [x for name in args.force for x in ("--force", name)]
    return subprocess.run([sys.executable, "-I", str(SUITE / "tools" / "make_corpus.py"), *force]).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="check out and build pdfcraft-cli in src/ and target/")
    b.add_argument("--ref", default="origin/main")
    b.add_argument("--pr", type=int)
    b.add_argument("--jobs", type=int, default=4)
    r = sub.add_parser("run", help="export and score the corpus")
    r.add_argument("--only", help="regex on corpus names")
    r.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    r.add_argument("--cli", help="pdfcraft-cli to test (default: target/debug/pdfcraft-cli, or $PDFCRAFT_CLI)")
    r.add_argument("--label", help="suffix for the run id")
    r.add_argument("--image-pages", type=int, default=12, help="pages per PDF shown in the HTML report")
    r.add_argument("--timeout", type=float, default=300, help="seconds per export")
    r.add_argument("--keep-work", action="store_true", help="keep each PDF's sandbox and page bitmaps")
    r.add_argument("--no-baseline", action="store_true")
    rp = sub.add_parser("report", help="rebuild a run's reports")
    rp.add_argument("run", nargs="?")
    c = sub.add_parser("compare", help="score changes between two runs")
    c.add_argument("run_a")
    c.add_argument("run_b")
    a = sub.add_parser("accept", help="make a run the baseline")
    a.add_argument("run", nargs="?")
    a.add_argument("--force", action="store_true")
    sub.add_parser("status")
    co = sub.add_parser("corpus", help="make missing corpus files")
    co.add_argument("--force", action="append", default=[], help="remake NAME (e.g. chrome/invoice), or 'all'")
    args = parser.parse_args()
    handler = {"build": cmd_build, "run": cmd_run, "report": cmd_report, "compare": cmd_compare,
               "accept": cmd_accept, "status": cmd_status, "corpus": cmd_corpus}[args.command]
    try:
        sys.exit(handler(args) or 0)
    except ToolFailed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
