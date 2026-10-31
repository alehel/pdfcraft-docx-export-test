#!/usr/bin/env python3
"""PdfCraft DOCX export test suite (see README.md).

  suite.py build [--ref REF | --pr N] [--jobs N]   check out and build pdfcraft-cli
  suite.py run [--only REGEX] [--workers N]        export every corpus PDF over MCP, score it, compare with the baseline
  suite.py report [RUN]                            rebuild a run's reports from its scores.json
  suite.py compare RUN_A RUN_B                     score changes from run A to run B (same build twice = flake check)
  suite.py accept [RUN]                            make RUN (default: the latest) the baseline
  suite.py status                                  build, baseline and recent runs
  suite.py index                                   rebuild public/index.html (every report, accuracy over time)
  suite.py rescore                                 score the kept runs again after the formula changed
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

from harness import docpage, history, index, pipeline, render, report, score  # noqa: E402
from harness.pdfref import ToolFailed  # noqa: E402

# The pdfcraft checkout, its build and the LibreOffice profiles; the container keeps them in a
# volume of their own ($SUITE_WORK), apart from the macOS ones in the checkout.
WORK = Path(os.environ.get("SUITE_WORK", SUITE))
SRC = WORK / "src"
TARGET = WORK / "target"
BIN = TARGET / "debug" / "pdfcraft-cli"
CACHE = WORK / "cache"
CORPUS = SUITE / "corpus"
# Everything to look at, as a static site: index.html, history.jsonl, and a folder per run with its
# report and the files the report shows.
PUBLIC = SUITE / "public"
HISTORY = PUBLIC / "history.jsonl"
BASELINE = SUITE / "baseline"
# A local clone (src/ becomes a worktree of it) or a URL (src/ becomes a clone).
PDFCRAFT_REPO = os.environ.get("PDFCRAFT_REPO", str(Path.home() / "dev" / "craft" / "pdfcraft"))
# Where the scores were made: set by the Dockerfile to the image's pins.
ENVIRONMENT = os.environ.get("SUITE_ENVIRONMENT") or f"host: {platform.system()} {platform.machine()}"
TOOLCHAIN_PATH = f"{Path.home()}/.cargo/bin:{Path.home()}/.rustup/toolchains/stable-aarch64-apple-darwin/bin:/opt/homebrew/bin"
# Run folders kept: the newest KEEP_RUNS, the baseline's, and the latest run of every tagged commit.
# Older ones are deleted; their scores stay in public/history.jsonl.
KEEP_RUNS = int(os.environ.get("SUITE_KEEP_RUNS", "10"))


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
    (TARGET / ".built-ref").write_text((f"pull/{args.pr}" if args.pr else args.ref) + "\n")
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
        ref = (TARGET / ".built-ref").read_text().strip() if (TARGET / ".built-ref").exists() else None
        return {"cli": "target/debug/pdfcraft-cli", "sha": sha, "ref": ref, **commit_info(sha)}
    p = subprocess.run([str(cli), "--version"], capture_output=True, text=True)
    return {"cli": str(cli), "sha": "", "subject": (p.stdout.splitlines() or ["?"])[0]}


def commit_info(sha):
    """What the pdfcraft clone knows about a commit: subject, date, tags on it, `git describe`,
    whether origin/main contains it, and its repository's web address."""
    if not sha or git("cat-file", "-e", f"{sha}^{{commit}}") is None:
        return {}
    log = (git("log", "-1", "--format=%s%x00%cI", sha) or "\x00").split("\x00")
    tags = git("tag", "--points-at", sha, "--sort=-v:refname")
    return {"subject": log[0], "date": log[1], "tags": tags.split() if tags else [],
            "describe": git("describe", "--tags", sha), "on_main": git("merge-base", "--is-ancestor", sha, "origin/main") is not None,
            "url": web_url(git("remote", "get-url", "origin"))}


def web_url(remote):
    m = re.match(r"(?:https://|git@)github\.com[/:](.+?)(?:\.git)?/?$", remote or "")
    return f"https://github.com/{m[1]}" if m else None


def suite_identity():
    """This checkout's commit, and whether tracked files differ from it."""
    sha = git("rev-parse", "HEAD", cwd=SUITE)
    return {"sha": sha, "dirty": bool(git("status", "--porcelain", "--untracked-files=no", cwd=SUITE))} if sha else None


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
    run_dir = PUBLIC / run_id
    run_dir.mkdir(parents=True)
    baseline = None if args.no_baseline else load_json(BASELINE / "scores.json")
    meta = {"id": run_id, "started": now.isoformat(timespec="seconds"), "label": args.label, "scoring": score.VERSION,
            "pdfcraft": identity,
            "suite": suite_identity(), "environment": ENVIRONMENT, "oracles": oracle_versions(soffice),
            "workers": args.workers, "only": args.only, "baseline": baseline["meta"].get("run") if baseline else None}
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
    update_index(history.summarize(meta, records))
    return summarize(run_dir, meta, records)


def write_reports(run_dir, meta, records, baseline):
    report.classify(records, baseline)
    (run_dir / "report.md").write_text(report.markdown(meta, records, baseline))
    (run_dir / "report.html").write_text(report.html_report(meta, records, baseline, corpus_meta(), run_dir))


def corpus_meta():
    """corpus/corpus.json: each test file's title, description and tags."""
    return load_json(CORPUS / "corpus.json") or {}


def run_dirs():
    """The run folders in public/, oldest first (public/ also holds docs/)."""
    return sorted((p for p in PUBLIC.iterdir() if (p / "run.json").exists()), key=lambda p: p.name) if PUBLIC.exists() else []


def update_index(entry=None):
    """Add a run to the history (with any run folders it lacks), prune old run folders, and rebuild
    public/index.html and the test files' history pages in public/docs/."""
    PUBLIC.mkdir(exist_ok=True)
    if entry:
        history.append(HISTORY, entry)
    entries = history.backfill(HISTORY, PUBLIC)
    refresh_commits(entries)
    baseline = load_json(BASELINE / "scores.json")
    baseline_run = baseline["meta"].get("run") if baseline else None
    prune_runs(entries, baseline_run)
    available = {p.name for p in PUBLIC.iterdir() if (p / "report.html").exists()}
    (PUBLIC / "index.html").write_text(index.html_index(entries, available, baseline_run, PUBLIC, corpus_meta()))
    docs = PUBLIC / "docs"
    shutil.rmtree(docs, ignore_errors=True)
    docs.mkdir()
    for name, page in docpage.build(index.tested_commits(entries)[0], PUBLIC, corpus_meta()).items():
        (docs / name).write_text(page)
    return PUBLIC / "index.html"


def refresh_commits(entries):
    """Fill in what the clone knows now: tags made after a run, and details older runs lack."""
    if not (SRC / ".git").exists():
        return
    known = {}
    for entry in entries:
        sha = entry["pdfcraft"].get("sha")
        if sha:
            if sha not in known:
                known[sha] = commit_info(sha)
            entry["pdfcraft"].update(known[sha])


def prune_runs(entries, baseline_run):
    runs = [p.name for p in run_dirs()]
    keep = set(runs[-KEEP_RUNS:]) | {baseline_run}
    tagged = {}
    for entry in sorted(entries, key=lambda e: e["started"]):
        if not entry.get("only"):
            for tag in entry["pdfcraft"].get("tags") or []:
                tagged[tag] = entry["id"]
    keep |= set(tagged.values())
    for name in runs:
        if name not in keep:
            shutil.rmtree(PUBLIC / name, ignore_errors=True)


def shown(path):
    """A path as the user sees it: in the container, /suite is the checkout, so relative to that."""
    return Path(path).relative_to(SUITE) if os.environ.get("SUITE_ENVIRONMENT") else path


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
    print(f"\nreport: {shown(run_dir / 'report.html')}\n        {shown(run_dir / 'report.md')}"
          f"\nindex:  {shown(PUBLIC / 'index.html')}")
    return 1 if counts.get("regression") or counts.get("failed") else 0


def resolve_run(name):
    runs = run_dirs()
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
    update_index()
    return summarize(run_dir, meta, records)


def cmd_index(args):
    print(f"index: {shown(update_index())}")
    return 0


def cmd_compare(args):
    a, b = resolve_run(args.run_a), resolve_run(args.run_b)
    base = {"meta": {**load_json(a / "run.json"), "run": a.name},
            "docs": {r["name"]: r for r in load_json(a / "scores.json")}}
    meta, records = load_json(b / "run.json"), load_json(b / "scores.json")
    report.classify(records, base)
    # Below 0.05 points is rounding (a rescored run keeps its page scores to four decimals).
    changed = [r for r in records if abs(r["delta"] or 0) >= 0.0005 or r["changes"] or r["class"] not in ("same",)]
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
    meta = load_json(run_dir / "run.json")
    if meta.get("only") and not args.force:
        sys.exit("this run covered a subset (--only); pass --force to accept it anyway")
    docs = accept(run_dir)
    update_index()
    print(f"baseline is now {run_dir.name}: {len(docs)} PDFs, mean {100 * report.mean_score(list(docs.values())):.1f}")
    print("commit it with: git add baseline && git commit -m 'Accept baseline " + run_dir.name + "'")
    return 0


def accept(run_dir):
    """Make a run the baseline: its scores (with the page scores, so it can be rescored) and DOCX files."""
    meta, records = load_json(run_dir / "run.json"), load_json(run_dir / "scores.json")
    keep = ("sha256", "status", "score", "halves", "components", "details", "counts", "pages")
    docs = {r["name"]: {k: r.get(k) for k in keep} for r in records}
    if meta.get("only"):
        docs = {**(load_json(BASELINE / "scores.json") or {}).get("docs", {}), **docs}
    # The DOCX files go into git with the scores; the page screenshots stay in the baseline's run
    # folder, which pruning keeps.
    for sub in ("images", "docx"):
        shutil.rmtree(BASELINE / sub, ignore_errors=True)
    for rec in records:
        if rec.get("docx_file"):
            (BASELINE / "docx").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run_dir / rec["docx_file"], BASELINE / "docx" / f"{pipeline.safe_name(rec['name'])}.docx")
    dump_json(BASELINE / "scores.json", {"meta": {**meta, "run": run_dir.name}, "docs": docs})
    return docs


def cmd_rescore(args):
    """Score the kept runs again with the current formula, from the page scores they stored; then
    the baseline (again from its run), their reports and their lines in the history. Runs that are
    no longer kept keep their old scores, and the index leaves them out of its charts."""
    rescored = []
    for run_dir in run_dirs():
        meta, records = load_json(run_dir / "run.json"), load_json(run_dir / "scores.json")
        if meta.get("scoring", 1) == score.VERSION and not args.force:
            continue
        for rec in records:
            pipeline.rescore(rec)
        meta["scoring"] = score.VERSION
        dump_json(run_dir / "run.json", meta)
        dump_json(run_dir / "scores.json", records)
        rescored.append(run_dir)
    baseline = load_json(BASELINE / "scores.json")
    if baseline and baseline["meta"].get("scoring", 1) != score.VERSION:
        base_dir = PUBLIC / baseline["meta"].get("run", "")
        if (base_dir / "scores.json").exists():
            accept(base_dir)
            print(f"baseline rescored from its run {base_dir.name}")
        else:
            print("warning: the baseline's run is not kept, so it cannot be rescored: accept a new baseline")
    baseline = load_json(BASELINE / "scores.json")
    entries = {e["id"]: e for e in history.load(HISTORY)}
    for run_dir in rescored:
        meta, records = load_json(run_dir / "run.json"), load_json(run_dir / "scores.json")
        write_reports(run_dir, meta, records, baseline)
        entries[run_dir.name] = history.summarize(meta, records)
    history.save(HISTORY, list(entries.values()))
    update_index()
    print(f"rescored {len(rescored)} runs with scoring version {score.VERSION}")
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
    for run_dir in run_dirs()[-5:]:
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
    sub.add_parser("index", help="rebuild public/index.html")
    rs = sub.add_parser("rescore", help="score the kept runs again with the current formula")
    rs.add_argument("--force", action="store_true", help="also runs already on the current formula")
    co = sub.add_parser("corpus", help="make missing corpus files")
    co.add_argument("--force", action="append", default=[], help="remake NAME (e.g. chrome/invoice), or 'all'")
    args = parser.parse_args()
    handler = {"build": cmd_build, "run": cmd_run, "report": cmd_report, "compare": cmd_compare,
               "accept": cmd_accept, "status": cmd_status, "index": cmd_index, "rescore": cmd_rescore, "corpus": cmd_corpus}[args.command]
    try:
        sys.exit(handler(args) or 0)
    except ToolFailed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
