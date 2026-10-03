"""
Preprocess raw VitalDB case files (src/data/download_vitaldb.py output) into
the common model-ready window archive described in configs/datasets.yaml.

VitalDB has no continuous R/C ground truth (r_true/c_true stay NaN, as with
MIMIC-BP), but unlike MIMIC-BP it can carry a genuine continuous
intervention channel (combined vasopressor infusion rate) when
download_vitaldb.py was run with --include-interventions.

Cases can be long (tens of minutes even after download_vitaldb's duration
cap), so windows per case are capped and evenly subsampled across the
recording to keep the processed archive a manageable size for evaluation.
"""

import argparse
import glob
import os

import numpy as np

from src.data.common import (
    WindowArchiveWriter,
    compute_forecast_horizon,
    compute_window_metrics,
    full_range_physiologically_plausible,
    missing_fraction,
    prepare_window_channels,
    sliding_window_starts,
)

DEFAULT_MAX_WINDOWS_PER_CASE = 150
DEFAULT_MAX_ABP_STEP_MMHG = 80.0
DEFAULT_MAX_INTERVENTION_INTENSITY = 12.0


def normalize_intervention_track(intervention: np.ndarray) -> np.ndarray:
    """
    Convert heterogeneous VitalDB vasopressor-rate tracks to a bounded model
    input. Synthetic interventions are in [0, 1]; raw VitalDB infusion rates can
    be several orders larger and previously caused unstable intervention-window
    forecasts. Robust per-case scaling preserves onset/timing while avoiding
    scale leakage into the model.
    """
    x = np.nan_to_num(np.asarray(intervention, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    x = np.maximum(x, 0.0)
    positive = x[x > 0.0]
    if positive.size == 0:
        return x.astype(np.float32)
    scale = float(np.nanpercentile(positive, 95))
    if not np.isfinite(scale) or scale <= 0.0:
        scale = float(np.nanmax(positive))
    if not np.isfinite(scale) or scale <= 0.0:
        return np.zeros_like(x, dtype=np.float32)
    return np.clip(x / scale, 0.0, 1.0).astype(np.float32)


def has_extreme_pressure_step(x: np.ndarray, max_step_mmHg: float = DEFAULT_MAX_ABP_STEP_MMHG) -> bool:
    """Reject obvious line-flush/spike transitions that pass absolute bounds."""
    x = np.asarray(x, dtype=np.float64)
    finite = x[np.isfinite(x)]
    if finite.size < 2:
        return True
    return bool(np.nanmax(np.abs(np.diff(finite))) > max_step_mmHg)


def preprocess_vitaldb(
    input_dir: str = "data/raw/vitaldb",
    output: str = "data/processed/vitaldb_windows.npz",
    window_sec: float = 10.0,
    stride_sec: float = 2.0,
    forecast_horizon_sec: float = 15.0,
    forecast_step_sec: float = 1.0,
    max_windows_per_case: int = DEFAULT_MAX_WINDOWS_PER_CASE,
) -> int:
    files = sorted(glob.glob(os.path.join(input_dir, "case_*.npz")))
    if not files:
        raise FileNotFoundError(
            f"No case_*.npz files found in {input_dir}. Run src.data.download_vitaldb first."
        )

    writer = WindowArchiveWriter()

    for fpath in files:
        data = np.load(fpath, allow_pickle=True)
        fs = float(data["fs"])
        case_id = str(data["case_id"])
        ecg_raw_full = data["ecg"].astype(np.float64)
        ppg_raw_full = data["ppg"].astype(np.float64)
        abp_raw_full = data["abp"].astype(np.float64)
        intervention_full = (
            data["intervention"].astype(np.float64)
            if "intervention" in data.files
            else np.zeros_like(ecg_raw_full)
        )
        intervention_full = normalize_intervention_track(intervention_full)

        window_len = int(round(window_sec * fs))
        stride = int(round(stride_sec * fs))
        forecast_step = max(1, int(round(forecast_step_sec * fs)))
        forecast_horizon = compute_forecast_horizon(forecast_horizon_sec, forecast_step_sec)

        starts = sliding_window_starts(
            n_points=len(ecg_raw_full),
            window_len=window_len,
            stride=stride,
            forecast_horizon=forecast_horizon,
            forecast_step=forecast_step,
            max_windows=max_windows_per_case,
        )

        for start in starts:
            end = start + window_len
            target_idx = [end + k * forecast_step for k in range(forecast_horizon)]

            ecg_raw = ecg_raw_full[start:end]
            ppg_raw = ppg_raw_full[start:end]
            abp_raw = abp_raw_full[start:end]
            future_abp = abp_raw_full[target_idx]
            intervention_win = intervention_full[start:end]
            future_abp_context = abp_raw_full[start : target_idx[-1] + 1]

            miss_ecg = missing_fraction(ecg_raw)
            miss_ppg = missing_fraction(ppg_raw)
            miss_abp = missing_fraction(abp_raw)

            ecg_filt, ppg_filt, abp_filled = prepare_window_channels(ecg_raw, ppg_raw, abp_raw, fs)
            metrics = compute_window_metrics(ecg_filt, ppg_filt, abp_raw, fs)

            is_valid = bool(
                metrics["quality_is_valid"]
                and metrics["abp_physiology_valid"]
                and miss_ecg < 0.3
                and miss_ppg < 0.3
                and miss_abp < 0.3
                and full_range_physiologically_plausible(abp_raw)
                and full_range_physiologically_plausible(future_abp)
                and not has_extreme_pressure_step(abp_raw)
                and not has_extreme_pressure_step(future_abp_context)
                and float(np.nanmax(intervention_win)) <= DEFAULT_MAX_INTERVENTION_INTENSITY
            )

            future_abp_filled = future_abp
            if np.isnan(future_abp_filled).any():
                import pandas as pd

                future_abp_filled = (
                    pd.Series(future_abp_filled).interpolate(limit_direction="both").fillna(0.0).to_numpy()
                )
            intervention_filled = intervention_win
            if np.isnan(intervention_filled).any():
                intervention_filled = np.nan_to_num(intervention_filled, nan=0.0)

            writer.add(
                arrays={
                    "ecg": ecg_filt,
                    "ppg": ppg_filt,
                    "abp": abp_filled,
                    "future_abp": future_abp_filled.astype(np.float32),
                    "intervention": intervention_filled.astype(np.float32),
                },
                scalars={
                    "dataset_source": "vitaldb",
                    "case_id": case_id,
                    "window_start_sec": start / fs,
                    "fs": fs,
                    "forecast_step_sec": forecast_step_sec,
                    "forecast_horizon": forecast_horizon,
                    "r_true": float("nan"),
                    "c_true": float("nan"),
                    "is_valid": is_valid,
                    "abp_physiology_valid": metrics["abp_physiology_valid"],
                    "composite_sqi": metrics["composite_sqi"],
                    "heart_rate_bpm": metrics["heart_rate_bpm"],
                    "mean_pat_ms": metrics["mean_pat_ms"],
                    "sbp": metrics["sbp"],
                    "dbp": metrics["dbp"],
                    "map": metrics["map"],
                    "pulse_pressure": metrics["pulse_pressure"],
                    "missing_ecg_fraction": miss_ecg,
                    "missing_ppg_fraction": miss_ppg,
                    "missing_abp_fraction": miss_abp,
                    "has_intervention_window": bool(np.nanmax(intervention_win) > 0.0),
                    "intervention_strength": float(np.nanmax(intervention_win)),
                    "scenario": "",
                    "simulator_model": "",
                },
            )

    df_summary = writer.save(output)
    print(f"Processed {len(files)} VitalDB cases.")
    print(f"Wrote {len(writer)} windows to {output}")
    print(f"Wrote per-window summary to {os.path.splitext(output)[0]}_summary.csv")
    print(f"Valid windows: {int(df_summary['is_valid'].sum())}/{len(df_summary)}")
    return len(writer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="data/raw/vitaldb")
    parser.add_argument("--output", default="data/processed/vitaldb_windows.npz")
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--stride-sec", type=float, default=2.0)
    parser.add_argument("--forecast-horizon-sec", type=float, default=15.0)
    parser.add_argument("--forecast-step-sec", type=float, default=1.0)
    parser.add_argument("--max-windows-per-case", type=int, default=DEFAULT_MAX_WINDOWS_PER_CASE)
    args = parser.parse_args()

    preprocess_vitaldb(
        input_dir=args.input_dir,
        output=args.output,
        window_sec=args.window_sec,
        stride_sec=args.stride_sec,
        forecast_horizon_sec=args.forecast_horizon_sec,
        forecast_step_sec=args.forecast_step_sec,
        max_windows_per_case=args.max_windows_per_case,
    )


if __name__ == "__main__":
    main()
