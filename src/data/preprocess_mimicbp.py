"""
Preprocess the MIMIC-BP raw archive (data/raw/Mimic BP/) into the common
model-ready window schema described in configs/datasets.yaml.

Raw layout (see data/raw/Mimic BP/read_data.py):
    abp/p000188_abp.npy   shape (30, 3750)  -- 30 curated 30-second segments at 125 Hz
    ecg/p000188_ecg.npy   shape (30, 3750)
    ppg/p000188_ppg.npy   shape (30, 3750)
    labels/p000188_labels.npy shape (30, 2)  -- [SBP, DBP] per segment (unused here;
                                                 SBP/DBP are recomputed from the ABP
                                                 waveform itself for consistency with
                                                 the other two datasets)
    train_subjects.txt / val_subjects.txt / test_subjects.txt -- Python list literals
                                                                  of subject IDs

MIMIC-BP has no continuous R/C ground truth and no measured intervention
channel, so r_true/c_true stay NaN and intervention is all zeros.
"""

import argparse
import ast
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

FS_HZ = 125.0


def _read_subject_list(path: str):
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return set()
    return set(str(item) for item in ast.literal_eval(content))


def load_subject_splits(input_dir: str):
    train_ids = _read_subject_list(os.path.join(input_dir, "train_subjects.txt"))
    val_ids = _read_subject_list(os.path.join(input_dir, "val_subjects.txt"))
    test_ids = _read_subject_list(os.path.join(input_dir, "test_subjects.txt"))
    return train_ids, val_ids, test_ids


def preprocess_mimicbp(
    input_dir: str = "data/raw/Mimic BP",
    output: str = "data/processed/mimicbp_windows.npz",
    window_sec: float = 10.0,
    stride_sec: float = 2.0,
    forecast_horizon_sec: float = 15.0,
    forecast_step_sec: float = 1.0,
) -> int:
    abp_files = sorted(glob.glob(os.path.join(input_dir, "abp", "*_abp.npy")))
    if not abp_files:
        raise FileNotFoundError(f"No *_abp.npy files found under {os.path.join(input_dir, 'abp')}")

    train_ids, val_ids, test_ids = load_subject_splits(input_dir)

    window_len = int(round(window_sec * FS_HZ))
    stride = int(round(stride_sec * FS_HZ))
    forecast_step = max(1, int(round(forecast_step_sec * FS_HZ)))
    forecast_horizon = compute_forecast_horizon(forecast_horizon_sec, forecast_step_sec)

    writer = WindowArchiveWriter()
    n_subjects = 0

    for abp_path in abp_files:
        fname = os.path.basename(abp_path)
        pid = fname[: -len("_abp.npy")]

        ecg_path = os.path.join(input_dir, "ecg", f"{pid}_ecg.npy")
        ppg_path = os.path.join(input_dir, "ppg", f"{pid}_ppg.npy")
        if not (os.path.exists(ecg_path) and os.path.exists(ppg_path)):
            print(f"Skipping {pid}: missing ecg/ppg file.")
            continue

        abp_segments = np.load(abp_path).astype(np.float64)
        ecg_segments = np.load(ecg_path).astype(np.float64)
        ppg_segments = np.load(ppg_path).astype(np.float64)
        if not (abp_segments.shape == ecg_segments.shape == ppg_segments.shape):
            print(f"Skipping {pid}: mismatched segment shapes.")
            continue

        if pid in train_ids:
            split = "train"
        elif pid in val_ids:
            split = "val"
        elif pid in test_ids:
            split = "test"
        else:
            split = "unassigned"

        n_subjects += 1
        n_segments = abp_segments.shape[0]

        for seg_idx in range(n_segments):
            ecg_seg = ecg_segments[seg_idx]
            ppg_seg = ppg_segments[seg_idx]
            abp_seg = abp_segments[seg_idx]

            starts = sliding_window_starts(
                n_points=len(ecg_seg),
                window_len=window_len,
                stride=stride,
                forecast_horizon=forecast_horizon,
                forecast_step=forecast_step,
            )
            case_id = f"{pid}_seg{seg_idx:02d}"

            for start in starts:
                end = start + window_len
                target_idx = [end + k * forecast_step for k in range(forecast_horizon)]

                ecg_raw = ecg_seg[start:end]
                ppg_raw = ppg_seg[start:end]
                abp_raw = abp_seg[start:end]
                future_abp = abp_seg[target_idx]

                miss_ecg = missing_fraction(ecg_raw)
                miss_ppg = missing_fraction(ppg_raw)
                miss_abp = missing_fraction(abp_raw)

                ecg_filt, ppg_filt, abp_filled = prepare_window_channels(ecg_raw, ppg_raw, abp_raw, FS_HZ)
                metrics = compute_window_metrics(ecg_filt, ppg_filt, abp_raw, FS_HZ)

                is_valid = bool(
                    metrics["quality_is_valid"]
                    and metrics["abp_physiology_valid"]
                    and miss_ecg < 0.3
                    and miss_ppg < 0.3
                    and miss_abp < 0.3
                    and full_range_physiologically_plausible(abp_raw)
                    and full_range_physiologically_plausible(future_abp)
                )

                future_abp_filled = future_abp
                if np.isnan(future_abp_filled).any():
                    import pandas as pd

                    future_abp_filled = (
                        pd.Series(future_abp_filled).interpolate(limit_direction="both").fillna(0.0).to_numpy()
                    )

                writer.add(
                    arrays={
                        "ecg": ecg_filt,
                        "ppg": ppg_filt,
                        "abp": abp_filled,
                        "future_abp": future_abp_filled.astype(np.float32),
                        "intervention": np.zeros(window_len, dtype=np.float32),
                    },
                    scalars={
                        "dataset_source": "mimicbp",
                        "case_id": case_id,
                        "window_start_sec": start / FS_HZ,
                        "fs": FS_HZ,
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
                        "scenario": "",
                        "simulator_model": "",
                        "split": split,
                        "subject_id": pid,
                    },
                )

        if n_subjects % 200 == 0:
            print(f"Processed {n_subjects} subjects, {len(writer)} windows so far...")

    df_summary = writer.save(output)
    print(f"Processed {n_subjects} MIMIC-BP subjects.")
    print(f"Wrote {len(writer)} windows to {output}")
    print(f"Wrote per-window summary to {os.path.splitext(output)[0]}_summary.csv")
    print(f"Valid windows: {int(df_summary['is_valid'].sum())}/{len(df_summary)}")
    if "split" in df_summary.columns:
        print(df_summary["split"].value_counts())
    return len(writer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="data/raw/Mimic BP")
    parser.add_argument("--output", default="data/processed/mimicbp_windows.npz")
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--stride-sec", type=float, default=2.0)
    parser.add_argument("--forecast-horizon-sec", type=float, default=15.0)
    parser.add_argument("--forecast-step-sec", type=float, default=1.0)
    args = parser.parse_args()

    preprocess_mimicbp(
        input_dir=args.input_dir,
        output=args.output,
        window_sec=args.window_sec,
        stride_sec=args.stride_sec,
        forecast_horizon_sec=args.forecast_horizon_sec,
        forecast_step_sec=args.forecast_step_sec,
    )


if __name__ == "__main__":
    main()
