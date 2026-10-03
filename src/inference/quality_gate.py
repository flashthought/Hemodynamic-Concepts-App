"""Runtime quality gates for patient-monitor waveform windows."""

from typing import Dict, Optional, Tuple

import numpy as np

from src.inference.schemas import QualityReport


def missing_fraction(x: Optional[np.ndarray]) -> float:
    if x is None or len(x) == 0:
        return 1.0
    return float(np.mean(~np.isfinite(np.asarray(x, dtype=np.float64))))


def is_flatline(x: Optional[np.ndarray], min_run: int) -> bool:
    if x is None or len(x) < min_run + 1:
        return True
    arr = np.nan_to_num(np.asarray(x, dtype=np.float64), nan=np.nanmedian(x))
    diffs = np.abs(np.diff(arr))
    close = diffs < 1e-5
    run = 0
    for value in close:
        run = run + 1 if value else 0
        if run >= min_run:
            return True
    return False


def has_abp_spike(x: Optional[np.ndarray], max_step_mmHg: float = 80.0) -> bool:
    if x is None or len(x) < 2:
        return False
    arr = np.asarray(x, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if len(finite) < 2:
        return True
    return bool(np.nanmax(np.abs(np.diff(finite))) > max_step_mmHg)


def abp_plausible(x: Optional[np.ndarray]) -> bool:
    if x is None:
        return False
    arr = np.asarray(x, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if len(finite) < 4:
        return False
    dbp = float(np.percentile(finite, 2))
    sbp = float(np.percentile(finite, 98))
    map_val = float(np.mean(finite))
    pp = sbp - dbp
    return bool(
        20.0 <= dbp <= 150.0
        and 60.0 <= sbp <= 260.0
        and 40.0 <= map_val <= 180.0
        and 5.0 <= pp <= 150.0
        and not has_abp_spike(finite)
    )


def assess_runtime_quality(
    ecg: Optional[np.ndarray],
    ppg: Optional[np.ndarray],
    abp: Optional[np.ndarray],
    intervention: Optional[np.ndarray],
    fs: float,
    max_missing_fraction: float = 0.30,
) -> QualityReport:
    """Return whether a window can be used and which optional channels are valid."""
    warnings = []
    metrics: Dict[str, float] = {}
    flatline_run = max(8, int(round(0.20 * fs)))

    channel_valid = {
        "ppg": ppg is not None,
        "ecg": ecg is not None,
        "abp": abp is not None,
        "intervention": intervention is not None,
    }

    for name, arr in {"ppg": ppg, "ecg": ecg, "abp": abp, "intervention": intervention}.items():
        miss = missing_fraction(arr)
        metrics[f"{name}_missing_fraction"] = miss
        if arr is not None and miss > max_missing_fraction:
            channel_valid[name] = False
            warnings.append(f"{name} missing fraction is high ({miss:.2f})")

    if not channel_valid["ppg"]:
        return QualityReport(
            status="invalid",
            warnings=warnings + ["PPG is required for deployed inference."],
            metrics=metrics,
            channel_valid=channel_valid,
        )

    for name, arr in {"ppg": ppg, "ecg": ecg}.items():
        if channel_valid.get(name, False) and is_flatline(arr, flatline_run):
            channel_valid[name] = False
            warnings.append(f"{name} appears flatlined")

    if not channel_valid["ppg"]:
        return QualityReport(
            status="invalid",
            warnings=warnings + ["PPG quality is too poor for prediction."],
            metrics=metrics,
            channel_valid=channel_valid,
        )

    if channel_valid.get("abp", False) and not abp_plausible(abp):
        channel_valid["abp"] = False
        warnings.append("ABP failed runtime physiology/spike checks; ABP channel will be ignored.")

    if intervention is not None:
        max_u = float(np.nanmax(np.nan_to_num(intervention, nan=0.0)))
        metrics["intervention_max"] = max_u
        if max_u > 1.5:
            warnings.append("Intervention channel exceeded expected normalized range and was clipped.")

    status = "valid" if not warnings else "low_confidence"
    return QualityReport(status=status, warnings=warnings, metrics=metrics, channel_valid=channel_valid)
