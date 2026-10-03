# 09 Slide deck outline

*BMED Chapter 14 (communicating your design). One slide per row: suggested title, the point to make, the figure to show, and the numbers to quote. All figures are in `docs/figures/`; all numbers come from `evaluation/results/`.*

**Suggested length:** 18 slides, about 15–18 minutes. Slides marked ★ carry the argument; the rest can be trimmed.

| # | Slide title | Key message (say this) | Visual | Numbers to quote |
|---|-------------|------------------------|--------|------------------|
| 1 | Hemodynamic decision support: choosing how to deploy a physics-informed model | One model, five ideas, three concepts, one choice backed by evidence | `ui02_concept_a.png` (cropped banner + forecast) | – |
| 2 ★ | The problem | Monitors alarm *after* hypotension; most alarms are noise | Short text + one citation each | MAP < 55 linked to AKI / myocardial injury (Walsh 2013); 72–99% alarms non-actionable (Sendelbach & Funk 2013) |
| 3 | Project statement | Technology push from Version 5; the MadLib (doc 01 §3) | MadLib text, needs and barriers highlighted | – |
| 4 | Stakeholders | Nurses carry the alarm burden; physicians decide; FDA gates | Power and influence table (doc 01 §5) | – |
| 5 ★ | Specifications | 15 measurable design inputs; must-meet vs trade-off | Table 4.3-style excerpt (doc 02 §2): SPEC-01, 02, 04, 05, 07, 11, 15 | Ideal / marginal values |
| 6 | Benchmarking | HPI predicts minutes ahead; we predict 15 s; persistence is a hard baseline | Benchmark table (doc 02 §4) | HPI 0.88 / 0.87; persistence 2.6 mmHg real |
| 7 ★ | From 5 briefs to 3 concepts | 1 + 2 + 3 share user, moment and data → merge; 4 and 5 differ in setting / time | `fig01_concept_confluence.png` | – |
| 8 | The prototype: one app, three concepts | Shared engine = fair comparison | `fig02_architecture.png` + `ui01_overview.png` | CPU p95 175 ms |
| 9 | Concept A in action | Explained alerts, forecast band, treatment response | `ui04_concept_a_intervention.png` | Vasopressor: MAP +30 mmHg, "improving" |
| 10 ★ | Storyboard: same patient, A vs B | A escalates WATCH → DETERIORATING → CRITICAL; B stays STABLE at ~74 while true MAP is 49 | `fig11_example_case.png` | A: DETERIORATING at 00:59 at MAP 65.9, 1 s before the threshold alarm |
| 11 ★ | Verification: who meets the specifications? | Only A passes every must-meet specification | `fig03_spec_scorecard.png` | A real: sensitivity 0.96, specificity 0.87, AUROC 0.96, MAE 4.7 mmHg; B real AUROC 0.59 |
| 12 ★ | The hazard we found (H-09) | Version 5 discarded real pressure in deep shock, so less information meant more reassurance; layered controls fixed it | `fig07_hazard_h09_layers.png` | Under-alert 53% → 0.18%; unsafe de-escalations 41 → 0 |
| 13 | Signal-quality gate | Version 5 forecast "normal" on 40% PPG drop-out; RC-07 fixed it | `fig08_quality_gate.png` | 24% → 100% handled; genuine shock ABP kept 52% → 95% |
| 14 | Risk management | 6 unacceptable risks → 1 (Concept B's false reassurance) | `fig12_fmea_risk_chart.png` | 13 risks; R-04 stays unacceptable |
| 15 ★ | Concept selection | Gate → matrix → DSM with rotated datum → House of Quality → sensitivity | `fig04_decision_matrix.png` + `fig05_dsm_rotated_datum.png` | A 3.95 (HoQ 4.15), C 3.60, B 2.00, current practice 3.70 |
| 16 ★ | Is the decision robust? (be honest) | C wins under equal weights and 66% of random weightings, but cannot detect in real time (gate); A wins whenever detection carries ≥ 9.5% weight | `fig06_weight_sensitivity.png` | Break-even 9.5% vs our 20% |
| 17 ★ | Decision and next iteration | Select A + C's report; defer B. Next: longer horizon, quieter WATCH, R/C-preserving training, usability study | Summary bullets | Persistence beats the model on average (2.1 vs 4.3 mmHg) but not during change (8.5 vs 10.0) |
| 18 | Validation plan and limitations | Verified, not yet validated; plan V2–V6 | Table from doc 07 §8 | 30 real cases, single centre |

## Lines for the selection slide (slide 15–17)

> "We chose Concept A because it is the only concept that meets every must-meet specification. It discriminates hypotension well on unseen real patients (AUROC 0.96, 4.7 mmHg error), it is fail-safe by design (it can never be less safe than the existing monitor), and it is the only concept that tells clinicians whether their treatment is working. Concept B looked attractive for lower-resource settings, but it missed every critical-hypotension period in testing; that risk is unacceptable under ISO 14971. Concept C scores well on documentation and low risk, so we kept its report as a module of A rather than as a separate product. A's honest weakness is lead time: at a 15-second horizon it is only seconds ahead of a threshold alarm, so extending the horizon is our next design iteration."

## Questions to prepare for

| Likely question | Answer and evidence |
|-----------------|---------------------|
| "Why not just use the current MAP?" | At 15 s, persistence *is* more accurate on average (2.1 vs 4.3 mmHg). The model's value is during change (8.5 vs 10.0) and in discrimination plus explanation. Hence the horizon recommendation (doc 07 §4) |
| "Your weights decided the outcome." | The gate decides before the weights. Under single pessimistic re-scorings A still wins; C wins only if detection gets < 9.5% weight (doc 04 §8) |
| "Is this better than HPI?" | It is not comparable yet: HPI forecasts 5–15 min ahead. Our contribution is the fail-safe alert layer and treatment-response view (doc 02 §4) |
| "Why does A alarm so much?" | WATCH-level burden is 12.6/h on real data vs 5.5 for the monitor; planned fix is WATCH as a non-alarming information signal (FMEA R-05) |
| "Is the R/C output trustworthy?" | No, not after fine-tuning (r = 0.54 / 0.03); shown as trends only, not used for alerts (doc 07 §6) |
| "Were the test patients in training?" | No. Held-out test splits only; calibration on validation splits only (doc 07 §1) |
