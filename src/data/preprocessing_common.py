"""
Common dataset preprocessing utilities.

The narrowed project uses three data sources with different roles, but the
model-facing representation should be consistent: aligned ECG, PPG, ABP
windows, future ABP targets, optional latent ground truth, optional
intervention inputs, and quality/feature summaries.
"""

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.preprocessing.fiducials import (
    compute_pat,
    detect_ecg_rpeaks,
    detect_ppg_fiducials,
)
from src.preprocessing.filters import filter_ecg, filter_ppg
from src.preprocessing.sqi import evaluate_window_quality


CORE_MODALITIES = ("ecg", "ppg", "abp")
OPTIONAL_MODEL_FIELDS = ("r_true", "c_true", "intervention")


def interpolate_nans(signal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fill NaNs by linear interpolation and return the filled signal plus the
    original missingness mask.
    """
    x = np.asarray(signal, dtype=np.float32).reshape(-1)
    missing = np.isnan(x)
    if len(x) == 0:
        return x, missing
    if missing.all():
        return np.zeros_like(x, dtype=np.float32), missing
    if missing.any():
        idx = np.arange(len(x))
        x[missing] = np.interp(idx[missing], idx[~missing], x[~missing])
    return x.astype(np.float32), missing


def resample_to_rate(
    signal: np.ndarray,
    source_fs: float,
    target_fs: float,
    kind: str = "linear",
) -> np.ndarray:
    """Resample a one-dimensional signal using timestamp interpolation."""
    x = np.asarray(signal, dtype=np.float32).reshape(-1)
    if len(x) == 0 or np.isclose(source_fs, target_fs):
        return x

    duration = len(x) / float(source_fs)
    source_t = np.arange(len(x), dtype=np.float64) / float(source_fs)
    target_n = int(np.floor(duration * float(target_fs)))
    target_t = np.arange(target_n, dtype=np.float64) / float(target_fs)

    if kind == "nearest":
        nearest_idx = np.searchsorted(source_t, target_t, side="left")
        nearest_idx = np.clip(nearest_idx, 0, len(x) - 1)
        return x[nearest_idx].astype(np.float32)
    return np.interp(target_t, source_t, x).astype(np.float32)


def safe_filter_ecg(ecg: np.ndarray, fs: float) -> np.ndarray:
    """Apply the ECG filter when the signal is long enough for filtfilt."""
    try:
        return filter_ecg(ecg, fs=fs).astype(np.float32)
    except ValueError:
        return np.asarray(ecg, dtype=np.float32)


def safe_filter_ppg(ppg: np.ndarray, fs: float) -> np.ndarray:
    """Apply the PPG filter when the signal is long enough for filtfilt."""
    try:
        return filter_ppg(ppg, fs=fs).astype(np.float32)
    except ValueError:
        return np.asarray(ppg, dtype=np.float32)


def compute_window_features(
    ecg_window: np.ndarray,
    ppg_window: np.ndarray,
    abp_window: np.ndarray,
    fs: float,
) -> Dict[str, float]:
    """Compute quality, timing, and ABP summary features for one window."""
    r_peaks = detect_ecg_rpeaks(ecg_window, fs=fs)
    ppg_fids = detect_ppg_fiducials(ppg_window, fs=fs)
    pat = compute_pat(r_peaks, ppg_fids["diastolic_feet"], fs=fs)

    if len(r_peaks) >= 2:
        rr_sec = np.diff(r_peaks) / fs
        heart_rate_bpm = 60.0 / np.mean(rr_sec)
        hrv_sdnn_ms = np.std(rr_sec) * 1000.0
    else:
        heart_rate_bpm = np.nan
        hrv_sdnn_ms = np.nan

    mean_pat_ms = float(np.mean(pat["pat_ms"])) if len(pat["pat_ms"]) else np.nan
    std_pat_ms = float(np.std(pat["pat_ms"])) if len(pat["pat_ms"]) else np.nan

    quality = evaluate_window_quality(
        ecg_window,
        ppg_window,
        num_ecg_rpeaks=len(r_peaks),
        num_ppg_pulses=len(ppg_fids["systolic_peaks"]),
        fs=fs,
    )

    sbp = float(np.nanmax(abp_window)) if len(abp_window) else np.nan
    dbp = float(np.nanmin(abp_window)) if len(abp_window) else np.nan
    map_value = float(np.nanmean(abp_window)) if len(abp_window) else np.nan

    return {
        "is_valid": float(quality["is_valid"]),
        "composite_sqi": float(quality["composite_sqi"]),
        "rhythm_match": float(quality["rhythm_match"]),
        "heart_rate_bpm": float(heart_rate_bpm),
        "hrv_sdnn_ms": float(hrv_sdnn_ms),
        "mean_pat_ms": mean_pat_ms,
        "std_pat_ms": std_pat_ms,
        "num_detected_beats": float(len(r_peaks)),
        "num_ppg_pulses": float(len(ppg_fids["systolic_peaks"])),
        "sbp": sbp,
        "dbp": dbp,
        "map": map_value,
        "pulse_pressure": sbp - dbp if np.isfinite(sbp) and np.isfinite(dbp) else np.nan,
    }


def is_physiological_abp_window(
    abp_window: np.ndarray,
    min_pressure: float = 20.0,
    max_pressure: float = 250.0,
    min_map: float = 30.0,
    max_map: float = 180.0,
    min_pulse_pressure: float = 5.0,
    max_pulse_pressure: float = 150.0,
) -> bool:
    """Reject artifact-heavy ABP windows before model-facing archives are made."""
    x = np.asarray(abp_window, dtype=np.float32)
    if len(x) == 0 or not np.all(np.isfinite(x)):
        return False

    sbp = float(np.nanmax(x))
    dbp = float(np.nanmin(x))
    map_value = float(np.nanmean(x))
    pulse_pressure = sbp - dbp
    return (
        min_pressure <= dbp
        and sbp <= max_pressure
        and min_map <= map_value <= max_map
        and min_pulse_pressure <= pulse_pressure <= max_pulse_pressure
    )


def iter_window_starts(
    n_points: int,
    window_len: int,
    forecast_horizon: int,
    forecast_step: int,
    stride: int,
) -> Iterable[int]:
    """Yield starts with enough future ABP samples for the target horizon."""
    last_future_offset = (forecast_horizon - 1) * forecast_step
    for start in range(0, max(0, n_points - window_len), stride):
        end = start + window_len
        if end + last_future_offset >= n_points:
            break
        yield start


def build_window_records(
    dataset_source: str,
    case_id: str,
    ecg: np.ndarray,
    ppg: np.ndarray,
    abp: np.ndarray,
    fs: float,
    target_fs: float = 125.0,
    window_sec: float = 10.0,
    stride_sec: float = 2.0,
    forecast_horizon: int = 15,
    forecast_step_sec: float = 1.0,
    r_true: Optional[np.ndarray] = None,
    c_true: Optional[np.ndarray] = None,
    intervention: Optional[np.ndarray] = None,
    target_abp: Optional[np.ndarray] = None,
    filter_waveforms: bool = True,
    enforce_physiological_abp: bool = False,
) -> List[Dict[str, Any]]:
    """
    Convert one aligned case into fixed-length model windows.

    Real datasets should pass only observable signals and optional intervention
    tracks. Synthetic data can also pass R and C ground truth.
    """
    ecg_filled, ecg_missing = interpolate_nans(ecg)
    ppg_filled, ppg_missing = interpolate_nans(ppg)
    abp_filled, abp_missing = interpolate_nans(abp)
    target_abp_filled, _ = interpolate_nans(abp if target_abp is None else target_abp)

    ecg_rs = resample_to_rate(ecg_filled, fs, target_fs)
    ppg_rs = resample_to_rate(ppg_filled, fs, target_fs)
    abp_rs = resample_to_rate(abp_filled, fs, target_fs)
    target_abp_rs = resample_to_rate(target_abp_filled, fs, target_fs)
    ecg_missing_rs = resample_to_rate(ecg_missing.astype(np.float32), fs, target_fs)
    ppg_missing_rs = resample_to_rate(ppg_missing.astype(np.float32), fs, target_fs)
    abp_missing_rs = resample_to_rate(abp_missing.astype(np.float32), fs, target_fs)

    n = min(len(ecg_rs), len(ppg_rs), len(abp_rs), len(target_abp_rs))
    ecg_rs, ppg_rs, abp_rs = ecg_rs[:n], ppg_rs[:n], abp_rs[:n]
    target_abp_rs = target_abp_rs[:n]
    ecg_missing_rs = ecg_missing_rs[:n]
    ppg_missing_rs = ppg_missing_rs[:n]
    abp_missing_rs = abp_missing_rs[:n]

    if filter_waveforms:
        ecg_proc = safe_filter_ecg(ecg_rs, fs=target_fs)
        ppg_proc = safe_filter_ppg(ppg_rs, fs=target_fs)
    else:
        ecg_proc = ecg_rs
        ppg_proc = ppg_rs

    r_rs = None if r_true is None else resample_to_rate(r_true, fs, target_fs)[:n]
    c_rs = None if c_true is None else resample_to_rate(c_true, fs, target_fs)[:n]
    u_rs = (
        np.zeros(n, dtype=np.float32)
        if intervention is None
        else resample_to_rate(intervention, fs, target_fs)[:n]
    )

    window_len = int(round(window_sec * target_fs))
    stride = int(round(stride_sec * target_fs))
    forecast_step = max(1, int(round(forecast_step_sec * target_fs)))

    records: List[Dict[str, Any]] = []
    for start in iter_window_starts(n, window_len, forecast_horizon, forecast_step, stride):
        end = start + window_len
        target_idx = end + np.arange(forecast_horizon) * forecast_step

        ecg_window = ecg_proc[start:end]
        ppg_window = ppg_proc[start:end]
        abp_window = abp_rs[start:end]
        future_abp = target_abp_rs[target_idx]

        features = compute_window_features(ecg_window, ppg_window, abp_window, fs=target_fs)
        abp_valid = is_physiological_abp_window(abp_window)
        if enforce_physiological_abp:
            features["is_valid"] = float(bool(features["is_valid"]) and abp_valid)

        record: Dict[str, Any] = {
            "dataset_source": dataset_source,
            "case_id": str(case_id),
            "window_start_sec": float(start / target_fs),
            "fs": float(target_fs),
            "forecast_step_sec": float(forecast_step_sec),
            "forecast_horizon": float(forecast_horizon),
            "ecg": ecg_window.astype(np.float32),
            "ppg": ppg_window.astype(np.float32),
            "abp": abp_window.astype(np.float32),
            "future_abp": future_abp.astype(np.float32),
            "r_true": np.nan if r_rs is None else float(np.nanmean(r_rs[start:end])),
            "c_true": np.nan if c_rs is None else float(np.nanmean(c_rs[start:end])),
            "intervention": u_rs[start:end].astype(np.float32),
            "missing_ecg_fraction": float(np.mean(ecg_missing_rs[start:end] > 0.5)),
            "missing_ppg_fraction": float(np.mean(ppg_missing_rs[start:end] > 0.5)),
            "missing_abp_fraction": float(np.mean(abp_missing_rs[start:end] > 0.5)),
            "abp_physiology_valid": float(abp_valid),
        }
        record.update(features)
        records.append(record)

    return records


def save_processed_records(records: List[Dict[str, Any]], output_path: str) -> pd.DataFrame:
    """Save model arrays to NPZ and a sidecar CSV summary."""
    if not records:
        raise ValueError("No processed windows were produced.")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    array_keys = ("ecg", "ppg", "abp", "future_abp", "intervention")
    scalar_keys = (
        "r_true",
        "c_true",
        "window_start_sec",
        "fs",
        "forecast_step_sec",
        "forecast_horizon",
        "is_valid",
        "abp_physiology_valid",
        "composite_sqi",
        "rhythm_match",
        "heart_rate_bpm",
        "hrv_sdnn_ms",
        "mean_pat_ms",
        "std_pat_ms",
        "num_detected_beats",
        "num_ppg_pulses",
        "sbp",
        "dbp",
        "map",
        "pulse_pressure",
        "missing_ecg_fraction",
        "missing_ppg_fraction",
        "missing_abp_fraction",
    )
    optional_scalar_keys = (
        "label_sbp",
        "label_dbp",
        "segment_index",
    )
    optional_string_keys = (
        "split",
        "patient_id",
        "scenario",
        "simulator_model",
    )

    payload: Dict[str, Any] = {
        key: np.stack([rec[key] for rec in records]).astype(np.float32)
        for key in array_keys
    }
    payload.update(
        {
            key: np.array([rec[key] for rec in records], dtype=np.float32)
            for key in scalar_keys
        }
    )
    for key in optional_scalar_keys:
        if key in records[0]:
            payload[key] = np.array([rec.get(key, np.nan) for rec in records], dtype=np.float32)
    for key in optional_string_keys:
        if key in records[0]:
            payload[key] = np.array([rec.get(key, "") for rec in records])
    payload["dataset_source"] = np.array([rec["dataset_source"] for rec in records])
    payload["case_id"] = np.array([rec["case_id"] for rec in records])

    np.savez_compressed(output_path, **payload)

    summary_cols = ["dataset_source", "case_id"] + list(scalar_keys)
    summary_cols += [key for key in optional_scalar_keys if key in records[0]]
    summary_cols += [key for key in optional_string_keys if key in records[0]]
    summary = pd.DataFrame([{col: rec[col] for col in summary_cols} for rec in records])
    summary_path = os.path.splitext(output_path)[0] + "_summary.csv"
    summary.to_csv(summary_path, index=False)
    return summary
