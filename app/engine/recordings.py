"""
Unified access to the patient recordings that the concepts replay.

Two sources are supported, both from data the Version 5 model never trained on:
  * synthetic  - held-out Windkessel simulator patients. These carry ground
                 truth (clean ABP, R, C, intervention type) so outputs can be
                 verified against known physiology.
  * vitaldb    - held-out real surgical cases (VitalDB open dataset): real ECG,
                 PPG, arterial line ABP and vasopressor infusion rates.

A Recording exposes exactly what a bedside monitor would stream (ECG, PPG,
ABP, intervention channel) plus optional ground truth that is used only for
verification overlays and never fed to the model.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from app.engine import config


@dataclass
class Recording:
    rec_id: str
    source: str
    title: str
    description: str
    fs: float
    ecg: np.ndarray
    ppg: np.ndarray
    abp: np.ndarray
    intervention: np.ndarray          # exactly as the model was trained on (model input)
    intervention_scale: Optional[float]
    # Normalised [0, 1] intervention level as a clinician would read it (pump
    # rate held between reports). Used for event detection, never as model input.
    intervention_held: Optional[np.ndarray] = None
    events: List[Dict[str, object]] = field(default_factory=list)
    truth_abp: Optional[np.ndarray] = None
    truth_r: Optional[np.ndarray] = None
    truth_c: Optional[np.ndarray] = None
    scenario: str = ""
    provenance: Dict[str, object] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return len(self.ppg) / self.fs

    @property
    def has_truth(self) -> bool:
        return self.truth_abp is not None

    def first_window_end(self) -> float:
        return config.WINDOW_SEC

    def last_window_end(self) -> float:
        # Leave room for the 15 s ground-truth horizon so every replayed window
        # can be verified against what actually happened next.
        return self.duration_sec - config.FORECAST_HORIZON_SEC

    def summary(self) -> Dict[str, object]:
        return {
            "id": self.rec_id,
            "source": self.source,
            "title": self.title,
            "description": self.description,
            "scenario": self.scenario,
            "duration_sec": round(self.duration_sec, 1),
            "fs": self.fs,
            "has_truth": self.has_truth,
            "events": self.events,
            "first_window_end": self.first_window_end(),
            "last_window_end": round(self.last_window_end(), 1),
        }


def _onsets(channel: np.ndarray, fs: float, kind: str, label: str) -> List[Dict[str, object]]:
    """Times where a channel switches on after being quiet - an intervention start."""
    on = np.nan_to_num(np.asarray(channel, dtype=np.float64), nan=0.0) > config.INTERVENTION_ON
    events = []
    quiet_needed = int(config.INTERVENTION_QUIET_SEC * fs)
    quiet_run = quiet_needed  # treat the record start as quiet
    for i, flag in enumerate(on):
        if flag:
            if quiet_run >= quiet_needed:
                events.append({"t": round(i / fs, 1), "type": kind, "label": label, "source": "recorded"})
            quiet_run = 0
        else:
            quiet_run += 1
    return events


def load_synthetic(path: str, rec_id: str, title: str = "", description: str = "") -> Recording:
    d = np.load(path, allow_pickle=True)
    fs = float(d["fs"])
    vaso = d["u_vasopressor"].astype(np.float32)
    fluid = d["u_fluid"].astype(np.float32)
    events = _onsets(vaso, fs, "vasopressor", "Vasopressor started") + _onsets(fluid, fs, "fluid", "Fluid bolus started")
    events.sort(key=lambda e: e["t"])
    scenario = str(d["scenario"])
    return Recording(
        rec_id=rec_id,
        source="synthetic",
        title=title or f"Simulated patient - {scenario.replace('_', ' ')}",
        description=description,
        fs=fs,
        ecg=d["ecg_noisy"].astype(np.float32),
        ppg=d["ppg_noisy"].astype(np.float32),
        abp=d["abp_noisy"].astype(np.float32),
        intervention=d["u_intervention"].astype(np.float32),
        intervention_scale=None,  # synthetic interventions are already in [0, 1]
        intervention_held=d["u_intervention"].astype(np.float32),
        events=events,
        truth_abp=d["abp_clean"].astype(np.float32),
        truth_r=d["R_ground_truth"].astype(np.float32),
        truth_c=d["C_ground_truth"].astype(np.float32),
        scenario=scenario,
        provenance={"file": os.path.basename(path)},
    )


PUMP_HOLD_SEC = 30.0  # a rate is assumed to persist this long without a new pump report


def hold_pump_rate(rate: np.ndarray, fs: float, max_hold_sec: float = PUMP_HOLD_SEC) -> np.ndarray:
    """
    Sample-and-hold a sparse infusion-pump rate track.

    VitalDB's Orchestra pump tracks report a rate only every few seconds. Version 5
    stored the gaps as 0, so a continuous infusion looks like isolated spikes.
    Clinically the rate persists until the pump reports a new one, so each
    non-zero report is held forward; if no report arrives for max_hold_sec the
    infusion is treated as stopped (3 missed ~10 s reports).
    """
    x = np.nan_to_num(np.asarray(rate, dtype=np.float64), nan=0.0)
    held = np.zeros_like(x)
    max_hold = int(max_hold_sec * fs)
    last_val, last_idx = 0.0, -max_hold - 1
    for i, v in enumerate(x):
        if v > 0:
            last_val, last_idx = v, i
        held[i] = last_val if i - last_idx <= max_hold else 0.0
    return held


def vitaldb_intervention_scale(intervention: np.ndarray) -> Optional[float]:
    """Per-case scale for raw vasopressor rates (matches Version 5 preprocess_vitaldb)."""
    x = np.nan_to_num(np.asarray(intervention, dtype=np.float64), nan=0.0)
    positive = x[x > 0.0]
    if positive.size == 0:
        return None
    scale = float(np.nanpercentile(positive, 95))
    return scale if np.isfinite(scale) and scale > 0 else None


def load_vitaldb(path: str, rec_id: str, title: str = "", description: str = "") -> Recording:
    d = np.load(path, allow_pickle=True)
    fs = float(d["fs"])
    intervention = np.nan_to_num(d["intervention"].astype(np.float32), nan=0.0)
    scale = vitaldb_intervention_scale(intervention)
    held = hold_pump_rate(intervention, fs)
    held_norm = np.clip(held / scale, 0.0, 1.0).astype(np.float32) if scale else np.zeros_like(intervention)
    events = _onsets(held_norm, fs, "vasopressor", "Vasopressor infusion started")
    case = str(d["case_id"]) if "case_id" in d.files else rec_id
    return Recording(
        rec_id=rec_id,
        source="vitaldb",
        title=title or f"VitalDB surgical case {case}",
        description=description,
        fs=fs,
        ecg=d["ecg"].astype(np.float32),
        ppg=d["ppg"].astype(np.float32),
        abp=d["abp"].astype(np.float32),
        intervention=intervention,
        intervention_scale=scale,
        intervention_held=held_norm,
        events=events,
        scenario="real surgical case",
        provenance={"file": os.path.basename(path), "case_id": case},
    )


class RecordingLibrary:
    """Loads demo recordings listed in demo_data/manifest.json, with caching."""

    def __init__(self, root: str):
        self.root = root
        manifest_path = os.path.join(root, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        self._cache: Dict[str, Recording] = {}

    def entries(self) -> List[Dict[str, object]]:
        return self.manifest["recordings"]

    def get(self, rec_id: str) -> Recording:
        if rec_id in self._cache:
            return self._cache[rec_id]
        entry = next((e for e in self.entries() if e["id"] == rec_id), None)
        if entry is None:
            raise KeyError(f"Unknown recording: {rec_id}")
        path = os.path.join(self.root, entry["path"])
        loader = load_synthetic if entry["source"] == "synthetic" else load_vitaldb
        rec = loader(path, rec_id, entry.get("title", ""), entry.get("description", ""))
        rec.provenance.update({k: v for k, v in entry.items() if k not in ("path",)})
        self._cache[rec_id] = rec
        return rec

    def summaries(self) -> List[Dict[str, object]]:
        return [self.get(e["id"]).summary() for e in self.entries()]
