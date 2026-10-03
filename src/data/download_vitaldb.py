"""
Download raw VitalDB waveform (and optional intervention/context) tracks
into per-case .npz files under data/raw/vitaldb/.

VitalDB (https://vitaldb.net) serves full-case .vital files over HTTP; the
`vitaldb` python package exposes this as vitaldb.load_case(caseid,
track_names, interval), returning one 2D array (time x track) aligned on
the case's absolute timeline. Case selection uses the (deprecated but still
served) per-track index at https://api.vitaldb.net/trks to find cases that
have all three required waveform tracks, optionally prioritizing cases that
also have a vasopressor infusion-rate track so downstream intervention-
response plots have real examples to show.

Each case can span several hours; --max-duration-sec bounds how much of the
recording is kept so the raw archive and the windows built from it later
stay a manageable size. When the case has a vasopressor track, the kept
segment is centered on the first nonzero infusion sample (with lead-in) so
the intervention response is not cropped out.
"""

import argparse
import os
import warnings
from typing import List, Optional

import numpy as np
import pandas as pd

REQUIRED_WAVEFORM_TRACKS = {
    "ecg": "SNUADC/ECG_II",
    "ppg": "SNUADC/PLETH",
    "abp": "SNUADC/ART",
}
VASOPRESSOR_TRACKS = (
    "Orchestra/NEPI_RATE",
    "Orchestra/PHEN_RATE",
    "Orchestra/EPI_RATE",
    "Orchestra/VASO_RATE",
)
CONTEXT_FIELDS = (
    "age",
    "sex",
    "bmi",
    "department",
    "optype",
    "asa",
    "emop",
    "intraop_crystalloid",
    "intraop_colloid",
    "intraop_rbc",
    "intraop_ffp",
    "intraop_phe",
    "intraop_eph",
    "intraop_epi",
)


def select_case_ids(include_interventions: bool, max_cases: Optional[int]) -> List[int]:
    """Rank waveform-complete case IDs, preferring ones with a vasopressor track."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        trks = pd.read_csv("https://api.vitaldb.net/trks")

    waveform_sets = [set(trks.loc[trks["tname"] == tname, "caseid"]) for tname in REQUIRED_WAVEFORM_TRACKS.values()]
    complete_cases = set.intersection(*waveform_sets)

    if include_interventions:
        vaso_cases = set()
        for tname in VASOPRESSOR_TRACKS:
            vaso_cases |= set(trks.loc[trks["tname"] == tname, "caseid"])
        prioritized = sorted(complete_cases & vaso_cases)
        remainder = sorted(complete_cases - vaso_cases)
        ordered = prioritized + remainder
        print(f"{len(prioritized)} cases have both full waveforms and a vasopressor track.")
    else:
        ordered = sorted(complete_cases)

    print(f"{len(complete_cases)} VitalDB cases have ECG/PPG/ABP waveforms available.")
    if max_cases is not None:
        ordered = ordered[:max_cases]
    return [int(c) for c in ordered]


def download_case(
    caseid: int,
    output_dir: str,
    fs: float,
    include_interventions: bool,
    include_context: bool,
    max_duration_sec: float,
    clinical_row: Optional[pd.Series],
) -> Optional[dict]:
    import vitaldb

    interval = 1.0 / fs
    waveform_names = list(REQUIRED_WAVEFORM_TRACKS.values())
    track_names = list(waveform_names)
    if include_interventions:
        track_names += list(VASOPRESSOR_TRACKS)

    arr = vitaldb.load_case(caseid, track_names, interval=interval)
    if arr is None or arr.shape[0] < int(10 * fs):
        return None

    ecg = arr[:, 0].astype(np.float32)
    ppg = arr[:, 1].astype(np.float32)
    abp = arr[:, 2].astype(np.float32)

    if include_interventions and arr.shape[1] > 3:
        intervention_tracks = arr[:, 3:]
        all_nan_rows = np.all(np.isnan(intervention_tracks), axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            intervention = np.nanmax(intervention_tracks, axis=1)
        intervention[all_nan_rows] = 0.0
        intervention = intervention.astype(np.float32)
    else:
        intervention = np.zeros_like(ecg)

    n = len(ecg)
    max_samples = int(max_duration_sec * fs)
    if n > max_samples:
        nonzero = np.where(np.nan_to_num(intervention) > 0)[0]
        if len(nonzero) > 0:
            onset = int(nonzero[0])
            lead_in = int(300 * fs)
            start = max(0, onset - lead_in)
            start = min(start, n - max_samples)
        else:
            start = 0
        end = start + max_samples
        ecg, ppg, abp, intervention = ecg[start:end], ppg[start:end], abp[start:end], intervention[start:end]

    has_intervention = bool(np.nanmax(intervention) > 0) if intervention.size else False

    payload = {
        "ecg": ecg,
        "ppg": ppg,
        "abp": abp,
        "intervention": intervention,
        "fs": np.float32(fs),
        "case_id": f"vitaldb_{caseid:04d}",
        "has_intervention": has_intervention,
    }
    if include_context and clinical_row is not None:
        for field in CONTEXT_FIELDS:
            value = clinical_row.get(field, np.nan)
            payload[f"context_{field}"] = value if pd.notna(value) else np.nan

    out_path = os.path.join(output_dir, f"case_{caseid:04d}.npz")
    np.savez_compressed(out_path, **payload)
    return {
        "case_id": caseid,
        "duration_sec": len(ecg) / fs,
        "has_intervention": has_intervention,
        "file": out_path,
    }


def download_vitaldb(
    output_dir: str = "data/raw/vitaldb",
    include_interventions: bool = False,
    include_context: bool = False,
    max_cases: Optional[int] = None,
    fs: float = 125.0,
    max_duration_sec: float = 1800.0,
) -> int:
    os.makedirs(output_dir, exist_ok=True)
    case_ids = select_case_ids(include_interventions, max_cases)
    if not case_ids:
        raise RuntimeError("No eligible VitalDB cases found (ECG/PPG/ABP tracks required).")

    clinical_df = None
    if include_context:
        import vitaldb

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            clinical_df = vitaldb.load_clinical_data(case_ids).set_index("caseid")

    manifest_rows = []
    for i, caseid in enumerate(case_ids, start=1):
        clinical_row = clinical_df.loc[caseid] if (clinical_df is not None and caseid in clinical_df.index) else None
        try:
            result = download_case(
                caseid,
                output_dir,
                fs=fs,
                include_interventions=include_interventions,
                include_context=include_context,
                max_duration_sec=max_duration_sec,
                clinical_row=clinical_row,
            )
        except Exception as exc:
            print(f"[{i}/{len(case_ids)}] case {caseid}: failed ({exc})")
            continue

        if result is None:
            print(f"[{i}/{len(case_ids)}] case {caseid}: too short, skipped")
            continue

        manifest_rows.append(result)
        flag = "with intervention" if result["has_intervention"] else "no intervention"
        print(f"[{i}/{len(case_ids)}] case {caseid}: {result['duration_sec']:.0f}s downloaded ({flag})")

    manifest_path = os.path.join(output_dir, "vitaldb_manifest.csv")
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
    print(f"Downloaded {len(manifest_rows)} VitalDB cases to {output_dir}")
    print(f"Wrote manifest to {manifest_path}")
    return len(manifest_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data/raw/vitaldb")
    parser.add_argument("--include-interventions", action="store_true")
    parser.add_argument("--include-context", action="store_true")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--fs", type=float, default=125.0)
    parser.add_argument(
        "--max-duration-sec",
        type=float,
        default=1800.0,
        help="Cap on downloaded duration per case; long cases are cropped around the "
        "vasopressor onset when present, otherwise from the start.",
    )
    args = parser.parse_args()

    download_vitaldb(
        output_dir=args.output_dir,
        include_interventions=args.include_interventions,
        include_context=args.include_context,
        max_cases=args.max_cases,
        fs=args.fs,
        max_duration_sec=args.max_duration_sec,
    )


if __name__ == "__main__":
    main()
