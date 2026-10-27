/**
 * The evaluate section's canvas, ported from the Claude Design file "Landing Motion".
 * A simulated drive with simulated ground truth and two simulated detector runs
 * ("det-v1", "det-v2"). Matching, precision and recall are computed live from
 * those simulated boxes; none of the numbers is a measured result.
 */
import { MONO, SANS, lerp, ss } from "./util";

interface Gt { kind: string; x: number; y: number; v: number; L: number; W: number; h: number; id: string; n: number; m: Pred | null; iou: number }
interface Pred { x: number; y: number; L: number; W: number; h: number; sc: number; die?: number; m: Gt | null; iou: number }
interface Post { x: number; y: number; h: number; tree: boolean }
interface State {
  t: number; egoX: number; gts: Gt[]; fps: Record<"v1" | "v2", Pred[]>; hist: number[][];
  acc: number; id: number; cam: number; lastStat: number; posts: Post[];
}

export interface EvalDom { canvas: HTMLCanvasElement; section: HTMLElement; stats: HTMLElement | null }
export interface EvalChoice { iou: number; run: "v1" | "v2" }

const hsh = (n: number) => { const x = Math.sin(n) * 43758.5453; return x - Math.floor(x); };

export class EvalScene {
  private E: State = { t: 0, egoX: 0, gts: [], fps: { v1: [], v2: [] }, hist: [], acc: 0, id: 1, cam: 0, lastStat: 0, posts: [] };
  private mouse: { x: number; y: number } | null = null;
  private drag: { x: number; y0: number } | null = null;
  private yaw = 0;
  private detach: () => void;

  constructor(private dom: EvalDom, private reduced: boolean) {
    const ec = dom.canvas;
    const onMove = (e: PointerEvent) => {
      const r = ec.getBoundingClientRect();
      this.mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
      if (this.drag) this.yaw = this.drag.y0 + (e.clientX - this.drag.x) * 0.006;
    };
    const onDown = (e: PointerEvent) => {
      this.drag = { x: e.clientX, y0: this.yaw };
      try { ec.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    };
    const up = () => { this.drag = null; };
    const leave = () => { this.mouse = null; };
    const dbl = () => { this.yaw = 0; };
    ec.addEventListener("pointermove", onMove);
    ec.addEventListener("pointerdown", onDown);
    ec.addEventListener("pointerup", up);
    ec.addEventListener("pointercancel", up);
    ec.addEventListener("pointerleave", leave);
    ec.addEventListener("dblclick", dbl);
    this.detach = () => {
      ec.removeEventListener("pointermove", onMove);
      ec.removeEventListener("pointerdown", onDown);
      ec.removeEventListener("pointerup", up);
      ec.removeEventListener("pointercancel", up);
      ec.removeEventListener("pointerleave", leave);
      ec.removeEventListener("dblclick", dbl);
    };
    // Same seeded posts every load; Math.random only drives the traffic below, as in the design.
    for (let i = 0; i < 70; i++) {
      this.E.posts.push({ x: i * 4.8 + hsh(i * 3.1) * 2.5, y: (hsh(i * 7.7) < 0.5 ? -1 : 1) * (13 + hsh(i * 1.3) * 7), h: 2 + hsh(i * 5.3) * 8, tree: hsh(i * 9.1) < 0.55 });
    }
  }

  dispose() {
    this.detach();
  }

  step(now: number, dtIn: number, choice: EvalChoice) {
    const cv = this.dom.canvas;
    const vh = window.innerHeight || 800, rc = cv.getBoundingClientRect();
    if (rc.bottom < 0 || rc.top > vh) return;
    const r2 = this.dom.section.getBoundingClientRect();
    const sp = Math.min(1, Math.max(0, -r2.top / Math.max(1, r2.height - vh)));
    const E = this.E;
    const thr = choice.iou, run = choice.run;
    const dt = this.reduced ? 0 : dtIn;
    E.t += dt;
    const t = E.t;
    E.egoX += 10 * dt;
    const spawn = (x: number) => {
      const r = Math.random();
      let o: Omit<Gt, "x" | "id" | "n" | "m" | "iou">;
      if (r < 0.2) o = { kind: "ped", y: (Math.random() < 0.5 ? -9 : 9) + (Math.random() - 0.5), v: (Math.random() < 0.5 ? -1 : 1) * 1.3, L: 0.8, W: 0.8, h: 1.8 };
      else if (r < 0.3) o = { kind: "truck", y: Math.random() < 0.5 ? -5.25 : 5.25, v: 0, L: 8.5, W: 2.6, h: 3.4 };
      else {
        const ln = [-5.25, -1.75, 1.75, 5.25][Math.floor(Math.random() * 4)];
        o = { kind: "car", y: ln, v: ln < 0 ? 5 + Math.random() * 4 : -(8 + Math.random() * 4), L: 4.5, W: 1.9, h: 1.5 };
      }
      if (o.kind === "truck") o.v = o.y < 0 ? 4 + Math.random() * 3 : -(7 + Math.random() * 3);
      E.gts.push({ ...o, x, id: String(E.id * 7 + 1000), n: E.id++, m: null, iou: 0 });
    };
    if (!E.gts.length) for (let i = 0; i < 16; i++) spawn(E.egoX - 20 + Math.random() * 150);
    for (const g of E.gts) g.x += g.v * dt;
    E.gts = E.gts.filter((g) => g.x > E.egoX - 40 && g.x < E.egoX + 160);
    for (let i = 0; i < 15 - E.gts.length; i++) spawn(E.egoX + 135 + Math.random() * 20);
    for (const r of ["v1", "v2"] as const) {
      const rate = r === "v1" ? 1.8 : 0.5;
      if (Math.random() < rate * dt) {
        const car = Math.random() < 0.6;
        E.fps[r].push({ x: E.egoX - 6 + Math.random() * 56, y: (Math.random() - 0.5) * 20, L: car ? 4.3 : 0.9, W: car ? 1.8 : 0.9, h: car ? 1.5 : 1.7, die: t + 0.8 + Math.random() * 0.9, sc: 0.3 + Math.random() * 0.35, m: null, iou: 0 });
      }
      E.fps[r] = E.fps[r].filter((f) => (f.die ?? 0) > t);
    }
    const x0 = E.egoX - 10, x1 = E.egoX + 55;
    const inR = (b: { x: number; y: number }) => b.x > x0 && b.x < x1 && Math.abs(b.y) < 11;
    const gts = E.gts.filter(inR);
    const PR = ({
      v1: { car: 0.86, truck: 0.9, ped: 0.6, sc: 0.9, sp: 0.3, sz: 0.12 },
      v2: { car: 0.96, truck: 0.97, ped: 0.86, sc: 0.4, sp: 0.14, sz: 0.05 },
    } as Record<string, Record<string, number>>)[run];
    const preds: Pred[] = [];
    for (const g of gts) {
      const seed = g.n * 13.7 + (run === "v2" ? 91 : 0);
      if (hsh(seed + Math.floor(t * 1.4)) > PR[g.kind]) continue;
      const sg = g.kind === "ped" ? PR.sp : PR.sc;
      const dx = sg * (0.7 * Math.sin(t * 0.8 + seed) + 0.3 * Math.sin(t * 2.3 + seed * 0.7));
      const dy = sg * 0.6 * (0.7 * Math.sin(t * 0.9 + seed * 1.3) + 0.3 * Math.sin(t * 2.1 + seed * 0.3));
      const k = 1 + PR.sz * Math.sin(t * 0.5 + seed * 2.1);
      preds.push({ x: g.x + dx, y: g.y + dy, L: g.L * k, W: g.W * k, h: g.h * k, sc: Math.min(0.99, 0.97 - Math.hypot(dx, dy) * 0.25 - hsh(seed) * 0.12), m: null, iou: 0 });
    }
    for (const f of E.fps[run]) if (inR(f)) preds.push({ ...f, m: null, iou: 0 });
    const iouOf = (a: Gt | Pred, b: Gt | Pred) => {
      const ix = Math.max(0, Math.min(a.x + a.L / 2, b.x + b.L / 2) - Math.max(a.x - a.L / 2, b.x - b.L / 2));
      const iy = Math.max(0, Math.min(a.y + a.W / 2, b.y + b.W / 2) - Math.max(a.y - a.W / 2, b.y - b.W / 2));
      const I = ix * iy;
      return I / (a.L * a.W + b.L * b.W - I);
    };
    const pairs: [number, Gt, Pred][] = [];
    for (const g of gts) for (const p of preds) { const v = iouOf(g, p); if (v > 0) pairs.push([v, g, p]); }
    pairs.sort((a, b) => b[0] - a[0]);
    for (const g of gts) { g.m = null; g.iou = 0; }
    for (const p of preds) { p.m = null; p.iou = 0; }
    for (const [v, g, p] of pairs) {
      if (g.m || p.m) continue;
      if (!g.iou) g.iou = v;
      if (!p.iou) p.iou = v;
      if (v >= thr) { g.m = p; p.m = g; g.iou = v; p.iou = v; }
    }
    const tp = gts.filter((g) => g.m).length, fn = gts.length - tp, fp = preds.length - tp;
    E.acc += dt;
    if (E.acc > 0.1 || !E.hist.length) {
      E.acc = 0;
      const w = E.hist.slice(-20);
      const pf = tp + fp ? tp / (tp + fp) : 1, rf = tp + fn ? tp / (tp + fn) : 1;
      const ps = (w.reduce((s, h) => s + h[3], 0) + pf) / (w.length + 1), rs = (w.reduce((s, h) => s + h[4], 0) + rf) / (w.length + 1);
      E.hist.push([ps, rs, E.egoX - 1.5, pf, rf]);
      if (E.hist.length > 400) E.hist.shift();
    }
    const last = E.hist[E.hist.length - 1] || [1, 1];
    const st = this.dom.stats;
    if (st && (!E.lastStat || now - E.lastStat > 120)) {
      E.lastStat = now;
      const big = (l: string, v: number, c: string) => `<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px"><span style="display:flex;align-items:center;gap:8px">${l}<span style="width:12px;height:2px;background:${c}"></span></span><span style="color:#f5f5f7;font-family:${SANS.replace(/"/g, "'")};font-size:clamp(48px,5vw,80px);letter-spacing:-0.04em;line-height:0.9">${v.toFixed(2)}</span></div>`;
      const row = (c: string, l: string, v: number) => `<span style="display:flex;align-items:center;gap:8px">${l} <span style="color:#f5f5f7;min-width:20px">${v}</span><span style="width:8px;height:8px;border-radius:1px;background:${c}"></span></span>`;
      st.innerHTML = `<div style="color:#f5f5f7">det-${run} · IoU ≥ ${thr}</div>` + big("precision", last[0], "#f5f5f7") + big("recall", last[1], "#3C8DFF") +
        `<div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">${row("#1FA3A3", "hits", tp)}${row("#8E6CEF", "misses", fn)}${row("#E0A431", "false pos.", fp)}</div>`;
    }

    // camera
    const dpr = Math.min(2, window.devicePixelRatio || 1), W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    E.cam += (ss(0.12, 0.88, sp) - E.cam) * 0.06;
    const e = E.cam;
    const yaw = this.yaw + Math.sin(t * 0.12) * 0.12 + lerp(0.18, 0.5, e);
    const back = lerp(15, 14, e), ez = lerp(4.0, 46, e);
    const ex = E.egoX - Math.cos(yaw) * back, ey = -1.75 - Math.sin(yaw) * back;
    const tx = E.egoX + lerp(26, 16, e), ty = lerp(-1, 0, e), tz = 0;
    let fx = tx - ex, fy = ty - ey, fz = tz - ez;
    const fl0 = Math.hypot(fx, fy, fz);
    fx /= fl0; fy /= fl0; fz /= fl0;
    let rx = fy, ry = -fx;
    const rz = 0;
    const rl = Math.hypot(rx, ry) || 1;
    rx /= rl; ry /= rl;
    const ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;
    const FL = H * 0.95, OX = W * (W > 900 ? 0.56 : 0.5), OY = H * 0.5;
    const cam = (x: number, y: number, z: number) => {
      const dx = x - ex, dy = y - ey, dz = z - ez;
      return [dx * rx + dy * ry + dz * rz, dx * ux + dy * uy + dz * uz, dx * fx + dy * fy + dz * fz];
    };
    const NEAR = 0.8;
    const pj = (c: number[]) => [OX + (c[0] * FL) / c[2], OY - (c[1] * FL) / c[2], c[2]];
    const P = (x: number, y: number, z: number) => { const c = cam(x, y, z); return c[2] < NEAR ? null : pj(c); };
    const seg = (a: number[], b: number[]) => {
      let A = cam(a[0], a[1], a[2]), B = cam(b[0], b[1], b[2]);
      if (A[2] < NEAR && B[2] < NEAR) return;
      if (A[2] < NEAR) { const k = (NEAR - A[2]) / (B[2] - A[2]); A = [A[0] + (B[0] - A[0]) * k, A[1] + (B[1] - A[1]) * k, NEAR]; }
      if (B[2] < NEAR) { const k = (NEAR - B[2]) / (A[2] - B[2]); B = [B[0] + (A[0] - B[0]) * k, B[1] + (A[1] - B[1]) * k, NEAR]; }
      const p = pj(A), q = pj(B);
      ctx.moveTo(p[0], p[1]);
      ctx.lineTo(q[0], q[1]);
    };
    const X0 = E.egoX - 40, X1 = E.egoX + 170;

    // ground
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,255,255,0.045)";
    ctx.beginPath();
    for (let gx = Math.ceil(X0 / 10) * 10; gx < X1; gx += 10) seg([gx, -40, 0], [gx, 40, 0]);
    for (let gy = -40; gy <= 40; gy += 10) seg([X0, gy, 0], [X1, gy, 0]);
    ctx.stroke();
    ctx.strokeStyle = "rgba(134,134,139,0.45)";
    ctx.beginPath();
    for (const y of [-7, 7]) seg([X0, y, 0], [X1, y, 0]);
    ctx.stroke();
    ctx.strokeStyle = "rgba(134,134,139,0.18)";
    ctx.beginPath();
    for (const y of [-11, 11]) seg([X0, y, 0], [X1, y, 0]);
    ctx.stroke();
    ctx.strokeStyle = "rgba(125,206,160,0.7)";
    ctx.beginPath();
    for (const y of [-0.15, 0.15]) seg([X0, y, 0], [X1, y, 0]);
    ctx.stroke();
    ctx.strokeStyle = "rgba(125,206,160,0.45)";
    ctx.beginPath();
    for (let dx0 = Math.floor(X0 / 9) * 9; dx0 < X1; dx0 += 9) for (const y of [-3.5, 3.5]) seg([dx0, y, 0], [dx0 + 4, y, 0]);
    ctx.stroke();
    ctx.setLineDash([3, 5]);
    ctx.strokeStyle = "rgba(255,255,255,0.22)";
    ctx.beginPath();
    seg([x0, -11, 0], [x1, -11, 0]);
    seg([x1, -11, 0], [x1, 11, 0]);
    seg([x1, 11, 0], [x0, 11, 0]);
    seg([x0, 11, 0], [x0, -11, 0]);
    ctx.stroke();
    ctx.setLineDash([]);
    const lab = P(x1, -11, 0.2);
    if (lab) {
      ctx.font = `11px ${MONO}`;
      ctx.fillStyle = "#86868b";
      ctx.textAlign = "right";
      ctx.fillText("eval range · world frame", lab[0] - 6, lab[1] - 8);
      ctx.textAlign = "left";
    }

    // lidar
    ctx.globalCompositeOperation = "lighter";
    const RINGS2 = [4, 6, 8.5, 11.5, 15, 19, 24, 30, 37, 45], ox0 = E.egoX, oy0 = -1.75;
    const dot = (x: number, y: number, z: number, c: string) => {
      const q = P(x, y, z);
      if (!q) return;
      const s = Math.min(3, Math.max(1, (FL * 0.045) / q[2]));
      ctx.fillStyle = c;
      ctx.fillRect(q[0] - s / 2, q[1] - s / 2, s, s);
    };
    const dcol = (d: number) => { const k = Math.min(1, d / 45); return "rgb(255," + Math.round(lerp(255, 138, k)) + "," + Math.round(lerp(255, 31, k)) + ")"; };
    ctx.globalAlpha = 0.75;
    for (let i = 0; i < 300; i++) {
      const a = (i / 300) * Math.PI * 2 + t * 0.05, ca = Math.cos(a), sa = Math.sin(a);
      for (let j = 0; j < RINGS2.length; j++) {
        const R = RINGS2[j] + Math.sin(i * 12.9 + j * 3.1) * 0.5;
        const px = ox0 + ca * R, py = oy0 + sa * R;
        if (Math.abs(py) > 24) break;
        dot(px, py, 0, dcol(R));
      }
    }
    ctx.globalAlpha = 0.9;
    for (const g of E.gts) {
      const d = Math.hypot(g.x - ox0, g.y - oy0);
      if (d > 48) continue;
      const c = dcol(d), fy2 = g.y > oy0 ? g.y - g.W / 2 : g.y + g.W / 2, fxe = g.x > ox0 ? g.x - g.L / 2 : g.x + g.L / 2;
      for (let z = 0.25; z < g.h; z += 0.32) {
        for (let u = -g.L / 2; u <= g.L / 2; u += 0.45) dot(g.x + u, fy2, z, c);
        for (let v = -g.W / 2; v <= g.W / 2; v += 0.45) dot(fxe, g.y + v, z, c);
      }
    }
    for (const p of E.posts) {
      const wx = p.x + Math.ceil((X0 - p.x) / 336) * 336;
      const d = Math.hypot(wx - ox0, p.y - oy0);
      const c = d < 48 ? dcol(d) : "rgb(110,70,30)";
      ctx.globalAlpha = d < 48 ? 0.85 : 0.35;
      for (let z = 0.3; z < p.h; z += 0.4) dot(wx, p.y, z, c);
      if (p.tree) for (let k = 0; k < 22; k++) { const a = k * 2.4, r = 1.2 + (k % 3) * 0.5; dot(wx + Math.cos(a) * r, p.y + Math.sin(a) * r, p.h - 0.6 + (k % 4) * 0.4, c); }
    }
    // precision / recall fence
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2;
    for (const [j, col, yy] of [[0, "rgba(245,245,247,0.9)", -12.5], [1, "rgba(60,141,255,0.95)", -12.5]] as [number, string, number][]) {
      ctx.strokeStyle = col;
      ctx.beginPath();
      let prev: number[] | null = null;
      for (const h of E.hist) {
        if (h[2] < X0) { prev = null; continue; }
        const cur = [h[2], yy, 0.3 + h[j] * 6];
        if (prev) seg(prev, cur);
        prev = cur;
      }
      ctx.stroke();
    }
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.beginPath();
    E.hist.forEach((h, i) => { if (i % 4 || h[2] < X0) return; seg([h[2], -12.5, 0], [h[2], -12.5, 0.3 + Math.max(h[0], h[1]) * 6]); });
    seg([X0, -12.5, 6.3], [E.egoX, -12.5, 6.3]);
    ctx.stroke();
    ctx.globalCompositeOperation = "source-over";
    if (E.hist.length) {
      const h = E.hist[E.hist.length - 1];
      ctx.font = `500 11px ${MONO}`;
      const a1 = P(h[2], -12.5, 0.3 + h[0] * 6 + 0.6), a2 = P(h[2], -12.5, 0.3 + h[1] * 6 - 0.6);
      if (a1) { ctx.fillStyle = "#f5f5f7"; ctx.fillText("precision " + h[0].toFixed(2), a1[0] + 8, a1[1]); }
      if (a2) { ctx.fillStyle = "#7AA7FF"; ctx.fillText("recall " + h[1].toFixed(2), a2[0] + 8, a2[1] + 10); }
    }

    // boxes
    type Box = { x: number; y: number; L: number; W: number; h: number };
    const corners = (b: Box) => {
      const hl = b.L / 2, hw = b.W / 2;
      return [[hl, hw, 0], [-hl, hw, 0], [-hl, -hw, 0], [hl, -hw, 0], [hl, hw, b.h], [-hl, hw, b.h], [-hl, -hw, b.h], [hl, -hw, b.h]].map(([u, v, z]) => [b.x + u, b.y + v, z]);
    };
    const FACES = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
    const EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
    const drawBox = (b: Box, fill: string | null, stroke: string | null, lw: number, solid?: (f: number[]) => string) => {
      const C = corners(b), CC = C.map((c) => cam(c[0], c[1], c[2]));
      if (fill) {
        const fs = FACES.map((f) => [f, f.reduce((s, i) => s + CC[i][2], 0) / 4] as [number[], number]).sort((a, b2) => b2[1] - a[1]);
        for (const [f] of fs) {
          if (f.some((i) => CC[i][2] < NEAR)) continue;
          ctx.beginPath();
          f.forEach((i, k) => { const q = pj(CC[i]); if (k) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
          ctx.closePath();
          ctx.fillStyle = solid ? solid(f) : fill;
          ctx.fill();
        }
      }
      if (stroke) {
        ctx.strokeStyle = stroke;
        ctx.lineWidth = lw;
        ctx.beginPath();
        for (const [i, j] of EDGES) seg(C[i], C[j]);
        ctx.stroke();
        ctx.lineWidth = 1;
      }
    };
    type Item = [number, "g", Gt] | [number, "p", Pred] | [number, "ego", Box];
    const items: Item[] = [];
    for (const g of E.gts) items.push([cam(g.x, g.y, 0)[2], "g", g]);
    for (const p of preds) items.push([cam(p.x, p.y, 0)[2] - 0.01, "p", p]);
    items.push([cam(E.egoX, -1.75, 0)[2], "ego", { x: E.egoX, y: -1.75, L: 4.6, W: 2.0, h: 1.6 }]);
    items.sort((a, b) => b[0] - a[0]);
    for (const item of items) {
      if (item[1] === "ego") {
        const b = item[2];
        drawBox(b, "#F58A1F", "rgba(255,200,150,0.9)", 1, (f) => (f[0] === 4 ? "#F58A1F" : "#8a4a10"));
        const q = P(b.x, b.y, 1.6);
        if (q) {
          ctx.globalCompositeOperation = "lighter";
          const gr = ctx.createRadialGradient(q[0], q[1], 0, q[0], q[1], 60);
          gr.addColorStop(0, "rgba(245,138,31,0.45)");
          gr.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = gr;
          ctx.fillRect(q[0] - 60, q[1] - 60, 120, 120);
          ctx.globalCompositeOperation = "source-over";
        }
      } else if (item[1] === "g") {
        const b = item[2], ev = inR(b);
        drawBox(b, null, ev ? (b.m ? "#1FA3A3" : "#8E6CEF") : "rgba(255,255,255,0.22)", ev ? 1.6 : 1);
      } else {
        const b = item[2], c = b.m ? "31,163,163" : "224,164,49";
        drawBox(b, "rgba(" + c + ",0.13)", "rgba(" + c + ",0.55)", 1);
      }
    }
    ctx.font = `500 11px ${MONO}`;
    ctx.textAlign = "center";
    for (const g of gts) { const q = P(g.x, g.y, g.h + 1.0); if (!q) continue; ctx.fillStyle = g.m ? "#5fd0d0" : "#b39cf5"; ctx.fillText(g.m ? "IoU " + g.iou.toFixed(2) : "miss", q[0], q[1]); }
    for (const p of preds) { if (p.m) continue; const q = P(p.x, p.y, p.h + 1.0); if (!q) continue; ctx.fillStyle = "#E0A431"; ctx.fillText("fp " + p.sc.toFixed(2), q[0], q[1]); }
    const eq = P(E.egoX, -1.75, 3.2);
    if (eq) { ctx.fillStyle = "#f5f5f7"; ctx.fillText("vehicle 1 · ego", eq[0], eq[1]); }
    ctx.textAlign = "left";

    // hover
    const m = this.mouse;
    if (m && !this.drag) {
      let hit: { g?: Gt; p?: Pred; q: number[] } | null = null, best = 40;
      for (const g of gts) { const q = P(g.x, g.y, g.h / 2); if (!q) continue; const d = Math.hypot(q[0] - m.x, q[1] - m.y); if (d < best) { best = d; hit = { g, q }; } }
      for (const p of preds) { if (p.m) continue; const q = P(p.x, p.y, p.h / 2); if (!q) continue; const d = Math.hypot(q[0] - m.x, q[1] - m.y); if (d < best) { best = d; hit = { p, q }; } }
      if (hit) {
        const g = hit.g, p = hit.p;
        const title = g ? g.kind + " · track " + g.id : "false positive", col = g ? (g.m ? "#1FA3A3" : "#8E6CEF") : "#E0A431";
        const lines = g
          ? g.m
            ? ["matched · IoU " + g.iou.toFixed(2) + " ≥ " + thr, "score " + g.m.sc.toFixed(2), "offset " + Math.hypot(g.m.x - g.x, g.m.y - g.y).toFixed(2) + " m"]
            : [g.iou ? "best IoU " + g.iou.toFixed(2) + " < " + thr : "no prediction overlaps", "counts as a miss", Math.abs(g.v).toFixed(1) + " m/s"]
          : p ? ["score " + p.sc.toFixed(2), p.iou ? "best IoU " + p.iou.toFixed(2) + " < " + thr : "no ground truth here"] : [];
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(hit.q[0], hit.q[1], 18, 0, 7);
        ctx.stroke();
        ctx.lineWidth = 1;
        ctx.font = `500 12px ${MONO}`;
        let w = ctx.measureText(title).width;
        ctx.font = `11px ${MONO}`;
        for (const l of lines) w = Math.max(w, ctx.measureText(l).width);
        const bw = w + 40, bh2 = 34 + lines.length * 17;
        let tx2 = m.x + 18, ty2 = m.y + 18;
        if (tx2 + bw > W - 8) tx2 = m.x - 18 - bw;
        if (ty2 + bh2 > H - 8) ty2 = m.y - 18 - bh2;
        ctx.fillStyle = "rgba(12,12,14,0.94)";
        ctx.strokeStyle = "rgba(255,255,255,0.16)";
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(tx2, ty2, bw, bh2, 10); else ctx.rect(tx2, ty2, bw, bh2);
        ctx.fill();
        ctx.stroke();
        ctx.textBaseline = "middle";
        ctx.fillStyle = col;
        ctx.fillRect(tx2 + 14, ty2 + 16, 8, 8);
        ctx.fillStyle = "#f5f5f7";
        ctx.font = `500 12px ${MONO}`;
        ctx.fillText(title, tx2 + 28, ty2 + 20);
        ctx.fillStyle = "#a1a1a6";
        ctx.font = `11px ${MONO}`;
        lines.forEach((l, i) => ctx.fillText(l, tx2 + 14, ty2 + 42 + i * 17));
        ctx.textBaseline = "alphabetic";
      }
    }
    cv.style.cursor = this.drag ? "grabbing" : "grab";
  }
}
