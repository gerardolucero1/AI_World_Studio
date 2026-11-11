"""Live bridge between the desktop editor and agents: the editor serves ITS world (service.SESSION) on 127.0.0.1, and the MCP server
forwards tool calls there when an editor is open, so the user and the agent work on the same world and the editor shows the agent's
activity as it happens.

  editor:      BridgeServer().start()      -> writes ~/.ai_world_studio/editor.json {port, token, pid}
  MCP server:  editor_endpoint() / call_editor(tool, args)  (falls back to its own world when no editor is running)

Only localhost, and every request carries the random token from the lock file (another user's process can't drive the editor)."""
from __future__ import annotations

import collections
import inspect
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def lock_path() -> Path:
    """Where the running editor advertises its bridge ($AWS_EDITOR_LOCK overrides, e.g. in tests)."""
    return Path(os.environ.get("AWS_EDITOR_LOCK") or Path.home() / ".ai_world_studio" / "editor.json")


# ------------------------------------------------------------------------------------------------ activity log
class Activity:
    """Thread-safe log of tool calls made through the bridge (newest last)."""

    def __init__(self, limit: int = 500):
        self._lock = threading.Lock()
        self._items: collections.deque = collections.deque(maxlen=limit)
        self._seq = 0

    def start(self, tool: str, args: dict, source: str = "agent") -> dict:
        with self._lock:
            self._seq += 1
            e = {"seq": self._seq, "time": time.time(), "tool": tool, "args": args, "source": source, "status": "running",
                 "ok": None, "error": None, "ms": None, "warnings": [], "result": None}
            self._items.append(e)
            return e

    def finish(self, e: dict, result, ms: float) -> None:
        with self._lock:
            ok = not (isinstance(result, dict) and result.get("ok") is False)
            e.update(status="done" if ok else "failed", ok=ok, ms=ms, result=result,
                     error=(result.get("error") if isinstance(result, dict) else None),
                     warnings=[w.get("code", w) if isinstance(w, dict) else w for w in (result.get("warnings") or [])]
                     if isinstance(result, dict) else [])

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._items if e["seq"] > seq or e["status"] == "running"]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq


ACTIVITY = Activity()


# ------------------------------------------------------------------------------------------------ server (in the editor)
def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


class BridgeServer:
    def __init__(self, port: int = 0):
        self.token = secrets.token_hex(16)
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._busy = threading.Lock()            # one tool at a time: agent calls never interleave

    def start(self) -> int:
        from core.world import service as S
        by_name = {f.__name__: f for f in S.TOOLS}
        token, busy = self.token, self._busy

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):          # quiet
                pass

            def _send(self, code, obj):
                b = json.dumps(obj, default=str).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def _authorized(self) -> bool:
                if self.headers.get("X-AWS-Token") != token:
                    self._send(403, {"ok": False, "error": "bad token"}); return False
                return True

            def do_GET(self):
                if not self._authorized():
                    return
                if self.path == "/ping":
                    self._send(200, {"ok": True, "editor": True, "pid": os.getpid()})
                elif self.path == "/tools":
                    self._send(200, sorted(by_name))
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                if not self._authorized():
                    return
                if not self.path.startswith("/call/"):
                    return self._send(404, {"ok": False, "error": "not found"})
                name = self.path[len("/call/"):]
                fn = by_name.get(name)
                if fn is None:
                    return self._send(404, {"ok": False, "error": f"unknown tool {name!r}"})
                try:
                    args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
                    inspect.signature(fn).bind(**args)
                except (TypeError, ValueError) as e:
                    return self._send(422, {"ok": False, "error": f"bad arguments: {e}"})
                e = ACTIVITY.start(name, args)
                t0 = time.perf_counter()
                with busy:
                    try:
                        res = fn(**args)
                    except Exception as ex:  # noqa: BLE001  (tools return errors, but never let one kill the editor)
                        res = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
                res = _jsonable(res)
                ACTIVITY.finish(e, res, (time.perf_counter() - t0) * 1000)
                self._send(200, res)

        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, name="aws-bridge", daemon=True).start()
        lock = lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid(), "started": time.time()}))
        return self.port

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown(); self._srv.server_close(); self._srv = None
        try:
            lock = lock_path()
            if lock.exists() and json.loads(lock.read_text()).get("token") == self.token:
                lock.unlink()
        except (OSError, ValueError):
            pass


# ------------------------------------------------------------------------------------------------ client (in the MCP server)
def editor_endpoint(timeout: float = 0.5) -> dict | None:
    """{port, token} of a running editor, or None (no lock file, stale lock, or AWS_EDITOR=off)."""
    if os.environ.get("AWS_EDITOR", "").lower() in ("off", "0", "no"):
        return None
    try:
        info = json.loads(lock_path().read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/ping", headers={"X-AWS-Token": info["token"]})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return info if json.loads(r.read()).get("editor") else None
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return None


def call_editor(info: dict, tool: str, args: dict, timeout: float = 3600) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/call/{tool}", data=json.dumps(args, default=str).encode(),
                                 headers={"X-AWS-Token": info["token"], "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"ok": False, "error": f"editor bridge HTTP {e.code}"}
