"""Fine-tune the synthetic-trained model on real processed windows."""

import argparse
import os
from typing import Dict, List, Optional, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, ConcatDataset

from src.models.train import CompleteModelPipeline, move_batch_to_device


def resolve_device(device_name: str) -> torch.device:
    if device_name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_name)


def split_case_ids(case_ids: Sequence[str], train_frac: float = 0.70, val_frac: float = 0.15):
    unique = sorted(set(str(case_id) for case_id in case_ids))
    if len(unique) < 3:
        raise ValueError("At least three cases are required for case-level splitting.")
    n_train = max(1, int(round(len(unique) * train_frac)))
    n_val = max(1, int(round(len(unique) * val_frac)))
    if n_train + n_val >= len(unique):
        n_train = max(1, len(unique) - 2)
        n_val = 1
    return unique[:n_train], unique[n_train:n_train + n_val], unique[n_train + n_val:]


class RealWindowDataset(Dataset):
    def __init__(
        self,
        archive_path: str,
        source: str,
        split: Optional[str] = None,
        case_ids: Optional[Sequence[str]] = None,
        max_windows: Optional[int] = None,
        seed: int = 42,
    ):
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Processed archive not found: {archive_path}")
        self.data = dict(np.load(archive_path, allow_pickle=True).items())
        mask = np.array([str(src) == source for src in self.data["dataset_source"]], dtype=bool)
        if "is_valid" in self.data:
            mask &= self.data["is_valid"].astype(bool)
        if split is not None:
            if "split" not in self.data:
                raise ValueError(f"{archive_path} does not contain split metadata.")
            mask &= np.array([str(value) == split for value in self.data["split"]], dtype=bool)
        if case_ids is not None:
            allowed = set(str(case_id) for case_id in case_ids)
            mask &= np.array([str(case_id) in allowed for case_id in self.data["case_id"]], dtype=bool)

        indices = np.where(mask)[0]
        if max_windows is not None and len(indices) > max_windows:
            rng = np.random.default_rng(seed)
            indices = np.sort(rng.choice(indices, size=max_windows, replace=False))
        if len(indices) == 0:
            raise ValueError(f"No windows selected from {archive_path} for {source}.")
        self.indices = indices
        self.source = source

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, idx):
        i = self.indices[idx]
        return {
            "ecg": torch.tensor(self.data["ecg"][i], dtype=torch.float32).unsqueeze(0),
            "ppg": torch.tensor(self.data["ppg"][i], dtype=torch.float32).unsqueeze(0),
            "abp": torch.tensor(self.data["abp"][i], dtype=torch.float32).unsqueeze(0),
            "intervention": torch.tensor(self.data["intervention"][i], dtype=torch.float32).unsqueeze(0),
            "target": torch.tensor(self.data["future_abp"][i], dtype=torch.float32),
            "case_id": str(self.data["case_id"][i]),
        }


def archive_case_ids(archive_path: str, source: str) -> List[str]:
    data = dict(np.load(archive_path, allow_pickle=True).items())
    mask = np.array([str(src) == source for src in data["dataset_source"]], dtype=bool)
    if "is_valid" in data:
        mask &= data["is_valid"].astype(bool)
    return [str(case_id) for case_id in data["case_id"][mask]]


def evaluate_mae(model, loader, device) -> float:
    model.eval()
    total, n = 0.0, 0
    with torch.no_grad():
        for batch in loader:
            batch = move_batch_to_device(batch, device)
            preds = model(
                batch["ecg"],
                batch["ppg"],
                abp=batch["abp"],
                intervention=batch["intervention"],
                regime="ppg_ecg_abp_intervention",
            )
            mae = torch.abs(preds["forecast"] - batch["target"]).mean()
            total += float(mae.item()) * len(batch["target"])
            n += len(batch["target"])
    return total / max(n, 1)


def fine_tune_real(
    base_model: str,
    output_model: str,
    mimic_archive: str,
    vitaldb_archive: str,
    include_vitaldb: bool,
    batch_size: int,
    num_epochs: int,
    learning_rate: float,
    weight_decay: float,
    max_windows_per_source: int,
    device_name: str,
    split_dir: str,
) -> None:
    device = resolve_device(device_name)
    os.makedirs(os.path.dirname(output_model) or ".", exist_ok=True)
    os.makedirs(split_dir, exist_ok=True)

    train_sets, val_sets = [], []
    train_sets.append(
        RealWindowDataset(
            mimic_archive,
            source="mimicbp",
            split="train",
            max_windows=max_windows_per_source,
            seed=11,
        )
    )
    val_sets.append(
        RealWindowDataset(
            mimic_archive,
            source="mimicbp",
            split="val",
            max_windows=max(1, max_windows_per_source // 5),
            seed=12,
        )
    )

    if include_vitaldb and os.path.exists(vitaldb_archive):
        vital_cases = archive_case_ids(vitaldb_archive, source="vitaldb")
        vital_train, vital_val, vital_test = split_case_ids(vital_cases)
        for name, cases in {
            "vitaldb_finetune_train_cases.txt": vital_train,
            "vitaldb_finetune_val_cases.txt": vital_val,
            "vitaldb_finetune_test_cases.txt": vital_test,
        }.items():
            with open(os.path.join(split_dir, name), "w", encoding="utf-8") as f:
                f.write("\n".join(cases) + "\n")
        train_sets.append(
            RealWindowDataset(
                vitaldb_archive,
                source="vitaldb",
                case_ids=vital_train,
                max_windows=max_windows_per_source,
                seed=21,
            )
        )
        val_sets.append(
            RealWindowDataset(
                vitaldb_archive,
                source="vitaldb",
                case_ids=vital_val,
                max_windows=max(1, max_windows_per_source // 5),
                seed=22,
            )
        )

    train_ds = ConcatDataset(train_sets)
    val_ds = ConcatDataset(val_sets)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    horizon = int(train_ds[0]["target"].shape[0])
    model = CompleteModelPipeline(forecast_horizon=horizon).to(device)
    model.load_state_dict(torch.load(base_model, map_location=device))
    loss_fn = torch.nn.SmoothL1Loss(beta=2.0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)

    best_val = float("inf")
    best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    patience, stalled = 4, 0
    print(f"Fine-tuning on {len(train_ds)} real windows; validating on {len(val_ds)} windows.")
    for epoch in range(1, num_epochs + 1):
        model.train()
        total, n = 0.0, 0
        for batch in train_loader:
            batch = move_batch_to_device(batch, device)
            optimizer.zero_grad(set_to_none=True)
            preds = model(
                batch["ecg"],
                batch["ppg"],
                abp=batch["abp"],
                intervention=batch["intervention"],
                regime="ppg_ecg_abp_intervention",
            )
            loss = loss_fn(preds["forecast"], batch["target"])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total += float(torch.abs(preds["forecast"] - batch["target"]).mean().item()) * len(batch["target"])
            n += len(batch["target"])

        train_mae = total / max(n, 1)
        val_mae = evaluate_mae(model, val_loader, device)
        print(f"Real fine-tune epoch {epoch:02d}/{num_epochs:02d} | Train MAE {train_mae:.2f} | Val MAE {val_mae:.2f}")
        if val_mae < best_val - 0.01:
            best_val = val_mae
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            stalled = 0
        else:
            stalled += 1
        if stalled >= patience:
            print(f"Early stopping real fine-tune at epoch {epoch:02d}; best Val MAE {best_val:.2f}.")
            break

    model.load_state_dict(best_state)
    torch.save(model.state_dict(), output_model)
    print(f"Saved real-data fine-tuned checkpoint to {output_model}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", default="data/models/physics-informed_model.pth")
    parser.add_argument("--output-model", default="data/models/physics-informed_real_finetuned.pth")
    parser.add_argument("--mimic-archive", default="data/processed/mimicbp_windows.npz")
    parser.add_argument("--vitaldb-archive", default="data/processed/vitaldb_windows.npz")
    parser.add_argument("--include-vitaldb", action="store_true")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--num-epochs", type=int, default=12)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--max-windows-per-source", type=int, default=80000)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--split-dir", default="data/processed")
    args = parser.parse_args()

    fine_tune_real(
        base_model=args.base_model,
        output_model=args.output_model,
        mimic_archive=args.mimic_archive,
        vitaldb_archive=args.vitaldb_archive,
        include_vitaldb=args.include_vitaldb,
        batch_size=args.batch_size,
        num_epochs=args.num_epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        max_windows_per_source=args.max_windows_per_source,
        device_name=args.device,
        split_dir=args.split_dir,
    )


if __name__ == "__main__":
    main()
