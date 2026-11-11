"""Export to Godot dialog: into an existing Godot project (subfolder) or as a standalone project; terrain as meshes or Terrain3D with the
game's own material; the game's water script; navigation; grass; verification. It reads the chosen project to pre-fill the options
(exporters.godot.host_project) and remembers the last choices. The export itself runs as a background task (service.export_to_godot)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton, QRadioButton,
                               QSpinBox, QVBoxLayout, QWidget)

from materials.materials import MATERIALS

NAV = [("auto", "Automática (hornear con Godot si está instalado)"), ("bake", "Hornear con Godot (Recast)"),
       ("python", "Malla simple (sin Godot)"), ("none", "Sin navegación")]


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar a Godot")
        self.setMinimumWidth(620)
        self.cfg = QSettings("AIWorldStudio", "export")
        lay = QVBoxLayout(self)

        # ---- destination
        g = QGroupBox("Destino"); f = QFormLayout(g)
        self.r_host = QRadioButton("Dentro de un proyecto de Godot existente (no se toca su project.godot)")
        self.r_alone = QRadioButton("Como proyecto independiente (carpeta vacía)")
        bg = QButtonGroup(self); bg.addButton(self.r_host); bg.addButton(self.r_alone)
        f.addRow(self.r_host)
        self.e_project = QLineEdit(); self.e_project.setPlaceholderText("Carpeta del proyecto (la que tiene project.godot)")
        f.addRow("Proyecto:", self._with_browse(self.e_project, self._pick_project))
        self.e_sub = QLineEdit(); self.e_sub.setPlaceholderText("generated_worlds/mi_mundo")
        f.addRow("Subcarpeta:", self.e_sub)
        self.lbl_detect = QLabel(""); self.lbl_detect.setWordWrap(True); self.lbl_detect.setStyleSheet("color:#047857")
        f.addRow(self.lbl_detect)
        f.addRow(self.r_alone)
        self.e_out = QLineEdit(); self.e_out.setPlaceholderText("Carpeta vacía (o de una exportación anterior)")
        f.addRow("Carpeta:", self._with_browse(self.e_out, self._pick_out))
        lay.addWidget(g)

        # ---- terrain + look
        g = QGroupBox("Terreno y aspecto"); f = QFormLayout(g)
        self.c_terrain = QComboBox()
        self.c_terrain.addItem("Malla propia (chunks con LOD y colisión, sin addons)", "mesh")
        self.c_terrain.addItem("Terrain3D (usa el addon del proyecto: LOD, colisión, pintar/esculpir)", "terrain3d")
        f.addRow("Terreno:", self.c_terrain)
        self.c_mat = QComboBox(); self.c_mat.setEditable(True)
        self.c_mat.setToolTip("Material de Terrain3D del juego (.tres o una escena .tscn que lo tenga). Vacío = nuestro material y 8 texturas.")
        f.addRow("Material del juego:", self.c_mat)
        self.s_path_id = QSpinBox(); self.s_path_id.setRange(0, 31)
        self.s_path_id.setToolTip("Con el material del juego: ID de textura que su shader pinta como camino. El resto del terreno va a la ID 0.")
        f.addRow("ID de textura de senderos:", self.s_path_id)
        self.c_water = QComboBox(); self.c_water.setEditable(True)
        self.c_water.setToolTip("Script de agua del juego (responde surface_at y lee water.json). Vacío = nuestras mallas de agua.")
        f.addRow("Script de agua del juego:", self.c_water)
        self.c_profile = QComboBox(); self.c_profile.addItem("Completo (Forward+/Mobile)", "full"); self.c_profile.addItem("Compatible (también renderer Compatibility)", "compatible")
        f.addRow("Perfil:", self.c_profile)
        lay.addWidget(g)

        # ---- gameplay
        g = QGroupBox("Juego"); f = QFormLayout(g)
        self.c_nav = QComboBox()
        for k, t in NAV:
            self.c_nav.addItem(t, k)
        f.addRow("Navegación:", self.c_nav)
        self.ch_grass = QCheckBox("Hierba alta (colocada por la GPU)")
        self.s_grass = QDoubleSpinBox(); self.s_grass.setRange(0.4, 3.0); self.s_grass.setSingleStep(0.1); self.s_grass.setSuffix(" m entre matas")
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.addWidget(self.ch_grass); h.addWidget(self.s_grass)
        f.addRow("Hierba:", row)
        self.ch_preview = QCheckBox("Incluir Preview.tscn (sol, cielo y cámara para verlo al instante)")
        self.ch_verify = QCheckBox("Verificar en Godot al terminar (en una copia temporal; tarda un poco más)")
        f.addRow(self.ch_preview); f.addRow(self.ch_verify)
        lay.addWidget(g)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Exportar")
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.r_host.toggled.connect(self._sync); self.c_terrain.currentIndexChanged.connect(self._sync)
        self.c_mat.currentTextChanged.connect(self._sync); self.ch_grass.toggled.connect(self._sync)
        self.e_project.editingFinished.connect(self._detect)
        self._load()
        self._detect(initial=True)
        self._sync()

    # ---------------------------------------------------------------- helpers
    def _with_browse(self, edit: QLineEdit, fn) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(edit, 1)
        b = QPushButton("Elegir…"); b.clicked.connect(fn); h.addWidget(b)
        return w

    def _pick_project(self):
        d = QFileDialog.getExistingDirectory(self, "Proyecto de Godot (carpeta con project.godot)", self.e_project.text())
        if d:
            self.e_project.setText(d); self._detect()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.e_out.text())
        if d:
            self.e_out.setText(d)

    def _detect(self, initial: bool = False):
        """Read the chosen Godot project and offer what it has (Terrain3D, its materials, its water script)."""
        from exporters.godot.host_project import inspect_host_project
        p = self.e_project.text().strip()
        keep_mat, keep_water = self.c_mat.currentText(), self.c_water.currentText()
        self.c_mat.clear(); self.c_water.clear(); self.c_mat.addItem(""); self.c_water.addItem("")
        if not p or not (Path(p) / "project.godot").exists():
            self.lbl_detect.setText("" if not p else "⚠ Esa carpeta no tiene project.godot."); self.lbl_detect.setStyleSheet("color:#b45309")
            self.c_mat.setCurrentText(keep_mat); self.c_water.setCurrentText(keep_water)
            return
        info = inspect_host_project(p)
        for m in info["terrain3d_materials"]:
            self.c_mat.addItem(m["path"])
        for w in info["water_scripts"]:
            self.c_water.addItem(w)
        bits = [f"Proyecto «{info['project_name']}»"]
        bits.append(f"Terrain3D {info['terrain3d']['version']}" if info["terrain3d"]["installed"] else "sin Terrain3D")
        if info["terrain3d_materials"]:
            bits.append(f"{len(info['terrain3d_materials'])} material(es) de Terrain3D")
        if info["water_scripts"]:
            bits.append("script de agua " + Path(info["water_scripts"][0]).name)
        self.lbl_detect.setText("✓ " + " · ".join(bits)); self.lbl_detect.setStyleSheet("color:#047857")
        sug = info["suggestion"]
        if initial and keep_mat is not None and self.cfg.value("project") == p:
            self.c_mat.setCurrentText(keep_mat); self.c_water.setCurrentText(keep_water)     # the user's last choice for this project
        else:
            self.c_terrain.setCurrentIndex(self.c_terrain.findData(sug["terrain"]))
            self.c_mat.setCurrentText(sug.get("terrain3d_material", "")); self.c_water.setCurrentText(sug.get("water_script", ""))
        if not info["terrain3d"]["installed"] and self.c_terrain.currentData() == "terrain3d":
            self.c_terrain.setCurrentIndex(self.c_terrain.findData("mesh"))
        self._sync()

    def _sync(self, *_):
        host = self.r_host.isChecked()
        for w in (self.e_project, self.e_sub):
            w.setEnabled(host)
        self.e_out.setEnabled(not host)
        t3d = self.c_terrain.currentData() == "terrain3d"
        self.c_mat.setEnabled(t3d); self.s_path_id.setEnabled(t3d and bool(self.c_mat.currentText().strip()))
        self.s_grass.setEnabled(self.ch_grass.isChecked())

    def _load(self):
        c = self.cfg
        (self.r_host if c.value("mode", "host") == "host" else self.r_alone).setChecked(True)
        self.e_project.setText(c.value("project", "")); self.e_sub.setText(c.value("subfolder", "generated_worlds/mi_mundo"))
        self.e_out.setText(c.value("out", ""))
        self.c_terrain.setCurrentIndex(max(self.c_terrain.findData(c.value("terrain", "mesh")), 0))
        self.c_mat.setCurrentText(c.value("material", "")); self.c_water.setCurrentText(c.value("water", ""))
        self.s_path_id.setValue(int(c.value("path_id", 1)))
        self.c_profile.setCurrentIndex(max(self.c_profile.findData(c.value("profile", "full")), 0))
        self.c_nav.setCurrentIndex(max(self.c_nav.findData(c.value("navigation", "auto")), 0))
        self.ch_grass.setChecked(c.value("grass", "true") == "true"); self.s_grass.setValue(float(c.value("grass_spacing", 1.5)))
        self.ch_preview.setChecked(c.value("preview", "true") == "true"); self.ch_verify.setChecked(c.value("verify", "false") == "true")

    def _save(self):
        c = self.cfg
        c.setValue("mode", "host" if self.r_host.isChecked() else "alone"); c.setValue("project", self.e_project.text().strip())
        c.setValue("subfolder", self.e_sub.text().strip()); c.setValue("out", self.e_out.text().strip())
        c.setValue("terrain", self.c_terrain.currentData()); c.setValue("material", self.c_mat.currentText().strip())
        c.setValue("water", self.c_water.currentText().strip()); c.setValue("path_id", self.s_path_id.value())
        c.setValue("profile", self.c_profile.currentData()); c.setValue("navigation", self.c_nav.currentData())
        c.setValue("grass", "true" if self.ch_grass.isChecked() else "false"); c.setValue("grass_spacing", self.s_grass.value())
        c.setValue("preview", "true" if self.ch_preview.isChecked() else "false"); c.setValue("verify", "true" if self.ch_verify.isChecked() else "false")

    # ---------------------------------------------------------------- result
    def kwargs(self) -> dict:
        """Arguments for service.export_to_godot."""
        kw = dict(profile=self.c_profile.currentData(), include_preview=self.ch_preview.isChecked(), navigation=self.c_nav.currentData(),
                  grass=self.ch_grass.isChecked(), grass_spacing=self.s_grass.value(), verify=self.ch_verify.isChecked(),
                  terrain=self.c_terrain.currentData())
        if self.r_host.isChecked():
            kw.update(godot_project=self.e_project.text().strip(), subfolder=self.e_sub.text().strip().strip("/\\") or "GodotWorld")
        else:
            kw["path"] = self.e_out.text().strip()
        mat = self.c_mat.currentText().strip()
        if kw["terrain"] == "terrain3d" and mat:
            kw["terrain3d_material"] = mat
            pid = self.s_path_id.value()
            kw["terrain3d_texture_ids"] = {m: (pid if m == "dirt" else 0) for m in MATERIALS}
        water = self.c_water.currentText().strip()
        if water:
            kw["water_script"] = water
        return kw

    def _accept(self):
        k = self.kwargs()
        if "godot_project" in k and not (Path(k["godot_project"]) / "project.godot").exists():
            QMessageBox.warning(self, "Falta el proyecto", "Elige la carpeta de un proyecto de Godot (la que tiene project.godot)."); return
        if "path" in k and not k["path"]:
            QMessageBox.warning(self, "Falta la carpeta", "Elige una carpeta de destino."); return
        if (k.get("water_script") or k.get("terrain3d_material", "").startswith("res://")) and "godot_project" not in k:
            QMessageBox.warning(self, "Rutas del juego", "El material y el script de agua del juego solo sirven al exportar dentro de su proyecto."); return
        self._save()
        self.accept()


def run_export(parent, kwargs: dict, on_done) -> None:
    """Run service.export_to_godot as a background task with a progress dialog; on_done(result dict) when it finishes."""
    from core.world import service as S
    r = S.export_to_godot(background=True, **kwargs)
    if not r.get("ok"):
        on_done(r); return
    tid = r["task_id"]
    dlg = QProgressDialog("Exportando a Godot… (con navegación, Terrain3D o verificación puede tardar unos minutos)", None, 0, 0, parent)
    dlg.setWindowTitle("Exportar a Godot"); dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0); dlg.show()
    timer = QTimer(parent)

    def poll():
        st = S.get_task_status(tid)
        status = st.get("status") or st.get("task", {}).get("status")
        if status in ("completed", "failed", "cancelled"):
            timer.stop(); dlg.close()
            res = st.get("result") if status == "completed" else {"ok": False, "error": st.get("error") or status}
            on_done(res or {"ok": False, "error": "sin resultado"})
    timer.timeout.connect(poll); timer.start(400)
