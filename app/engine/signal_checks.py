"""
Arterial-line artifact detection (risk control RC-09).

Version 5's runtime gate (src/inference/quality_gate.abp_plausible) accepts
ABP only if MAP >= 40, DBP >= 20 and SBP >= 60 mmHg. Those are physiology
NORMALITY bounds, so a genuine profound-shock waveform is classed as an
artifact and dropped - verification showed the device then fell back to the
non-invasive regime and de-escalated a CRITICAL alert to WATCH while the true
MAP was 38-42 mmHg (hazard H-09).

This check rejects ABP only for signs of ARTIFACT, never for being low:
  * line flush / fast-flush test: sample-to-sample step > 80 mmHg
  * clamped or disconnected line: flat run >= 0.2 s
  * over-damped / disconnected transducer: pulse pressure < 5 mmHg
  * physically impossible values for a perfused patient: MAP < 15 or > 200,
    SBP > 260, or DBP < 0 (transducer zeroing error)
"""

from typing import Optional, Tuple

import numpy as np

MAX_STEP_MMHG = 80.0
MIN_PULSE_PRESSURE = 5.0
FLAT_RUN_SEC = 0.2


def abp_artifact_free(abp: Optional[np.ndarray], fs: float) -> Tuple[bool, str]:
    if abp is None:
        return False, "no arterial line"
    x = np.asarray(abp, dtype=np.float64)
    finite = x[np.isfinite(x)]
    if finite.size < max(10, int(0.7 * x.size)):
        return False, "too much missing arterial data"
    if finite.size > 1 and np.max(np.abs(np.diff(finite))) > MAX_STEP_MMHG:
        return False, "line flush or spike"
    run_needed = max(2, int(FLAT_RUN_SEC * fs))
    flat = np.abs(np.diff(finite)) < 1e-5
    run = 0
    for f in flat:
        run = run + 1 if f else 0
        if run >= run_needed:
            return False, "flat (clamped or disconnected) line"
    sbp, dbp, mean = np.percentile(finite, 98), np.percentile(finite, 2), float(np.mean(finite))
    if sbp - dbp < MIN_PULSE_PRESSURE:
        return False, "no pulsatility (over-damped or disconnected transducer)"
    if not (15.0 <= mean <= 200.0) or sbp > 260.0 or dbp < 0.0:
        return False, "physically implausible pressure (check zeroing)"
    return True, "artifact-free"
