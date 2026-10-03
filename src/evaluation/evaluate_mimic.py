"""Evaluate a trained model on processed MIMIC-BP windows."""

import argparse
import os
from typing import Optional

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset

from src.models.train import CompleteModelPipeline, move_batch_to_device


class ProcessedRealDataset(Dataset):
    """Small evaluation dataset for preprocessed real-window archives."""

    def __init__(self, archive_path: str, source: str, split: Optional[str] = None, valid_only: bool = True):
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Processed archive not found: {archive_path}")
        # Materialize into a plain dict: NpzFile re-decompresses an array from the
        # zip on every single key access, so indexing self.data[key][i] per sample
        # inside __getitem__ would otherwise re-decompress full arrays every call.
        self.data = dict(np.load(archive_path, allow_pickle=True).items())

        mask = np.array([str(src) == source for src in self.data["dataset_source"]], dtype=bool)
        if valid_only and "is_valid" in self.data:
            mask &= self.data["is_valid"].astype(bool)
        if split is not None:
            if "split" not in self.data:
                raise ValueError(f"{archive_path} does not contain split metadata.")
            mask &= np.array([str(value) == split for value in self.data["split"]], dtype=bool)

        self.indices = np.where(mask)[0]
        if len(self.indices) == 0:
            raise ValueError("No matching processed MIMIC-BP windows were found.")

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


def evaluate_on_mimic(
    model_path: str,
    archive_path: str = "data/processed/mimicbp_windows.npz",
    split: Optional[str] = "test",
    batch_size: int = 64,
    device_name: str = "auto",
) -> pd.DataFrame:
    """Evaluate ABP forecasting on processed MIMIC-BP windows."""
    if device_name == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(device_name)

    ds = ProcessedRealDataset(archive_path, source="mimicbp", split=split)
    forecast_horizon = int(ds[0]["target"].shape[0])
    model = CompleteModelPipeline(forecast_horizon=forecast_horizon).to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    rows = []
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False)
    with torch.no_grad():
        for batch in loader:
            batch = move_batch_to_device(batch, device)
            preds = model(
                batch["ecg"],
                batch["ppg"],
                abp=batch["abp"],
                intervention=None,
                regime="ppg_ecg_abp",
            )
            forecast = preds["forecast"]
            target = batch["target"]
            error = forecast - target
            mae = torch.abs(error).mean(dim=1).detach().cpu().numpy()
            rmse = torch.sqrt(torch.mean(error ** 2, dim=1)).detach().cpu().numpy()
            case_ids = batch["case_id"]
            starts = batch["window_start_sec"].detach().cpu().numpy()
            for case_id, start, row_mae, row_rmse in zip(case_ids, starts, mae, rmse):
                rows.append(
                    {
                        "case_id": str(case_id),
                        "window_start_sec": float(start),
                        "mae_mmHg": float(row_mae),
                        "rmse_mmHg": float(row_rmse),
                    }
                )

    result = pd.DataFrame(rows)
    print("MIMIC-BP processed-window evaluation")
    print(f"Windows: {len(result)}")
    print(f"MAE : {result['mae_mmHg'].mean():.2f} mmHg")
    print(f"RMSE: {result['rmse_mmHg'].mean():.2f} mmHg")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="data/models/physics-informed_model.pth")
    parser.add_argument("--archive", default="data/processed/mimicbp_windows.npz")
    parser.add_argument("--split", default="test")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--output", default="data/processed/mimicbp_evaluation.csv")
    args = parser.parse_args()

    df = evaluate_on_mimic(
        model_path=args.model,
        archive_path=args.archive,
        split=args.split,
        batch_size=args.batch_size,
        device_name=args.device,
    )
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Saved per-window results to {args.output}")


if __name__ == "__main__":
    main()
