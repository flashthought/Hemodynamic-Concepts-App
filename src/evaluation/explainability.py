"""Integrated-gradients explanations for a trained hemodynamic model."""

import argparse
import os
from typing import Tuple

import numpy as np
import torch
import matplotlib.pyplot as plt

from src.models.train import CompleteModelPipeline


def resolve_device(device_name: str) -> torch.device:
    if device_name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_name)


def integrated_gradients(
    model: torch.nn.Module,
    ecg: torch.Tensor,
    ppg: torch.Tensor,
    abp: torch.Tensor = None,
    intervention: torch.Tensor = None,
    target_idx: str = "forecast",
    steps: int = 50,
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute ECG and PPG integrated gradients while holding ABP/intervention fixed."""
    baseline_ecg = torch.zeros_like(ecg)
    baseline_ppg = torch.zeros_like(ppg)
    alphas = torch.linspace(0, 1, steps=steps, device=ecg.device).view(steps, 1, 1)

    interp_ecg = baseline_ecg + alphas * (ecg - baseline_ecg)
    interp_ppg = baseline_ppg + alphas * (ppg - baseline_ppg)
    interp_ecg.requires_grad_(True)
    interp_ppg.requires_grad_(True)

    abp_rep = abp.expand(steps, -1, -1) if abp is not None else None
    intervention_rep = intervention.expand(steps, -1, -1) if intervention is not None else None

    preds = model(
        interp_ecg,
        interp_ppg,
        abp=abp_rep,
        intervention=intervention_rep,
        regime="ppg_ecg_abp_intervention",
    )
    if target_idx == "forecast":
        output = preds["forecast"].sum()
    elif target_idx == "R":
        output = preds["R"].sum()
    elif target_idx == "C":
        output = preds["C"].sum()
    else:
        raise ValueError(f"Unknown target: {target_idx}")

    model.zero_grad(set_to_none=True)
    output.backward()

    avg_grad_ecg = interp_ecg.grad.mean(dim=0, keepdim=True)
    avg_grad_ppg = interp_ppg.grad.mean(dim=0, keepdim=True)
    ig_ecg = (ecg - baseline_ecg) * avg_grad_ecg
    ig_ppg = (ppg - baseline_ppg) * avg_grad_ppg

    return (
        ig_ecg.detach().cpu().numpy()[0, 0],
        ig_ppg.detach().cpu().numpy()[0, 0],
    )


def choose_sample(archive_path: str, source: str, sample_index: int):
    data = dict(np.load(archive_path, allow_pickle=True).items())
    mask = np.array([str(src) == source for src in data["dataset_source"]], dtype=bool)
    if "is_valid" in data:
        mask &= data["is_valid"].astype(bool)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        raise ValueError(f"No valid {source} windows found in {archive_path}")
    idx = int(indices[min(sample_index, len(indices) - 1)])
    return data, idx


def plot_attributions(
    ecg: np.ndarray,
    ppg: np.ndarray,
    ig_ecg: np.ndarray,
    ig_ppg: np.ndarray,
    fs: float,
    save_path: str,
    title: str,
) -> None:
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    t = np.arange(len(ecg), dtype=np.float32) / float(fs)
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    axes[0].plot(t, ecg, color="tab:blue", linewidth=1.0, label="ECG")
    ax0 = axes[0].twinx()
    ax0.fill_between(t, 0, ig_ecg / (np.max(np.abs(ig_ecg)) + 1e-8), color="tab:red", alpha=0.25)
    axes[0].set_ylabel("ECG")
    ax0.set_ylabel("Attribution")
    axes[0].set_title(f"{title}: ECG Attribution")

    axes[1].plot(t, ppg, color="tab:green", linewidth=1.0, label="PPG")
    ax1 = axes[1].twinx()
    ax1.fill_between(t, 0, ig_ppg / (np.max(np.abs(ig_ppg)) + 1e-8), color="tab:red", alpha=0.25)
    axes[1].set_ylabel("PPG")
    ax1.set_ylabel("Attribution")
    axes[1].set_title(f"{title}: PPG Attribution")
    axes[1].set_xlabel("Time (sec)")

    fig.tight_layout()
    fig.savefig(save_path, dpi=300)
    plt.close(fig)
    print(f"Saved explainability map to {save_path}")


def run_explainability(
    model_path: str,
    archive_path: str,
    output_dir: str,
    source: str,
    sample_index: int,
    target: str,
    steps: int,
    device_name: str,
) -> None:
    device = resolve_device(device_name)
    data, idx = choose_sample(archive_path, source=source, sample_index=sample_index)
    target_horizon = int(np.asarray(data["future_abp"][idx]).shape[0])

    model = CompleteModelPipeline(forecast_horizon=target_horizon).to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    ecg = torch.tensor(data["ecg"][idx], dtype=torch.float32, device=device).view(1, 1, -1)
    ppg = torch.tensor(data["ppg"][idx], dtype=torch.float32, device=device).view(1, 1, -1)
    abp = torch.tensor(data["abp"][idx], dtype=torch.float32, device=device).view(1, 1, -1)
    intervention = torch.tensor(data["intervention"][idx], dtype=torch.float32, device=device).view(1, 1, -1)

    ig_ecg, ig_ppg = integrated_gradients(
        model,
        ecg,
        ppg,
        abp=abp,
        intervention=intervention,
        target_idx=target,
        steps=steps,
    )

    case_id = str(data["case_id"][idx])
    start = float(data["window_start_sec"][idx])
    fs = float(data["fs"][idx]) if "fs" in data else 125.0
    title = f"{case_id} at {start:.1f}s, target={target}"
    save_path = os.path.join(output_dir, f"ig_{source}_{case_id}_{int(start)}_{target}.png")
    plot_attributions(
        ecg.detach().cpu().numpy()[0, 0],
        ppg.detach().cpu().numpy()[0, 0],
        ig_ecg,
        ig_ppg,
        fs=fs,
        save_path=save_path,
        title=title,
    )

    meta_path = os.path.join(output_dir, "ig_metadata.csv")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("model_path,archive_path,source,case_id,window_start_sec,target,steps\n")
        f.write(f"{model_path},{archive_path},{source},{case_id},{start},{target},{steps}\n")
    print(f"Saved explainability metadata to {meta_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="data/models/physics-informed_real_finetuned.pth")
    parser.add_argument("--fallback-model", default="data/models/physics-informed_model.pth")
    parser.add_argument("--archive", default="data/processed/synthetic_windows.npz")
    parser.add_argument("--output-dir", default="data/synthetic/explainability")
    parser.add_argument("--source", default="synthetic")
    parser.add_argument("--sample-index", type=int, default=0)
    parser.add_argument("--target", choices=["forecast", "R", "C"], default="forecast")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    model_path = args.model if os.path.exists(args.model) else args.fallback_model
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"No trained model checkpoint found: {args.model} or {args.fallback_model}")

    run_explainability(
        model_path=model_path,
        archive_path=args.archive,
        output_dir=args.output_dir,
        source=args.source,
        sample_index=args.sample_index,
        target=args.target,
        steps=args.steps,
        device_name=args.device,
    )


if __name__ == "__main__":
    main()
