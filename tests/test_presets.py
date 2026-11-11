import numpy as np
import pytest

from core.world import service as S
from core.world.session import WorldSession


@pytest.fixture(autouse=True)
def fresh():
    S.SESSION = WorldSession()
    yield


def test_highland_lake_generates_valid_world():
    r = S.generate_landscape("highland_lake", 768, seed=3)
    assert r["ok"], r
    assert S.get_world_info()["size_m"] == [768.0, 768.0]
    v = S.validate_world(); assert v["counts"]["error"] == 0
    kinds = [o.kind for o in S.SESSION.op_log]
    assert {"bowl", "terrace", "border_mountains", "lake", "path", "scatter_vegetation"} <= set(kinds)
    assert r["traversability"]["summit_reachable"] and r["traversability"]["summit"]["route_length_m"] > 100     # the hero mountain can be climbed on foot
    assert r["traversability"]["reachable_fraction"] > 0.15
    hf = S.SESSION.hf
    from biomes.masks.masks import slope_degrees
    sl = slope_degrees(hf); assert (sl > 60).mean() > 0.01 and (sl < 10).mean() > 0.2     # real cliffs AND walkable meadows


def test_generation_is_deterministic():
    a = S.generate_landscape("highland_lake", 768, 5); h1 = S.SESSION.hf.data.copy()
    S.generate_landscape("highland_lake", 768, 5); assert np.array_equal(h1, S.SESSION.hf.data)
    S.generate_landscape("highland_lake", 768, 6); assert not np.array_equal(h1, S.SESSION.hf.data)


def test_preset_errors_and_border_has_no_diagonal_crease():
    assert S.generate_landscape("nope")["ok"] is False and S.generate_landscape("highland_lake", 100)["ok"] is False
    S.create_world("t", 512, 257); S.create_border_mountains(width=150, height=100, roughness=0.0, seed=1)
    d = S.SESSION.hf.data
    # rounded corners: height on the corner diagonal must not exceed the height at the middle of an edge by much
    assert d[10, 10] <= d[10, 128] * 1.35 + 1


def test_style_snow_line_roundtrip(tmp_path):
    S.create_world("t", 256, 129); S.set_world_style(snow_line=123.0, rock_slope_min=30, rock_slope_max=50)
    S.save_world(str(tmp_path / "w.awp")); S.create_world("o", 256, 129)
    S.load_world(str(tmp_path / "w.awp")); assert S.SESSION.hf.style["snow_line"] == 123.0 and tuple(S.SESSION.hf.style["rock_slope"]) == (30, 50)


@pytest.mark.parametrize("seed", [1, 3, 4, 7])
def test_highland_lake_succeeds_for_several_seeds(seed):
    """Seed 4 used to fail twice: the river aimed at the lake centre (the island sits there; a river can't climb onto it) and the tier trail
    found no route at the first try. Now the river aims at the near shore and both retry along the rim."""
    from core.world import presets
    r = presets.generate_landscape("highland_lake", 768, seed)
    assert r["ok"], [s for s in r["steps"] if not s.get("ok")]
    river = next(s for s in r["steps"] if s["step"] == "river")
    assert river["waterfalls"] >= 1 and r["traversability"]["summit_reachable"]


@pytest.mark.parametrize("preset,size", [("mesa_valley", 1024), ("river_valley", 1536), ("canyon", 1536)])
@pytest.mark.parametrize("seed", [1, 2, 5])
def test_new_presets_build_a_playable_world(preset, size, seed):
    from biomes.masks.masks import water_mask
    from core.world import presets
    from core.world import service as S
    r = presets.generate_landscape(preset, size, seed)
    assert r["ok"], [s for s in r["steps"] if not s.get("ok")]
    hf = S.SESSION.hf
    st = r["player_start"]
    assert not water_mask(hf, S.SESSION.op_log)[int(st[1] / hf.cell_size), int(st[0] / hf.cell_size)]     # the player starts on land
    assert r["traversability"]["reachable_fraction"] > 0.3
    kinds = {o.kind for o in S.SESSION.op_log}
    assert "scatter_vegetation" in kinds and ({"river", "lake"} & kinds)
    assert float(hf.data.max() - hf.data.min()) > size * 0.05                                         # real relief, not a flat map


def test_presets_are_listed_for_agents():
    from core.world import service as S
    caps = S.get_capabilities()["landscape_presets"]
    assert set(caps) >= {"highland_lake", "mesa_valley", "river_valley", "canyon"}
