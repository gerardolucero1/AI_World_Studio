from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QSurfaceFormat
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QToolBar, QVBoxLayout, QWidget)

from core.history.operations import Operation
from core.world.session import WorldSession
from materials.materials import MATERIALS
from terrain.heightfield.heightfield import Heightfield
from ui.viewport.viewport import TerrainViewport

MATERIAL_ES = {"grass": "Pasto", "dirt": "Tierra", "rock": "Roca", "snow": "Nieve", "sand": "Arena",
               "mud": "Barro", "wet_rock": "Roca húmeda", "gravel": "Grava"}
OP_ES = {"noise": "Relieve base", "mountain": "Montaña", "mountain_range": "Cordillera", "plateau": "Meseta",
         "cliff_plateau": "Meseta con paredes", "valley": "Valle", "basin": "Cuenca", "river": "Río", "lake": "Lago",
         "path": "Sendero", "thermal_erosion": "Erosión térmica", "hydraulic_erosion": "Erosión hidráulica",
         "scatter_vegetation": "Vegetación", "place_objects": "Objetos colocados", "paint_material": "Pintura de suelo", "sculpt": "Pincel", "brush": "Pincel",
         "bowl": "Cuenco", "terrace": "Terrazas", "border_mountains": "Montañas del borde", "cap": "Cima plana",
         "climbable_mountain": "Montaña escalable"}


WARN_ES = {
    "river_uphill": "El terreno sube contra la corriente del río; se abrió el cauce a la fuerza. Baja el terreno más adelante, mueve el río o añade un lago.",
    "lake_leak": "El borde del lago es más bajo que el agua: se derramaría. Sube el borde o baja el nivel.",
    "lake_dry_cells": "Hay zonas secas dentro del lago.",
    "path_too_steep": "El sendero es demasiado empinado para esa pendiente máxima. Prueba zigzags, una rampa o escalones, o cambia el recorrido.",
    "cliff_detached": "La pared de roca quedó separada del terreno. Vuelve a crear la meseta.",
    "cliff_low": "La pared es muy baja: la meseta casi no sobresale del suelo.",
    "veg_in_water": "Hay vegetación dentro del agua.",
    "veg_steep": "Hay vegetación en pendientes demasiado fuertes.",
}


@dataclass
class ToolSpec:
    id: str
    glyph: str
    label: str
    title: str                  # heading in the properties panel
    size_label: str | None = None
    height_label: str | None = None


GROUPS: list[tuple[str, list[ToolSpec]]] = [
    ("Vista", [ToolSpec("navigate", "✥", "Mover vista", "Mover la vista")]),
    ("Editar", [ToolSpec("edit_curve", "✎", "Editar ríos, lagos y senderos", "Editar ríos, lagos y senderos")]),
    ("Esculpir", [ToolSpec("raise", "▲", "Subir terreno", "Subir terreno"),
                  ToolSpec("lower", "▼", "Bajar terreno", "Bajar terreno"),
                  ToolSpec("smooth", "≈", "Suavizar", "Suavizar"),
                  ToolSpec("flatten", "▬", "Aplanar", "Aplanar"),
                  ToolSpec("paint", "✎", "Pintar suelo", "Pintar suelo")]),
    ("Crear en el mapa", [ToolSpec("mountain", "⛰", "Montaña", "Crear montaña", "Radio (m)", "Altura (m)"),
                          ToolSpec("cliff", "▇", "Meseta con paredes", "Crear meseta con paredes de roca", "Radio (m)", "Altura sobre el suelo (m)"),
                          ToolSpec("lake", "◉", "Lago", "Crear lago", "Radio (m)"),
                          ToolSpec("place", "🌳", "Colocar objeto", "Colocar un objeto (árbol, roca…)"),
                          ToolSpec("river", "∿", "Río", "Dibujar río", "Ancho (m)"),
                          ToolSpec("path", "┈", "Sendero", "Dibujar sendero", "Ancho (m)")]),
]
SPECS = {t.id: t for _, ts in GROUPS for t in ts}

STYLE = """
QMainWindow, QDockWidget { background: #f3f4f6; color: #111827; }
QLabel, QCheckBox, QGroupBox { color: #111827; }
QDockWidget::title { background: #e5e7eb; padding: 6px; font-weight: bold; }
QPushButton[class="tool"] { text-align: left; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 6px; background: white; color: #111827; font-size: 13px; }
QPushButton[class="tool"]:hover { background: #eef2ff; }
QPushButton[class="tool"]:checked { background: #2563eb; color: white; border-color: #1d4ed8; font-weight: bold; }
QPushButton[class="action"] { text-align: left; padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; background: white; color: #111827; }
QPushButton[class="action"]:hover { background: #ecfdf5; border-color: #10b981; }
QGroupBox { font-weight: bold; border: 1px solid #d1d5db; border-radius: 8px; margin-top: 12px; padding-top: 10px; background: #fafafa; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTabBar::tab { background: #e5e7eb; color: #111827; padding: 6px 14px; border-radius: 4px; }
QTabBar::tab:selected { background: #2563eb; color: white; }
QToolBar { spacing: 4px; padding: 4px; background: #e5e7eb; border: 0; }
QToolBar QToolButton { color: #111827; padding: 5px 8px; border-radius: 5px; }
QToolBar QToolButton:hover { background: #cbd5e1; }
QToolBar QToolButton:disabled { color: #9ca3af; }
"""


def new_terrain(res: int = 513, size_m: float = 1024.0) -> Heightfield:
    return Heightfield(res, res, size_m / (res - 1))


class LabeledSlider(QWidget):
    """Slider with a live numeric label (value = slider value * step)."""

    def __init__(self, lo: float, hi: float, value: float, on_change, unit: str = "", step: float = 1.0):
        super().__init__()
        self.step, self.unit, self._cb = step, unit, on_change
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.label = QLabel(); self.label.setMinimumWidth(62); self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1); lay.addWidget(self.label)
        self.set_range(lo, hi, value)
        self.slider.valueChanged.connect(self._changed)

    def set_range(self, lo: float, hi: float, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setRange(int(round(lo / self.step)), int(round(hi / self.step)))
        self.slider.setValue(int(round(value / self.step)))
        self.slider.blockSignals(False)
        self._refresh()

    @property
    def value(self) -> float:
        return self.slider.value() * self.step

    def set_value(self, v: float) -> None:
        self.slider.setValue(int(round(v / self.step)))

    def _refresh(self) -> None:
        self.label.setText(f"{self.value:.0f}{self.unit}")

    def _changed(self) -> None:
        self._refresh(); self._cb(self.value)


class MainWindow(QMainWindow):
    def __init__(self, bridge: bool = True):
        super().__init__()
        self.setWindowTitle("AI World Studio")
        self.resize(1440, 860)
        self.setStyleSheet(STYLE)
        self.session = WorldSession(new_terrain())
        from core.world import service as S
        S.SESSION = self.session          # one world: presets, tools (CLI/MCP-style calls) and the editor all act on what is on screen
        self.view = TerrainViewport(self.session)
        self.setCentralWidget(self.view)
        self.stat = QLabel(); self.statusBar().addPermanentWidget(self.stat)
        self.view.stats.connect(self.stat.setText)
        self.view.curve_edited.connect(self.on_curve_edited)
        self.bridge = None
        if bridge:                          # agents (MCP) work on this world while the editor is open
            from core.world.bridge import BridgeServer
            try:
                self.bridge = BridgeServer(); self.bridge.start()
            except OSError:
                self.bridge = None
        self.view.placed.connect(self.on_placed)
        self.view.brush_radius_changed.connect(lambda r: self.s_radius.set_value(r))
        self.tool_buttons: dict[str, QPushButton] = {}
        self._build_menu_and_toolbar()
        self._build_tool_dock()
        self._build_props_dock()
        self._build_history_dock()
        self.view.changed.connect(self.refresh)
        self.select_tool("navigate")
        self.refresh()
        self.statusBar().showMessage("Bienvenido. Elige una herramienta a la izquierda; con «Mover vista» arrastra el mapa para girarlo.", 8000)

    # ------------------------------------------------------------------ helpers
    @property
    def world(self) -> float:
        return float(max(self.session.hf.size))

    def _act(self, menu_or_bar, text, fn, shortcut=None, tip=None):
        a = QAction(text, self); a.triggered.connect(lambda *_: fn())
        if shortcut: a.setShortcut(QKeySequence(shortcut))
        if tip: a.setToolTip(tip); a.setStatusTip(tip)
        menu_or_bar.addAction(a); return a

    # ------------------------------------------------------------------ menus & toolbar
    def _build_menu_and_toolbar(self):
        m = self.menuBar().addMenu("&Archivo")
        self._act(m, "Nuevo mundo…", self.on_new, "Ctrl+N")
        self._act(m, "Abrir…", self.on_open, "Ctrl+O")
        self._act(m, "Guardar", self.on_save, "Ctrl+S")
        self._act(m, "Guardar como…", self.on_save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self._act(m, "Generar paisaje…", self.on_generate, "Ctrl+G", "Crea un paisaje completo: cuenco con lago, valle de mesetas, valle fluvial o cañón")
        self._act(m, "Exportar a Godot 4…", self.on_export, "Ctrl+E", "Genera un proyecto de Godot con el mundo (chunks, colisiones, agua, vegetación)")
        e = self.menuBar().addMenu("&Editar")
        self.a_undo = self._act(e, "Deshacer", self.view.undo, "Ctrl+Z", "Deshacer el último paso")
        self.a_redo = self._act(e, "Rehacer", self.view.redo, "Ctrl+Shift+Z", "Rehacer")
        v = self.menuBar().addMenu("&Vista")
        self._act(v, "Centrar mapa", self.view.frame_camera)
        self._act(v, "Vista desde arriba", self.view.view_top)
        h = self.menuBar().addMenu("&Ayuda")
        self._act(h, "Controles…", self.show_help)

        tb = QToolBar("Principal"); tb.setMovable(False); self.addToolBar(tb)
        self._act(tb, "📄 Nuevo", self.on_new, tip="Crear un mundo nuevo (Ctrl+N)")
        self._act(tb, "📂 Abrir", self.on_open, tip="Abrir un proyecto (Ctrl+O)")
        self._act(tb, "💾 Guardar", self.on_save, tip="Guardar el proyecto (Ctrl+S)")
        self._act(tb, "🏞 Generar paisaje", self.on_generate, tip="Crear un mapa completo: cuenco con lago, valle de mesetas, valle fluvial o cañón (Ctrl+G)")
        self._act(tb, "🎮 Exportar a Godot", self.on_export, tip="Exportar el mundo a un proyecto de Godot 4 (Ctrl+E)")
        tb.addSeparator()
        tb.addAction(self.a_undo); tb.addAction(self.a_redo)
        self.a_undo.setText("↶ Deshacer"); self.a_redo.setText("↷ Rehacer")
        tb.addSeparator()
        self._act(tb, "⌂ Centrar", self.view.frame_camera, tip="Volver a ver todo el mapa (tecla Inicio)")
        self._act(tb, "⬒ Desde arriba", self.view.view_top, tip="Vista cenital")
        self._act(tb, "＋ Acercar", lambda: self.view.zoom_by(0.8), tip="Acercar la vista")
        self._act(tb, "－ Alejar", lambda: self.view.zoom_by(1.25), tip="Alejar la vista")
        tb.addSeparator()
        self._act(tb, "❓ Ayuda", self.show_help, tip="Cómo se maneja")

    # ------------------------------------------------------------------ left dock: tools
    def _build_tool_dock(self):
        w = QWidget(); lay = QVBoxLayout(w); lay.setSpacing(6)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        for title, tools in GROUPS:
            lab = QLabel(title.upper()); lab.setStyleSheet("color:#6b7280;font-size:11px;font-weight:bold;margin-top:8px;")
            lay.addWidget(lab)
            for t in tools:
                b = QPushButton(f"  {t.glyph}   {t.label}"); b.setCheckable(True); b.setProperty("class", "tool")
                b.setToolTip(TerrainViewport.HINTS.get(t.id, ""))
                b.clicked.connect(lambda _=False, tid=t.id: self.select_tool(tid))
                self.group.addButton(b); self.tool_buttons[t.id] = b; lay.addWidget(b)
        lay.addStretch(1)
        d = QDockWidget("Herramientas"); d.setWidget(w); d.setFeatures(QDockWidget.DockWidgetMovable)
        d.setMinimumWidth(210); self.addDockWidget(Qt.LeftDockWidgetArea, d)

    # ------------------------------------------------------------------ right dock: properties
    def _build_props_dock(self):
        body = QWidget(); lay = QVBoxLayout(body); lay.setSpacing(10)
        self.title = QLabel(); self.title.setStyleSheet("font-size:16px;font-weight:bold;"); lay.addWidget(self.title)
        self.help_text = QLabel(); self.help_text.setWordWrap(True); self.help_text.setStyleSheet("color:#4b5563;"); lay.addWidget(self.help_text)

        p = self.view.params
        self.g_brush = QGroupBox("Pincel"); f = QFormLayout(self.g_brush)
        self.s_radius = LabeledSlider(1, 300, p.radius, lambda v: setattr(p, "radius", v), " m")
        self.s_strength = LabeledSlider(1, 100, p.strength * 100, lambda v: setattr(p, "strength", v / 100), " %")
        self.s_hard = LabeledSlider(0, 99, p.hardness * 100, lambda v: setattr(p, "hardness", v / 100), " %")
        f.addRow("Tamaño", self.s_radius); f.addRow("Intensidad", self.s_strength); f.addRow("Borde duro", self.s_hard)
        self.c_material = QComboBox()
        for m in MATERIALS:
            self.c_material.addItem(MATERIAL_ES[m], m)
        self.row_material = QLabel("Material"); f.addRow(self.row_material, self.c_material)
        lay.addWidget(self.g_brush)

        self.g_create = QGroupBox("Parámetros"); f2 = QFormLayout(self.g_create)
        self.lbl_size = QLabel("Radio (m)"); self.lbl_height = QLabel("Altura (m)")
        self.s_size = LabeledSlider(2, 500, 100, self._marker_changed, " m")
        self.s_height = LabeledSlider(2, 500, 100, lambda v: None, " m")
        self.seed = QSpinBox(); self.seed.setRange(0, 9999); self.seed.setValue(1)
        f2.addRow(self.lbl_size, self.s_size); f2.addRow(self.lbl_height, self.s_height); f2.addRow("Semilla (variación)", self.seed)
        lay.addWidget(self.g_create)

        self.g_place = QGroupBox("Objeto"); fp = QFormLayout(self.g_place)
        self.c_kind = QComboBox()
        for k, label in [("oak", "Roble (árbol de copa)"), ("pine", "Pino"), ("shrub", "Arbusto"), ("reed", "Juncos"), ("rock", "Roca")]:
            self.c_kind.addItem(label, k)
        self.s_scale = QDoubleSpinBox(); self.s_scale.setRange(0.2, 8.0); self.s_scale.setSingleStep(0.1); self.s_scale.setValue(1.0); self.s_scale.setSuffix(" ×")
        self.s_scale.setToolTip("1 = tamaño normal; 3 = un árbol gigante que sirve de punto de referencia")
        self.s_yaw = QSpinBox(); self.s_yaw.setRange(0, 359); self.s_yaw.setSuffix(" °")
        self.ch_yaw_rand = QCheckBox("Giro al azar"); self.ch_yaw_rand.setChecked(True)
        self.s_sink = QDoubleSpinBox(); self.s_sink.setRange(0.0, 5.0); self.s_sink.setSingleStep(0.1); self.s_sink.setSuffix(" m")
        self.s_sink.setToolTip("Cuánto se hunde en el suelo (útil en rocas grandes)")
        fp.addRow("Tipo", self.c_kind); fp.addRow("Tamaño", self.s_scale); fp.addRow("Giro", self.s_yaw); fp.addRow(self.ch_yaw_rand)
        fp.addRow("Hundir", self.s_sink)
        for sig in (self.c_kind.currentIndexChanged, self.s_scale.valueChanged):
            sig.connect(lambda *_: self._place_marker())
        lay.addWidget(self.g_place)

        g = QGroupBox("Acciones rápidas (todo el mapa)"); v = QVBoxLayout(g)
        for label, fn, tip in [("🌄  Generar relieve base", self.op_noise, "Añade colinas y valles suaves a todo el mapa"),
                               ("🪨  Erosión térmica", self.op_thermal, "Desmorona las pendientes demasiado empinadas"),
                               ("💧  Erosión por agua", self.op_hydraulic, "Excava cauces y deposita sedimento"),
                               ("🌲  Plantar vegetación", self.op_scatter, "Árboles, arbustos y juncos según altura, pendiente y agua")]:
            b = QPushButton(label); b.setProperty("class", "action"); b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f_=fn: f_()); v.addWidget(b)
        lay.addWidget(g)

        gv = QGroupBox("Vista"); fv = QFormLayout(gv)
        self.s_zoom = LabeledSlider(1, 10, 4, lambda val: setattr(self.view, "zoom_sensitivity", val), "")
        fv.addRow("Sensibilidad del zoom", self.s_zoom)
        c1 = QCheckBox("Mostrar ríos, lagos y senderos (líneas guía)"); c1.setChecked(True)
        c1.toggled.connect(lambda on: setattr(self.view, "show_features", on))
        c2 = QCheckBox("Mostrar vegetación"); c2.setChecked(True)
        c2.toggled.connect(lambda on: setattr(self.view, "show_vegetation", on))
        fv.addRow(c1); fv.addRow(c2)
        from render.overlays import MODES
        self.c_overlay = QComboBox()
        for k, (label, _) in MODES.items():
            self.c_overlay.addItem(label, k)
        self.lbl_overlay = QLabel(); self.lbl_overlay.setWordWrap(True); self.lbl_overlay.setStyleSheet("color:#4b5563;font-size:11px;")
        self.c_overlay.currentIndexChanged.connect(self._overlay_changed)
        fv.addRow("Capa de análisis", self.c_overlay); fv.addRow(self.lbl_overlay)
        lay.addWidget(gv)

        self.warn = QLabel(); self.warn.setWordWrap(True)
        self.warn.setStyleSheet("background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:8px;color:#78350f;"); self.warn.hide()
        lay.addWidget(self.warn)
        lay.addStretch(1)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(body); sc.setFrameShape(QScrollArea.NoFrame)
        d = QDockWidget("Propiedades"); d.setWidget(sc); d.setFeatures(QDockWidget.DockWidgetMovable)
        d.setMinimumWidth(340); self.addDockWidget(Qt.RightDockWidgetArea, d)
        self.props_dock = d
        self._sync_scales()

    def _build_history_dock(self):
        from app.op_editor import OpEditor
        self.ops = OpEditor(OP_ES, lambda w: WARN_ES.get(w.get("code"), w.get("message", "")))
        self.ops.set_session(self.session)
        self.ops.apply_requested.connect(lambda i, params, en: self._modify(i, params, en))
        self.hist = self.ops.list
        d = QDockWidget("Historial"); d.setWidget(self.ops); d.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.RightDockWidgetArea, d)
        self.tabifyDockWidget(self.props_dock, d)
        from app.agent_panel import AgentPanel
        self.agent = AgentPanel(self.bridge)
        self.agent.world_changed.connect(self._agent_changed_world)
        da = QDockWidget("Agente"); da.setWidget(self.agent); da.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.RightDockWidgetArea, da); self.tabifyDockWidget(d, da)
        self.props_dock.raise_()

    # ------------------------------------------------------------------ state
    def _sync_scales(self):
        """Adapt slider ranges/defaults to the world size."""
        w = self.world
        self.s_radius.set_range(1, max(30, w * 0.2), max(5, w * 0.03))
        self.view.params.radius = self.s_radius.value
        self.s_size.set_range(2, max(20, w * 0.4), w * 0.1)
        self.s_height.set_range(2, max(20, w * 0.4), w * 0.12)
        self.view.marker_radius = self.s_size.value

    def _marker_changed(self, v: float):
        self.view.marker_radius = v

    def select_tool(self, tid: str):
        spec = SPECS[tid]
        self.tool_buttons[tid].setChecked(True)
        self.view.set_mode(tid)
        w = self.world
        self.title.setText(f"{spec.glyph}  {spec.title}")
        self.help_text.setText(TerrainViewport.HINTS[tid])
        brush = tid in ("raise", "lower", "smooth", "flatten", "paint")
        self.g_brush.setVisible(brush)
        self.c_material.setVisible(tid == "paint"); self.row_material.setVisible(tid == "paint")
        self.g_create.setVisible(spec.size_label is not None)
        self.g_place.setVisible(tid == "place")
        if tid == "place":
            self._place_marker()
        if spec.size_label:
            self.lbl_size.setText(spec.size_label)
            defaults = {"mountain": (w * 0.12, w * 0.12), "cliff": (w * 0.08, w * 0.05), "lake": (w * 0.06, 0),
                        "river": (max(6, w * 0.012), 0), "path": (max(3, w * 0.005), 0)}[tid]
            if tid in ("river", "path"):
                self.s_size.set_range(2, 80, defaults[0])
            else:
                self.s_size.set_range(5, max(30, w * 0.4), defaults[0])
            has_h = spec.height_label is not None
            self.lbl_height.setVisible(has_h); self.s_height.setVisible(has_h)
            if has_h:
                self.lbl_height.setText(spec.height_label); self.s_height.set_range(2, max(20, w * 0.4), defaults[1])
            self.view.marker_radius = self.s_size.value

    def refresh(self):
        self.a_undo.setEnabled(self.session.history.can_undo); self.a_redo.setEnabled(self.session.history.can_redo)
        self.ops.refresh()
        name = self.session.name + (f" — {self.session.path}" if self.session.path else " (sin guardar)")
        self.setWindowTitle(f"AI World Studio — {name}")

    # ------------------------------------------------------------------ operations
    def _op(self, kind, **params) -> Operation | None:
        op = Operation(kind, params)
        try:
            self.view.run_operation(op)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "No se pudo aplicar", f"{type(ex).__name__}: {ex}")
            return None
        warns = (op.result or {}).get("warnings", [])
        if warns:
            self.warn.setText("⚠ " + "\n⚠ ".join(WARN_ES.get(w.get("code"), w["message"]) for w in warns))
            self.warn.show()
        else:
            self.warn.hide()
        self.statusBar().showMessage(f"{OP_ES.get(kind, kind)} aplicado." + (" Con avisos (ver panel Propiedades)." if warns else ""), 6000)
        return op

    def _poly(self, x: float, z: float, r: float, n: int = 9) -> list[tuple[float, float]]:
        rng = np.random.default_rng(self.seed.value())
        hf = self.session.hf; sx, sz = hf.size; m = hf.cell_size * 4
        out = []
        for a in np.linspace(0, 2 * np.pi, n, endpoint=False):
            rr = r * rng.uniform(0.8, 1.15)
            out.append((float(np.clip(x + np.cos(a) * rr, m, sx - m)), float(np.clip(z + np.sin(a) * rr, m, sz - m))))
        return out

    def on_curve_edited(self, index: int, pts: list):
        """A handle of a river/trail/lake was dragged: change that operation and replay the world (dependent features adapt)."""
        from features.curves.edit import EDITABLE
        self._modify(index, {EDITABLE[self.session.op_log[index].kind][0]: pts})

    def _modify(self, index: int, params: dict, enabled=None):
        """Change a past operation (parameters and/or on/off) and replay the world from the original terrain."""
        if not self.session.can_modify:
            QMessageBox.information(self, "No se puede editar", "Este mundo se abrió sin su terreno original (proyecto antiguo): "
                                    "guárdalo y vuelve a abrirlo, o crea el elemento de nuevo."); return
        op = self.session.op_log[index]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.session.modify(index, params, enabled=enabled)
        except Exception as ex:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "No se pudo cambiar", f"{type(ex).__name__}: {ex}"); return
        QApplication.restoreOverrideCursor()
        self.view.apply_modify()
        warns = [(i, w) for i, o in enumerate(self.session.op_log) if i >= index for w in (o.result or {}).get("warnings", [])]
        if warns:
            self.warn.setText("⚠ " + "\n⚠ ".join(f"{OP_ES.get(self.session.op_log[i].kind, '')}: {WARN_ES.get(w.get('code'), w['message'])}"
                                                  for i, w in warns[:6]))
            self.warn.show()
        else:
            self.warn.hide()
        what = "desactivada" if enabled is False else ("activada" if enabled else "cambiada")
        self.statusBar().showMessage(f"{OP_ES.get(op.kind, op.kind)} {what}; se rehízo todo lo que depende de ella.", 6000)

    def _agent_changed_world(self):
        self.view.refresh_world(); self._sync_scales(); self.refresh()
        self.statusBar().showMessage("El agente cambió el mundo (ver panel «Agente»).", 5000)

    def closeEvent(self, e):
        if self.bridge:
            self.bridge.stop()
        super().closeEvent(e)

    def _overlay_changed(self, *_):
        from render.overlays import MODES
        mode = self.c_overlay.currentData()
        self.lbl_overlay.setText(MODES[mode][1])
        if mode == "walkable":
            QApplication.setOverrideCursor(Qt.WaitCursor)
        self.view.set_overlay(mode)
        self.view.repaint()
        if mode == "walkable":
            QApplication.restoreOverrideCursor()

    def _place_marker(self):
        from biomes.scatter.place import KINDS
        self.view.marker_radius = KINDS[self.c_kind.currentData()][0] * self.s_scale.value()
        self.view.update()

    def on_placed(self, mode: str, pts: list):
        hf = self.session.hf; seed = self.seed.value(); r = self.s_size.value
        if mode == "place":
            import random
            from biomes.scatter.place import place_params
            x, z = pts[0]
            yaw = random.uniform(0, 360) if self.ch_yaw_rand.isChecked() else float(self.s_yaw.value())
            try:
                params = place_params([dict(kind=self.c_kind.currentData(), x=x, y=z, scale=self.s_scale.value(), yaw=yaw,
                                            sink=self.s_sink.value())], hf.size)
            except ValueError as ex:
                QMessageBox.warning(self, "No se pudo colocar", str(ex)); return
            self._op("place_objects", **params)
        elif mode == "mountain":
            x, z = pts[0]
            self._op("mountain", x=x, y=z, radius=r, height=self.s_height.value, roughness=0.25, seed=seed)
        elif mode == "cliff":
            x, z = pts[0]
            self._op("cliff_plateau", polygon=self._poly(x, z, r), height=hf.sample(x, z) + self.s_height.value, strata=6,
                     ledge=r * 0.01, overhang=r * 0.015, flare=r * 0.03, roughness=r * 0.02, seed=seed)
        elif mode == "lake":
            x, z = pts[0]
            self._op("lake", polygon=self._poly(x, z, r), depth=max(2.0, r * 0.08), shore_width=max(6.0, r * 0.25))
        elif mode == "river":
            self._op("river", path=[list(p) for p in pts], width=r, depth=max(1.5, r * 0.3), bank_width=r * 1.2,
                     smoothing=max(30.0, r * 3))
        elif mode == "path":
            self._op("path", path=[list(p) for p in pts], width=r, bank_width=r * 1.5, smoothing=max(20.0, r * 4))
        elif mode == "paint":
            self._op("paint_material", points=[list(p) for p in pts], radius=self.view.params.radius,
                     material=self.c_material.currentData(), strength=self.view.params.strength,
                     hardness=self.view.params.hardness)

    def op_noise(self):
        w = self.world
        self._op("noise", seed=self.seed.value(), amplitude=w * 0.05, base_freq=2.5 / w, octaves=6, ridged=False)

    def op_thermal(self):
        self._op("thermal_erosion", iterations=30, talus=0.7)

    def op_hydraulic(self):
        self._op("hydraulic_erosion", iterations=40)

    def op_scatter(self):
        top = float(self.session.hf.data.max())
        sp = [dict(name="pine", min_sep=9, density=0.8, altitude=(-1e9, max(top * 0.7, 30)), max_slope=28, radius=2.5,
                   height=11, clusters=0.6),
              dict(name="shrub", min_sep=5, density=0.5, max_slope=35, radius=1.3, height=1.6, water_dist=(3, 1e9)),
              dict(name="reed", min_sep=4, density=0.9, max_slope=20, radius=0.6, height=1.4, water_dist=(1, 12))]
        self._op("scatter_vegetation", species=sp, seed=self.seed.value())

    # ------------------------------------------------------------------ files
    def on_new(self):
        res, ok = QInputDialog.getItem(self, "Nuevo mundo", "Detalle (vértices por lado).\nMás alto = más detalle pero más lento:",
                                       ["257", "513", "1025", "2049"], 1, False)
        if not ok:
            return
        size, ok = QInputDialog.getDouble(self, "Nuevo mundo", "Tamaño del mundo (metros por lado):", 1024, 64, 20000)
        if not ok:
            return
        self.session.replace(new_terrain(int(res), size), "Sin título"); self.session.path = None
        self.view.reload(); self._sync_scales(); self.select_tool(self.view.mode if self.view.mode in SPECS else "navigate")

    def on_open(self):
        d = QFileDialog.getExistingDirectory(self, "Abrir proyecto .awp")
        if d:
            try:
                self.session.load(d)
            except Exception as ex:  # noqa: BLE001
                QMessageBox.critical(self, "No se pudo abrir", str(ex)); return
            self.view.reload(); self._sync_scales(); self.select_tool("navigate")

    def on_save(self):
        if self.session.path is None:
            return self.on_save_as()
        self.session.save(); self.refresh(); self.statusBar().showMessage(f"Guardado en {self.session.path}", 4000)

    def on_save_as(self):
        d, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto", "MiMundo.awp")
        if d:
            self.session.name = Path(d).stem
            self.session.save(d if d.endswith(".awp") else d + ".awp"); self.refresh()
            self.statusBar().showMessage(f"Guardado en {self.session.path}", 4000)

    def on_generate(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from core.world import presets
        kinds = [("highland_lake", "Cuenco con lago (terrazas en anillos, lago con isla, cascadas, montaña escalable)", 1536),
                 ("mesa_valley", "Valle de mesetas (al estilo de Genshin: mesetas escalonadas, montañas, charcas, árbol grande, cascada)", 1024),
                 ("river_valley", "Valle fluvial (llanura con colinas, río grande que serpentea, lago, bosques)", 1536),
                 ("canyon", "Cañón (altiplano con cañón escalonado, río al fondo, mesetas sueltas, sendero de bajada)", 1536)]
        dlg = QDialog(self); dlg.setWindowTitle("Generar paisaje"); f = QFormLayout(dlg)
        c = QComboBox()
        for k, label, _ in kinds:
            c.addItem(label.split(" (")[0], k)
        desc = QLabel(); desc.setWordWrap(True); desc.setStyleSheet("color:#4b5563;"); desc.setMinimumWidth(420)
        size = QSpinBox(); size.setRange(768, 3000); size.setSingleStep(256); size.setSuffix(" m")
        seed = QSpinBox(); seed.setRange(0, 9999); seed.setValue(self.seed.value())

        def changed(i):
            k, label, default = kinds[i]
            t = label[label.index("(") + 1:-1]; desc.setText(t[:1].upper() + t[1:] + "."); size.setValue(default)
        c.currentIndexChanged.connect(changed); changed(0)
        f.addRow("Tipo de mapa", c); f.addRow(desc); f.addRow("Tamaño (lado)", size)
        f.addRow("Semilla (cambia el resultado)", seed)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.button(QDialogButtonBox.Ok).setText("Generar")
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); f.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            r = presets.generate_landscape(c.currentData(), float(size.value()), seed.value())
        finally:
            QApplication.restoreOverrideCursor()
        self.view.reload(); self._sync_scales(); self.select_tool("navigate")
        if not r.get("ok"):
            bad = [s for s in r.get("steps", []) if not s.get("ok")]
            QMessageBox.warning(self, "Paisaje con problemas", str(r.get("error") or bad)[:800]); return
        tr = r.get("traversability", {})
        self.statusBar().showMessage(f"{c.currentText()} generado (semilla {seed.value()}). Zona transitable a pie: {tr.get('reachable_fraction', 0):.0%}. "
                                     "Prueba otra semilla para variarlo.", 10000)

    def on_export(self):
        from app.export_dialog import ExportDialog, run_export
        dlg = ExportDialog(self)
        if dlg.exec() != ExportDialog.Accepted:
            return
        kw = dlg.kwargs()

        def done(r):
            if not r.get("ok"):
                QMessageBox.critical(self, "No se pudo exportar", str(r.get("error", r))[:1500]); return
            f = r.get("features", {})
            errs = [x for x in r.get("validation", {}).get("findings", []) if x["severity"] == "error"]
            v = r.get("godot_verification")
            where = r.get("path", "")
            lines = [f"Terreno: {'Terrain3D' if r.get('terrain') == 'terrain3d' else str(r.get('chunks')) + ' chunks'} · {f.get('lakes', 0)} lagos · "
                     f"{f.get('rivers', 0)} ríos · {f.get('waterfalls', 0)} cascadas · {r.get('vegetation_instances', 0)} plantas.",
                     f"Carpeta: {where}"]
            if errs:
                lines.append(f"⚠ Validación: {len(errs)} errores (ver WorldData/manifest.json).")
            if v is not None:
                lines.append("✓ Verificado en Godot." if v.get("ok") else f"⚠ La verificación en Godot falló: {v.get('error') or v.get('godot_errors')}")
            lines.append(r.get("next_step", ""))
            QMessageBox.information(self, "Exportación lista", "\n\n".join(l for l in lines if l))
            self.statusBar().showMessage(f"Exportado a {where}", 8000)
        run_export(self, kw, done)

    def show_help(self):
        QMessageBox.information(self, "Cómo se maneja", (
            "<h3>Mover la vista</h3>"
            "<ul><li><b>Girar:</b> clic derecho + arrastrar (o clic izquierdo con «Mover vista»). Gira alrededor de lo que está bajo el cursor.</li>"
            "<li><b>Desplazar el mapa:</b> Mayús + arrastrar, o botón central, o flechas / WASD.</li>"
            "<li><b>Zoom:</b> rueda del ratón o pellizco en el trackpad (acerca hacia el cursor). "
            "Ajusta la <i>sensibilidad del zoom</i> en «Propiedades».</li>"
            "<li><b>Q / E:</b> girar · <b>+ / −:</b> zoom · <b>Inicio:</b> ver todo el mapa.</li></ul>"
            "<h3>Esculpir</h3><ul><li>Elige <i>Subir / Bajar / Suavizar / Aplanar</i> y arrastra con el clic izquierdo. "
            "<b>Ctrl</b> invierte subir/bajar. <b>Alt + rueda</b> cambia el tamaño del pincel.</li></ul>"
            "<h3>Crear</h3><ul><li><i>Montaña, Meseta, Lago:</i> haz clic en el mapa.</li>"
            "<li><i>Río y Sendero:</i> clic para cada punto; doble clic o Intro para terminar; Retroceso borra el último; Esc cancela.</li>"
            "<li>Todo se puede deshacer con <b>Ctrl+Z</b>.</li></ul>"))


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")                      # consistent look; ignore the OS dark mode
    pal = QPalette()
    for role, col in [(QPalette.Window, "#f3f4f6"), (QPalette.WindowText, "#111827"), (QPalette.Base, "#ffffff"),
                      (QPalette.AlternateBase, "#f3f4f6"), (QPalette.Text, "#111827"), (QPalette.Button, "#ffffff"),
                      (QPalette.ButtonText, "#111827"), (QPalette.ToolTipBase, "#fef9c3"), (QPalette.ToolTipText, "#111827"),
                      (QPalette.Highlight, "#2563eb"), (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, "#6b7280")]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)


def main() -> int:
    fmt = QSurfaceFormat(); fmt.setVersion(3, 3); fmt.setProfile(QSurfaceFormat.CoreProfile); fmt.setDepthBufferSize(24)
    fmt.setSwapInterval(1); QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv)
    apply_theme(app)
    w = MainWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
