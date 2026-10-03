"""Stable input/output schemas for deployment inference."""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence


@dataclass
class MonitorWindow:
    """One rolling patient-monitor window sent by the app."""

    fs: float
    ppg: Sequence[float]
    ecg: Optional[Sequence[float]] = None
    abp: Optional[Sequence[float]] = None
    intervention: Optional[Sequence[float]] = None
    timestamp: Optional[str] = None
    patient_id: Optional[str] = None
    intervention_scale: Optional[float] = None


@dataclass
class QualityReport:
    """Runtime signal quality and channel availability report."""

    status: str
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    channel_valid: Dict[str, bool] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.status in {"valid", "low_confidence"}


@dataclass
class PredictionResult:
    """Structured model output returned to the app."""

    status: str
    quality: str
    warnings: List[str]
    forecast_abp: List[float]
    forecast_map: Optional[float]
    r_estimate: Optional[float]
    c_estimate: Optional[float]
    regime: str
    model_version: str
    latency_ms: float
    metadata: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
