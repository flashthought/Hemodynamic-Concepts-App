"""
Preprocess the synthetic Windkessel cohort (src/physics/generate_synthetic_cohort.py
output) into the common model-ready window archive described in
configs/datasets.yaml.

Synthetic recordings are the only dataset with known R/C ground truth, so
this is the only preprocessing script that fills r_true/c_true with real
values instead of NaN.
"""

import argparse
import glob
import os
import re

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

PATIENT_FILE_RE = re.compile(r"^patient_(\d+)_")


def preprocess_synthetic(
    input_dir: str = "data/synthetic",
    output: str = "data/processed/synthetic_windows.npz",
    window_sec: float = 10.0,
    stride_sec: float = 2.0,
    forecast_horizon_sec: float = 15.0,
    forecast_step_sec: float = 1.0,
) -> int:
    files = sorted(glob.glob(os.path.join(input_dir, "patient_*.npz")))
    if not files:
        raise FileNotFoundError(
            f"No patient_*.npz files found in {input_dir}. "
            "Run src.physics.generate_synthetic_cohort first."
        )

    writer = WindowArchiveWriter()

    for fpath in files:
        fname = os.path.basename(fpath)
        match = PATIENT_FILE_RE.match(fname)
        if not match:
            continue
        pid = int(match.group(1))
        case_id = f"synthetic_{pid:03d}"

        data = np.load(fpath, allow_pickle=True)
        fs = float(data["fs"])
        ecg_noisy = data["ecg_noisy"].astype(np.float64)
        ppg_noisy = data["ppg_noisy"].astype(np.float64)
        abp_noisy = data["abp_noisy"].astype(np.float64)
        abp_clean = data["abp_clean"].astype(np.float64)
        r_traj = data["R_ground_truth"].astype(np.float64)
        c_traj = data["C_ground_truth"].astype(np.float64)
        if "u_intervention" in data.files:
            intervention_traj = data["u_intervention"].astype(np.float64)
        elif "u_fluid" in data.files or "u_vasopressor" in data.files:
            vaso = data["u_vasopressor"].astype(np.float64) if "u_vasopressor" in data.files else 0.0
            fluid = data["u_fluid"].astype(np.float64) if "u_fluid" in data.files else 0.0
            intervention_traj = np.maximum(vaso, fluid)
        else:
            intervention_traj = np.zeros_like(ecg_noisy)
        scenario = str(data["scenario"]) if "scenario" in data.files else "unknown"
        simulator_model = str(data["simulator_model"]) if "simulator_model" in data.files else "unknown"

        window_len = int(round(window_sec * fs))
        stride = int(round(stride_sec * fs))
        forecast_step = max(1, int(round(forecast_step_sec * fs)))
        forecast_horizon = compute_forecast_horizon(forecast_horizon_sec, forecast_step_sec)

        starts = sliding_window_starts(
            n_points=len(ecg_noisy),
            window_len=window_len,
            stride=stride,
            forecast_horizon=forecast_horizon,
            forecast_step=forecast_step,
        )

        for start in starts:
            end = start + window_len
            target_idx = [end + k * forecast_step for k in range(forecast_horizon)]

            ecg_raw = ecg_noisy[start:end]
            ppg_raw = ppg_noisy[start:end]
            abp_raw = abp_noisy[start:end]
            future_abp = abp_clean[target_idx]
            intervention_win = intervention_traj[start:end]

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
            )

            writer.add(
                arrays={
                    "ecg": ecg_filt,
                    "ppg": ppg_filt,
                    "abp": abp_filled,
                    "future_abp": interpolate_future(future_abp),
                    "intervention": interpolate_future(intervention_win, target_len=window_len),
                },
                scalars={
                    "dataset_source": "synthetic",
                    "case_id": case_id,
                    "window_start_sec": start / fs,
                    "fs": fs,
                    "forecast_step_sec": forecast_step_sec,
                    "forecast_horizon": forecast_horizon,
                    "r_true": float(np.mean(r_traj[start:end])),
                    "c_true": float(np.mean(c_traj[start:end])),
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
                    "scenario": scenario,
                    "simulator_model": simulator_model,
                },
            )

    df_summary = writer.save(output)
    print(f"Wrote {len(writer)} synthetic windows to {output}")
    print(f"Wrote per-window summary to {os.path.splitext(output)[0]}_summary.csv")
    print(f"Valid windows: {int(df_summary['is_valid'].sum())}/{len(df_summary)}")
    return len(writer)


def interpolate_future(x: np.ndarray, target_len: int = None) -> np.ndarray:
    """Fill any NaNs in a short target/intervention slice with simple ffill/bfill."""
    x = np.asarray(x, dtype=np.float64)
    if np.isnan(x).any():
        import pandas as pd

        x = pd.Series(x).interpolate(limit_direction="both").fillna(0.0).to_numpy()
    return x.astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="data/synthetic")
    parser.add_argument("--output", default="data/processed/synthetic_windows.npz")
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--stride-sec", type=float, default=2.0)
    parser.add_argument("--forecast-horizon-sec", type=float, default=15.0)
    parser.add_argument("--forecast-step-sec", type=float, default=1.0)
    args = parser.parse_args()

    preprocess_synthetic(
        input_dir=args.input_dir,
        output=args.output,
        window_sec=args.window_sec,
        stride_sec=args.stride_sec,
        forecast_horizon_sec=args.forecast_horizon_sec,
        forecast_step_sec=args.forecast_step_sec,
    )


if __name__ == "__main__":
    main()
