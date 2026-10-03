"""Generate result plots for the narrowed hemodynamic project."""

import argparse
import glob
import os
import re
from typing import Iterable, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_OUTPUT_DIR = "data/figures"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_").lower()


def _savefig(path: str) -> None:
    plt.tight_layout()
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Saved {path}")


def _read_csv_if_exists(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        print(f"Skipping missing file: {path}")
        return None
    return pd.read_csv(path)


def plot_regime_comparison(
    comparison_csv: str = "data/synthetic/model_comparison_results.csv",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    df = _read_csv_if_exists(comparison_csv)
    if df is None or df.empty:
        return
    if "Regime" not in df.columns or "Model" not in df.columns:
        print(f"Skipping {comparison_csv}: expected columns Model and Regime. Rerun training to regenerate it.")
        return

    metrics = [
        "Test_MAE_mmHg",
        "Test_RMSE_mmHg",
        "Physics_Residual",
        "R_Correlation",
        "C_Correlation",
    ]
    for metric in metrics:
        if metric not in df.columns:
            continue
        pivot = df.pivot(index="Regime", columns="Model", values=metric)
        pivot.plot(kind="bar", figsize=(10, 5), rot=25)
        plt.ylabel(metric)
        plt.title(f"{metric} by Observation Regime")
        plt.grid(axis="y", alpha=0.25)
        _savefig(os.path.join(output_dir, f"regime_comparison_{_safe_name(metric)}.png"))


def plot_identifiability_bars(
    identifiability_csv: str = "data/synthetic/identifiability_ablation_results.csv",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    df = _read_csv_if_exists(identifiability_csv)
    if df is None or df.empty:
        return

    for metrics, suffix, ylabel in [
        (["R_MAE", "C_MAE"], "mae", "Absolute Error"),
        (["R_RelErr_pct", "C_RelErr_pct"], "relative_error", "Relative Error (%)"),
    ]:
        available = [m for m in metrics if m in df.columns]
        if not available:
            continue
        df.plot(x="regime", y=available, kind="bar", figsize=(10, 5), rot=25)
        plt.ylabel(ylabel)
        plt.title(f"Identifiability {ylabel} Across Regimes")
        plt.grid(axis="y", alpha=0.25)
        _savefig(os.path.join(output_dir, f"identifiability_{suffix}.png"))


def plot_prediction_scatter(
    predictions_csv: str = "data/synthetic/test_window_predictions.csv",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    df = _read_csv_if_exists(predictions_csv)
    if df is None or df.empty:
        return

    for model_name, model_df in df.groupby("Model"):
        for regime, sub in model_df.groupby("Regime"):
            for true_col, pred_col, label in [
                ("r_true", "r_pred", "R"),
                ("c_true", "c_pred", "C"),
            ]:
                if true_col not in sub.columns or pred_col not in sub.columns:
                    continue
                x = sub[true_col].to_numpy()
                y = sub[pred_col].to_numpy()
                finite = np.isfinite(x) & np.isfinite(y)
                if finite.sum() < 2:
                    continue
                x, y = x[finite], y[finite]
                lo, hi = min(x.min(), y.min()), max(x.max(), y.max())
                plt.figure(figsize=(5.5, 5.5))
                plt.scatter(x, y, s=12, alpha=0.35)
                plt.plot([lo, hi], [lo, hi], color="black", linewidth=1.0)
                plt.xlabel(f"True {label}")
                plt.ylabel(f"Predicted {label}")
                plt.title(f"{label}: True vs Predicted\n{model_name} | {regime}")
                plt.grid(alpha=0.25)
                fname = f"scatter_{_safe_name(model_name)}_{_safe_name(regime)}_{label.lower()}.png"
                _savefig(os.path.join(output_dir, fname))


def plot_error_distributions(
    predictions_csv: str = "data/synthetic/test_window_predictions.csv",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    df = _read_csv_if_exists(predictions_csv)
    if df is None or df.empty:
        return

    df = df.copy()
    df["r_abs_err"] = np.abs(df["r_pred"] - df["r_true"])
    df["c_abs_err"] = np.abs(df["c_pred"] - df["c_true"])
    metrics = ["forecast_mae_mmHg", "physics_residual", "r_abs_err", "c_abs_err"]
    for metric in metrics:
        if metric not in df.columns:
            continue
        plt.figure(figsize=(11, 5))
        labels = []
        values = []
        for (model, regime), sub in df.groupby(["Model", "Regime"]):
            labels.append(f"{model}\n{regime}")
            values.append(sub[metric].dropna().to_numpy())
        plt.boxplot(values, tick_labels=labels, showfliers=False)
        plt.xticks(rotation=35, ha="right")
        plt.ylabel(metric)
        plt.title(f"{metric} Distribution")
        plt.grid(axis="y", alpha=0.25)
        _savefig(os.path.join(output_dir, f"distribution_{_safe_name(metric)}.png"))


def plot_forecast_overlays(
    examples_npz: str = "data/synthetic/forecast_examples.npz",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    if not os.path.exists(examples_npz):
        print(f"Skipping missing file: {examples_npz}")
        return
    data = np.load(examples_npz, allow_pickle=True)
    forecast_step_sec = float(data["forecast_step_sec"]) if "forecast_step_sec" in data.files else 1.0
    forecast_keys = sorted(k for k in data.files if k.endswith("__forecast"))
    for key in forecast_keys:
        prefix = key.replace("__forecast", "")
        target_key = f"{prefix}__target"
        if target_key not in data.files:
            continue
        forecast = data[key]
        target = data[target_key]
        t = np.arange(len(target)) * forecast_step_sec
        plt.figure(figsize=(7, 4))
        plt.plot(t, target, marker="o", label="True future ABP/MAP")
        plt.plot(t, forecast, marker="s", label="Predicted future ABP/MAP")
        plt.xlabel("Forecast time (s)")
        plt.ylabel("Pressure (mmHg)")
        plt.title(prefix.replace("__", " | "))
        plt.grid(alpha=0.25)
        plt.legend()
        _savefig(os.path.join(output_dir, f"forecast_overlay_{_safe_name(prefix)}.png"))


def plot_signal_quality_summary(
    summary_paths: Optional[List[str]] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    if summary_paths is None:
        summary_paths = sorted(glob.glob("data/processed/*_summary.csv"))
    for path in summary_paths:
        df = _read_csv_if_exists(path)
        if df is None or df.empty:
            continue
        dataset = os.path.basename(path).replace("_summary.csv", "")

        if "is_valid" in df.columns:
            valid_counts = df["is_valid"].astype(bool).value_counts().rename(index={True: "valid", False: "rejected"})
            valid_counts.plot(kind="bar", figsize=(5, 4), rot=0)
            plt.ylabel("Windows")
            plt.title(f"{dataset}: Usable vs Rejected Windows")
            plt.grid(axis="y", alpha=0.25)
            _savefig(os.path.join(output_dir, f"quality_validity_{_safe_name(dataset)}.png"))

        for metric in ["composite_sqi", "heart_rate_bpm", "mean_pat_ms", "map", "pulse_pressure"]:
            if metric not in df.columns:
                continue
            values = df[metric].replace([np.inf, -np.inf], np.nan).dropna()
            if values.empty:
                continue
            plt.figure(figsize=(6, 4))
            plt.hist(values, bins=40, alpha=0.85)
            plt.xlabel(metric)
            plt.ylabel("Windows")
            plt.title(f"{dataset}: {metric} Distribution")
            plt.grid(axis="y", alpha=0.25)
            _savefig(os.path.join(output_dir, f"quality_hist_{_safe_name(dataset)}_{metric}.png"))

        missing_cols = [c for c in ["missing_ecg_fraction", "missing_ppg_fraction", "missing_abp_fraction"] if c in df.columns]
        if missing_cols:
            df[missing_cols].mean().plot(kind="bar", figsize=(6, 4), rot=20)
            plt.ylabel("Mean Missing Fraction")
            plt.title(f"{dataset}: Missingness by Modality")
            plt.grid(axis="y", alpha=0.25)
            _savefig(os.path.join(output_dir, f"missingness_{_safe_name(dataset)}.png"))


def plot_vitaldb_intervention_response(
    vitaldb_npz: str = "data/processed/vitaldb_windows.npz",
    output_dir: str = DEFAULT_OUTPUT_DIR,
    max_examples: int = 6,
) -> None:
    if not os.path.exists(vitaldb_npz):
        print(f"Skipping missing file: {vitaldb_npz}")
        return
    data = np.load(vitaldb_npz, allow_pickle=True)
    if "intervention" not in data.files or "abp" not in data.files:
        print(f"Skipping {vitaldb_npz}: intervention or ABP arrays missing.")
        return

    intervention = data["intervention"]
    abp = data["abp"]
    strength = np.nanmax(intervention, axis=1)
    indices = np.argsort(strength)[::-1]
    indices = [idx for idx in indices if strength[idx] > 0][:max_examples]
    if not indices:
        print("No nonzero intervention windows found for VitalDB plot.")
        return

    fs = float(data["fs"][0]) if "fs" in data.files else 125.0
    t = np.arange(abp.shape[1]) / fs
    for rank, idx in enumerate(indices):
        fig, ax1 = plt.subplots(figsize=(8, 4))
        ax1.plot(t, abp[idx], color="tab:red", label="ABP")
        ax1.set_xlabel("Time in window (s)")
        ax1.set_ylabel("ABP (mmHg)", color="tab:red")
        ax1.tick_params(axis="y", labelcolor="tab:red")
        ax2 = ax1.twinx()
        ax2.plot(t, intervention[idx], color="tab:blue", label="Intervention")
        ax2.set_ylabel("Intervention intensity", color="tab:blue")
        ax2.tick_params(axis="y", labelcolor="tab:blue")
        case = str(data["case_id"][idx]) if "case_id" in data.files else f"window_{idx}"
        start = float(data["window_start_sec"][idx]) if "window_start_sec" in data.files else np.nan
        plt.title(f"VitalDB Intervention Window: {case} at {start:.1f}s")
        fig.tight_layout()
        _savefig(os.path.join(output_dir, f"vitaldb_intervention_response_{rank}_{_safe_name(case)}.png"))


def plot_physics_residual(
    predictions_csv: str = "data/synthetic/test_window_predictions.csv",
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> None:
    df = _read_csv_if_exists(predictions_csv)
    if df is None or df.empty or "physics_residual" not in df.columns:
        return
    pivot = df.groupby(["Model", "Regime"])["physics_residual"].median().reset_index()
    pivot = pivot.pivot(index="Regime", columns="Model", values="physics_residual")
    pivot.plot(kind="bar", figsize=(10, 5), rot=25)
    plt.ylabel("Median Physics Residual")
    plt.title("Physics Residual by Model and Regime")
    plt.grid(axis="y", alpha=0.25)
    _savefig(os.path.join(output_dir, "physics_residual_by_model_regime.png"))


def plot_dataset_role_figure(output_dir: str = DEFAULT_OUTPUT_DIR) -> None:
    plt.figure(figsize=(10, 4))
    ax = plt.gca()
    ax.axis("off")
    boxes = [
        (0.05, 0.55, "Synthetic Simulator\nTrain/test R,C recovery\nKnown interventions\nKnown ground truth"),
        (0.38, 0.55, "MIMIC-BP\nICU waveform feasibility\nSQI, HR, PAT, MAP\nNo true R/C"),
        (0.70, 0.55, "VitalDB\nReal waveforms + interventions\nForecast/intervention response\nNo true R/C"),
        (0.37, 0.12, "Common processed windows\nECG + PPG + ABP + intervention\nfuture ABP target + quality metadata"),
    ]
    for x, y, text in boxes:
        ax.text(
            x,
            y,
            text,
            transform=ax.transAxes,
            fontsize=10,
            va="center",
            ha="left",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#f4f7fb", edgecolor="#30475e"),
        )
    arrows = [
        ((0.22, 0.48), (0.43, 0.25)),
        ((0.52, 0.48), (0.52, 0.25)),
        ((0.80, 0.48), (0.61, 0.25)),
    ]
    for start, end in arrows:
        ax.annotate("", xy=end, xytext=start, xycoords="axes fraction", arrowprops=dict(arrowstyle="->", lw=1.2))
    _savefig(os.path.join(output_dir, "dataset_role_schematic.png"))


def generate_all_plots(output_dir: str = DEFAULT_OUTPUT_DIR) -> None:
    _ensure_dir(output_dir)
    plot_dataset_role_figure(output_dir)
    plot_regime_comparison(output_dir=output_dir)
    plot_identifiability_bars(output_dir=output_dir)
    plot_prediction_scatter(output_dir=output_dir)
    plot_error_distributions(output_dir=output_dir)
    plot_forecast_overlays(output_dir=output_dir)
    plot_physics_residual(output_dir=output_dir)
    plot_signal_quality_summary(output_dir=output_dir)
    plot_vitaldb_intervention_response(output_dir=output_dir)
    try:
        from src.evaluation.validate_simulator import validate_simulator

        validate_simulator(output_dir=os.path.join(output_dir, "simulator_validation"))
    except FileNotFoundError as exc:
        print(f"Skipping simulator validation: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    generate_all_plots(args.output_dir)


if __name__ == "__main__":
    main()
