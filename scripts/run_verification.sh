#!/usr/bin/env bash
# Regenerate every number and figure in the Design History File (about 15 min on GPU).
# Needs the Version 5 project (simulated cohort + VitalDB cases): set VERSION5 if it is not
# at the default location.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/kyei/Document/Class-Project/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export VERSION5="${VERSION5:-/home/kyei/Document/Version 5}"
"$PY" tests/test_engine.py                 # unit-level checks of the risk controls
"$PY" -u evaluation/evaluate_concepts.py --version5 "$VERSION5" | tee evaluation/results/evaluation_run.log
"$PY" evaluation/static_checks.py          # SPEC-12/13/14 (must run after evaluate_concepts.py)
"$PY" evaluation/concept_selection.py      # Chapter 6 selection -> concept_selection.json
"$PY" scripts/make_figures.py              # docs/figures/fig*.png
echo "Done. UI screenshots (docs/figures/ui*.png) are captured separately with the app running."
