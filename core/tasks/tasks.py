"""Async task manager: queued -> running -> completed | failed | cancelled."""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class TaskManager:
    def __init__(self, workers: int = 1):
        self._pool = ThreadPoolExecutor(workers)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, label: str, fn: Callable[[], Any]) -> str:
        tid = f"task-{next(self._ids)}"
        rec = {"id": tid, "label": label, "status": "queued", "result": None, "error": None, "created": time.time()}
        with self._lock:
            self._tasks[tid] = rec

        def runner():
            with self._lock:
                if rec["status"] == "cancelled":
                    return
                rec["status"] = "running"
            try:
                res = fn()
                with self._lock:
                    rec["result"], rec["status"] = res, "completed"
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    rec["error"], rec["status"] = f"{type(e).__name__}: {e}", "failed"
                    rec["trace"] = traceback.format_exc()

        rec["future"] = self._pool.submit(runner)
        return tid

    def status(self, tid: str) -> dict[str, Any]:
        with self._lock:
            r = self._tasks.get(tid)
            if r is None:
                raise KeyError(f"unknown task {tid}")
            return {k: v for k, v in r.items() if k not in ("future", "trace")}

    def cancel(self, tid: str) -> bool:
        """Only queued tasks can be cancelled (running numpy work is not interruptible)."""
        with self._lock:
            r = self._tasks[tid]
            if r["status"] == "queued":
                r["status"] = "cancelled"
                return True
            return False

    def wait(self, tid: str, timeout: float | None = None) -> dict[str, Any]:
        f: Future = self._tasks[tid]["future"]
        try:
            f.result(timeout)
        except Exception:  # noqa: BLE001
            pass
        return self.status(tid)
