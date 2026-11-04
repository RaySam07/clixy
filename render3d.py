"""Real 3D on the note card: model3d meshes drawn on the GPU.

``RealModel`` is what the note card uses for a 3D note (it has the same face as
scene3d.NoteModel, which stays as the fallback on a PC without OpenGL 3.3). The
model is built off the Qt thread (model3d), then drawn each frame in an
offscreen OpenGL context (moderngl): physically based materials (wet flesh,
metal, glass, plastic...), a studio of soft lights, soft shadows, ambient
occlusion from the model's own shape, fine surface grain, 8x multisampling and
filmic tone mapping. The frame comes back as an image the card paints, with the
labels on leader lines beside it, so the card itself is plain QPainter.

Drag the model to turn it; it rocks slowly by itself until it comes to rest.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen, QPolygonF, QRadialGradient

import model3d
import theme

try:
    import moderngl
except ImportError:            # an install without it: the older painter's 3D instead
    moderngl = None

_POISSON = """
const vec2 POISSON[16] = vec2[](
  vec2(-0.94201624,-0.39906216), vec2(0.94558609,-0.76890725), vec2(-0.09418410,-0.92938870),
  vec2(0.34495938,0.29387760), vec2(-0.91588581,0.45771432), vec2(-0.81544232,-0.87912464),
  vec2(-0.38277543,0.27676845), vec2(0.97484398,0.75648379), vec2(0.44323325,-0.97511554),
  vec2(0.53742981,-0.47373420), vec2(-0.26496911,-0.41893023), vec2(0.79197514,0.19090188),
  vec2(-0.24188840,0.99706507), vec2(-0.81409955,0.91437590), vec2(0.19984126,0.78641367),
  vec2(0.14383161,-0.14100790));
"""

_VERT = """
#version 330
uniform mat4 u_model;
uniform mat4 u_vp;
uniform mat4 u_light;
in vec3 in_pos; in vec3 in_nrm; in vec3 in_col; in float in_ao; in float in_cut;
out vec3 v_pos; out vec3 v_obj; out vec3 v_nrm; out vec3 v_col; out float v_ao; out float v_cut; out vec4 v_lp;
void main() {
  vec4 w = u_model * vec4(in_pos, 1.0);
  v_pos = w.xyz; v_obj = in_pos;
  v_nrm = normalize(mat3(u_model) * in_nrm);
  v_col = in_col; v_ao = in_ao; v_cut = in_cut;
  v_lp = u_light * w;
  gl_Position = u_vp * w;
}
"""

_FRAG = """
#version 330
uniform vec3 u_eye;
uniform mat3 u_vrot;                       // world -> view, for the studio around the camera
uniform mat4 u_model;
uniform vec3 u_key; uniform vec3 u_fill; uniform vec3 u_rim;
uniform float u_rough, u_metal, u_sss, u_coat, u_bump, u_bumpscale, u_emit, u_opacity, u_dim, u_exposure;
uniform sampler2DShadow u_shadow;
uniform float u_soft;
uniform mat4 u_light;
uniform float u_offset;
in vec3 v_pos; in vec3 v_obj; in vec3 v_nrm; in vec3 v_col; in float v_ao; in float v_cut; in vec4 v_lp;
out vec4 f_color;
""" + _POISSON + """
float hash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise(vec3 x) {
  vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float fbm(vec3 p) { return 0.6 * noise(p) + 0.3 * noise(p * 2.03) + 0.1 * noise(p * 4.1); }
vec3 lin(vec3 c) { return pow(c, vec3(2.2)); }

// A photo studio around the camera: a bright ceiling, a darker floor, a big soft key box
// up-left, a strip light right. Reflections in glossy and metal parts show it.
vec3 studio(vec3 d, float r) {
  vec3 c = mix(vec3(0.20, 0.18, 0.165), vec3(0.95, 0.94, 0.92), smoothstep(-0.45, 0.75, d.y));
  float w = r * 0.55;
  c += vec3(3.2, 3.1, 2.95) * smoothstep(0.83 - w, 0.93 - w * 0.4, dot(d, normalize(vec3(-0.55, 0.62, 0.55)))) * (1.0 - 0.55 * r);
  c += vec3(1.5, 1.6, 1.75) * smoothstep(0.90 - w, 0.97 - w * 0.4, dot(d, normalize(vec3(0.9, 0.2, 0.25)))) * (1.0 - 0.55 * r);
  c += vec3(1.1) * smoothstep(0.86 - w, 0.95 - w * 0.4, dot(d, normalize(vec3(0.1, 0.35, -0.95)))) * (1.0 - 0.55 * r);
  return c;
}

mat2 spin() { float a = 6.2831853 * fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))); return mat2(cos(a), -sin(a), sin(a), cos(a)); }
float shadow(vec3 N) {
  vec4 lp = u_light * vec4(v_pos + N * u_offset * (1.5 - clamp(dot(N, u_key), 0.0, 1.0)), 1.0);
  vec3 p = lp.xyz / lp.w * 0.5 + 0.5;
  if (p.x <= 0.0 || p.x >= 1.0 || p.y <= 0.0 || p.y >= 1.0 || p.z >= 1.0) return 1.0;
  float bias = 0.0006;
  float s = 0.0;
  mat2 r = spin();
  for (int i = 0; i < 16; i++) s += texture(u_shadow, vec3(p.xy + r * POISSON[i] * u_soft, p.z - bias));
  return s / 16.0;
}

float ggx(float NH, float a) { float a2 = a * a; float d = NH * NH * (a2 - 1.0) + 1.0; return a2 / (3.14159 * d * d); }
float smith(float NV, float NL, float r) { float k = (r + 1.0) * (r + 1.0) / 8.0; return (NV / (NV * (1.0 - k) + k)) * (NL / (NL * (1.0 - k) + k)); }

vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }

void main() {
  vec3 N = normalize(v_nrm);
  vec3 V = normalize(u_eye - v_pos);
  if (dot(N, V) < -0.2) N = -N;
  float bump = v_cut > 0.5 ? max(u_bump, 0.3) : u_bump;
  if (bump > 0.0) {             // fine grain: tissue, stone, wood
    vec3 q = v_obj * u_bumpscale;
    float e = 0.12;
    float n0 = fbm(q);
    vec3 g = vec3(fbm(q + vec3(e, 0, 0)) - n0, fbm(q + vec3(0, e, 0)) - n0, fbm(q + vec3(0, 0, e)) - n0) / e;
    g = mat3(u_model) * g;
    N = normalize(N - bump * 0.22 * (g - dot(g, N) * N));
  }
  vec3 base = lin(v_col);
  float rough = u_rough, metal = u_metal, coat = u_coat, sss = u_sss;
  if (v_cut > 0.5) { rough = max(rough, 0.6); metal = 0.0; coat *= 0.3; base *= 0.95; }
  if (u_dim > 0.5) { float l = dot(base, vec3(0.3, 0.59, 0.11)); base = mix(vec3(l), base, 0.25) * 0.75 + 0.12; }
  float ao = v_ao;
  float NV = max(dot(N, V), 1e-3);
  vec3 F0 = mix(vec3(0.04), base, metal);
  vec3 diffc = base * (1.0 - metal);
  vec3 tint = clamp(pow(max(base, 1e-4), vec3(0.55)) * 1.35, 0.0, 1.0);   // light that passes under the surface takes its colour
  float sh = shadow(N);
  vec3 col = vec3(0.0);
  vec3 Ls[3] = vec3[](u_key, u_fill, u_rim);
  vec3 Cs[3] = vec3[](vec3(3.1, 2.95, 2.75) , vec3(0.75, 0.82, 0.95), vec3(1.6, 1.6, 1.65));
  for (int i = 0; i < 3; i++) {
    vec3 L = Ls[i];
    float NL = dot(N, L);
    float vis = i == 0 ? sh : 1.0;
    float wrap = sss * 0.6;
    float lambert = max(NL, 0.0);
    float wrapped = max((NL + wrap) / (1.0 + wrap), 0.0);
    vec3 diff = diffc * (lambert * vis + sss * tint * max(wrapped - lambert * vis, 0.0) * (0.55 + 0.45 * vis));
    vec3 H = normalize(L + V);
    float NH = max(dot(N, H), 0.0), NLc = max(NL, 0.0), VH = max(dot(V, H), 0.0);
    float r = max(rough, 0.045);
    vec3 Fr = F0 + (1.0 - F0) * pow(1.0 - VH, 5.0);
    vec3 spec = ggx(NH, r * r) * smith(NV, NLc, r) * Fr / (4.0 * NV * max(NLc, 1e-3) + 1e-4) * NLc;
    float cspec = coat * ggx(NH, 0.09 * 0.09) * smith(NV, NLc, 0.09) * (0.04 + 0.96 * pow(1.0 - VH, 5.0)) / (4.0 * NV * max(NLc, 1e-3) + 1e-4) * NLc;
    col += (diff * (1.0 - Fr * 0.5) + spec + cspec) * Cs[i] * vis * (0.65 + 0.35 * ao);
  }
  // Light from the whole room: soft and blocked in creases.
  vec3 Nv = u_vrot * N;
  vec3 room = mix(vec3(0.13, 0.115, 0.105), vec3(0.55, 0.56, 0.6), Nv.y * 0.5 + 0.5);
  col += diffc * room * pow(ao, 1.4) * (1.0 + 0.25 * sss);
  vec3 R = u_vrot * reflect(-V, N);
  vec3 Fa = F0 + (max(vec3(1.0 - rough), F0) - F0) * pow(1.0 - NV, 5.0);
  col += studio(R, rough) * Fa * (metal > 0.5 ? 0.9 : 0.55) * pow(ao, 1.8);
  col += coat * studio(R, 0.05) * (0.04 + 0.96 * pow(1.0 - NV, 5.0)) * 0.45 * ao;
  col += base * u_emit * 2.2;
  col = aces(col * u_exposure);
  col = pow(col, vec3(1.0 / 2.2));
  float a = u_opacity;
  if (a < 0.999) a = clamp(a + 0.6 * pow(1.0 - NV, 3.0), 0.0, 1.0);
  f_color = vec4(col * a, a);
}
"""

_DEPTH_VERT = """
#version 330
uniform mat4 u_model; uniform mat4 u_vp;
in vec3 in_pos;
void main() { gl_Position = u_vp * u_model * vec4(in_pos, 1.0); }
"""
_DEPTH_FRAG = """
#version 330
void main() {}
"""
_ID_FRAG = """
#version 330
uniform float u_id;
out vec4 f_color;
void main() { f_color = vec4(u_id, 0.0, 0.0, 1.0); }
"""

_GROUND_VERT = """
#version 330
uniform mat4 u_vp; uniform mat4 u_light; uniform mat4 u_top;
in vec3 in_pos;
out vec4 v_lp; out vec4 v_tp; out vec3 v_pos;
void main() { v_pos = in_pos; v_lp = u_light * vec4(in_pos, 1.0); v_tp = u_top * vec4(in_pos, 1.0); gl_Position = u_vp * vec4(in_pos, 1.0); }
"""
_GROUND_FRAG = """
#version 330
uniform sampler2DShadow u_shadow;
uniform sampler2D u_topdepth;
uniform float u_soft; uniform float u_range; uniform float u_reach; uniform vec3 u_centre; uniform float u_strength;
in vec4 v_lp; in vec4 v_tp; in vec3 v_pos;
out vec4 f_color;
""" + _POISSON + """
mat2 spin() { float a = 6.2831853 * fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))); return mat2(cos(a), -sin(a), sin(a), cos(a)); }
void main() {
  mat2 r = spin();
  vec3 p = v_lp.xyz / v_lp.w * 0.5 + 0.5;
  float lit = 1.0;
  if (p.x > 0.0 && p.x < 1.0 && p.y > 0.0 && p.y < 1.0 && p.z < 1.0) {
    lit = 0.0;
    for (int i = 0; i < 16; i++) lit += texture(u_shadow, vec3(p.xy + r * POISSON[i] * u_soft * 2.5, p.z - 0.002));
    lit /= 16.0;
  }
  // Contact: how close the model comes above this spot (dark right under it).
  vec3 t = v_tp.xyz / v_tp.w * 0.5 + 0.5;
  float contact = 0.0;
  for (int i = 0; i < 16; i++) {
    float d = texture(u_topdepth, t.xy + r * POISSON[i] * 0.022).r;
    float height = (1.0 - d) * u_range;        // depth 1 = nothing above
    contact += d < 0.9999 ? 1.0 - smoothstep(0.0, 0.35 * u_reach, height) : 0.0;
  }
  contact /= 16.0;
  float fade = 1.0 - smoothstep(0.55 * u_reach, 1.25 * u_reach, length(v_pos.xy - u_centre.xy));
  float a = clamp((0.30 * (1.0 - lit) + 0.42 * contact) * fade * u_strength, 0.0, 0.6);
  f_color = vec4(vec3(0.17, 0.12, 0.08) * a, a);
}
"""


# --- Matrices ---------------------------------------------------------------------------------

def look_at(eye, target, up) -> np.ndarray:
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-6:
        s = np.cross(f, np.array([0.0, 1.0, 0.0]))
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def perspective(fovy: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    m = np.zeros((4, 4))
    m[0, 0], m[1, 1] = f / aspect, f
    m[2, 2], m[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def ortho(half_w: float, half_h: float, near: float, far: float) -> np.ndarray:
    m = np.eye(4)
    m[0, 0], m[1, 1] = 1 / half_w, 1 / half_h
    m[2, 2], m[2, 3] = -2 / (far - near), -(far + near) / (far - near)
    return m


def _gl(m: np.ndarray) -> bytes:
    return np.asarray(m, dtype="f4").T.tobytes()


# --- The GPU ----------------------------------------------------------------------------------

class GPU:
    """One offscreen OpenGL context for the app (None on a PC that can't make one)."""

    _ctx = None
    _failed = False
    programs: dict = {}

    @classmethod
    def get(cls):
        if cls._ctx is not None or cls._failed or moderngl is None:
            return cls._ctx
        try:
            ctx = moderngl.create_standalone_context(require=330)
            cls.programs = {
                "main": ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG),
                "depth": ctx.program(vertex_shader=_DEPTH_VERT, fragment_shader=_DEPTH_FRAG),
                "id": ctx.program(vertex_shader=_DEPTH_VERT, fragment_shader=_ID_FRAG),
                "ground": ctx.program(vertex_shader=_GROUND_VERT, fragment_shader=_GROUND_FRAG),
            }
            cls._ctx = ctx
        except Exception:      # no OpenGL 3.3 (an old or remote PC): the painter's 3D instead
            cls._failed = True
            cls._ctx = None
        return cls._ctx


class Renderer:
    """A built model on the GPU, drawn from any angle at any time."""

    SAMPLES = 8
    SHADOW = 2048
    EXPOSURE = 0.74

    def __init__(self, model: model3d.Model, ground: bool = True) -> None:
        ctx = GPU.get()
        if ctx is None:
            raise RuntimeError("no OpenGL")
        self.ctx = ctx
        self.model = model
        self.meshes = model.meshes
        P = GPU.programs
        self._items = []
        for m in self.meshes:
            data = np.hstack([m.pos, m.nrm, m.col, m.ao[:, None], m.cut[:, None]]).astype("f4")
            vbo = ctx.buffer(data.tobytes())
            ibo = ctx.buffer(m.tri.astype("u4").tobytes())
            fmt = (vbo, "3f 3f 3f 1f 1f", "in_pos", "in_nrm", "in_col", "in_ao", "in_cut")
            vao = ctx.vertex_array(P["main"], [fmt], index_buffer=ibo)
            dvao = ctx.vertex_array(P["depth"], [(vbo, "3f 32x", "in_pos")], index_buffer=ibo)
            ivao = ctx.vertex_array(P["id"], [(vbo, "3f 32x", "in_pos")], index_buffer=ibo)
            self._items.append((m, vbo, ibo, vao, dvao, ivao))
        allp = np.vstack([m.pos for m in self.meshes]) if self.meshes else np.zeros((1, 3), "f4")
        self.lo, self.hi = allp.min(0).astype(float), allp.max(0).astype(float)
        self.centre = (self.lo + self.hi) / 2
        self.radius = float(np.linalg.norm(self.hi - self.lo)) / 2 or 1.0
        self._sample = allp[:: max(1, len(allp) // 4000)].astype(float)
        self.ground = ground
        g = self.radius * 3.0
        z = self.lo[2] - self.radius * 0.004
        c = self.centre
        quad = np.array([[c[0] - g, c[1] - g, z], [c[0] + g, c[1] - g, z], [c[0] + g, c[1] + g, z],
                         [c[0] - g, c[1] - g, z], [c[0] + g, c[1] + g, z], [c[0] - g, c[1] + g, z]], "f4")
        self._gvbo = ctx.buffer(quad.tobytes())
        self._gvao = ctx.vertex_array(P["ground"], [(self._gvbo, "3f", "in_pos")])
        self._gdvao = ctx.vertex_array(P["depth"], [(self._gvbo, "3f", "in_pos")])
        self.shadow_tex = ctx.depth_texture((self.SHADOW, self.SHADOW))
        self.shadow_tex.compare_func = "<="
        self.shadow_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.shadow_fbo = ctx.framebuffer(depth_attachment=self.shadow_tex)
        self.top_tex = ctx.depth_texture((512, 512))
        self.top_tex.compare_func = ""
        self.top_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.top_tex.repeat_x = self.top_tex.repeat_y = False
        self.shadow_tex.repeat_x = self.shadow_tex.repeat_y = False
        self.top_fbo = ctx.framebuffer(depth_attachment=self.top_tex)
        self._size = None
        self._fbo = self._resolve = None

    def release(self) -> None:
        for _m, vbo, ibo, vao, dvao, ivao in self._items:
            for o in (vao, dvao, ivao, vbo, ibo):
                o.release()
        for o in (self._gvao, self._gdvao, self._gvbo, self.shadow_fbo, self.shadow_tex, self.top_fbo, self.top_tex):
            o.release()
        self._free_targets()
        self._items = []

    def _free_targets(self) -> None:
        for o in (self._fbo, self._resolve):
            if o is not None:
                for a in list(o.color_attachments) + ([o.depth_attachment] if o.depth_attachment else []):
                    a.release()
                o.release()
        self._fbo = self._resolve = None

    def _targets(self, w: int, h: int) -> None:
        if self._size == (w, h):
            return
        self._free_targets()
        ctx = self.ctx
        samples = min(self.SAMPLES, ctx.max_samples)
        self._fbo = ctx.framebuffer(color_attachments=[ctx.renderbuffer((w, h), 4, samples=samples)],
                                    depth_attachment=ctx.depth_renderbuffer((w, h), samples=samples))
        self._resolve = ctx.framebuffer(color_attachments=[ctx.renderbuffer((w, h), 4)])
        self._size = (w, h)

    # --- Camera -------------------------------------------------------------------------------
    def camera(self, az: float, el: float, aspect: float, zoom: float = 1.0, fit=None, stage=None):
        """View and projection for a turn (az) and tilt (el), in degrees; ``fit`` is the
        (scale, shift x, shift y) that frames the model, from ``frame``."""
        a, e = math.radians(az), math.radians(el)
        d = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
        dist = self.radius * 4.2
        eye = self.centre + d * dist
        view = look_at(eye, self.centre, np.array([0.0, 0.0, 1.0]))
        proj = perspective(26.0, aspect, dist - self.radius * 2.2, dist + self.radius * 2.6)
        if fit is not None:
            s, sx, sy = fit
            proj[0, 0] *= s * zoom
            proj[1, 1] *= s * zoom
            proj[0, 2], proj[1, 2] = sx * zoom, sy * zoom
        if stage is not None:     # the box sits inside a bigger picture: (ax, bx, ay, by) in its NDC
            ax, bx, ay, by = stage
            T = np.eye(4)
            T[0, 0], T[0, 3], T[1, 1], T[1, 3] = ax, bx, ay, by
            proj = T @ proj
        return eye, view, proj

    def frame(self, az: float, el: float, aspect: float, swing: float, margin: float = 0.9):
        """The scale and shift that keep the whole model in view through its swing."""
        pts = self._sample
        xs, ys = [], []
        for turn in (-swing, -swing / 2, 0.0, swing / 2, swing):
            _eye, view, proj = self.camera(az + turn, el, aspect)
            h = np.hstack([pts, np.ones((len(pts), 1))]) @ (proj @ view).T
            ndc = h[:, :2] / h[:, 3:4]
            xs.append(ndc[:, 0])
            ys.append(ndc[:, 1])
        x, y = np.concatenate(xs), np.concatenate(ys)
        w, h = (x.max() - x.min()) / 2, (y.max() - y.min()) / 2
        s = margin / max(w, h, 1e-6)
        cx, cy = (x.max() + x.min()) / 2 * s, (y.max() + y.min()) / 2 * s
        return s, cx, cy

    def _lights(self, view: np.ndarray):
        rot = view[:3, :3]
        to_world = rot.T
        key = to_world @ _norm(np.array([-0.55, 0.78, 0.5]))
        fill = to_world @ _norm(np.array([0.85, 0.05, 0.45]))
        rim = to_world @ _norm(np.array([0.35, 0.55, -0.85]))
        return key, fill, rim, rot

    # --- Drawing ------------------------------------------------------------------------------
    def matrices(self, t: float) -> list[np.ndarray]:
        out = []
        for m, *_rest in self._items:
            mo = m.part.motion
            out.append(mo.matrix(t) if mo is not None else np.eye(4))
        return out

    def render(self, w: int, h: int, az: float, el: float, t: float, fit, zoom: float = 1.0,
               aspect: float | None = None, stage=None) -> QImage:
        """The model as a w x h picture. ``aspect`` and ``stage`` frame it in a box inside
        the picture (see camera); without them the picture is the box."""
        ctx = self.ctx
        with ctx:
            self._targets(w, h)
            eye, view, proj = self.camera(az, el, aspect or w / h, zoom, fit, stage)
            vp = proj @ view
            key, fill, rim, vrot = self._lights(view)
            mats = self.matrices(t)
            # Shadow map from the key light.
            r = self.radius
            lview = look_at(self.centre + key * r * 4, self.centre, np.array([0.0, 0.0, 1.0])
                            if abs(key[2]) < 0.95 else np.array([0.0, 1.0, 0.0]))
            lproj = ortho(r * 1.6, r * 1.6, r * 1.0, r * 7.5)
            light = lproj @ lview
            P = GPU.programs
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.disable(moderngl.CULL_FACE)
            ctx.disable(moderngl.BLEND)
            self.shadow_fbo.use()
            self.shadow_fbo.clear(depth=1.0)
            P["depth"]["u_vp"].write(_gl(light))
            for (m, _v, _i, _vao, dvao, _ivao), mat in zip(self._items, mats):
                if m.part.opacity > 0.5:
                    P["depth"]["u_model"].write(_gl(mat))
                    dvao.render()
            # The model seen from straight above, for the contact shadow under it.
            top_h = r * 2.2
            tview = look_at(self.centre + np.array([0, 0, top_h]), self.centre, np.array([0.0, 1.0, 0.0]))
            ground_z = self.lo[2] - r * 0.004
            tnear, tfar = 0.01, top_h + (self.centre[2] - ground_z)
            top = ortho(r * 1.7, r * 1.7, tnear, tfar) @ tview
            if self.ground:
                self.top_fbo.use()
                self.top_fbo.clear(depth=1.0)
                P["depth"]["u_vp"].write(_gl(top))
                for (m, _v, _i, _vao, dvao, _ivao), mat in zip(self._items, mats):
                    P["depth"]["u_model"].write(_gl(mat))
                    dvao.render()
            # The picture.
            self._fbo.use()
            self._fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
            self.shadow_tex.use(0)
            soft = 1.4 / self.SHADOW * 2.0
            if self.ground:
                g = P["ground"]
                g["u_vp"].write(_gl(vp))
                g["u_light"].write(_gl(light))
                g["u_top"].write(_gl(top))
                g["u_shadow"].value = 0
                self.top_tex.use(1)
                g["u_topdepth"].value = 1
                g["u_soft"].value = soft
                g["u_range"].value = float(tfar - tnear)
                g["u_reach"].value = float(r)
                g["u_centre"].value = tuple(float(x) for x in self.centre)
                g["u_strength"].value = 1.0
                ctx.enable(moderngl.BLEND)
                ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
                self._fbo.depth_mask = False
                self._gvao.render()
                self._fbo.depth_mask = True
                ctx.disable(moderngl.BLEND)
            prog = P["main"]
            prog["u_vp"].write(_gl(vp))
            prog["u_light"].write(_gl(light))
            prog["u_eye"].value = tuple(float(x) for x in eye)
            prog["u_vrot"].write(np.asarray(vrot, "f4").T.tobytes())
            prog["u_key"].value = tuple(float(x) for x in key)
            prog["u_fill"].value = tuple(float(x) for x in fill)
            prog["u_rim"].value = tuple(float(x) for x in rim)
            prog["u_shadow"].value = 0
            prog["u_soft"].value = soft
            prog["u_offset"].value = float(r * 3.2 / self.SHADOW * 2.0)
            prog["u_exposure"].value = self.EXPOSURE
            order = sorted(range(len(self._items)), key=lambda k: self._items[k][0].part.opacity < 0.999)
            clear = [k for k in order if self._items[k][0].part.opacity < 0.999]
            if clear:   # see-through parts last, farthest first
                cam = view[:3, :3]
                clear.sort(key=lambda k: float((cam @ (mats[k][:3, :3] @ self._items[k][0].centre + mats[k][:3, 3]))[2]))
                order = [k for k in order if k not in clear] + clear
            for k in order:
                m, _v, _i, vao, _dvao, _ivao = self._items[k]
                part = m.part
                mat = model3d.MATERIALS[part.material]
                prog["u_model"].write(_gl(mats[k]))
                prog["u_rough"].value = mat["rough"]
                prog["u_metal"].value = mat["metal"]
                prog["u_sss"].value = mat["sss"]
                prog["u_coat"].value = mat["coat"]
                prog["u_bump"].value = mat["bump"]
                prog["u_bumpscale"].value = float(18.0 / self.radius)
                prog["u_emit"].value = mat["emit"]
                prog["u_opacity"].value = float(part.opacity)
                prog["u_dim"].value = 1.0 if part.dim else 0.0
                if part.opacity < 0.999:
                    ctx.enable(moderngl.BLEND)
                    ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
                    self._fbo.depth_mask = False
                vao.render()
                self._fbo.depth_mask = True
                ctx.disable(moderngl.BLEND)
            ctx.copy_framebuffer(self._resolve, self._fbo)
            data = self._resolve.read(components=4, alignment=1)
        arr = np.frombuffer(data, np.uint8).reshape(h, w, 4)[::-1].copy()
        img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888_Premultiplied).copy()
        return img

    def pick(self, w: int, h: int, az: float, el: float, fit, zoom: float = 1.0, t: float = 0.0):
        """Which part shows at each pixel, and its depth (for placing labels on what's seen)."""
        ctx = self.ctx
        with ctx:
            tex = ctx.texture((w, h), 1, dtype="f4")
            depth = ctx.depth_texture((w, h))
            fbo = ctx.framebuffer(color_attachments=[tex], depth_attachment=depth)
            try:
                fbo.use()
                fbo.clear(-1.0, 0, 0, 0, depth=1.0)
                ctx.enable(moderngl.DEPTH_TEST)
                ctx.disable(moderngl.BLEND)
                _eye, view, proj = self.camera(az, el, w / h, zoom, fit)
                prog = GPU.programs["id"]
                prog["u_vp"].write(_gl(proj @ view))
                for k, ((_m, _v, _i, _vao, _dvao, ivao), mat) in enumerate(zip(self._items, self.matrices(t))):
                    prog["u_model"].write(_gl(mat))
                    prog["u_id"].value = float(k)
                    ivao.render()
                ids = np.frombuffer(tex.read(), "f4").reshape(h, w)[::-1]
                z = np.frombuffer(depth.read(), "f4").reshape(h, w)[::-1]
            finally:
                fbo.release()
                tex.release()
                depth.release()
        return np.rint(ids).astype(int), z, proj @ view


def _norm(v):
    return v / np.linalg.norm(v)


# --- The note card's model --------------------------------------------------------------------

class _Label:
    def __init__(self, text: str, mesh_index: int, color: QColor) -> None:
        self.text = text
        self.k = mesh_index
        self.color = color
        self.anchor = None          # model space, at rest
        self.fixed = False          # a spot the model names (not found by looking)
        self.side = 1
        self.rect = QRectF()


def _ink(rgb) -> QColor:
    """A part's colour, deep enough to read as text on paper."""
    r, g, b = (float(x) for x in rgb)
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    k = min(1.0, 0.42 / lum) if lum > 0.42 else 1.0
    r, g, b = r * k, g * k, b * k
    sat = max(r, g, b) - min(r, g, b)
    if sat < 0.12:   # greys and whites: plain ink
        return QColor(70, 66, 62)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


class RealModel:
    """A 3D note's model on the GPU, with labels; the note card's face for it."""

    TURN = 24.0
    ROW = 21.0
    FONT = 14.0

    def __init__(self, note: dict, spec: model3d.Spec) -> None:
        self.note = note
        self.spec = spec
        view = spec.view
        self.az0 = model3d._f(view.get("turn"), 30.0, -360, 360)
        self.el = model3d._f(view.get("tilt"), 16.0, -60, 80)
        self.zoom = model3d._f(view.get("zoom"), 1.0, 0.4, 3.0)
        self.ground = view.get("ground", True) is not False
        self.model = model3d.Model(spec)
        self.renderer: Renderer | None = None
        self.failed = False
        self.captions = list(spec.captions)
        self.labels: list[_Label] = []
        self._built = threading.Event()
        self._error = None
        self._fit_for = None
        self._fit = None
        self._area = QRectF()
        self._stage = QRectF()
        self._r0 = None              # when (in note time) the model was ready to show
        self.drag_az = 0.0
        self.drag_el = 0.0
        self._img = None
        self._img_key = None
        threading.Thread(target=self._build, daemon=True, name="clixy-3d-build").start()

    @classmethod
    def from_note(cls, note: dict) -> "RealModel | None":
        if moderngl is None or model3d.needs_painter(note) or GPU.get() is None:
            return None
        spec = model3d.spec_from_note(note)
        if spec is None:
            return None
        return cls(note, spec)

    def _build(self) -> None:
        try:
            self.model.build()
        except Exception as exc:     # shown as the fallback by the card
            self._error = exc
        self._built.set()

    def wait(self, timeout: float = 30.0) -> bool:
        return self._built.wait(timeout)

    @property
    def ready(self) -> bool:
        return self.renderer is not None

    def _start(self) -> None:
        if self.renderer is not None or self.failed or not self._built.is_set():
            return
        if self._error is not None or not self.model.meshes:
            self.failed = True
            return
        try:
            self.renderer = Renderer(self.model, ground=self.ground)
        except Exception:
            self.failed = True
            return
        for k, m in enumerate(self.model.meshes):
            if m.part.label:
                self.labels.append(_Label(m.part.label, k, _ink(m.part.color)))
            for text, at in m.part.marks:   # labelled spots: snapped onto the part's surface
                lb = _Label(text, k, _ink(m.part.color))
                spot = m.part.to_model(at)
                lb.anchor = m.pos[int(np.argmin(((m.pos - spot) ** 2).sum(1)))].astype(float)
                lb.fixed = True
                self.labels.append(lb)

    def label_count(self) -> int:
        return len(self.labels)

    def reveal_end(self) -> float:
        if self._r0 is None:
            return 99.0
        return self._r0 + 1.0 + 0.2 * len(self.labels) + 0.5 * len(self.captions) + 0.5

    def release(self) -> None:
        """Frees the model's GPU buffers (the card is done with it)."""
        if self.renderer is not None:
            try:
                self.renderer.release()
            except Exception:
                pass
            self.renderer = None
        self.failed = True

    def drag(self, dx: float, dy: float) -> None:
        self.drag_az -= dx * 0.45
        self.drag_el = max(-50.0, min(60.0, self.drag_el + dy * 0.35))

    # --- Fitting the model and labels into the canvas ------------------------------------------
    def _layout(self, canvas: QRectF, dpr: float) -> None:
        from PyQt6.QtGui import QFontMetricsF
        fm = QFontMetricsF(theme.hand_font(self.FONT))
        row = self.ROW
        bottom = canvas.bottom() - row * len(self.captions) - (6 if self.captions else 0)
        area = QRectF(canvas.left(), canvas.top(), canvas.width(), bottom - canvas.top())
        widths = {id(lb): fm.horizontalAdvance(lb.text) + 4 for lb in self.labels}
        r = self.renderer
        # First guess: the model in the middle, labels on the side their part is on.
        for _round in range(2):
            lw = max([widths[id(lb)] for lb in self.labels if lb.side < 0], default=0)
            rw = max([widths[id(lb)] for lb in self.labels if lb.side > 0], default=0)
            gap = 22
            left = area.left() + (lw + gap if lw else 6)
            right = area.right() - (rw + gap if rw else 6)
            box = QRectF(left, area.top() + 4, max(60.0, right - left), area.height() - 8)
            w, h = max(32, int(box.width() * dpr)), max(32, int(box.height() * dpr))
            self._fit = r.frame(self.az0, self.el, w / h, self.TURN)
            self._area = box
            if not self.labels:
                break
            ids, z, vp = r.pick(max(48, w // 3), max(48, h // 3), self.az0, self.el, self._fit, self.zoom)
            inv = np.linalg.inv(vp)
            ph, pw = ids.shape
            for lb in self.labels:
                if lb.fixed:
                    ndc = vp @ np.append(lb.anchor, 1.0)
                    lb.side = -1 if ndc[0] / ndc[3] < 0.0 else 1
                    continue
                mask = ids == lb.k
                if mask.sum() < 3:          # hidden from this side: its middle
                    m = self.model.meshes[lb.k]
                    lb.anchor = m.centre.copy()
                    ndc = vp @ np.append(lb.anchor, 1.0)
                    sx = ndc[0] / ndc[3]
                else:
                    from scipy import ndimage
                    inner = ndimage.distance_transform_edt(mask)
                    yy, xx = np.unravel_index(int(np.argmax(inner)), inner.shape)
                    sx = (xx + 0.5) / pw * 2 - 1
                    sy = 1 - (yy + 0.5) / ph * 2
                    dz = float(z[yy, xx]) * 2 - 1
                    p = inv @ np.array([sx, sy, dz, 1.0])
                    lb.anchor = p[:3] / p[3]
                lb.side = -1 if sx < 0.0 else 1
            # Keep the two sides roughly even.
            for _ in range(len(self.labels)):
                left_n = sum(1 for lb in self.labels if lb.side < 0)
                right_n = len(self.labels) - left_n
                if abs(left_n - right_n) <= 1:
                    break
                move_from = -1 if left_n > right_n else 1
                cands = [lb for lb in self.labels if lb.side == move_from]
                proj = [(abs(self._project_rest(lb.anchor)[0] - box.center().x()), lb) for lb in cands]
                proj.sort(key=lambda x: x[0])
                proj[0][1].side = -move_from
        self._stage = QRectF(canvas.left() - 8, canvas.top() - 6, canvas.width() + 16, area.height() + 10)
        self._fit_for = (canvas.x(), canvas.y(), canvas.width(), canvas.height(), dpr)

    def _project_rest(self, p) -> tuple[float, float]:
        box = self._area
        _eye, view, proj = self.renderer.camera(self.az0, self.el, box.width() / box.height(), self.zoom, self._fit)
        h = proj @ view @ np.append(p, 1.0)
        x, y = h[0] / h[3], h[1] / h[3]
        return box.left() + (x + 1) / 2 * box.width(), box.top() + (1 - y) / 2 * box.height()

    # --- Painting ---------------------------------------------------------------------------------
    def paint(self, p: QPainter, canvas: QRectF, *, turn: float = 0.0, t: float = 0.0, reveal: float = 99.0) -> None:
        self._start()
        if self.failed:
            raise RuntimeError("the model couldn't be built")
        if self.renderer is None:
            self._paint_waiting(p, canvas, reveal)
            return
        if self._r0 is None:
            self._r0 = max(0.0, reveal)
        dpr = p.device().devicePixelRatioF() if p.device() is not None else 1.0
        key = (canvas.x(), canvas.y(), canvas.width(), canvas.height(), dpr)
        if self._fit_for != key:
            self._layout(canvas, dpr)
        local = reveal - self._r0
        show = max(0.0, min(1.0, local / 0.8))
        if show <= 0:
            return
        box = self._area
        stage = self._stage
        w, h = max(32, int(stage.width() * dpr)), max(32, int(stage.height() * dpr))
        az = self.az0 + turn + self.drag_az
        el = max(-60.0, min(80.0, self.el + self.drag_el))
        img_key = (w, h, round(az, 2), round(el, 2), round(t, 3))
        if img_key != self._img_key:
            sx, sy = box.width() / stage.width(), box.height() / stage.height()
            frame = (sx, (box.center().x() - stage.center().x()) / (stage.width() / 2),
                     sy, -(box.center().y() - stage.center().y()) / (stage.height() / 2))
            self._img = self.renderer.render(w, h, az, el, t, self._fit, self.zoom,
                                             aspect=box.width() / box.height(), stage=frame)
            self._img.setDevicePixelRatio(dpr)
            self._img_key = img_key
        ease = 1 - (1 - show) ** 3
        p.save()
        c = box.center()
        p.translate(c)
        p.scale(0.94 + 0.06 * ease, 0.94 + 0.06 * ease)
        p.translate(-c)
        p.setOpacity(p.opacity() * ease)
        p.drawImage(stage.topLeft(), self._img)
        p.restore()
        self._paint_labels(p, canvas, az, el, t, local)

    def _paint_waiting(self, p: QPainter, canvas: QRectF, reveal: float) -> None:
        """While the model is being shaped: a soft pulse where it will appear."""
        c = canvas.center()
        pulse = 0.5 + 0.5 * math.sin(reveal * 3.2)
        rad = min(canvas.width(), canvas.height()) * (0.18 + 0.02 * pulse)
        g = QRadialGradient(c, rad)
        g.setColorAt(0.0, QColor(120, 110, 100, int(26 + 18 * pulse)))
        g.setColorAt(1.0, QColor(120, 110, 100, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, rad, rad)
        p.setPen(QColor(130, 124, 116))
        p.setFont(theme.hand_font(13))
        p.drawText(QRectF(canvas.left(), c.y() - 10, canvas.width(), 20), Qt.AlignmentFlag.AlignCenter,
                   "shaping the model...")

    def _paint_labels(self, p: QPainter, canvas: QRectF, az: float, el: float, t: float, local: float) -> None:
        if not self.labels and not self.captions:
            return
        box = self._area
        _eye, view, proj = self.renderer.camera(az, el, box.width() / box.height(), self.zoom, self._fit)
        vp = proj @ view
        mats = self.renderer.matrices(t)
        font = theme.hand_font(self.FONT)
        p.setFont(font)
        fm = QFontMetricsF(font)
        row = self.ROW

        def screen(pt):
            hh = vp @ np.append(pt, 1.0)
            x, y = hh[0] / hh[3], hh[1] / hh[3]
            return QPointF(box.left() + (x + 1) / 2 * box.width(), box.top() + (1 - y) / 2 * box.height())

        anchors = {}
        for lb in self.labels:
            m = mats[lb.k]
            anchors[id(lb)] = screen(m[:3, :3] @ lb.anchor + m[:3, 3])
        bottom = canvas.bottom() - row * len(self.captions) - (6 if self.captions else 0)
        from scene3d import _spread
        for side in (-1, 1):
            col = [lb for lb in self.labels if lb.side == side]
            if not col:
                continue
            rest = {id(lb): self._project_rest(lb.anchor)[1] for lb in col}
            col.sort(key=lambda lb: rest[id(lb)])
            ys = _spread([rest[id(lb)] for lb in col], row + 2, canvas.top() + row / 2, bottom - row / 2)
            for lb, y in zip(col, ys):
                wd = fm.horizontalAdvance(lb.text) + 4
                x = box.left() - 14 - wd if side < 0 else box.right() + 14
                x = max(canvas.left(), min(x, canvas.right() - wd))
                lb.rect = QRectF(x, y - row / 2, wd, row)
        order = sorted(self.labels, key=lambda lb: (lb.rect.top(), lb.side))
        for n, lb in enumerate(order):
            u = local - (0.9 + 0.2 * n)
            if u <= 0:
                continue
            a = anchors[id(lb)]
            grow = min(1.0, u / 0.25)
            y = lb.rect.center().y()
            edge = QPointF(lb.rect.right() + 3, y) if lb.side < 0 else QPointF(lb.rect.left() - 3, y)
            elbow = QPointF(edge.x() + (9 if lb.side < 0 else -9), y)
            pen = QPen(QColor(lb.color.red(), lb.color.green(), lb.color.blue(), 165), 1.25)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            end = QPointF(elbow.x() + (a.x() - elbow.x()) * grow, elbow.y() + (a.y() - elbow.y()) * grow)
            p.drawPolyline(QPolygonF([edge, elbow, end]))
            if grow >= 1:
                p.setPen(QPen(QColor(255, 255, 255, 235), 1.4))
                p.setBrush(lb.color)
                p.drawEllipse(a, 3.0, 3.0)
            text_u = max(0.0, min(1.0, (u - 0.1) / 0.3))
            if text_u <= 0:
                continue
            p.save()
            p.setClipRect(QRectF(lb.rect.left() - 2, lb.rect.top() - 6, (lb.rect.width() + 4) * text_u, row + 12))
            p.setPen(lb.color)
            p.drawText(QPointF(lb.rect.left(), lb.rect.bottom() - 5), lb.text)
            p.restore()
        for k, cap in enumerate(self.captions):
            u = local - (0.9 + 0.2 * len(self.labels) + 0.4 * k)
            if u <= 0:
                continue
            y = canvas.bottom() - row * (len(self.captions) - k - 1) - 5
            w = fm.horizontalAdvance(cap)
            p.save()
            p.setClipRect(QRectF(canvas.left(), y - row, (w + 6) * min(1.0, u / 0.4), row + 8))
            p.setPen(QColor(43, 43, 43))
            p.drawText(QPointF(canvas.left() + 2, y), cap)
            p.restore()
