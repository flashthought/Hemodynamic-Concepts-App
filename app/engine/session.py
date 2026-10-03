"""
Replay of a recording through the model, one monitor update at a time.

A ReplaySession is the software equivalent of a patient monitor feeding the
device: every step advances the clock by STEP_SEC, takes the most recent
WINDOW_SEC of the channels the concept is allowed to see, and runs the frozen
Version 5 predictor on it. The window is the same contiguous slice that
src/inference/streaming_buffer.StreamingWindowBuffer would hold at that moment
(verified in tests/test_engine.py).

Concept configuration - which channels each concept may use:
  A  integrated alert + dashboard + intervention response: ECG, PPG, ABP, intervention
  B  non-invasive early warning:                           ECG, PPG only
  C  retrospective review: replays with A's channels in batch (see review.py)
"""

import time
from typing import Dict, List, Optional

import numpy as np

from app.engine import config
from app.engine.alerting import AlertStateMachine, prob_below_target
from app.engine.intervention import InterventionTracker
from app.engine.recordings import Recording
from src.inference.schemas import MonitorWindow

CONCEPTS = {
    "A": {"abp": True, "intervention": True, "track_interventions": True},
    "B": {"abp": False, "intervention": False, "track_interventions": False},
}

CURRENT_MAP_SEC = 4.0  # displayed "current MAP" averages the last 4 s, like a monitor


def _finite_mean(x: np.ndarray, lo: float = 20.0, hi: float = 220.0) -> Optional[float]:
    arr = np.asarray(x, dtype=np.float64)
    arr = arr[np.isfinite(arr) & (arr > lo) & (arr < hi)]
    return float(np.mean(arr)) if arr.size >= 10 else None


def _decimate(x: np.ndarray, factor: int = 2) -> List[float]:
    arr = np.nan_to_num(np.asarray(x, dtype=np.float64)[::factor], nan=0.0)
    return [round(float(v), 3) for v in arr]


class ReplaySession:
    def __init__(self, recording: Recording, model, concept: str, start_sec: Optional[float] = None,
                 step_sec: float = config.STEP_SEC, calibrate: bool = True, risk_controls: bool = True):
        if concept not in CONCEPTS:
            raise ValueError(f"concept must be one of {sorted(CONCEPTS)}")
        self.rec = recording
        self.model = model
        self.concept = concept
        self.cfg = CONCEPTS[concept]
        self.step_sec = float(step_sec)
        self.calibrate = calibrate
        # Calibration is domain-specific (real patients vs simulator patients).
        self.domain = "synthetic" if recording.source == "synthetic" else "real"
        first = recording.first_window_end()
        self.t = max(first, float(start_sec)) if start_sec is not None else first
        self.t -= self.step_sec  # first step() lands on start
        # risk_controls=False reproduces Version 5 alerting without RC-08/RC-10,
        # used only by verification to measure hazard H-09.
        self.alerts = AlertStateMachine(fail_safe_hold=risk_controls, use_measured_floor=risk_controls)
        self.tracker = InterventionTracker() if self.cfg["track_interventions"] else None
        self.created = time.time()
        self.last_used = time.time()
        self.finished = False

    # -- helpers --------------------------------------------------------------
    def _idx(self, t: float) -> int:
        return int(round(t * self.rec.fs))

    def _window(self, t_end: float) -> MonitorWindow:
        i1 = self._idx(t_end)
        i0 = i1 - int(round(config.WINDOW_SEC * self.rec.fs))
        return MonitorWindow(
            fs=self.rec.fs,
            ppg=self.rec.ppg[i0:i1].tolist(),
            ecg=self.rec.ecg[i0:i1].tolist(),
            abp=self.rec.abp[i0:i1].tolist() if self.cfg["abp"] else None,
            intervention=self.rec.intervention[i0:i1].tolist() if self.cfg["intervention"] else None,
            intervention_scale=self.rec.intervention_scale,
            timestamp=f"{t_end:.1f}",
            patient_id=self.rec.rec_id,
        )

    def _u_now(self, t: float) -> float:
        """Clinician-readable intervention level now (held pump rate), for event detection."""
        held = self.rec.intervention_held
        if held is None:
            return 0.0
        i = min(self._idx(t), len(held)) - 1
        u = float(np.nan_to_num(held[max(i, 0)], nan=0.0))
        return min(max(u, 0.0), 1.0)

    def _truth(self, t: float) -> Dict[str, Optional[float]]:
        i1 = self._idx(t)
        ih = i1 + int(round(config.FORECAST_HORIZON_SEC * self.rec.fs))
        i0w = i1 - int(round(config.WINDOW_SEC * self.rec.fs))
        ic = i1 - int(round(CURRENT_MAP_SEC * self.rec.fs))
        out: Dict[str, Optional[float]] = {}
        if self.rec.has_truth:
            out["future_map"] = float(np.mean(self.rec.truth_abp[i1:ih]))
            out["current_map"] = float(np.mean(self.rec.truth_abp[ic:i1]))
            out["r"] = float(np.mean(self.rec.truth_r[i0w:i1]))
            out["c"] = float(np.mean(self.rec.truth_c[i0w:i1]))
            out["kind"] = "simulation ground truth"
        else:
            # Real data: the best available truth is the arterial line itself.
            out["future_map"] = _finite_mean(self.rec.abp[i1:ih])
            out["current_map"] = _finite_mean(self.rec.abp[ic:i1])
            out["kind"] = "measured arterial line"
        return out

    # -- public API -----------------------------------------------------------
    def add_manual_event(self, kind: str) -> Dict[str, object]:
        if self.tracker is None:
            raise ValueError("This concept does not track interventions.")
        label = {"vasopressor": "Vasopressor (clinician entry)", "fluid": "Fluid bolus (clinician entry)"}.get(
            kind, "Intervention (clinician entry)")
        return self.tracker.start(max(self.t, 0.0), kind, label, "manual")

    def step(self, include_waveforms: bool = True) -> Optional[Dict[str, object]]:
        if self.t + self.step_sec > self.rec.last_window_end() + 1e-9:
            self.finished = True
            return None
        self.t += self.step_sec
        self.last_used = time.time()
        t = self.t
        result = self.model.predict(self._window(t))
        usable = result.status in ("ok", "low_confidence") and result.forecast_map is not None
        channel_valid = result.metadata.get("channel_valid", {}) if result.metadata else {}

        current_map = None
        if self.cfg["abp"] and channel_valid.get("abp", False):
            i1 = self._idx(t)
            current_map = _finite_mean(self.rec.abp[i1 - int(CURRENT_MAP_SEC * self.rec.fs):i1])

        bias = self.model.bias(result.regime, self.domain) if (usable and self.calibrate) else 0.0
        forecast_map = (result.forecast_map - bias) if usable else None
        forecast_abp = [v - bias for v in result.forecast_abp] if usable else []
        sigma = self.model.sigma(result.regime, self.domain) if usable else None
        confidence = config.confidence_for_sigma(sigma) if sigma is not None else None
        p_below = prob_below_target(forecast_map, sigma) if usable else None
        alert = self.alerts.update(t, forecast_map, current_map, usable, p_below or 0.0, confidence or "Low",
                                   measured_map=current_map)

        intervention_changes = []
        if self.tracker is not None:
            # Documented events (pump/EHR feed) that occurred since the last update.
            for ev in self.rec.events:
                if t - self.step_sec < float(ev["t"]) <= t:
                    intervention_changes.append(self.tracker.start(float(ev["t"]), str(ev["type"]), str(ev["label"]), "recorded"))
            map_basis = current_map if current_map is not None else forecast_map
            intervention_changes += self.tracker.update(
                t, map_basis, result.r_estimate if usable else None, result.c_estimate if usable else None)

        tick: Dict[str, object] = {
            "t": round(t, 2),
            "concept": self.concept,
            "status": result.status,
            "quality": result.quality,
            "regime": result.regime,
            "warnings": result.warnings,
            "latency_ms": round(result.latency_ms, 2),
            "current_map": None if current_map is None else round(current_map, 1),
            "forecast_map": None if not usable else round(forecast_map, 1),
            "forecast_map_uncalibrated": None if not usable else round(result.forecast_map, 1),
            "bias_correction": round(bias, 2),
            "forecast_abp": [round(v, 1) for v in forecast_abp],
            "r": None if not usable else round(result.r_estimate, 4),
            "c": None if not usable else round(result.c_estimate, 4),
            "sigma": None if sigma is None else round(sigma, 2),
            "confidence": confidence,
            "p_below_target": None if p_below is None else round(p_below, 4),
            "risk_score": None if p_below is None else int(round(100 * p_below)),
            "alert": alert,
            "conventional_alarm": None if current_map is None else bool(current_map < config.MAP_TARGET),
            "u_now": round(self._u_now(t), 3) if self.cfg["intervention"] else None,
            "interventions": [dict(r) for r in self.tracker.records] if self.tracker else [],
            "intervention_changes": [dict(r) for r in intervention_changes],
            "channels_used": [c for c in ("ecg", "ppg", "abp", "intervention") if c in ("ecg", "ppg") or self.cfg.get(c)],
            "truth": self._truth(t),
        }
        if include_waveforms:
            i1 = self._idx(t)
            i0 = i1 - int(round(config.WINDOW_SEC * self.rec.fs))
            tick["waveforms"] = {
                "fs": self.rec.fs / 2,
                "ecg": _decimate(self.rec.ecg[i0:i1]),
                "ppg": _decimate(self.rec.ppg[i0:i1]),
                "abp": _decimate(self.rec.abp[i0:i1]) if self.cfg["abp"] else None,
            }
        return tick

    def run_to_end(self, include_waveforms: bool = False) -> List[Dict[str, object]]:
        ticks = []
        while True:
            tick = self.step(include_waveforms=include_waveforms)
            if tick is None:
                return ticks
            ticks.append(tick)
