"""Agent panel: the editor's live bridge (core/world/bridge.py) and what agents are doing on the world, call by call."""
from __future__ import annotations

import json
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget

from core.world.bridge import ACTIVITY

TOOL_ES = {
    "create_world": "Crear mundo", "generate_landscape": "Generar paisaje", "apply_terrain_noise": "Relieve base", "create_mountain": "Montaña",
    "create_mountain_range": "Cordillera", "create_valley": "Valle", "create_bowl": "Cuenco", "terrace_terrain": "Terrazas",
    "create_border_mountains": "Montañas del borde", "create_basin": "Cuenca", "create_plateau": "Meseta", "create_cliff": "Meseta con paredes",
    "apply_erosion": "Erosión", "create_river": "Río", "create_river_auto": "Río automático", "create_lake": "Lago", "create_path": "Sendero",
    "create_trail": "Sendero automático", "create_climbable_mountain": "Montaña escalable", "scatter_vegetation": "Vegetación",
    "place_objects": "Colocar objetos", "modify_operation": "Editar operación", "set_operation_enabled": "Activar/desactivar operación",
    "paint_terrain_material": "Pintar suelo", "capture_world_view": "Mirar el mundo", "get_world_preview": "Mapa desde arriba",
    "validate_world": "Validar", "analyze_traversability": "Transitabilidad", "export_to_godot": "Exportar a Godot", "save_world": "Guardar",
    "load_world": "Abrir", "undo": "Deshacer", "redo": "Rehacer", "create_checkpoint": "Checkpoint", "rollback_to_checkpoint": "Volver a checkpoint",
}
READ_ONLY = {"get_capabilities", "get_world_info", "get_terrain_info", "inspect_terrain", "inspect_region", "analyze_elevation", "analyze_slope",
             "detect_terrain_issues", "validate_world", "get_world_preview", "capture_world_view", "list_operations", "get_task_status",
             "get_export_status", "prepare_godot_export", "analyze_traversability", "compare_world_versions", "inspect_godot_project",
             "save_world", "create_checkpoint"}


def _short(args: dict, n: int = 70) -> str:
    s = ", ".join(f"{k}={json.dumps(v, default=str)}" for k, v in args.items())
    return s if len(s) <= n else s[: n - 1] + "…"


class AgentPanel(QWidget):
    world_changed = Signal()            # an agent call changed the world: redraw

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 6, 6, 6)
        self.status = QLabel(); self.status.setWordWrap(True); lay.addWidget(self.status)
        split = QSplitter(Qt.Vertical)
        self.list = QListWidget(); split.addWidget(self.list)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); split.addWidget(self.detail)
        split.setSizes([380, 200]); lay.addWidget(split)
        self._items: dict[int, QListWidgetItem] = {}
        self._entries: dict[int, dict] = {}
        self._seen = 0
        self._handled: set[int] = set()
        self.list.currentItemChanged.connect(self._show)
        self._set_status()
        self.timer = QTimer(self, interval=300); self.timer.timeout.connect(self.poll); self.timer.start()

    def _set_status(self):
        if self.bridge and self.bridge.port:
            self.status.setText(f"🟢 Conexión para agentes activa en 127.0.0.1:{self.bridge.port}. Un agente con el MCP «ai-world-studio» "
                                "(p. ej. Claude Code en esta carpeta) trabaja sobre ESTE mundo; aquí ves cada paso.")
        else:
            self.status.setText("⚪ Conexión para agentes desactivada.")

    def poll(self) -> None:
        changed = False
        for e in ACTIVITY.since(self._seen):
            seq = e["seq"]
            self._entries[seq] = e
            icon = {"running": "⏳", "done": "✓", "failed": "✗"}[e["status"]] + (" ⚠" if e["warnings"] else "")
            text = (f"{time.strftime('%H:%M:%S', time.localtime(e['time']))}  {icon}  {TOOL_ES.get(e['tool'], e['tool'])}"
                    + (f"  ({e['ms']:.0f} ms)" if e["ms"] is not None else "") + f"\n      {_short(e['args'])}")
            it = self._items.get(seq)
            if it is None:
                it = QListWidgetItem(text); self.list.addItem(it); self._items[seq] = it
                self.list.scrollToBottom()
            else:
                it.setText(text)
            it.setForeground(QColor("#b91c1c") if e["status"] == "failed" else (QColor("#92400e") if e["warnings"] else QColor("#111827")))
            if e["status"] != "running":
                self._seen = max(self._seen, seq)
                if seq not in self._handled:
                    self._handled.add(seq)
                    if e["tool"] not in READ_ONLY:           # even a failed call may have changed part of the world
                        changed = True
            if self.list.currentItem() is it:
                self._show(it)
        if changed:
            self.world_changed.emit()

    def _show(self, it, *_):
        seq = next((k for k, v in self._items.items() if v is it), None)
        if seq is None:
            return
        e = self._entries[seq]
        res = e["result"]
        if isinstance(res, dict):
            res = {k: v for k, v in res.items() if k not in ("validation",)}
        self.detail.setPlainText(f"{e['tool']}  ·  {e['status']}" + (f"  ·  {e['ms']:.0f} ms" if e["ms"] is not None else "")
                                 + "\n\nArgumentos:\n" + json.dumps(e["args"], indent=2, ensure_ascii=False, default=str)
                                 + ("\n\nResultado:\n" + json.dumps(res, indent=2, ensure_ascii=False, default=str)[:6000] if res is not None else ""))
