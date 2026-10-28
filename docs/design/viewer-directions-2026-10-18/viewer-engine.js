// Synthetic Griffin-like scene: a vehicle and a UAV on one 10 Hz clock, with eval statuses.
(function () {
  const N = 200, T_END = 19.9;
  const C = { tp: '#1FA3A3', fp: '#E0A431', miss: '#8E6CEF', veh: '#F58A1F', uav: '#3C8DFF' };
  const CLS = { car: '#e67828', truck: '#c45c26', bus: '#d4a017', pedestrian: '#3d9a86', cyclist: '#2878dc', barrier: '#9aa7b2' };
  function hash(a, b) { const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return x - Math.floor(x); }
  function along(s, lat) {
    const y = 14 * Math.sin(s / 55), h = Math.atan2((14 / 55) * Math.cos(s / 55), 1);
    return { x: s - Math.sin(h) * lat, y: y + Math.cos(h) * lat, h };
  }
  const OBJ = [
    { id: 1, cat: 'car', s0: 22, lat: -1.75, v: 6.6, l: 4.6, w: 1.9, h: 1.5 },
    { id: 2, cat: 'car', s0: 120, lat: 1.75, v: -8, l: 4.6, w: 1.9, h: 1.5 },
    { id: 3, cat: 'truck', s0: 200, lat: 1.75, v: -6.5, l: 8, w: 2.5, h: 3.2 },
    { id: 4, cat: 'bus', s0: 96, lat: -5.4, v: 0, l: 12, w: 2.6, h: 3.2 },
    { id: 5, cat: 'pedestrian', s0: 93, lat: -8.2, v: 0.35, l: 0.6, w: 0.6, h: 1.75 },
    { id: 6, cat: 'pedestrian', s0: 97, lat: -8.8, v: 0.25, l: 0.6, w: 0.6, h: 1.7 },
    { id: 7, cat: 'pedestrian', s0: 100.5, lat: -8, v: -0.3, l: 0.6, w: 0.6, h: 1.8 },
    { id: 8, cat: 'cyclist', s0: 58, lat: -4, v: 3.8, l: 1.8, w: 0.6, h: 1.6 },
    { id: 9, cat: 'car', s0: 118, lat: 5.6, v: 0, l: 4.6, w: 1.9, h: 1.5 },
    { id: 10, cat: 'car', s0: 112, lat: -1.75, v: 2.2, l: 4.6, w: 1.9, h: 1.5 },
    { id: 11, cat: 'pedestrian', s0: 150, lat: 8, v: -0.4, l: 0.6, w: 0.6, h: 1.7 },
    { id: 12, cat: 'car', s0: -8, lat: -1.75, v: 7.6, l: 4.6, w: 1.9, h: 1.5 },
    { id: 13, cat: 'barrier', s0: 78, lat: 4.6, v: 0, l: 2, w: 0.4, h: 1 },
  ];
  OBJ.forEach((o) => { o.pts = []; for (let i = 0; i < 26; i++) o.pts.push([hash(o.id, i) - 0.5, hash(o.id, i + 50) - 0.5, hash(o.id, i + 99)]); });
  function vehAt(t) { const s = 5 + 7.5 * t; const p = along(s, -1.75); return { s, x: p.x, y: p.y, z: 0, h: p.h }; }
  function uavAt(t) { const s = 19 + 7.5 * t; const p = along(s, 4); return { s, x: p.x, y: p.y, z: 24, h: p.h }; }
  function objAt(o, t) { const s = o.s0 + o.v * t; const p = along(s, o.lat); return { x: p.x, y: p.y, h: p.h + (o.v < 0 ? Math.PI : 0) }; }
  function occluder(from, to, self, t) {
    for (const o of OBJ) {
      if (o === self || (o.cat !== 'bus' && o.cat !== 'truck')) continue;
      const q = objAt(o, t), c = Math.cos(-q.h), sn = Math.sin(-q.h);
      for (let j = 2; j < 30; j++) {
        const f = j / 31, x = from.x + (to.x - from.x) * f - q.x, y = from.y + (to.y - from.y) * f - q.y;
        const lx = x * c - y * sn, ly = x * sn + y * c;
        if (Math.abs(lx) < o.l / 2 && Math.abs(ly) < o.w / 2) return o;
      }
    }
    return null;
  }
  const frames = [];
  for (let k = 0; k < N; k++) {
    const t = k / 10, V = vehAt(t), U = uavAt(t);
    const objs = OBJ.map((o) => {
      const p = objAt(o, t), dv = Math.hypot(p.x - V.x, p.y - V.y), du = Math.hypot(p.x - U.x, p.y - U.y);
      const inRange = dv <= 45, occ = inRange ? occluder(V, p, o, t) : null;
      const vVis = inRange && !occ, dVis = du <= 55;
      const b = Math.floor(k / 3);
      const vDet = vVis && hash(o.id, b) < (dv > 32 ? 0.72 : 0.95);
      const dDet = dVis && hash(o.id + 40, b) < 0.93;
      let coop = null;
      if (inRange) coop = vDet && dDet ? 'both' : vDet ? 'vehicle' : dDet ? 'drone' : 'nobody';
      return { o, p, dv, inRange, occ, vVis, dVis, vDet, dDet, coop,
        sV: inRange ? (vDet ? 'tp' : 'miss') : null, sF: inRange ? (vDet || dDet ? 'tp' : 'miss') : null,
        score: 0.45 + hash(o.id, k) * 0.5 };
    });
    const bl = Math.floor(k / 7), ghosts = [];
    if (hash(bl, 9) < 0.5) { const g = along(V.s + 12 + hash(bl, 3) * 26, -9 + hash(bl, 4) * 18); ghosts.push({ x: g.x, y: g.y, h: g.h, fused: hash(bl, 5) < 0.45, score: 0.31 + hash(bl, 6) * 0.3 }); }
    frames.push({ k, t, V, U, objs, ghosts });
  }
  const S = { recallV: [], recallF: [], precV: [], precF: [], fpV: [], points: [], speed: [], boxes: [] };
  let tot = { tpV: 0, missV: 0, fpV: 0, tpF: 0, missF: 0, fpF: 0, both: 0, vehicle: 0, drone: 0, nobody: 0 };
  frames.forEach((f) => {
    let tpV = 0, mV = 0, tpF = 0, mF = 0, vis = 0;
    f.objs.forEach((r) => { if (!r.inRange) return; vis++; r.vDet ? tpV++ : mV++; r.sF === 'tp' ? tpF++ : mF++; tot[r.coop]++; });
    const fpV = f.ghosts.length, fpF = f.ghosts.filter((g) => g.fused).length;
    Object.assign(f, { tpV, mV, tpF, mF, fpV, fpF, gt: vis });
    tot.tpV += tpV; tot.missV += mV; tot.fpV += fpV; tot.tpF += tpF; tot.missF += mF; tot.fpF += fpF;
    S.recallV.push(vis ? tpV / vis : 1); S.recallF.push(vis ? tpF / vis : 1);
    S.precV.push(tpV + fpV ? tpV / (tpV + fpV) : 1); S.precF.push(tpF + fpF ? tpF / (tpF + fpF) : 1);
    S.fpV.push(fpV); S.points.push(29000 + vis * 420 + hash(f.k, 1) * 1800);
    S.speed.push(7.5 + Math.sin(f.k / 18) * 0.35); S.boxes.push(vis);
  });
  const M = {
    recallV: tot.tpV / (tot.tpV + tot.missV), recallF: tot.tpF / (tot.tpF + tot.missF),
    precV: tot.tpV / (tot.tpV + tot.fpV), precF: tot.tpF / (tot.tpF + tot.fpF),
  };
  M.map5V = M.recallV * M.precV * 0.97; M.map7V = M.map5V * 0.78; M.map5F = M.recallF * M.precF * 0.97; M.map7F = M.map5F * 0.8;
  M.coop = { both: tot.both, vehicle: tot.vehicle, drone: tot.drone, nobody: tot.nobody };
  // tracks of recovered GT for chapters
  let kA = -1, kB = -1;
  frames.forEach((f) => { if (f.objs.some((r) => r.coop === 'drone' && r.occ)) { if (kA < 0) kA = f.k; kB = f.k; } });
  M.occA = kA; M.occB = kB;

  function path(values, height, max) {
    const top = height - 1, ceil = max > 1e-6 ? max : 1e-6;
    return values.map((v, i) => `${i ? 'L' : 'M'}${((i / (values.length - 1)) * 100).toFixed(2)} ${(top - (Math.max(0, v) / ceil) * (top - 1)).toFixed(2)}`).join(' ');
  }
  function area(values, height, max) { return `${path(values, height, max)} L100 ${height} L0 ${height} Z`; }
  function band(a, b, height) {
    const top = height - 1, pa = a.map((v, i) => [(i / (a.length - 1)) * 100, top - v * (top - 1)]);
    const pb = b.map((v, i) => [(i / (b.length - 1)) * 100, top - v * (top - 1)]).reverse();
    return pa.concat(pb).map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join(' ') + ' Z';
  }
  function minMax(v) { let a = Infinity, b = -Infinity; v.forEach((x) => { a = Math.min(a, x); b = Math.max(b, x); }); return [a, b]; }
  function norm(v) { const [a, b] = minMax(v); return v.map((x) => (x - a * 0.9) / (b - a * 0.9)); }

  // ---------- drawing ----------
  function fit(cv) {
    const r = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return null;
    if (cv.width !== Math.round(w * r) || cv.height !== Math.round(h * r)) { cv.width = Math.round(w * r); cv.height = Math.round(h * r); }
    const ctx = cv.getContext('2d'); ctx.setTransform(r, 0, 0, r, 0, 0); ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }
  function lerpPose(a, b, f) { return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z, h: a.h + (b.h - a.h) * f }; }
  function stateAt(t) {
    const k = Math.min(N - 1, Math.floor(t * 10)), f = frames[k];
    return { k, f, V: vehAt(t), U: uavAt(t), objs: f.objs.map((r) => Object.assign({}, r, { p: objAt(r.o, t) })) };
  }
  const ENV = [];
  for (let s = -30; s < 230; s += 1.1) {
    for (const side of [-1, 1]) {
      const lat = side * (12.5 + hash(s, side) * 2.5), p = along(s, lat);
      for (let z = 0; z < 4; z++) ENV.push([p.x, p.y, hash(s, z + side) * 7]);
      const e = along(s, side * 3.7); ENV.push([e.x, e.y, 0.05]);
    }
    for (let i = 0; i < 6; i++) { const p = along(s + hash(s, i) , -11 + hash(i, s) * 22); ENV.push([p.x, p.y, 0]); }
  }
  function colorOf(r, mode, blend) {
    if (mode === 'drone') return r.dDet ? C.tp : C.miss;
    if (mode === 'fused') {
      if (r.vDet) return C.tp;
      if (r.dDet) return mix(C.miss, C.tp, blend);
      return C.miss;
    }
    return r.vDet ? C.tp : C.miss;
  }
  function mix(a, b, f) {
    const pa = [1, 3, 5].map((i) => parseInt(a.substr(i, 2), 16)), pb = [1, 3, 5].map((i) => parseInt(b.substr(i, 2), 16));
    return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * f).toString(16).padStart(2, '0')).join('');
  }
  function corners(p, o) {
    const c = Math.cos(p.h), s = Math.sin(p.h), out = [];
    for (const z of [0, o.h]) for (const [a, b] of [[1, 1], [1, -1], [-1, -1], [-1, 1]]) {
      const lx = (a * o.l) / 2, ly = (b * o.w) / 2; out.push([p.x + lx * c - ly * s, p.y + lx * s + ly * c, z]);
    }
    return out;
  }
  const EDGES = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];

  function makeCam(cfg, V, w, h, t) {
    const yaw = V.h + Math.PI + (cfg.yaw || 0.45) + (cfg.sway ? Math.sin(t * 0.22) * cfg.sway : 0);
    const d = cfg.dist || 32, ht = cfg.height || 18, ahead = cfg.ahead || 12;
    const T = [V.x + Math.cos(V.h) * ahead, V.y + Math.sin(V.h) * ahead, 0];
    const P = [V.x + Math.cos(yaw) * d, V.y + Math.sin(yaw) * d, ht];
    let f = [T[0] - P[0], T[1] - P[1], T[2] - P[2]]; const fl = Math.hypot(...f); f = f.map((v) => v / fl);
    let r = [f[1], -f[0], 0]; const rl = Math.hypot(...r); r = r.map((v) => v / rl);
    const u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]];
    const foc = h * (cfg.focal || 1.15);
    return (x, y, z) => {
      const dx = x - P[0], dy = y - P[1], dz = z - P[2];
      const zc = dx * f[0] + dy * f[1] + dz * f[2]; if (zc < 0.6) return null;
      return [w / 2 + ((dx * r[0] + dy * r[1] + dz * r[2]) / zc) * foc, h / 2 - ((dx * u[0] + dy * u[1] + dz * u[2]) / zc) * foc, zc];
    };
  }
  function makeBev(cfg, V, U, w, h) {
    const cx = cfg.center === 'mid' ? (V.x + U.x) / 2 + 6 : V.x + (cfg.offset || 0), cy = cfg.center === 'mid' ? (V.y + U.y) / 2 : V.y;
    const sc = cfg.scale || 5;
    return (x, y) => [w / 2 + (x - cx) * sc, h / 2 - (y - cy) * sc, 1];
  }

  function draw(cv, t, cfg) {
    const F = fit(cv); if (!F) return; const { ctx, w, h } = F;
    const st = stateAt(t), { V, U } = st, dark = cfg.dark, mode = cfg.mode || 'vehicle', blend = cfg.blend ?? 1;
    const ink = dark ? '244,244,237' : '28,28,28';
    const persp = cfg.view === 'persp';
    const pr = persp ? makeCam(cfg, V, w, h, t) : makeBev(cfg, V, U, w, h);
    const sc = cfg.scale || 5;
    // map lanes
    ctx.lineWidth = 1;
    for (const lat of [-3.6, 3.6, 0]) {
      ctx.beginPath(); let started = false;
      ctx.strokeStyle = lat === 0 ? `rgba(${ink},0.18)` : `rgba(${ink},0.28)`;
      ctx.setLineDash(lat === 0 ? [6, 8] : []);
      for (let s = V.s - 70; s < V.s + 110; s += 2) {
        const p = along(s, lat), q = pr(p.x, p.y, 0); if (!q) { started = false; continue; }
        started ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); started = true;
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    if (!persp) {
      ctx.fillStyle = `rgba(${ink},0.04)`; ctx.beginPath();
      for (let s = V.s - 80; s < V.s + 120; s += 3) { const p = along(s, -3.6), q = pr(p.x, p.y); s === V.s - 80 ? ctx.moveTo(q[0], q[1]) : ctx.lineTo(q[0], q[1]); }
      for (let s = V.s + 120; s > V.s - 80; s -= 3) { const p = along(s, 3.6), q = pr(p.x, p.y); ctx.lineTo(q[0], q[1]); }
      ctx.fill();
    }
    // lidar env
    const showV = mode !== 'drone', showU = mode !== 'vehicle';
    for (const pt of ENV) {
      const dv = Math.hypot(pt[0] - V.x, pt[1] - V.y), du = Math.hypot(pt[0] - U.x, pt[1] - U.y);
      const inV = showV && dv < 48, inU = showU && du < 58; if (!inV && !inU) continue;
      const q = pr(pt[0], pt[1], pt[2]); if (!q || q[0] < -5 || q[0] > w + 5 || q[1] < -5 || q[1] > h + 5) continue;
      const a = persp ? Math.max(0.12, 0.8 - q[2] / 90) : 0.5;
      ctx.fillStyle = cfg.colorBy === 'mono' ? `rgba(${ink},${a * 0.55})` : inV ? `rgba(245,138,31,${a * 0.7})` : `rgba(60,141,255,${a * 0.7})`;
      const sz = persp ? Math.max(1, 2.4 - q[2] / 40) : 1.4; ctx.fillRect(q[0], q[1], sz, sz);
    }
    // range rings (bev)
    if (!persp && cfg.rings !== false) {
      ctx.setLineDash([3, 5]);
      if (showV) { const q = pr(V.x, V.y); ctx.strokeStyle = 'rgba(245,138,31,0.45)'; ctx.beginPath(); ctx.arc(q[0], q[1], 45 * sc, 0, 7); ctx.stroke(); ctx.fillStyle = 'rgba(245,138,31,0.04)'; ctx.fill(); }
      if (showU) { const q = pr(U.x, U.y); ctx.strokeStyle = 'rgba(60,141,255,0.45)'; ctx.beginPath(); ctx.arc(q[0], q[1], 55 * sc, 0, 7); ctx.stroke(); ctx.fillStyle = 'rgba(60,141,255,0.04)'; ctx.fill(); }
      ctx.setLineDash([]);
    }
    // sight lines for occluded objects
    if (cfg.rays && showV) {
      st.objs.forEach((r) => {
        if (!r.occ) return; const a = pr(V.x, V.y, 1.6), b = pr(r.p.x, r.p.y, 1);
        if (!a || !b) return; ctx.strokeStyle = 'rgba(142,108,239,0.55)'; ctx.setLineDash([2, 4]); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
      });
    }
    // objects
    st.objs.forEach((r) => {
      const o = r.o, seenV = showV && r.vVis, seenU = showU && r.dVis;
      const cs = corners(r.p, o);
      for (const u of o.pts) {
        if (!seenV && !seenU) break;
        const c = Math.cos(r.p.h), s = Math.sin(r.p.h), lx = u[0] * o.l, ly = u[1] * o.w;
        const q = pr(r.p.x + lx * c - ly * s, r.p.y + lx * s + ly * c, u[2] * o.h); if (!q) continue;
        ctx.fillStyle = cfg.colorBy === 'mono' ? `rgba(${ink},0.8)` : seenV ? 'rgba(255,170,90,0.95)' : 'rgba(120,175,255,0.95)';
        ctx.fillRect(q[0], q[1], 2, 2);
      }
      if (!r.inRange && cfg.allBoxes !== true) return;
      if (mode === 'drone' && !r.inRange) return;
      let col = r.inRange ? colorOf(r, mode, blend) : `rgba(${ink},0.3)`;
      if (cfg.colorBy === 'class') col = CLS[o.cat] || '#999';
      const isMiss = r.inRange && col === C.miss;
      if (cfg.hidden && r.inRange && cfg.hidden[isMiss ? 'miss' : 'tp']) return;
      const sel = cfg.selected === o.id;
      ctx.strokeStyle = col; ctx.lineWidth = sel ? 2.4 : persp ? 1.4 : 1.6; ctx.setLineDash(isMiss ? [5, 3] : []);
      ctx.beginPath();
      const E = persp ? EDGES : EDGES.slice(4, 8);
      for (const [a, b] of E) { const p1 = pr(...cs[a]), p2 = pr(...cs[b]); if (!p1 || !p2) continue; ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); }
      ctx.stroke(); ctx.setLineDash([]);
      const recovered = mode === 'fused' && r.coop === 'drone';
      if (recovered && cfg.glow) {
        const q = pr(r.p.x, r.p.y, o.h / 2); if (q) { const rad = (persp ? 220 / q[2] : 3 * sc) * (1 + 0.25 * Math.sin(t * 6)); ctx.strokeStyle = `rgba(31,163,163,${0.5 * blend})`; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(q[0], q[1], Math.max(6, rad), 0, 7); ctx.stroke(); }
      }
      if (sel) { const q = pr(r.p.x, r.p.y, o.h); if (q) { ctx.strokeStyle = `rgba(${ink},0.9)`; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(q[0], q[1], 10 + 3 * Math.sin(t * 5), 0, 7); ctx.stroke(); } }
      if (cfg.labels && r.inRange && persp) {
        const q = pr(r.p.x, r.p.y, o.h + 0.6); if (q && q[2] < 60) { ctx.fillStyle = `rgba(${ink},0.75)`; ctx.font = '500 10px "Geist Mono", ui-monospace, monospace'; ctx.fillText(`${o.cat} ${o.id}`, q[0] + 4, q[1]); }
      }
    });
    // false positives
    if (!(cfg.hidden && cfg.hidden.fp)) st.f.ghosts.forEach((g) => {
      if (mode === 'drone') return; if (mode === 'fused' && !g.fused) return;
      const cs = corners(g, { l: 4.4, w: 1.8, h: 1.4 }); ctx.strokeStyle = C.fp; ctx.lineWidth = 1.3; ctx.setLineDash([2, 3]); ctx.beginPath();
      for (const [a, b] of (persp ? EDGES : EDGES.slice(4, 8))) { const p1 = pr(...cs[a]), p2 = pr(...cs[b]); if (!p1 || !p2) continue; ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); }
      ctx.stroke(); ctx.setLineDash([]);
    });
    // agents: trails, vehicle body, drone + leader
    const trail = (fn, col) => { ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.beginPath(); let st0 = false; for (let tt = Math.max(0, t - 6); tt <= t; tt += 0.2) { const a = fn(tt), q = pr(a.x, a.y, a.z); if (!q) continue; st0 ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); st0 = true; } ctx.stroke(); };
    if (showV) {
      trail(vehAt, 'rgba(245,138,31,0.5)');
      const cs = corners(V, { l: 4.6, w: 1.9, h: 1.5 }); ctx.fillStyle = 'rgba(245,138,31,0.35)'; ctx.strokeStyle = C.veh; ctx.lineWidth = 1.8;
      ctx.beginPath(); const top = [0, 1, 2, 3].map((i) => pr(...cs[persp ? i + 4 : i])).filter(Boolean); top.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]))); ctx.closePath(); ctx.fill(); ctx.stroke();
      if (persp) { ctx.beginPath(); for (const [a, b] of EDGES) { const p1 = pr(...cs[a]), p2 = pr(...cs[b]); if (p1 && p2) { ctx.moveTo(p1[0], p1[1]); ctx.lineTo(p2[0], p2[1]); } } ctx.stroke(); }
    }
    if (showU) {
      trail(uavAt, 'rgba(60,141,255,0.5)');
      const q = pr(U.x, U.y, persp ? U.z : 0), g = pr(U.x, U.y, 0);
      if (q) {
        if (persp && g) { ctx.strokeStyle = 'rgba(60,141,255,0.5)'; ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.moveTo(q[0], q[1]); ctx.lineTo(g[0], g[1]); ctx.stroke(); ctx.setLineDash([]); }
        ctx.fillStyle = C.uav; ctx.strokeStyle = dark ? '#0c0c0b' : '#fff'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(q[0], q[1], persp ? 6 : 5, 0, 7); ctx.fill(); ctx.stroke();
        ctx.strokeStyle = C.uav; ctx.lineWidth = 1.5; for (const a of [0.8, 2.35, 3.9, 5.5]) { ctx.beginPath(); ctx.arc(q[0] + Math.cos(a) * 9, q[1] + Math.sin(a) * 9, 3.2, 0, 7); ctx.stroke(); }
      }
    }
  }

  function drawRadar(cv, t) {
    const F = fit(cv); if (!F) return; const { ctx, w } = F, half = w / 2, range = 80;
    const st = stateAt(t), V = st.V;
    ctx.fillStyle = '#141414'; ctx.beginPath(); ctx.roundRect(0, 0, w, w, 8); ctx.fill();
    ctx.strokeStyle = '#2e2e2e'; [20, 40, 60, 80].forEach((r) => { ctx.beginPath(); ctx.arc(half, half, (r / range) * (half - 10), 0, 7); ctx.stroke(); });
    ctx.strokeStyle = '#2a2a2a'; ctx.beginPath(); ctx.moveTo(half, 12); ctx.lineTo(half, w - 12); ctx.moveTo(12, half); ctx.lineTo(w - 12, half); ctx.stroke();
    ctx.fillStyle = '#a3a3a3'; ctx.font = '9px Geist, sans-serif'; ctx.fillText('N', half + 4, 18);
    const place = (x, y) => [half + ((x - V.x) / range) * (half - 10), half - ((y - V.y) / range) * (half - 10)];
    st.objs.forEach((r) => { const p = place(r.p.x, r.p.y); if (p[0] < 4 || p[1] < 4 || p[0] > w - 4 || p[1] > w - 4) return; ctx.fillStyle = '#8a8a8a'; ctx.beginPath(); ctx.arc(p[0], p[1], 1.6, 0, 7); ctx.fill(); });
    [[V, '#F58A1F'], [st.U, '#3C8DFF']].forEach(([a, c]) => { const p = place(a.x, a.y); ctx.fillStyle = c; ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, 7); ctx.fill(); });
  }

  function drawRibbon(cv, t, selected, dark) {
    const F = fit(cv); if (!F) return; const { ctx, w, h } = F, rows = OBJ.length, rh = h / rows, cw = w / N;
    const col = { both: C.tp, vehicle: C.veh, drone: C.uav, nobody: C.miss };
    OBJ.forEach((o, i) => {
      if (selected === o.id) { ctx.fillStyle = dark ? 'rgba(244,244,237,0.06)' : 'rgba(28,28,28,0.05)'; ctx.fillRect(0, i * rh, w, rh); }
      for (let k = 0; k < N; k++) {
        const r = frames[k].objs[i]; if (!r.coop) continue;
        ctx.fillStyle = col[r.coop]; ctx.globalAlpha = selected && selected !== o.id ? 0.35 : 1;
        ctx.fillRect(k * cw, i * rh + rh * 0.28, cw + 0.4, rh * 0.44);
      }
      ctx.globalAlpha = 1;
    });
    const x = (t / T_END) * w; ctx.fillStyle = '#de3b21'; ctx.fillRect(x - 1, 0, 2, h);
  }

  window.CPEngine = { N, T_END, C, CLS, OBJ, frames, S, M, path, area, band, norm, draw, drawRadar, drawRibbon, stateAt, vehAt, uavAt };
})();
