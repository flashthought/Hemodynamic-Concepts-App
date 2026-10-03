"""Evaluate a trained model on processed VitalDB windows."""

import argparse
import os

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset

from src.models.train import CompleteModelPipeline, move_batch_to_device


def load_case_file(path: str):
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(f"Case list not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


class ProcessedVitalDataset(Dataset):
    """Small evaluation dataset for the processed VitalDB archive."""

    def __init__(
        self,
        archive_path: str,
        valid_only: bool = True,
        include_cases=None,
        exclude_cases=None,
    ):
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Processed archive not found: {archive_path}")
        # Materialize into a plain dict: NpzFile re-decompresses an array from the
        # zip on every single key access, so indexing self.data[key][i] per sample
        # inside __getitem__ would otherwise re-decompress full arrays every call.
        self.data = dict(np.load(archive_path, allow_pickle=True).items())
        mask = np.array([str(src) == "vitaldb" for src in self.data["dataset_source"]], dtype=bool)
        if valid_only and "is_valid" in self.data:
            mask &= self.data["is_valid"].astype(bool)
        if include_cases is not None:
            mask &= np.array([str(case_id) in include_cases for case_id in self.data["case_id"]], dtype=bool)
        if exclude_cases is not None:
            mask &= np.array([str(case_id) not in exclude_cases for case_id in self.data["case_id"]], dtype=bool)
        self.indices = np.where(mask)[0]
        if len(self.indices) == 0:
            raise ValueError("No valid processed VitalDB windows were found.")

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, idx: int):
        i = self.indices[idx]
        return {
            "ecg": torch.tensor(self.data["ecg"][i], dtype=torch.float32).unsqueeze(0),
            "ppg": torch.tensor(self.data["ppg"][i], dtype=torch.float32).unsqueeze(0),
            "abp": torch.tensor(self.data["abp"][i], dtype=torch.float32).unsqueeze(0),
            "intervention": torch.tensor(self.data["intervention"][i], dtype=torch.float32).unsqueeze(0),
            "target": torch.tensor(self.data["future_abp"][i], dtype=torch.float32),
            "case_id": str(self.data["case_id"][i]),
            "window_start_sec": torch.tensor(float(self.data["window_start_sec"][i]), dtype=torch.float32),
        }


def evaluate_on_vitaldb(
    model_path: str,
    archive_path: str = "data/processed/vitaldb_windows.npz",
    batch_size: int = 64,
    device_name: str = "auto",
    include_cases_file: str = None,
    exclude_cases_file: str = None,
) -> pd.DataFrame:
    """Evaluate ABP forecasting on processed VitalDB windows."""
    if device_name == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(device_name)

    include_cases = load_case_file(include_cases_file)
    exclude_cases = load_case_file(exclude_cases_file)
    ds = ProcessedVitalDataset(
        archive_path,
        include_cases=include_cases,
        exclude_cases=exclude_cases,
    )
    forecast_horizon = int(ds[0]["target"].shape[0])
    model = CompleteModelPipeline(forecast_horizon=forecast_horizon).to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    rows = []
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for batch in loader:
            batch = move_batch_to_device(batch, device)
            has_intervention = batch["intervention"].amax(dim=(1, 2)) > 0
            preds = model(
                batch["ecg"],
                batch["ppg"],
                abp=batch["abp"],
                intervention=batch["intervention"],
                regime="ppg_ecg_abp_intervention",
            )
            forecast = preds["forecast"]
            target = batch["target"]
            error = forecast - target
            mae = torch.abs(error).mean(dim=1).detach().cpu().numpy()
            rmse = torch.sqrt(torch.mean(error ** 2, dim=1)).detach().cpu().numpy()
            case_ids = batch["case_id"]
            starts = batch["window_start_sec"].detach().cpu().numpy()
            intervention_flags = has_intervention.detach().cpu().numpy()
            for case_id, start, row_mae, row_rmse, flag in zip(case_ids, starts, mae, rmse, intervention_flags):
                rows.append(
                    {
                        "case_id": str(case_id),
                        "window_start_sec": float(start),
                        "mae_mmHg": float(row_mae),
                        "rmse_mmHg": float(row_rmse),
                        "has_intervention": bool(flag),
                    }
                )

    result = pd.DataFrame(rows)
    print("VitalDB processed-window evaluation")
    print(f"Windows: {len(result)}")
    print(f"MAE : {result['mae_mmHg'].mean():.2f} mmHg")
    print(f"RMSE: {result['rmse_mmHg'].mean():.2f} mmHg")
    print(f"Intervention windows: {int(result['has_intervention'].sum())}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="data/models/physics-informed_model.pth")
    parser.add_argument("--archive", default="data/processed/vitaldb_windows.npz")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--include-cases-file", default=None)
    parser.add_argument("--exclude-cases-file", default=None)
    parser.add_argument("--output", default="data/processed/vitaldb_evaluation.csv")
    args = parser.parse_args()

    df = evaluate_on_vitaldb(
        model_path=args.model,
        archive_path=args.archive,
        batch_size=args.batch_size,
        device_name=args.device,
        include_cases_file=args.include_cases_file,
        exclude_cases_file=args.exclude_cases_file,
    )
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Saved per-window results to {args.output}")


if __name__ == "__main__":
    main()
