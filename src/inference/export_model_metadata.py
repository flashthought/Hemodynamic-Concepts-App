"""Write deployment metadata beside a trained checkpoint."""

import argparse
import json
import os
from datetime import datetime, timezone


def export_metadata(
    output: str,
    model_version: str,
    model_path: str,
    target_fs: float,
    window_sec: float,
    forecast_horizon_steps: int,
    forecast_step_sec: float,
) -> None:
    payload = {
        "model_version": model_version,
        "model_path": model_path,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target_fs": float(target_fs),
        "window_sec": float(window_sec),
        "window_samples": int(round(float(target_fs) * float(window_sec))),
        "forecast_horizon_steps": int(forecast_horizon_steps),
        "forecast_step_sec": float(forecast_step_sec),
        "required_channels": ["ppg"],
        "optional_channels": ["ecg", "abp", "intervention"],
        "outputs": [
            "forecast_abp",
            "forecast_map",
            "r_estimate",
            "c_estimate",
            "quality",
            "warnings",
        ],
        "intended_use": (
            "Research decision-support component for short-horizon hemodynamic trend "
            "forecasting from patient-monitor waveforms. Not an autonomous diagnostic "
            "or treatment-prescribing device."
        ),
    }
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote model metadata to {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/models/model_metadata.json")
    parser.add_argument("--model-version", default="physics-informed-real-finetuned-v1")
    parser.add_argument("--model-path", default="data/models/physics-informed_real_finetuned.pth")
    parser.add_argument("--target-fs", type=float, default=125.0)
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--forecast-horizon-steps", type=int, default=15)
    parser.add_argument("--forecast-step-sec", type=float, default=1.0)
    args = parser.parse_args()

    export_metadata(
        output=args.output,
        model_version=args.model_version,
        model_path=args.model_path,
        target_fs=args.target_fs,
        window_sec=args.window_sec,
        forecast_horizon_steps=args.forecast_horizon_steps,
        forecast_step_sec=args.forecast_step_sec,
    )


if __name__ == "__main__":
    main()
