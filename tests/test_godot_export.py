import json
import struct
from pathlib import Path

import numpy as np
import pytest

from core.world.session import WorldSession
from exporters.godot.exporter import ExportSettings, export_world, validate_export, verify_with_godot
from exporters.godot.glb import write_glb
from exporters.godot.terrain_lod import chunk_mesh, lod_steps_for, pick_chunk_cells
from terrain.heightfield.heightfield import Heightfield

GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"


def world(res=129, size=256):
    from core.world import service as S
    S.SESSION = WorldSession()
    S.create_world("W", size, res)
    S.create_mountain(size / 2, size / 2, size * 0.3, 30, seed=1)
    S.create_cliff([[size * .3, size * .3], [size * .45, size * .28], [size * .47, size * .45], [size * .32, size * .46]], 40)
    S.create_river([[10, 40], [size / 2, 60], [size - 10, 50]], width=6, depth=2)
    S.create_lake([[size * .7, size * .7], [size * .85, size * .7], [size * .85, size * .85], [size * .7, size * .85]], depth=2)
    S.scatter_vegetation(["conifer_forest", "meadow_shrubs"], seed=1)
    return S.SESSION


def read_glb(path):
    b = Path(path).read_bytes()
    magic, ver, total = struct.unpack("<4sII", b[:12])
    n = struct.unpack("<I", b[12:16])[0]
    return magic, ver, total, json.loads(b[20:20 + n]), len(b)


def test_glb_valid_header_and_accessors(tmp_path):
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], np.float32)
    size = write_glb(tmp_path / "t.glb", [dict(name="A", positions=pos, normals=np.tile([0, 1, 0], (3, 1)), uvs=pos[:, [0, 2]],
                                               indices=[0, 2, 1], material="M"),
                                          dict(name="A-colonly", positions=pos, normals=None, uvs=None, indices=[0, 2, 1])])
    magic, ver, total, js, flen = read_glb(tmp_path / "t.glb")
    assert magic == b"glTF" and ver == 2 and total == flen == size
    assert [n["name"] for n in js["nodes"]] == ["A", "A-colonly"] and js["accessors"][0]["max"] == [1, 0, 1]
    assert js["buffers"][0]["byteLength"] % 4 == 0


def test_chunk_geometry_orientation_seams_and_lod():
    h = Heightfield(129, 129, 2.0); h.data[:] = np.random.default_rng(0).random(h.data.shape) * 5
    p, n, uv, idx = chunk_mesh(h, 0, 0, 64, 1, skirt=False)
    f = idx.reshape(-1, 3); fn = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
    assert (fn[:, 1] > 0).all() and uv.min() >= 0 and uv.max() <= 1
    a = chunk_mesh(h, 0, 0, 64, 1, False)[0].reshape(65, 65, 3); b = chunk_mesh(h, 1, 0, 64, 1, False)[0].reshape(65, 65, 3)
    np.testing.assert_allclose(a[:, -1], b[:, 0])                                   # shared border vertices
    lo = chunk_mesh(h, 0, 0, 64, 4, False)[0].reshape(17, 17, 3); np.testing.assert_allclose(lo[:, -1], a[::4, -1])   # LOD border matches
    ps, _, _, idxs = chunk_mesh(h, 0, 0, 64, 4, True)
    assert len(ps) == 17 * 17 + 4 * 17 and idxs.max() < len(ps)
    sk = idxs.reshape(-1, 3)[16 * 16 * 2:]; sfn = np.cross(ps[sk[:, 1]] - ps[sk[:, 0]], ps[sk[:, 2]] - ps[sk[:, 0]])
    cen = np.array([64, 64])                                                         # skirt faces must point away from the chunk centre
    assert (np.einsum("ij,ij->i", sfn[:, [0, 2]], ps[sk].mean(1)[:, [0, 2]] - cen) > -1e-6).all()


def test_chunk_and_lod_rules():
    assert pick_chunk_cells(Heightfield(513, 513, 1)) == 64 and pick_chunk_cells(Heightfield(129, 129, 1)) == 64
    assert pick_chunk_cells(Heightfield(97, 97, 1)) == 32
    with pytest.raises(ValueError):
        pick_chunk_cells(Heightfield(102, 102, 1))
    assert lod_steps_for(64) == [1, 2, 4, 8] and lod_steps_for(8) == [1, 2]


def test_export_package_structure_manifest_validation(tmp_path):
    sess = world()
    r = export_world(sess, tmp_path / "GW")
    out = tmp_path / "GW"
    assert r["ok"] and r["validation"]["ok"] and r["chunks"] == 4
    for f in ["World.tscn", "project.godot", "WorldData/manifest.json", "Materials/terrain.tres", "Materials/terrain.gdshader",
              "Textures/splat0.png", "Textures/splat1.png", "Textures/splat2.png", "Textures/heightmap16.png",
              "Terrain/Chunk_00_00.glb", "Terrain/Chunk_01_01.glb", "Features/Lakes/Lake_0.glb", "Features/Rivers/River_0.glb", "Geometry/Cliff_0.glb"]:
        assert (out / f).exists(), f
    mf = json.loads((out / "WorldData/manifest.json").read_text())
    assert mf["godot_version"] == "4.7.2" and mf["chunks"]["grid"] == [2, 2] and mf["chunks"]["lod_steps"] == [1, 2, 4, 8]
    assert mf["vegetation"]["total_instances"] > 100 and all(v["sha1"] for v in mf["files"].values())
    js = read_glb(out / "Terrain/Chunk_00_00.glb")[3]
    assert [n["name"] for n in js["nodes"]] == ["Terrain_LOD0", "Terrain_LOD1", "Terrain_LOD2", "Terrain_LOD3", "Collision-colonly"]
    assert 'mesh = SubResource' in (out / "World.tscn").read_text() and "MultiMesh" in (out / "World.tscn").read_text()
    from PIL import Image
    assert Image.open(out / "Textures/heightmap16.png").mode in ("I;16", "I")


def test_vegetation_instances_match_manifest_and_world_bounds(tmp_path):
    sess = world(); r = export_world(sess, tmp_path / "GW", ExportSettings(grass=False))     # grass patches are counted separately
    txt = (tmp_path / "GW/World.tscn").read_text()
    import re
    n = sum(int(m) for m in re.findall(r"instance_count = (\d+)", txt))
    assert n == r["vegetation_instances"] == sum(len(o.result["_instances"]) for o in sess.op_log if o.result and "_instances" in o.result)


def test_validation_detects_broken_package(tmp_path):
    sess = world(); export_world(sess, tmp_path / "GW")
    (tmp_path / "GW/Terrain/Chunk_00_01.glb").unlink()
    (tmp_path / "GW/Textures/splat1.png").unlink()
    codes = {f["code"] for f in validate_export(tmp_path / "GW")["findings"]}
    assert {"missing_chunk", "missing_texture", "broken_reference"} <= codes


def test_refuses_to_overwrite_foreign_folder_and_reexport_ok(tmp_path):
    sess = world()
    foreign = tmp_path / "mine"; foreign.mkdir(); (foreign / "important.txt").write_text("x")
    with pytest.raises(ValueError, match="refusing"):
        export_world(sess, foreign)
    assert (foreign / "important.txt").exists()
    export_world(sess, tmp_path / "GW"); r = export_world(sess, tmp_path / "GW")       # re-export over previous export
    assert r["ok"]


def test_res_prefix_for_embedding(tmp_path):
    sess = world(); r = export_world(sess, tmp_path / "GW", ExportSettings(res_prefix="res://GodotWorld/"))
    assert r["validation"]["ok"] and 'path="res://GodotWorld/Terrain/Chunk_00_00.glb"' in (tmp_path / "GW/World.tscn").read_text()


def test_agent_tools():
    from core.world import service as S
    world()
    p = S.prepare_godot_export(); assert p["ok"] and p["chunk_grid"] == [2, 2] and p["target"] == "Godot 4.7.2"
    S.create_world("odd", 100, 102)
    assert S.prepare_godot_export()["ok"] is False


@pytest.mark.skipif(not Path(GODOT).exists(), reason="Godot not installed")
def test_real_godot_imports_and_instantiates(tmp_path):
    sess = world(); r = export_world(sess, tmp_path / "GW", ExportSettings(grass=False))
    v = verify_with_godot(tmp_path / "GW")
    assert v["ok"], v
    assert v["classes"]["MeshInstance3D"] == 4 * 4 + 3 and v["static_bodies"] == 4 + 1 and v["collision_shapes"] == 5
    assert v["multimesh_instances"] == r["vegetation_instances"] and v["material_overrides"] == v["classes"]["MeshInstance3D"]


def test_world_scene_is_drop_in_and_preview_is_separate(tmp_path):
    sess = world(); export_world(sess, tmp_path / "GW")
    w = (tmp_path / "GW/World.tscn").read_text(); pv = (tmp_path / "GW/Preview.tscn").read_text()
    assert "DirectionalLight3D" not in w and "WorldEnvironment" not in w and "Camera3D" not in w      # no clashes with the host game
    assert "Marker3D" in w and 'name="PlayerStart"' in w and 'type="Path3D"' in w and "Curve3D" in w
    assert "DirectionalLight3D" in pv and "WorldEnvironment" in pv and 'instance=ExtResource("world")' in pv
    r = export_world(sess, tmp_path / "GW2", ExportSettings(include_preview=False)); assert not (tmp_path / "GW2/Preview.tscn").exists() and r["validation"]["ok"]
    mf = json.loads((tmp_path / "GW/WorldData/manifest.json").read_text())
    assert mf["markers"]["trails"] >= 0 and "player_start" in mf["markers"] and (tmp_path / "GW/Textures/heightmap.r16").stat().st_size == 129 * 129 * 2


def test_compatible_profile_uses_depthless_water(tmp_path):
    sess = world(); export_world(sess, tmp_path / "GW", ExportSettings(profile="compatible"))
    assert "water_simple.gdshader" in (tmp_path / "GW/Materials/water.tres").read_text()
    assert "glow_enabled" not in (tmp_path / "GW/Environment/stylized_environment.tres").read_text()
    assert "hint_depth_texture" not in (tmp_path / "GW/Materials/water_simple.gdshader").read_text()


def test_relocate_package_rewrites_prefix(tmp_path):
    from exporters.godot.exporter import relocate_package
    sess = world(); export_world(sess, tmp_path / "GW")
    r = relocate_package(tmp_path / "GW", "res://addons/my_world/")
    assert r["ok"] and r["files_rewritten"] > 3
    assert 'path="res://addons/my_world/Terrain/Chunk_00_00.glb"' in (tmp_path / "GW/World.tscn").read_text()
    assert 'path="res://Terrain' not in (tmp_path / "GW/World.tscn").read_text()
    assert 'res://addons/my_world/Materials/common.gdshaderinc' in (tmp_path / "GW/Materials/terrain.gdshader").read_text()
    assert validate_export(tmp_path / "GW")["ok"]
    relocate_package(tmp_path / "GW", "res://x/"); assert validate_export(tmp_path / "GW")["ok"]            # relocatable repeatedly
    with pytest.raises(ValueError):
        relocate_package(tmp_path / "GW", "addons/x")


@pytest.mark.skipif(not Path(GODOT).exists(), reason="Godot not installed")
def test_package_works_inside_a_foreign_godot_project_after_relocation(tmp_path):
    """The real use case: drop the generated world into someone else's project, in any folder."""
    from exporters.godot.exporter import relocate_package
    host = tmp_path / "somebody_elses_game"; host.mkdir()
    (host / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Other game"\nconfig/features=PackedStringArray("4.7")\n'
                                        '[rendering]\nrenderer/rendering_method="mobile"\n')
    (host / "main.tscn").write_text('[gd_scene format=3]\n[node name="Main" type="Node3D"]\n')
    sess = world(); export_world(sess, tmp_path / "pkg", ExportSettings(write_project_file=False))
    dest = host / "addons/generated_world"; dest.parent.mkdir(parents=True); (tmp_path / "pkg").rename(dest)
    relocate_package(dest, "res://addons/generated_world/")
    v = verify_with_godot(dest, project_dir=host)
    assert v["ok"], v
    assert v["classes"].get("Path3D", 0) >= 1 and v["markers"] == 1 and v["paths_ok"] >= 1
    assert v["classes"]["MeshInstance3D"] == 4 * 4 + 3 and v["multimesh_instances"] > 100
    assert "DirectionalLight3D" not in v["classes"] and "Camera3D" not in v["classes"]
    assert (host / "project.godot").read_text().count("Other game") == 1           # host untouched


@pytest.mark.skipif(not Path(GODOT).exists(), reason="Godot not installed")
def test_climbable_mountain_is_walkable_to_the_summit_in_real_godot(tmp_path):
    """End to end: generate -> export with a baked NavigationMesh -> Godot's own navigation server finds a walking route from the spawn to the summit."""
    from core.world import service as S
    S.SESSION = WorldSession()
    S.create_world("cm", 768, 385)
    S.apply_terrain_noise(seed=2, amplitude=5, feature_size_m=300)
    r = S.create_climbable_mountain(384, 384, 230, 150, grade=0.22, width=6)
    assert r["ok"] and r["ramp"]["turn_spacing_m"] > 60
    e = S.export_to_godot(path=str(tmp_path / "pkg"), navigation="bake")
    assert e["ok"] and e["validation"]["ok"], e
    mf = json.loads((tmp_path / "pkg/WorldData/manifest.json").read_text())
    assert mf["navigation"]["ok"] and mf["markers"]["summit"][1] > 100 and (tmp_path / "pkg/Navigation/navmesh.res").exists()
    v = verify_with_godot(tmp_path / "pkg")
    assert v["ok"], v
    assert v["nav_reaches_summit"] and v["nav_path_length_m"] > 400 and v["nav_goal_gap_m"] < 15


def test_navigation_modes(tmp_path):
    sess = world()
    r = export_world(sess, tmp_path / "py", ExportSettings(navigation="python", nav_cell_m=8))
    assert r["validation"]["ok"] and (tmp_path / "py/Navigation/navmesh.tres").exists() and 'type="NavigationRegion3D"' in (tmp_path / "py/World.tscn").read_text()
    r = export_world(sess, tmp_path / "none", ExportSettings(navigation="none"))
    assert not (tmp_path / "none/Navigation").exists() and "NavigationRegion3D" not in (tmp_path / "none/World.tscn").read_text()
    with pytest.raises(ValueError, match="Godot binary"):
        export_world(sess, tmp_path / "bake", ExportSettings(navigation="bake", godot_binary="/nonexistent/godot", write_project_file=False))


@pytest.mark.skipif(not Path(GODOT).exists(), reason="Godot not installed")
@pytest.mark.parametrize("size,seed", [(1024, 3), (1024, 4)])
def test_generated_landscape_is_walkable_spawn_to_summit_in_real_godot(tmp_path, size, seed):
    """The product promise: a generated highland world has a spawn point and a climbable summit that Godot's navigation can actually connect."""
    from core.world import service as S
    S.SESSION = WorldSession()
    g = S.generate_landscape("highland_lake", size, seed); assert g["ok"]
    e = S.export_to_godot(path=str(tmp_path / "pkg"), navigation="bake"); assert e["ok"] and e["validation"]["ok"]
    v = verify_with_godot(tmp_path / "pkg")
    assert v["ok"], v
    assert v["nav_reaches_summit"], {k: v.get(k) for k in ("nav_end_gap_m", "nav_goal_gap_m", "nav_path_length_m")}


def test_grass_is_gpu_placed_lossless_and_shared(tmp_path):
    import base64
    import re
    sess = world(); r = export_world(sess, tmp_path / "GW")
    mf = json.loads((tmp_path / "GW/WorldData/manifest.json").read_text())
    assert r["validation"]["ok"] and mf["grass"]["chunks_with_grass"] >= 1 and mf["grass"]["tufts_per_chunk"] > 1000
    tscn = (tmp_path / "GW/World.tscn").read_text()
    assert tscn.count("[sub_resource type=\"MultiMesh\" id=\"grass_patch_") == 3                      # 3 shared patches, not one per chunk
    assert tscn.count("MultiMeshInstance3D") >= mf["grass"]["chunks_with_grass"] and "custom_aabb" in tscn and "cast_shadow = 0" in tscn
    # the height texture round-trips exactly (16-bit heights split in two bytes)
    txt = (tmp_path / "GW/Textures/height_rg.tres").read_text()
    raw = np.frombuffer(base64.b64decode(re.search(r'PackedByteArray\("([^"]+)"\)', txt).group(1)), np.uint8).reshape(129, 129, 2)
    h16 = (raw[..., 0].astype(np.uint16) << 8) | raw[..., 1]
    ref = np.fromfile(tmp_path / "GW/Textures/heightmap.r16", "<u2").reshape(129, 129)
    assert np.array_equal(h16, ref)
    # water mask is in the blue channel of splat2
    from PIL import Image
    assert np.asarray(Image.open(tmp_path / "GW/Textures/splat2.png"))[..., 2].max() == 255
    r2 = export_world(sess, tmp_path / "NoGrass", ExportSettings(grass=False))
    assert "Grass" not in (tmp_path / "NoGrass/World.tscn").read_text() and r2["validation"]["ok"]


@pytest.mark.skipif(not Path(GODOT).exists(), reason="Godot not installed")
def test_grass_loads_in_real_godot(tmp_path):
    sess = world(); export_world(sess, tmp_path / "GW")
    v = verify_with_godot(tmp_path / "GW")
    assert v["ok"], v
    mm = v["classes"].get("MultiMeshInstance3D", 0)
    assert mm >= 1 + json.loads((tmp_path / "GW/WorldData/manifest.json").read_text())["grass"]["chunks_with_grass"]
