// Concept C - Clinician Decision Support Review System (original concept 5).
// Retrospective: processes a stored episode in batch for handoff, audit and
// quality review. It never alerts in real time. Shown in the same full-screen
// monitor frame as Concepts A and B (a "review station"), without page scroll.
import { api, el, fmt, href, LEVEL } from "./api.js";
import { drawChart } from "./charts.js";

const COL = { measured: "#ff8a80", forecast: "#c792ff", truth: "#ffab40", r: "#4dd0a0", c: "#ff80ab" };
const ORDER = ["STABLE", "WATCH", "DETERIORATING", "CRITICAL", "SIGNAL CHECK"];

export function mountReview(root, meta) {
  const info = meta.concepts.C;
  const th = meta.thresholds;
  const recSelect = el("select", { "aria-label": "Episode to review" });
  const runBtn = el("button", { class: "pm-key primary" }, "Generate review");
  const status = el("div", { class: "pm-clock", role: "status" }, "");
  const band = el("section", { class: "pm-alarm lvl-REVIEW", role: "status" },
    el("div", { class: "pm-alarm-txt" }, el("div", { class: "pm-alarm-level" }, "RETROSPECTIVE REVIEW"),
      el("div", { class: "pm-alarm-reason" }, "Select an episode. No real-time alerts are issued in this mode.")));
  let data = null;

  const top = el("header", { class: "pm-top" },
    el("a", { class: "pm-menu", href: "#/", title: "Back to the overview" }, "☰ Menu"),
    el("div", { class: "pm-id" },
      el("div", { class: "pm-concept" }, el("span", { class: "pm-tag" }, "C"), info.short || info.name),
      el("label", { class: "pm-bed" }, "Episode", recSelect)),
    band,
    el("div", { class: "pm-right" }, status, el("div", { class: "pm-ru" }, "REVIEW STATION · research prototype")));

  // Compact colour key for a channel label column.
  const key = (items) => el("span", { class: "pm-key-list" }, items.map(([t, c, mark]) =>
    el("span", {}, el("i", { style: `background:${c}` , class: mark ? "bar" : "" }), t)));
  const canv = (label) => el("canvas", { class: "fill", "aria-label": label });
  const C = { map: canv("MAP timeline with alert levels"), r: canv("Resistance trend"), c: canv("Compliance trend") };
  const stateBar = el("div", { class: "pm-statebar", role: "img", "aria-label": "Share of time in each alert state" });
  const channel = (cls, name, colour, canvas, scale) =>
    el("div", { class: `pm-ch ${cls}` },
      el("div", { class: "pm-ch-lbl", style: `color:${colour}` }, name, scale ? el("span", { class: "pm-scale" }, scale) : null),
      el("div", { class: "pm-plot" }, canvas));
  const left = el("section", { class: "pm-waves review", "aria-label": "Episode timeline" },
    channel("chart big", "MAP", COL.forecast, C.map, key([["measured", COL.measured], ["forecast", COL.forecast], ["true", COL.truth], ["treatment", COL.forecast, "|"]])),
    channel("chart", "R", COL.r, C.r, "resistance · trend"),
    channel("chart", "C", COL.c, C.c, "compliance · trend"),
    el("div", { class: "pm-ch state" }, el("div", { class: "pm-ch-lbl", style: "color:#b0bec5" }, "TIME IN STATE"), stateBar));

  const P = {};
  const param = (key, label, colour, cls = "") => {
    const box = el("div", { class: `pm-p ${cls}`, style: `--c:${colour}` }, el("div", { class: "pm-p-lbl" }, label), el("div", { class: "pm-p-body" }));
    P[key] = box.querySelector(".pm-p-body");
    return box;
  };
  const right = el("section", { class: "pm-params review", "aria-label": "Episode summary" },
    param("glance", "EPISODE AT A GLANCE", "#b0bec5", "glance"),
    param("handoff", "HANDOFF SUMMARY", "#ffd54f", "handoff"),
    param("episodes", "ALERT EPISODES", "#ff5252", "table"),
    param("tx", "TREATMENTS & RESPONSE", COL.forecast, "table"),
    param("check", "FORECAST CHECK", "#4fc3f7"));

  const exportLinks = el("div", { class: "pm-exports" });
  const keys = el("footer", { class: "pm-keys", "aria-label": "Review controls" },
    runBtn, exportLinks, el("span", { class: "grow" }),
    el("span", { class: "pm-note" }, "Documentation, handoff and audit only - cannot protect a patient in real time."),
    el("nav", { class: "pm-switch", "aria-label": "Switch concept" },
      ["a", "b", "c"].map((c) => el("a", { href: `#/concept-${c}`, class: c === "c" ? "on" : "" }, c.toUpperCase()))));

  root.replaceChildren(el("div", { class: "pm", "data-concept": "C" }, top, el("div", { class: "pm-main" }, left, right), keys));

  const sub = (...t) => el("div", { class: "pm-sub" }, ...t);
  const put = (node, ...kids) => node.replaceChildren(...kids.filter((x) => x !== null && x !== undefined && x !== false));

  function draw() {
    if (!data) return;
    const tl = data.timeline;
    const t0 = tl[0].t, t1 = tl[tl.length - 1].t;
    const spans = tl.map((r, i) => ({ x0: r.t, x1: tl[i + 1] ? tl[i + 1].t : r.t + data.step_sec,
      colour: LEVEL[r.level_name]?.colour || "#999", alpha: 0.3 }));
    const vlines = data.interventions.map((iv) => ({ x: iv.onset_t, label: iv.type, colour: COL.forecast }));
    const series = [
      { points: tl.map((r) => [r.t, r.current_map ?? NaN]), colour: COL.measured, width: 1.3 },
      { points: tl.map((r) => [r.t, r.forecast_map ?? NaN]), colour: COL.forecast, width: 1.8 },
    ];
    if (tl.some((r) => r.truth_future_map !== null)) {
      series.push({ points: tl.map((r) => [r.t, r.truth_future_map ?? NaN]), colour: COL.truth, width: 1.1, dash: [6, 4] });
    }
    drawChart(C.map, { height: "fill", theme: "dark", xMin: t0, xMax: t1, series, spans, vlines, xFmt: fmt.time,
      hlines: [{ y: th.target, label: `${th.target}`, colour: "#ffd54f" }, { y: th.critical, label: `${th.critical}`, colour: "#ff5252" }] });
    drawChart(C.r, { height: "fill", theme: "dark", xMin: t0, xMax: t1, vlines, xFmt: fmt.time,
      series: [{ points: tl.map((r) => [r.t, r.r ?? NaN]), colour: COL.r, width: 1.5 }] });
    drawChart(C.c, { height: "fill", theme: "dark", xMin: t0, xMax: t1, vlines, xFmt: fmt.time,
      series: [{ points: tl.map((r) => [r.t, r.c ?? NaN]), colour: COL.c, width: 1.5 }] });
  }

  function render() {
    const s = data.summary, recId = recSelect.value, pct = s.time_in_state_pct;
    band.className = "pm-alarm lvl-REVIEW";
    band.replaceChildren(el("div", { class: "pm-alarm-txt" },
      el("div", { class: "pm-alarm-level" }, "RETROSPECTIVE REVIEW", el("span", {}, data.recording.title)),
      el("div", { class: "pm-alarm-reason" },
        `${fmt.time(s.monitored_sec)} monitored · ${fmt.num(pct.DETERIORATING + pct.CRITICAL)}% at DETERIORATING or CRITICAL · ` +
        `${s.alert_episodes} alert episode(s) · lowest forecast MAP ${fmt.num(s.min_forecast_map)} mmHg`)));

    stateBar.replaceChildren(...ORDER.filter((k) => pct[k] > 0).map((k) =>
      el("span", { style: `flex:${pct[k]};background:${LEVEL[k].colour}`, title: `${k} ${pct[k]}%` },
        pct[k] >= 7 ? `${k === "DETERIORATING" ? "DETER." : k} ${fmt.num(pct[k])}%` : "")));

    const kv = (k, v) => el("div", { class: "pm-kv" }, el("span", {}, k), el("b", {}, v));
    put(P.glance, el("div", { class: "pm-kvs" },
      kv("Monitored", fmt.time(s.monitored_sec)), kv("Alert episodes", String(s.alert_episodes)),
      kv("Lowest fMAP", `${fmt.num(s.min_forecast_map)} mmHg`), kv("Treatments", String(data.interventions.length)),
      kv("Withheld", `${fmt.num(s.abstention_pct)}%`), kv("Batch time", `${fmt.num(s.median_latency_ms, 1)} ms/win`)));

    put(P.handoff, el("ul", { class: "pm-narr" }, data.narrative.map((line) => el("li", {}, line))));

    const MAXROWS = 4;
    const eps = data.episodes;
    put(P.episodes, eps.length
      ? el("table", {}, el("thead", {}, el("tr", {}, ["Start", "End", "Dur", "Peak", "Low"].map((h) => el("th", {}, h)))),
          el("tbody", {}, eps.slice(0, MAXROWS).map((e) => el("tr", {}, el("td", {}, fmt.time(e.start)), el("td", {}, fmt.time(e.end)),
            el("td", {}, `${fmt.num(e.duration_sec)} s`),
            el("td", {}, el("span", { class: "lvl-dot", style: `background:${LEVEL[e.peak_level].colour}` }), e.peak_level),
            el("td", {}, fmt.num(e.min_forecast_map))))))
      : sub("No alert episodes."),
      eps.length > MAXROWS ? sub(`+ ${eps.length - MAXROWS} more in the printable report`) : null);

    const ivs = data.interventions;
    put(P.tx, ivs.length
      ? el("table", {}, el("thead", {}, el("tr", {}, ["Onset", "Event", "Response", "ΔMAP", "ΔR"].map((h) => el("th", {}, h)))),
          el("tbody", {}, ivs.slice(0, MAXROWS).map((iv) => el("tr", {}, el("td", {}, fmt.time(iv.onset_t)), el("td", {}, iv.label),
            el("td", {}, el("span", { class: `status ${iv.status.replaceAll(" ", "-")}` }, iv.status)),
            el("td", {}, iv.d_map === null ? "—" : `${iv.d_map > 0 ? "+" : ""}${fmt.num(iv.d_map)}`),
            el("td", {}, iv.r_change_pct === null ? "—" : `${iv.r_change_pct > 0 ? "+" : ""}${fmt.num(iv.r_change_pct)}%`)))))
      : sub("No treatments recorded."),
      ivs.length > MAXROWS ? sub(`+ ${ivs.length - MAXROWS} more in the printable report`) : null);

    const v = data.verification;
    put(P.check, v && v.windows
      ? sub(`Against ${v.truth_kind}: MAE `, el("b", {}, `${fmt.num(v.map_mae, 1)} mmHg`),
          `, bias ${v.map_bias > 0 ? "+" : ""}${fmt.num(v.map_bias, 1)} mmHg over ${v.windows} windows` +
          (v.r_correlation !== undefined ? `; R r=${fmt.num(v.r_correlation, 2)}, C r=${fmt.num(v.c_correlation, 2)}` : "") + ".")
      : sub("Not available."));

    exportLinks.replaceChildren(
      el("a", { class: "pm-key", href: href(`/api/review/${recId}/report.html`), target: "_blank", rel: "noopener" }, "Printable report"),
      el("a", { class: "pm-key", href: href(`/api/review/${recId}/timeline.csv`), download: `timeline_${recId}.csv` }, "CSV"),
      el("a", { class: "pm-key", href: href(`/api/review/${recId}/review.json`), download: `review_${recId}.json` }, "JSON"));
    draw();
  }

  async function run() {
    runBtn.disabled = true; status.textContent = "Processing…";
    const t = performance.now();
    try {
      data = await api(`/api/review/${recSelect.value}`);
      status.replaceChildren(el("b", {}, fmt.time(data.summary.monitored_sec)), el("span", {}, ` · ${data.timeline.length} windows`));
      render();
    } catch (e) { status.textContent = e.message; }
    finally { runBtn.disabled = false; }
  }

  runBtn.addEventListener("click", run);
  recSelect.addEventListener("change", run);
  let resizeTimer = null;
  const onResize = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 80); };
  window.addEventListener("resize", onResize);

  (async () => {
    const { recordings } = await api("/api/recordings");
    const groups = { synthetic: "Simulated patients (with ground truth)", vitaldb: "Real surgical patients (VitalDB)" };
    recSelect.replaceChildren(...Object.entries(groups).map(([src, label]) =>
      el("optgroup", { label }, recordings.filter((r) => r.source === src).map((r) => el("option", { value: r.id }, r.title)))));
    recSelect.value = (recordings.find((r) => r.source === "vitaldb" && r.events.length) || recordings[0]).id;
    run();
  })();

  return () => window.removeEventListener("resize", onResize);
}
