"""Locate the Godot 4 binary on macOS, Windows and Linux.

Order: explicit path → $AWS_GODOT → known install locations → `godot`/`godot4` on PATH.
On Windows the `_console.exe` sibling is preferred: it writes stdout/stderr to the pipes we capture."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _windows_candidates() -> list[Path]:
    home = Path.home()
    roots = [home / "Documents", home / "Downloads", home / "Desktop", home / "Godot", Path("C:/Godot"),
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Godot"]
    found: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        # the official zip extracts to a folder named like the exe, so look one level deep too
        for pat in ("Godot_v4*_win64*.exe", "Godot_v4*_win64*.exe/Godot_v4*_win64*.exe", "*/Godot_v4*_win64*.exe"):
            found += [p for p in r.glob(pat) if p.is_file()]
    found.sort(key=lambda p: p.name, reverse=True)             # newest version first (lexicographic is good enough for v4.x.y)
    return found


def _prefer_console(p: Path) -> Path:
    if sys.platform == "win32" and p.suffix.lower() == ".exe" and not p.stem.endswith("_console"):
        c = p.with_name(p.stem + "_console.exe")
        if c.exists():
            return c
    return p


def find_godot(explicit: str | None = None) -> str | None:
    """Return a path to an existing Godot binary or None. An explicit path is taken literally (no silent fallback)."""
    if explicit:
        return str(_prefer_console(Path(explicit))) if Path(explicit).exists() else None
    cands: list[Path] = []
    if os.environ.get("AWS_GODOT"):
        cands.append(Path(os.environ["AWS_GODOT"]))
    cands.append(Path("/Applications/Godot.app/Contents/MacOS/Godot"))
    if sys.platform == "win32":
        cands += _windows_candidates()
    for name in ("godot", "godot4"):
        w = shutil.which(name)
        if w:
            cands.append(Path(w))
    for c in cands:
        if c.exists() and c.is_file():
            return str(_prefer_console(c))
    return None


GODOT = find_godot()
