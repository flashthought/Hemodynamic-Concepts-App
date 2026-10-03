"""
Fiducial Point Detection & Timing Feature Extraction (PAT, Heart Rate, Peaks)
"""

import numpy as np
from scipy import signal
from typing import Dict, List, Optional

def detect_ecg_rpeaks(
    ecg: np.ndarray,
    fs: float = 125.0,
    min_rr_sec: float = 0.35
) -> np.ndarray:
    """
    Robust derivative-based R-peak detector with refractory blanking period.
    """
    min_distance = int(min_rr_sec * fs)
    
    # Differentiate to highlight steep QRS slopes
    diff = np.diff(ecg)
    squared = diff ** 2
    
    # Moving window integration (approx 120 ms QRS width)
    win_size = max(3, int(0.12 * fs))
    integrated = np.convolve(squared, np.ones(win_size) / win_size, mode='same')
    
    # Thresholding
    threshold = np.mean(integrated) + 0.5 * np.std(integrated)
    peaks, _ = signal.find_peaks(integrated, height=threshold, distance=min_distance)
    
    # Refine peak locations by finding local maximum in raw filtered ECG near integrated peak
    refined_rpeaks = []
    search_radius = int(0.08 * fs)
    for p in peaks:
        start = max(0, p - search_radius)
        end = min(len(ecg), p + search_radius + 1)
        r_idx = start + np.argmax(ecg[start:end])
        refined_rpeaks.append(r_idx)
        
    return np.unique(np.array(refined_rpeaks, dtype=int))

def detect_ppg_fiducials(
    ppg: np.ndarray,
    fs: float = 125.0,
    min_distance_sec: float = 0.35
) -> Dict[str, np.ndarray]:
    """
    Detects systolic peaks and diastolic feet in PPG pulses.
    """
    min_dist = int(min_distance_sec * fs)
    
    # Systolic peaks
    systolic_peaks, _ = signal.find_peaks(ppg, distance=min_dist, prominence=0.1 * np.ptp(ppg))
    
    # Diastolic feet (valleys immediately preceding systolic peaks)
    feet = []
    search_window = int(0.4 * fs)
    for p in systolic_peaks:
        start = max(0, p - search_window)
        foot_idx = start + np.argmin(ppg[start:p]) if p > start else p
        feet.append(foot_idx)
        
    return {
        'systolic_peaks': systolic_peaks,
        'diastolic_feet': np.unique(np.array(feet, dtype=int))
    }

def compute_pat(
    r_peaks: np.ndarray,
    ppg_feet: np.ndarray,
    fs: float = 125.0,
    max_pat_ms: float = 450.0,
    min_pat_ms: float = 80.0
) -> Dict[str, np.ndarray]:
    """
    Computes Pulse Arrival Time (PAT) for each cardiac cycle:
        PAT = t_PPG_foot - t_ECG_R
    Returns matched R-peaks, PPG feet, and PAT values in milliseconds.
    """
    matched_r = []
    matched_feet = []
    pats_ms = []
    
    for r in r_peaks:
        # Find next foot within physiological delay window
        t_r_sec = r / fs
        candidate_feet = ppg_feet[ppg_feet > r]
        
        if len(candidate_feet) > 0:
            foot = candidate_feet[0]
            delay_ms = ((foot - r) / fs) * 1000.0
            
            if min_pat_ms <= delay_ms <= max_pat_ms:
                matched_r.append(r)
                matched_feet.append(foot)
                pats_ms.append(delay_ms)
                
    return {
        'matched_rpeaks': np.array(matched_r, dtype=int),
        'matched_feet': np.array(matched_feet, dtype=int),
        'pat_ms': np.array(pats_ms, dtype=float)
    }

def compute_cardiovascular_features(
    ecg: np.ndarray,
    ppg: np.ndarray,
    fs: float = 125.0
) -> Dict[str, float]:
    """
    Extracts summary cardiovascular timing features across a waveform segment.
    """
    r_peaks = detect_ecg_rpeaks(ecg, fs=fs)
    ppg_fids = detect_ppg_fiducials(ppg, fs=fs)
    pat_res = compute_pat(r_peaks, ppg_fids['diastolic_feet'], fs=fs)
    
    # Heart rate from RR intervals
    if len(r_peaks) >= 2:
        rr_sec = np.diff(r_peaks) / fs
        mean_hr = 60.0 / np.mean(rr_sec)
        hrv_sdnn = np.std(rr_sec) * 1000.0
    else:
        mean_hr = np.nan
        hrv_sdnn = np.nan
        
    mean_pat = np.mean(pat_res['pat_ms']) if len(pat_res['pat_ms']) > 0 else np.nan
    std_pat = np.std(pat_res['pat_ms']) if len(pat_res['pat_ms']) > 0 else np.nan
    
    return {
        'heart_rate_bpm': float(mean_hr),
        'hrv_sdnn_ms': float(hrv_sdnn),
        'mean_pat_ms': float(mean_pat),
        'std_pat_ms': float(std_pat),
        'num_detected_beats': int(len(r_peaks)),
        'num_matched_pats': int(len(pat_res['pat_ms']))
    }
