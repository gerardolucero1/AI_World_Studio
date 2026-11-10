from __future__ import annotations

import numpy as np


def perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / np.tan(np.radians(fov_deg) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def look_at(eye, fwd, up=(0, 1, 0)) -> np.ndarray:
    f = fwd / np.linalg.norm(fwd)
    s = np.cross(f, up); s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


class OrbitCamera:
    """Map-style camera: orbits a target point, pans on the ground plane, zooms along the view direction."""
    PITCH_MIN, PITCH_MAX = -89.5, -4.0

    def __init__(self):
        self.target = np.array([0.0, 0.0, 0.0], np.float64)
        self.distance = 500.0
        self.yaw, self.pitch = 90.0, -45.0
        self.fov = 50.0
        self.min_dist, self.max_dist = 3.0, 50000.0

    # --- basis ---
    @property
    def forward(self) -> np.ndarray:
        y, p = np.radians(self.yaw), np.radians(self.pitch)
        return np.array([np.cos(p) * np.cos(y), np.sin(p), np.cos(p) * np.sin(y)])

    @property
    def right(self) -> np.ndarray:
        r = np.cross(self.forward, (0, 1, 0)); return r / np.linalg.norm(r)

    @property
    def up(self) -> np.ndarray:
        return np.cross(self.right, self.forward)

    @property
    def flat_forward(self) -> np.ndarray:
        y = np.radians(self.yaw); return np.array([np.cos(y), 0.0, np.sin(y)])

    @property
    def pos(self) -> np.ndarray:
        return self.target - self.forward * self.distance

    # --- navigation ---
    def orbit(self, dx: float, dy: float) -> None:
        self.yaw = (self.yaw + dx * 0.3) % 360
        self.pitch = float(np.clip(self.pitch - dy * 0.3, self.PITCH_MIN, self.PITCH_MAX))

    def pan(self, dx: float, dy: float, viewport_h: float) -> None:
        """Drag the map with the cursor (ground-plane pan)."""
        per_px = 2 * self.distance * np.tan(np.radians(self.fov) / 2) / max(viewport_h, 1)
        k = per_px / max(np.sin(np.radians(-self.pitch)), 0.25)
        self.target = self.target - self.right * dx * per_px + self.flat_forward * dy * k

    def move_flat(self, forward: float, right: float, amount: float) -> None:
        self.target = self.target + (self.flat_forward * forward + self.right * right) * amount

    def zoom(self, factor: float) -> None:
        self.distance = float(np.clip(self.distance * factor, self.min_dist, self.max_dist))

    def retarget(self, point) -> None:
        """Make `point` the orbit/zoom centre WITHOUT moving the camera (view stays identical)."""
        p = np.asarray(point, np.float64)
        d = p - self.pos
        dist = float(np.linalg.norm(d))
        if dist < 1e-6:
            return
        self.distance = dist
        self.yaw = float(np.degrees(np.arctan2(d[2], d[0]))) % 360
        self.pitch = float(np.clip(np.degrees(np.arcsin(d[1] / dist)), self.PITCH_MIN, self.PITCH_MAX))
        self.target = p

    def frame(self, size_x: float, size_z: float, ground_y: float = 0.0) -> None:
        self.target = np.array([size_x / 2, ground_y, size_z / 2], np.float64)
        self.distance = max(size_x, size_z) * 1.15
        self.yaw, self.pitch = 90.0, -45.0
        self.min_dist, self.max_dist = 3.0, max(size_x, size_z) * 6

    def top_view(self) -> None:
        self.pitch = self.PITCH_MIN

    def keep_above(self, ground_y: float, clearance: float = 2.0) -> None:
        if self.pos[1] < ground_y + clearance:
            self.target[1] += ground_y + clearance - self.pos[1]

    # --- matrices ---
    def view_proj(self, aspect: float) -> np.ndarray:
        near = max(0.2, self.distance * 0.02)
        far = self.distance * 12 + 2000
        return perspective(self.fov, aspect, near, far) @ look_at(self.pos.astype(np.float32), self.forward)

    def ray(self, ndc_x: float, ndc_y: float, aspect: float):
        inv = np.linalg.inv(self.view_proj(aspect))
        p = inv @ np.array([ndc_x, ndc_y, 1.0, 1.0]); p = p[:3] / p[3]
        o = self.pos
        d = p - o
        return o.astype(np.float32), (d / np.linalg.norm(d)).astype(np.float32)
