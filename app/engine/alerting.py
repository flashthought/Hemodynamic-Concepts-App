"""
Alert-level logic shared by all three concepts.

Physiological alert levels follow the priority conventions of IEC 60601-1-8
(medical electrical equipment - alarm systems): CRITICAL maps to a high
priority alarm, DETERIORATING to medium, WATCH to low, STABLE to no alarm.
An unusable signal produces a *technical* condition ("check signal") instead
of a physiological level, because the device must not claim a patient is
stable (or unstable) from data it could not interpret.
"""

import math
from typing import Dict, List, Optional

from app.engine import config

LEVELS = ["STABLE", "WATCH", "DETERIORATING", "CRITICAL"]
PRIORITY = {"STABLE": "none", "WATCH": "low", "DETERIORATING": "medium", "CRITICAL": "high"}

# Highest alarm priority an estimate of a given confidence may raise. A
# low-confidence estimate must not produce a high-priority alarm: that would
# feed alarm fatigue and automation bias (FMEA R-03, R-05).
MAX_LEVEL_FOR_CONFIDENCE = {"High": 3, "Moderate": 2, "Low": 1}
CONF_RANK = {"Low": 0, "Moderate": 1, "High": 2}


def measured_floor(measured_map: Optional[float], margin: float = 0.0) -> int:
    """
    RC-10: the alert level that the MEASURED arterial pressure alone justifies.
    Only DETERIORATING and CRITICAL are floored (WATCH stays forecast-driven, to
    avoid adding alarm burden). A positive margin makes the floor harder to
    clear, giving the same hysteresis as the forecast path.
    """
    if measured_map is None:
        return 0
    m = measured_map - margin
    if m < config.MAP_CRITICAL:
        return 3
    if m < config.MAP_TARGET:
        return 2
    return 0


def raw_level(forecast_map: float, current_map: Optional[float] = None) -> int:
    """Instantaneous alert level for one forecast, before persistence logic."""
    if forecast_map < config.MAP_CRITICAL:
        return 3
    if forecast_map < config.MAP_TARGET:
        return 2
    if forecast_map < config.MAP_WATCH:
        return 1
    if current_map is not None and (forecast_map - current_map) <= -config.RAPID_DROP_MMHG:
        return 1
    return 0


def prob_below_target(forecast_map: float, sigma: float, target: float = config.MAP_TARGET) -> float:
    """P(true MAP < target | forecast) assuming Gaussian forecast error with SD sigma."""
    sigma = max(float(sigma), 1e-6)
    z = (target - float(forecast_map)) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def explain(level: int, raw: int, forecast_map: float, current_map: Optional[float], p_below: float) -> str:
    """Plain-language reason for the displayed level (Nielsen: visibility of system status)."""
    fmap = f"{forecast_map:.0f} mmHg"
    if raw < level:
        why = (f"Holding {LEVELS[level]} while the forecast recovers ({fmap}); the alert steps down only after "
               f"{config.DEESCALATE_PERSIST} consecutive improved updates.")
    elif raw > level:
        why = f"Forecast MAP {fmap} indicates {LEVELS[raw]}; confirming on the next update before escalating."
    elif level == 3:
        why = f"Forecast MAP {fmap} is below the critical threshold ({config.MAP_CRITICAL:.0f} mmHg)."
    elif level == 2:
        why = f"Forecast MAP {fmap} is below the {config.MAP_TARGET:.0f} mmHg treatment target."
    elif level == 1:
        if forecast_map < config.MAP_WATCH or current_map is None:
            why = f"Forecast MAP {fmap} is approaching the {config.MAP_TARGET:.0f} mmHg target."
        else:
            why = f"Forecast MAP {fmap} is falling fast from the current {current_map:.0f} mmHg."
    else:
        why = f"Forecast MAP {fmap} is within the expected range."
    return f"{why} P(MAP < {config.MAP_TARGET:.0f}) = {100 * p_below:.0f}%."


class AlertStateMachine:
    """
    Converts per-window raw levels into the displayed alert level.

    Escalation needs ESCALATE_PERSIST consecutive windows above the current
    level and escalates to the lowest of those windows' levels (a single
    spike cannot jump straight to CRITICAL). De-escalation needs
    DEESCALATE_PERSIST consecutive windows below the current level, evaluated
    with a HYSTERESIS_MMHG margin so the alert does not chatter on a boundary.
    Windows with neither a usable forecast nor a measured pressure are ignored.

    Two safety rules sit on top (risk controls from hazard H-09, docs/06):
      RC-10 measured-pressure floor - when an artifact-free arterial line shows
            MAP < 65 (< 55) the level is at least DETERIORATING (CRITICAL). A
            forecast may add warning but never suppress what is measured, so
            the device can never be less safe than a conventional monitor.
      RC-08 fail-safe hold - an alarm established on higher-confidence evidence
            cannot be cleared by lower-confidence evidence (e.g. after the
            arterial line is lost). The level is held until evidence of at
            least the same confidence supports stepping down.
    """

    def __init__(self, fail_safe_hold: bool = True, use_measured_floor: bool = True):
        # Both controls are switchable only so verification can measure the
        # hazard with and without them (evaluation/evaluate_concepts.py).
        self.fail_safe_hold = fail_safe_hold
        self.use_measured_floor = use_measured_floor
        self.level = 0
        self.level_conf = CONF_RANK["High"]
        self._run: List[int] = []
        self._direction = 0
        self.last_valid_level = 0
        self.last_valid_t: Optional[float] = None

    def update(self, t: float, forecast_map: Optional[float], current_map: Optional[float],
               usable: bool, p_below: float = 0.0, confidence: str = "High",
               measured_map: Optional[float] = None) -> Dict[str, object]:
        if not self.use_measured_floor:
            measured_map = None  # baseline Version 5 behaviour: alerts come from the forecast only
        has_forecast = usable and forecast_map is not None
        if not has_forecast and measured_map is None:
            return {
                "level": None,
                "level_name": "SIGNAL CHECK",
                "priority": "technical",
                "technical": True,
                "raw_level": None,
                "changed": False,
                "held": self.level > 0,
                "last_valid_level": LEVELS[self.last_valid_level],
                "last_valid_age_sec": None if self.last_valid_t is None else round(t - self.last_valid_t, 1),
                "reason": "Signal quality is insufficient for a reliable forecast. Check sensors; no haemodynamic "
                          "alert is issued from unusable data"
                          + (f" (last valid level {LEVELS[self.level]} is held)." if self.level > 0 else "."),
            }

        # Confidence of the evidence on this update: a valid arterial line is direct measurement.
        evidence = max(CONF_RANK.get(confidence, 0) if has_forecast else -1,
                       CONF_RANK["High"] if measured_map is not None else -1)
        cap = MAX_LEVEL_FOR_CONFIDENCE.get(confidence, 1) if has_forecast else 0
        uncapped = raw_level(forecast_map, current_map) if has_forecast else 0
        floor = measured_floor(measured_map) if self.use_measured_floor else 0
        raw = max(min(uncapped, cap), floor)
        previous = self.level
        held = False
        if raw > self.level:
            direction, candidate = 1, raw
        else:
            # Hysteresis: judge de-escalation as if the pressure were lower.
            fc = min(raw_level(forecast_map - config.HYSTERESIS_MMHG, current_map), cap) if has_forecast else 0
            fl = measured_floor(measured_map, config.HYSTERESIS_MMHG) if self.use_measured_floor else 0
            candidate = max(fc, fl)
            direction = -1 if candidate < self.level else 0
            if direction < 0 and self.fail_safe_hold and evidence < self.level_conf:
                direction, held = 0, True   # RC-08: weaker evidence cannot clear the alarm

        if direction == 0:
            self._run = []
        else:
            if direction != self._direction:
                self._run = []
            self._run.append(candidate)
            needed = config.ESCALATE_PERSIST if direction > 0 else config.DEESCALATE_PERSIST
            if len(self._run) >= needed:
                recent = self._run[-needed:]
                self.level = min(recent) if direction > 0 else max(recent)
                self._run = []
        self._direction = direction
        if self.level != previous:
            self.level_conf = evidence
        elif raw >= self.level:
            self.level_conf = max(self.level_conf, evidence)

        self.last_valid_level = self.level
        self.last_valid_t = t
        name = LEVELS[self.level]
        if held:
            reason = (f"Holding {name}: the arterial-pressure channel is unavailable and a lower-confidence estimate "
                      f"cannot clear an alarm raised on higher-confidence data. Check the arterial line.")
        elif floor >= self.level and floor > (min(uncapped, cap) if has_forecast else 0):
            threshold = config.MAP_CRITICAL if floor == 3 else config.MAP_TARGET
            reason = (f"Measured arterial MAP {measured_map:.0f} mmHg is below {threshold:.0f} mmHg; "
                      f"a forecast cannot lower an alert the measured pressure supports.")
        elif has_forecast:
            reason = explain(self.level, raw, forecast_map, current_map, p_below)
            if uncapped > cap:
                reason += (f" Capped at {LEVELS[cap]}: a {confidence.lower()}-confidence estimate cannot "
                           f"raise a higher-priority alarm.")
        else:
            reason = (f"Forecast unavailable (signal quality); alert based on measured arterial MAP "
                      f"{measured_map:.0f} mmHg.")
        return {
            "level": self.level,
            "level_name": name,
            "priority": PRIORITY[name],
            "technical": False,
            "raw_level": raw,
            "uncapped_level": uncapped,
            "confidence_cap": LEVELS[cap],
            "measured_floor": LEVELS[floor],
            "held": held,
            "changed": self.level != previous,
            "reason": reason,
        }
