import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run_cli(*args):
    return subprocess.run([PY, str(ROOT / "awstudio.py"), *args], capture_output=True, text=True, cwd=ROOT, timeout=600)


def test_cli_generate_exports_into_existing_godot_project(tmp_path):
    proj = tmp_path / "game"; proj.mkdir(); (proj / "project.godot").write_text("config_version=5\n")
    r = run_cli("generate", "highland_lake", "--size", "768", "--seed", "2", "--save", str(tmp_path / "w.awp"), "--godot-project", str(proj))
    assert r.returncode == 0, r.stdout + r.stderr
    out = proj / "GodotWorld"
    assert (out / "World.tscn").exists() and (out / "WorldData/manifest.json").exists() and (tmp_path / "w.awp/project.json").exists()
    assert 'path="res://GodotWorld/Terrain/Chunk_00_00.glb"' in (out / "World.tscn").read_text()
    assert (proj / "project.godot").read_text() == "config_version=5\n" and not (out / "project.godot").exists()   # host project untouched
    assert json.loads((out / "WorldData/manifest.json").read_text())["validation"]["ok"]


def test_cli_export_from_saved_world_and_call(tmp_path):
    assert run_cli("call", "create_world", '{"size_m":256,"resolution":129}', "--save", str(tmp_path / "a.awp")).returncode == 0
    r = run_cli("call", "create_mountain", '{"x":128,"y":128,"radius":70,"height":30}', "--world", str(tmp_path / "a.awp"), "--save", str(tmp_path / "b.awp"))
    assert r.returncode == 0 and '"ok": true' in r.stdout
    e = run_cli("export", str(tmp_path / "b.awp"), "--out", str(tmp_path / "standalone"))
    assert e.returncode == 0 and (tmp_path / "standalone/project.godot").exists()
    assert run_cli("call", "nope").returncode == 2


def test_export_refuses_non_godot_project_folder(tmp_path):
    from core.world import service as S
    S.create_world("x", 256, 129)
    assert S.export_to_godot(godot_project=str(tmp_path))["ok"] is False
    assert S.export_to_godot()["ok"] is False


def test_mcp_server_over_stdio(tmp_path):
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp import ClientSession

    async def go():
        params = StdioServerParameters(command=PY, args=["-m", "mcp_server.server"], cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = {t.name for t in tools.tools}
                assert {"get_capabilities", "generate_landscape", "create_trail", "export_to_godot", "capture_world_view", "view_image"} <= names
                cap = await s.call_tool("get_capabilities", {})
                assert "highland_lake" in cap.content[0].text
                g = await s.call_tool("generate_landscape", {"preset": "highland_lake", "size_m": 768, "seed": 1})
                assert json.loads(g.content[0].text)["ok"] is True
                v = await s.call_tool("capture_world_view", {"view": "aerial", "width": 160, "height": 100})
                path = json.loads(v.content[0].text)["path"]
                img = await s.call_tool("view_image", {"path": path})
                assert img.content[0].type == "image"
    asyncio.run(go())


def test_mcp_config_points_to_existing_python():
    cfg = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["ai-world-studio"]
    assert (ROOT / cfg["command"]).exists() and cfg["args"] == ["-m", "mcp_server.server"]
