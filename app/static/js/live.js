// Live monitor shared by Concept A (integrated monitor) and Concept B
// (non-invasive warning). Both concepts use the SAME layout, control bar and
// alert-banner position (BME Design 8.2.5.3: a control must never move between
// otherwise-similar screens) - only the content each concept may show differs.
import { api, el, fmt, LEVEL, LEVEL_BY_INDEX, levelIcon, STATIC } from "./api.js";
import { drawChart, drawStrip } from "./charts.js";

const COL = { measured: "#37474f", forecast: "#6a1b9a", truth: "#e65100", r: "#00695c", c: "#ad1457" };
const SPEEDS = [1, 2, 5, 10, 20];

export function mountLive(root, concept, meta) {
  const isA = concept === "A";
  const info = meta.concepts[concept];
  const th = meta.thresholds;
  const S = { sid: null, rec: null, ticks: [], playing: false, speed: 5, inflight: false, timer: null,
              truth: false, log: [], lastLevel: null, lastQuality: null, disposed: false, pendingConfirm: null };

  // ------------------------------------------------------------------ layout
  const recSelect = el("select", { id: "rec-select", "aria-label": "Patient recording" });
  const startSelect = el("select", { id: "start-select", "aria-label": "Start position" });
  const playBtn = el("button", { class: "btn primary", id: "play-btn", title: "Play / pause (Space)" }, "▶ Play");
  const stepBtn = el("button", { class: "btn", id: "step-btn", title: "Advance one update (→)" }, "Step +1 s");
  const resetBtn = el("button", { class: "btn", id: "reset-btn", title: "Restart this recording" }, "↺ Restart");
  const speedSeg = el("div", { class: "seg", role: "group", "aria-label": "Playback speed" },
    SPEEDS.map((s) => el("button", { "data-speed": s, "aria-pressed": String(s === S.speed) }, `${s}×`)));
  const clock = el("span", { class: "clock", id: "clock", "aria-live": "off" }, "--:-- / --:--");
  const truthToggle = el("input", { type: "checkbox", id: "truth-toggle" });

  const banner = el("section", { class: "alert-banner lvl-IDLE", id: "alert-banner", role: "status", "aria-live": "polite" });
  const strips = {
    ecg: el("canvas", { "aria-label": "ECG waveform" }),
    ppg: el("canvas", { "aria-label": "PPG waveform" }),
    abp: el("canvas", { "aria-label": "Arterial pressure waveform" }),
  };
  const forecastCanvas = el("canvas", { "aria-label": "MAP forecast chart" });
  const trendCanvas = el("canvas", { "aria-label": "Session MAP trend with alert levels" });
  const rCanvas = el("canvas", { "aria-label": "Vascular resistance trend" });
  const cCanvas = el("canvas", { "aria-label": "Arterial compliance trend" });
  const tiles = el("div", { class: "tiles", id: "tiles" });
  const logList = el("ul", { class: "log", id: "event-log" });
  const ivList = el("div", { class: "iv-list", id: "iv-list" });
  const errorBox = el("div", { class: "warnbox", hidden: true, role: "alert" });

  const header = el("div", { class: "page-head" },
    el("div", {},
      el("h1", {}, `Concept ${concept} · ${info.name}`),
      el("div", { class: "sub" }, `${info.inputs} · ${info.setting}`)),
    el("div", { class: "chips" }, info.combines.map((c) => el("span", { class: "chip strong" }, c)),
      el("span", { class: "chip" }, info.mode)));

  const controls = el("section", { class: "card controls", "aria-label": "Playback controls" },
    el("label", { for: "rec-select" }, "Patient"), recSelect,
    el("label", { for: "start-select" }, "Start"), startSelect,
    playBtn, stepBtn, resetBtn, speedSeg, clock,
    el("span", { class: "grow" }),
    el("label", { for: "truth-toggle", title: "Shows what actually happened. Not available at the bedside." },
      truthToggle, "Verification overlay"));

  const monitor = el("section", { class: "monitor", "aria-label": "Patient monitor waveforms" },
    el("div", { class: "mon-head" }, el("span", {}, "Last 10 s · model input window"), el("span", { id: "mon-quality" }, "")),
    el("div", { class: "strip" }, el("span", { class: "lbl", style: "color:var(--ecg)" }, "ECG II"), strips.ecg),
    el("div", { class: "strip" }, el("span", { class: "lbl", style: "color:var(--ppg)" }, "PLETH"), strips.ppg),
    el("div", { class: "strip" }, el("span", { class: "lbl", style: "color:var(--abp)" }, "ART"),
      isA ? strips.abp : el("div", { class: "absent" }, "Not measured - non-invasive mode (no arterial line)")));

  const forecastCard = el("section", { class: "card chart-card" },
    el("h3", {}, isA ? "Pressure forecast - next 15 s" : "Estimated pressure - next 15 s (low confidence)"),
    forecastCanvas,
    el("div", { class: "legend" },
      isA && el("span", {}, el("i", { style: `background:${COL.measured}` }), "Measured MAP"),
      el("span", {}, el("i", { style: `background:${COL.forecast}` }), isA ? "Forecast MAP (next 15 s)" : "Estimated MAP (next 15 s)"),
      el("span", { style: `color:${COL.forecast}` }, "• predicted pressure samples (1 s apart)"),
      el("span", { style: `color:${COL.forecast}` }, el("i", { class: "band", style: `background:${COL.forecast}` }), "± calibrated error"),
      el("span", {}, el("i", { class: "dash", style: `color:${COL.forecast}` }), "past forecasts"),
      el("span", { class: "truth-legend", hidden: true, style: `color:${COL.truth}` }, el("i", { class: "dash" }), "True MAP (verification)"),
      el("span", {}, `Dashed lines: ${th.watch} watch · ${th.target} target · ${th.critical} critical`)));

  const trendCard = el("section", { class: "card chart-card" },
    el("h3", {}, "Session trend - forecast MAP and alert level"),
    trendCanvas,
    el("div", { class: "legend" },
      ["STABLE", "WATCH", "DETERIORATING", "CRITICAL", "SIGNAL CHECK"].map((n) =>
        el("span", {}, el("span", { class: "lvl-dot", style: `background:${LEVEL[n].colour}` }), n)),
      isA && el("span", { style: "color:#6a1b9a" }, "| purple line = intervention")));

  const rcCard = el("section", { class: "card chart-card" },
    el("h3", {}, "Vascular state - relative trends"),
    el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:10px" }, rCanvas, cCanvas),
    el("div", { class: "legend" },
      el("span", {}, el("i", { style: `background:${COL.r}` }), "Resistance R"),
      el("span", {}, el("i", { style: `background:${COL.c}` }), "Compliance C"),
      el("span", { class: "truth-legend", hidden: true, style: `color:${COL.truth}` }, el("i", { class: "dash" }), "True value (simulation)")),
    el("p", { class: "small muted", style: "margin:6px 0 0" },
      "Shown as trends, not calibrated absolute values: verification found real-data fine-tuning weakened R/C tracking (docs/07)."));

  const rightCol = el("div", { class: "stack" });
  const tilesCard = el("section", { class: "card" }, el("h3", {}, isA ? "Current status" : "Risk estimate"), tiles);
  rightCol.append(tilesCard);

  if (isA) {
    const markVaso = el("button", { class: "btn warn", "data-kind": "vasopressor" }, "+ Mark vasopressor");
    const markFluid = el("button", { class: "btn warn", "data-kind": "fluid" }, "+ Mark fluid bolus");
    [markVaso, markFluid].forEach((b) => b.addEventListener("click", () => confirmMark(b)));
    rightCol.append(el("section", { class: "card" },
      el("h3", {}, "Intervention response"),
      el("p", { class: "small muted", style: "margin:0 0 8px" },
        "Compares the patient with a 30 s pre-treatment baseline from 30-60 s after onset. Reports a trend only - never a treatment recommendation."),
      ivList, STATIC
        // Public static demo: no live model, so hand-entered events cannot be assessed.
        ? el("p", { class: "small muted", style: "margin:8px 0 0" },
            "Public demo: treatment events come from the recorded pump/EHR feed. Hand-marking interventions needs the live model server (scripts/run_app.sh).")
        : el("div", { class: "iv-actions" }, markVaso, markFluid)));
  } else {
    rightCol.append(el("section", { class: "card" },
      el("h3", {}, "Inputs available"),
      el("div", { class: "missing" },
        el("div", {}, el("span", {}, "ECG"), el("span", { class: "yes" }, "✓ used")),
        el("div", {}, el("span", {}, "PPG (pulse oximeter)"), el("span", { class: "yes" }, "✓ used")),
        el("div", {}, el("span", {}, "Invasive arterial pressure"), el("span", { class: "no" }, "✗ not available")),
        el("div", {}, el("span", {}, "Intervention events"), el("span", { class: "no" }, "✗ not used"))),
      el("div", { class: "warnbox", id: "b-limit", style: "margin-top:8px" }, "")));
  }

  rightCol.append(el("section", { class: "card" }, el("h3", {}, "Event log"), logList),
    el("section", { class: "card help" }, el("details", {},
      el("summary", {}, "How alerts work"),
      el("dl", {},
        el("dt", {}, "STABLE"), el("dd", {}, `Forecast MAP at or above ${th.watch} mmHg.`),
        el("dt", {}, "WATCH (low priority)"), el("dd", {}, `Forecast below ${th.watch}, or falling ≥${th.rapid_drop} mmHg over 15 s.`),
        el("dt", {}, "DETERIORATING (medium)"), el("dd", {}, `Forecast below the ${th.target} mmHg treatment target.`),
        el("dt", {}, "CRITICAL (high)"), el("dd", {}, `Forecast below ${th.critical} mmHg.`),
        el("dt", {}, "Persistence"), el("dd", {}, `Escalates after ${th.escalate_persist} consecutive updates; steps down after ${th.deescalate_persist} with a 2 mmHg margin.`),
        el("dt", {}, "Confidence cap"), el("dd", {}, "Low-confidence estimates can raise at most WATCH; moderate at most DETERIORATING."),
        el("dt", {}, "SIGNAL CHECK"), el("dd", {}, "Technical alarm: signal unusable, so no haemodynamic alert is issued."),
        el("dt", {}, "Keyboard"), el("dd", {}, "Space = play/pause, → = step one update.")))));

  root.replaceChildren(header, controls, errorBox, banner,
    el("div", { class: "live-grid" },
      el("div", { class: "stack" }, monitor, forecastCard, trendCard, rcCard),
      rightCol));

  // ------------------------------------------------------------------ helpers
  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = !msg;
  }

  function addLog(t, text, colour) {
    S.log.unshift({ t, text, colour });
    S.log = S.log.slice(0, 80);
    logList.replaceChildren(...S.log.map((e) =>
      el("li", {}, el("span", { class: "t" }, fmt.time(e.t)),
        el("span", {}, e.colour ? el("span", { class: "lvl-dot", style: `background:${e.colour}` }) : null, e.text))));
  }

  function tile(k, v, unit, d, extra = "") {
    return el("div", { class: `tile ${extra}` }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v, unit ? el("small", {}, unit) : null), d ? el("div", { class: "d" }, d) : null);
  }

  function riskDial(score) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 120 120");
    svg.setAttribute("class", "risk-dial");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Risk score ${score ?? "unavailable"} of 100`);
    const r = 48, c = 2 * Math.PI * r, frac = score === null ? 0 : score / 100;
    const colour = score === null ? "#b0bec5" : score >= 80 ? LEVEL.CRITICAL.colour : score >= 50 ? LEVEL.DETERIORATING.colour : score >= 20 ? LEVEL.WATCH.colour : LEVEL.STABLE.colour;
    const bg = document.createElementNS(ns, "circle");
    Object.entries({ cx: 60, cy: 60, r, fill: "none", stroke: "#e3e8ed", "stroke-width": 12 }).forEach(([k, v]) => bg.setAttribute(k, v));
    const fg = document.createElementNS(ns, "circle");
    Object.entries({ cx: 60, cy: 60, r, fill: "none", stroke: colour, "stroke-width": 12, "stroke-linecap": "round",
      "stroke-dasharray": `${c * frac} ${c}`, transform: "rotate(-90 60 60)" }).forEach(([k, v]) => fg.setAttribute(k, v));
    const txt = document.createElementNS(ns, "text");
    Object.entries({ x: 60, y: 66, "text-anchor": "middle", "font-size": 28, "font-weight": 800, fill: "#17212b" }).forEach(([k, v]) => txt.setAttribute(k, v));
    txt.textContent = score === null ? "—" : String(score);
    const sub = document.createElementNS(ns, "text");
    Object.entries({ x: 60, y: 84, "text-anchor": "middle", "font-size": 10, fill: "#566573" }).forEach(([k, v]) => sub.setAttribute(k, v));
    sub.textContent = "of 100";
    svg.append(bg, fg, txt, sub);
    return svg;
  }

  // ------------------------------------------------------------------ rendering
  function renderBanner(k) {
    const a = k.alert;
    const name = a.level_name;
    const cls = a.technical ? "SIGNAL" : name;
    banner.className = `alert-banner lvl-${cls}`;
    const levelBox = el("div", { class: "level" }, levelIcon(name), el("div", {},
      el("div", {}, name), el("div", { style: "font-size:12.5px;font-weight:600;opacity:.9" }, LEVEL[name]?.priority || "")));
    const side = el("div", { class: "side" });
    if (k.forecast_map !== null) {
      side.append(el("div", {}, isA ? "Forecast MAP " : "Estimated MAP ", el("b", {}, `${fmt.num(k.forecast_map)} mmHg`)));
      side.append(el("div", {}, `P(MAP < ${th.target}) `, el("b", {}, fmt.pct(k.p_below_target))));
    }
    if (isA && k.conventional_alarm !== null) {
      side.append(el("div", { title: "What a conventional threshold monitor shows from the measured MAP alone" },
        `Conventional monitor: ${k.conventional_alarm ? "alarming" : "quiet"}`));
    }
    if (a.held) {
      side.append(el("div", { style: "font-weight:700" }, "⚑ Alarm held - arterial line unavailable"));
    }
    banner.replaceChildren(levelBox, el("div", { class: "reason" }, a.reason), side);
  }

  function renderTiles(k) {
    const truth = S.truth && k.truth ? k.truth : null;
    const items = [];
    if (isA) {
      items.push(tile("Measured MAP", fmt.num(k.current_map), "mmHg", "Arterial line, last 4 s"));
      items.push(tile("Forecast MAP", fmt.num(k.forecast_map), "mmHg", `next 15 s · ± ${fmt.num(k.sigma, 1)}`));
      items.push(tile(`P(MAP < ${th.target})`, fmt.pct(k.p_below_target), "", "calibrated probability"));
      items.push(tile("Confidence", k.confidence || "—", "", k.regime ? k.regime.replaceAll("_", " + ").toUpperCase() : ""));
      items.push(tile("Resistance R", fmt.num(k.r, 2), "", "mmHg·s/mL (trend)"));
      items.push(tile("Compliance C", fmt.num(k.c, 2), "", "mL/mmHg (trend)"));
    } else {
      const score = k.risk_score;
      items.push(el("div", { class: "tile wide risk" }, riskDial(score),
        el("div", {}, el("div", { class: "k" }, `Risk of MAP < ${th.target} in 15 s`),
          el("div", { style: "margin:6px 0" }, el("span", { class: `conf ${k.confidence || ""}` }, `${k.confidence || "—"} confidence`)),
          el("div", { class: "d" }, "Probability from the estimate and its calibrated error. A low-confidence score stays near 50 - it cannot separate high from low risk."))));
      items.push(tile("Estimated MAP", fmt.num(k.forecast_map), "mmHg", `± ${fmt.num(k.sigma, 0)} mmHg (1 SD)`));
      items.push(tile("Alert cap", k.alert.confidence_cap || "—", "", "highest level allowed"));
    }
    items.push(tile("Signal quality", (k.quality || "—").replace("_", " "), "", k.warnings.length ? `${k.warnings.length} warning(s)` : "no warnings"));
    items.push(tile("Update latency", fmt.num(k.latency_ms, 1), "ms", `model ${meta.model.device}`));
    if (truth) {
      items.push(tile("True future MAP", fmt.num(truth.future_map), "mmHg", truth.kind, "truth"));
      if (truth.r !== undefined) items.push(tile("True R / C", `${fmt.num(truth.r, 2)} / ${fmt.num(truth.c, 2)}`, "", "simulation ground truth", "truth"));
    }
    tiles.replaceChildren(...items);
    if (!isA) {
      const box = root.querySelector("#b-limit");
      box.textContent = `Without arterial pressure the calibrated estimate error is about ±${fmt.num(k.sigma, 0)} mmHg (1 SD), ` +
        `so this concept can raise at most a WATCH alert. Verification: AUROC well below the 0.80 marginal target (see Evidence).`;
    }
  }

  function renderInterventions(k) {
    if (!isA) return;
    const records = k.interventions || [];
    if (!records.length) {
      ivList.replaceChildren(el("p", { class: "small muted", style: "margin:0" }, "No interventions yet. Events come from the pump/EHR feed or can be entered with the buttons below."));
      return;
    }
    ivList.replaceChildren(...records.slice().reverse().map((r) => {
      const statusCls = r.status.replaceAll(" ", "-");
      const parts = [];
      if (r.d_map !== null) parts.push(`MAP ${r.d_map > 0 ? "+" : ""}${fmt.num(r.d_map, 0)} mmHg`);
      if (r.r_change_pct !== null) parts.push(`R ${r.r_change_pct > 0 ? "+" : ""}${fmt.num(r.r_change_pct, 0)}%`);
      if (r.c_change_pct !== null) parts.push(`C ${r.c_change_pct > 0 ? "+" : ""}${fmt.num(r.c_change_pct, 0)}%`);
      const since = S.ticks.length ? S.ticks[S.ticks.length - 1].t - r.onset_t : 0;
      return el("div", { class: "iv" },
        el("div", { class: "iv-top" }, el("b", {}, r.label),
          el("span", { class: `status ${statusCls}` }, r.status + (r.final ? "" : r.status === "pending" ? "" : " (provisional)"))),
        el("div", { class: "meta" }, `Onset ${fmt.time(r.onset_t)} · ${r.origin === "manual" ? "clinician entry" : "pump/EHR event"} · `,
          r.status === "pending" ? `assessing in ${Math.max(0, Math.ceil(30 - since))} s` : parts.join(" · ") || "awaiting data"),
        r.confounded_with && r.confounded_with.length
          ? el("div", { class: "meta", style: "color:#8a5a00" }, "⚠ Overlaps another intervention - effects cannot be separated.") : null,
        r.baseline && r.baseline.map !== null ? el("div", { class: "meta" }, `Baseline MAP ${fmt.num(r.baseline.map)} mmHg (${fmt.num(r.baseline.seconds)} s)`) : null);
    }));
  }

  function renderStrips(k) {
    const w = k.waveforms;
    if (!w) return;
    drawStrip(strips.ecg, w.ecg, "#39ff9c");
    drawStrip(strips.ppg, w.ppg, "#4dd8ee");
    if (isA) drawStrip(strips.abp, w.abp, "#ff5a5a", { fixed: [20, 180], unit: "mmHg" });
    root.querySelector("#mon-quality").textContent = `quality: ${k.quality}${k.warnings.length ? " · " + k.warnings[0] : ""}`;
  }

  function renderCharts() {
    if (!S.ticks.length) return;
    const k = S.ticks[S.ticks.length - 1];
    const now = k.t;
    const hist = S.ticks.filter((x) => x.t >= now - 60);
    const series = [];
    const bands = [];
    if (isA) series.push({ points: hist.map((x) => [x.t, x.current_map ?? NaN]), colour: COL.measured, width: 2 });
    series.push({ points: hist.map((x) => [x.t, x.forecast_map ?? NaN]), colour: COL.forecast, width: 1.3, dash: [3, 3] });
    if (k.forecast_abp && k.forecast_abp.length) {
      // The model predicts instantaneous pressure SAMPLES (anywhere in the
      // cardiac cycle), so they are drawn as dots, not joined into a line that
      // would read as a MAP trajectory. The forecast MAP is their mean.
      const fpts = k.forecast_abp.map((v, i) => [now + i, v]);
      const avg = k.forecast_map;
      const end = now + k.forecast_abp.length - 1;
      bands.push({ points: [[now, avg - k.sigma, avg + k.sigma], [end, avg - k.sigma, avg + k.sigma]], colour: COL.forecast, alpha: 0.15 });
      series.push({ points: [[now, avg], [end, avg]], colour: COL.forecast, width: 3 });
      series.push({ points: fpts, colour: COL.forecast, width: 0, dots: true });
    }
    if (S.truth) {
      series.push({ points: hist.map((x) => [x.t, x.truth?.current_map ?? NaN]), colour: COL.truth, width: 1.6, dash: [6, 4] });
      if (k.truth?.future_map !== null && k.truth?.future_map !== undefined) {
        series.push({ points: [[now, k.truth.future_map], [now + 14, k.truth.future_map]], colour: COL.truth, width: 2, dash: [6, 4] });
      }
    }
    const hl = [
      { y: th.watch, label: `${th.watch} watch`, colour: "#0277bd" },
      { y: th.target, label: `${th.target} target`, colour: "#b26a00" },
      { y: th.critical, label: `${th.critical} critical`, colour: "#c62828" },
    ];
    drawChart(forecastCanvas, { height: 220, xMin: now - 60, xMax: now + 15, series, bands, hlines: hl, now,
      xFmt: fmt.time, yMin: isA ? null : 0, yLabel: "mmHg" });

    const all = S.ticks;
    const t0 = all[0].t, t1 = Math.max(all[all.length - 1].t, t0 + 30);
    const spans = [];
    for (let i = 0; i < all.length; i++) {
      const a = all[i], b = all[i + 1];
      spans.push({ x0: a.t, x1: b ? b.t : a.t + 1, colour: LEVEL[a.alert.level_name]?.colour || "#999", alpha: 0.18 });
    }
    const vlines = isA ? (all[all.length - 1].interventions || []).map((r) => ({ x: r.onset_t, label: r.type, colour: "#6a1b9a" })) : [];
    const tSeries = [{ points: all.map((x) => [x.t, x.forecast_map ?? NaN]), colour: COL.forecast, width: 1.8 }];
    if (isA) tSeries.unshift({ points: all.map((x) => [x.t, x.current_map ?? NaN]), colour: COL.measured, width: 1.2 });
    if (S.truth) tSeries.push({ points: all.map((x) => [x.t, x.truth?.future_map ?? NaN]), colour: COL.truth, width: 1.4, dash: [6, 4] });
    drawChart(trendCanvas, { height: 190, xMin: t0, xMax: t1, series: tSeries, spans, vlines, hlines: hl, xFmt: fmt.time, yLabel: "mmHg" });

    const rS = [{ points: all.map((x) => [x.t, x.r ?? NaN]), colour: COL.r, width: 1.8 }];
    const cS = [{ points: all.map((x) => [x.t, x.c ?? NaN]), colour: COL.c, width: 1.8 }];
    if (S.truth && all[0].truth?.r !== undefined) {
      rS.push({ points: all.map((x) => [x.t, x.truth.r]), colour: COL.truth, width: 1.3, dash: [6, 4] });
      cS.push({ points: all.map((x) => [x.t, x.truth.c]), colour: COL.truth, width: 1.3, dash: [6, 4] });
    }
    drawChart(rCanvas, { height: 140, xMin: t0, xMax: t1, series: rS, vlines, xFmt: fmt.time, yLabel: "R" });
    drawChart(cCanvas, { height: 140, xMin: t0, xMax: t1, series: cS, vlines, xFmt: fmt.time, yLabel: "C" });
  }

  function ingest(ticks) {
    for (const k of ticks) {
      S.ticks.push(k);
      const name = k.alert.level_name;
      if (name !== S.lastLevel) {
        if (S.lastLevel !== null) addLog(k.t, `${S.lastLevel} → ${name}`, LEVEL[name]?.colour);
        else addLog(k.t, `Monitoring started: ${name}`, LEVEL[name]?.colour);
        S.lastLevel = name;
      }
      if (k.quality !== S.lastQuality && S.lastQuality !== null && (k.quality === "invalid" || S.lastQuality === "invalid")) {
        addLog(k.t, k.quality === "invalid" ? "Signal unusable - forecasts withheld" : "Signal recovered");
      }
      S.lastQuality = k.quality;
      for (const ch of k.intervention_changes || []) {
        if (ch.status === "pending") addLog(ch.onset_t, `${ch.label} (${ch.origin === "manual" ? "clinician" : "pump/EHR"})`, "#6a1b9a");
        else if (ch.final) addLog(k.t, `${ch.label}: ${ch.status} (final)`, "#6a1b9a");
      }
    }
    const k = ticks[ticks.length - 1];
    if (!k) return;
    renderBanner(k);
    renderTiles(k);
    renderInterventions(k);
    if (k.waveforms) renderStrips(k);
    renderCharts();
    clock.textContent = `${fmt.time(k.t)} / ${fmt.time(S.rec.duration_sec)}`;
  }

  // ------------------------------------------------------------------ actions
  async function startSession() {
    stop();
    if (S.sid) api(`/api/session/${S.sid}`, { method: "DELETE" }).catch(() => {});
    S.ticks = []; S.log = []; S.lastLevel = null; S.lastQuality = null;
    logList.replaceChildren(); showError("");
    banner.className = "alert-banner lvl-IDLE";
    banner.replaceChildren(el("div", { class: "level" }, "READY"), el("div", { class: "reason" }, "Press Play to stream the recording through the model."), el("div"));
    const recId = recSelect.value;
    const start = startSelect.value === "" ? null : Number(startSelect.value);
    try {
      const res = await api("/api/session", { method: "POST", body: { recording_id: recId, concept, start_sec: start } });
      S.sid = res.session_id;
      S.rec = res.recording;
      clock.textContent = `${fmt.time(start ?? S.rec.first_window_end)} / ${fmt.time(S.rec.duration_sec)}`;
      await advance(1);
    } catch (e) { showError(e.message); }
  }

  async function advance(n) {
    if (!S.sid || S.inflight || S.disposed) return;
    S.inflight = true;
    try {
      const res = await api(`/api/session/${S.sid}/step`, { method: "POST", body: { n } });
      if (res.ticks.length) ingest(res.ticks);
      if (res.finished) { stop(); addLog(S.ticks.at(-1)?.t ?? 0, "End of recording"); }
    } catch (e) {
      stop(); showError(`${e.message} Press Restart to begin again.`);
    } finally { S.inflight = false; }
  }

  function play() {
    if (!S.sid) return;
    S.playing = true; playBtn.textContent = "❚❚ Pause";
    S.timer = setInterval(() => advance(S.speed), 1000);
  }
  function stop() {
    S.playing = false; playBtn.textContent = "▶ Play";
    if (S.timer) clearInterval(S.timer);
    S.timer = null;
  }

  async function confirmMark(btn) {
    const kind = btn.dataset.kind;
    if (S.pendingConfirm !== btn) {
      // Error prevention (Nielsen): a second, explicit click confirms the entry.
      if (S.pendingConfirm) S.pendingConfirm.textContent = S.pendingConfirm.dataset.label;
      btn.dataset.label = btn.dataset.label || btn.textContent;
      btn.textContent = `Confirm ${kind} at ${fmt.time(S.ticks.at(-1)?.t)}?`;
      btn.classList.add("confirm");
      S.pendingConfirm = btn;
      setTimeout(() => { if (S.pendingConfirm === btn) { btn.textContent = btn.dataset.label; btn.classList.remove("confirm"); S.pendingConfirm = null; } }, 4000);
      return;
    }
    btn.textContent = btn.dataset.label; btn.classList.remove("confirm"); S.pendingConfirm = null;
    try {
      const res = await api(`/api/session/${S.sid}/event`, { method: "POST", body: { type: kind } });
      addLog(res.record.onset_t, `${res.record.label}`, "#6a1b9a");
      if (S.ticks.length) {
        const last = S.ticks.at(-1);
        last.interventions = [...(last.interventions || []), res.record];
        renderInterventions(last);
      }
    } catch (e) { showError(e.message); }
  }

  function populateStarts() {
    const rec = S.recordings.find((r) => r.id === recSelect.value);
    const opts = [el("option", { value: "" }, "Beginning")];
    if (rec) {
      for (const ev of rec.events.slice(0, 3)) {
        const t = Math.max(rec.first_window_end, ev.t - 60);
        opts.push(el("option", { value: t }, `1 min before ${ev.label.toLowerCase()} (${fmt.time(ev.t)})`));
      }
      if (rec.duration_sec > 600) opts.push(el("option", { value: Math.round(rec.duration_sec / 2) }, `Midpoint (${fmt.time(rec.duration_sec / 2)})`));
    }
    startSelect.replaceChildren(...opts);
  }

  // ------------------------------------------------------------------ wiring
  playBtn.addEventListener("click", () => (S.playing ? stop() : play()));
  stepBtn.addEventListener("click", () => { stop(); advance(1); });
  resetBtn.addEventListener("click", startSession);
  recSelect.addEventListener("change", () => { populateStarts(); startSession(); });
  startSelect.addEventListener("change", startSession);
  speedSeg.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    S.speed = Number(b.dataset.speed);
    speedSeg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (S.playing) { stop(); play(); }
  });
  truthToggle.addEventListener("change", () => {
    S.truth = truthToggle.checked;
    root.querySelectorAll(".truth-legend").forEach((x) => (x.hidden = !S.truth));
    if (S.ticks.length) { renderTiles(S.ticks.at(-1)); renderCharts(); }
  });
  const onKey = (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); S.playing ? stop() : play(); }
    if (e.code === "ArrowRight") { e.preventDefault(); stop(); advance(1); }
  };
  const onResize = () => { if (S.ticks.length) { renderCharts(); const w = S.ticks.findLast((x) => x.waveforms); if (w) renderStrips(w); } };
  window.addEventListener("keydown", onKey);
  window.addEventListener("resize", onResize);

  (async () => {
    try {
      const { recordings } = await api("/api/recordings");
      S.recordings = recordings;
      const groups = { synthetic: "Simulated patients (with ground truth)", vitaldb: "Real surgical patients (VitalDB)" };
      recSelect.replaceChildren(...Object.entries(groups).map(([src, label]) =>
        el("optgroup", { label }, recordings.filter((r) => r.source === src).map((r) =>
          el("option", { value: r.id, title: r.description }, r.title)))));
      const preferred = isA ? "vasopressor" : "septic";
      const pick = recordings.find((r) => r.title.toLowerCase().includes(preferred)) || recordings[0];
      recSelect.value = pick.id;
      populateStarts();
      await startSession();
    } catch (e) { showError(e.message); }
  })();

  return () => {
    S.disposed = true; stop();
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", onResize);
    if (S.sid) api(`/api/session/${S.sid}`, { method: "DELETE" }).catch(() => {});
  };
}
