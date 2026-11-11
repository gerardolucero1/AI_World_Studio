from __future__ import annotations

import time

import numpy as np
from OpenGL import GL as gl
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QNativeGestureEvent
from PySide6.QtWidgets import QLabel
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from core.history.history import History, Stroke
from core.history.operations import Operation
from core.world.session import WorldSession
from materials.materials import COLORS, paint_weights
from terrain.chunks.mesh import CHUNK_CELLS, build_chunk, chunk_grid, chunk_indices, dirty_chunks
from terrain.heightfield.heightfield import Heightfield, Rect
from terrain.sculpt.brushes import BrushParams, Tool, apply_dab, union
from terrain.sculpt.picking import pick
from ui.viewport.camera import OrbitCamera

VS = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec3 nrm;
uniform mat4 mvp; out vec3 n; out float h; out vec2 wp;
void main(){ n=nrm; h=pos.y; wp=pos.xz; gl_Position=mvp*vec4(pos,1.0); }"""
FS = """#version 330 core
in vec3 n; in float h; in vec2 wp; uniform vec3 ground; uniform vec2 worldSize;
uniform sampler2D paint0; uniform sampler2D paint1; uniform vec3 mats[8];
out vec4 c;
void main(){
  vec3 L=normalize(vec3(0.4,0.8,0.3)); float d=max(dot(normalize(n),L),0.0);
  float slope=1.0-clamp(n.y,0.0,1.0);
  vec3 grass=mats[0], rock=mats[2], snow=mats[3];
  vec3 base=mix(grass,rock,smoothstep(0.15,0.4,slope));
  base=mix(base,snow,smoothstep(ground.y*0.7,ground.y,h)*(1.0-slope));
  vec2 uv=wp/worldSize; vec4 p0=texture(paint0,uv), p1=texture(paint1,uv);
  float cov=p0.r+p0.g+p0.b+p0.a+p1.r+p1.g+p1.b+p1.a;
  if(cov>0.001){
    vec3 pc=(mats[0]*p0.r+mats[1]*p0.g+mats[2]*p0.b+mats[3]*p0.a+mats[4]*p1.r+mats[5]*p1.g+mats[6]*p1.b+mats[7]*p1.a)/cov;
    base=mix(base,pc,clamp(cov,0.0,1.0));
  }
  float amb=0.42+0.18*n.y; c=vec4(base*(amb+0.7*d),1.0);
}"""
VS_VEG = """#version 330 core
layout(location=0) in vec3 pos; layout(location=1) in vec3 nrm;
layout(location=2) in vec4 a; layout(location=3) in vec3 b;   // a=(x,y,z,yaw) b=(radius,height,species)
uniform mat4 mvp; uniform vec3 vcols[8]; out vec3 n; out vec3 col;
void main(){ n=nrm; col=vcols[int(b.z)%8];
  gl_Position=mvp*vec4(a.xyz+vec3(pos.x*b.x,pos.y*b.y,pos.z*b.x),1.0); }"""
FS_VEG = """#version 330 core
in vec3 n; in vec3 col; out vec4 c;
void main(){ float d=max(dot(normalize(n),normalize(vec3(0.4,0.8,0.3))),0.0); c=vec4(col*(0.45+0.65*d),1.0); }"""
VS_LINE = """#version 330 core
layout(location=0) in vec3 pos; uniform mat4 mvp;
void main(){ gl_Position=mvp*vec4(pos,1.0); gl_Position.z-=0.0005*gl_Position.w; }"""
FS_LINE = """#version 330 core
uniform vec3 col; out vec4 c; void main(){ c=vec4(col,1.0); }"""

RING_PTS = 96
VEG_COLORS = np.array([(.10, .35, .12), (.25, .5, .15), (.5, .48, .45), (.7, .55, .2),
                       (.2, .4, .25), (.35, .25, .15), (.6, .2, .3), (.3, .3, .5)], np.float32)


def _program(vs: str, fs: str) -> int:
    from OpenGL.GL import shaders
    return shaders.compileProgram(shaders.compileShader(vs, gl.GL_VERTEX_SHADER),
                                  shaders.compileShader(fs, gl.GL_FRAGMENT_SHADER),
                                  validate=False)  # core profile: validation needs a bound VAO


class TerrainViewport(QOpenGLWidget):
    changed = Signal()                  # terrain edited / history changed
    stats = Signal(str)
    placed = Signal(str, list)          # (mode, [(x, y) in meters]) when a placement/paint gesture finishes
    curve_edited = Signal(int, list)    # (operation index, new points) after dragging a handle in "edit_curve" mode
    brush_radius_changed = Signal(float)

    BRUSH_MODES = {"raise", "lower", "smooth", "flatten"}
    SINGLE_MODES = {"mountain", "cliff", "lake", "place"}
    MULTI_MODES = {"river", "path"}
    HINTS = {
        "navigate": "Mover vista · arrastra para girar · Mayús+arrastrar para desplazar",
        "raise": "Mantén clic izquierdo y arrastra para subir terreno · Ctrl = bajar",
        "lower": "Mantén clic izquierdo y arrastra para bajar terreno · Ctrl = subir",
        "smooth": "Arrastra para suavizar el relieve",
        "flatten": "El primer clic fija la altura; arrastra para aplanar a esa altura",
        "paint": "Arrastra para pintar el suelo con el material elegido",
        "mountain": "Haz clic donde quieras la montaña",
        "cliff": "Haz clic donde quieras la meseta con paredes de roca",
        "lake": "Haz clic donde quieras el lago",
        "place": "Haz clic para colocar el objeto elegido (árbol, roca…) · el aro muestra su tamaño",
        "river": "Clic para marcar el recorrido (de arriba hacia abajo) · doble clic o Intro para terminar · Esc cancela",
        "path": "Clic para marcar el sendero · doble clic o Intro para terminar · Esc cancela",
        "edit_curve": "Arrastra un punto blanco de un río, sendero o lago · al soltar se rehace todo lo que depende de él · Ctrl+Z deshace",
    }
    HANDLE_PX = 14                      # how close (screen pixels) a click must be to grab a handle

    def __init__(self, session: WorldSession, parent=None):
        super().__init__(parent)
        self.session = session   # same service layer the API/MCP use
        self.tool = Tool.RAISE
        self.mode = "navigate"
        self.marker_radius = 30.0
        self.show_features = True
        self.show_vegetation = True
        self.zoom_sensitivity = 4.0      # 1 (very gentle) .. 10
        self._drag: str | None = None    # 'orbit' | 'pan'
        self._multi: list[tuple[float, float]] = []
        self._paint_pts: list[tuple[float, float]] = []
        self.params = BrushParams()
        self.camera = OrbitCamera()
        self._keys: set[int] = set()
        self._last_mouse = QPoint()
        self._sculpting = False
        self._invert = False
        self._stroke: Stroke | None = None
        self._dabs: list = []              # dabs of the current brush stroke (logged as a replayable "brush" operation)
        self._cursor_pick = None
        self._chunks: dict[tuple[int, int], tuple[int, int, int, int]] = {}  # key -> (vao, vbo, count, ebo)
        self._ebos: dict[tuple[int, int], tuple[int, int]] = {}
        self._pending: set[tuple[int, int]] = set()
        self._geo: dict[int, tuple[int, int, int, int]] = {}   # id(mesh) -> (vao, vbo, ebo, count)
        self._geo_dirty = False
        self._veg_dirty = False
        self._paint_dirty = False
        self._veg_count = 0
        self._tex = (0, 0)
        self._feat_lines: list[tuple[tuple, np.ndarray]] = []  # (color, Nx3 points)
        self._handles: list[dict] = []    # editable curves: {op, key, closed, pts (N,2), idx (handle point indices)}
        self._grab: tuple[dict, int] | None = None   # (curve, point index) being dragged
        self._preview: list | None = None            # dragged curve, not applied yet
        self._gl_ready = False
        self._last_t = time.perf_counter()
        self._frame_ms = 0.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.frame_camera()
        self._hint = QLabel(self)
        self._hint.setStyleSheet("background: rgba(20,24,30,190); color: white; padding: 8px 12px; border-radius: 6px; font-size: 13px;")
        self._hint.move(12, 12)
        self._legend = QLabel("Clic der.: girar   ·   Rueda / pellizco: zoom   ·   Mayús+clic der.: desplazar   ·   Flechas: mover   ·   Inicio: centrar", self)
        self._legend.setStyleSheet("background: rgba(20,24,30,150); color: #dde; padding: 4px 10px; border-radius: 4px; font-size: 11px;")
        self.set_mode("navigate")

    # ---- public API -------------------------------------------------
    @property
    def hf(self) -> Heightfield:
        return self.session.hf

    @property
    def history(self) -> History:
        return self.session.history

    @property
    def op_log(self) -> list[Operation]:
        return self.session.op_log

    def reload(self) -> None:
        """Call after the session's world was replaced (new/open): re-upload everything."""
        ncx, ncy = chunk_grid(self.hf)
        self._pending = {(cx, cy) for cx in range(ncx) for cy in range(ncy)}
        self._ops_changed()
        self.frame_camera()
        self.changed.emit()

    def frame_camera(self) -> None:
        sx, sz = self.hf.size
        self.camera.frame(sx, sz, float(self.hf.data.mean()))

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode in self.BRUSH_MODES:
            self.tool = Tool(mode)
        self._multi.clear(); self._paint_pts.clear(); self._end_drag_and_stroke()
        self._hint.setText(self.HINTS.get(mode, ""))
        self._hint.adjustSize()
        self.setCursor(Qt.OpenHandCursor if mode == "navigate" else Qt.CrossCursor)

    def view_top(self) -> None:
        self.camera.top_view()

    def zoom_by(self, factor: float) -> None:
        self.camera.zoom(factor)

    def finish_multi(self) -> None:
        if self.mode in self.MULTI_MODES and len(self._multi) >= 2:
            pts = list(self._multi); self._multi.clear()
            self.placed.emit(self.mode, pts)
        elif self._multi:
            self._multi.clear()

    def run_operation(self, op: Operation) -> None:
        """Apply an operation as one undoable step (shared WorldSession logic)."""
        rect = self.session.apply(op)
        if rect:
            self._mark_dirty(rect)
        self._ops_changed()
        self.changed.emit()

    def _ops_changed(self) -> None:
        self._rebuild_feature_lines()
        self._geo_dirty = True
        self._veg_dirty = True
        self._paint_dirty = True

    def undo(self) -> None:
        self._after_history(self.session.undo())

    def redo(self) -> None:
        self._after_history(self.session.redo())

    def _after_history(self, rect: Rect | None) -> None:
        if rect:
            self._mark_dirty(rect)
        self._ops_changed()
        self.changed.emit()

    # ---- GL ---------------------------------------------------------
    def initializeGL(self) -> None:
        self._prog = _program(VS, FS)
        self._line = _program(VS_LINE, FS_LINE)
        self._ring_vao = gl.glGenVertexArrays(1)
        self._ring_vbo = gl.glGenBuffers(1)
        gl.glBindVertexArray(self._ring_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._ring_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, RING_PTS * 12, None, gl.GL_DYNAMIC_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 12, None)
        gl.glEnable(gl.GL_DEPTH_TEST)
        gl.glClearColor(0.55, 0.7, 0.85, 1.0)
        ncx, ncy = chunk_grid(self.hf)
        self._pending = {(cx, cy) for cx in range(ncx) for cy in range(ncy)}
        self._veg_prog = _program(VS_VEG, FS_VEG)
        self._init_veg_buffers()
        self._tex = tuple(gl.glGenTextures(2))
        for t in self._tex:
            gl.glBindTexture(gl.GL_TEXTURE_2D, t)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        self._paint_dirty = True
        self._veg_dirty = True
        self._feat_vao = gl.glGenVertexArrays(1)
        self._feat_vbo = gl.glGenBuffers(1)
        gl.glBindVertexArray(self._feat_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._feat_vbo)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 12, None)
        self._gl_ready = True

    def _upload_chunk(self, key: tuple[int, int]) -> None:
        verts = build_chunk(self.hf, *key)
        h, w = verts.shape[:2]
        buf = np.ascontiguousarray(verts.reshape(-1, 6))
        if key in self._chunks:
            vao, vbo, count, ebo = self._chunks[key]
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
            gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, buf.nbytes, buf)  # partial update, no realloc
            return
        if (w, h) not in self._ebos:
            idx = chunk_indices(w, h)
            ebo = gl.glGenBuffers(1)
            gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ebo)
            gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, gl.GL_STATIC_DRAW)
            self._ebos[(w, h)] = (ebo, len(idx))
        ebo, count = self._ebos[(w, h)]
        vao, vbo = gl.glGenVertexArrays(1), gl.glGenBuffers(1)
        gl.glBindVertexArray(vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, buf.nbytes, buf, gl.GL_DYNAMIC_DRAW)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 24, None)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, False, 24, gl.ctypes.c_void_p(12))
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ebo)
        self._chunks[key] = (vao, vbo, count, ebo)

    def _reset_chunks(self) -> None:
        for vao, vbo, _, _ in self._chunks.values():
            gl.glDeleteVertexArrays(1, [vao]); gl.glDeleteBuffers(1, [vbo])
        self._chunks.clear()

    def paintGL(self) -> None:
        t0 = time.perf_counter()
        ncx, ncy = chunk_grid(self.hf)
        if self._chunks and any(k[0] >= ncx or k[1] >= ncy for k in self._chunks):
            self._reset_chunks()
        for key in list(self._pending):
            self._upload_chunk(key)
        self._pending.clear()
        w, h = max(self.width(), 1), max(self.height(), 1)
        ratio = self.devicePixelRatioF()
        gl.glViewport(0, 0, int(w * ratio), int(h * ratio))
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        mvp = np.ascontiguousarray(self.camera.view_proj(w / h).T)
        gl.glUseProgram(self._prog)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._prog, "mvp"), 1, False, mvp)
        top = max(float(self.hf.data.max()), 1.0)
        gl.glUniform3f(gl.glGetUniformLocation(self._prog, "ground"), 0.0, top, 0.0)
        self._sync_paint()
        gl.glUseProgram(self._prog)
        sx, sz = self.hf.size
        gl.glUniform2f(gl.glGetUniformLocation(self._prog, "worldSize"), sx, sz)
        gl.glUniform3fv(gl.glGetUniformLocation(self._prog, "mats"), 8, np.ascontiguousarray(COLORS))
        for unit, t, name in ((0, self._tex[0], "paint0"), (1, self._tex[1], "paint1")):
            gl.glActiveTexture(gl.GL_TEXTURE0 + unit); gl.glBindTexture(gl.GL_TEXTURE_2D, t)
            gl.glUniform1i(gl.glGetUniformLocation(self._prog, name), unit)
        for vao, _, count, _ in self._chunks.values():
            gl.glBindVertexArray(vao)
            gl.glDrawElements(gl.GL_TRIANGLES, count, gl.GL_UNSIGNED_INT, None)
        self._sync_geometry()
        for vao, _, _, count in self._geo.values():
            gl.glBindVertexArray(vao)
            gl.glDrawElements(gl.GL_TRIANGLES, count, gl.GL_UNSIGNED_INT, None)
        self._sync_vegetation()
        if self._veg_count and self.show_vegetation:
            gl.glUseProgram(self._veg_prog)
            gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._veg_prog, "mvp"), 1, False, mvp)
            gl.glUniform3fv(gl.glGetUniformLocation(self._veg_prog, "vcols"), 8, np.ascontiguousarray(VEG_COLORS))
            gl.glBindVertexArray(self._veg_vao)
            gl.glDrawElementsInstanced(gl.GL_TRIANGLES, self._cone_indices, gl.GL_UNSIGNED_INT, None, self._veg_count)
        if self.show_features:
            self._draw_features(mvp)
        self._draw_multi(mvp)
        self._draw_handles(mvp)
        if self._cursor_pick and self._ring_radius():
            self._draw_ring(mvp)
        self._frame_ms = (time.perf_counter() - t0) * 1000

    def _init_veg_buffers(self) -> None:
        k = 8
        ang = np.linspace(0, 2 * np.pi, k, endpoint=False)
        ring = np.stack([np.cos(ang), np.zeros(k), np.sin(ang)], -1)
        verts = np.vstack([[0, 1, 0], ring]).astype(np.float32)
        nrm = np.vstack([[0, 1, 0], np.stack([np.cos(ang), np.full(k, 0.5), np.sin(ang)], -1)]).astype(np.float32)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        idx = np.array([[0, 1 + (i + 1) % k, 1 + i] for i in range(k)], np.uint32).reshape(-1)
        self._veg_vao = gl.glGenVertexArrays(1)
        gl.glBindVertexArray(self._veg_vao)
        mesh = np.ascontiguousarray(np.concatenate([verts, nrm], 1))
        vbo, ebo, self._veg_inst = gl.glGenBuffers(1), gl.glGenBuffers(1), gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo); gl.glBufferData(gl.GL_ARRAY_BUFFER, mesh.nbytes, mesh, gl.GL_STATIC_DRAW)
        gl.glEnableVertexAttribArray(0); gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 24, None)
        gl.glEnableVertexAttribArray(1); gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, False, 24, gl.ctypes.c_void_p(12))
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ebo); gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, gl.GL_STATIC_DRAW)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._veg_inst)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, 28, None, gl.GL_DYNAMIC_DRAW)
        gl.glEnableVertexAttribArray(2); gl.glVertexAttribPointer(2, 4, gl.GL_FLOAT, False, 28, None); gl.glVertexAttribDivisor(2, 1)
        gl.glEnableVertexAttribArray(3); gl.glVertexAttribPointer(3, 3, gl.GL_FLOAT, False, 28, gl.ctypes.c_void_p(16)); gl.glVertexAttribDivisor(3, 1)
        self._cone_indices = len(idx)

    def _sync_vegetation(self) -> None:
        if not self._veg_dirty:
            return
        self._veg_dirty = False
        arrs = [o.result["_instances"] for o in self.op_log if o.result and "_instances" in o.result]
        inst = np.ascontiguousarray(np.vstack(arrs) if arrs else np.zeros((0, 7), np.float32), np.float32)
        gl.glBindVertexArray(self._veg_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._veg_inst)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, max(inst.nbytes, 28), inst if len(inst) else None, gl.GL_DYNAMIC_DRAW)
        self._veg_count = len(inst)

    def _sync_paint(self) -> None:
        if not self._paint_dirty:
            return
        self._paint_dirty = False
        paint, _ = paint_weights(self.hf, self.op_log)
        for t, sl in zip(self._tex, (slice(0, 4), slice(4, 8))):
            data = np.ascontiguousarray((np.clip(paint[..., sl], 0, 1) * 255).astype(np.uint8))
            gl.glBindTexture(gl.GL_TEXTURE_2D, t)
            gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, self.hf.nx, self.hf.ny, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data)

    def _sync_geometry(self) -> None:
        """Upload independent geometry (cliff walls...) from the op log; drop buffers of removed meshes."""
        if not self._geo_dirty:
            return
        self._geo_dirty = False
        meshes = {id(o.result["_mesh"]): o.result["_mesh"] for o in self.op_log if o.result and "_mesh" in o.result}
        for key in [k for k in self._geo if k not in meshes]:
            vao, vbo, ebo, _ = self._geo.pop(key)
            gl.glDeleteVertexArrays(1, [vao]); gl.glDeleteBuffers(2, [vbo, ebo])
        for key, m in meshes.items():
            if key in self._geo:
                continue
            buf = np.ascontiguousarray(m.interleaved()); idx = np.ascontiguousarray(m.faces.reshape(-1), np.uint32)
            vao, vbo, ebo = gl.glGenVertexArrays(1), gl.glGenBuffers(1), gl.glGenBuffers(1)
            gl.glBindVertexArray(vao)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo); gl.glBufferData(gl.GL_ARRAY_BUFFER, buf.nbytes, buf, gl.GL_STATIC_DRAW)
            gl.glEnableVertexAttribArray(0); gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, 24, None)
            gl.glEnableVertexAttribArray(1); gl.glVertexAttribPointer(1, 3, gl.GL_FLOAT, False, 24, gl.ctypes.c_void_p(12))
            gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, ebo); gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, gl.GL_STATIC_DRAW)
            self._geo[key] = (vao, vbo, ebo, len(idx))

    def _rebuild_handles(self) -> None:
        from features.curves.edit import EDITABLE, handle_indices
        self._handles = []
        for i, op in enumerate(self.op_log):
            if op.kind in EDITABLE and op.enabled:
                key, closed = EDITABLE[op.kind]
                pts = op.params.get(key)
                if pts and len(pts) >= 2:
                    arr = np.asarray(pts, np.float64)
                    self._handles.append({"op": i, "key": key, "closed": closed, "pts": arr, "idx": handle_indices(arr, closed)})

    def _project(self, x: float, y: float, z: float):
        w, h = max(self.width(), 1), max(self.height(), 1)
        c = self.camera.view_proj(w / h) @ np.array([x, y, z, 1.0])
        if c[3] <= 1e-6:
            return None
        return ((c[0] / c[3] + 1) * 0.5 * w, (1 - c[1] / c[3]) * 0.5 * h)

    def _handle_at(self, pos: QPoint):
        best, bd = None, self.HANDLE_PX
        for cv in self._handles:
            for j in cv["idx"]:
                x, z = cv["pts"][j]
                sp = self._project(x, self.hf.sample(x, z) + 1.0, z)
                if sp is None:
                    continue
                d = float(np.hypot(sp[0] - pos.x(), sp[1] - pos.y()))
                if d < bd:
                    best, bd = (cv, j), d
        return best

    def _draw_handles(self, mvp) -> None:
        if self.mode != "edit_curve" or not self._handles:
            return
        gl.glUseProgram(self._line)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._line, "mvp"), 1, False, mvp)
        gl.glBindVertexArray(self._feat_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._feat_vbo)
        gl.glPointSize(11.0)
        for cv in self._handles:
            grabbed = self._grab is not None and self._grab[0] is cv and self._preview is not None
            pts = np.asarray(self._preview, np.float64) if grabbed else cv["pts"]
            if grabbed:                                   # the curve as it will be
                line = pts if not cv["closed"] else np.vstack([pts, pts[:1]])
                arr = np.ascontiguousarray([(x, self.hf.sample(x, z) + 0.8, z) for x, z in line], np.float32)
                gl.glUniform3f(gl.glGetUniformLocation(self._line, "col"), 1.0, 0.9, 0.1)
                gl.glBufferData(gl.GL_ARRAY_BUFFER, arr.nbytes, arr, gl.GL_STREAM_DRAW)
                gl.glDrawArrays(gl.GL_LINE_STRIP, 0, len(arr))
            hp = np.ascontiguousarray([(pts[j][0], self.hf.sample(*pts[j]) + 1.0, pts[j][1]) for j in cv["idx"]], np.float32)
            gl.glUniform3f(gl.glGetUniformLocation(self._line, "col"), *((1.0, 0.9, 0.1) if grabbed else (1.0, 1.0, 1.0)))
            gl.glBufferData(gl.GL_ARRAY_BUFFER, hp.nbytes, hp, gl.GL_STREAM_DRAW)
            gl.glDrawArrays(gl.GL_POINTS, 0, len(hp))

    def _rebuild_feature_lines(self) -> None:
        self._rebuild_handles()
        self._feat_lines = []
        for op in self.op_log:
            r = op.result
            if not r:
                continue
            if op.kind == "river":
                p = np.array(r["points"]); y = np.array(r["water_level"]) + 0.2
                self._feat_lines.append(((0.1, 0.4, 1.0), np.column_stack([p[:, 0], y, p[:, 1]]).astype(np.float32)))
            elif op.kind == "path":
                p = np.array(r["points"]); y = np.array(r["elevation"]) + 0.3
                self._feat_lines.append(((0.9, 0.7, 0.3), np.column_stack([p[:, 0], y, p[:, 1]]).astype(np.float32)))
            elif op.kind == "lake":
                from features.curves.curve import sample_curve
                poly = r["polygon"]; p = sample_curve(poly + [poly[0]], self.hf.cell_size * 2)
                y = np.full(len(p), r["level"] + 0.2)
                self._feat_lines.append(((0.2, 0.7, 1.0), np.column_stack([p[:, 0], y, p[:, 1]]).astype(np.float32)))

    def _draw_features(self, mvp) -> None:
        if not self._feat_lines:
            return
        gl.glUseProgram(self._line)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._line, "mvp"), 1, False, mvp)
        gl.glBindVertexArray(self._feat_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._feat_vbo)
        for col, pts in self._feat_lines:
            gl.glUniform3f(gl.glGetUniformLocation(self._line, "col"), *col)
            gl.glBufferData(gl.GL_ARRAY_BUFFER, pts.nbytes, pts, gl.GL_STREAM_DRAW)
            gl.glDrawArrays(gl.GL_LINE_STRIP, 0, len(pts))

    def apply_modify(self) -> None:
        """After an operation was edited and the world replayed: redraw everything."""
        self._mark_dirty((0, self.hf.ny, 0, self.hf.nx))
        self._ops_changed()
        self.changed.emit()

    def _ring_radius(self) -> float:
        if self.mode == "edit_curve":
            return 0.0
        if self.mode in self.BRUSH_MODES or self.mode == "paint":
            return self.params.radius
        if self.mode in self.SINGLE_MODES:
            return self.marker_radius
        return 0.0

    def _draw_multi(self, mvp) -> None:
        pts = list(self._multi)
        if self.mode not in self.MULTI_MODES or not pts:
            return
        if self._cursor_pick:
            pts.append((self._cursor_pick[0], self._cursor_pick[2]))
        dense = []
        for (x0, z0), (x1, z1) in zip(pts[:-1], pts[1:]):
            n = max(int(np.hypot(x1 - x0, z1 - z0) / (self.hf.cell_size * 2)), 1)
            for t in np.linspace(0, 1, n, endpoint=False):
                x, z = x0 + (x1 - x0) * t, z0 + (z1 - z0) * t
                dense.append((x, self.hf.sample(x, z) + 0.6, z))
        dense.append((pts[-1][0], self.hf.sample(*pts[-1]) + 0.6, pts[-1][1]))
        arr = np.ascontiguousarray(dense, np.float32)
        gl.glUseProgram(self._line)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._line, "mvp"), 1, False, mvp)
        gl.glUniform3f(gl.glGetUniformLocation(self._line, "col"), 1.0, 0.9, 0.1)
        gl.glBindVertexArray(self._feat_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._feat_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, arr.nbytes, arr, gl.GL_STREAM_DRAW)
        gl.glDrawArrays(gl.GL_LINE_STRIP, 0, len(arr))
        pm = np.ascontiguousarray([(x, self.hf.sample(x, z) + 1.0, z) for x, z in self._multi], np.float32)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, pm.nbytes, pm, gl.GL_STREAM_DRAW)
        gl.glPointSize(9.0)
        gl.glDrawArrays(gl.GL_POINTS, 0, len(pm))

    def _draw_ring(self, mvp) -> None:
        cx, _, cz = self._cursor_pick
        radius = self._ring_radius()
        a = np.linspace(0, 2 * np.pi, RING_PTS, endpoint=False)
        xs = np.clip(cx + np.cos(a) * radius, 0, self.hf.size[0])
        zs = np.clip(cz + np.sin(a) * radius, 0, self.hf.size[1])
        ys = np.array([self.hf.sample(x, z) + 0.3 for x, z in zip(xs, zs)])
        pts = np.stack([xs, ys, zs], -1).astype(np.float32)
        gl.glUseProgram(self._line)
        gl.glUniformMatrix4fv(gl.glGetUniformLocation(self._line, "mvp"), 1, False, mvp)
        gl.glUniform3f(gl.glGetUniformLocation(self._line, "col"), 1.0, 0.9, 0.1)
        gl.glBindVertexArray(self._ring_vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._ring_vbo)
        gl.glBufferSubData(gl.GL_ARRAY_BUFFER, 0, pts.nbytes, pts)
        gl.glDrawArrays(gl.GL_LINE_LOOP, 0, RING_PTS)

    # ---- editing ----------------------------------------------------
    def _mark_dirty(self, rect: Rect) -> None:
        self._pending |= dirty_chunks(rect, self.hf)

    def _pick_at(self, pos: QPoint):
        w, h = max(self.width(), 1), max(self.height(), 1)
        o, d = self.camera.ray(2 * pos.x() / w - 1, 1 - 2 * pos.y() / h, w / h)
        return pick(self.hf, o, d)

    def _dab(self) -> None:
        if not (self._sculpting and self._cursor_pick):
            return
        x, _, z = self._cursor_pick
        tool = self.tool
        if self._invert and tool in (Tool.RAISE, Tool.LOWER):
            tool = Tool.LOWER if tool is Tool.RAISE else Tool.RAISE
        self._stroke.before_edit(self.hf.world_to_rect(x, z, self.params.radius))
        self._dabs.append([tool.value, round(float(x), 3), round(float(z), 3)])
        rect = apply_dab(self.hf, tool, x, z, self.params)
        if rect:
            self._mark_dirty(rect)

    def _begin_stroke(self) -> None:
        self._stroke = Stroke(self.hf, self.tool.value)
        self._dabs = []
        if self.tool is Tool.FLATTEN and self._cursor_pick:
            self.params.target_height = self._cursor_pick[1]
        self._sculpting = True
        self._dab()

    def _end_stroke(self) -> None:
        if self._stroke:
            prm = self.params
            op = Operation("brush", dict(dabs=self._dabs, radius=prm.radius, strength=prm.strength, hardness=prm.hardness,
                                         target_height=prm.target_height, strength_scale=prm.strength_scale)) if self._dabs else None
            self.session.commit_stroke(self._stroke, op)      # logged (replayable) + sits vegetation back on the new ground
            self._stroke = None
            self._veg_dirty = True
            self.changed.emit()
        self._sculpting = False

    # ---- input ------------------------------------------------------
    def _end_drag_and_stroke(self) -> None:
        self._drag = None
        if self._sculpting:
            self._end_stroke()

    def _tick(self) -> None:
        now = time.perf_counter(); dt = min(now - self._last_t, 0.1); self._last_t = now
        k = self._keys
        f = (Qt.Key_Up in k or Qt.Key_W in k) - (Qt.Key_Down in k or Qt.Key_S in k)
        r = (Qt.Key_Right in k or Qt.Key_D in k) - (Qt.Key_Left in k or Qt.Key_A in k)
        if f or r:
            self.camera.move_flat(f, r, self.camera.distance * 0.7 * dt * (3.0 if Qt.Key_Shift in k else 1.0))
        rot = (Qt.Key_E in k) - (Qt.Key_Q in k)
        if rot:
            self.camera.yaw = (self.camera.yaw + rot * 60 * dt) % 360
        zoom = (Qt.Key_Minus in k) - (Qt.Key_Plus in k or Qt.Key_Equal in k)
        if zoom:
            self.camera.zoom(1.0 + zoom * 1.2 * dt)
        tx, tz = self.camera.pos[0], self.camera.pos[2]
        sx, sz = self.hf.size
        if 0 <= tx <= sx and 0 <= tz <= sz:
            self.camera.keep_above(self.hf.sample(float(tx), float(tz)))
        self._dab()
        self.update()
        self.stats.emit(f"{self._frame_ms:.1f} ms/frame  ·  malla {self.hf.nx}×{self.hf.ny}")

    def event(self, e) -> bool:
        if isinstance(e, QNativeGestureEvent) and e.gestureType() == Qt.ZoomNativeGesture:   # trackpad pinch
            self._zoom_at(e.position().toPoint(), 1.0 - e.value() * 1.2)
            return True
        return super().event(e)

    def _zoom_at(self, pos: QPoint, factor: float) -> None:
        hit = self._pick_at(pos)
        if hit:
            self.camera.retarget(hit)           # zoom towards the point under the cursor
        self.camera.zoom(float(np.clip(factor, 0.5, 2.0)))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self.setFocus()
        p = e.position().toPoint()
        self._last_mouse = p
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        b = e.button()
        if b == Qt.MiddleButton or (b == Qt.RightButton and shift):
            self._drag = "pan"
        elif b == Qt.RightButton:
            self._drag = "orbit"
        elif b == Qt.LeftButton:
            if self.mode == "navigate":
                self._drag = "pan" if shift else "orbit"
            elif self.mode in self.BRUSH_MODES:
                self._invert = bool(e.modifiers() & Qt.ControlModifier)
                self._cursor_pick = self._pick_at(p)
                self._begin_stroke()
            elif self.mode == "paint":
                self._cursor_pick = self._pick_at(p)
                if self._cursor_pick:
                    self._paint_pts = [(self._cursor_pick[0], self._cursor_pick[2])]
            elif self.mode in self.SINGLE_MODES:
                hit = self._pick_at(p)
                if hit:
                    self.placed.emit(self.mode, [(hit[0], hit[2])])
            elif self.mode in self.MULTI_MODES:
                hit = self._pick_at(p)
                if hit:
                    self._multi.append((hit[0], hit[2]))
            elif self.mode == "edit_curve":
                self._grab = self._handle_at(p)
                self._preview = None
                if self._grab is None:
                    self._drag = "pan" if shift else "orbit"      # not on a handle: move the view as usual
        if self._drag == "orbit":
            hit = self._pick_at(p)
            if hit:
                self.camera.retarget(hit)       # orbit around what is under the cursor
            self.setCursor(Qt.ClosedHandCursor)
        elif self._drag == "pan":
            self.setCursor(Qt.SizeAllCursor)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and self.mode in self.MULTI_MODES:
            self.finish_multi()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        p = e.position().toPoint()
        dx, dy = p.x() - self._last_mouse.x(), p.y() - self._last_mouse.y()
        if self._drag == "orbit":
            self.camera.orbit(dx, dy)
        elif self._drag == "pan":
            self.camera.pan(dx, dy, self.height())
        elif self._grab is not None:
            from features.curves.edit import drag_points
            hit = self._pick_at(p)
            if hit:
                cv, j = self._grab
                self._preview = drag_points(cv["pts"], j, (hit[0], hit[2]), cv["closed"])
        else:
            self._cursor_pick = self._pick_at(p)
            if self.mode == "paint" and self._paint_pts and self._cursor_pick:
                lx, lz = self._paint_pts[-1]
                if np.hypot(self._cursor_pick[0] - lx, self._cursor_pick[2] - lz) > self.params.radius * 0.4:
                    self._paint_pts.append((self._cursor_pick[0], self._cursor_pick[2]))
        self._last_mouse = p

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        b = e.button()
        if b == Qt.LeftButton and self._grab is not None:
            cv, _ = self._grab
            pts, self._grab, self._preview = self._preview, None, None
            if pts is not None:
                self.curve_edited.emit(cv["op"], pts)
        if self._drag and (b in (Qt.RightButton, Qt.MiddleButton) or (b == Qt.LeftButton and self.mode in ("navigate", "edit_curve"))):
            self._drag = None
            self.setCursor(Qt.OpenHandCursor if self.mode == "navigate" else Qt.CrossCursor)
        if b == Qt.LeftButton:
            if self._sculpting:
                self._end_stroke()
            if self.mode == "paint" and self._paint_pts:
                pts = list(self._paint_pts); self._paint_pts.clear()
                self.placed.emit("paint", pts)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta().y() or e.angleDelta().x()
        if not d:
            return
        if e.modifiers() & Qt.AltModifier and (self.mode in self.BRUSH_MODES or self.mode == "paint"):
            self.brush_radius_changed.emit(float(np.clip(self.params.radius * (1.08 if d > 0 else 0.92), 1, 2000)))
            return
        d = float(np.clip(d, -240, 240))                 # a notch is 120; cap trackpad bursts
        self._zoom_at(e.position().toPoint(), float(np.exp(-d / 120.0 * self.zoom_sensitivity * 0.02)))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._legend.adjustSize()
        self._legend.move(12, self.height() - self._legend.height() - 12)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_multi()
        elif e.key() == Qt.Key_Escape:
            self._multi.clear(); self._paint_pts.clear()
        elif e.key() == Qt.Key_Backspace and self._multi:
            self._multi.pop()
        elif e.key() == Qt.Key_Home:
            self.frame_camera()
        if not e.isAutoRepeat():
            self._keys.add(e.key())

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if not e.isAutoRepeat():
            self._keys.discard(e.key())
