"""History panel: the list of operations, and an editor for the selected one (its numeric/boolean parameters and whether it is on).
Applying replays the world from the original terrain (session.modify), so everything that depends on it adapts."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

PARAM_ES = {
    "x": "X (m)", "y": "Y (m)", "radius": "Radio (m)", "height": "Altura (m)", "width": "Ancho (m)", "depth": "Profundidad (m)",
    "seed": "Semilla", "amplitude": "Amplitud (m)", "roughness": "Rugosidad", "profile": "Perfil (forma del pico)", "level": "Nivel del agua (m)",
    "step": "Altura de cada terraza (m)", "riser": "Inclinación de las paredes", "warp": "Deformación (m)", "strength": "Intensidad",
    "r_inner": "Radio interior (m)", "r_outer": "Radio exterior (m)", "base_freq": "Frecuencia base", "octaves": "Octavas", "ridged": "Crestas",
    "bank_width": "Ancho de orillas (m)", "max_slope": "Pendiente máxima", "smoothing": "Suavizado (m)", "max_cut_fill": "Corte/relleno máx. (m)",
    "shore_width": "Ancho de playa (m)", "wall_width": "Ancho de la pared (m)", "strata": "Estratos", "ledge": "Cornisas", "overhang": "Voladizo",
    "flare": "Base ensanchada", "feather": "Difuminado (m)", "grade": "Pendiente de la rampa", "iterations": "Iteraciones",
    "hardness": "Borde duro", "target_height": "Altura objetivo (m)", "sides": "Lados", "flat": "Fondo plano", "min_height": "Altura mínima (m)",
}
SKIP = {"dabs", "species", "objects", "path", "polygon", "points", "region", "exclusion", "radial_mask", "center"}


class OpEditor(QWidget):
    """Emits apply_requested(index, changed_params, enabled_or_None)."""
    apply_requested = Signal(int, dict, object)

    def __init__(self, names: dict, warn_text, parent=None):
        super().__init__(parent)
        self.names, self.warn_text = names, warn_text
        self.session = None
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Vertical)
        self.list = QListWidget(); split.addWidget(self.list)
        self.box = QGroupBox("Operación seleccionada")
        self.form_host = QVBoxLayout(self.box)
        self.info = QLabel("Selecciona una operación para ver y cambiar sus parámetros."); self.info.setWordWrap(True)
        self.form_host.addWidget(self.info)
        self.form_w = QWidget(); self.form = QFormLayout(self.form_w); self.form_host.addWidget(self.form_w)
        self.ch_enabled = QCheckBox("Activa (desmárcala para ver el mundo sin esta operación)")
        self.form_host.addWidget(self.ch_enabled)
        self.warns = QLabel(); self.warns.setWordWrap(True); self.warns.setStyleSheet("color:#92400e;")
        self.form_host.addWidget(self.warns)
        row = QHBoxLayout(); self.b_apply = QPushButton("Aplicar cambios"); self.b_apply.setProperty("class", "action")
        row.addWidget(self.b_apply); row.addStretch(1); self.form_host.addLayout(row)
        self.form_host.addStretch(1)
        split.addWidget(self.box); split.setSizes([260, 420])
        lay.addWidget(split)
        self._fields: dict[str, QWidget] = {}
        self._index = -1
        self.list.currentRowChanged.connect(self.show_op)
        self.b_apply.clicked.connect(self._apply)
        self.show_op(-1)

    # ------------------------------------------------------------------
    def set_session(self, session) -> None:
        self.session = session

    def refresh(self) -> None:
        keep = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for i, op in enumerate(self.session.op_log, 1):
            w = (op.result or {}).get("warnings", [])
            self.list.addItem(f"{i}. {self.names.get(op.kind, op.kind)}" + ("   ⚠" if w else "") + ("" if op.enabled else "   (desactivada)"))
        self.list.blockSignals(False)
        if 0 <= keep < self.list.count():
            self.list.setCurrentRow(keep)
        self.show_op(self.list.currentRow())

    def show_op(self, index: int) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._fields.clear()
        self._index = index
        ok = self.session is not None and 0 <= index < len(self.session.op_log)
        for w in (self.form_w, self.ch_enabled, self.b_apply):
            w.setVisible(ok)
        if not ok:
            self.box.setTitle("Operación seleccionada"); self.info.setText("Selecciona una operación para ver y cambiar sus parámetros.")
            self.warns.setText("")
            return
        op = self.session.op_log[index]
        self.box.setTitle(f"{index + 1}. {self.names.get(op.kind, op.kind)}")
        editable_curve = op.kind in ("river", "path", "lake")
        n = 0
        for k, v in op.params.items():
            if k in SKIP or v is None or isinstance(v, (list, dict, str)):
                continue
            if isinstance(v, bool):
                w = QCheckBox(); w.setChecked(v)
            elif isinstance(v, int):
                w = QSpinBox(); w.setRange(-1_000_000, 1_000_000); w.setValue(v)
            else:
                w = QDoubleSpinBox(); w.setRange(-1e6, 1e6); w.setDecimals(4 if abs(float(v)) < 1 else 2)
                w.setSingleStep(max(abs(float(v)) * 0.05, 0.01)); w.setValue(float(v))
            self._fields[k] = w; self.form.addRow(PARAM_ES.get(k, k), w); n += 1
        msg = []
        if not n:
            msg.append("Esta operación no tiene parámetros numéricos.")
        if editable_curve:
            msg.append("Su recorrido se edita en el mapa con «Editar ríos, lagos y senderos».")
        if self.session.origin is None:
            msg.append("⚠ Este mundo se abrió sin su terreno original: no se pueden aplicar cambios.")
        self.info.setText(" ".join(msg) or "Cambia los valores y pulsa «Aplicar cambios»: se rehace todo lo que viene después.")
        self.ch_enabled.setChecked(op.enabled)
        self.b_apply.setEnabled(self.session.origin is not None)
        self.warns.setText("\n".join("⚠ " + self.warn_text(w) for w in (op.result or {}).get("warnings", [])))

    def changes(self) -> tuple[dict, bool | None]:
        op = self.session.op_log[self._index]
        changed = {}
        for k, w in self._fields.items():
            v = w.isChecked() if isinstance(w, QCheckBox) else w.value()
            old = op.params[k]
            if (isinstance(old, float) and abs(float(v) - old) > 1e-9) or (not isinstance(old, float) and v != old):
                changed[k] = type(old)(v)
        en = self.ch_enabled.isChecked()
        return changed, (en if en != op.enabled else None)

    def _apply(self) -> None:
        if self._index < 0:
            return
        changed, en = self.changes()
        if changed or en is not None:
            self.apply_requested.emit(self._index, changed, en)
