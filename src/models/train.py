"""
Phase 4: Training & Head-to-Head Evaluation
Compares Purely Data-Driven Baseline vs. Physics-Informed Neural Framework
"""

import argparse
import glob
import copy
import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from typing import Dict, Tuple, List, Optional

from src.models.encoders import WaveformEncoder
from src.models.fusion import CrossModalAttentionFusion, LatentHemodynamicModel
from src.models.physics_loss import PhysicsInformedLoss

OBSERVATION_REGIMES = (
    'ppg_only',
    'ppg_ecg',
    'ppg_ecg_abp',
    'ppg_ecg_abp_intervention',
)

DEFAULT_MODALITY_DROPOUT = {
    'ecg': 0.15,
    'abp': 0.20,
    'intervention': 0.30,
}


DEFAULT_TRAINING_CONFIG = {
    'training': {
        'num_epochs': 20,
        'batch_size': 16,
        'learning_rate': 1.5e-3,
        'weight_decay': 1e-4,
        'gradient_clip_norm': 1.0,
        'early_stopping_patience': 5,
        'early_stopping_min_delta': 0.01,
        'processed_archive': 'data/processed/synthetic_windows.npz',
        'training_regime': 'ppg_ecg_abp_intervention',
        'use_modality_dropout': True,
        'forecast_step_sec': 1.0,
        'holdout_scenarios': [],
        'train_models': ['data_only', 'physics_informed'],
        'log_every': 1,
        'device': 'auto',
    },
    'modality_dropout': DEFAULT_MODALITY_DROPOUT,
    'loss': {
        'data_only': {
            'w_obs': 1.0,
            'w_phys': 0.0,
            'w_param': 0.0,
            'w_pulse': 0.0,
        },
        'physics_informed': {
            'w_obs': 1.0,
            'w_phys': 0.15,
            'w_param': 0.5,
            'w_pulse': 0.10,
        },
    },
}


def deep_update(base: Dict, updates: Dict) -> Dict:
    """Recursively merge dictionaries without mutating the original base."""
    merged = copy.deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_update(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_training_config(config_path: str = 'configs/training.yaml') -> Dict:
    """Load YAML training config when present, falling back to safe defaults."""
    cfg = copy.deepcopy(DEFAULT_TRAINING_CONFIG)
    if not config_path or not os.path.exists(config_path):
        return cfg

    try:
        import yaml
    except ImportError:
        print("PyYAML is not installed; using built-in training defaults.")
        return cfg

    with open(config_path, 'r', encoding='utf-8') as f:
        loaded = yaml.safe_load(f) or {}
    return deep_update(cfg, loaded)


def normalize_model_selection(train_models) -> List[str]:
    """Normalize configured/CLI model names."""
    if train_models is None:
        return ['data_only', 'physics_informed']
    if isinstance(train_models, str):
        train_models = [item.strip() for item in train_models.split(',') if item.strip()]
    aliases = {
        'data': 'data_only',
        'data_only': 'data_only',
        'data-only': 'data_only',
        'baseline': 'data_only',
        'physics': 'physics_informed',
        'physics_informed': 'physics_informed',
        'physics-informed': 'physics_informed',
        'pi': 'physics_informed',
        'both': 'both',
        'all': 'both',
    }
    selected: List[str] = []
    for item in train_models:
        key = aliases.get(str(item).lower())
        if key is None:
            raise ValueError("Unknown model selection. Use data_only, physics_informed, or both.")
        if key == 'both':
            selected.extend(['data_only', 'physics_informed'])
        else:
            selected.append(key)
    return list(dict.fromkeys(selected))


def resolve_device(device_name: str = 'auto') -> torch.device:
    """Resolve configured training device."""
    if device_name == 'auto':
        return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    return torch.device(device_name)


def move_batch_to_device(batch: Dict[str, torch.Tensor], device: torch.device) -> Dict[str, torch.Tensor]:
    """Move tensor batch fields to the selected device."""
    return {
        key: value.to(device) if torch.is_tensor(value) else value
        for key, value in batch.items()
    }


def select_regime_inputs(batch: Dict[str, torch.Tensor], regime: str) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Mask model inputs according to the four observation regimes from the
    narrowed project.
    """
    if regime not in OBSERVATION_REGIMES:
        raise ValueError(f"Unknown regime {regime}. Expected one of {OBSERVATION_REGIMES}")

    ecg = batch['ecg']
    ppg = batch['ppg']
    abp = batch.get('abp')
    intervention = batch.get('intervention')

    if regime == 'ppg_only':
        ecg = torch.zeros_like(ecg)
        abp = None
        intervention = None
    elif regime == 'ppg_ecg':
        abp = None
        intervention = None
    elif regime == 'ppg_ecg_abp':
        intervention = None

    return ecg, ppg, abp, intervention


def apply_modality_dropout(
    ecg: torch.Tensor,
    ppg: torch.Tensor,
    abp: torch.Tensor = None,
    intervention: torch.Tensor = None,
    dropout_probs: Dict[str, float] = None,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Training-time modality dropout for incomplete-observability robustness.

    PPG is intentionally kept as the minimum observation channel. ECG, ABP, and
    intervention inputs are randomly zeroed per sample when present. Evaluation
    remains deterministic through select_regime_inputs.
    """
    probs = DEFAULT_MODALITY_DROPOUT if dropout_probs is None else dropout_probs

    def maybe_zero(x: torch.Tensor, modality: str) -> torch.Tensor:
        if x is None:
            return None
        p_drop = float(probs.get(modality, 0.0))
        if p_drop <= 0.0:
            return x
        keep = torch.rand(x.shape[0], 1, 1, device=x.device, dtype=x.dtype) >= p_drop
        return x * keep

    return (
        maybe_zero(ecg, 'ecg'),
        ppg,
        maybe_zero(abp, 'abp'),
        maybe_zero(intervention, 'intervention'),
    )


def split_case_ids(case_ids: List[str], train_frac: float = 0.70, val_frac: float = 0.15) -> Tuple[List[str], List[str], List[str]]:
    """Create deterministic patient/case-level splits from available case IDs."""
    unique_cases = sorted(set(str(case_id) for case_id in case_ids))
    if len(unique_cases) < 3:
        raise ValueError("At least three independent cases are required for train/val/test splitting.")

    n_cases = len(unique_cases)
    n_train = max(1, int(round(n_cases * train_frac)))
    n_val = max(1, int(round(n_cases * val_frac)))
    if n_train + n_val >= n_cases:
        n_train = max(1, n_cases - 2)
        n_val = 1

    train_case_ids = unique_cases[:n_train]
    val_case_ids = unique_cases[n_train:n_train + n_val]
    test_case_ids = unique_cases[n_train + n_val:]
    return train_case_ids, val_case_ids, test_case_ids


def split_case_ids_with_scenario_holdout(
    archive: np.lib.npyio.NpzFile,
    holdout_scenarios: List[str],
) -> Tuple[List[str], List[str], List[str]]:
    """Train/val on non-held-out scenarios and test on held-out scenarios."""
    if 'scenario' not in archive.files:
        raise ValueError("Scenario holdout requires processed archives with a scenario field.")

    holdout = set(str(item) for item in holdout_scenarios)
    case_to_scenario: Dict[str, str] = {}
    for case_id, scenario, source in zip(archive['case_id'], archive['scenario'], archive['dataset_source']):
        if str(source) == 'synthetic':
            case_to_scenario[str(case_id)] = str(scenario)

    test_case_ids = sorted([case_id for case_id, scenario in case_to_scenario.items() if scenario in holdout])
    remaining_case_ids = sorted([case_id for case_id, scenario in case_to_scenario.items() if scenario not in holdout])
    if len(test_case_ids) == 0:
        raise ValueError(f"No synthetic cases found for holdout scenarios: {sorted(holdout)}")
    if len(remaining_case_ids) < 2:
        raise ValueError("Scenario holdout left fewer than two training/validation cases.")

    train_case_ids, val_case_ids, _ = split_case_ids(remaining_case_ids, train_frac=0.85, val_frac=0.15)
    return train_case_ids, val_case_ids, test_case_ids

class SyntheticHemodynamicDataset(Dataset):
    """
    Extracts sliding temporal windows from synthetic patient files.
    Strictly partitioned at the patient level to eliminate data leakage.
    """
    def __init__(
        self,
        data_dir: str = 'data/synthetic',
        patient_ids: List[int] = None,
        window_len: int = 1250, # 10 seconds at 125 Hz
        forecast_horizon: int = 15, # 15 future steps
        forecast_step_sec: float = 1.0,
        stride: int = 250 # 2 second slide
    ):
        self.samples = []
        files = sorted(glob.glob(os.path.join(data_dir, 'patient_*.npz')))
        
        for fpath in files:
            pid = int(os.path.basename(fpath).split('_')[1])
            if patient_ids is not None and pid not in patient_ids:
                continue
                
            data = np.load(fpath)
            ecg = data['ecg_noisy'].astype(np.float32)
            ppg = data['ppg_noisy'].astype(np.float32)
            abp = data['abp_clean'].astype(np.float32)
            abp_obs = data['abp_noisy'].astype(np.float32) if 'abp_noisy' in data.files else abp
            r_true = data['R_ground_truth'].astype(np.float32)
            c_true = data['C_ground_truth'].astype(np.float32)
            if 'u_intervention' in data.files:
                intervention = data['u_intervention'].astype(np.float32)
            elif 'u_fluid' in data.files and 'u_vasopressor' in data.files:
                intervention = np.maximum(data['u_fluid'], data['u_vasopressor']).astype(np.float32)
            elif 'u_vasopressor' in data.files:
                intervention = data['u_vasopressor'].astype(np.float32)
            else:
                intervention = np.zeros_like(ecg, dtype=np.float32)
            
            # Subsample future MAP trajectory
            n_points = len(ecg)
            forecast_step = max(1, int(round(forecast_step_sec * 125.0)))
            max_start = n_points - window_len - ((forecast_horizon - 1) * forecast_step)
            
            for start in range(0, max_start, stride):
                end = start + window_len
                target_indices = [end + k * forecast_step for k in range(forecast_horizon)]
                future_map = abp[target_indices]
                
                self.samples.append({
                    'case_id': f"synthetic_{pid:03d}",
                    'window_start_sec': float(start / 125.0),
                    'ecg': ecg[start:end],
                    'ppg': ppg[start:end],
                    'abp': abp_obs[start:end],
                    'intervention': intervention[start:end],
                    'target_map': future_map,
                    'r_true': float(np.mean(r_true[start:end])),
                    'c_true': float(np.mean(c_true[start:end]))
                })

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        return {
            'ecg': torch.tensor(s['ecg']).unsqueeze(0), # (1, L)
            'ppg': torch.tensor(s['ppg']).unsqueeze(0), # (1, L)
            'abp': torch.tensor(s['abp']).unsqueeze(0), # (1, L)
            'intervention': torch.tensor(s['intervention']).unsqueeze(0), # (1, L)
            'target': torch.tensor(s['target_map']),     # (H,)
            'r_true': torch.tensor(s['r_true']),
            'c_true': torch.tensor(s['c_true']),
            'case_id': s['case_id'],
            'window_start_sec': torch.tensor(s['window_start_sec'], dtype=torch.float32),
        }


class ProcessedWindowDataset(Dataset):
    """
    Loads common preprocessed window archives produced by src/data/preprocess_*.py.

    Synthetic windows include R/C ground truth. Real MIMIC-BP and VitalDB windows
    keep those fields as NaN and can still be used for waveform forecasting or
    representation checks.
    """
    def __init__(
        self,
        archive_path: str,
        case_ids: List[str] = None,
        dataset_sources: List[str] = None,
        valid_only: bool = True
    ):
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Processed archive not found: {archive_path}")

        # Materialize into a plain dict: NpzFile re-decompresses an array from the
        # zip on every single key access, so indexing self.data[key][i] per sample
        # inside __getitem__ would otherwise re-decompress full arrays every call.
        self.data = dict(np.load(archive_path, allow_pickle=True).items())
        if 'forecast_step_sec' not in self.data:
            raise ValueError(
                f"{archive_path} is missing forecast_step_sec metadata. "
                "Re-run the preprocessing script before training."
            )
        self.forecast_step_sec = float(np.nanmedian(self.data['forecast_step_sec']))
        n = len(self.data['ecg'])
        mask = np.ones(n, dtype=bool)

        if valid_only and 'is_valid' in self.data:
            mask &= self.data['is_valid'].astype(bool)
        if case_ids is not None:
            allowed = set(str(case_id) for case_id in case_ids)
            mask &= np.array([str(case_id) in allowed for case_id in self.data['case_id']])
        if dataset_sources is not None:
            allowed_sources = set(dataset_sources)
            mask &= np.array([str(src) in allowed_sources for src in self.data['dataset_source']])

        self.indices = np.where(mask)[0]

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, idx):
        i = self.indices[idx]
        r_true = float(self.data['r_true'][i])
        c_true = float(self.data['c_true'][i])
        return {
            'ecg': torch.tensor(self.data['ecg'][i], dtype=torch.float32).unsqueeze(0),
            'ppg': torch.tensor(self.data['ppg'][i], dtype=torch.float32).unsqueeze(0),
            'abp': torch.tensor(self.data['abp'][i], dtype=torch.float32).unsqueeze(0),
            'intervention': torch.tensor(self.data['intervention'][i], dtype=torch.float32).unsqueeze(0),
            'target': torch.tensor(self.data['future_abp'][i], dtype=torch.float32),
            'r_true': torch.tensor(r_true if np.isfinite(r_true) else 1.0, dtype=torch.float32),
            'c_true': torch.tensor(c_true if np.isfinite(c_true) else 1.2, dtype=torch.float32),
            'has_param_ground_truth': torch.tensor(bool(np.isfinite(r_true) and np.isfinite(c_true))),
            'case_id': str(self.data['case_id'][i]),
            'window_start_sec': torch.tensor(float(self.data['window_start_sec'][i]), dtype=torch.float32),
        }

class CompleteModelPipeline(nn.Module):
    """
    End-to-End pipeline tying together Encoders, Fusion, and Latent Physics Model.
    """
    def __init__(self, forecast_horizon: int = 15):
        super().__init__()
        self.ecg_enc = WaveformEncoder(in_channels=1, hidden_dim=32, out_dim=64)
        self.ppg_enc = WaveformEncoder(in_channels=1, hidden_dim=32, out_dim=64)
        self.abp_enc = WaveformEncoder(in_channels=1, hidden_dim=32, out_dim=64)
        self.intervention_enc = WaveformEncoder(in_channels=1, hidden_dim=16, out_dim=64)
        self.fusion = CrossModalAttentionFusion(feature_dim=64, num_heads=4)
        self.multimodal_proj = nn.Sequential(
            nn.Conv1d(64 * 4, 64, kernel_size=1),
            nn.BatchNorm1d(64),
            nn.GELU()
        )
        self.latent_model = LatentHemodynamicModel(feature_dim=64, latent_dim=6, forecast_horizon=forecast_horizon)
        
    def forward(
        self,
        ecg: torch.Tensor,
        ppg: torch.Tensor,
        abp: torch.Tensor = None,
        intervention: torch.Tensor = None,
        regime: str = 'ppg_ecg'
    ) -> Dict[str, torch.Tensor]:
        h_ecg = self.ecg_enc(ecg)
        h_ppg = self.ppg_enc(ppg)
        h_pair = self.fusion(h_ecg, h_ppg)

        if abp is None:
            h_abp = torch.zeros_like(h_pair)
        else:
            h_abp = self.abp_enc(abp)

        if intervention is None:
            h_u = torch.zeros_like(h_pair)
            u_input = None
        else:
            h_u = self.intervention_enc(intervention)
            u_input = intervention

        h_fused = self.multimodal_proj(torch.cat([h_pair, h_ecg, h_abp, h_u], dim=1))
        outputs = self.latent_model(h_fused, u_interventions=u_input)
        outputs['regime'] = regime
        return outputs

def train_and_evaluate_models(
    num_epochs: Optional[int] = None,
    batch_size: Optional[int] = None,
    lr: Optional[float] = None,
    processed_archive: Optional[str] = None,
    training_regime: Optional[str] = None,
    use_modality_dropout: Optional[bool] = None,
    modality_dropout: Dict[str, float] = None,
    weight_decay: Optional[float] = None,
    gradient_clip_norm: Optional[float] = None,
    early_stopping_patience: Optional[int] = None,
    early_stopping_min_delta: Optional[float] = None,
    device_name: Optional[str] = None,
    train_models: Optional[List[str]] = None,
    log_every: Optional[int] = None,
    config_path: str = 'configs/training.yaml'
) -> pd.DataFrame:
    """
    Trains Model A (Data-Only) vs. Model B (Physics-Informed) and evaluates on held-out test cohort.
    """
    config = load_training_config(config_path)
    training_cfg = config['training']
    loss_cfg = config['loss']

    num_epochs = int(training_cfg['num_epochs'] if num_epochs is None else num_epochs)
    batch_size = int(training_cfg['batch_size'] if batch_size is None else batch_size)
    lr = float(training_cfg['learning_rate'] if lr is None else lr)
    weight_decay = float(training_cfg['weight_decay'] if weight_decay is None else weight_decay)
    gradient_clip_norm = float(
        training_cfg['gradient_clip_norm'] if gradient_clip_norm is None else gradient_clip_norm
    )
    early_stopping_patience = int(
        training_cfg['early_stopping_patience']
        if early_stopping_patience is None
        else early_stopping_patience
    )
    early_stopping_min_delta = float(
        training_cfg['early_stopping_min_delta']
        if early_stopping_min_delta is None
        else early_stopping_min_delta
    )
    processed_archive = (
        str(training_cfg['processed_archive']) if processed_archive is None else processed_archive
    )
    training_regime = (
        str(training_cfg['training_regime']) if training_regime is None else training_regime
    )
    use_modality_dropout = (
        bool(training_cfg['use_modality_dropout'])
        if use_modality_dropout is None
        else bool(use_modality_dropout)
    )
    device_name = str(training_cfg['device'] if device_name is None else device_name)
    forecast_step_sec = float(training_cfg.get('forecast_step_sec', 1.0))
    holdout_scenarios = [str(item) for item in training_cfg.get('holdout_scenarios', [])]
    selected_models = normalize_model_selection(
        training_cfg.get('train_models', ['data_only', 'physics_informed'])
        if train_models is None
        else train_models
    )
    log_every = int(training_cfg.get('log_every', 1) if log_every is None else log_every)
    dropout_probs = config['modality_dropout'] if modality_dropout is None else modality_dropout
    device = resolve_device(device_name)

    torch.manual_seed(42)
    np.random.seed(42)
    print(f"Training device: {device}")
    
    if os.path.exists(processed_archive):
        archive = np.load(processed_archive, allow_pickle=True)
        synthetic_mask = np.array([str(src) == 'synthetic' for src in archive['dataset_source']])
        synthetic_case_ids = [str(case_id) for case_id in archive['case_id'][synthetic_mask]]
        if holdout_scenarios:
            train_case_ids, val_case_ids, test_case_ids = split_case_ids_with_scenario_holdout(
                archive,
                holdout_scenarios,
            )
            print(f"Scenario holdout test set: {', '.join(holdout_scenarios)}")
        else:
            train_case_ids, val_case_ids, test_case_ids = split_case_ids(synthetic_case_ids)
        print(
            f"Loading datasets: Train={len(train_case_ids)} cases, "
            f"Val={len(val_case_ids)} cases, Test={len(test_case_ids)} cases..."
        )
        train_ds = ProcessedWindowDataset(processed_archive, case_ids=train_case_ids, dataset_sources=['synthetic'])
        val_ds = ProcessedWindowDataset(processed_archive, case_ids=val_case_ids, dataset_sources=['synthetic'])
        test_ds = ProcessedWindowDataset(processed_archive, case_ids=test_case_ids, dataset_sources=['synthetic'])
        forecast_step_sec = train_ds.forecast_step_sec
        print(f"Using processed archive: {processed_archive}")
    else:
        files = sorted(glob.glob(os.path.join('data/synthetic', 'patient_*.npz')))
        available_pids = [int(os.path.basename(path).split('_')[1]) for path in files]
        case_ids = [f"synthetic_{pid:03d}" for pid in available_pids]
        train_case_ids, val_case_ids, test_case_ids = split_case_ids(case_ids)
        train_pids = [int(case_id.split('_')[1]) for case_id in train_case_ids]
        val_pids = [int(case_id.split('_')[1]) for case_id in val_case_ids]
        test_pids = [int(case_id.split('_')[1]) for case_id in test_case_ids]
        print(
            f"Loading datasets: Train={len(train_pids)} cases, "
            f"Val={len(val_pids)} cases, Test={len(test_pids)} cases..."
        )
        train_ds = SyntheticHemodynamicDataset(patient_ids=train_pids)
        val_ds = SyntheticHemodynamicDataset(patient_ids=val_pids)
        test_ds = SyntheticHemodynamicDataset(patient_ids=test_pids)
        print("Processed archive not found; using raw synthetic NPZ files directly.")
    
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)
    
    print(f"Total window samples: Train={len(train_ds)}, Val={len(val_ds)}, Test={len(test_ds)}")
    
    if len(train_ds) == 0 or len(val_ds) == 0 or len(test_ds) == 0:
        raise ValueError(
            "One of the train/val/test datasets is empty. Regenerate and preprocess the synthetic cohort."
        )

    forecast_horizon = int(train_ds[0]['target'].shape[0])
    print(f"Forecast target: {forecast_horizon} steps at {forecast_step_sec:g} sec/step")

    def finite_param_scales(dataset: Dataset) -> Tuple[float, float]:
        r_values, c_values = [], []
        for idx in range(len(dataset)):
            row = dataset[idx]
            r_val = float(row['r_true'])
            c_val = float(row['c_true'])
            if np.isfinite(r_val):
                r_values.append(r_val)
            if np.isfinite(c_val):
                c_values.append(c_val)
        r_scale = float(np.std(r_values)) if r_values else 1.0
        c_scale = float(np.std(c_values)) if c_values else 1.0
        return max(r_scale, 0.05), max(c_scale, 0.05)

    r_scale, c_scale = finite_param_scales(train_ds)
    print(f"Parameter loss scales: R={r_scale:.3f}, C={c_scale:.3f}")

    data_loss_cfg = dict(loss_cfg['data_only'])
    physics_loss_cfg = dict(loss_cfg['physics_informed'])
    data_loss_cfg.update({'r_scale': r_scale, 'c_scale': c_scale})
    physics_loss_cfg.update({'r_scale': r_scale, 'c_scale': c_scale})

    all_models = {
        'Data-Only Baseline': {
            'key': 'data_only',
            'model': CompleteModelPipeline(forecast_horizon=forecast_horizon),
            'loss_fn': PhysicsInformedLoss(**data_loss_cfg)
        },
        'Physics-Informed Model': {
            'key': 'physics_informed',
            'model': CompleteModelPipeline(forecast_horizon=forecast_horizon),
            'loss_fn': PhysicsInformedLoss(**physics_loss_cfg)
        }
    }
    models = {
        name: cfg
        for name, cfg in all_models.items()
        if cfg['key'] in selected_models
    }
    print(f"Training models: {', '.join(models.keys())}")
    
    results = []
    prediction_records = []
    forecast_examples = {}

    train_targets = []
    for batch in DataLoader(train_ds, batch_size=batch_size, shuffle=False):
        train_targets.append(batch['target'].float())
    train_mean_target = torch.cat(train_targets, dim=0).mean(dim=0)

    def append_baseline_result(name: str, regime: str, errors: List[float]) -> None:
        arr = np.asarray(errors, dtype=np.float64)
        results.append({
            'Model': name,
            'Regime': regime,
            'Test_MAE_mmHg': float(np.mean(arr)),
            'Test_RMSE_mmHg': float(np.sqrt(np.mean(arr ** 2))),
            'Physics_Residual': float('nan'),
            'R_Correlation': float('nan'),
            'C_Correlation': float('nan'),
            'R_MAE': float('nan'),
            'C_MAE': float('nan'),
            'R_RelErr_pct': float('nan'),
            'C_RelErr_pct': float('nan'),
            'R_Pred_Std': float('nan'),
            'C_Pred_Std': float('nan'),
            'R_True_Std': float('nan'),
            'C_True_Std': float('nan'),
        })

    mean_errors, persistence_errors = [], []
    for batch in test_loader:
        target = batch['target'].float()
        mean_forecast = train_mean_target.unsqueeze(0).expand_as(target)
        mean_errors.extend(torch.abs(mean_forecast - target).mean(dim=-1).numpy())
        if 'abp' in batch:
            last_abp = batch['abp'][:, 0, -1].float().unsqueeze(1)
            persistence = last_abp.expand(-1, target.shape[-1])
            persistence_errors.extend(torch.abs(persistence - target).mean(dim=-1).numpy())
    append_baseline_result('Train-Mean Baseline', 'all_regimes', mean_errors)
    if persistence_errors:
        append_baseline_result('ABP Persistence Baseline', 'ppg_ecg_abp', persistence_errors)
    
    for model_name, cfg in models.items():
        print(f"\n==========================================")
        print(f"Training {model_name}...")
        print(f"==========================================")
        model = cfg['model'].to(device)
        loss_fn = cfg['loss_fn'].to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=num_epochs)
        best_val_mae = float('inf')
        best_epoch = 0
        best_state = copy.deepcopy(model.state_dict())
        epochs_without_improvement = 0
        
        for epoch in range(1, num_epochs + 1):
            model.train()
            train_loss = 0.0
            train_mae = 0.0
            
            for batch in train_loader:
                batch = move_batch_to_device(batch, device)
                optimizer.zero_grad()
                ecg, ppg, abp, intervention = select_regime_inputs(batch, training_regime)
                if use_modality_dropout:
                    ecg, ppg, abp, intervention = apply_modality_dropout(
                        ecg,
                        ppg,
                        abp=abp,
                        intervention=intervention,
                        dropout_probs=dropout_probs,
                    )
                preds = model(ecg, ppg, abp=abp, intervention=intervention, regime=training_regime)
                loss_dict = loss_fn(
                    preds,
                    batch['target'],
                    r_true=batch['r_true'] if 'Physics' in model_name else None,
                    c_true=batch['c_true'] if 'Physics' in model_name else None,
                    dt=forecast_step_sec,
                )
                
                loss_dict['total_loss'].backward()
                if gradient_clip_norm and gradient_clip_norm > 0:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=gradient_clip_norm)
                optimizer.step()
                
                train_loss += loss_dict['total_loss'].item() * len(batch['target'])
                train_mae += loss_dict['forecast_mae'].item() * len(batch['target'])
                
            scheduler.step()
            train_mae /= len(train_ds)
            
            # Validation
            model.eval()
            val_mae = 0.0
            with torch.no_grad():
                for batch in val_loader:
                    batch = move_batch_to_device(batch, device)
                    ecg, ppg, abp, intervention = select_regime_inputs(batch, training_regime)
                    preds = model(ecg, ppg, abp=abp, intervention=intervention, regime=training_regime)
                    loss_dict = loss_fn(preds, batch['target'], dt=forecast_step_sec)
                    val_mae += loss_dict['forecast_mae'].item() * len(batch['target'])
            val_mae /= len(val_ds)

            if val_mae < best_val_mae - early_stopping_min_delta:
                best_val_mae = val_mae
                best_epoch = epoch
                best_state = copy.deepcopy(model.state_dict())
                epochs_without_improvement = 0
            else:
                epochs_without_improvement += 1
            
            if log_every > 0 and (epoch % log_every == 0 or epoch == num_epochs):
                print(f"Epoch {epoch:02d}/{num_epochs:02d} | Train MAE: {train_mae:.2f} mmHg | Val MAE: {val_mae:.2f} mmHg")

            if early_stopping_patience > 0 and epochs_without_improvement >= early_stopping_patience:
                print(
                    f"Early stopping at epoch {epoch:02d}; "
                    f"best Val MAE {best_val_mae:.2f} mmHg at epoch {best_epoch:02d}."
                )
                break
                
        # Save the trained model checkpoint
        model.load_state_dict(best_state)
        os.makedirs('data/models', exist_ok=True)
        ckpt_path = f"data/models/{model_name.replace(' ', '_').lower()}.pth"
        torch.save(model.state_dict(), ckpt_path)
        print(f"Saved best model checkpoint from epoch {best_epoch} to {ckpt_path}")
        
        # Final Evaluation on Held-Out Test Set (Patients 26-30)
        print(f"\nEvaluating {model_name} on Held-Out Test Cohort...")
        model.eval()
        for eval_regime in OBSERVATION_REGIMES:
            test_errors = []
            physics_residuals = []
            r_preds, r_trues = [], []
            c_preds, c_trues = [], []
            example_count = 0

            with torch.no_grad():
                for batch in test_loader:
                    batch = move_batch_to_device(batch, device)
                    ecg, ppg, abp, intervention = select_regime_inputs(batch, eval_regime)
                    preds = model(ecg, ppg, abp=abp, intervention=intervention, regime=eval_regime)
                    forecast = preds['forecast']
                    targets = batch['target']

                    # Forecasting error
                    mae = torch.abs(forecast - targets).mean(dim=-1).detach().cpu().numpy()
                    test_errors.extend(mae)

                    # Physical residual calculation
                    dp_dt = (forecast[:, 1:] - forecast[:, :-1]) / forecast_step_sec
                    p_mid = 0.5 * (forecast[:, 1:] + forecast[:, :-1])
                    q_nom = (85.0 - 4.0) / 1.0
                    drain = (p_mid - 4.0) / (preds['R'] + 1e-4)
                    res = torch.abs(preds['C'] * dp_dt - (q_nom - drain)).mean(dim=-1).detach().cpu().numpy()
                    physics_residuals.extend(res)

                    r_batch = preds['R'].detach().cpu().numpy().reshape(-1)
                    c_batch = preds['C'].detach().cpu().numpy().reshape(-1)
                    r_true_batch = batch['r_true'].detach().cpu().numpy().reshape(-1)
                    c_true_batch = batch['c_true'].detach().cpu().numpy().reshape(-1)
                    forecast_batch = forecast.detach().cpu().numpy()
                    target_batch = targets.detach().cpu().numpy()
                    window_start_batch = batch['window_start_sec'].detach().cpu().numpy()
                    case_ids = batch.get('case_id', ["unknown"] * len(mae))

                    r_preds.extend(r_batch)
                    r_trues.extend(r_true_batch)
                    c_preds.extend(c_batch)
                    c_trues.extend(c_true_batch)

                    for row_idx in range(len(mae)):
                        prediction_records.append({
                            'Model': model_name,
                            'Regime': eval_regime,
                            'case_id': str(case_ids[row_idx]),
                            'window_start_sec': float(window_start_batch[row_idx]),
                            'forecast_mae_mmHg': float(mae[row_idx]),
                            'physics_residual': float(res[row_idx]),
                            'r_true': float(r_true_batch[row_idx]),
                            'r_pred': float(r_batch[row_idx]),
                            'c_true': float(c_true_batch[row_idx]),
                            'c_pred': float(c_batch[row_idx]),
                        })

                        if example_count < 6:
                            prefix = f"{model_name.replace(' ', '_').lower()}__{eval_regime}__{example_count}"
                            forecast_examples[f"{prefix}__forecast"] = forecast_batch[row_idx].astype(np.float32)
                            forecast_examples[f"{prefix}__target"] = target_batch[row_idx].astype(np.float32)
                            forecast_examples[f"{prefix}__case_id"] = np.array(str(case_ids[row_idx]))
                            forecast_examples[f"{prefix}__window_start_sec"] = np.array(float(window_start_batch[row_idx]))
                            example_count += 1

            test_mae = float(np.mean(test_errors))
            test_rmse = float(np.sqrt(np.mean(np.array(test_errors) ** 2)))
            mean_phys_res = float(np.mean(physics_residuals))

            r_preds_arr = np.array(r_preds)
            r_trues_arr = np.array(r_trues)
            c_preds_arr = np.array(c_preds)
            c_trues_arr = np.array(c_trues)
            if np.std(r_preds_arr) > 1e-4 and np.std(r_trues_arr) > 1e-4:
                r_corr = float(np.corrcoef(r_preds_arr, r_trues_arr)[0, 1])
            else:
                r_corr = 0.0
            if np.std(c_preds_arr) > 1e-4 and np.std(c_trues_arr) > 1e-4:
                c_corr = float(np.corrcoef(c_preds_arr, c_trues_arr)[0, 1])
            else:
                c_corr = 0.0
            r_mae = float(np.mean(np.abs(r_preds_arr - r_trues_arr)))
            c_mae = float(np.mean(np.abs(c_preds_arr - c_trues_arr)))
            r_rel_err = float(np.mean(np.abs(r_preds_arr - r_trues_arr) / (np.abs(r_trues_arr) + 1e-6)) * 100.0)
            c_rel_err = float(np.mean(np.abs(c_preds_arr - c_trues_arr) / (np.abs(c_trues_arr) + 1e-6)) * 100.0)

            results.append({
                'Model': model_name,
                'Regime': eval_regime,
                'Test_MAE_mmHg': test_mae,
                'Test_RMSE_mmHg': test_rmse,
                'Physics_Residual': mean_phys_res,
                'R_Correlation': r_corr,
                'C_Correlation': c_corr,
                'R_MAE': r_mae,
                'C_MAE': c_mae,
                'R_RelErr_pct': r_rel_err,
                'C_RelErr_pct': c_rel_err,
                'R_Pred_Std': float(np.std(r_preds_arr)),
                'C_Pred_Std': float(np.std(c_preds_arr)),
                'R_True_Std': float(np.std(r_trues_arr)),
                'C_True_Std': float(np.std(c_trues_arr)),
            })

    df_res = pd.DataFrame(results)
    
    print("\n=======================================================")
    print("FINAL HEAD-TO-HEAD COMPARISON ON HELD-OUT TEST COHORT")
    print("=======================================================")
    print(df_res.to_string(index=False))
    
    out_csv = 'data/synthetic/model_comparison_results.csv'
    df_res.to_csv(out_csv, index=False)
    print(f"\nSaved head-to-head comparison to {out_csv}")

    pred_csv = 'data/synthetic/test_window_predictions.csv'
    pd.DataFrame(prediction_records).to_csv(pred_csv, index=False)
    print(f"Saved per-window test predictions to {pred_csv}")

    if forecast_examples:
        examples_path = 'data/synthetic/forecast_examples.npz'
        forecast_examples["forecast_step_sec"] = np.array(float(forecast_step_sec), dtype=np.float32)
        np.savez_compressed(examples_path, **forecast_examples)
        print(f"Saved forecast overlay examples to {examples_path}")
    return df_res

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/training.yaml")
    parser.add_argument("--num-epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--processed-archive", default=None)
    parser.add_argument("--training-regime", default=None)
    parser.add_argument("--train-models", default=None, help="data_only, physics_informed, or both")
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--gradient-clip-norm", type=float, default=None)
    parser.add_argument("--early-stopping-patience", type=int, default=None)
    parser.add_argument("--early-stopping-min-delta", type=float, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--log-every", type=int, default=None)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Short local sanity run: 5 epochs, batch size 64, physics-informed model only.",
    )
    args = parser.parse_args()

    num_epochs = 5 if args.quick and args.num_epochs is None else args.num_epochs
    batch_size = 64 if args.quick and args.batch_size is None else args.batch_size
    early_stopping_patience = (
        2 if args.quick and args.early_stopping_patience is None else args.early_stopping_patience
    )
    train_models = (
        "physics_informed" if args.quick and args.train_models is None else args.train_models
    )

    train_and_evaluate_models(
        num_epochs=num_epochs,
        batch_size=batch_size,
        lr=args.learning_rate,
        processed_archive=args.processed_archive,
        training_regime=args.training_regime,
        weight_decay=args.weight_decay,
        gradient_clip_norm=args.gradient_clip_norm,
        early_stopping_patience=early_stopping_patience,
        early_stopping_min_delta=args.early_stopping_min_delta,
        device_name=args.device,
        train_models=normalize_model_selection(train_models) if train_models is not None else None,
        log_every=args.log_every,
        config_path=args.config,
    )


if __name__ == '__main__':
    main()
