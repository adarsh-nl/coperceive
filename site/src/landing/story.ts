/**
 * The scroll story's canvas, ported from the Claude Design file "Landing Motion".
 * A generated city block, cars with lidar and drones with a camera footprint.
 * Nothing here is recorded data: the scene, the objects and every number drawn
 * on it come from this simulation.
 */
import { MONO, hex, lerp, seq, ss } from "./util";

const CAR_COLS = ["#F58A1F", "#4CC9F0", "#FF6B8B", "#E0A431", "#1FA3A3"];
const DRONE_COLS = ["#3C8DFF", "#8E6CEF", "#7AA7FF", "#B59CFF"];
const ROUTES: [number, number, number, number, boolean, number][] = [
  [-47, -22, 47, 22, true, 9],
  [-53, -28, 3, 28, false, 8],
  [-3, -22, 53, 28, true, 8.5],
  [-53, 22, 53, 28, false, 7.5],
  [-3, -28, 3, 28, true, 7],
];
const DRONES: [number, number, number, number, number, "c" | "l"][] = [
  [-25, 0, 34, 0.12, 34, "c"],
  [30, -5, 40, 0.1, 30, "l"],
  [60, 30, 24, -0.15, 38, "c"],
  [-62, -32, 22, 0.18, 26, "c"],
];
const RINGS = [3, 5, 7.5, 10, 13, 16.5, 20.5, 25, 30, 35.5, 41.5];
const RANGE = 44;
const FP = 21;
const NRAYS = 200;

type Pt = [number, number];
type Seg = [Pt, Pt, number];
interface Route { segs: Seg[]; L: number }
interface Building { x0: number; x1: number; y0: number; y1: number; h: number }
interface Obj {
  id: string; horiz: boolean; road: number; along: number; off: number; kind: string;
  speed: number; min: number; max: number;
  x: number; y: number; yaw: number; L: number; Wd: number; h: number; seers: Agent[];
}
interface Agent {
  kind: "car" | "drone"; i: number; k: number; name: string; col: string;
  rgb: number[]; fillA: string; fpA: string; ring: string[]; w: number; hidden?: boolean;
  route?: Route; speed: number; d0: number;
  cx: number; cy: number; R: number; om: number; z: number; ty: "c" | "l";
  x: number; y: number; zz: number; yaw: number | null; fp?: Pt[];
}
type Hover = { t: "agent"; it: Agent; p: number[] } | { t: "obj"; it: Obj; p: number[] } | null;

function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function loop(x0: number, y0: number, x1: number, y1: number, cw: boolean): Route {
  const p: Pt[] = cw ? [[x0, y0], [x1, y0], [x1, y1], [x0, y1]] : [[x0, y0], [x0, y1], [x1, y1], [x1, y0]];
  const segs: Seg[] = [];
  let L = 0;
  for (let i = 0; i < 4; i++) {
    const a = p[i], b = p[(i + 1) % 4];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    segs.push([a, b, l]);
    L += l;
  }
  return { segs, L };
}

function onLoop(r: Route, d: number): [number, number, number] {
  d = ((d % r.L) + r.L) % r.L;
  for (const [a, b, l] of r.segs) {
    if (d <= l) {
      const t = l ? d / l : 0;
      return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), Math.atan2(b[1] - a[1], b[0] - a[0])];
    }
    d -= l;
  }
  return [0, 0, 0];
}

export interface StoryDom {
  canvas: HTMLCanvasElement;
  story: HTMLElement;
  hud: HTMLElement | null;
  bar: HTMLElement | null;
  captions: (HTMLElement | null)[];
}

export class StoryScene {
  private B: Building[] = [];
  private O: Obj[] = [];
  private A: Agent[] = [];
  private HR = [-25, 25];
  private VR = [-50, 0, 50];
  private jit: number[] = [];
  private tOff: number[] = [];
  private cam = { zoom: 1, tilt: 58, rot: 0, focus: 0 };
  private simT = 0;
  private mouse: { x: number; y: number } | null = null;
  private drag: { x: number; y: number; r: number; t: number; moved: boolean; hov: Hover } | null = null;
  private hov: Hover = null;
  private uRot = 0;
  private uTilt = 0;
  private detach: () => void;

  constructor(
    private dom: StoryDom,
    private opts: { cars: number; drones: number; autoRotate: boolean; reduced: boolean },
  ) {
    this.buildWorld();
    this.buildAgents();
    const cv = dom.canvas;
    const pos = (e: PointerEvent) => {
      const r = cv.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const onMove = (e: PointerEvent) => {
      this.mouse = pos(e);
      const d = this.drag;
      if (d) {
        const dx = e.clientX - d.x, dy = e.clientY - d.y;
        this.uRot = d.r + dx * 0.006;
        this.uTilt = Math.max(-35, Math.min(25, d.t - dy * 0.15));
        if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
      }
    };
    const onDown = (e: PointerEvent) => {
      this.drag = { x: e.clientX, y: e.clientY, r: this.uRot, t: this.uTilt, moved: false, hov: this.hov };
      try { cv.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    };
    const onUp = () => {
      const d = this.drag;
      this.drag = null;
      if (d && !d.moved && d.hov && d.hov.t === "agent") d.hov.it.hidden = !d.hov.it.hidden;
    };
    const onLeave = () => { this.mouse = null; };
    const onDbl = () => {
      this.uRot = 0;
      this.uTilt = 0;
      this.A.forEach((a) => (a.hidden = false));
    };
    cv.addEventListener("pointermove", onMove);
    cv.addEventListener("pointerdown", onDown);
    cv.addEventListener("pointerup", onUp);
    cv.addEventListener("pointercancel", onUp);
    cv.addEventListener("pointerleave", onLeave);
    cv.addEventListener("dblclick", onDbl);
    this.detach = () => {
      cv.removeEventListener("pointermove", onMove);
      cv.removeEventListener("pointerdown", onDown);
      cv.removeEventListener("pointerup", onUp);
      cv.removeEventListener("pointercancel", onUp);
      cv.removeEventListener("pointerleave", onLeave);
      cv.removeEventListener("dblclick", onDbl);
    };
  }

  dispose() {
    this.detach();
  }

  private buildWorld() {
    const r = rng(11);
    const B: Building[] = [];
    const XB = [[-100, -56], [-44, -6], [6, 44], [56, 100]], YB = [[-64, -31], [-19, 19], [31, 64]];
    for (const [x0, x1] of XB) for (const [y0, y1] of YB) {
      if (r() < 0.1) continue;
      const xs = x1 - x0 > 30 && r() < 0.75
        ? (() => { const m = x0 + (x1 - x0) * (0.38 + r() * 0.24); return [[x0, m], [m, x1]]; })()
        : [[x0, x1]];
      for (const [a, b] of xs) {
        const ys = y1 - y0 > 30 && r() < 0.5
          ? (() => { const m = y0 + (y1 - y0) * (0.4 + r() * 0.2); return [[y0, m], [m, y1]]; })()
          : [[y0, y1]];
        for (const [c, d] of ys) {
          const ia = a === x0 ? 2.5 : 1.4, ib = b === x1 ? 2.5 : 1.4, ic = c === y0 ? 2.5 : 1.4, id = d === y1 ? 2.5 : 1.4;
          B.push({ x0: a + ia, x1: b - ib, y0: c + ic, y1: d - id, h: 7 + r() * (Math.abs(c + d) < 10 ? 30 : 20) });
        }
      }
    }
    this.B = B;
    const O: Obj[] = [];
    for (let i = 0; i < 24; i++) {
      const horiz = r() < 0.5;
      const offs = [-7.6, 7.6, -5, 5, -3, 3];
      const off = offs[Math.floor(r() * offs.length)];
      const kind = Math.abs(off) > 7 ? (r() < 0.3 ? "cyclist" : "ped") : Math.abs(off) === 5 && r() < 0.35 ? "truck" : "car";
      const moving = Math.abs(off) === 3 || kind === "ped" || kind === "cyclist";
      const speed = moving ? (kind === "ped" ? 1.3 : kind === "cyclist" ? 3.8 : 4 + r() * 3) * (off > 0 ? 1 : -1) : 0;
      const id = String((1021 + i * 137) % 9999).padStart(4, "0");
      const base = { id, horiz, off, kind, speed, x: 0, y: 0, yaw: 0, L: 0, Wd: 0, h: 0, seers: [] as Agent[] };
      if (horiz) O.push({ ...base, road: this.HR[Math.floor(r() * 2)], along: -95 + r() * 190, min: -100, max: 100 });
      else O.push({ ...base, road: this.VR[Math.floor(r() * 3)], along: -60 + r() * 120, min: -64, max: 64 });
    }
    this.O = O;
    this.jit = Array.from({ length: NRAYS * RINGS.length }, () => r() - 0.5);
    this.tOff = Array.from({ length: 12 }, () => (r() - 0.5) * 0.085);
  }

  private buildAgents() {
    const nc = Math.max(1, Math.min(5, this.opts.cars)), nd = Math.max(0, Math.min(4, this.opts.drones));
    const A: Agent[] = [];
    const blank = { cx: 0, cy: 0, R: 0, om: 0, z: 0, ty: "c" as const, x: 0, y: 0, zz: 0, yaw: null, speed: 0, d0: 0 };
    for (let i = 0; i < nc; i++) {
      const [x0, y0, x1, y1, cw, sp] = ROUTES[i];
      A.push({ ...blank, kind: "car", i, k: 0, name: "vehicle " + (i + 1), col: CAR_COLS[i], route: loop(x0, y0, x1, y1, cw), speed: sp, d0: i * 37, w: 1, rgb: [], fillA: "", fpA: "", ring: [] });
    }
    for (let i = 0; i < nd; i++) {
      const [cx, cy, R, om, z, ty] = DRONES[i];
      A.push({ ...blank, kind: "drone", i, k: 0, name: "drone " + (i + 1), col: DRONE_COLS[i], cx, cy, R, om, z, ty, w: 1, rgb: [], fillA: "", fpA: "", ring: [] });
    }
    A.forEach((a, k) => {
      const [r, g, b] = hex(a.col);
      a.rgb = [r, g, b];
      a.fillA = `rgba(${r},${g},${b},0.2)`;
      a.fpA = `rgba(${r},${g},${b},0.07)`;
      a.k = k;
      a.ring = RINGS.map((R) => {
        const t = R / RANGE;
        return `rgb(${Math.round(lerp(255, r, t))},${Math.round(lerp(255, g, t))},${Math.round(lerp(255, b, t))})`;
      });
    });
    this.A = A;
  }

  private rayHit(ox: number, oy: number, dx: number, dy: number, maxT: number): [number, number] {
    let best = maxT, bh = 0;
    const idx = 1 / (dx || 1e-9), idy = 1 / (dy || 1e-9);
    for (const b of this.B) {
      const tx1 = (b.x0 - ox) * idx, tx2 = (b.x1 - ox) * idx, ty1 = (b.y0 - oy) * idy, ty2 = (b.y1 - oy) * idy;
      const tmin = Math.max(Math.min(tx1, tx2), Math.min(ty1, ty2)), tmax = Math.min(Math.max(tx1, tx2), Math.max(ty1, ty2));
      if (tmax >= tmin && tmin > 0 && tmin < best) { best = tmin; bh = b.h; }
    }
    return [best, bh];
  }

  private blocked(ax: number, ay: number, bx: number, by: number) {
    const dx = bx - ax, dy = by - ay, idx = 1 / (dx || 1e-9), idy = 1 / (dy || 1e-9);
    for (const b of this.B) {
      const tx1 = (b.x0 - ax) * idx, tx2 = (b.x1 - ax) * idx, ty1 = (b.y0 - ay) * idy, ty2 = (b.y1 - ay) * idy;
      const tmin = Math.max(Math.min(tx1, tx2), Math.min(ty1, ty2)), tmax = Math.min(Math.max(tx1, tx2), Math.max(ty1, ty2));
      if (tmax >= tmin && tmin < 1 && tmax > 0) return true;
    }
    return false;
  }

  /** Scroll progress through the pinned story, 0 to 1. */
  private progress() {
    const rc = this.dom.story.getBoundingClientRect(), vh = window.innerHeight || 800;
    return Math.min(1, Math.max(0, -rc.top / Math.max(1, rc.height - vh)));
  }

  tick(dtIn: number) {
    const cv = this.dom.canvas;
    const dt = this.opts.reduced ? 0 : dtIn;
    this.simT += dt;
    const t = this.simT;
    const dpr = Math.min(2, window.devicePixelRatio || 1), W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const s = Math.min(4.999, this.progress() * 5);

    // captions and progress bar
    this.dom.captions.forEach((el, k) => {
      if (!el) return;
      const o = (k === 0 ? 1 : ss(k - 0.18, k + 0.04, s)) * (k === 4 ? 1 : 1 - ss(k + 0.84, k + 1.04, s));
      el.style.opacity = String(o);
      el.style.pointerEvents = o > 0.5 ? "auto" : "none";
      el.setAttribute("aria-hidden", o > 0.5 ? "false" : "true");
      el.style.transform = k === 0 ? `translateY(${(1 - o) * -30}px)` : `translateY(calc(-50% + ${(1 - o) * 28}px))`;
    });
    if (this.dom.bar) this.dom.bar.style.height = (s / 4.999) * 100 + "%";

    // agents
    const A = this.A;
    for (const a of A) {
      const tgt = a.hidden ? 0 : seq(s, [1, a.k === 0 ? 1 : 0, a.kind === "car" ? 1 : 0, 1, 1]);
      a.w += (tgt - a.w) * 0.08;
      if (a.kind === "car" && a.route) {
        const [x, y, yaw] = onLoop(a.route, a.d0 + a.speed * t);
        a.x = x; a.y = y; a.z = 0;
        a.yaw = a.yaw == null ? yaw : a.yaw + Math.atan2(Math.sin(yaw - a.yaw), Math.cos(yaw - a.yaw)) * 0.2;
      } else {
        const ph = t * a.om + a.i * 1.7;
        let x: number, y: number;
        if (a.ty === "c") { x = a.cx + Math.cos(ph) * a.R; y = a.cy + Math.sin(ph) * a.R * 0.7; }
        else { const d = 1 + Math.sin(ph) ** 2; x = a.cx + (a.R * Math.cos(ph)) / d; y = a.cy + (a.R * Math.sin(ph) * Math.cos(ph)) / d; }
        if (a.yaw !== null || a.x !== 0) {
          const yy = Math.atan2(y - a.y, x - a.x);
          a.yaw = a.yaw == null ? yy : a.yaw + Math.atan2(Math.sin(yy - a.yaw), Math.cos(yy - a.yaw)) * 0.05;
        }
        a.x = x; a.y = y; a.zz = a.z + Math.sin(t * 1.3 + a.i) * 0.8;
      }
    }
    const DIMS: Record<string, number[]> = { ped: [0.8, 0.8, 1.8], cyclist: [1.8, 0.7, 1.7], car: [4.4, 1.9, 1.5], truck: [8, 2.5, 3.2] };
    for (const o of this.O) {
      if (o.speed) {
        o.along += o.speed * dt;
        if (o.along > o.max) o.along = o.min;
        if (o.along < o.min) o.along = o.max;
      }
      o.x = o.horiz ? o.along : o.road + o.off;
      o.y = o.horiz ? o.road + o.off : o.along;
      o.yaw = o.horiz ? 0 : Math.PI / 2;
      const D = DIMS[o.kind];
      o.L = D[0]; o.Wd = D[1]; o.h = D[2];
    }

    // visibility
    let seen = 0, shared = 0;
    for (const o of this.O) {
      o.seers = [];
      for (const a of A) {
        if (a.w < 0.5) continue;
        if (a.kind === "car") {
          if (Math.hypot(o.x - a.x, o.y - a.y) < RANGE && !this.blocked(a.x, a.y, o.x, o.y)) o.seers.push(a);
        } else {
          const yw = a.yaw ?? 0, c = Math.cos(-yw), sn = Math.sin(-yw), dx = o.x - a.x, dy = o.y - a.y;
          if (Math.abs(dx * c - dy * sn) < FP && Math.abs(dx * sn + dy * c) < FP) o.seers.push(a);
        }
      }
      if (o.seers.length) seen++;
      if (o.seers.length > 1) shared++;
    }
    if (this.dom.hud) {
      this.dom.hud.innerHTML = `seen&nbsp;&nbsp;<span style="color:#f5f5f7">${String(seen).padStart(2, "0")} / ${this.O.length}</span><br>shared&nbsp;&nbsp;<span style="color:#f5f5f7">${String(shared).padStart(2, "0")}</span><br>agents&nbsp;&nbsp;<span style="color:#f5f5f7">${A.filter((a) => a.w > 0.5).length} / ${A.length}</span>`;
    }

    // camera
    const c = this.cam, k = 0.06;
    c.zoom += (seq(s, [1, 2.0, 1.25, 0.95, 1.05]) - c.zoom) * k;
    c.tilt += (seq(s, [58, 47, 54, 62, 70]) - c.tilt) * k;
    c.rot += (seq(s, [0, 0.5, 0.25, -0.15, -0.35]) - c.rot) * k;
    c.focus += (seq(s, [0, 1, 0.3, 0, 0]) - c.focus) * k;
    const auto = this.opts.autoRotate ? t * 0.025 : 0;
    const th = auto + c.rot + this.uRot;
    const tilt = (Math.min(85, Math.max(22, c.tilt + this.uTilt)) * Math.PI) / 180;
    const S = Math.min(W / 190, H / 115) * c.zoom;
    const cx = lerp(0, A[0].x, c.focus), cy = lerp(0, A[0].y, c.focus);
    const ct = Math.cos(th), st = Math.sin(th), cT = Math.cos(tilt), sT = Math.sin(tilt);
    const OX = W * (W > 900 ? 0.6 : 0.5), OY = H * 0.55;
    const P = (x: number, y: number, z: number) => {
      const dx = x - cx, dy = y - cy, ry = dx * st + dy * ct;
      return [OX + (dx * ct - dy * st) * S, OY + ry * S * cT - z * S * sT, ry];
    };

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);

    // roads
    ctx.lineWidth = 1;
    const line = (x0: number, y0: number, x1: number, y1: number) => {
      const a = P(x0, y0, 0), b = P(x1, y1, 0);
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
    };
    const unP = (sx: number, sy: number) => {
      const rx = (sx - OX) / S, ry = (sy - OY) / (S * cT);
      return [cx + rx * ct + ry * st, cy - rx * st + ry * ct];
    };
    const poly = (pts: Pt[]) => {
      ctx.beginPath();
      pts.forEach((q, i) => { const p = P(q[0], q[1], 0); if (i) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); });
      ctx.closePath();
    };
    ctx.fillStyle = "rgba(255,255,255,0.03)";
    for (const y of this.HR) { poly([[-100, y - 6], [100, y - 6], [100, y + 6], [-100, y + 6]]); ctx.fill(); }
    for (const x of this.VR) { poly([[x - 6, -64], [x + 6, -64], [x + 6, 64], [x - 6, 64]]); ctx.fill(); }
    ctx.strokeStyle = "rgba(255,255,255,0.2)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    for (const x of this.VR) for (const y of this.HR) for (const sg of [-1, 1]) for (let j = -5; j <= 5; j += 1.4) {
      line(x + sg * 7.5, y + j, x + sg * 10, y + j);
      line(x + j, y + sg * 7.5, x + j, y + sg * 10);
    }
    ctx.stroke();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(134,134,139,0.28)";
    ctx.beginPath();
    for (const y of this.HR) for (const o of [-6, 6]) line(-100, y + o, 100, y + o);
    for (const x of this.VR) for (const o of [-6, 6]) line(x + o, -64, x + o, 64);
    ctx.stroke();
    ctx.strokeStyle = "rgba(125,206,160,0.5)";
    ctx.beginPath();
    for (const y of this.HR) for (const o of [-3, 3]) line(-100, y + o, 100, y + o);
    for (const x of this.VR) for (const o of [-3, 3]) line(x + o, -64, x + o, 64);
    ctx.stroke();

    // drone footprints
    for (const a of A) {
      if (a.kind !== "drone" || a.w < 0.01) continue;
      const yw = a.yaw ?? 0, cs = Math.cos(yw), sn = Math.sin(yw);
      a.fp = ([[1, 1], [-1, 1], [-1, -1], [1, -1]] as Pt[]).map(([u, v]) => [a.x + (u * FP * cs - v * FP * sn), a.y + (u * FP * sn + v * FP * cs)] as Pt);
      ctx.globalAlpha = a.w;
      ctx.beginPath();
      a.fp.forEach((p, i) => { const q = P(p[0], p[1], 0); if (i) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
      ctx.closePath();
      ctx.fillStyle = a.fpA;
      ctx.fill();
      ctx.strokeStyle = a.col;
      ctx.globalAlpha = a.w * 0.45;
      ctx.stroke();
    }

    // lidar
    ctx.globalCompositeOperation = "lighter";
    for (const a of A) {
      if (a.kind !== "car" || a.w < 0.01) continue;
      ctx.globalAlpha = a.w * 0.9;
      for (let i = 0; i < NRAYS; i++) {
        const ang = (i / NRAYS) * Math.PI * 2 + t * 0.15, dx = Math.cos(ang), dy = Math.sin(ang);
        const [hit, bh] = this.rayHit(a.x, a.y, dx, dy, RANGE);
        for (let j = 0; j < RINGS.length; j++) {
          const R = RINGS[j] + this.jit[i * RINGS.length + j] * 1.2;
          if (R >= hit) break;
          const p = P(a.x + dx * R, a.y + dy * R, 0);
          ctx.fillStyle = a.ring[j];
          ctx.fillRect(p[0] - 0.8, p[1] - 0.8, 1.7, 1.7);
        }
        if (hit < RANGE) {
          ctx.fillStyle = a.col;
          const top = Math.min(bh, 11);
          for (let z = 0.6; z < top; z += 1.4) {
            const p = P(a.x + dx * hit, a.y + dy * hit, z);
            ctx.fillRect(p[0] - 0.8, p[1] - 0.8, 1.7, 1.7);
          }
        }
      }
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;

    // depth-sorted solids
    const box = (x: number, y: number, yaw: number, L: number, Wd: number, h: number, top: string | null, side: string | null, stroke: string) => {
      const cs = Math.cos(yaw), sn = Math.sin(yaw), hl = L / 2, hw = Wd / 2;
      const cr = [[hl, hw], [-hl, hw], [-hl, -hw], [hl, -hw]].map(([u, v]) => [x + u * cs - v * sn, y + u * sn + v * cs]);
      const b = cr.map((p) => P(p[0], p[1], 0)), tp = cr.map((p) => P(p[0], p[1], h));
      const sides = [0, 1, 2, 3].map((i) => [i, (i + 1) % 4, (b[i][2] + b[(i + 1) % 4][2]) / 2]).sort((m, n) => m[2] - n[2]);
      ctx.strokeStyle = stroke;
      for (const [i, j] of sides) {
        ctx.beginPath();
        ctx.moveTo(b[i][0], b[i][1]);
        ctx.lineTo(b[j][0], b[j][1]);
        ctx.lineTo(tp[j][0], tp[j][1]);
        ctx.lineTo(tp[i][0], tp[i][1]);
        ctx.closePath();
        if (side) { ctx.fillStyle = side; ctx.fill(); }
        ctx.stroke();
      }
      ctx.beginPath();
      tp.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.closePath();
      if (top) { ctx.fillStyle = top; ctx.fill(); }
      ctx.stroke();
    };
    type Item = [number, 0, Building] | [number, 1, Obj] | [number, 2, Agent];
    const items: Item[] = [];
    for (const b of this.B) items.push([P((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, 0)[2] - 3, 0, b]);
    for (const o of this.O) items.push([P(o.x, o.y, 0)[2], 1, o]);
    for (const a of A) items.push([a.kind === "drone" ? 1e6 : P(a.x, a.y, 0)[2], 2, a]);
    items.sort((m, n) => m[0] - n[0]);
    for (const item of items) {
      if (item[1] === 0) {
        const it = item[2];
        ctx.globalAlpha = 1;
        ctx.lineWidth = 1;
        box((it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2, 0, it.x1 - it.x0, it.y1 - it.y0, it.h, "#0d0d10", "#070709", "rgba(255,255,255,0.11)");
      } else if (item[1] === 1) {
        const it = item[2], n = it.seers.length;
        ctx.globalAlpha = n ? 1 : 0.55;
        ctx.lineWidth = n ? 1.2 : 1;
        if (n > 1) box(it.x, it.y, it.yaw, it.L, it.Wd, it.h, "rgba(255,255,255,0.22)", null, "#ffffff");
        else if (n === 1) box(it.x, it.y, it.yaw, it.L, it.Wd, it.h, it.seers[0].fillA, null, it.seers[0].col);
        else box(it.x, it.y, it.yaw, it.L, it.Wd, it.h, null, null, "rgba(255,255,255,0.22)");
      } else if (item[2].kind === "car") {
        const it = item[2];
        ctx.globalAlpha = 0.3 + 0.7 * it.w;
        ctx.lineWidth = 1;
        box(it.x, it.y, it.yaw ?? 0, 4.6, 2.0, 1.6, it.col, `rgba(${it.rgb.map((v) => Math.round(v * 0.55)).join(",")},1)`, it.col);
        const p = P(it.x, it.y, 1.6);
        ctx.globalCompositeOperation = "lighter";
        const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], 28 * it.w + 4);
        g.addColorStop(0, `rgba(${it.rgb.join(",")},0.45)`);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g;
        ctx.fillRect(p[0] - 34, p[1] - 34, 68, 68);
        ctx.globalCompositeOperation = "source-over";
      } else {
        const it = item[2];
        const p = P(it.x, it.y, it.zz), gnd = P(it.x, it.y, 0);
        ctx.globalAlpha = 0.3 + 0.7 * it.w;
        ctx.strokeStyle = `rgba(${it.rgb.join(",")},0.35)`;
        ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(p[0], p[1]);
        ctx.lineTo(gnd[0], gnd[1]);
        ctx.stroke();
        ctx.setLineDash([]);
        if (it.fp && it.w > 0.01) {
          ctx.globalCompositeOperation = "lighter";
          ctx.strokeStyle = `rgba(${it.rgb.join(",")},${0.22 * it.w})`;
          ctx.beginPath();
          for (const q of it.fp) { const g2 = P(q[0], q[1], 0); ctx.moveTo(p[0], p[1]); ctx.lineTo(g2[0], g2[1]); }
          ctx.stroke();
          ctx.globalCompositeOperation = "source-over";
        }
        ctx.globalCompositeOperation = "lighter";
        const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], 22);
        g.addColorStop(0, `rgba(${it.rgb.join(",")},0.9)`);
        g.addColorStop(0.25, `rgba(${it.rgb.join(",")},0.35)`);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g;
        ctx.fillRect(p[0] - 24, p[1] - 24, 48, 48);
        ctx.globalCompositeOperation = "source-over";
        ctx.fillStyle = "#fff";
        const yw = (it.yaw ?? 0) + Math.PI / 4, cs = Math.cos(yw), sn = Math.sin(yw);
        for (const [u, v] of [[1.6, 0], [-1.6, 0], [0, 1.6], [0, -1.6]]) {
          const q = P(it.x + u * cs - v * sn, it.y + u * sn + v * cs, it.zz);
          ctx.beginPath();
          ctx.arc(q[0], q[1], 1.6, 0, 7);
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(p[0], p[1], 2.4, 0, 7);
        ctx.fill();
      }
    }

    // shared links
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "lighter";
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);
    ctx.strokeStyle = "rgba(255,255,255,0.32)";
    ctx.beginPath();
    for (const o of this.O) {
      if (o.seers.length < 2) continue;
      const a0 = P(o.x, o.y, o.h);
      for (const a of o.seers) { const b = P(a.x, a.y, a.kind === "drone" ? a.zz : 1.6); ctx.moveTo(a0[0], a0[1]); ctx.lineTo(b[0], b[1]); }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalCompositeOperation = "source-over";

    // semantic labels
    ctx.font = `500 10px ${MONO}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    for (const o of this.O) {
      const n = o.seers.length;
      if (!n) continue;
      const p = P(o.x, o.y, o.h + 1.4);
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = n > 1 ? "#ffffff" : o.seers[0].col;
      ctx.fillText(`${o.kind} ${o.id}`, p[0], p[1]);
    }
    for (const a of A) {
      if (a.w < 0.05) continue;
      const p = P(a.x, a.y, a.kind === "drone" ? a.zz + 4 : 4.5);
      ctx.globalAlpha = a.w;
      ctx.fillStyle = a.col;
      ctx.fillText(a.kind === "car" ? `${a.name} · ${a.speed.toFixed(1)} m/s` : `${a.name} · alt ${a.z} m`, p[0], p[1]);
    }
    ctx.textAlign = "left";
    ctx.globalAlpha = 1;

    // interaction
    const m = this.mouse;
    let hov: Hover = null;
    const tip = (x: number, y: number, title: string, col: string, lines: string[]) => {
      ctx.font = `500 12px ${MONO}`;
      let w = ctx.measureText(title).width;
      ctx.font = `11px ${MONO}`;
      for (const l of lines) w = Math.max(w, ctx.measureText(l).width);
      const bw = w + 28, bh = 34 + lines.length * 17;
      let tx = x + 18, ty = y + 18;
      if (tx + bw > W - 12) tx = x - 18 - bw;
      if (ty + bh > H - 12) ty = y - 18 - bh;
      ctx.globalAlpha = 1;
      ctx.fillStyle = "rgba(12,12,14,0.94)";
      ctx.strokeStyle = "rgba(255,255,255,0.16)";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(tx, ty, bw, bh, 10); else ctx.rect(tx, ty, bw, bh);
      ctx.fill();
      ctx.stroke();
      ctx.textBaseline = "middle";
      ctx.fillStyle = col;
      ctx.fillRect(tx + 14, ty + 16, 8, 2);
      ctx.font = `500 12px ${MONO}`;
      ctx.fillStyle = "#f5f5f7";
      ctx.fillText(title, tx + 28, ty + 17);
      ctx.font = `11px ${MONO}`;
      ctx.fillStyle = "#a1a1a6";
      lines.forEach((l, i) => ctx.fillText(l, tx + 14, ty + 38 + i * 17));
    };
    const ray = (a: Agent, x: number, y: number, z: number, col: string, alpha: number) => {
      const p0 = P(a.x, a.y, a.kind === "drone" ? a.zz : 1.6), p1 = P(x, y, z);
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(p0[0], p0[1]);
      ctx.lineTo(p1[0], p1[1]);
      ctx.stroke();
      ctx.lineWidth = 1;
    };
    if (m && !this.drag) {
      let best = 22;
      for (const a of A) {
        const p = P(a.x, a.y, a.kind === "drone" ? a.zz : 1);
        const d = Math.hypot(p[0] - m.x, p[1] - m.y);
        if (d < best) { best = d; hov = { t: "agent", it: a, p }; }
      }
      for (const o of this.O) {
        const p = P(o.x, o.y, o.h / 2);
        const d = Math.hypot(p[0] - m.x, p[1] - m.y);
        if (d < best - 4) { best = d; hov = { t: "obj", it: o, p }; }
      }
      if (hov && hov.t === "obj") {
        const o = hov.it, n = o.seers.length;
        for (const a of o.seers) ray(a, o.x, o.y, o.h / 2, a.col, 0.9);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#fff";
        ctx.beginPath();
        ctx.arc(hov.p[0], hov.p[1], 14, 0, 7);
        ctx.stroke();
        let near = A[0], nd = 1e9;
        for (const a of A) { const d = Math.hypot(a.x - o.x, a.y - o.y); if (d < nd) { nd = d; near = a; } }
        tip(m.x, m.y, `${o.kind} · track ${o.id}`, n > 1 ? "#ffffff" : n ? o.seers[0].col : "#86868b", [
          n > 1 ? `shared track · ${n} agents` : n ? `seen by ${o.seers[0].name}` : "unseen · occluded from every agent",
          n ? o.seers.map((a) => a.name).join(", ") : "no line of sight",
          o.speed ? `${Math.abs(o.speed).toFixed(1)} m/s` : "parked",
          `${nd.toFixed(1)} m to ${near.name}`,
        ]);
      } else if (hov) {
        const a = hov.it;
        ctx.globalAlpha = 1;
        ctx.strokeStyle = a.col;
        ctx.beginPath();
        ctx.arc(hov.p[0], hov.p[1], 16, 0, 7);
        ctx.stroke();
        const sees = this.O.filter((o) => o.seers.includes(a)).length;
        tip(m.x, m.y, a.name, a.col, a.kind === "car"
          ? [`lidar · ${NRAYS} beams · ${RANGE} m`, `${a.speed.toFixed(1)} m/s`, `sees ${sees} objects`, a.hidden ? "click to show" : "click to hide"]
          : [`camera · ${FP * 2}×${FP * 2} m footprint`, `altitude ${a.z} m`, `sees ${sees} objects`, a.hidden ? "click to show" : "click to hide"]);
      } else {
        const [gx, gy] = unP(m.x, m.y);
        if (Math.abs(gx) < 100 && Math.abs(gy) < 64) {
          const bld = this.B.find((b) => gx > b.x0 && gx < b.x1 && gy > b.y0 && gy < b.y1);
          const onRoad = this.HR.some((y) => Math.abs(gy - y) < 6) || this.VR.some((x) => Math.abs(gx - x) < 6);
          const vis: Agent[] = [];
          if (!bld) for (const a of A) {
            if (a.w < 0.5) continue;
            if (a.kind === "car") {
              if (Math.hypot(gx - a.x, gy - a.y) < RANGE && !this.blocked(a.x, a.y, gx, gy)) vis.push(a);
            } else {
              const yw = a.yaw ?? 0, c2 = Math.cos(-yw), s2 = Math.sin(-yw), dx = gx - a.x, dy = gy - a.y;
              if (Math.abs(dx * c2 - dy * s2) < FP && Math.abs(dx * s2 + dy * c2) < FP) vis.push(a);
            }
          }
          for (const a of vis) ray(a, gx, gy, 0, a.col, 0.85);
          const p = P(gx, gy, 0);
          ctx.globalAlpha = 1;
          ctx.strokeStyle = vis.length ? "#fff" : "#E03A22";
          ctx.beginPath();
          ctx.ellipse(p[0], p[1], 10, 10 * cT, 0, 0, 7);
          ctx.stroke();
          tip(m.x, m.y, bld ? "building" : onRoad ? "drivable area" : "sidewalk", bld ? "#86868b" : vis.length ? "#7dcea0" : "#E03A22", bld
            ? [`height ${bld.h.toFixed(1)} m`, "occludes ground lidar"]
            : [`x ${gx.toFixed(1)}  y ${gy.toFixed(1)} m`, vis.length ? `visible to ${vis.length} agent${vis.length > 1 ? "s" : ""}` : "blind spot · no agent sees here", ...(vis.length ? [vis.map((a) => a.name).join(", ")] : [])]);
        }
      }
    }
    this.hov = hov;
    cv.style.cursor = this.drag ? "grabbing" : hov && hov.t === "agent" ? "pointer" : "crosshair";
    ctx.globalAlpha = 1;

    // legend
    ctx.font = `500 12px ${MONO}`;
    ctx.textBaseline = "middle";
    const pad = W < 640 ? 24 : 56;
    let lx = pad;
    const ly = 92;
    for (const a of A) {
      ctx.globalAlpha = 0.25 + 0.75 * a.w;
      ctx.fillStyle = a.col;
      ctx.beginPath();
      if (a.kind === "car") ctx.rect(lx, ly - 4, 8, 8); else ctx.arc(lx + 4, ly, 4, 0, 7);
      ctx.fill();
      ctx.fillStyle = "#d2d2d7";
      ctx.fillText(a.name, lx + 14, ly);
      lx += 14 + ctx.measureText(a.name).width + 20;
    }
    ctx.globalAlpha = 1;
    ctx.font = `11px ${MONO}`;
    ctx.fillStyle = "#86868b";
    ctx.fillText("drag to orbit · hover to inspect · click an agent to hide it · double-click to reset", pad, ly + 24);

    // timeline
    const tw = ss(3.8, 4.2, s);
    if (tw > 0.01) {
      const u = ss(4.2, 4.65, s), rowH = 20, pw = Math.min(620, W * 0.5), x0 = W - 56 - pw, y1 = H - 56, ph = A.length * rowH + 48, y0 = y1 - ph;
      ctx.globalAlpha = tw;
      ctx.fillStyle = "rgba(12,12,14,0.86)";
      ctx.strokeStyle = "rgba(255,255,255,0.14)";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x0, y0, pw, ph, 12); else ctx.rect(x0, y0, pw, ph);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#86868b";
      ctx.fillText(u > 0.98 ? "synced · one clock" : "per-agent clocks", x0 + 18, y0 + 20);
      const us = Math.round(t * 1e6).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
      ctx.textAlign = "right";
      ctx.fillStyle = "#f5f5f7";
      ctx.fillText("t = " + us + " µs", x0 + pw - 18, y0 + 20);
      ctx.textAlign = "left";
      const tx0 = x0 + 110, tx1 = x0 + pw - 18, win = 1.6, mid = (tx0 + tx1) / 2, sc = (tx1 - tx0) / win;
      A.forEach((a, i) => {
        const ry = y0 + 40 + i * rowH + rowH / 2;
        ctx.globalAlpha = tw * (0.35 + 0.65 * a.w);
        ctx.fillStyle = "#a1a1a6";
        ctx.fillText(a.name, x0 + 18, ry);
        const per = 0.1, off = this.tOff[i] * (1 - u);
        ctx.fillStyle = a.col;
        for (let kk = Math.floor((t - win / 2 - off) / per); kk * per + off < t + win / 2; kk++) {
          const x = mid + (kk * per + off - t) * sc;
          if (x < tx0 || x > tx1) continue;
          ctx.fillRect(x - 1, ry - 5, 2, 10);
        }
      });
      ctx.globalAlpha = tw;
      ctx.fillStyle = "#E03A22";
      ctx.fillRect(mid - 0.75, y0 + 34, 1.5, A.length * rowH + 8);
    }
    ctx.globalAlpha = 1;
  }
}
