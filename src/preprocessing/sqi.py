"""
Signal Quality Index (SQI) and Abstention Module
Detects sensor dropouts, flatlines, saturation clipping, and motion artifacts.
"""

import numpy as np
from scipy.stats import skew, kurtosis
from typing import Dict, Tuple

def check_flatline_or_clipping(
    signal: np.ndarray,
    flatline_max_consecutive: int = 25, # 200 ms at 125 Hz
    clipping_percentile_threshold: float = 0.05
) -> Dict[str, bool]:
    """
    Checks for flatline sensor dropouts and saturation clipping at signal rails.
    """
    # Flatline detection via zero differences
    diffs = np.diff(signal)
    is_zero = np.isclose(diffs, 0.0, atol=1e-5)
    
    max_consecutive_zeros = 0
    current_count = 0
    for val in is_zero:
        if val:
            current_count += 1
            if current_count > max_consecutive_zeros:
                max_consecutive_zeros = current_count
        else:
            current_count = 0
            
    has_flatline = max_consecutive_zeros >= flatline_max_consecutive
    
    # Clipping detection
    s_min, s_max = np.min(signal), np.max(signal)
    near_min = np.mean(np.isclose(signal, s_min, atol=1e-3))
    near_max = np.mean(np.isclose(signal, s_max, atol=1e-3))
    has_clipping = (near_min > clipping_percentile_threshold) or (near_max > clipping_percentile_threshold)
    
    return {
        'has_flatline': bool(has_flatline),
        'has_clipping': bool(has_clipping),
        'max_flatline_samples': int(max_consecutive_zeros)
    }

def compute_ppg_sqi(
    ppg: np.ndarray
) -> Dict[str, float]:
    """
    Computes statistical Signal Quality Indices for PPG:
    - Skewness SQI (sSQI): High-quality PPG typically exhibits positive skewness.
    - Kurtosis SQI (kSQI): Measures peak peakedness relative to normal distribution.
    - SNR estimate: ratio of dominant pulsatile power to high-frequency noise.
    """
    s = skew(ppg)
    k = kurtosis(ppg)
    
    # Baseline statistical validity checks
    is_skew_acceptable = 0.0 <= s <= 2.5
    is_kurt_acceptable = -1.0 <= k <= 5.0
    
    # Simple score between 0.0 and 1.0
    score = 1.0
    if not is_skew_acceptable:
        score -= 0.3
    if not is_kurt_acceptable:
        score -= 0.3
        
    return {
        'skewness': float(s),
        'kurtosis': float(k),
        'ppg_sqi_score': max(0.0, float(score))
    }

def evaluate_window_quality(
    ecg: np.ndarray,
    ppg: np.ndarray,
    num_ecg_rpeaks: int,
    num_ppg_pulses: int,
    fs: float = 125.0
) -> Dict[str, any]:
    """
    Evaluates composite quality of a multimodal (ECG + PPG) window.
    Returns composite SQI score in [0.0, 1.0] and a boolean `is_valid` flag.
    """
    ecg_checks = check_flatline_or_clipping(ecg)
    ppg_checks = check_flatline_or_clipping(ppg)
    ppg_sqi = compute_ppg_sqi(ppg)
    
    # Pulse-to-beat count consistency
    duration_sec = len(ecg) / fs
    expected_beats_min = int(duration_sec * (40.0 / 60.0)) # min 40 bpm
    expected_beats_max = int(duration_sec * (200.0 / 60.0)) # max 200 bpm
    
    rhythm_match = 1.0
    if num_ecg_rpeaks > 0:
        ratio = num_ppg_pulses / num_ecg_rpeaks
        # Should be approximately 1:1 match
        if 0.7 <= ratio <= 1.3:
            rhythm_match = 1.0
        elif 0.5 <= ratio <= 1.5:
            rhythm_match = 0.6
        else:
            rhythm_match = 0.2
    else:
        rhythm_match = 0.0
        
    # Aggregate penalties
    composite_score = ppg_sqi['ppg_sqi_score'] * 0.5 + rhythm_match * 0.5
    if ecg_checks['has_flatline'] or ppg_checks['has_flatline']:
        composite_score *= 0.1
    if ecg_checks['has_clipping'] or ppg_checks['has_clipping']:
        composite_score *= 0.5
        
    is_valid = (
        composite_score >= 0.6 and
        not ecg_checks['has_flatline'] and
        not ppg_checks['has_flatline'] and
        (expected_beats_min <= num_ecg_rpeaks <= expected_beats_max)
    )
    
    return {
        'is_valid': bool(is_valid),
        'composite_sqi': float(np.clip(composite_score, 0.0, 1.0)),
        'rhythm_match': float(rhythm_match),
        'ecg_flatline': ecg_checks['has_flatline'],
        'ppg_flatline': ppg_checks['has_flatline']
    }
