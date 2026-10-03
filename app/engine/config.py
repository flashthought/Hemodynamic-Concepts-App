"""
Clinical thresholds and engineering constants shared by all three concepts.

Every value here is a design input (ISO 13485 / BME Design Ch. 4): it is
traceable to a source in docs/02_specifications.md. Changing a value here
changes the device's behaviour, so changes must be recorded in the Design
History File and re-verified with evaluation/evaluate_concepts.py.
"""

# --- Haemodynamic thresholds (mmHg) -----------------------------------------
# Surviving Sepsis Campaign 2021: initial target MAP >= 65 mmHg in septic shock.
MAP_TARGET = 65.0
# Intraoperative MAP < 55 mmHg is associated with acute kidney injury and
# myocardial injury (Walsh et al., Anesthesiology 2013;119:507-515).
MAP_CRITICAL = 55.0
# Early-warning buffer above the treatment target.
MAP_WATCH = 70.0
# A forecast fall of this size across the 15 s horizon raises WATCH even when
# the absolute forecast is still above MAP_WATCH (rapid-decline rule).
RAPID_DROP_MMHG = 10.0

# --- Alarm management (alarm-fatigue risk control, FMEA R-03) ----------------
# A level must be forecast on this many consecutive windows before the
# displayed alert escalates. Suppresses single-window transients.
ESCALATE_PERSIST = 2
# De-escalation needs the lower level on this many consecutive windows, AND the
# forecast must clear the threshold by HYSTERESIS_MMHG. Prevents alert chatter.
DEESCALATE_PERSIST = 3
HYSTERESIS_MMHG = 2.0

# --- Intervention response (Concept 3 / Concept A) ---------------------------
INTERVENTION_ON = 0.05          # normalised channel level counted as "running"
INTERVENTION_QUIET_SEC = 5.0    # channel must be off this long before an onset
RESPONSE_BASELINE_SEC = 30.0    # pre-intervention window for the baseline
RESPONSE_MIN_SEC = 30.0         # earliest assessment after onset
RESPONSE_WINDOW_SEC = 60.0      # assessment horizon after onset
RESPONSE_MAP_DELTA = 5.0        # mmHg change counted as a real MAP response
RESPONSE_R_FRACTION = 0.10      # 10 % rise in R counted as a vasopressor effect

# --- Model / streaming --------------------------------------------------------
WINDOW_SEC = 10.0               # model observation window
FORECAST_HORIZON_SEC = 15.0     # model forecast horizon (15 steps x 1 s)
STEP_SEC = 1.0                  # live update period

# Forecast RMSE per regime, used as the uncertainty (sigma) when converting a
# forecast MAP into P(MAP < target). These defaults are the Version 5 held-out
# synthetic results; evaluation/evaluate_concepts.py overwrites them with the
# deployed model's own measured values in evaluation/results/calibration.json.
DEFAULT_REGIME_SIGMA = {
    "ppg_only": 36.2,
    "ppg_ecg": 36.0,
    "ppg_ecg_abp": 10.5,
    "ppg_ecg_abp_intervention": 10.1,
}

# Confidence label for a regime, from its calibrated sigma (mmHg).
CONFIDENCE_BANDS = ((12.0, "High"), (20.0, "Moderate"))  # else "Low"


def confidence_for_sigma(sigma: float) -> str:
    for limit, label in CONFIDENCE_BANDS:
        if sigma <= limit:
            return label
    return "Low"

# Configuration control: the verified Version 5 checkpoint. Every result in
# evaluation/results/ was produced with this file; any other weights (including
# Version 5's silent fallback to the synthetic-only model) are refused.
EXPECTED_MODEL_SHA256 = "6fbec94f8b40570badaf491689e6f15d1451a243567dd53f1f3541c5bc3e31b4"
