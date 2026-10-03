# Hemodynamic Concepts App

One web app that deploys the **Version 5 physics-informed hemodynamic model** in three ways. All three were designed, prototyped, verified and compared with the methods of *Biomedical Engineering Design* (Tranquillo, Goldberg & Allen, 2023).

| Concept | Combines briefs | What it is |
|---------|-----------------|------------|
| **A: Integrated Monitor** ★ selected | 1 Deterioration alert + 2 Forecast dashboard + 3 Intervention response | Real-time alert with reasons, 15 s MAP forecast with uncertainty, R/C trends and treatment response (arterial line + ECG + PPG) |
| **B: Non-Invasive Early Warning** | 4 | Risk estimate from ECG + PPG only, confidence-capped |
| **C: Episode Review** | 5 | Retrospective handoff/audit review with printable report, CSV and JSON |

**Decision:** Concept A, with C's report folded in as its documentation module; B deferred. See [docs/04_concept_selection.md](docs/04_concept_selection.md) for the full engineering rationale.

> Research prototype, decision support only. It is not a diagnostic device and never recommends treatment. Demo data are held-out simulated patients and de-identified VitalDB cases.

## Run the app

```bash
scripts/run_app.sh                 # http://127.0.0.1:8765  (options: --port 9000 --device cpu)
```

The app uses the Class-Project virtual environment by default (`PYTHON=... scripts/run_app.sh` to override). It needs the packages in `requirements.txt`. The model checksum is verified at start-up, and the server refuses to start with unverified weights.

## Public demo (static build, e.g. Vercel)

PyTorch and the server's in-memory sessions cannot run on Vercel's serverless platform. So the public site is a **static build**: `scripts/build_static.py` runs the verified model once on every demo recording and every start position, using the same pipeline as the live server, and writes `site/`. In the browser, `app/static/js/static-api.js` answers the app's API calls from those files, so the pages, alerts, forecasts, reviews, evidence and design docs are identical to the live app.

```bash
python scripts/build_static.py     # rebuild site/ after any change to the model, engine, UI or docs
```

On Vercel, import the GitHub repo and set **Root Directory = `site`**, **Framework Preset = Other**, with no build command. The only difference from the live app is that interventions cannot be hand-marked; the recorded pump/EHR events still drive the response tracker.

## Pages

- **Overview:** the three concepts, scores and the selection.
- **Integrated Monitor / Non-Invasive:** pick a patient, press Play (Space), step (→), speed 1–20×. Tick *Verification overlay* to see the true future MAP (not available at the bedside).
- **Episode Review:** pick an episode, then *Generate review*, then open the printable report or download CSV/JSON.
- **Evidence:** the live verification tables.
- **Design File:** the Design History File (docs 00–09), readable in the app.

## Reproduce every result

```bash
scripts/run_verification.sh        # unit tests, verification, static checks, selection, figures (~15 min, GPU)
scripts/run_app.sh --port 8765 &   # then:
python scripts/capture_screenshots.py   # UI test (no JS errors, no mobile overflow) + storyboard screenshots
```

`VERSION5` must point at the Version 5 project (simulated cohort and VitalDB cases) when re-running verification. Default: `/home/kyei/Document/Version 5`.

## Layout

```
app/            server.py (stdlib HTTP), engine/ (model service, alerts, risk controls, sessions, review), static/ (UI)
src/            Version 5 code, vendored unchanged
data/models/    Version 5 checkpoints + metadata (SHA-256 verified)
demo_data/      held-out demo recordings (manifest.json)
evaluation/     specifications.json, fmea.json, heuristic_evaluation.json, verifier scripts, results/
docs/           Design History File 00-09 + figures/
scripts/        run_app.sh, run_verification.sh, make_figures.py, capture_screenshots.py, build_demo_data.py
tests/          unit tests of the safety-relevant logic
```

## Headline results (held-out data)

| | Concept A | Concept B | Current practice (threshold alarm) |
|---|---|---|---|
| Real-patient forecast MAE | **4.7 mmHg** | 12.6 mmHg | 2.6 mmHg (persistence) |
| Real-patient AUROC, P(MAP < 65) | **0.96** | 0.59 | – |
| Real sensitivity / specificity (DETERIORATING+) | 0.96 / 0.87 | 0.00 / 1.00 | 0.95 / 0.92 |
| Under-alert during true MAP < 55 (simulated) | **0.3%** (Version 5 alone: 53%) | 100% | 0.3% |
| Signal faults handled | 100% (Version 5 alone: 85%) | 100% | – |
| Must-meet specifications passed | **10 / 10** | 6 / 10 | – |

The honest limits are in docs 04 §9 and 07: the lead time is only seconds at a 15 s horizon, persistence is more accurate on average, alarm burden is above target, and R/C tracking was weakened by fine-tuning.
