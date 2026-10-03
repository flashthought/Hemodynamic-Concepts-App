"""
Intervention-response tracking (original Concept 3, part of combined Concept A).

When a vasopressor or fluid intervention starts, the tracker freezes a
baseline from the preceding RESPONSE_BASELINE_SEC, waits RESPONSE_MIN_SEC for
the drug or fluid to act, then compares the patient's state against that
baseline until RESPONSE_WINDOW_SEC after onset, when the assessment is final.

It reports a *physiological trend*, never a treatment recommendation:
"improving", "no clear response" or "possible worsening". All treatment
decisions stay with the clinician (intended use, docs/01).

Classification rule (identical for the app and for ground-truth scoring in
evaluation/, so agreement between the two is a fair verification test):
  vasopressor: improving  if dMAP >= +5 mmHg, or R rose >= 10 % and dMAP >= 0
  fluid/other: improving  if dMAP >= +5 mmHg
  any:         worsening  if dMAP <= -5 mmHg; otherwise "no clear response"
"""

from collections import deque
from typing import Dict, List, Optional

import numpy as np

from app.engine import config


def classify_response(kind: str, d_map: float, r_frac: Optional[float]) -> str:
    if d_map <= -config.RESPONSE_MAP_DELTA:
        return "possible worsening"
    if d_map >= config.RESPONSE_MAP_DELTA:
        return "improving"
    if kind == "vasopressor" and r_frac is not None and r_frac >= config.RESPONSE_R_FRACTION and d_map >= 0:
        return "improving"
    return "no clear response"


def _median(values):
    vals = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.median(vals)) if vals else None


class InterventionTracker:
    """
    Interventions enter as typed, timestamped events - from the pump / EHR feed
    ("recorded") or entered by a clinician ("manual") - rather than being inferred
    from the model's combined intervention channel, which cannot tell a
    vasopressor from a fluid bolus. Overlapping interventions are flagged as
    confounded because their effects on MAP cannot be separated.
    """

    def __init__(self):
        self.history = deque()          # (t, map_value, r, c)
        self.records: List[Dict[str, object]] = []

    def _baseline(self, t0: float):
        window = [h for h in self.history if t0 - config.RESPONSE_BASELINE_SEC <= h[0] < t0]
        return {
            "map": _median(h[1] for h in window),
            "r": _median(h[2] for h in window),
            "c": _median(h[3] for h in window),
            "seconds": round(window[-1][0] - window[0][0], 1) if len(window) > 1 else 0.0,
        }

    def start(self, t: float, kind: str, label: str, origin: str) -> Dict[str, object]:
        overlapping = [r["id"] for r in self.records if abs(r["onset_t"] - t) < config.RESPONSE_WINDOW_SEC]
        rec = {
            "id": len(self.records) + 1,
            "onset_t": round(t, 1),
            "type": kind,
            "label": label,
            "origin": origin,            # "recorded" (pump/EHR event) or "manual" (clinician entry)
            "baseline": self._baseline(t),
            "status": "pending",
            "final": False,
            "d_map": None, "r_change_pct": None, "c_change_pct": None,
            "confounded_with": overlapping,
        }
        for other in self.records:
            if other["id"] in overlapping:
                other["confounded_with"] = sorted(set(other.get("confounded_with", [])) | {rec["id"]})
        self.records.append(rec)
        return rec

    def update(self, t: float, map_value: Optional[float], r: Optional[float],
               c: Optional[float]) -> List[Dict[str, object]]:
        """Feed one tick. Returns records whose assessment changed on this tick."""
        self.history.append((t, map_value, r, c))
        while self.history and self.history[0][0] < t - (config.RESPONSE_BASELINE_SEC + config.RESPONSE_WINDOW_SEC + 30):
            self.history.popleft()

        changed = []
        for rec in self.records:
            if rec["final"]:
                continue
            elapsed = t - rec["onset_t"]
            if elapsed < config.RESPONSE_MIN_SEC:
                continue
            base = rec["baseline"]
            recent = [h for h in self.history if t - 10.0 <= h[0] <= t]
            now_map, now_r, now_c = _median(h[1] for h in recent), _median(h[2] for h in recent), _median(h[3] for h in recent)
            if base["map"] is None or now_map is None:
                continue
            d_map = now_map - base["map"]
            r_frac = (now_r - base["r"]) / base["r"] if base["r"] and now_r is not None else None
            c_frac = (now_c - base["c"]) / base["c"] if base["c"] and now_c is not None else None
            status = classify_response(rec["type"], d_map, r_frac)
            before = (rec["status"], rec["final"])
            rec.update({
                "d_map": round(d_map, 1),
                "r_change_pct": None if r_frac is None else round(100 * r_frac, 1),
                "c_change_pct": None if c_frac is None else round(100 * c_frac, 1),
                "status": status,
                "assessed_t": round(t, 1),
                "final": elapsed >= config.RESPONSE_WINDOW_SEC,
            })
            if (rec["status"], rec["final"]) != before:
                changed.append(rec)
        return changed
