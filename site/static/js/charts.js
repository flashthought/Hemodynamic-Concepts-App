// Small, dependency-free canvas charts (no CDN: works offline on a hospital network).

function setup(canvas, cssHeight) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(50, canvas.clientWidth || canvas.parentElement.clientWidth);
  const h = cssHeight || canvas.clientHeight || 160;
  canvas.style.height = `${h}px`;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function percentile(sorted, p) {
  if (!sorted.length) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))));
  return sorted[i];
}

// Bedside-monitor style waveform strip (last 10 s).
export function drawStrip(canvas, values, colour, { fixed = null, unit = "" } = {}) {
  const { ctx, w, h } = setup(canvas, 74);
  ctx.strokeStyle = "#16232d";
  ctx.lineWidth = 1;
  for (let i = 1; i < 10; i++) {
    const x = (i / 10) * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  if (!values || values.length < 2) return;
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return;
  let lo, hi;
  if (fixed) { [lo, hi] = fixed; } else {
    const s = [...finite].sort((a, b) => a - b);
    lo = percentile(s, 0.01); hi = percentile(s, 0.99);
    const pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad;
  }
  const y = (v) => h - 6 - ((v - lo) / (hi - lo)) * (h - 12);
  ctx.strokeStyle = colour;
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round";
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const yy = y(Math.min(hi, Math.max(lo, Number.isFinite(v) ? v : lo)));
    i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
  });
  ctx.stroke();
  if (fixed && unit) {
    ctx.fillStyle = "#7f93a3"; ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(`${hi} ${unit}`, 4, 11); ctx.fillText(`${lo}`, 4, h - 3);
  }
}

// General time-series chart with threshold lines, uncertainty bands, background
// spans (alert levels) and event markers.
export function drawChart(canvas, opts) {
  const {
    height = 200, xMin, xMax, series = [], bands = [], hlines = [], vlines = [], spans = [],
    yMin = null, yMax = null, yLabel = "", xFmt = (x) => x.toFixed(0), now = null, yPad = 0.08,
  } = opts;
  const { ctx, w, h } = setup(canvas, height);
  const ml = 44, mr = 10, mt = 14, mb = 22;
  const pw = w - ml - mr, ph = h - mt - mb;

  const ys = [];
  series.forEach((s) => s.points.forEach(([, v]) => Number.isFinite(v) && ys.push(v)));
  bands.forEach((b) => b.points.forEach(([, lo, hi]) => { if (Number.isFinite(lo)) ys.push(lo); if (Number.isFinite(hi)) ys.push(hi); }));
  hlines.forEach((l) => ys.push(l.y));
  let lo = yMin ?? (ys.length ? Math.min(...ys) : 0);
  let hi = yMax ?? (ys.length ? Math.max(...ys) : 1);
  if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * yPad;
  if (yMin === null) lo -= pad;
  if (yMax === null) hi += pad;
  const x0 = xMin, x1 = xMax > xMin ? xMax : xMin + 1;
  const X = (x) => ml + ((x - x0) / (x1 - x0)) * pw;
  const Y = (v) => mt + ((hi - v) / (hi - lo)) * ph;

  ctx.fillStyle = "#fbfcfd"; ctx.fillRect(ml, mt, pw, ph);
  spans.forEach((s) => {
    const a = Math.max(ml, X(s.x0)), b = Math.min(ml + pw, X(s.x1));
    if (b > a) { ctx.fillStyle = s.colour; ctx.globalAlpha = s.alpha ?? 0.13; ctx.fillRect(a, mt, b - a, ph); ctx.globalAlpha = 1; }
  });

  ctx.strokeStyle = "#e3e8ed"; ctx.lineWidth = 1;
  ctx.fillStyle = "#5c6b78"; ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (i / 4) * (hi - lo);
    const yy = Y(v);
    ctx.beginPath(); ctx.moveTo(ml, yy); ctx.lineTo(ml + pw, yy); ctx.stroke();
    ctx.fillText(Math.abs(hi - lo) < 4 ? v.toFixed(2) : v.toFixed(0), ml - 5, yy + 3.5);
  }
  ctx.textAlign = "center";
  for (let i = 0; i <= 5; i++) {
    const x = x0 + (i / 5) * (x1 - x0);
    ctx.textAlign = i === 0 ? "left" : i === 5 ? "right" : "center";  // keep end labels inside the canvas
    ctx.fillText(xFmt(x), i === 0 ? ml : i === 5 ? ml + pw : X(x), h - 6);
  }

  hlines.forEach((l, li) => {
    if (l.y < lo || l.y > hi) return;
    ctx.strokeStyle = l.colour || "#78909c"; ctx.lineWidth = 1.2; ctx.setLineDash(l.dash || [5, 4]);
    ctx.beginPath(); ctx.moveTo(ml, Y(l.y)); ctx.lineTo(ml + pw, Y(l.y)); ctx.stroke(); ctx.setLineDash([]);
    if (l.label) { ctx.fillStyle = l.colour || "#78909c"; ctx.textAlign = "left"; ctx.font = "10.5px system-ui, sans-serif";
      // Near the top edge the label would collide with the axis label, so draw it under the line.
      const ly = Y(l.y) - 3 < mt + 16 ? Y(l.y) + 12 : Y(l.y) - 3;
      ctx.fillText(l.label, ml + 4 + li * 72, ly); }  // staggered so close thresholds stay legible
  });

  bands.forEach((b) => {
    const pts = b.points.filter(([, l, u]) => Number.isFinite(l) && Number.isFinite(u));
    if (pts.length < 2) return;
    ctx.fillStyle = b.colour; ctx.globalAlpha = b.alpha ?? 0.18;
    ctx.beginPath();
    pts.forEach(([x, l], i) => (i ? ctx.lineTo(X(x), Y(Math.max(lo, l))) : ctx.moveTo(X(x), Y(Math.max(lo, l)))));
    [...pts].reverse().forEach(([x, , u]) => ctx.lineTo(X(x), Y(Math.min(hi, u))));
    ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
  });

  if (now !== null && now >= x0 && now <= x1) {
    ctx.strokeStyle = "#263238"; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(X(now), mt); ctx.lineTo(X(now), mt + ph); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#263238"; ctx.textAlign = "center"; ctx.font = "10.5px system-ui, sans-serif"; ctx.fillText("now", X(now), mt - 3);
  }

  series.forEach((s) => {
    if (s.width !== 0) {  // width 0 = markers only (canvas ignores lineWidth 0)
      ctx.strokeStyle = s.colour; ctx.lineWidth = s.width || 2; ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      let pen = false;
      s.points.forEach(([x, v]) => {
        if (!Number.isFinite(v) || x < x0 - 1e-9 || x > x1 + 1e-9) { pen = false; return; }
        const vv = Math.min(hi, Math.max(lo, v));
        pen ? ctx.lineTo(X(x), Y(vv)) : ctx.moveTo(X(x), Y(vv));
        pen = true;
      });
      ctx.stroke(); ctx.setLineDash([]);
    }
    if (s.dots) {
      ctx.fillStyle = s.colour;
      s.points.forEach(([x, v]) => { if (Number.isFinite(v) && x >= x0 && x <= x1) { ctx.beginPath(); ctx.arc(X(x), Y(Math.min(hi, Math.max(lo, v))), 2.4, 0, 6.283); ctx.fill(); } });
    }
  });

  vlines.forEach((v) => {
    if (v.x < x0 || v.x > x1) return;
    ctx.strokeStyle = v.colour || "#6a1b9a"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X(v.x), mt); ctx.lineTo(X(v.x), mt + ph); ctx.stroke();
    if (v.label) {
      ctx.fillStyle = v.colour || "#6a1b9a"; ctx.textAlign = "left"; ctx.font = "600 10.5px system-ui, sans-serif";
      ctx.fillText(v.label, Math.min(X(v.x) + 4, ml + pw - 60), mt + 11);
    }
  });

  ctx.strokeStyle = "#cfd8df"; ctx.lineWidth = 1; ctx.strokeRect(ml + 0.5, mt + 0.5, pw - 1, ph - 1);
  if (yLabel) {
    ctx.save(); ctx.fillStyle = "#5c6b78"; ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "left";
    ctx.fillText(yLabel, ml + 4, mt + 11); ctx.restore();
  }
}
