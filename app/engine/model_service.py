"""
Thread-safe access to the frozen Version 5 model.

The model code under src/ and the weights under data/models/ are copied
unmodified from Version 5. This module only wraps them: it serialises GPU
access across server threads, loads the calibration measured by
evaluation/evaluate_concepts.py, and fingerprints the checkpoint (SHA-256) so
every result can be traced to the exact weights that produced it.
"""

import hashlib
import json
import os
import threading
import time
from dataclasses import replace
from typing import Dict

import numpy as np

import src.inference.predictor as v5_predictor
from app.engine import config
from app.engine.signal_checks import abp_artifact_free
from src.inference.predictor import HemodynamicPredictor
from src.inference.quality_gate import assess_runtime_quality as v5_assess_runtime_quality
from src.inference.schemas import MonitorWindow


def shock_aware_assess(ecg, ppg, abp, intervention, fs, **kwargs):
    """
    RC-09: Version 5's gate, except that an arterial line it rejected only for
    being outside normal physiology is RETAINED when it shows no sign of
    artifact - profound hypotension is real, not noise (hazard H-09).
    """
    report = v5_assess_runtime_quality(ecg=ecg, ppg=ppg, abp=abp, intervention=intervention, fs=fs, **kwargs)
    if abp is not None and not report.channel_valid.get("abp", True):
        ok, _ = abp_artifact_free(abp, fs)
        if ok:
            report.channel_valid["abp"] = True
            report.warnings = [w for w in report.warnings if not w.startswith("ABP failed runtime")]
            report.metrics["abp_retained_shock_aware"] = 1.0
            report.status = "valid" if not report.warnings else "low_confidence"
    return report


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


MAX_MISSING_FRACTION = 0.30  # same limit the Version 5 runtime gate intends to apply


def _missing(x) -> float:
    if x is None:
        return 0.0
    arr = np.asarray(x, dtype=np.float64)
    return float(np.mean(~np.isfinite(arr))) if arr.size else 1.0


class ModelService:
    """
    shock_aware_abp (risk control RC-09, hazard H-09): see shock_aware_assess.

    raw_precheck (risk control RC-07, FMEA R-07): Version 5's runtime quality
    gate measures missing data AFTER the preprocessor has interpolated every gap,
    so its "missing fraction > 30 %" check can never fire - verification found a
    40 % PPG dropout forecast as 'ok'. This wrapper measures missing data on the
    RAW window first: PPG over the limit -> abstain; ECG/ABP over the limit ->
    that channel is withheld from the model. The frozen model code is unchanged.
    """

    def __init__(self, app_root: str, device: str = "auto", raw_precheck: bool = True,
                 shock_aware_abp: bool = True, verify_checksum: bool = True):
        self.raw_precheck = raw_precheck
        self.shock_aware_abp = shock_aware_abp
        models = os.path.join(app_root, "data", "models")
        self.model_path = os.path.join(models, "physics-informed_real_finetuned.pth")
        self.predictor = HemodynamicPredictor(
            model_path=self.model_path,
            metadata_path=os.path.join(models, "model_metadata.json"),
            fallback_model_path=os.path.join(models, "physics-informed_model.pth"),
            device=device,
        )
        self.checksum = sha256(self.predictor.model_path)
        if verify_checksum and self.checksum != config.EXPECTED_MODEL_SHA256:
            raise RuntimeError(
                f"Model checksum mismatch: loaded {self.predictor.model_path} (sha256 {self.checksum[:12]}...), "
                f"expected the verified checkpoint {config.EXPECTED_MODEL_SHA256[:12]}... Refusing to start.")
        self._lock = threading.Lock()
        self.calibration_path = os.path.join(app_root, "evaluation", "results", "calibration.json")
        self.clear_calibration()
        self.reload_calibration()

    def clear_calibration(self) -> None:
        defaults = dict(config.DEFAULT_REGIME_SIGMA)
        self.sigma_by = {"real": dict(defaults), "synthetic": dict(defaults)}
        self.bias_by: Dict[str, Dict[str, float]] = {"real": {}, "synthetic": {}}
        self.calibration_source = "defaults (Version 5 synthetic test results, no bias correction)"

    def reload_calibration(self) -> None:
        """
        Calibration = per-regime forecast-MAP bias and residual RMSE, fitted per
        DOMAIN on held-out VALIDATION data by evaluation/evaluate_concepts.py:
          real      - VitalDB validation split (the deployment population)
          synthetic - synthetic validation split (simulator demo patients)
        then verified on the separate TEST splits (no leakage). Calibration is
        domain-specific because a bias fitted on simulations does not transfer
        to real patients (verification finding V-03).
        """
        if os.path.exists(self.calibration_path):
            with open(self.calibration_path, "r", encoding="utf-8") as f:
                cal = json.load(f)
            for domain, block in cal.get("domains", {}).items():
                for regime, stats in block.get("regimes", {}).items():
                    if stats.get("rmse_map") is not None:
                        self.sigma_by.setdefault(domain, {})[regime] = float(stats["rmse_map"])
                    if stats.get("bias_map") is not None:
                        self.bias_by.setdefault(domain, {})[regime] = float(stats["bias_map"])
            self.calibration_source = cal.get("source", self.calibration_path)

    def sigma(self, regime: str, domain: str = "real") -> float:
        table = self.sigma_by.get(domain, self.sigma_by["real"])
        return table.get(regime, max(table.values()))

    def bias(self, regime: str, domain: str = "real") -> float:
        """Mean (forecast - true) MAP for a regime in a domain; subtracted to calibrate the forecast."""
        return self.bias_by.get(domain, {}).get(regime, 0.0)

    def _precheck(self, window: MonitorWindow):
        """Return (window to run, warnings, abstain?) based on the RAW channels."""
        warnings = []
        if _missing(window.ppg) > MAX_MISSING_FRACTION:
            return window, [f"ppg missing fraction is high ({_missing(window.ppg):.2f})",
                            "PPG is required for deployed inference."], True
        updates = {}
        for name in ("ecg", "abp"):
            miss = _missing(getattr(window, name))
            if getattr(window, name) is not None and miss > MAX_MISSING_FRACTION:
                updates[name] = None
                warnings.append(f"{name} missing fraction is high ({miss:.2f}); {name} withheld from the model")
        return (replace(window, **updates) if updates else window), warnings, False

    def predict(self, window):
        if not isinstance(window, MonitorWindow):
            window = MonitorWindow(**window)
        start = time.perf_counter()
        pre_warnings, abstain = [], False
        if self.raw_precheck:
            window, pre_warnings, abstain = self._precheck(window)
        if abstain:
            return self.predictor._empty_result("abstained", "invalid", pre_warnings, self.predictor.model_version,
                                                (time.perf_counter() - start) * 1000.0)
        with self._lock:
            # RC-09 is applied by substituting the gate function the frozen V5
            # predictor calls, for this call only (inside the lock, so threads
            # cannot interleave). The V5 source files themselves are unmodified.
            v5_predictor.assess_runtime_quality = shock_aware_assess if self.shock_aware_abp else v5_assess_runtime_quality
            try:
                result = self.predictor.predict(window)
            finally:
                v5_predictor.assess_runtime_quality = v5_assess_runtime_quality
        if pre_warnings:
            result.warnings = pre_warnings + list(result.warnings)
            if result.status == "ok":
                result.status = "low_confidence"
            if result.quality == "valid":
                result.quality = "low_confidence"
        return result

    def info(self) -> Dict[str, object]:
        meta = self.predictor.metadata
        return {
            "model_version": self.predictor.model_version,
            "model_file": os.path.basename(self.predictor.model_path),
            "sha256": self.checksum,
            "device": str(self.predictor.device),
            "window_sec": self.predictor.window_sec,
            "forecast_horizon_steps": self.predictor.forecast_horizon,
            "target_fs": self.predictor.target_fs,
            "intended_use": meta.get("intended_use"),
            "sigma_by_domain": {d: {k: round(v, 2) for k, v in t.items()} for d, t in self.sigma_by.items()},
            "bias_by_domain": {d: {k: round(v, 2) for k, v in t.items()} for d, t in self.bias_by.items()},
            "raw_signal_precheck": self.raw_precheck,
            "shock_aware_abp_check": self.shock_aware_abp,
            "calibration_source": self.calibration_source,
        }
