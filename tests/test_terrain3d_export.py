"""Terrain3D export: control-map encoding, region sizing, addon discovery, and (with Godot + the addon) a real bake/load/navigation check."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from core.world.session import WorldSession
from exporters.godot.exporter import ExportSettings, export_world, relocate_package, validate_export, verify_with_godot
from exporters.godot.godot_bin import GODOT
from exporters.godot.terrain3d import (MATERIALS, control_map, copy_addon, find_terrain3d_addon, material_textures,
                                       pick_region_size)


def _find_addon():
    """$AWS_TERRAIN3D_ADDON, or any Godot project of the user that has Terrain3D installed."""
    a = find_terrain3d_addon(explicit=os.environ.get("AWS_TERRAIN3D_ADDON"))
    if a:
        return a
    for root in (Path.home() / "Desktop/proyectos/godot", Path.home() / "Desktop/Proyectos/Godot"):
        for c in sorted(root.glob("*/addons/terrain_3d")) if root.exists() else []:
            if (c / "terrain.gdextension").exists():
                return c
    return None


ADDON = _find_addon()
needs_godot_t3d = pytest.mark.skipif(not (GODOT and ADDON), reason="needs Godot and the Terrain3D addon ($AWS_TERRAIN3D_ADDON)")


def small_world():
    from core.world import service as S
    S.SESSION = WorldSession()
    S.create_world("T", 256, 129)
    S.apply_terrain_noise(seed=2, amplitude=4, feature_size_m=120)
    S.create_mountain(128, 128, 70, 35, seed=1)
    S.create_lake([[40, 180], [90, 180], [90, 230], [40, 230]], depth=2)
    S.scatter_vegetation(["meadow_shrubs"], seed=1)
    return S.SESSION


def test_control_map_bits_follow_terrain3d_layout():
    w = np.zeros((2, 3, 8), np.float32)
    w[0, 0, MATERIALS.index("rock")] = 1.0                                   # pure rock
    w[0, 1, MATERIALS.index("grass")] = 0.75; w[0, 1, MATERIALS.index("snow")] = 0.25
    w[0, 2, MATERIALS.index("sand")] = 0.5; w[0, 2, MATERIALS.index("mud")] = 0.5
    w[1, :, MATERIALS.index("grass")] = 1.0
    water = np.zeros((2, 3), bool); water[1, 2] = True
    c = control_map(w, water)
    base, over, blend = c >> 27 & 0x1F, c >> 22 & 0x1F, (c >> 14 & 0xFF) / 255.0
    nav = c >> 1 & 1
    assert base[0, 0] == MATERIALS.index("rock") and blend[0, 0] == 0
    assert base[0, 1] == MATERIALS.index("grass") and over[0, 1] == MATERIALS.index("snow") and abs(blend[0, 1] - 0.25) < 0.01
    assert {int(base[0, 2]), int(over[0, 2])} == {MATERIALS.index("sand"), MATERIALS.index("mud")} and abs(blend[0, 2] - 0.5) < 0.01
    assert nav[1, 2] == 0 and nav[0, 0] == 1 and (c & 1).max() == 0 and (c >> 2 & 1).max() == 0    # no autoshader, no holes
    # host texture slots: several of our materials can share one slot of the game's texture list
    c2 = control_map(w, None, {"grass": 3, "snow": 3, "rock": 5, "sand": 0, "mud": 0, "dirt": 0, "wet_rock": 5, "gravel": 5})
    assert c2[0, 1] >> 27 & 0x1F == 3 and (c2[0, 1] >> 14 & 0xFF) == 0              # grass+snow merged -> one slot, no blend
    assert c2[0, 0] >> 27 & 0x1F == 5
    with pytest.raises(ValueError):
        control_map(w, None, {"grass": 40})


def test_region_size_fits_region_map_and_minimises_padding():
    assert pick_region_size(129, 129) == 64            # 3x3 regions of 64 pad less than 1 region of 256
    rs = pick_region_size(1025, 1025)
    assert -(-1025 // rs) <= 16
    assert pick_region_size(4097, 4097) >= 512
    with pytest.raises(ValueError):
        pick_region_size(40000, 40000)


def test_material_textures_are_tileable_rgba8():
    t = material_textures(64)
    assert [x["name"] for x in t] == MATERIALS
    for x in t:
        assert x["albedo"].shape == (64, 64, 4) and x["albedo"].dtype == np.uint8 and x["normal"].shape == (64, 64, 4)
        a = x["albedo"].astype(int)
        assert np.abs(a[0] - a[-1]).mean() < 25 and np.abs(a[:, 0] - a[:, -1]).mean() < 25      # wraps without a visible seam
        assert x["normal"][..., 2].min() > 128                                                  # normals point out of the surface


def test_terrain3d_mode_fails_clearly_without_addon(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_TERRAIN3D_ADDON", raising=False)
    with pytest.raises(ValueError, match="Terrain3D addon"):
        export_world(small_world(), tmp_path / "pkg", ExportSettings(terrain="terrain3d", terrain3d_addon=str(tmp_path / "nope"), navigation="none"))
    assert not (tmp_path / "pkg").exists()              # nothing written


def test_relocation_leaves_a_bundled_addon_alone(tmp_path):
    pkg = tmp_path / "pkg"; (pkg / "WorldData").mkdir(parents=True); (pkg / "addons/terrain_3d").mkdir(parents=True)
    (pkg / "WorldData/manifest.json").write_text(json.dumps({"res_prefix": "res://"}))
    (pkg / "World.tscn").write_text('[ext_resource path="res://Terrain3D/assets.tres"]\ndata_directory = "res://Terrain3D/data"\n')
    (pkg / "addons/terrain_3d/x.tscn").write_text('[ext_resource path="res://addons/terrain_3d/a.gd"]')
    relocate_package(pkg, "res://worlds/a/")
    assert '"res://worlds/a/Terrain3D/data"' in (pkg / "World.tscn").read_text()
    assert '"res://addons/terrain_3d/a.gd"' in (pkg / "addons/terrain_3d/x.tscn").read_text()


@needs_godot_t3d
def test_terrain3d_export_inside_a_host_project_bakes_loads_and_navigates(tmp_path):
    """Host project with Terrain3D installed -> export into a subfolder -> regions match the heightfield exactly, Godot loads them with collision,
    navigation is baked from the Terrain3D geometry, and the host project is never opened."""
    host = tmp_path / "game"; host.mkdir()
    (host / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Game with Terrain3D"\n')
    copy_addon(ADDON, host)
    assert find_terrain3d_addon(host / "worlds/w1") == host / "addons/terrain_3d"      # discovered from the host project
    sess = small_world()
    from core.world import service as S
    r = S.export_to_godot(godot_project=str(host), subfolder="worlds/w1", terrain="terrain3d", navigation="bake", verify=True)
    assert r["ok"] and r["validation"]["ok"] and r["terrain"] == "terrain3d", r
    out = host / "worlds/w1"
    mf = json.loads((out / "WorldData/manifest.json").read_text())
    t = mf["terrain"]
    assert t["mode"] == "terrain3d" and t["region_size"] == 64 and len(t["regions"]) == 9 and t["addon_version"]
    assert not list((out / "Terrain").glob("*.glb"))                                  # no mesh chunks in this mode
    scene = (out / "World.tscn").read_text()
    assert 'type="Terrain3D"' in scene and 'data_directory = "res://worlds/w1/Terrain3D/data"' in scene
    assert 'path="res://worlds/w1/Terrain3D/textures/' in (out / "Terrain3D/assets.tres").read_text()
    v = r["godot_verification"]
    assert v["ok"], v
    assert v["terrain3d"]["regions"] == 9 and v["terrain3d"]["texture_arrays_ready"] and v["terrain3d"]["collision_mode"] == 3
    assert v["ground_under_start"] and abs(v["ground_gap_m"] - 1.0) < 0.3              # PlayerStart sits 1 m above the Terrain3D collision
    assert mf["navigation"]["ok"] and v["nav_polygons"] > 50
    assert not (host / ".godot").exists()                                              # the host project was never opened by Godot
    assert sess.hf.nx == 129


def test_extract_material_from_a_scene_keeps_host_paths(tmp_path):
    from exporters.godot.terrain3d import extract_material
    sc = tmp_path / "level.tscn"
    sc.write_text('[gd_scene format=3 uid="uid://x"]\n\n[ext_resource type="Shader" uid="uid://s" path="res://shaders/toon.gdshader" id="1_s"]\n'
                  '[ext_resource type="Texture2D" path="res://tex/noise.png" id="2_t"]\n[ext_resource type="Script" path="res://p.gd" id="3_p"]\n\n'
                  '[sub_resource type="Gradient" id="G"]\n\n[sub_resource type="GradientTexture1D" id="GT"]\ngradient = SubResource("G")\n\n'
                  '[sub_resource type="Terrain3DMaterial" id="M"]\n_shader_parameters = {\n&"noise": ExtResource("2_t"),\n&"ramp": SubResource("GT")\n}\n'
                  'shader_override_enabled = true\nshader_override = ExtResource("1_s")\n\n[node name="L" type="Node3D"]\nscript = ExtResource("3_p")\n')
    t = extract_material(sc)
    assert t.startswith('[gd_resource type="Terrain3DMaterial"') and 'path="res://shaders/toon.gdshader"' in t and "uid=" not in t
    assert "p.gd" not in t and '[sub_resource type="Gradient" id="G"]' in t and t.index('id="G"]') < t.index('id="GT"]') < t.index("[resource]")
    assert "shader_override = ExtResource(\"1_s\")" in t.split("[resource]")[1]


@needs_godot_t3d
def test_terrain3d_export_with_the_games_own_material(tmp_path):
    """The game keeps its Terrain3DMaterial inside a level scene with a shader override: the package copies that material, keeps pointing to the
    game's shader, maps our materials to the game's texture ids, and the temp-copy verification brings the shader along."""
    import shutil
    host = tmp_path / "game"; (host / "shaders").mkdir(parents=True)
    (host / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Game"\n')
    copy_addon(ADDON, host)
    shutil.copy(ADDON / "extras/shaders/minimum.gdshader", host / "shaders/terrain_toon.gdshader")
    (host / "level.tscn").write_text('[gd_scene format=3]\n\n[ext_resource type="Shader" path="res://shaders/terrain_toon.gdshader" id="1_s"]\n\n'
                                     '[sub_resource type="Terrain3DMaterial" id="M"]\nshader_override_enabled = true\nshader_override = ExtResource("1_s")\n'
                                     'world_background = 1\n\n[node name="Level" type="Node3D"]\n')
    small_world()
    from core.world import service as S
    ids = {m: 0 for m in MATERIALS}; ids["dirt"] = 1
    r = S.export_to_godot(godot_project=str(host), subfolder="generated_worlds/w", terrain="terrain3d", navigation="none", verify=True,
                          terrain3d_material="res://level.tscn", terrain3d_texture_ids=ids)
    assert r["ok"] and r["validation"]["ok"], r
    mat = (host / "generated_worlds/w/Terrain3D/material.tres").read_text()
    assert 'path="res://shaders/terrain_toon.gdshader"' in mat and "world_background = 0" in mat and "world_background = 1" not in mat
    mf = json.loads((host / "generated_worlds/w/WorldData/manifest.json").read_text())
    assert mf["terrain"]["material_source"] == "res://level.tscn" and mf["terrain"]["texture_ids"]["dirt"] == 1
    assert r["godot_verification"]["ok"], r["godot_verification"]
    assert not (host / ".godot").exists()
