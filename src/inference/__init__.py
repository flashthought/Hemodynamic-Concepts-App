"""Deployment-oriented inference utilities for patient-monitor inputs."""

from src.inference.schemas import MonitorWindow, PredictionResult

__all__ = ["HemodynamicPredictor", "MonitorWindow", "PredictionResult"]


def __getattr__(name):
    if name == "HemodynamicPredictor":
        from src.inference.predictor import HemodynamicPredictor

        return HemodynamicPredictor
    raise AttributeError(name)
