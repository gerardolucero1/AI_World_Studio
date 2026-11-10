"""MCP server (stdio by default). Tools = the same semantic functions as the REST API.
Run: python -m mcp_server.server   (or add to an MCP client config)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import Image, MCPServer

from core.world import service

mcp = MCPServer("ai-world-studio", instructions=(
    "Build and inspect 3D landscapes. Units are meters; origin is the NW corner, x east, y south. "
    "Typical flow: create_world -> terrain shapes -> features -> erosion -> vegetation -> capture_world_view/validate_world -> fix -> save_world. "
    "Large operations auto-create checkpoints; use rollback_to_checkpoint if a result gets worse."))

import os

if os.environ.get("AWS_PROJECT"):                       # optional: start with a saved world loaded
    service.load_world(os.environ["AWS_PROJECT"])

for _f in service.TOOLS:
    mcp.tool()(_f)


@mcp.tool()
def view_image(path: str) -> Image:
    """Return a preview PNG previously produced by get_world_preview/capture_world_view so it can be seen."""
    p = Path(path).resolve()
    if p.suffix != ".png" or "previews" not in p.parts or not p.exists():
        raise ValueError("path must be a preview PNG returned by the capture tools")
    return Image(path=str(p))


if __name__ == "__main__":
    mcp.run("stdio")
