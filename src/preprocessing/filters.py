"""
Signal Preprocessing: Filtering and Baseline Wander Removal
"""

import numpy as np
from scipy import signal
from typing import Tuple

def butter_bandpass(
    lowcut: float,
    highcut: float,
    fs: float,
    order: int = 3
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Computes Butterworth bandpass filter coefficients (b, a).
    """
    nyquist = 0.5 * fs
    low = lowcut / nyquist
    high = highcut / nyquist
    b, a = signal.butter(order, [low, high], btype='band')
    return b, a

def filter_ecg(
    ecg: np.ndarray,
    fs: float = 125.0,
    lowcut: float = 0.5,
    highcut: float = 40.0,
    order: int = 3
) -> np.ndarray:
    """
    Zero-phase bandpass filter for ECG signals to eliminate baseline wander
    and high-frequency electromyographic/powerline noise.
    """
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    return signal.filtfilt(b, a, ecg)

def filter_ppg(
    ppg: np.ndarray,
    fs: float = 125.0,
    lowcut: float = 0.5,
    highcut: float = 8.0,
    order: int = 3
) -> np.ndarray:
    """
    Zero-phase bandpass filter for photoplethysmography (PPG) to isolate
    the arterial pulse waveform while rejecting motion wander and optical noise.
    """
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    return signal.filtfilt(b, a, ppg)

def normalize_signal(
    x: np.ndarray,
    method: str = 'zscore'
) -> np.ndarray:
    """
    Standardize or min-max normalize a physiological signal.
    """
    if method == 'zscore':
        std = np.std(x)
        return (x - np.mean(x)) / (std if std > 1e-8 else 1.0)
    elif method == 'minmax':
        xmin, xmax = np.min(x), np.max(x)
        diff = xmax - xmin
        return (x - xmin) / (diff if diff > 1e-8 else 1.0)
    else:
        raise ValueError(f"Unknown normalization method: {method}")
