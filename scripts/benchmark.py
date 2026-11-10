"""Measure dab + chunk rebuild time per resolution. Usage: python scripts/benchmark.py"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terrain.chunks.mesh import build_chunk, dirty_chunks
from terrain.heightfield.heightfield import Heightfield
from terrain.sculpt.brushes import BrushParams, Tool, apply_dab

for n, c in [(513, 2.0), (1025, 1.0), (2049, 1.0), (4097, 0.5)]:
    hf = Heightfield(n, n, c)
    mid = hf.size[0] / 2
    for tool in Tool:
        p = BrushParams(radius=40 * c, strength=0.5, target_height=3)
        t = time.perf_counter(); N = 20
        for i in range(N):
            r = apply_dab(hf, tool, mid + i, mid, p)
        t_dab = (time.perf_counter() - t) / N * 1000
        t = time.perf_counter()
        ch = dirty_chunks(r, hf)
        for cx, cy in ch:
            build_chunk(hf, cx, cy)
        t_mesh = (time.perf_counter() - t) * 1000
        print(f"{n}x{n} cell={c} {tool.value:8s} dab={t_dab:6.2f}ms  rebuild {len(ch)} chunk(s)={t_mesh:6.2f}ms")
