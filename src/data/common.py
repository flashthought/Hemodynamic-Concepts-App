"""
Shared windowing, quality-control, and archive-writing utilities for the
dataset-specific ingestion scripts in src/data/.

Every preprocessing script (preprocess_synthetic.py, preprocess_mimicbp.py,
preprocess_vitaldb.py) slices a continuous multimodal recording into fixed
windows and writes them into the common archive schema defined in
configs/datasets.yaml, so src/models/train.py and src/evaluation/*.py can
consume any of the three datasets interchangeably.
"""

import os
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from src.preprocessing.filters import filter_ecg, filter_ppg, normalize_signal
from src.preprocessing.fiducials import (
    detect_ecg_rpeaks,
    detect_ppg_fiducials,
    compute_pat,
)
from src.preprocessing.sqi import evaluate_window_quality

# Array fields stored as (N, window_len) or (N, forecast_horizon) float32 matrices.
ARRAY_FIELDS = ("ecg", "ppg", "abp", "future_abp", "intervention")

# Per-window scalar fields from the processed_window_schema in configs/datasets.yaml.
SCALAR_FIELDS = (
    "dataset_source",
    "case_id",
    "window_start_sec",
    "fs",
    "forecast_step_sec",
    "forecast_horizon",
    "r_true",
    "c_true",
    "is_valid",
    "abp_physiology_valid",
    "composite_sqi",
    "heart_rate_bpm",
    "mean_pat_ms",
    "sbp",
    "dbp",
    "map",
    "pulse_pressure",
    "missing_ecg_fraction",
    "missing_ppg_fraction",
    "missing_abp_fraction",
    "scenario",
    "simulator_model",
)

STRING_FIELDS = ("dataset_source", "case_id", "scenario", "simulator_model")
BOOL_FIELDS = ("is_valid", "abp_physiology_valid")
INT_FIELDS = ("forecast_horizon",)


def compute_forecast_horizon(forecast_horizon_sec: float, forecast_step_sec: float) -> int:
    """Number of future steps to predict, e.g. 15 sec at 1 sec/step -> 15 steps."""
    return max(1, int(round(forecast_horizon_sec / forecast_step_sec)))


def interpolate_nans(x: np.ndarray) -> np.ndarray:
    """Linearly interpolate NaN gaps (sensor dropouts) and fill any remaining edges."""
    x = np.asarray(x, dtype=np.float64)
    if not np.isnan(x).any():
        return x.astype(np.float32)
    series = pd.Series(x)
    filled = series.interpolate(method="linear", limit_direction="both")
    if filled.isna().all():
        filled = filled.fillna(0.0)
    else:
        filled = filled.fillna(0.0)
    return filled.to_numpy(dtype=np.float32)


def full_range_physiologically_plausible(x: np.ndarray, lo: float = -30.0, hi: float = 300.0) -> bool:
    """
    Reject a waveform slice containing any sample outside a generous absolute
    pressure bound. This catches sensor-fault artifacts (e.g. an arterial-line
    flush transiently reading tens of thousands of mmHg) that a percentile-based
    check on a different slice of the recording (e.g. the observation window,
    when checking the forecast target) would not see.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0 or not np.all(np.isfinite(x)):
        return False
    return bool(np.all(x >= lo) and np.all(x <= hi))


def missing_fraction(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return 1.0
    return float(np.mean(~np.isfinite(x)))


def sliding_window_starts(
    n_points: int,
    window_len: int,
    stride: int,
    forecast_horizon: int,
    forecast_step: int,
    max_windows: Optional[int] = None,
) -> List[int]:
    """
    Candidate window start indices such that both the observation window and
    the future forecast target fit inside the recording.

    When max_windows is set (used to bound very long recordings, e.g. VitalDB
    surgical cases that can run several hours), candidates are evenly
    subsampled across the full recording rather than truncated to the start.
    """
    max_start = n_points - window_len - (forecast_horizon - 1) * forecast_step
    if max_start <= 0:
        return []
    starts = list(range(0, max_start, stride))
    if max_windows is not None and len(starts) > max_windows:
        idx = np.linspace(0, len(starts) - 1, max_windows).astype(int)
        starts = sorted(set(int(starts[i]) for i in idx))
    return starts


def compute_window_metrics(
    ecg_window: np.ndarray,
    ppg_window: np.ndarray,
    abp_window_raw: np.ndarray,
    fs: float,
) -> Dict[str, float]:
    """
    Compute quality-control and clinical summary metrics for one window.

    ecg_window/ppg_window are expected to already be band-pass filtered.
    abp_window_raw should be the *unfiltered* pressure waveform (mmHg) so
    absolute SBP/DBP/MAP values stay physically meaningful.
    """
    r_peaks = detect_ecg_rpeaks(ecg_window, fs=fs)
    ppg_fids = detect_ppg_fiducials(ppg_window, fs=fs)
    pat_res = compute_pat(r_peaks, ppg_fids["diastolic_feet"], fs=fs)

    if len(r_peaks) >= 2:
        rr_sec = np.diff(r_peaks) / fs
        heart_rate_bpm = float(60.0 / np.mean(rr_sec))
    else:
        heart_rate_bpm = float("nan")
    mean_pat_ms = float(np.mean(pat_res["pat_ms"])) if len(pat_res["pat_ms"]) > 0 else float("nan")

    quality = evaluate_window_quality(
        ecg_window,
        ppg_window,
        num_ecg_rpeaks=len(r_peaks),
        num_ppg_pulses=len(ppg_fids["systolic_peaks"]),
        fs=fs,
    )

    finite_abp = abp_window_raw[np.isfinite(abp_window_raw)]
    if finite_abp.size >= 4:
        dbp = float(np.percentile(finite_abp, 2))
        sbp = float(np.percentile(finite_abp, 98))
        map_val = float(np.mean(finite_abp))
    else:
        dbp = sbp = map_val = float("nan")
    pulse_pressure = sbp - dbp if np.isfinite(sbp) and np.isfinite(dbp) else float("nan")

    abp_physiology_valid = bool(
        np.isfinite(sbp)
        and np.isfinite(dbp)
        and 20.0 <= dbp <= 150.0
        and 60.0 <= sbp <= 260.0
        and 40.0 <= map_val <= 180.0
        and 5.0 <= pulse_pressure <= 150.0
        and sbp > dbp
    )

    return {
        "composite_sqi": float(quality["composite_sqi"]),
        "heart_rate_bpm": heart_rate_bpm,
        "mean_pat_ms": mean_pat_ms,
        "sbp": sbp,
        "dbp": dbp,
        "map": map_val,
        "pulse_pressure": pulse_pressure,
        "abp_physiology_valid": abp_physiology_valid,
        "quality_is_valid": bool(quality["is_valid"]),
    }


def prepare_window_channels(ecg_raw: np.ndarray, ppg_raw: np.ndarray, abp_raw: np.ndarray, fs: float):
    """
    Filter ECG/PPG (baseline wander + high-frequency noise removal), then
    z-score normalize each per window so amplitude scale is comparable across
    datasets that record in very different native units (e.g. VitalDB's
    SNUADC/PLETH swings roughly +/-100 while MIMIC-BP/synthetic PPG sit near
    +/-2-4). Without this, a model trained on one dataset's scale sees
    wildly out-of-distribution inputs from another and produces garbage.
    ABP is intentionally left unfiltered/unnormalized so its mmHg scale
    (the physical quantity being forecast) is preserved.
    """
    ecg_clean = interpolate_nans(ecg_raw)
    ppg_clean = interpolate_nans(ppg_raw)
    abp_filled = interpolate_nans(abp_raw)

    try:
        ecg_filt = filter_ecg(ecg_clean, fs=fs).astype(np.float32)
    except Exception:
        ecg_filt = ecg_clean
    try:
        ppg_filt = filter_ppg(ppg_clean, fs=fs).astype(np.float32)
    except Exception:
        ppg_filt = ppg_clean

    ecg_filt = normalize_signal(ecg_filt, method="zscore").astype(np.float32)
    ppg_filt = normalize_signal(ppg_filt, method="zscore").astype(np.float32)

    return ecg_filt, ppg_filt, abp_filled


class WindowArchiveWriter:
    """Accumulates per-window records and writes the common .npz + summary CSV pair."""

    def __init__(self):
        self._array_rows: List[Dict[str, np.ndarray]] = []
        self._scalar_rows: List[Dict[str, object]] = []

    def add(self, arrays: Dict[str, np.ndarray], scalars: Dict[str, object]) -> None:
        missing_arrays = set(ARRAY_FIELDS) - set(arrays)
        if missing_arrays:
            raise ValueError(f"Missing array fields: {sorted(missing_arrays)}")
        missing_scalars = set(SCALAR_FIELDS) - set(scalars)
        if missing_scalars:
            raise ValueError(f"Missing scalar fields: {sorted(missing_scalars)}")
        self._array_rows.append({k: np.asarray(v, dtype=np.float32) for k, v in arrays.items()})
        self._scalar_rows.append(dict(scalars))

    def __len__(self) -> int:
        return len(self._scalar_rows)

    def save(self, output_path: str, summary_path: Optional[str] = None) -> pd.DataFrame:
        if len(self._scalar_rows) == 0:
            raise ValueError("No windows were accumulated; nothing to save.")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        archive_payload: Dict[str, np.ndarray] = {}
        for field in ARRAY_FIELDS:
            archive_payload[field] = np.stack([row[field] for row in self._array_rows]).astype(np.float32)

        extra_scalar_keys = set()
        for row in self._scalar_rows:
            extra_scalar_keys.update(row.keys())
        extra_scalar_keys -= set(SCALAR_FIELDS)

        all_scalar_fields = list(SCALAR_FIELDS) + sorted(extra_scalar_keys)
        summary_columns: Dict[str, np.ndarray] = {}
        for field in all_scalar_fields:
            values = [row.get(field) for row in self._scalar_rows]
            if field in STRING_FIELDS:
                arr = np.array([str(v) for v in values])
            elif field in BOOL_FIELDS:
                arr = np.array([bool(v) for v in values])
            elif field in INT_FIELDS:
                arr = np.array([int(v) for v in values], dtype=np.int64)
            else:
                try:
                    arr = np.array([float(v) if v is not None else np.nan for v in values], dtype=np.float64)
                except (TypeError, ValueError):
                    arr = np.array([str(v) for v in values])
            archive_payload[field] = arr
            summary_columns[field] = arr

        np.savez_compressed(output_path, **archive_payload)

        df_summary = pd.DataFrame(summary_columns)
        if summary_path is None:
            root, _ = os.path.splitext(output_path)
            summary_path = f"{root}_summary.csv"
        os.makedirs(os.path.dirname(summary_path) or ".", exist_ok=True)
        df_summary.to_csv(summary_path, index=False)
        return df_summary
