"""Export one PDF to DOCX through a fresh `pdfcraft-cli mcp` server."""

import shutil
import time
from pathlib import Path

from .mcp import CallTimeout, McpProcess, ServerDied, ToolError


def export_docx(cli, pdf, workdir, out_docx, timeout=300.0):
    """Open `pdf` in a sandboxed server and export it to `out_docx`.

    Returns {"ok", "stage", "error", "seconds", "result", "panic"}; `stage` says how far it got
    (start, open, export, done) and `error` is set when ok is false."""
    sandbox = Path(workdir) / "sandbox"
    home = Path(workdir) / "home"
    for d in (sandbox, home / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, sandbox / "source.pdf")
    stderr_path = Path(workdir) / "server.stderr"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home / "tmp"),
           "TZ": "UTC", "LANG": "en_US.UTF-8"}
    out = {"ok": False, "stage": "start", "error": None, "seconds": None, "result": None, "panic": False}
    proc = McpProcess(cli, ["mcp", "--root", str(sandbox)], env, sandbox, stderr_path)
    try:
        proc.initialize()
        out["stage"] = "open"
        opened = proc.call_tool("doc_open", {"path": "source.pdf"}, 120)
        out["stage"] = "export"
        started = time.monotonic()
        result = proc.call_tool("doc_export_office", {"doc": opened["doc"], "path": "export.docx"}, timeout)
        out["seconds"] = round(time.monotonic() - started, 3)
        out["result"] = {k: v for k, v in result.items() if k != "path"}
        produced = sandbox / "export.docx"
        if not produced.exists():
            out["error"] = "the tool reported success but wrote no file"
        else:
            shutil.move(produced, out_docx)
            out["ok"] = True
            out["stage"] = "done"
    except ToolError as e:
        # "internal error: …" is PdfCraft's panic guard answering instead of crashing.
        out["panic"] = str(e).startswith("internal error:")
        out["error"] = f"{out['stage']} refused: {e}"
    except ServerDied as e:
        out["error"] = f"server crashed during {out['stage']} (exit {e.returncode})"
    except CallTimeout:
        out["error"] = f"no answer during {out['stage']} (hang)"
        proc.kill()
        proc = None
    finally:
        if proc is not None:
            proc.close()
    stderr = stderr_path.read_text(errors="replace") if stderr_path.exists() else ""
    if "panicked at" in stderr:
        out["panic"] = True
        out["ok"] = False
        line = next(l for l in stderr.splitlines() if "panicked at" in l)
        out["error"] = (out["error"] + "; " if out["error"] else "") + line.strip()[:300]
    return out
