"""Export trained checkpoints into app-importable deployment formats."""

import argparse
import json
import os
from typing import Dict, Iterable, Tuple

import torch
import torch.nn as nn

from src.models.train import CompleteModelPipeline


class DeploymentModelWrapper(nn.Module):
    """
    Stable tensor-only wrapper for app deployment.

    The training model returns a dictionary, which is convenient in Python but
    less convenient for system apps. This wrapper returns a fixed tuple:
    forecast_abp, R, C.
    """

    def __init__(self, model: CompleteModelPipeline):
        super().__init__()
        self.model = model

    def forward(
        self,
        ecg: torch.Tensor,
        ppg: torch.Tensor,
        abp: torch.Tensor,
        intervention: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        outputs = self.model(
            ecg,
            ppg,
            abp=abp,
            intervention=intervention,
            regime="ppg_ecg_abp_intervention",
        )
        return outputs["forecast"], outputs["R"], outputs["C"]


def load_metadata(metadata_path: str) -> Dict[str, object]:
    if metadata_path and os.path.exists(metadata_path):
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_metadata(metadata_path: str, updates: Dict[str, object]) -> None:
    metadata = load_metadata(metadata_path)
    metadata.update(updates)
    os.makedirs(os.path.dirname(metadata_path) or ".", exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def export_torchscript(wrapper: nn.Module, example_inputs, output_path: str) -> None:
    wrapper.eval()
    with torch.no_grad():
        traced = torch.jit.trace(wrapper, example_inputs, strict=False)
        traced = torch.jit.freeze(traced)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    traced.save(output_path)
    print(f"Saved TorchScript model to {output_path}")


def export_onnx(wrapper: nn.Module, example_inputs, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    torch.onnx.export(
        wrapper,
        example_inputs,
        output_path,
        input_names=["ecg", "ppg", "abp", "intervention"],
        output_names=["forecast_abp", "R", "C"],
        dynamic_axes={
            "ecg": {0: "batch"},
            "ppg": {0: "batch"},
            "abp": {0: "batch"},
            "intervention": {0: "batch"},
            "forecast_abp": {0: "batch"},
            "R": {0: "batch"},
            "C": {0: "batch"},
        },
        opset_version=17,
    )
    print(f"Saved ONNX model to {output_path}")


def export_deployment_model(
    checkpoint_path: str,
    output_dir: str,
    metadata_path: str,
    formats: Iterable[str],
    target_fs: float,
    window_sec: float,
    forecast_horizon: int,
    device_name: str,
) -> Dict[str, str]:
    device = torch.device("cuda" if device_name == "auto" and torch.cuda.is_available() else "cpu")
    if device_name != "auto":
        device = torch.device(device_name)

    model = CompleteModelPipeline(forecast_horizon=forecast_horizon).to(device)
    model.load_state_dict(torch.load(checkpoint_path, map_location=device))
    model.eval()
    wrapper = DeploymentModelWrapper(model).to(device).eval()

    n_samples = int(round(float(target_fs) * float(window_sec)))
    example_inputs = (
        torch.zeros(1, 1, n_samples, dtype=torch.float32, device=device),
        torch.zeros(1, 1, n_samples, dtype=torch.float32, device=device),
        torch.zeros(1, 1, n_samples, dtype=torch.float32, device=device),
        torch.zeros(1, 1, n_samples, dtype=torch.float32, device=device),
    )

    exported: Dict[str, str] = {}
    os.makedirs(output_dir, exist_ok=True)
    requested = {fmt.strip().lower() for fmt in formats}

    if "torchscript" in requested or "pt" in requested:
        path = os.path.join(output_dir, "physics_informed_hemo.pt")
        export_torchscript(wrapper, example_inputs, path)
        exported["torchscript"] = path

    if "onnx" in requested:
        path = os.path.join(output_dir, "physics_informed_hemo.onnx")
        try:
            export_onnx(wrapper, example_inputs, path)
            exported["onnx"] = path
        except Exception as exc:
            print(f"ONNX export skipped/failed: {exc}")

    write_metadata(
        metadata_path,
        {
            "checkpoint_path": checkpoint_path,
            "deployment_exports": exported,
            "deployment_input_order": ["ecg", "ppg", "abp", "intervention"],
            "deployment_output_order": ["forecast_abp", "R", "C"],
            "deployment_input_shape": [1, 1, n_samples],
            "deployment_output_forecast_steps": forecast_horizon,
        },
    )
    return exported


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="data/models/physics-informed_real_finetuned.pth")
    parser.add_argument("--output-dir", default="data/models/deployment")
    parser.add_argument("--metadata", default="data/models/model_metadata.json")
    parser.add_argument("--formats", default="torchscript,onnx")
    parser.add_argument("--target-fs", type=float, default=125.0)
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--forecast-horizon", type=int, default=15)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    export_deployment_model(
        checkpoint_path=args.checkpoint,
        output_dir=args.output_dir,
        metadata_path=args.metadata,
        formats=args.formats.split(","),
        target_fs=args.target_fs,
        window_sec=args.window_sec,
        forecast_horizon=args.forecast_horizon,
        device_name=args.device,
    )


if __name__ == "__main__":
    main()
