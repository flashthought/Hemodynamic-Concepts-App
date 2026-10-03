"""
Publication-oriented synthetic hemodynamic cohort generator.

The synthetic cohort is the only project dataset with known latent resistance,
compliance, and intervention response. This module therefore records the
simulation assumptions, samples parameters from explicit physiological ranges,
uses a 3-element Windkessel model by default, and injects audited artifacts so
model robustness can be evaluated under controlled but realistic corruption.
"""

import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Tuple

import numpy as np
import pandas as pd

from src.physics.windkessel import WindkesselSimulator


SCENARIOS = (
    "stable",
    "septic_shock",
    "vasopressor_rescue",
    "low_compliance",
    "fluid_challenge",
    "mixed_shock_stiff",
    "hemorrhage_resuscitation",
)


PHYSIOLOGICAL_RANGES: Dict[str, Tuple[float, float]] = {
    "heart_rate_bpm": (55.0, 105.0),
    "stroke_volume_ml": (50.0, 84.0),
    "systemic_resistance_mmHg_s_ml": (0.64, 1.12),
    "arterial_compliance_ml_mmHg": (0.80, 1.75),
    "characteristic_resistance_ratio": (0.055, 0.085),
    "systole_fraction": (0.30, 0.38),
    "ppg_transit_time_ms": (430.0, 670.0),
    "ppg_damping": (0.18, 0.42),
    "abp_snr_db": (22.0, 32.0),
    "ppg_snr_db": (16.0, 26.0),
    "ecg_snr_db": (16.0, 26.0),
}


@dataclass
class ScenarioMetadata:
    scenario: str
    resistance_drop_fraction: float = 0.0
    resistance_rescue_fraction: float = 0.0
    compliance_change_fraction: float = 0.0
    vasopressor_onset_sec: float = np.nan
    fluid_onset_sec: float = np.nan
    hemorrhage_onset_sec: float = np.nan


def smooth_step(t: float, start: float, duration: float) -> float:
    """Return a clipped linear ramp from 0 to 1."""
    if duration <= 0:
        return float(t >= start)
    return float(np.clip((t - start) / duration, 0.0, 1.0))


def gamma_response(t: float, onset: float, tau_rise: float, tau_decay: float, amplitude: float = 1.0) -> float:
    """
    Simple pharmacodynamic response with delayed onset, peak, and slow decay.

    It is not drug-specific PK/PD; it is a transparent latent response function
    that is more realistic than an immediate step change.
    """
    x = max(0.0, t - onset)
    if x <= 0.0:
        return 0.0
    rise = 1.0 - np.exp(-x / max(tau_rise, 1e-6))
    decay = np.exp(-x / max(tau_decay, 1e-6))
    return float(amplitude * rise * decay)


def plateau_response(t: float, onset: float, rise_sec: float, amplitude: float = 1.0) -> float:
    """Delayed ramp that plateaus after onset."""
    return float(amplitude * smooth_step(t, onset, rise_sec))


def pressure_window_summary(abp: np.ndarray, fs: float) -> Dict[str, float]:
    """Estimate beat-scale pressure summaries using a one-second rolling window."""
    window = max(2, int(round(fs)))
    pressure = pd.Series(abp)
    rolling_sbp = pressure.rolling(window, min_periods=window).max()
    rolling_dbp = pressure.rolling(window, min_periods=window).min()
    pulse_pressure = rolling_sbp - rolling_dbp
    return {
        "mean_sbp": float(rolling_sbp.mean()),
        "mean_dbp": float(rolling_dbp.mean()),
        "mean_pulse_pressure": float(pulse_pressure.mean()),
    }


def build_scenario(
    scenario_name: str,
    r_base: float,
    c_base: float,
    duration_sec: float,
    rng: np.random.Generator,
) -> Tuple[
    Callable[[float], float],
    Callable[[float], float],
    Callable[[float], float],
    Callable[[float], float],
    ScenarioMetadata,
]:
    """Create R(t), C(t), vasopressor input, fluid input, and audit metadata."""
    t_drop = rng.uniform(0.18, 0.34) * duration_sec
    t_rescue = rng.uniform(0.48, 0.68) * duration_sec
    t_fluid = rng.uniform(0.38, 0.58) * duration_sec
    t_bleed = rng.uniform(0.22, 0.42) * duration_sec

    meta = ScenarioMetadata(scenario=scenario_name)

    if scenario_name == "stable":
        def r_func(t: float) -> float:
            circadian_like = 0.025 * np.sin(2.0 * np.pi * t / max(duration_sec, 1.0))
            return r_base * (1.0 + circadian_like)

        def c_func(t: float) -> float:
            return c_base * (1.0 + 0.015 * np.cos(2.0 * np.pi * t / max(duration_sec, 1.0)))

        u_vaso = lambda t: 0.0
        u_fluid = lambda t: 0.0

    elif scenario_name == "septic_shock":
        r_drop = rng.uniform(0.35, 0.58)
        c_rise = rng.uniform(0.08, 0.24)
        meta.resistance_drop_fraction = float(r_drop)
        meta.compliance_change_fraction = float(c_rise)

        def r_func(t: float) -> float:
            shock = r_drop * smooth_step(t, t_drop, 0.18 * duration_sec)
            return max(0.32, r_base * (1.0 - shock))

        def c_func(t: float) -> float:
            return min(2.1, c_base * (1.0 + c_rise * smooth_step(t, t_drop, 0.25 * duration_sec)))

        u_vaso = lambda t: 0.0
        u_fluid = lambda t: 0.0

    elif scenario_name == "vasopressor_rescue":
        r_drop = rng.uniform(0.35, 0.58)
        rescue_fraction = rng.uniform(0.60, 0.95)
        c_rise = rng.uniform(0.04, 0.14)
        drug_effect_onset = t_rescue + rng.uniform(20.0, 45.0)
        meta.resistance_drop_fraction = float(r_drop)
        meta.resistance_rescue_fraction = float(rescue_fraction)
        meta.compliance_change_fraction = float(c_rise)
        meta.vasopressor_onset_sec = float(t_rescue)

        def r_func(t: float) -> float:
            shock = r_drop * smooth_step(t, t_drop, 0.14 * duration_sec)
            drug_effect = rescue_fraction * r_drop * plateau_response(
                t,
                onset=drug_effect_onset,
                rise_sec=0.18 * duration_sec,
            )
            return max(0.35, r_base * (1.0 - shock + drug_effect))

        def c_func(t: float) -> float:
            return c_base * (1.0 + c_rise * smooth_step(t, t_drop, 0.20 * duration_sec))

        u_vaso = lambda t: plateau_response(t, onset=t_rescue, rise_sec=0.08 * duration_sec)
        u_fluid = lambda t: 0.0

    elif scenario_name == "low_compliance":
        c_drop = rng.uniform(0.25, 0.48)
        r_change = rng.uniform(-0.06, 0.12)
        meta.compliance_change_fraction = float(-c_drop)

        def r_func(t: float) -> float:
            return max(0.55, r_base * (1.0 + r_change * smooth_step(t, t_drop, 0.20 * duration_sec)))

        def c_func(t: float) -> float:
            return max(0.45, c_base * (1.0 - c_drop * smooth_step(t, t_drop, 0.25 * duration_sec)))

        u_vaso = lambda t: 0.0
        u_fluid = lambda t: 0.0

    elif scenario_name == "fluid_challenge":
        c_bump = rng.uniform(0.08, 0.22)
        r_dip = rng.uniform(0.04, 0.12)
        meta.compliance_change_fraction = float(c_bump)
        meta.fluid_onset_sec = float(t_fluid)

        def r_func(t: float) -> float:
            return max(0.55, r_base * (1.0 - r_dip * gamma_response(t, t_fluid + 15.0, 25.0, 300.0)))

        def c_func(t: float) -> float:
            fluid_effect = gamma_response(t, t_fluid + 20.0, 35.0, 420.0)
            return min(2.0, c_base * (1.0 + c_bump * fluid_effect))

        u_vaso = lambda t: 0.0
        u_fluid = lambda t: plateau_response(t, onset=t_fluid, rise_sec=0.08 * duration_sec)

    elif scenario_name == "mixed_shock_stiff":
        r_drop = rng.uniform(0.25, 0.48)
        c_drop = rng.uniform(0.18, 0.38)
        rescue_fraction = rng.uniform(0.45, 0.78)
        meta.resistance_drop_fraction = float(r_drop)
        meta.resistance_rescue_fraction = float(rescue_fraction)
        meta.compliance_change_fraction = float(-c_drop)
        meta.vasopressor_onset_sec = float(t_rescue)
        meta.fluid_onset_sec = float(t_fluid)

        def r_func(t: float) -> float:
            shock = r_drop * smooth_step(t, t_drop, 0.14 * duration_sec)
            drug = rescue_fraction * r_drop * gamma_response(t, t_rescue + 20.0, 40.0, 480.0)
            return max(0.38, r_base * (1.0 - shock + drug))

        def c_func(t: float) -> float:
            stiff = c_drop * smooth_step(t, t_drop, 0.25 * duration_sec)
            fluid = 0.08 * gamma_response(t, t_fluid + 15.0, 35.0, 360.0)
            return max(0.45, c_base * (1.0 - stiff + fluid))

        u_vaso = lambda t: plateau_response(t, onset=t_rescue, rise_sec=0.10 * duration_sec)
        u_fluid = lambda t: plateau_response(t, onset=t_fluid, rise_sec=0.10 * duration_sec)

    elif scenario_name == "hemorrhage_resuscitation":
        r_rise = rng.uniform(0.10, 0.28)
        c_drop = rng.uniform(0.08, 0.22)
        fluid_recovery = rng.uniform(0.35, 0.65)
        meta.resistance_rescue_fraction = float(r_rise)
        meta.compliance_change_fraction = float(-c_drop)
        meta.hemorrhage_onset_sec = float(t_bleed)
        meta.fluid_onset_sec = float(t_fluid)

        def r_func(t: float) -> float:
            compensation = r_rise * smooth_step(t, t_bleed, 0.18 * duration_sec)
            resuscitation = fluid_recovery * r_rise * gamma_response(t, t_fluid + 20.0, 45.0, 520.0)
            return max(0.50, r_base * (1.0 + compensation - resuscitation))

        def c_func(t: float) -> float:
            bleed = c_drop * smooth_step(t, t_bleed, 0.20 * duration_sec)
            fluid = 0.10 * gamma_response(t, t_fluid + 20.0, 40.0, 420.0)
            return max(0.48, c_base * (1.0 - bleed + fluid))

        u_vaso = lambda t: 0.0
        u_fluid = lambda t: plateau_response(t, onset=t_fluid, rise_sec=0.08 * duration_sec)

    else:
        raise ValueError(f"Unknown scenario: {scenario_name}")

    return r_func, c_func, u_vaso, u_fluid, meta


def generate_ecg_impulses(t: np.ndarray, heart_rate: float, fs: float, rng: np.random.Generator) -> np.ndarray:
    """Generate simple R-peak impulses with mild beat-to-beat variability."""
    ecg = np.zeros_like(t, dtype=np.float32)
    t_cycle = 60.0 / heart_rate
    current_t = 0.04
    while current_t < t[-1]:
        idx = int(current_t * fs)
        if 0 <= idx < len(ecg):
            ecg[idx] = 1.0
            if idx > 0:
                ecg[idx - 1] = 0.45
            if idx < len(ecg) - 1:
                ecg[idx + 1] = 0.45
        current_t += max(0.35, t_cycle + rng.normal(0.0, 0.025 * t_cycle))
    return ecg


def add_measurement_artifacts(
    signal: np.ndarray,
    fs: float,
    rng: np.random.Generator,
    modality: str,
    artifact_prob: float,
) -> Tuple[np.ndarray, Dict[str, float]]:
    """Inject audited measurement artifacts into one waveform."""
    x = np.asarray(signal, dtype=np.float32).copy()
    n = len(x)
    audit = {
        f"{modality}_artifact_bursts": 0.0,
        f"{modality}_dropout_segments": 0.0,
        f"{modality}_flatline_segments": 0.0,
        f"{modality}_clipped": 0.0,
        f"{modality}_artifact_fraction": 0.0,
    }
    if n == 0 or artifact_prob <= 0:
        return x, audit

    artifact_mask = np.zeros(n, dtype=bool)

    if rng.random() < artifact_prob:
        num_bursts = int(rng.integers(1, 4))
        audit[f"{modality}_artifact_bursts"] = float(num_bursts)
        scale = max(float(np.nanstd(x)), 1e-3)
        for _ in range(num_bursts):
            width = int(rng.uniform(0.25, 1.5) * fs)
            start = int(rng.integers(0, max(1, n - width)))
            stop = min(n, start + width)
            x[start:stop] += rng.normal(0.0, scale * rng.uniform(2.0, 5.0), stop - start)
            artifact_mask[start:stop] = True

    if rng.random() < artifact_prob * 0.7:
        width = int(rng.uniform(0.2, 1.2) * fs)
        start = int(rng.integers(0, max(1, n - width)))
        stop = min(n, start + width)
        x[start:stop] = np.nan
        artifact_mask[start:stop] = True
        audit[f"{modality}_dropout_segments"] = 1.0

    if rng.random() < artifact_prob * 0.5:
        width = int(rng.uniform(0.5, 2.5) * fs)
        start = int(rng.integers(0, max(1, n - width)))
        stop = min(n, start + width)
        fill_value = float(x[max(0, start - 1)]) if start > 0 and np.isfinite(x[start - 1]) else float(np.nanmedian(x))
        x[start:stop] = fill_value
        artifact_mask[start:stop] = True
        audit[f"{modality}_flatline_segments"] = 1.0

    if modality == "abp" and rng.random() < artifact_prob * 0.5:
        width = int(rng.uniform(0.15, 0.6) * fs)
        start = int(rng.integers(0, max(1, n - width)))
        stop = min(n, start + width)
        x[start:stop] += rng.uniform(80.0, 160.0)
        artifact_mask[start:stop] = True
        audit[f"{modality}_artifact_bursts"] += 1.0

    if rng.random() < artifact_prob * 0.4:
        lo, hi = np.nanpercentile(x, [2, 98])
        if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
            x = np.clip(x, lo, hi)
            audit[f"{modality}_clipped"] = 1.0

    audit[f"{modality}_artifact_fraction"] = float(np.mean(artifact_mask))
    return x.astype(np.float32), audit


def generate_cohort(
    num_patients: int = 300,
    duration_sec: float = 120.0,
    fs: float = 125.0,
    output_dir: str = "data/synthetic",
    seed: int = 42,
    simulator_model: str = "3element",
    artifact_prob: float = 0.12,
) -> pd.DataFrame:
    """Generate a reproducible cohort with audited physiology and artifacts."""
    os.makedirs(output_dir, exist_ok=True)
    rng_phys = np.random.default_rng(seed)
    rng_artifact = np.random.default_rng(seed + 10_000)
    summary_records = []

    config = {
        "seed": seed,
        "num_patients": num_patients,
        "duration_sec": duration_sec,
        "fs": fs,
        "simulator_model": simulator_model,
        "artifact_prob": artifact_prob,
        "scenarios": list(SCENARIOS),
        "physiological_ranges": PHYSIOLOGICAL_RANGES,
        "notes": (
            "Ranges are intentionally broad adult perioperative/ICU plausible ranges. "
            "Use the saved cohort_summary.csv to report the exact sampled distribution."
        ),
    }
    with open(os.path.join(output_dir, "simulator_config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    for pid in range(1, num_patients + 1):
        hr_base = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["heart_rate_bpm"])
        sv_base = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["stroke_volume_ml"])
        r_base = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["systemic_resistance_mmHg_s_ml"])
        c_base = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["arterial_compliance_ml_mmHg"])
        r_c_ratio = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["characteristic_resistance_ratio"])
        systole_fraction = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["systole_fraction"])
        scenario_name = SCENARIOS[(pid - 1) % len(SCENARIOS)]

        sim = WindkesselSimulator(
            R_baseline=r_base,
            C_baseline=c_base,
            R_c_ratio=r_c_ratio,
            sample_rate=fs,
        )
        r_func, c_func, u_vaso, u_fluid, scenario_meta = build_scenario(
            scenario_name,
            r_base,
            c_base,
            duration_sec,
            rng_phys,
        )

        simulate = sim.simulate_3element if simulator_model == "3element" else sim.simulate_2element
        res = simulate(
            duration=duration_sec,
            heart_rate=hr_base,
            stroke_volume=sv_base,
            systole_fraction=systole_fraction,
            R_func=r_func,
            C_func=c_func,
        )

        t = res["t"]
        abp = res["abp"]
        r_traj = res["R"]
        c_traj = res["C"]
        q_in = res["q_in"]

        transit_time_ms = rng_phys.uniform(*PHYSIOLOGICAL_RANGES["ppg_transit_time_ms"])
        ppg_clean = sim.generate_synthetic_ppg(
            abp,
            transit_time_ms=transit_time_ms,
            viscoelastic_damping=rng_phys.uniform(*PHYSIOLOGICAL_RANGES["ppg_damping"]),
        )
        ecg_clean = generate_ecg_impulses(t, heart_rate=hr_base, fs=fs, rng=rng_phys)

        abp_noisy = sim.add_noise(
            abp,
            snr_db=rng_artifact.uniform(*PHYSIOLOGICAL_RANGES["abp_snr_db"]),
            baseline_amplitude=rng_artifact.uniform(0.5, 2.0),
            rng=rng_artifact,
        )
        ppg_noisy = sim.add_noise(
            ppg_clean,
            snr_db=rng_artifact.uniform(*PHYSIOLOGICAL_RANGES["ppg_snr_db"]),
            baseline_amplitude=rng_artifact.uniform(0.01, 0.05),
            rng=rng_artifact,
        )
        ecg_noisy = sim.add_noise(
            ecg_clean,
            snr_db=rng_artifact.uniform(*PHYSIOLOGICAL_RANGES["ecg_snr_db"]),
            baseline_amplitude=rng_artifact.uniform(0.02, 0.08),
            rng=rng_artifact,
        )

        artifact_audit: Dict[str, float] = {}
        abp_noisy, audit = add_measurement_artifacts(abp_noisy, fs, rng_artifact, "abp", artifact_prob)
        artifact_audit.update(audit)
        ppg_noisy, audit = add_measurement_artifacts(ppg_noisy, fs, rng_artifact, "ppg", artifact_prob)
        artifact_audit.update(audit)
        ecg_noisy, audit = add_measurement_artifacts(ecg_noisy, fs, rng_artifact, "ecg", artifact_prob)
        artifact_audit.update(audit)

        vaso_traj = np.array([u_vaso(float(ti)) for ti in t], dtype=np.float32)
        fluid_traj = np.array([u_fluid(float(ti)) for ti in t], dtype=np.float32)
        intervention_traj = np.maximum(vaso_traj, fluid_traj).astype(np.float32)

        fname = f"patient_{pid:03d}_{scenario_name}.npz"
        np.savez_compressed(
            os.path.join(output_dir, fname),
            t=t.astype(np.float32),
            fs=float(fs),
            simulator_model=simulator_model,
            abp_clean=abp.astype(np.float32),
            abp_noisy=abp_noisy.astype(np.float32),
            ppg_clean=ppg_clean.astype(np.float32),
            ppg_noisy=ppg_noisy.astype(np.float32),
            ecg_clean=ecg_clean.astype(np.float32),
            ecg_noisy=ecg_noisy.astype(np.float32),
            q_in=q_in.astype(np.float32),
            R_ground_truth=r_traj.astype(np.float32),
            C_ground_truth=c_traj.astype(np.float32),
            u_vasopressor=vaso_traj,
            u_fluid=fluid_traj,
            u_intervention=intervention_traj,
            scenario=scenario_name,
            artifact_prob=float(artifact_prob),
        )

        summary_record = {
            "patient_id": pid,
            "scenario": scenario_name,
            "file_name": fname,
            "simulator_model": simulator_model,
            "mean_map": float(np.mean(abp)),
            "min_abp": float(np.min(abp)),
            "max_abp": float(np.max(abp)),
            "mean_r": float(np.mean(r_traj)),
            "r_baseline": float(r_base),
            "mean_c": float(np.mean(c_traj)),
            "c_baseline": float(c_base),
            "heart_rate": float(hr_base),
            "stroke_volume": float(sv_base),
            "r_c_ratio": float(r_c_ratio),
            "systole_fraction": float(systole_fraction),
            "ppg_transit_time_ms": float(transit_time_ms),
            "has_vasopressor": bool(np.max(vaso_traj) > 0.0),
            "has_fluid": bool(np.max(fluid_traj) > 0.0),
        }
        summary_record.update(pressure_window_summary(abp, fs))
        summary_record.update(asdict(scenario_meta))
        summary_record.update(artifact_audit)
        summary_records.append(summary_record)

    df_summary = pd.DataFrame(summary_records)
    summary_csv = os.path.join(output_dir, "cohort_summary.csv")
    df_summary.to_csv(summary_csv, index=False)
    print(f"Generated {num_patients} synthetic patient trajectories in {output_dir}")
    print(f"Cohort summary saved to {summary_csv}")
    print(f"Simulator configuration saved to {os.path.join(output_dir, 'simulator_config.json')}")
    return df_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-patients", type=int, default=300)
    parser.add_argument("--duration-sec", type=float, default=120.0)
    parser.add_argument("--fs", type=float, default=125.0)
    parser.add_argument("--output-dir", default="data/synthetic")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--simulator-model", choices=["2element", "3element"], default="3element")
    parser.add_argument(
        "--artifact-prob",
        type=float,
        default=0.12,
        help="Per-modality probability of artifact injection. Use 0 for clean synthetic waveforms.",
    )
    args = parser.parse_args()

    generate_cohort(
        num_patients=args.num_patients,
        duration_sec=args.duration_sec,
        fs=args.fs,
        output_dir=args.output_dir,
        seed=args.seed,
        simulator_model=args.simulator_model,
        artifact_prob=args.artifact_prob,
    )


if __name__ == "__main__":
    main()
