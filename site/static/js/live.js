// Live monitor shared by Concept A (integrated monitor) and Concept B
// (non-invasive warning), laid out like a bedside patient monitor: one fixed,
// full-screen, non-scrolling display with the alarm message field at the top,
// waveform channels on the left, numeric parameter boxes on the right and
// soft-keys along the bottom. Both concepts use the SAME layout, so no control
// or alarm field ever moves between them (BME Design 8.2.5.3).
import { api, el, fmt, LEVEL, levelIcon, STATIC } from "./api.js";
import { drawChart, drawStrip } from "./charts.js";

// Monitor colour convention: ECG green, arterial pressure red, pleth cyan.
const COL = { ecg: "#39ff9c", abp: "#ff5a5a", ppg: "#4dd8ee", forecast: "#c792ff", truth: "#ffab40",
              r: "#4dd0a0", c: "#ff80ab", measured: "#ff8a80" };
const SPEEDS = [1, 2, 5, 10, 20];

// Beat rate (per min) from a 10 s window by autocorrelation: robust to
// beat-to-beat amplitude changes. Display only - not a verified model output.
function beatRate(x, fs) {
  if (!x || x.length < fs * 4) return null;
  const n = x.length, mean = x.reduce((a, v) => a + v, 0) / n;
  const d = x.map((v) => v - mean);
  const r0 = d.reduce((a, v) => a + v * v, 0);
  if (!(r0 > 1e-9)) return null;
  const lo = Math.round(0.27 * fs), hi = Math.round(2.0 * fs);   // 30-220 per min
  const r = [];
  for (let lag = lo - 1; lag <= hi + 1; lag++) {
    let acc = 0;
    for (let i = 0; i + lag < n; i++) acc += d[i] * d[i + lag];
    r.push(acc / r0);
  }
  const peaks = [];
  for (let i = 1; i < r.length - 1; i++) if (r[i] >= r[i - 1] && r[i] >= r[i + 1] && r[i] > 0.2) peaks.push(i);
  if (!peaks.length) return null;
  const top = Math.max(...peaks.map((i) => r[i]));
  const first = peaks.find((i) => r[i] >= 0.8 * top);   // earliest strong peak: avoids double-period
  return Math.round((60 * fs) / (first + lo - 1));
}

// HR from ECG; if ECG is missing or disagrees with the pleth by > 15 %, show
// the pleth pulse rate instead (labelled PR), as bedside monitors do.
function heartRate(w) {
  const hr = beatRate(w.ecg, w.fs), pr = beatRate(w.ppg, w.fs);
  if (hr && (!pr || Math.abs(hr - pr) / pr < 0.15)) return { v: hr, src: "HR" };
  return pr ? { v: pr, src: "PR" } : null;
}

function percentile(arr, p) {
  const s = arr.filter((v) => Number.isFinite(v) && v > 5).sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : null;
}

export function mountLive(root, concept, meta) {
  const isA = concept === "A";
  const info = meta.concepts[concept];
  const th = meta.thresholds;
  const S = { sid: null, rec: null, ticks: [], playing: false, speed: 5, inflight: false, timer: null,
              truth: false, log: [], lastLevel: null, lastQuality: null, disposed: false, pendingConfirm: null,
              lastWave: null };

  // ------------------------------------------------------------------ top bar
  const recSelect = el("select", { id: "rec-select", "aria-label": "Patient (bed)" });
  const clock = el("div", { class: "pm-clock", id: "clock", "aria-live": "off" }, "--:--");
  const alarm = el("section", { class: "pm-alarm lvl-IDLE", id: "alert-banner", role: "status", "aria-live": "polite" });
  const top = el("header", { class: "pm-top" },
    el("a", { class: "pm-menu", href: "#/", title: "Back to the overview" }, "☰ Menu"),
    el("div", { class: "pm-id" },
      el("div", { class: "pm-concept" }, el("span", { class: "pm-tag" }, concept), info.short || info.name),
      el("label", { class: "pm-bed" }, "Bed 1", recSelect)),
    alarm,
    el("div", { class: "pm-right" }, clock,
      el("div", { class: "pm-ru", title: "Research prototype: clinical decision support only. It does not diagnose or recommend treatment." },
        STATIC ? "RESEARCH DEMO · pre-computed" : "RESEARCH PROTOTYPE")));

  // ------------------------------------------------------------------ waveform / chart channels
  // Compact colour key for a channel label column.
  const key = (items, title = "") => el("span", { class: "pm-key-list" }, title ? el("span", {}, title) : null,
    items.map(([t, c, mark]) => el("span", {}, el("i", { style: `background:${c}`, class: mark || "" }), t)));
  const canv = (label) => el("canvas", { class: "fill", "aria-label": label });
  const C = {
    ecg: canv("ECG lead II waveform"), abp: canv("Arterial pressure waveform"), ppg: canv("Pleth waveform"),
    fc: canv("MAP forecast, last 60 s and next 15 s"), trend: canv("Session MAP trend with alert levels"),
  };
  const channel = (cls, name, colour, canvas, scale = null, extra = null) =>
    el("div", { class: `pm-ch ${cls}` },
      el("div", { class: "pm-ch-lbl", style: `color:${colour}` }, name, scale ? el("span", { class: "pm-scale" }, scale) : null),
      el("div", { class: "pm-plot" }, canvas, extra));
  const waves = el("section", { class: "pm-waves", "aria-label": "Waveforms and trends" },
    channel("wave", "II", COL.ecg, C.ecg, "ECG"),
    isA ? channel("wave", "ART", COL.abp, C.abp, "20–180 mmHg")
        : el("div", { class: "pm-ch wave off" }, el("div", { class: "pm-ch-lbl", style: `color:${COL.abp}` }, "ART"),
            el("div", { class: "pm-off" }, "ART not connected · non-invasive mode (ECG + pulse oximeter only)")),
    channel("wave", "PLETH", COL.ppg, C.ppg),
    channel("chart", isA ? "FCST" : "EST", COL.forecast, C.fc,
      key([...(isA ? [["measured", COL.measured]] : []), ["forecast", COL.forecast], ["±σ", COL.forecast, "band"], ["true", COL.truth]],
        isA ? "MAP · 60 s + 15 s" : "MAP · LOW CONF.")),
    channel("chart", "TREND", "#b0bec5", C.trend, key([["alert level", "#2e7d32", "band"], ...(isA ? [["treatment", COL.forecast, "bar"]] : [])], "session MAP")));

  // ------------------------------------------------------------------ parameter boxes
  const P = {};
  const param = (key, label, colour, extraCls = "") => {
    const box = el("div", { class: `pm-p ${extraCls}`, style: `--c:${colour}` },
      el("div", { class: "pm-p-lbl" }, label), el("div", { class: "pm-p-body" }));
    P[key] = box.querySelector(".pm-p-body");
    return box;
  };
  const logList = el("ul", { class: "pm-log", id: "event-log" });
  const params = el("section", { class: "pm-params", "aria-label": "Numeric parameters" },
    param("hr", "HR  bpm", COL.ecg),
    param("art", isA ? "ART  mmHg" : "ART", COL.abp),
    param("fc", isA ? "FCST MAP · 15 s" : "EST MAP · 15 s", COL.forecast),
    param("risk", `RISK  P(MAP<${th.target})`, "#ffd54f"),
    param("rc", "VASCULAR STATE  (trend)", COL.r, "rc"),
    isA ? param("tx", "TX RESPONSE", COL.forecast, "tx") : param("inputs", "INPUTS", "#b0bec5", "tx"),
    el("div", { class: "pm-p log", style: "--c:#90a4ae" }, el("div", { class: "pm-p-lbl" }, "EVENTS"), logList),
    el("div", { class: "pm-status", id: "mon-quality" }, ""));

  // ------------------------------------------------------------------ soft-keys
  const startSelect = el("select", { id: "start-select", "aria-label": "Start position" });
  const playBtn = el("button", { class: "pm-key primary", id: "play-btn", title: "Play / pause (Space)" }, "▶ Play");
  const stepBtn = el("button", { class: "pm-key", id: "step-btn", title: "Advance one update (→)" }, "Step +1 s");
  const resetBtn = el("button", { class: "pm-key", id: "reset-btn", title: "Restart this recording" }, "↺ Restart");
  const speedSeg = el("div", { class: "pm-seg", role: "group", "aria-label": "Playback speed" },
    SPEEDS.map((s) => el("button", { "data-speed": s, "aria-pressed": String(s === S.speed) }, `${s}×`)));
  const truthToggle = el("input", { type: "checkbox", id: "truth-toggle" });
  const helpBtn = el("button", { class: "pm-key", title: "How alerts work" }, "? Alarms");
  const markBtns = [];
  if (isA && !STATIC) {
    for (const [kind, label] of [["vasopressor", "+ Vasopressor"], ["fluid", "+ Fluid bolus"]]) {
      const b = el("button", { class: "pm-key warn", "data-kind": kind }, label);
      b.addEventListener("click", () => confirmMark(b));
      markBtns.push(b);
    }
  }
  const keys = el("footer", { class: "pm-keys", "aria-label": "Monitor controls" },
    el("label", { class: "pm-key-lbl", for: "start-select" }, "Start"), startSelect,
    playBtn, stepBtn, resetBtn, speedSeg, ...markBtns,
    el("span", { class: "grow" }),
    el("label", { class: "pm-key toggle", for: "truth-toggle", title: "Shows what actually happened. Not available at the bedside." },
      truthToggle, "Verification overlay"),
    helpBtn,
    el("nav", { class: "pm-switch", "aria-label": "Switch concept" },
      ["a", "b", "c"].map((c) => el("a", { href: `#/concept-${c}`, class: c.toUpperCase() === concept ? "on" : "" }, c.toUpperCase()))));

  const errorBox = el("div", { class: "pm-toast", hidden: true, role: "alert" });
  const help = el("div", { class: "pm-help", hidden: true, role: "dialog", "aria-label": "How alerts work" },
    el("div", { class: "pm-help-card" },
      el("h2", {}, "How alerts work"),
      el("dl", {},
        el("dt", {}, "STABLE"), el("dd", {}, `Forecast MAP at or above ${th.watch} mmHg.`),
        el("dt", {}, "WATCH · low priority"), el("dd", {}, `Forecast below ${th.watch}, or falling ≥ ${th.rapid_drop} mmHg over 15 s.`),
        el("dt", {}, "DETERIORATING · medium"), el("dd", {}, `Forecast below the ${th.target} mmHg treatment target.`),
        el("dt", {}, "CRITICAL · high"), el("dd", {}, `Forecast below ${th.critical} mmHg.`),
        el("dt", {}, "Persistence"), el("dd", {}, `Escalates after ${th.escalate_persist} consecutive updates; steps down after ${th.deescalate_persist}, with a 2 mmHg margin.`),
        el("dt", {}, "Confidence cap"), el("dd", {}, "A low-confidence estimate can raise at most WATCH; moderate at most DETERIORATING."),
        el("dt", {}, "Fail-safe"), el("dd", {}, "The alarm never drops below what the measured arterial pressure shows, and a weaker estimate cannot clear an alarm."),
        el("dt", {}, "SIGNAL CHECK"), el("dd", {}, "Technical alarm: the signal is unusable, so no haemodynamic alert is issued."),
        el("dt", {}, "HR"), el("dd", {}, "Beat rate from the 10 s ECG on screen (PR from the pleth if the ECG is missing or disagrees). For orientation only; not a verified model output."),
        el("dt", {}, "Keyboard"), el("dd", {}, "Space = play / pause · → = step one update · Esc = close")),
      el("p", { class: "pm-help-note" }, "Research prototype · clinical decision support only. It does not diagnose or recommend treatment."),
      el("button", { class: "pm-key primary" }, "Close")));

  root.replaceChildren(el("div", { class: "pm", "data-concept": concept },
    top, el("div", { class: "pm-main" }, waves, params), keys, errorBox, help));

  // ------------------------------------------------------------------ helpers
  function showError(msg) { errorBox.textContent = msg; errorBox.hidden = !msg; }

  function addLog(t, text, colour) {
    S.log.unshift({ t, text, colour });
    S.log = S.log.slice(0, 40);
    logList.replaceChildren(...S.log.map((e) =>
      el("li", {}, el("span", { class: "t" }, fmt.time(e.t)),
        el("span", {}, e.colour ? el("span", { class: "lvl-dot", style: `background:${e.colour}` }) : null, e.text))));
  }

  // replaceChildren() would print "null", so empty slots are dropped first.
  const put = (node, ...kids) => node.replaceChildren(...kids.filter((x) => x !== null && x !== undefined && x !== false));
  const big = (v, unit, cls = "") => el("div", { class: `pm-big ${cls}` }, v, unit ? el("small", {}, unit) : null);
  const sub = (...t) => el("div", { class: "pm-sub" }, ...t);

  // ------------------------------------------------------------------ rendering
  function renderAlarm(k) {
    const a = k.alert, name = a.level_name;
    alarm.className = `pm-alarm lvl-${a.technical ? "SIGNAL" : name}`;
    alarm.title = a.reason;
    alarm.replaceChildren(levelIcon(name),
      el("div", { class: "pm-alarm-txt" },
        el("div", { class: "pm-alarm-level" }, name, el("span", {}, LEVEL[name]?.priority || ""),
          a.held ? el("span", { class: "held" }, "⚑ HELD") : null),
        el("div", { class: "pm-alarm-reason" }, a.reason)));
  }

  function renderParams(k) {
    const truth = S.truth && k.truth ? k.truth : null;
    const w = S.lastWave;
    // HR
    const hr = w ? heartRate(w) : null;
    P.hr.previousSibling.textContent = `${hr ? hr.src : "HR"}  /min`;
    put(P.hr, big(hr ? hr.v : "---", "", "hr"), sub(hr && hr.src === "PR" ? "PR from pleth · display only" : "from ECG · display only"));
    // ART
    if (isA) {
      const recent = w && w.abp ? w.abp.slice(-Math.round(4 * w.fs)) : [];
      const sys = k.current_map !== null ? percentile(recent, 0.97) : null;
      const dia = k.current_map !== null ? percentile(recent, 0.03) : null;
      put(P.art, 
        big(sys !== null ? `${Math.round(sys)}/${Math.round(dia)}` : "---/---", "", "art"),
        el("div", { class: "pm-mid" }, `(${fmt.num(k.current_map)})`, el("small", {}, " MAP")),
        sub(k.conventional_alarm === null ? "arterial line unavailable" : `threshold alarm: ${k.conventional_alarm ? "ALARMING" : "quiet"}`));
    } else {
      put(P.art, big("---", "", "off"), sub("no arterial line"));
    }
    // Forecast / estimate
    put(P.fc, 
      big(fmt.num(k.forecast_map), "mmHg"),
      sub(`± ${fmt.num(k.sigma, isA ? 1 : 0)} · `, el("b", { class: `conf ${k.confidence || ""}` }, `${k.confidence || "—"} confidence`)),
      truth ? sub(el("span", { style: `color:${COL.truth}` }, `true future ${fmt.num(truth.future_map)} mmHg`)) : null);
    // Risk
    const score = k.risk_score;
    const rc = score === null ? "#78909c" : score >= 80 ? LEVEL.CRITICAL.colour : score >= 50 ? LEVEL.DETERIORATING.colour
      : score >= 20 ? LEVEL.WATCH.colour : LEVEL.STABLE.colour;
    put(P.risk, 
      el("div", { class: "pm-riskrow" }, big(score === null ? "--" : String(score), "%", "risk"),
        el("div", { class: "pm-meter", "aria-hidden": "true" }, el("i", { style: `width:${score ?? 0}%;background:${rc}` }))),
      sub(isA ? "calibrated probability" : `alert capped at ${k.alert.confidence_cap || "WATCH"}`));
    // R / C
    put(P.rc, el("div", { class: "pm-rc" },
      el("div", {}, el("span", { style: `color:${COL.r}` }, "R "), el("b", {}, fmt.num(k.r, 2)),
        truth && truth.r !== undefined ? el("small", { style: `color:${COL.truth}` }, ` true ${fmt.num(truth.r, 2)}`) : null),
      el("div", {}, el("span", { style: `color:${COL.c}` }, "C "), el("b", {}, fmt.num(k.c, 2)),
        truth && truth.c !== undefined ? el("small", { style: `color:${COL.truth}` }, ` true ${fmt.num(truth.c, 2)}`) : null)),
      sub("relative trend · not calibrated (see docs 07)"));
    // Treatment response (A) or inputs (B)
    if (isA) renderTx(k);
    else {
      put(P.inputs, el("div", { class: "pm-inputs" },
        el("span", { class: "yes" }, "ECG ✓"), el("span", { class: "yes" }, "PLETH ✓"),
        el("span", { class: "no" }, "ART ✗"), el("span", { class: "no" }, "EVENTS ✗")),
        sub(`Error ±${fmt.num(k.sigma, 0)} mmHg without arterial pressure: alarms limited to WATCH.`));
    }
    root.querySelector("#mon-quality").textContent =
      `SQI ${(k.quality || "—").replace("_", " ").toUpperCase()}` +
      `${k.warnings.length ? " · " + k.warnings[0] : ""} · ${k.regime ? k.regime.replaceAll("_", "+").toUpperCase() : ""} · ` +
      `${fmt.num(k.latency_ms, 1)} ms`;
  }

  function renderTx(k) {
    const records = k.interventions || [];
    if (!records.length) {
      put(P.tx, big("—", "", "tx"), sub(STATIC ? "no treatment event yet (pump/EHR feed)" : "no treatment yet · use + Vasopressor / + Fluid"));
      return;
    }
    const r = records[records.length - 1];
    const since = S.ticks.length ? S.ticks[S.ticks.length - 1].t - r.onset_t : 0;
    const parts = [];
    if (r.d_map !== null) parts.push(`MAP ${r.d_map > 0 ? "+" : ""}${fmt.num(r.d_map, 0)}`);
    if (r.r_change_pct !== null) parts.push(`R ${r.r_change_pct > 0 ? "+" : ""}${fmt.num(r.r_change_pct, 0)}%`);
    put(P.tx, 
      el("div", { class: `pm-tx status ${r.status.replaceAll(" ", "-")}` }, r.status.toUpperCase()),
      sub(`${r.label} · ${fmt.time(r.onset_t)}`),
      sub(r.status === "pending" ? `assessing in ${Math.max(0, Math.ceil(30 - since))} s` : parts.join(" · ") + (r.final ? " · final" : "")),
      r.confounded_with && r.confounded_with.length ? sub("⚠ overlapping treatments") : null);
  }

  function renderStrips() {
    const w = S.lastWave;
    if (!w) return;
    drawStrip(C.ecg, w.ecg, COL.ecg, { height: "fill" });
    drawStrip(C.ppg, w.ppg, COL.ppg, { height: "fill" });
    if (isA) drawStrip(C.abp, w.abp, COL.abp, { fixed: [20, 180], unit: "mmHg", height: "fill" });
  }

  function renderCharts() {
    if (!S.ticks.length) return;
    const k = S.ticks[S.ticks.length - 1];
    const now = k.t;
    const hist = S.ticks.filter((x) => x.t >= now - 60);
    const series = [], bands = [];
    if (isA) series.push({ points: hist.map((x) => [x.t, x.current_map ?? NaN]), colour: COL.measured, width: 2 });
    series.push({ points: hist.map((x) => [x.t, x.forecast_map ?? NaN]), colour: COL.forecast, width: 1.3, dash: [3, 3] });
    if (k.forecast_abp && k.forecast_abp.length) {
      // Predicted instantaneous pressure samples are dots; their mean is the forecast MAP.
      const avg = k.forecast_map, end = now + k.forecast_abp.length - 1;
      bands.push({ points: [[now, avg - k.sigma, avg + k.sigma], [end, avg - k.sigma, avg + k.sigma]], colour: COL.forecast, alpha: 0.2 });
      series.push({ points: [[now, avg], [end, avg]], colour: COL.forecast, width: 3 });
      series.push({ points: k.forecast_abp.map((v, i) => [now + i, v]), colour: COL.forecast, width: 0, dots: true });
    }
    if (S.truth) {
      series.push({ points: hist.map((x) => [x.t, x.truth?.current_map ?? NaN]), colour: COL.truth, width: 1.6, dash: [6, 4] });
      if (k.truth?.future_map != null) series.push({ points: [[now, k.truth.future_map], [now + 14, k.truth.future_map]], colour: COL.truth, width: 2, dash: [6, 4] });
    }
    const hl = [
      { y: th.watch, label: `${th.watch}`, colour: "#4fc3f7" },
      { y: th.target, label: `${th.target}`, colour: "#ffd54f" },
      { y: th.critical, label: `${th.critical}`, colour: "#ff5252" },
    ];
    drawChart(C.fc, { height: "fill", theme: "dark", xMin: now - 60, xMax: now + 15, series, bands, hlines: hl, now,
      xFmt: fmt.time, yMin: isA ? null : 0 });

    const all = S.ticks;
    const t0 = all[0].t, t1 = Math.max(all[all.length - 1].t, t0 + 30);
    const spans = all.map((a, i) => ({ x0: a.t, x1: all[i + 1] ? all[i + 1].t : a.t + 1,
      colour: LEVEL[a.alert.level_name]?.colour || "#999", alpha: 0.28 }));
    const vlines = isA ? (k.interventions || []).map((r) => ({ x: r.onset_t, label: r.type, colour: COL.forecast })) : [];
    const tSeries = [{ points: all.map((x) => [x.t, x.forecast_map ?? NaN]), colour: COL.forecast, width: 1.8 }];
    if (isA) tSeries.unshift({ points: all.map((x) => [x.t, x.current_map ?? NaN]), colour: COL.measured, width: 1.2 });
    if (S.truth) tSeries.push({ points: all.map((x) => [x.t, x.truth?.future_map ?? NaN]), colour: COL.truth, width: 1.4, dash: [6, 4] });
    drawChart(C.trend, { height: "fill", theme: "dark", xMin: t0, xMax: t1, series: tSeries, spans, vlines, hlines: hl, xFmt: fmt.time });
  }

  function redraw() {
    if (!S.ticks.length) return;
    renderStrips();
    renderCharts();
  }

  function ingest(ticks) {
    for (const k of ticks) {
      S.ticks.push(k);
      const name = k.alert.level_name;
      if (name !== S.lastLevel) {
        addLog(k.t, S.lastLevel !== null ? `${S.lastLevel} → ${name}` : `Monitoring started: ${name}`, LEVEL[name]?.colour);
        S.lastLevel = name;
      }
      if (k.quality !== S.lastQuality && S.lastQuality !== null && (k.quality === "invalid" || S.lastQuality === "invalid")) {
        addLog(k.t, k.quality === "invalid" ? "Signal unusable - forecasts withheld" : "Signal recovered");
      }
      S.lastQuality = k.quality;
      for (const ch of k.intervention_changes || []) {
        if (ch.status === "pending") addLog(ch.onset_t, `${ch.label} (${ch.origin === "manual" ? "clinician" : "pump/EHR"})`, COL.forecast);
        else if (ch.final) addLog(k.t, `${ch.label}: ${ch.status}`, COL.forecast);
      }
      if (k.waveforms) S.lastWave = k.waveforms;
    }
    const k = ticks[ticks.length - 1];
    if (!k) return;
    renderAlarm(k);
    renderParams(k);
    redraw();
    clock.replaceChildren(el("b", {}, fmt.time(k.t)), el("span", {}, ` / ${fmt.time(S.rec.duration_sec)}`));
  }

  // ------------------------------------------------------------------ actions
  async function startSession() {
    stop();
    if (S.sid) api(`/api/session/${S.sid}`, { method: "DELETE" }).catch(() => {});
    S.ticks = []; S.log = []; S.lastLevel = null; S.lastQuality = null; S.lastWave = null;
    logList.replaceChildren(); showError("");
    alarm.className = "pm-alarm lvl-IDLE";
    alarm.replaceChildren(el("div", { class: "pm-alarm-txt" }, el("div", { class: "pm-alarm-level" }, "READY"),
      el("div", { class: "pm-alarm-reason" }, "Press Play to stream the recording through the model.")));
    const recId = recSelect.value;
    const start = startSelect.value === "" ? null : Number(startSelect.value);
    try {
      const res = await api("/api/session", { method: "POST", body: { recording_id: recId, concept, start_sec: start } });
      S.sid = res.session_id;
      S.rec = res.recording;
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
      // Error prevention (Nielsen): a second, explicit press confirms the entry.
      if (S.pendingConfirm) S.pendingConfirm.textContent = S.pendingConfirm.dataset.label;
      btn.dataset.label = btn.dataset.label || btn.textContent;
      btn.textContent = `Confirm at ${fmt.time(S.ticks.at(-1)?.t)}?`;
      btn.classList.add("confirm");
      S.pendingConfirm = btn;
      setTimeout(() => { if (S.pendingConfirm === btn) { btn.textContent = btn.dataset.label; btn.classList.remove("confirm"); S.pendingConfirm = null; } }, 4000);
      return;
    }
    btn.textContent = btn.dataset.label; btn.classList.remove("confirm"); S.pendingConfirm = null;
    try {
      const res = await api(`/api/session/${S.sid}/event`, { method: "POST", body: { type: kind } });
      addLog(res.record.onset_t, res.record.label, COL.forecast);
      if (S.ticks.length) {
        const last = S.ticks.at(-1);
        last.interventions = [...(last.interventions || []), res.record];
        renderTx(last);
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
    if (S.ticks.length) { renderParams(S.ticks.at(-1)); renderCharts(); }
  });
  const openHelp = (open) => { help.hidden = !open; if (open) help.querySelector("button").focus(); };
  helpBtn.addEventListener("click", () => openHelp(true));
  help.querySelector("button").addEventListener("click", () => openHelp(false));
  help.addEventListener("click", (e) => { if (e.target === help) openHelp(false); });
  const onKey = (e) => {
    if (e.code === "Escape") { openHelp(false); return; }
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); S.playing ? stop() : play(); }
    if (e.code === "ArrowRight") { e.preventDefault(); stop(); advance(1); }
  };
  let resizeTimer = null;
  const onResize = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(redraw, 80); };
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
