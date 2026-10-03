"""Deployment preprocessing matching the model training window format."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.inference.schemas import MonitorWindow

try:
    from src.data.common import interpolate_nans, prepare_window_channels
except ImportError:
    def interpolate_nans(x: np.ndarray) -> np.ndarray:
        arr = np.asarray(x, dtype=np.float64)
        if not np.isnan(arr).any():
            return arr.astype(np.float32)
        idx = np.arange(len(arr))
        finite = np.isfinite(arr)
        if not np.any(finite):
            return np.zeros_like(arr, dtype=np.float32)
        return np.interp(idx, idx[finite], arr[finite]).astype(np.float32)

    def _zscore(x: np.ndarray) -> np.ndarray:
        arr = interpolate_nans(x).astype(np.float32)
        std = float(np.std(arr))
        return ((arr - float(np.mean(arr))) / (std if std > 1e-8 else 1.0)).astype(np.float32)

    def prepare_window_channels(ecg_raw: np.ndarray, ppg_raw: np.ndarray, abp_raw: np.ndarray, fs: float):
        return _zscore(ecg_raw), _zscore(ppg_raw), interpolate_nans(abp_raw)


@dataclass
class PreparedWindow:
    fs: float
    ecg: np.ndarray
    ppg: np.ndarray
    abp: Optional[np.ndarray]
    intervention: Optional[np.ndarray]
    original_fs: float
    n_samples: int


def as_array(x) -> Optional[np.ndarray]:
    if x is None:
        return None
    return np.asarray(x, dtype=np.float32)


def resample_to_fs(x: Optional[np.ndarray], original_fs: float, target_fs: float) -> Optional[np.ndarray]:
    if x is None:
        return None
    arr = np.asarray(x, dtype=np.float32)
    if len(arr) == 0:
        return arr
    if abs(float(original_fs) - float(target_fs)) < 1e-6:
        return arr
    duration = len(arr) / float(original_fs)
    n_target = max(1, int(round(duration * target_fs)))
    old_t = np.arange(len(arr), dtype=np.float64) / float(original_fs)
    new_t = np.arange(n_target, dtype=np.float64) / float(target_fs)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros(n_target, dtype=np.float32)
    filled = np.interp(old_t, old_t[finite], arr[finite])
    return np.interp(new_t, old_t, filled).astype(np.float32)


def trim_last_window(x: Optional[np.ndarray], n: int) -> Optional[np.ndarray]:
    if x is None:
        return None
    arr = np.asarray(x, dtype=np.float32)
    if len(arr) < n:
        raise ValueError(f"Expected at least {n} samples, got {len(arr)}.")
    return arr[-n:].astype(np.float32)


def normalize_intervention_live(intervention: Optional[np.ndarray], scale: Optional[float]) -> Optional[np.ndarray]:
    if intervention is None:
        return None
    x = np.nan_to_num(np.asarray(intervention, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    x = np.maximum(x, 0.0)
    if scale is not None and scale > 0:
        x = x / float(scale)
    elif np.nanmax(x) > 1.5:
        positive = x[x > 0.0]
        denom = float(np.nanpercentile(positive, 95)) if positive.size else 1.0
        x = x / max(denom, 1e-6)
    return np.clip(x, 0.0, 1.0).astype(np.float32)


class MonitorPreprocessor:
    """Convert a raw monitor window into model-ready arrays."""

    def __init__(self, target_fs: float = 125.0, window_sec: float = 10.0):
        self.target_fs = float(target_fs)
        self.window_sec = float(window_sec)
        self.window_samples = int(round(self.target_fs * self.window_sec))

    def prepare(self, window: MonitorWindow) -> PreparedWindow:
        original_fs = float(window.fs)
        if original_fs <= 0:
            raise ValueError("MonitorWindow.fs must be positive.")

        ecg = resample_to_fs(as_array(window.ecg), original_fs, self.target_fs)
        ppg = resample_to_fs(as_array(window.ppg), original_fs, self.target_fs)
        abp = resample_to_fs(as_array(window.abp), original_fs, self.target_fs)
        intervention = resample_to_fs(as_array(window.intervention), original_fs, self.target_fs)

        if ppg is None:
            raise ValueError("PPG is required for deployed inference.")
        ppg = trim_last_window(ppg, self.window_samples)
        ecg = trim_last_window(ecg, self.window_samples) if ecg is not None else np.zeros_like(ppg)
        abp = trim_last_window(abp, self.window_samples) if abp is not None else None
        intervention = trim_last_window(intervention, self.window_samples) if intervention is not None else None

        abp_for_preprocess = abp if abp is not None else np.zeros_like(ppg)
        ecg_filt, ppg_filt, abp_filled = prepare_window_channels(ecg, ppg, abp_for_preprocess, self.target_fs)
        intervention_norm = normalize_intervention_live(intervention, window.intervention_scale)

        return PreparedWindow(
            fs=self.target_fs,
            ecg=interpolate_nans(ecg_filt).astype(np.float32),
            ppg=interpolate_nans(ppg_filt).astype(np.float32),
            abp=abp_filled.astype(np.float32) if abp is not None else None,
            intervention=intervention_norm,
            original_fs=original_fs,
            n_samples=self.window_samples,
        )
