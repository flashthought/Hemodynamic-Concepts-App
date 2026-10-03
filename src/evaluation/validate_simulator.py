"""Validate synthetic simulator distributions against processed real datasets."""

import argparse
import os
from typing import Dict, Iterable, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_METRICS = (
    "heart_rate_bpm",
    "map",
    "sbp",
    "dbp",
    "pulse_pressure",
    "mean_pat_ms",
    "missing_ecg_fraction",
    "missing_ppg_fraction",
    "missing_abp_fraction",
)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def read_summary(path: str, dataset_label: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        print(f"Skipping missing summary: {path}")
        return None
    df = pd.read_csv(path)
    if df.empty:
        print(f"Skipping empty summary: {path}")
        return None
    df = df.copy()
    df["validation_dataset"] = dataset_label
    return df


def finite_values(df: pd.DataFrame, metric: str) -> np.ndarray:
    if metric not in df.columns:
        return np.array([], dtype=np.float32)
    return df[metric].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=np.float32)


def distribution_rows(frames: Dict[str, pd.DataFrame], metrics: Iterable[str]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for dataset, df in frames.items():
        for metric in metrics:
            values = finite_values(df, metric)
            if values.size == 0:
                continue
            rows.append(
                {
                    "dataset": dataset,
                    "metric": metric,
                    "n": int(values.size),
                    "mean": float(np.mean(values)),
                    "std": float(np.std(values)),
                    "median": float(np.median(values)),
                    "q05": float(np.quantile(values, 0.05)),
                    "q95": float(np.quantile(values, 0.95)),
                }
            )
    return rows


def standardized_mean_difference(synth: np.ndarray, real: np.ndarray) -> float:
    pooled = np.sqrt(0.5 * (np.var(synth) + np.var(real)))
    if pooled <= 1e-8:
        return 0.0
    return float((np.mean(synth) - np.mean(real)) / pooled)


def overlap_rows(frames: Dict[str, pd.DataFrame], metrics: Iterable[str]) -> List[Dict[str, float]]:
    if "synthetic" not in frames:
        return []
    rows: List[Dict[str, float]] = []
    synth_df = frames["synthetic"]
    for dataset, df in frames.items():
        if dataset == "synthetic":
            continue
        for metric in metrics:
            synth = finite_values(synth_df, metric)
            real = finite_values(df, metric)
            if synth.size < 5 or real.size < 5:
                continue
            synth_q05, synth_q95 = np.quantile(synth, [0.05, 0.95])
            real_q05, real_q95 = np.quantile(real, [0.05, 0.95])
            overlap_low = max(synth_q05, real_q05)
            overlap_high = min(synth_q95, real_q95)
            synth_width = max(1e-8, synth_q95 - synth_q05)
            real_width = max(1e-8, real_q95 - real_q05)
            overlap_fraction = max(0.0, overlap_high - overlap_low) / min(synth_width, real_width)
            rows.append(
                {
                    "real_dataset": dataset,
                    "metric": metric,
                    "standardized_mean_difference": standardized_mean_difference(synth, real),
                    "q05_q95_overlap_fraction": float(overlap_fraction),
                    "synthetic_mean": float(np.mean(synth)),
                    "real_mean": float(np.mean(real)),
                    "synthetic_n": int(synth.size),
                    "real_n": int(real.size),
                }
            )
    return rows


def plot_metric_overlay(
    frames: Dict[str, pd.DataFrame],
    metric: str,
    output_dir: str,
) -> None:
    values_by_dataset = {
        dataset: finite_values(df, metric)
        for dataset, df in frames.items()
        if finite_values(df, metric).size > 0
    }
    if len(values_by_dataset) < 2:
        return

    plt.figure(figsize=(7, 4))
    for dataset, values in values_by_dataset.items():
        lo, hi = np.quantile(values, [0.01, 0.99])
        clipped = values[(values >= lo) & (values <= hi)]
        plt.hist(clipped, bins=40, density=True, alpha=0.35, label=dataset)
    plt.xlabel(metric)
    plt.ylabel("Density")
    plt.title(f"Synthetic vs Real Distribution: {metric}")
    plt.grid(axis="y", alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"simulator_validation_{metric}.png"), dpi=200)
    plt.close()


def plot_scenario_summary(cohort_summary_path: str, output_dir: str) -> None:
    if not os.path.exists(cohort_summary_path):
        return
    df = pd.read_csv(cohort_summary_path)
    if df.empty or "scenario" not in df.columns:
        return

    for metric in ["mean_map", "mean_r", "mean_c", "mean_pulse_pressure"]:
        if metric not in df.columns:
            continue
        plt.figure(figsize=(9, 4))
        df.boxplot(column=metric, by="scenario", rot=30)
        plt.title(f"Synthetic Scenario Distribution: {metric}")
        plt.suptitle("")
        plt.ylabel(metric)
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, f"simulator_scenario_{metric}.png"), dpi=200)
        plt.close()


def validate_simulator(
    synthetic_summary: str = "data/processed/synthetic_windows_summary.csv",
    mimic_summary: str = "data/processed/mimicbp_windows_summary.csv",
    vitaldb_summary: str = "data/processed/vitaldb_windows_summary.csv",
    cohort_summary: str = "data/synthetic/cohort_summary.csv",
    output_dir: str = "data/figures/simulator_validation",
) -> None:
    """Create validation tables and distribution plots."""
    ensure_dir(output_dir)
    frames: Dict[str, pd.DataFrame] = {}
    for label, path in [
        ("synthetic", synthetic_summary),
        ("mimicbp", mimic_summary),
        ("vitaldb", vitaldb_summary),
    ]:
        df = read_summary(path, label)
        if df is not None:
            if "is_valid" in df.columns:
                df = df[df["is_valid"].astype(bool)].copy()
            frames[label] = df

    if "synthetic" not in frames:
        raise FileNotFoundError("Synthetic processed summary is required for simulator validation.")

    metrics = [metric for metric in DEFAULT_METRICS if any(metric in df.columns for df in frames.values())]
    distribution_df = pd.DataFrame(distribution_rows(frames, metrics))
    overlap_df = pd.DataFrame(overlap_rows(frames, metrics))

    distribution_path = os.path.join(output_dir, "simulator_distribution_summary.csv")
    overlap_path = os.path.join(output_dir, "simulator_real_overlap_summary.csv")
    distribution_df.to_csv(distribution_path, index=False)
    overlap_df.to_csv(overlap_path, index=False)

    for metric in metrics:
        plot_metric_overlay(frames, metric, output_dir)
    plot_scenario_summary(cohort_summary, output_dir)

    print(f"Saved simulator distribution summary to {distribution_path}")
    print(f"Saved simulator-real overlap summary to {overlap_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-summary", default="data/processed/synthetic_windows_summary.csv")
    parser.add_argument("--mimic-summary", default="data/processed/mimicbp_windows_summary.csv")
    parser.add_argument("--vitaldb-summary", default="data/processed/vitaldb_windows_summary.csv")
    parser.add_argument("--cohort-summary", default="data/synthetic/cohort_summary.csv")
    parser.add_argument("--output-dir", default="data/figures/simulator_validation")
    args = parser.parse_args()

    validate_simulator(
        synthetic_summary=args.synthetic_summary,
        mimic_summary=args.mimic_summary,
        vitaldb_summary=args.vitaldb_summary,
        cohort_summary=args.cohort_summary,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
