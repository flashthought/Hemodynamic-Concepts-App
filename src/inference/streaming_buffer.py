"""Rolling waveform buffer for patient-monitor streaming integration."""

from collections import deque
from typing import Dict, Optional

from src.inference.schemas import MonitorWindow


class StreamingWindowBuffer:
    """Maintain the most recent N seconds of monitor samples."""

    def __init__(self, fs: float, window_sec: float = 10.0, patient_id: Optional[str] = None):
        self.fs = float(fs)
        self.window_sec = float(window_sec)
        self.maxlen = int(round(self.fs * self.window_sec))
        self.patient_id = patient_id
        self.ecg = deque(maxlen=self.maxlen)
        self.ppg = deque(maxlen=self.maxlen)
        self.abp = deque(maxlen=self.maxlen)
        self.intervention = deque(maxlen=self.maxlen)

    def append(
        self,
        ppg: float,
        ecg: Optional[float] = None,
        abp: Optional[float] = None,
        intervention: Optional[float] = None,
    ) -> None:
        self.ppg.append(ppg)
        self.ecg.append(0.0 if ecg is None else ecg)
        self.abp.append(float("nan") if abp is None else abp)
        self.intervention.append(0.0 if intervention is None else intervention)

    def extend(self, samples: Dict[str, list]) -> None:
        ppg_values = samples.get("ppg", [])
        for i, ppg_value in enumerate(ppg_values):
            self.append(
                ppg=ppg_value,
                ecg=samples.get("ecg", [None] * len(ppg_values))[i],
                abp=samples.get("abp", [None] * len(ppg_values))[i],
                intervention=samples.get("intervention", [None] * len(ppg_values))[i],
            )

    @property
    def ready(self) -> bool:
        return len(self.ppg) >= self.maxlen

    def to_window(self, timestamp: Optional[str] = None) -> MonitorWindow:
        if not self.ready:
            raise ValueError(f"Need {self.maxlen} samples before inference; currently have {len(self.ppg)}.")
        return MonitorWindow(
            fs=self.fs,
            ecg=list(self.ecg),
            ppg=list(self.ppg),
            abp=list(self.abp),
            intervention=list(self.intervention),
            timestamp=timestamp,
            patient_id=self.patient_id,
        )
