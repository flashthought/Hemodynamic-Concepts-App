"""
Physics-Informed Hemodynamic State Estimation
Reduced-Order Cardiovascular Simulator (Windkessel Models)
"""

import numpy as np
from typing import Dict, Tuple, Optional, Callable

class WindkesselSimulator:
    """
    Controllable cardiovascular dynamical simulator based on 2-element
    and 3-element Windkessel differential equations.
    
    Variables and Units:
    - P: Arterial Blood Pressure [mmHg]
    - Q_in: Aortic inflow rate [mL/s]
    - R: Systemic Vascular Resistance [mmHg * s / mL]
         (Normal adult ~0.9 - 1.2 mmHg*s/mL; 1 mmHg*s/mL = 80 dyn*s/cm^5)
    - C: Arterial Compliance [mL / mmHg]
         (Normal adult ~1.0 - 1.5 mL/mmHg)
    - P_v: Central Venous / downstream pressure [mmHg] (default ~4 mmHg)
    - R_c: Characteristic Aortic Impedance [mmHg * s / mL] (~0.05 - 0.1 * R)
    """
    
    def __init__(
        self,
        R_baseline: float = 1.0,
        C_baseline: float = 1.2,
        P_v: float = 4.0,
        R_c_ratio: float = 0.08,
        sample_rate: float = 125.0
    ):
        self.R_baseline = R_baseline
        self.C_baseline = C_baseline
        self.P_v = P_v
        self.R_c_ratio = R_c_ratio
        self.sample_rate = sample_rate

    def generate_inflow(
        self,
        duration: float,
        heart_rate: float = 75.0,
        stroke_volume: float = 70.0,
        systole_fraction: float = 0.35
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generates physiological pulsatile cardiac inflow Q_in(t) [mL/s].
        """
        dt = 1.0 / self.sample_rate
        t = np.arange(0, duration, dt)
        t_cycle = 60.0 / heart_rate
        t_sys = systole_fraction * t_cycle
        
        phase = np.mod(t, t_cycle)
        amplitude = stroke_volume * np.pi / (2.0 * t_sys)
        
        q_in = np.where(
            phase < t_sys,
            amplitude * np.sin(np.pi * phase / t_sys),
            0.0
        )
        return t, q_in

    def simulate_2element(
        self,
        duration: float,
        heart_rate: float = 75.0,
        stroke_volume: float = 70.0,
        systole_fraction: float = 0.35,
        R_func: Optional[Callable[[float], float]] = None,
        C_func: Optional[Callable[[float], float]] = None,
        P_init: float = 80.0
    ) -> Dict[str, np.ndarray]:
        """
        Simulate 2-Element Windkessel:
            C(t) * dP/dt = Q_in(t) - (P(t) - P_v) / R(t)
        """
        t, q_in = self.generate_inflow(duration, heart_rate, stroke_volume, systole_fraction)
        dt = 1.0 / self.sample_rate
        n_steps = len(t)
        
        r_traj = np.array([R_func(ti) if R_func else self.R_baseline for ti in t])
        c_traj = np.array([C_func(ti) if C_func else self.C_baseline for ti in t])
        
        p = np.zeros(n_steps)
        p[0] = P_init
        
        for i in range(n_steps - 1):
            qi = q_in[i]
            ri = r_traj[i]
            ci = c_traj[i]
            
            dp1 = (qi - (p[i] - self.P_v) / ri) / ci
            p_mid = p[i] + 0.5 * dt * dp1
            q_mid = 0.5 * (qi + q_in[i+1])
            r_mid = 0.5 * (ri + r_traj[i+1])
            c_mid = 0.5 * (ci + c_traj[i+1])
            dp2 = (q_mid - (p_mid - self.P_v) / r_mid) / c_mid
            
            p[i+1] = p[i] + dt * dp2
            
        return {
            't': t,
            'abp': p,
            'q_in': q_in,
            'R': r_traj,
            'C': c_traj
        }

    def simulate_3element(
        self,
        duration: float,
        heart_rate: float = 75.0,
        stroke_volume: float = 70.0,
        systole_fraction: float = 0.35,
        R_func: Optional[Callable[[float], float]] = None,
        C_func: Optional[Callable[[float], float]] = None,
        P_init: float = 80.0
    ) -> Dict[str, np.ndarray]:
        """
        Simulate 3-Element Windkessel:
            Total R = R_c + R_p
            C * dP_c/dt = Q_in - (P_c - P_v) / R_p
            P_aortic = P_c + Q_in * R_c
        """
        t, q_in = self.generate_inflow(duration, heart_rate, stroke_volume, systole_fraction)
        dt = 1.0 / self.sample_rate
        n_steps = len(t)
        
        r_total = np.array([R_func(ti) if R_func else self.R_baseline for ti in t])
        c_traj = np.array([C_func(ti) if C_func else self.C_baseline for ti in t])
        
        r_c = self.R_c_ratio * r_total
        r_p = (1.0 - self.R_c_ratio) * r_total
        
        p_c = np.zeros(n_steps)
        p_c[0] = P_init
        
        for i in range(n_steps - 1):
            qi = q_in[i]
            ci = c_traj[i]
            rpi = r_p[i]
            
            dp1 = (qi - (p_c[i] - self.P_v) / rpi) / ci
            p_mid = p_c[i] + 0.5 * dt * dp1
            q_mid = 0.5 * (qi + q_in[i+1])
            c_mid = 0.5 * (ci + c_traj[i+1])
            rp_mid = 0.5 * (rpi + r_p[i+1])
            
            dp2 = (q_mid - (p_mid - self.P_v) / rp_mid) / c_mid
            p_c[i+1] = p_c[i] + dt * dp2
            
        p_aortic = p_c + q_in * r_c
        
        return {
            't': t,
            'abp': p_aortic,
            'p_c': p_c,
            'q_in': q_in,
            'R': r_total,
            'C': c_traj
        }

    def generate_synthetic_ppg(
        self,
        abp: np.ndarray,
        transit_time_ms: float = 180.0,
        viscoelastic_damping: float = 0.15
    ) -> np.ndarray:
        """
        Synthesizes a peripheral optical PPG waveform from arterial pressure
        incorporating pulse transit delay and low-pass vascular wall filtering.
        """
        shift_samples = int(np.round((transit_time_ms / 1000.0) * self.sample_rate))
        shifted_p = np.roll(abp, shift_samples)
        shifted_p[:shift_samples] = abp[0]
        
        alpha = 1.0 - viscoelastic_damping
        ppg = np.zeros_like(shifted_p)
        ppg[0] = shifted_p[0]
        for k in range(1, len(shifted_p)):
            ppg[k] = alpha * shifted_p[k] + (1.0 - alpha) * ppg[k-1]
            
        p_min, p_max = np.min(ppg), np.max(ppg)
        if p_max > p_min:
            ppg = (ppg - p_min) / (p_max - p_min)
        return ppg

    def add_noise(
        self,
        signal: np.ndarray,
        snr_db: float = 20.0,
        baseline_wander_hz: float = 0.25,
        baseline_amplitude: float = 0.05,
        rng: Optional[np.random.Generator] = None
    ) -> np.ndarray:
        """
        Adds parameterized Gaussian white noise and respiratory baseline wander.
        """
        if rng is None:
            rng = np.random.default_rng()
        t = np.arange(len(signal)) / self.sample_rate
        signal_power = np.mean(signal ** 2)
        snr_linear = 10.0 ** (snr_db / 10.0)
        noise_power = signal_power / snr_linear
        noise = rng.normal(0, np.sqrt(noise_power), size=len(signal))
        
        wander = baseline_amplitude * np.sin(2.0 * np.pi * baseline_wander_hz * t)
        return signal + noise + wander
