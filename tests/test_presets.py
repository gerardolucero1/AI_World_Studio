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
