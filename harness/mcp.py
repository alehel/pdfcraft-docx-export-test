"""A minimal MCP client: one `pdfcraft-cli mcp` child process, newline-delimited JSON-RPC 2.0.

Adapted from the MCP regression suite (~/dev/craft/pdfcraft-mcp-suite/harness/mcp.py)."""

import json
import queue
import subprocess
import threading
import time


class ServerDied(Exception):
    """The server process ended (or closed stdout) while we waited for a reply."""

    def __init__(self, returncode):
        super().__init__(f"server exited with {returncode}")
        self.returncode = returncode


class CallTimeout(Exception):
    """No reply within the time limit."""


class ToolError(Exception):
    """The tool call answered with an error (a JSON-RPC error or `isError: true`)."""


class McpProcess:
    def __init__(self, binary, args, env, cwd, stderr_path):
        self._stderr = open(stderr_path, "ab")
        self.proc = subprocess.Popen(
            [str(binary), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
            env=env, cwd=cwd,
        )
        self._lines = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._next_id = 1

    def _read(self):
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _send(self, text):
        try:
            self.proc.stdin.write(text.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ServerDied(self._returncode())

    def _returncode(self):
        try:
            return self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return None

    def _next_line(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CallTimeout()
        try:
            line = self._lines.get(timeout=remaining)
        except queue.Empty:
            raise CallTimeout()
        if line is None:
            raise ServerDied(self._returncode())
        return line.decode("utf-8", errors="replace")

    def request(self, method, params=None, timeout=120.0):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline)
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(reply, dict) and reply.get("id") == rid:
                return reply

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(json.dumps(msg))

    def initialize(self, version="2025-06-18", timeout=60.0):
        reply = self.request("initialize", {
            "protocolVersion": version, "capabilities": {},
            "clientInfo": {"name": "pdfcraft-docx-export-test", "version": "1"},
        }, timeout)
        self.notify("notifications/initialized")
        return reply

    def call_tool(self, name, arguments, timeout=120.0):
        """Call a tool and return its structured result; raise ToolError when it refuses."""
        reply = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if "error" in reply:
            raise ToolError(reply["error"].get("message") or json.dumps(reply["error"]))
        result = reply.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise ToolError(text or "isError without a message")
        if "structuredContent" in result:
            return result["structuredContent"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self):
        """Close stdin and wait; returns the exit code (None when it had to be killed)."""
        code = None
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._stderr.close()
        return code

    def kill(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._stderr.close()
