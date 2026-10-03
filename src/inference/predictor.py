"""Deployment predictor wrapper for app/runtime use."""

import json
import os
import time
from typing import Dict, Optional, Union

import numpy as np
import torch

from src.inference.preprocessing import MonitorPreprocessor
from src.inference.quality_gate import assess_runtime_quality
from src.inference.schemas import MonitorWindow, PredictionResult
from src.models.train import CompleteModelPipeline


class HemodynamicPredictor:
    """
    Stable inference wrapper around the trained physics-informed model.

    The app should instantiate this once, then call predict(...) for each
    rolling patient-monitor window.
    """

    def __init__(
        self,
        model_path: str = "data/models/physics-informed_real_finetuned.pth",
        metadata_path: str = "data/models/model_metadata.json",
        fallback_model_path: str = "data/models/physics-informed_model.pth",
        device: str = "auto",
    ):
        self.metadata = self._load_metadata(metadata_path)
        self.model_version = str(self.metadata.get("model_version", "unversioned"))
        self.target_fs = float(self.metadata.get("target_fs", 125.0))
        self.window_sec = float(self.metadata.get("window_sec", 10.0))
        self.forecast_horizon = int(self.metadata.get("forecast_horizon_steps", 15))

        self.model_path = model_path if os.path.exists(model_path) else fallback_model_path
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"No deployment checkpoint found: {model_path} or {fallback_model_path}")

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.preprocessor = MonitorPreprocessor(target_fs=self.target_fs, window_sec=self.window_sec)
        self.model = CompleteModelPipeline(forecast_horizon=self.forecast_horizon).to(self.device)
        self.model.load_state_dict(torch.load(self.model_path, map_location=self.device))
        self.model.eval()

    @staticmethod
    def _load_metadata(path: str) -> Dict[str, object]:
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _coerce_window(window: Union[MonitorWindow, Dict[str, object]]) -> MonitorWindow:
        if isinstance(window, MonitorWindow):
            return window
        if isinstance(window, dict):
            return MonitorWindow(**window)
        raise TypeError("window must be a MonitorWindow or dict")

    @staticmethod
    def _empty_result(status: str, quality: str, warnings, model_version: str, latency_ms: float):
        return PredictionResult(
            status=status,
            quality=quality,
            warnings=list(warnings),
            forecast_abp=[],
            forecast_map=None,
            r_estimate=None,
            c_estimate=None,
            regime="none",
            model_version=model_version,
            latency_ms=latency_ms,
        )

    def _select_regime_and_tensors(self, prepared, quality):
        channel_valid = quality.channel_valid
        ecg = torch.tensor(prepared.ecg, dtype=torch.float32, device=self.device).view(1, 1, -1)
        ppg = torch.tensor(prepared.ppg, dtype=torch.float32, device=self.device).view(1, 1, -1)

        if not channel_valid.get("ecg", True):
            ecg = torch.zeros_like(ecg)

        abp = None
        intervention = None
        regime = "ppg_only"

        if channel_valid.get("ecg", True):
            regime = "ppg_ecg"

        if channel_valid.get("abp", False) and prepared.abp is not None:
            abp = torch.tensor(prepared.abp, dtype=torch.float32, device=self.device).view(1, 1, -1)
            regime = "ppg_ecg_abp"

        if abp is not None and prepared.intervention is not None and np.nanmax(prepared.intervention) > 0:
            intervention = torch.tensor(prepared.intervention, dtype=torch.float32, device=self.device).view(1, 1, -1)
            regime = "ppg_ecg_abp_intervention"

        return ecg, ppg, abp, intervention, regime

    def predict(self, window: Union[MonitorWindow, Dict[str, object]]) -> PredictionResult:
        start_time = time.perf_counter()
        try:
            monitor_window = self._coerce_window(window)
            prepared = self.preprocessor.prepare(monitor_window)
            quality = assess_runtime_quality(
                ecg=prepared.ecg,
                ppg=prepared.ppg,
                abp=prepared.abp,
                intervention=prepared.intervention,
                fs=prepared.fs,
            )
            if not quality.is_usable:
                latency = (time.perf_counter() - start_time) * 1000.0
                return self._empty_result("abstained", quality.status, quality.warnings, self.model_version, latency)

            ecg, ppg, abp, intervention, regime = self._select_regime_and_tensors(prepared, quality)
            with torch.no_grad():
                preds = self.model(ecg, ppg, abp=abp, intervention=intervention, regime=regime)

            forecast = preds["forecast"].detach().cpu().numpy().reshape(-1).astype(float)
            r_est = float(preds["R"].detach().cpu().numpy().reshape(-1)[0])
            c_est = float(preds["C"].detach().cpu().numpy().reshape(-1)[0])
            warnings = list(quality.warnings)

            if np.any(~np.isfinite(forecast)) or not np.isfinite(r_est) or not np.isfinite(c_est):
                warnings.append("Model produced non-finite output.")
                latency = (time.perf_counter() - start_time) * 1000.0
                return self._empty_result("error", quality.status, warnings, self.model_version, latency)

            if float(np.max(forecast)) > 260.0 or float(np.min(forecast)) < 20.0:
                warnings.append("Forecast is outside expected physiologic ABP range.")

            latency = (time.perf_counter() - start_time) * 1000.0
            return PredictionResult(
                status="ok" if quality.status == "valid" and not warnings else "low_confidence",
                quality=quality.status,
                warnings=warnings,
                forecast_abp=[float(v) for v in forecast],
                forecast_map=float(np.mean(forecast)),
                r_estimate=r_est,
                c_estimate=c_est,
                regime=regime,
                model_version=self.model_version,
                latency_ms=float(latency),
                metadata={
                    "model_path": self.model_path,
                    "target_fs": self.target_fs,
                    "window_sec": self.window_sec,
                    "quality_metrics": quality.metrics,
                    "channel_valid": quality.channel_valid,
                },
            )
        except Exception as exc:
            latency = (time.perf_counter() - start_time) * 1000.0
            return self._empty_result(
                "error",
                "invalid",
                [f"Inference failed: {exc}"],
                self.model_version,
                latency,
            )
