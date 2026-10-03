"""
Unit-level verification of the safety-relevant engine logic (IEC 62304 §5.5;
risk controls in docs/06). Fast: no model or data needed except the checksum test.

    python -m pytest tests -q        or        python tests/test_engine.py
"""

import os
import sys

import numpy as np

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
from app import markdown_lite  # noqa: E402
from app.engine import config  # noqa: E402
from app.engine.alerting import AlertStateMachine, raw_level  # noqa: E402
from app.engine.intervention import classify_response  # noqa: E402
from app.engine.signal_checks import abp_artifact_free  # noqa: E402

FS = 125.0


def run(sm, steps, **kw):
    out = None
    for i in range(steps):
        out = sm.update(float(i), **kw)
    return out


def test_levels_follow_thresholds():
    assert raw_level(80, 80) == 0
    assert raw_level(68, 70) == 1     # below watch threshold
    assert raw_level(62, 66) == 2     # below 65 target
    assert raw_level(50, 60) == 3     # below 55 critical


def test_escalation_needs_persistence():
    sm = AlertStateMachine()
    a = sm.update(0, 50, 60, True, 0.99, "High")
    assert a["level"] == 0            # one update is not enough
    a = sm.update(1, 50, 60, True, 0.99, "High")
    assert a["level"] == 3


def test_confidence_cap_limits_low_confidence():
    sm = AlertStateMachine()
    a = run(sm, 5, forecast_map=40, current_map=None, usable=True, p_below=0.99, confidence="Low")
    assert a["level"] == 1 and a["level_name"] == "WATCH"


def test_rc10_measured_floor_overrides_optimistic_forecast():
    sm = AlertStateMachine()
    # Forecast says 75 (stable) but the arterial line measures 50 mmHg.
    a = run(sm, 4, forecast_map=75, current_map=50, usable=True, p_below=0.1, confidence="Moderate", measured_map=50)
    assert a["level"] == 3


def test_rc08_hold_blocks_deescalation_on_weaker_evidence():
    sm = AlertStateMachine()
    run(sm, 3, forecast_map=50, current_map=50, usable=True, p_below=0.99, confidence="High", measured_map=50)
    assert sm.level == 3
    # Arterial line lost: low-confidence estimate says 80. The alarm must hold.
    a = run(sm, 10, forecast_map=80, current_map=None, usable=True, p_below=0.0, confidence="Low")
    assert a["level"] == 3 and a["held"] is True


def test_deescalation_with_equal_evidence_is_allowed():
    sm = AlertStateMachine()
    run(sm, 3, forecast_map=50, current_map=50, usable=True, p_below=0.99, confidence="High", measured_map=50)
    a = run(sm, 6, forecast_map=85, current_map=85, usable=True, p_below=0.0, confidence="High", measured_map=85)
    assert a["level"] == 0


def test_unusable_signal_is_technical_alarm_only():
    sm = AlertStateMachine()
    a = sm.update(0, None, None, False)
    assert a["level_name"] == "SIGNAL CHECK" and a["technical"] and a["level"] is None


def _abp(mean, pp, seconds=10):
    t = np.arange(int(seconds * FS)) / FS
    return mean + 0.5 * pp * np.sin(2 * np.pi * 1.3 * t)


def test_rc09_keeps_genuine_shock_pressure():
    ok, why = abp_artifact_free(_abp(38, 18), FS)   # MAP 38: Version 5 would reject this
    assert ok, why


def test_rc09_rejects_artifacts():
    flat = np.full(int(10 * FS), 60.0)
    assert not abp_artifact_free(flat, FS)[0]                       # clamped / flat line
    flush = _abp(80, 40); flush[600:700] = 300.0
    assert not abp_artifact_free(flush, FS)[0]                      # flush spike
    assert not abp_artifact_free(_abp(80, 2), FS)[0]                # over-damped
    assert not abp_artifact_free(None, FS)[0]


def test_intervention_classification():
    assert classify_response("vasopressor", 12.0, 0.2) != classify_response("vasopressor", 0.5, 0.0)


def test_markdown_escapes_html():
    out = markdown_lite.render("<script>alert(1)</script> [x](javascript:alert(1))")
    assert "<script>" not in out and "javascript:" not in out


def test_model_checksum_constant_matches_verified_file():
    import hashlib
    path = os.path.join(APP_ROOT, "data", "models", "physics-informed_real_finetuned.pth")
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert h == config.EXPECTED_MODEL_SHA256


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(tests)} tests passed")
