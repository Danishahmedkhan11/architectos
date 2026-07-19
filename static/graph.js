/* ArchitectOS graph engine — custom force-directed canvas renderer, zero deps. */
"use strict";

const NODE_COLORS = {
  file: "#4cc9f0",
  function: "#a78bfa",
  class: "#f472b6",
  endpoint: "#34d399",
  model: "#fbbf24",
  doc: "#94a3b8",
  package: "#5b6478",
};

const RISK_COLORS = {
  seed: "#ff4d6d",
  high: "#ff8c42",
  medium: "#ffd166",
  low: "#7dd3fc",
};

const EDGE_ALPHA = { default: 0.16, CALLS_API: 0.34, USES_MODEL: 0.22, DOCUMENTS: 0.12 };

// Above this many nodes, dense repos read as an unreadable hairball — auto-hide
// the two highest-count, lowest-landmark-value types (function/class) so the
// graph opens legible; the user can still click them back on via the filter chips.
const LARGE_GRAPH_NODE_THRESHOLD = 200;
const AUTO_HIDE_TYPES_ON_LARGE_GRAPH = ["function", "class"];

function clusterColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360}, 55%, 62%)`;
}

class GraphView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.nodes = [];
    this.edges = [];
    this.byId = new Map();
    this.cam = { x: 0, y: 0, k: 1 };
    this.alpha = 0;
    this.hovered = null;
    this.selected = null;
    this.hiddenTypes = new Set();
    this.searchSet = null;      // Set of ids matching search, or null
    this.impact = null;         // blast-radius result or null
    this.impactStart = 0;
    this.onSelect = null;       // callback(id | null)
    this.dragNode = null;
    this.panStart = null;
    this._raf = null;
    this._bind();
    this._resize();
    window.addEventListener("resize", () => this._resize());
    new ResizeObserver(() => {
      this._resize();
      if (!this._userMoved) this.fit();
    }).observe(canvas);
    this._loop();
  }

  // ------------------------------------------------------------------ data

  setData(nodes, edges) {
    const prev = new Map(this.nodes.map((n) => [n.id, n]));
    const clusters = [...new Set(nodes.map((n) => n.cluster || "other"))].sort();
    const cIndex = new Map(clusters.map((c, i) => [c, i]));
    const R = 90 + clusters.length * 26;

    this.nodes = nodes.map((n) => {
      const old = prev.get(n.id);
      const ci = cIndex.get(n.cluster || "other");
      const angle = (ci / Math.max(1, clusters.length)) * Math.PI * 2;
      const jitter = () => (Math.random() - 0.5) * 120;
      return {
        ...n,
        x: old ? old.x : Math.cos(angle) * R + jitter(),
        y: old ? old.y : Math.sin(angle) * R + jitter(),
        vx: 0, vy: 0,
        r: Math.min(14, 3.5 + Math.sqrt(n.degree || 1) * 1.7),
        clusterAngle: angle,
        clusterR: R,
      };
    });
    this.byId = new Map(this.nodes.map((n) => [n.id, n]));
    this.edges = edges
      .filter((e) => this.byId.has(e.src) && this.byId.has(e.dst))
      .map((e) => ({ ...e, s: this.byId.get(e.src), t: this.byId.get(e.dst) }));

    this.adj = new Map();
    for (const e of this.edges) {
      if (!this.adj.has(e.src)) this.adj.set(e.src, new Set());
      if (!this.adj.has(e.dst)) this.adj.set(e.dst, new Set());
      this.adj.get(e.src).add(e.dst);
      this.adj.get(e.dst).add(e.src);
    }
    this.selected = null;
    this.impact = null;
    this.alpha = 1;
    this._userMoved = false;
    this._settled = false;
    this._frames = 0;
  }

  // --------------------------------------------------------------- physics

  _step() {
    if (this.alpha <= 0.003) return;
    const nodes = this.nodes, edges = this.edges;
    const a = this.alpha;

    // repulsion (grid-bucketed to stay fast)
    const cell = 90;
    const grid = new Map();
    const key = (cx, cy) => cx + ":" + cy;
    for (const n of nodes) {
      const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
      const k = key(cx, cy);
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(n);
    }
    for (const n of nodes) {
      const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
      for (let gx = cx - 1; gx <= cx + 1; gx++) {
        for (let gy = cy - 1; gy <= cy + 1; gy++) {
          const bucket = grid.get(key(gx, gy));
          if (!bucket) continue;
          for (const m of bucket) {
            if (m === n) continue;
            let dx = n.x - m.x, dy = n.y - m.y;
            let d2 = dx * dx + dy * dy;
            if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
            if (d2 > cell * cell * 4) continue;
            const f = (1300 * a) / d2;
            const d = Math.sqrt(d2);
            n.vx += (dx / d) * f;
            n.vy += (dy / d) * f;
          }
        }
      }
    }

    // springs
    for (const e of edges) {
      const rest = e.type === "DEFINES" ? 46 : e.type === "IMPORTS" ? 100 : 80;
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const f = ((d - rest) / d) * 0.028 * a * 14;
      e.s.vx += dx * f * 0.001 * 60; e.s.vy += dy * f * 0.001 * 60;
      e.t.vx -= dx * f * 0.001 * 60; e.t.vy -= dy * f * 0.001 * 60;
    }

    // cluster gravity + centering
    for (const n of nodes) {
      const tx = Math.cos(n.clusterAngle) * n.clusterR * 0.62;
      const ty = Math.sin(n.clusterAngle) * n.clusterR * 0.62;
      n.vx += (tx - n.x) * 0.012 * a;
      n.vy += (ty - n.y) * 0.012 * a;
      n.vx += -n.x * 0.002 * a;
      n.vy += -n.y * 0.002 * a;
    }

    for (const n of nodes) {
      if (n === this.dragNode) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82;
      const vmax = 14;
      n.x += Math.max(-vmax, Math.min(vmax, n.vx));
      n.y += Math.max(-vmax, Math.min(vmax, n.vy));
    }
    this.alpha *= 0.992;

    // keep the settling layout in frame until the user takes over the camera
    this._frames++;
    if (!this._userMoved && this.alpha > 0.008 && this._frames % 30 === 0) this.fit();
    if (this.alpha <= 0.008 && !this._settled) {
      this._settled = true;
      if (!this._userMoved) this.fit();
    }
  }

  reheat(v = 0.35) { this.alpha = Math.max(this.alpha, v); }

  // ---------------------------------------------------------------- camera

  fit() {
    const vis = this.nodes.filter((n) => this._visible(n));
    if (!vis.length) return;
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    for (const n of vis) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    }
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    const pad = 70;
    const k = Math.min(2, Math.min(
      w / Math.max(120, maxX - minX + pad * 2),
      h / Math.max(120, maxY - minY + pad * 2)
    ));
    this.cam.k = k;
    this.cam.x = w / 2 - ((minX + maxX) / 2) * k;
    this.cam.y = h / 2 - ((minY + maxY) / 2) * k;
  }

  focusNode(id, select = true) {
    const n = this.byId.get(id);
    if (!n) return false;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.cam.k = Math.max(this.cam.k, 1.15);
    this.cam.x = w / 2 - n.x * this.cam.k;
    this.cam.y = h / 2 - n.y * this.cam.k;
    if (select) {
      this.selected = id;
      if (this.onSelect) this.onSelect(id);
    }
    return true;
  }

  _toWorld(px, py) {
    return { x: (px - this.cam.x) / this.cam.k, y: (py - this.cam.y) / this.cam.k };
  }

  // ---------------------------------------------------------------- events

  _bind() {
    const c = this.canvas;
    c.addEventListener("mousedown", (ev) => {
      const p = this._toWorld(ev.offsetX, ev.offsetY);
      const n = this._pick(p.x, p.y);
      if (n) { this.dragNode = n; this._dragMoved = false; }
      else { this.panStart = { x: ev.offsetX - this.cam.x, y: ev.offsetY - this.cam.y }; this._userMoved = true; }
      c.classList.add("dragging");
    });
    c.addEventListener("mousemove", (ev) => {
      if (this.dragNode) {
        const p = this._toWorld(ev.offsetX, ev.offsetY);
        this.dragNode.x = p.x; this.dragNode.y = p.y;
        this._dragMoved = true;
        this.reheat(0.12);
      } else if (this.panStart) {
        this.cam.x = ev.offsetX - this.panStart.x;
        this.cam.y = ev.offsetY - this.panStart.y;
      } else {
        const p = this._toWorld(ev.offsetX, ev.offsetY);
        const n = this._pick(p.x, p.y);
        this.hovered = n ? n.id : null;
        c.style.cursor = n ? "pointer" : "grab";
      }
    });
    window.addEventListener("mouseup", () => {
      if (this.dragNode && !this._dragMoved) {
        this.selected = this.dragNode.id;
        if (this.onSelect) this.onSelect(this.dragNode.id);
      }
      this.dragNode = null;
      this.panStart = null;
      c.classList.remove("dragging");
    });
    c.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      this._userMoved = true;
      const factor = Math.exp(-ev.deltaY * 0.0016);
      const k2 = Math.max(0.15, Math.min(4, this.cam.k * factor));
      const wx = (ev.offsetX - this.cam.x) / this.cam.k;
      const wy = (ev.offsetY - this.cam.y) / this.cam.k;
      this.cam.x = ev.offsetX - wx * k2;
      this.cam.y = ev.offsetY - wy * k2;
      this.cam.k = k2;
    }, { passive: false });
    c.addEventListener("dblclick", () => this.fit());
  }

  _pick(x, y) {
    let best = null, bestD = 1e9;
    for (const n of this.nodes) {
      if (!this._visible(n)) continue;
      const dx = n.x - x, dy = n.y - y;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < n.r + 6 / this.cam.k && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  // -------------------------------------------------------------- filters

  setTypeVisible(type, visible) {
    if (visible) this.hiddenTypes.delete(type);
    else this.hiddenTypes.add(type);
  }

  setSearch(ids) { this.searchSet = ids && ids.size ? ids : null; }

  setImpact(result) {
    this.impact = null;
    if (!result) return;
    const map = new Map();
    for (const a of result.affected) map.set(a.id, a);
    this.impact = { map, start: performance.now(), maxHop: Math.max(...result.affected.map((a) => a.hop), 0) };
    this.reheat(0.05);
  }

  clearImpact() { this.impact = null; }

  _visible(n) { return !this.hiddenTypes.has(n.type); }

  // ------------------------------------------------------------- rendering

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = this.canvas.clientWidth * dpr;
    this.canvas.height = this.canvas.clientHeight * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  _impactState(id) {
    if (!this.impact) return null;
    const a = this.impact.map.get(id);
    if (!a) return { faded: true };
    const elapsed = performance.now() - this.impact.start;
    const revealAt = a.hop * 380;
    if (elapsed < revealAt) return { faded: true, pending: true };
    return { a, justRevealed: elapsed - revealAt < 420 };
  }

  _loop() {
    this._step();
    this._draw();
    this._raf = requestAnimationFrame(() => this._loop());
  }

  _draw() {
    const ctx = this.ctx;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(this.cam.x, this.cam.y);
    ctx.scale(this.cam.k, this.cam.k);

    const neighborSet = this.selected ? (this.adj.get(this.selected) || new Set()) : null;

    // ---- cluster hulls (soft grouping so services read as regions, not noise)
    if (!this.impact) {
      const byCluster = new Map();
      for (const n of this.nodes) {
        if (!this._visible(n)) continue;
        const c = n.cluster || "other";
        if (!byCluster.has(c)) byCluster.set(c, []);
        byCluster.get(c).push(n);
      }
      for (const [name, members] of byCluster) {
        if (members.length < 2) continue;
        let cx = 0, cy = 0;
        for (const n of members) { cx += n.x; cy += n.y; }
        cx /= members.length; cy /= members.length;
        let radius = 0;
        for (const n of members) {
          const d = Math.hypot(n.x - cx, n.y - cy) + n.r;
          if (d > radius) radius = d;
        }
        radius += 26 / this.cam.k;
        const color = clusterColor(name);
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.11;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.3 / this.cam.k;
        ctx.globalAlpha = 0.4;
        ctx.stroke();
        ctx.globalAlpha = 1;
        if (this.cam.k > 0.4) {
          ctx.font = `${12 / this.cam.k}px ${getComputedStyle(document.body).getPropertyValue("--mono") || "monospace"}`;
          ctx.textAlign = "center";
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.75;
          ctx.fillText(name, cx, cy - radius + 14 / this.cam.k);
          ctx.globalAlpha = 1;
        }
      }
    }

    // ---- edges
    for (const e of this.edges) {
      if (!this._visible(e.s) || !this._visible(e.t)) continue;
      let alpha = EDGE_ALPHA[e.type] ?? EDGE_ALPHA.default;
      let color = "148,163,184";
      let width = 1;

      if (this.impact) {
        const si = this.impact.map.get(e.src), ti = this.impact.map.get(e.dst);
        const sSt = this._impactState(e.src), tSt = this._impactState(e.dst);
        if (si && ti && sSt && tSt && !sSt.faded && !tSt.faded) {
          alpha = 0.4; color = "255,120,90"; width = 1.4;
        } else alpha = 0.025;
      } else if (this.selected) {
        const on = e.src === this.selected || e.dst === this.selected;
        alpha = on ? 0.5 : 0.05;
        if (on) { color = "34,211,238"; width = 1.4; }
      } else if (this.searchSet) {
        alpha = 0.05;
      }

      ctx.strokeStyle = `rgba(${color},${alpha})`;
      ctx.lineWidth = width / this.cam.k;
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      ctx.stroke();
    }

    // ---- nodes
    const now = performance.now();
    for (const n of this.nodes) {
      if (!this._visible(n)) continue;
      let color = NODE_COLORS[n.type] || "#8b98af";
      let alpha = 1, r = n.r, glow = 0;

      const ist = this._impactState(n.id);
      if (ist) {
        if (ist.faded) { alpha = ist.pending ? 0.1 : 0.07; }
        else {
          const a = ist.a;
          color = RISK_COLORS[a.risk] || color;
          glow = a.risk === "seed" ? 22 : a.risk === "high" ? 14 : 6;
          if (a.risk === "seed") {
            r = n.r + 2;
            const pulse = 1 + 0.35 * Math.sin(now / 300);
            ctx.beginPath();
            ctx.arc(n.x, n.y, (r + 7) * pulse, 0, Math.PI * 2);
            ctx.strokeStyle = "rgba(255,77,109,0.4)";
            ctx.lineWidth = 1.6 / this.cam.k;
            ctx.stroke();
          }
          if (ist.justRevealed) r += 3 * (1 - (now - this.impact.start - a.hop * 380) / 420);
        }
      } else if (this.selected) {
        const isSel = n.id === this.selected;
        const isNb = neighborSet && neighborSet.has(n.id);
        alpha = isSel || isNb ? 1 : 0.12;
        if (isSel) { glow = 20; r = n.r + 2; }
      } else if (this.searchSet) {
        alpha = this.searchSet.has(n.id) ? 1 : 0.08;
        if (this.searchSet.has(n.id)) glow = 12;
      }
      if (this.hovered === n.id) { glow = Math.max(glow, 14); r += 1; }

      ctx.globalAlpha = alpha;
      if (glow) { ctx.shadowColor = color; ctx.shadowBlur = glow; }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.shadowBlur = 0;

      if (n.type === "endpoint" && alpha > 0.5) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 2.4, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.lineWidth = 0.8 / this.cam.k;
        ctx.globalAlpha = alpha * 0.6;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // ---- labels (bright/priority labels claim space first; ambient labels
    // that would collide with an already-placed label are skipped rather than
    // drawn on top of it — this is what stops dense graphs from turning into
    // a wall of overlapping text)
    ctx.font = `${11 / this.cam.k}px ${getComputedStyle(document.body).getPropertyValue("--mono") || "monospace"}`;
    ctx.textAlign = "center";
    const candidates = [];
    for (const n of this.nodes) {
      if (!this._visible(n)) continue;
      const ist = this._impactState(n.id);
      let show = false, bright = false;
      if (ist && !ist.faded) { show = ist.a.risk === "seed" || ist.a.risk === "high"; bright = true; }
      else if (!this.impact) {
        if (n.id === this.selected || n.id === this.hovered) { show = true; bright = true; }
        else if (this.searchSet && this.searchSet.has(n.id)) { show = true; bright = true; }
        else if (!this.selected && !this.searchSet) {
          show = this.cam.k > 0.85 ? n.degree >= 6 : n.degree >= 12;
        }
      }
      if (n.id === this.hovered) { show = true; bright = true; }
      if (!show) continue;
      candidates.push({ n, bright });
    }
    candidates.sort((a, b) => (b.bright - a.bright) || (b.n.degree || 0) - (a.n.degree || 0));
    const placed = [];
    const padY = 3 / this.cam.k;
    for (const { n, bright } of candidates) {
      const label = n.label.length > 34 ? n.label.slice(0, 32) + "…" : n.label;
      const tw = ctx.measureText(label).width;
      const th = 11 / this.cam.k;
      const cx = n.x, cyTop = n.y - n.r - 5 / this.cam.k - th;
      const rect = { x1: cx - tw / 2, y1: cyTop - padY, x2: cx + tw / 2, y2: cyTop + th + padY };
      if (!bright) {
        const collides = placed.some((r) => !(rect.x2 < r.x1 || rect.x1 > r.x2 || rect.y2 < r.y1 || rect.y1 > r.y2));
        if (collides) continue;
      }
      placed.push(rect);
      ctx.fillStyle = bright ? "rgba(240,246,255,0.95)" : "rgba(190,202,222,0.55)";
      ctx.fillText(label, n.x, n.y - n.r - 5 / this.cam.k);
    }
    ctx.restore();
  }
}

window.GraphView = GraphView;
window.NODE_COLORS = NODE_COLORS;
window.RISK_COLORS = RISK_COLORS;
