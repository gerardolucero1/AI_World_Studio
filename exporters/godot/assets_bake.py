"""Bake the procedural prototypes into Godot ArrayMesh .res files (needs the Godot editor binary once).
Run:  python -m exporters.godot.assets_bake        Output: exporters/godot/library/*.res (committed, copied on export)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from exporters.godot.glb import write_glb
from exporters.godot.procedural_assets import PROTOTYPES

LIB = Path(__file__).parent / "library"
GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"
BAKE_GD = """extends SceneTree
func _initialize():
	var dir = DirAccess.open("res://src")
	var n = 0
	for f in dir.get_files():
		if not f.ends_with(".glb"): continue
		var scene = load("res://src/" + f).instantiate()
		var mi = null
		var stack = [scene]
		while stack.size() > 0 and mi == null:
			var x = stack.pop_back()
			if x is MeshInstance3D: mi = x
			for c in x.get_children(): stack.append(c)
		if mi == null:
			printerr("NO MESH in ", f); continue
		var err = ResourceSaver.save(mi.mesh, "res://out/" + f.get_basename() + ".res")
		if err != OK: printerr("SAVE FAILED ", f, " ", err)
		else: n += 1
	print("BAKED ", n)
	quit(0)
"""


def bake(godot: str = GODOT) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="aws_bake_") as td:
        t = Path(td)
        (t / "src").mkdir(); (t / "out").mkdir()
        (t / "project.godot").write_text('config_version=5\n[application]\nconfig/name="bake"\n')
        for name, fn in PROTOTYPES.items():
            m = fn()
            write_glb(t / "src" / f"{name}.glb", [dict(name=name, positions=m["positions"], normals=m["normals"], uvs=None,
                                                       colors=m["colors"], indices=m["indices"])])
        subprocess.run([godot, "--headless", "--path", str(t), "--import"], capture_output=True, text=True, timeout=600)
        (t / "bake.gd").write_text(BAKE_GD)
        r = subprocess.run([godot, "--headless", "--path", str(t), "--script", "res://bake.gd"], capture_output=True, text=True, timeout=600)
        if "BAKED" not in r.stdout:
            raise RuntimeError("bake failed:\n" + r.stdout[-1500:] + r.stderr[-1500:])
        LIB.mkdir(exist_ok=True)
        done = []
        for f in sorted((t / "out").glob("*.res")):
            shutil.copy(f, LIB / f.name); done.append(f.name)
        return done


if __name__ == "__main__":
    print(bake())
