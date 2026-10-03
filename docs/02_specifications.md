# 02 Needs and engineering specifications

*BMED Chapter 4: turning needs into measurable design inputs (Tables 4.1–4.4).*

The single source of truth is `evaluation/specifications.json`. The verifier reads that same file, so the targets in this document and the pass/fail results in doc 07 cannot drift apart.

## 1. Needs (BMED Table 4.2 format)

| Need no. | Need (stakeholder voice) | Source | Importance |
|----------|--------------------------|--------|------------|
| N1 | "Warn me before the pressure drops, not after." | Concept brief; clinicians | 5 |
| N2 | "Don't cry wolf." | Nurses; alarm-fatigue literature | 5 |
| N3 | "Show me why you are alerting." | Clinicians; FDA CDS guidance | 4 |
| N4 | "Tell me whether my treatment is working." | Concept 3 brief | 4 |
| N5 | "Never act on a bad signal." | Clinical engineering; IEC 60601-1-8 | 5 |
| N6 | "Must work on real patients, not just simulations." | Design team; regulators | 5 |
| N7 | "Must run on the bedside computer in real time." | Clinical engineering | 4 |
| N8 | "Work without an arterial line too." | Concept 4 brief | 3 |
| N9 | "Give me a record for handoff and audit." | Quality officers; Concept 5 brief | 3 |
| N10 | "Support my decision; never make it for me." | Clinicians; ethics; FDA | 5 |
| N11 | "Never tell me things are fine when they are not." | Patients; ISO 14971 (added after hazard H-09) | 5 |
| N12 | "Show me the state of the circulation (R, C)." | Project objective | 2 |

## 2. Engineering specifications (BMED Table 4.3 format)

Priority: **\*** = must be met; **\*\*** = trade-off acceptable (BMED convention). *Ideal* is the target. *Marginal* is the least acceptable value.

| Metric no. | Need | Pri. | Metric | Units | Ideal | Marginal | Source of target |
|------------|------|------|--------|-------|-------|----------|------------------|
| SPEC-01 | N6 | \* | MAE of 15 s forecast MAP (held-out simulation) | mmHg | ≤ 5 | ≤ 10 | ISO 81060-2 (mean error within 5 mmHg) |
| SPEC-02 | N1 | \* | Sensitivity of DETERIORATING+ alert for true future MAP < 65 | fraction | ≥ 0.90 | ≥ 0.80 | HPI ≈ 0.88 (Hatib 2018) |
| SPEC-03 | N2 | \*\* | Specificity of DETERIORATING+ alert | fraction | ≥ 0.90 | ≥ 0.80 | HPI ≈ 0.87 (Hatib 2018) |
| SPEC-04 | N2 | \*\* | False alarm episodes per patient-hour (any level) | /h | ≤ 1 | ≤ 3 | Sendelbach & Funk 2013; NPSG.06.01.01 |
| SPEC-05 | N1 | \* | Median paired lead over a conventional monitor with identical alarm management | s | ≥ 10 | ≥ 0 | Concept brief |
| SPEC-06 | N1 | \* | AUROC of P(MAP < 65) | AUROC | ≥ 0.90 | ≥ 0.80 | Prediction-model convention |
| SPEC-07 | N5 | \* | Corrupted windows handled safely (abstain or drop channel) | fraction | 1.00 | ≥ 0.95 | ISO 14971; IEC 60601-1-8 |
| SPEC-08 | N12 | \*\* | Within-patient correlation of R and C with ground truth (lower of the two) | r | ≥ 0.80 | ≥ 0.50 | Project objective |
| SPEC-09 | N4 | \*\* | Intervention-response agreement with ground truth | fraction | ≥ 0.90 | ≥ 0.75 | Concept 3 brief |
| SPEC-10 | N7 | \* | 95th-percentile CPU latency per update | ms | ≤ 100 | ≤ 1000 | 1 s update period; IEC 62304 |
| SPEC-11 | N6 | \* | MAE of forecast MAP vs arterial line (held-out VitalDB) | mmHg | ≤ 10 | ≤ 15 | External validity |
| SPEC-12 | N3 | \* | Nielsen heuristics met | of 10 | 10 | ≥ 8 | BMED 8.2.5.2; IEC 62366-1 |
| SPEC-13 | N9 | \*\* | Exportable episode report (print + data) | yes/no | yes | yes | Concept 5 brief |
| SPEC-14 | N10 | \* | Outputs containing treatment recommendations | count | 0 | 0 | FDA CDS guidance 2022; ethics |
| SPEC-15 | N11 | \* | Share of true MAP < 55 time shown only as STABLE or WATCH | fraction | 0 | ≤ 0.05 | Hazard H-09; fail-safe principle |

### Notes on how the metrics are defined

- **The event** is the true MAP over the next 15 s falling below 65 mmHg. Alerts are evaluated at every 1 s update, the rate used at the bedside, so the alarm-persistence logic behaves exactly as deployed.
- **The datum, or "conventional monitor",** is a threshold alarm on the *measured* MAP. It goes through the **same** alert state machine (persistence, hysteresis), so the lead-time comparison in SPEC-05 is like-for-like. An earlier version of the verifier compared against a datum with no persistence; that comparison was unfair and was replaced (doc 07 §2).
- **SPEC-04 counts every level, including WATCH.** Counting only DETERIORATING and above would hide a constant low-priority alert. That trap was found during verification and closed.
- **SPEC-15 was added during the design.** Verification found hazard H-09 (doc 06). Design is iterative: a hazard found in testing becomes a new design input (BMED Ch. 8.9 risk control, re-evaluated in Ch. 9).

## 3. Check against requirement characteristics (BMED Table 4.1)

| Characteristic | How the set satisfies it |
|----------------|--------------------------|
| Complete | Every need N1–N12 maps to at least one specification; N8 is scored in selection criterion C6 |
| Measurable | Each specification has a unit, a test method (doc 07) and numeric ideal and marginal values |
| Feasible | Marginal values were set from published devices (HPI) and standards, not aspirations |
| Unambiguous | Event definition, update rate and datum are fixed in code and stated above |
| Non-conflicting | SPEC-02 vs SPEC-03/04 conflict by nature (sensitivity vs alarm burden); SPEC-03/04 are marked ** (trade-off acceptable), and the conflict appears in the House of Quality roof (doc 04) |
| Traceable | Need → SPEC → test → result in `spec_verification.csv` |

## 4. Competitive benchmarking (BMED Table 4.4 format)

| Product / approach | Inputs | Prediction | Reported performance | Treatment response | Notes |
|--------------------|--------|------------|----------------------|--------------------|-------|
| Conventional threshold alarm (datum) | Arterial line | None (reactive) | Our measurement, real data: sensitivity 0.95, specificity 0.92 for "MAP < 65 in the next 15 s" | No | Cleared, universal; a 15 s window is short, so "reactive" still scores well |
| Persistence ("pressure stays as it is") | Arterial line | Current MAP carried forward | Our measurement: MAE 2.1 mmHg simulated, 2.6 real over 15 s | No | Hard-to-beat baseline at a 15 s horizon (doc 07 §3) |
| Hypotension Prediction Index (Edwards) | Arterial line (or FloTrac / ClearSight) | Probability of MAP < 65 within **5–15 min** | Sensitivity 0.88, specificity 0.87 at 15 min (Hatib et al., *Anesthesiology* 2018) | No | FDA De Novo DEN160044 (21 CFR 870.2210); proprietary hardware |
| **Concept A** | ABP + ECG + PPG + events | MAP forecast at **15 s** | Real data: sensitivity 0.96, specificity 0.87, AUROC 0.96, MAE 4.7 mmHg | Yes (96% agreement) | This project |
| **Concept B** | ECG + PPG | MAP estimate at 15 s | Real data: AUROC 0.59, MAE 12.6 mmHg | No | This project |

**Honest reading of the benchmark.** HPI predicts minutes ahead; Version 5 predicts 15 s ahead. SPEC-02 and SPEC-03 borrow HPI's numbers as a quality bar, but meeting them at 15 s is a much easier task than meeting them at 15 min. The horizon, not the alert logic, is the main gap to commercial practice. It is listed as the first design iteration in doc 04 §9.
