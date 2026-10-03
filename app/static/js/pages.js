// Overview, Evidence and Design History File pages.
import { api, el, fmt } from "./api.js";

const resultClass = (r) => (!r || r === "n/a" ? "na" : r.startsWith("PASS (ideal)") ? "pass" : r.startsWith("PASS") ? "marg" : "fail");

export async function mountOverview(root, meta) {
  root.replaceChildren(el("div", { class: "loading" }, "Loading…"));
  const [ver, sel] = await Promise.all([api("/api/verification").catch(() => null), api("/api/selection").catch(() => null)]);
  const haveVer = ver && !ver.error;
  const winner = sel && !sel.error ? sel.selected : meta.recommended;
  const syn = haveVer ? ver.synthetic_test : null;
  const vit = haveVer ? ver.vitaldb_test : null;

  const kpis = {
    A: syn ? [["AUROC", fmt.num(syn.A.alerts.auroc, 2), syn.A.alerts.auroc >= 0.8],
              ["Real MAE", `${fmt.num(vit.A.accuracy_calibrated.mae, 1)}`, vit.A.accuracy_calibrated.mae <= 15],
              ["Tx agree", fmt.pct(syn.A.intervention_agreement_unconfounded), syn.A.intervention_agreement_unconfounded >= 0.75]] : [],
    B: syn ? [["AUROC", fmt.num(syn.B.alerts.auroc, 2), syn.B.alerts.auroc >= 0.8],
              ["Real MAE", `${fmt.num(vit.B.accuracy_calibrated.mae, 1)}`, vit.B.accuracy_calibrated.mae <= 15],
              ["Spec. (watch)", fmt.num(syn.B.alerts.specificity_watch, 2), syn.B.alerts.specificity_watch >= 0.8]] : [],
    C: syn ? [["Real-time alerts", "None", false],
              ["Real MAE", `${fmt.num(vit.A.accuracy_calibrated.mae, 1)}`, vit.A.accuracy_calibrated.mae <= 15],
              ["Batch speed", `${fmt.num(ver.performance.concept_c_seconds_per_monitored_hour, 0)} s/h`, true]] : [],
  };
  const totals = sel && !sel.error ? sel.weighted_totals : null;

  const cards = ["A", "B", "C"].map((c) => {
    const info = meta.concepts[c];
    return el("article", { class: `card concept-card ${c === winner ? "recommended" : ""}` },
      c === winner ? el("span", { class: "ribbon" }, "SELECTED CONCEPT") : null,
      el("div", { style: "display:flex;gap:10px;align-items:center" }, el("div", { class: "letter" }, c), el("h2", {}, info.name)),
      el("div", { class: "chips" }, info.combines.map((x) => el("span", { class: "chip strong" }, x))),
      el("dl", {}, el("dt", {}, "Inputs"), el("dd", {}, info.inputs), el("dt", {}, "Setting"), el("dd", {}, info.setting),
        el("dt", {}, "Mode"), el("dd", {}, info.mode),
        totals ? el("dt", {}, "Decision score") : null, totals ? el("dd", {}, el("b", {}, `${fmt.num(totals[c], 2)} / 5.00`), " weighted") : null),
      el("div", { class: "spacer" }),
      kpis[c].length ? el("div", { class: "kpis" }, kpis[c].map(([k, v, ok]) =>
        el("div", { class: "kpi" }, el("div", { class: "k" }, k), el("div", { class: `v ${ok ? "good" : "bad"}` }, v)))) : null,
      el("a", { class: "btn primary", href: `#/concept-${c.toLowerCase()}` }, `Open Concept ${c} →`));
  });

  const why = sel && !sel.error ? sel.rationale : [];
  root.replaceChildren(
    el("section", { class: "hero" },
      el("div", { class: "card" },
        el("h1", {}, "Three deployment concepts for one physics-informed model"),
        el("p", {}, "The Version 5 model forecasts arterial pressure 15 s ahead from bedside-monitor waveforms and estimates ",
          "vascular resistance (R) and arterial compliance (C). Five app concepts were proposed; following the concept-generation ",
          "and selection methods of ", el("em", {}, "Biomedical Engineering Design"), " (Tranquillo, Goldberg & Allen, 2023), concepts 1, 2 and 3 ",
          "were merged into one, giving three concepts that are prototyped, verified and compared here."),
        el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, el("th", {}, "Original concepts"), el("th", {}, "Became"), el("th", {}, "Why"))),
          el("tbody", {},
            el("tr", {}, el("td", {}, "1 Deterioration alert · 2 Forecast dashboard · 3 Intervention response"), el("td", {}, el("b", {}, "Concept A")),
              el("td", {}, "Same users, setting and inputs; each covers a gap in the others (alerting, explanation, treatment feedback).")),
            el("tr", {}, el("td", {}, "4 Non-invasive early warning"), el("td", {}, el("b", {}, "Concept B")), el("td", {}, "Different setting and inputs (no arterial line).")),
            el("tr", {}, el("td", {}, "5 Decision-support review"), el("td", {}, el("b", {}, "Concept C")), el("td", {}, "Different mode: retrospective, not real time.")))))),
      el("div", { class: "card" },
        el("h3", {}, `Selected: Concept ${winner}`),
        why.length ? el("ul", { class: "narrative" }, why.slice(0, 3).map((w) => el("li", {}, w)))
          : el("p", { class: "muted" }, "Run scripts/run_verification.sh to populate the evidence."),
        why.length > 3 ? el("details", {}, el("summary", {}, `Robustness, safety and honest limits (${why.length - 3} more)`),
          el("ul", { class: "narrative" }, why.slice(3).map((w) => el("li", {}, w)))) : null,
        el("p", { class: "small" }, el("a", { href: "#/design/04_concept_selection" }, "Full selection method and matrices →"), " · ",
          el("a", { href: "#/evidence" }, "Verification evidence →")))),
    el("section", { class: "concepts" }, cards));
}

export async function mountEvidence(root) {
  root.replaceChildren(el("div", { class: "loading" }, "Loading verification evidence…"));
  const ver = await api("/api/verification").catch((e) => ({ error: e.message }));
  if (ver.error) {
    root.replaceChildren(el("div", { class: "card" }, el("h1", {}, "Evidence"), el("p", {}, ver.error)));
    return;
  }
  const syn = ver.synthetic_test, vit = ver.vitaldb_test, gate = ver.quality_gate;
  const specRows = ver.spec_verification.map((r) => el("tr", {},
    el("td", {}, el("b", {}, r.id)), el("td", {}, r.need), el("td", { class: "small" }, r.metric),
    el("td", { class: "num" }, String(r.ideal)), el("td", { class: "num" }, String(r.marginal)),
    ...["A", "B", "C"].map((c) => el("td", { class: resultClass(r[`result_${c}`]) },
      r[`measured_${c}`] === null || r[`measured_${c}`] === undefined ? "—" : String(r[`measured_${c}`]), el("br"),
      el("span", { class: "small" }, r[`result_${c}`])))));

  const row = (label, a, b, d, digits = 2, pct = false) => el("tr", {}, el("td", {}, label),
    ...[a, b, d].map((v) => el("td", { class: "num" }, v === undefined ? "n/a" : pct ? fmt.pct(v, 0) : fmt.num(v, digits))));
  const cmp = (src) => [
    row("Forecast MAE (mmHg)", src.A.accuracy?.mae ?? src.A.accuracy_calibrated?.mae, src.B.accuracy?.mae ?? src.B.accuracy_calibrated?.mae, undefined, 1),
    row("AUROC, P(MAP<65)", src.A.alerts.auroc, src.B.alerts.auroc, undefined),
    row("Sensitivity (DETERIORATING+)", src.A.alerts.sensitivity_deteriorating, src.B.alerts.sensitivity_deteriorating, src.DATUM.alerts.sensitivity_deteriorating, 2, true),
    row("Specificity (DETERIORATING+)", src.A.alerts.specificity_deteriorating, src.B.alerts.specificity_deteriorating, src.DATUM.alerts.specificity_deteriorating, 2, true),
    row("Specificity (WATCH+)", src.A.alerts.specificity_watch, src.B.alerts.specificity_watch, src.DATUM.alerts.specificity_watch, 2, true),
    row("Time alarming (WATCH+)", src.A.alerts.fraction_time_alarming_any, src.B.alerts.fraction_time_alarming_any, src.DATUM.alerts.fraction_time_alarming_any, 2, true),
    row("False alarms / patient-hour", src.A.alerts.false_alarms_per_hour_any, src.B.alerts.false_alarms_per_hour_any, src.DATUM.alerts.false_alarms_per_hour_any, 1),
    row("Lead vs datum, DETERIORATING (s)", src.A.lead_vs_datum?.deteriorating.median_advantage_s, src.B.lead_vs_datum?.deteriorating.median_advantage_s, 0, 1),
    row("Lead vs datum, WATCH (s)", src.A.lead_vs_datum?.watch.median_advantage_s, src.B.lead_vs_datum?.watch.median_advantage_s, 0, 1),
  ];
  const table = (rows) => el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Metric"), el("th", { class: "num" }, "Concept A"), el("th", { class: "num" }, "Concept B"),
      el("th", { class: "num" }, "Datum (threshold monitor)"))), el("tbody", {}, rows)));

  root.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Verification evidence"),
      el("div", { class: "sub" }, `Generated ${ver.generated} · ${ver.design.test_patients} held-out synthetic test patients · ` +
        `${vit.cases.length} held-out VitalDB cases · model sha256 ${ver.model.sha256.slice(0, 12)}…`))),
    el("section", { class: "card", style: "margin-bottom:12px" }, el("h3", {}, "Specification verification (design input = design output)"),
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["ID", "Need", "Metric", "Ideal", "Marginal", "A", "B", "C"].map((h) => el("th", {}, h)))),
        el("tbody", {}, specRows)))),
    el("div", { class: "review-grid" },
      el("div", { class: "stack" },
        el("section", { class: "card" }, el("h3", {}, "Held-out simulated patients"), table(cmp(syn))),
        el("section", { class: "card" }, el("h3", {}, "Held-out real patients (VitalDB, arterial line as reference)"), table(cmp(vit)))),
      el("div", { class: "stack" },
        el("section", { class: "card" }, el("h3", {}, "Safety: signal-quality gate"),
          el("p", { class: "small", style: "margin-top:0" }, "Corrupted windows handled safely (abstain or drop the bad channel):"),
          el("div", { class: "kpis" },
            el("div", { class: "kpi" }, el("div", { class: "k" }, "Version 5 alone"), el("div", { class: "v bad" }, fmt.pct(gate.before_rc07.all_corruptions_handled_fraction))),
            el("div", { class: "kpi" }, el("div", { class: "k" }, "With RC-07"), el("div", { class: "v good" }, fmt.pct(gate.all_corruptions_handled_fraction))),
            el("div", { class: "kpi" }, el("div", { class: "k" }, "Alerts on bad data"), el("div", { class: "v good" }, String(gate.physiological_alerts_from_abstained_windows)))),
          el("p", { class: "small muted" }, "Verification found Version 5 checks missing data after interpolation, so a 40% PPG dropout was forecast as normal. RC-07 checks the raw signal first.")),
        el("section", { class: "card" }, el("h3", {}, "Performance"),
          el("div", { class: "kpis" },
            el("div", { class: "kpi" }, el("div", { class: "k" }, "CPU p95"), el("div", { class: "v" }, `${fmt.num(ver.performance.latency_cpu_ms.p95)} ms`)),
            el("div", { class: "kpi" }, el("div", { class: "k" }, "GPU p95"), el("div", { class: "v" }, `${fmt.num(syn.latency_gpu_ms.p95, 1)} ms`)),
            el("div", { class: "kpi" }, el("div", { class: "k" }, "Review speed"), el("div", { class: "v" }, `${fmt.num(ver.performance.concept_c_seconds_per_monitored_hour, 0)} s/h`)))),
        el("section", { class: "card" }, el("h3", {}, "Physiological tracking (simulation)"),
          el("p", { class: "small", style: "margin:0" },
            `Within-patient correlation with ground truth - Concept A: R ${fmt.num(syn.A.r_within_patient_corr, 2)}, C ${fmt.num(syn.A.c_within_patient_corr, 2)}; `,
            `Concept B: R ${fmt.num(syn.B.r_within_patient_corr, 2)}, C ${fmt.num(syn.B.c_within_patient_corr, 2)}. `,
            "Intervention-response agreement (Concept A, unconfounded): ", el("b", {}, fmt.pct(syn.A.intervention_agreement_unconfounded)), "."))))
  );
}

export async function mountDesign(root, docName) {
  root.replaceChildren(el("div", { class: "loading" }, "Loading Design History File…"));
  const { docs } = await api("/api/docs");
  const current = docName && docs.some((d) => d.name === docName) ? docName : docs[0]?.name;
  const nav = el("nav", { class: "card doc-nav", "aria-label": "Design History File sections" },
    el("h3", {}, "Design History File"),
    docs.map((d) => el("a", { href: `#/design/${d.name}`, class: d.name === current ? "active" : "" }, d.title)));
  const body = el("article", { class: "card doc", id: "doc-body" }, el("div", { class: "loading" }, "Loading…"));
  root.replaceChildren(el("div", { class: "docs-layout" }, nav, body));
  if (!current) { body.replaceChildren(el("p", {}, "No documents found in docs/.")); return; }
  const doc = await api(`/api/docs/${current}`);
  // Server-rendered from our own Markdown with all text HTML-escaped (app/markdown_lite.py).
  body.innerHTML = doc.html;
  window.scrollTo(0, 0);
}
