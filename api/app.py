"""REST API: exposes the same semantic tools as the MCP server. Run: uvicorn api.app:app"""
from __future__ import annotations

import inspect
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

from core.world import service

app = FastAPI(title="AI World Studio API", version="0.1")
_BY_NAME = {f.__name__: f for f in service.TOOLS}


@app.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    return [{"name": n, "doc": inspect.getdoc(f), "signature": str(inspect.signature(f))} for n, f in _BY_NAME.items()]


@app.post("/tools/{name}")
def call_tool(name: str, args: dict[str, Any] = Body(default_factory=dict)) -> dict:
    fn = _BY_NAME.get(name)
    if fn is None:
        raise HTTPException(404, f"unknown tool {name!r}")
    try:
        inspect.signature(fn).bind(**args)
    except TypeError as e:
        raise HTTPException(422, str(e))
    return fn(**args)


@app.get("/preview")
def preview_file(path: str):
    from pathlib import Path
    p = Path(path).resolve()
    if p.suffix != ".png" or not p.exists() or "previews" not in p.parts:
        raise HTTPException(404)
    return FileResponse(p)
