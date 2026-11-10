import numpy as np

from core.history.operations import Operation, dumps, loads, run
from terrain.erosion.erosion import hydraulic, thermal
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.noise import fbm
from terrain.procedural.shapes import basin, mountain, plateau, valley


def hf(n=129):
    return Heightfield(n, n, 2.0)


def test_noise_reproducible_and_bounded():
    a, b = fbm(100, 100, 2.0, 7), fbm(100, 100, 2.0, 7)
    assert np.array_equal(a, b) and not np.array_equal(a, fbm(100, 100, 2.0, 8))
    assert abs(a).max() <= 1 and a.std() > 0.05


def test_mountain_peak_at_center():
    h = hf(); mountain(h, 128, 128, 80, 50, roughness=0)
    assert abs(h.data.max() - 50) < 2 and h.data[0, 0] == 0
    assert np.unravel_index(h.data.argmax(), h.data.shape) == (64, 64)


def test_plateau_flat_top_and_outside_untouched():
    h = hf(); h.data[:] = 3
    plateau(h, [(80, 80), (180, 80), (180, 180), (80, 180)], 40, wall_width=15)
    assert np.allclose(h.data[64:90, 64:90], 40, atol=1e-3)  # inside (x,y 128..180 => idx 64..90)
    assert h.data[0, 0] == 3 and h.data[120, 120] == 3


def test_basin_valley_lower_terrain():
    h = hf(); basin(h, 128, 128, 60, 10); assert h.data[64, 64] < -9.9
    h2 = hf(); valley(h2, [(0, 128), (256, 128)], 30, 8); assert h2.data[64, 10] < -7.9 and h2.data[0, 10] == 0


def test_thermal_conserves_mass_and_reduces_slope():
    h = hf(); mountain(h, 128, 128, 80, 120, profile=3, roughness=0)
    m0, s0 = h.data.sum(), np.abs(np.diff(h.data, axis=1)).max()
    thermal(h, 50, talus=0.3)
    assert abs(h.data.sum() - m0) < 1e-2 * abs(m0) and np.abs(np.diff(h.data, axis=1)).max() < s0


def test_hydraulic_runs_finite_and_deterministic():
    def go():
        h = hf(); mountain(h, 128, 128, 90, 60, seed=3); hydraulic(h, 20); return h.data
    a, b = go(), go()
    assert np.isfinite(a).all() and np.array_equal(a, b)


def test_operation_log_replay():
    ops = [Operation("mountain", dict(x=128, y=128, radius=70, height=30, seed=1)),
           Operation("thermal_erosion", dict(iterations=5)), Operation("basin", dict(x=40, y=40, radius=20, depth=5), enabled=False)]
    def build(o):
        h = hf()
        for op in o: run(h, op)
        return h.data
    assert np.array_equal(build(ops), build(loads(dumps(ops))))
