// Concept C - Clinician Decision Support Review System (original concept 5).
// Retrospective: processes a stored episode in batch for handoff, audit and
// quality review. It never alerts in real time.
import { api, el, fmt, href, LEVEL, LEVEL_BY_INDEX } from "./api.js";
import { drawChart } from "./charts.js";

export function mountReview(root, meta) {
  const info = meta.concepts.C;
  const th = meta.thresholds;
  const recSelect = el("select", { "aria-label": "Episode to review" });
  const runBtn = el("button", { class: "btn primary" }, "Generate review");
  const status = el("span", { class: "muted small", role: "status" });
  const out = el("div", { id: "review-out" });
  let data = null;

  root.replaceChildren(
    el("div", { class: "page-head" },
      el("div", {}, el("h1", {}, `Concept C · ${info.name}`),
        el("div", { class: "sub" }, `${info.inputs} · ${info.setting}`)),
      el("div", { class: "chips" }, info.combines.map((c) => el("span", { class: "chip strong" }, c)),
        el("span", { class: "chip" }, info.mode))),
    el("section", { class: "card controls" },
      el("label", {}, "Episode"), recSelect, runBtn, status, el("span", { class: "grow" }),
      el("span", { class: "small muted" }, "Retrospective review - no real-time alerts are issued.")),
    out);

  function bar(summary) {
    const order = ["STABLE", "WATCH", "DETERIORATING", "CRITICAL", "SIGNAL CHECK"];
    return el("div", {},
      el("div", { class: "state-bar", role: "img", "aria-label": "Share of time in each alert state" },
        order.filter((k) => summary.time_in_state_pct[k] > 0).map((k) =>
          el("span", { style: `width:${summary.time_in_state_pct[k]}%;background:${LEVEL[k].colour}`, title: `${k} ${summary.time_in_state_pct[k]}%` }))),
      el("div", { class: "legend" }, order.map((k) =>
        el("span", {}, el("span", { class: "lvl-dot", style: `background:${LEVEL[k].colour}` }), `${k} ${fmt.num(summary.time_in_state_pct[k])}%`))));
  }

  function draw() {
    if (!data) return;
    const tl = data.timeline;
    const t0 = tl[0].t, t1 = tl[tl.length - 1].t;
    const spans = tl.map((r, i) => ({ x0: r.t, x1: tl[i + 1] ? tl[i + 1].t : r.t + data.step_sec,
      colour: LEVEL[r.level_name]?.colour || "#999", alpha: 0.2 }));
    const vlines = data.interventions.map((iv) => ({ x: iv.onset_t, label: iv.type, colour: "#6a1b9a" }));
    const series = [
      { points: tl.map((r) => [r.t, r.current_map ?? NaN]), colour: "#37474f", width: 1.3 },
      { points: tl.map((r) => [r.t, r.forecast_map ?? NaN]), colour: "#6a1b9a", width: 1.8 },
    ];
    if (tl.some((r) => r.truth_future_map !== null)) {
      series.push({ points: tl.map((r) => [r.t, r.truth_future_map ?? NaN]), colour: "#e65100", width: 1.2, dash: [6, 4] });
    }
    drawChart(root.querySelector("#rv-map"), { height: 230, xMin: t0, xMax: t1, series, spans, vlines, xFmt: fmt.time, yLabel: "MAP mmHg",
      hlines: [{ y: th.target, label: `${th.target} target`, colour: "#b26a00" }, { y: th.critical, label: `${th.critical} critical`, colour: "#c62828" }] });
    drawChart(root.querySelector("#rv-r"), { height: 140, xMin: t0, xMax: t1, vlines, xFmt: fmt.time, yLabel: "R",
      series: [{ points: tl.map((r) => [r.t, r.r ?? NaN]), colour: "#00695c", width: 1.6 }] });
    drawChart(root.querySelector("#rv-c"), { height: 140, xMin: t0, xMax: t1, vlines, xFmt: fmt.time, yLabel: "C",
      series: [{ points: tl.map((r) => [r.t, r.c ?? NaN]), colour: "#ad1457", width: 1.6 }] });
  }

  function render() {
    const s = data.summary;
    const recId = recSelect.value;
    const kpi = (k, v) => el("div", { class: "kpi" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v));
    const episodes = data.episodes.length
      ? data.episodes.map((e) => el("tr", {}, el("td", {}, fmt.time(e.start)), el("td", {}, fmt.time(e.end)),
          el("td", { class: "num" }, `${fmt.num(e.duration_sec)} s`),
          el("td", {}, el("span", { class: "lvl-dot", style: `background:${LEVEL[e.peak_level].colour}` }), e.peak_level),
          el("td", { class: "num" }, fmt.num(e.min_forecast_map))))
      : [el("tr", {}, el("td", { colspan: 5, class: "muted" }, "No alert episodes."))];
    const ivs = data.interventions.length
      ? data.interventions.map((iv) => el("tr", {}, el("td", {}, fmt.time(iv.onset_t)), el("td", {}, iv.label),
          el("td", {}, el("span", { class: `status ${iv.status.replaceAll(" ", "-")}` }, iv.status)),
          el("td", { class: "num" }, iv.d_map === null ? "—" : `${iv.d_map > 0 ? "+" : ""}${fmt.num(iv.d_map)} mmHg`),
          el("td", { class: "num" }, iv.r_change_pct === null ? "—" : `${iv.r_change_pct > 0 ? "+" : ""}${fmt.num(iv.r_change_pct)}%`),
          el("td", {}, iv.confounded_with?.length ? "overlapping" : "")))
      : [el("tr", {}, el("td", { colspan: 6, class: "muted" }, "No interventions recorded."))];
    const v = data.verification;
    out.replaceChildren(el("div", { class: "review-grid" },
      el("div", { class: "stack" },
        el("section", { class: "card" }, el("h3", {}, "Handoff summary"),
          el("ul", { class: "narrative" }, data.narrative.map((line) => el("li", {}, line)))),
        el("section", { class: "card" }, el("h3", {}, "Time in each state"), bar(s)),
        el("section", { class: "card chart-card" }, el("h3", {}, "Episode timeline"),
          el("canvas", { id: "rv-map", "aria-label": "MAP timeline with alert levels" }),
          el("div", { class: "legend" },
            el("span", {}, el("i", { style: "background:#37474f" }), "Measured MAP"),
            el("span", {}, el("i", { style: "background:#6a1b9a" }), "15 s forecast"),
            el("span", { style: "color:#e65100" }, el("i", { class: "dash" }), "True future MAP"),
            el("span", {}, "Background = alert level · purple lines = interventions")),
          el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px" },
            el("canvas", { id: "rv-r", "aria-label": "Resistance trend" }), el("canvas", { id: "rv-c", "aria-label": "Compliance trend" }))),
        el("section", { class: "card" }, el("h3", {}, "Alert episodes (WATCH or higher)"),
          el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {}, ["Start", "End", "Duration", "Peak level", "Lowest forecast MAP"].map((h) => el("th", {}, h)))),
            el("tbody", {}, episodes)))),
        el("section", { class: "card" }, el("h3", {}, "Interventions and response"),
          el("div", { class: "table-wrap" }, el("table", {},
            el("thead", {}, el("tr", {}, ["Onset", "Event", "Response", "Δ MAP", "Δ R", "Note"].map((h) => el("th", {}, h)))),
            el("tbody", {}, ivs))))),
      el("div", { class: "stack" },
        el("section", { class: "card" }, el("h3", {}, "Episode at a glance"),
          el("div", { class: "kpis" },
            kpi("Monitored", fmt.time(s.monitored_sec)), kpi("Alert episodes", String(s.alert_episodes)),
            kpi("Lowest fMAP", `${fmt.num(s.min_forecast_map)}`), kpi("Interventions", String(data.interventions.length)),
            kpi("Withheld", `${fmt.num(s.abstention_pct)}%`), kpi("Batch time", `${fmt.num(s.median_latency_ms, 1)} ms/win`))),
        el("section", { class: "card" }, el("h3", {}, "Export for handoff / audit"),
          el("p", { class: "small muted", style: "margin-top:0" }, "Self-contained report with charts; prints to PDF from the browser."),
          el("div", { class: "exports" },
            el("a", { class: "btn primary", href: href(`/api/review/${recId}/report.html`), target: "_blank", rel: "noopener" }, "Open printable report"),
            el("a", { class: "btn", href: href(`/api/review/${recId}/timeline.csv`), download: `timeline_${recId}.csv` }, "Timeline CSV"),
            el("a", { class: "btn", href: href(`/api/review/${recId}/review.json`), download: `review_${recId}.json` }, "Full JSON"))),
        v && v.windows ? el("section", { class: "card" }, el("h3", {}, "Forecast check"),
          el("p", { class: "small", style: "margin:0" },
            `Against ${v.truth_kind}: MAE ${fmt.num(v.map_mae, 1)} mmHg, bias ${v.map_bias > 0 ? "+" : ""}${fmt.num(v.map_bias, 1)} mmHg over ${v.windows} windows` +
            (v.r_correlation !== undefined ? `; R correlation ${fmt.num(v.r_correlation, 2)}, C correlation ${fmt.num(v.c_correlation, 2)}` : "") + ".")) : null,
        el("section", { class: "card" }, el("h3", {}, "Role of this concept"),
          el("p", { class: "small", style: "margin:0" },
            "Strong for documentation, handoff, quality review and research because it preserves the whole timeline. ",
            "It cannot protect a patient in real time: deterioration is only seen after the fact.")))));
    draw();
  }

  async function run() {
    runBtn.disabled = true; status.textContent = "Processing the full episode…";
    const t = performance.now();
    try {
      data = await api(`/api/review/${recSelect.value}`);
      status.textContent = `Done in ${((performance.now() - t) / 1000).toFixed(1)} s · ${data.timeline.length} windows`;
      render();
    } catch (e) { status.textContent = e.message; }
    finally { runBtn.disabled = false; }
  }

  runBtn.addEventListener("click", run);
  recSelect.addEventListener("change", run);
  const onResize = () => draw();
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
