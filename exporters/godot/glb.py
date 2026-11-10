"""Minimal binary glTF (GLB) writer. Nodes named '<x>-colonly' become collision-only bodies in Godot."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np


def write_glb(path: str | Path, nodes: list[dict]) -> int:
    """nodes: [{name, positions (N,3), normals (N,3)|None, uvs (N,2)|None, indices (M,3)|(M*3,), material: str|None}].
    Returns file size in bytes."""
    gltf: dict = {"asset": {"version": "2.0", "generator": "AI World Studio"}, "scene": 0,
                  "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": [], "meshes": [], "accessors": [],
                  "bufferViews": [], "buffers": [{"byteLength": 0}], "materials": []}
    blob = bytearray()
    mat_ids: dict[str, int] = {}

    def add_view(arr: np.ndarray, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": target})
        blob.extend(arr.tobytes())
        return len(gltf["bufferViews"]) - 1

    def add_acc(view: int, comp: int, count: int, typ: str, mn=None, mx=None) -> int:
        a = {"bufferView": view, "componentType": comp, "count": count, "type": typ}
        if mn is not None:
            a["min"], a["max"] = mn, mx
        gltf["accessors"].append(a)
        return len(gltf["accessors"]) - 1

    for i, n in enumerate(nodes):
        pos = np.ascontiguousarray(n["positions"], np.float32).reshape(-1, 3)
        idx = np.ascontiguousarray(n["indices"], np.uint32).reshape(-1)
        attrs = {"POSITION": add_acc(add_view(pos, 34962), 5126, len(pos), "VEC3", pos.min(0).tolist(), pos.max(0).tolist())}
        if n.get("normals") is not None:
            nr = np.ascontiguousarray(n["normals"], np.float32).reshape(-1, 3)
            attrs["NORMAL"] = add_acc(add_view(nr, 34962), 5126, len(nr), "VEC3")
        if n.get("uvs") is not None:
            uv = np.ascontiguousarray(n["uvs"], np.float32).reshape(-1, 2)
            attrs["TEXCOORD_0"] = add_acc(add_view(uv, 34962), 5126, len(uv), "VEC2")
        if n.get("colors") is not None:
            col = np.ascontiguousarray(n["colors"], np.float32).reshape(-1, 3)
            attrs["COLOR_0"] = add_acc(add_view(col, 34962), 5126, len(col), "VEC3")
        prim = {"attributes": attrs, "indices": add_acc(add_view(idx, 34963), 5125, len(idx), "SCALAR"), "mode": 4}
        m = n.get("material")
        if m:
            if m not in mat_ids:
                mat_ids[m] = len(gltf["materials"])
                gltf["materials"].append({"name": m, "pbrMetallicRoughness": {"baseColorFactor": [0.6, 0.6, 0.6, 1], "metallicFactor": 0}})
            prim["material"] = mat_ids[m]
        gltf["meshes"].append({"name": n["name"], "primitives": [prim]})
        gltf["nodes"].append({"name": n["name"], "mesh": i})
    if not gltf["materials"]:
        del gltf["materials"]
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"][0]["byteLength"] = len(blob)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js \
        + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    Path(path).write_bytes(out)
    return total
