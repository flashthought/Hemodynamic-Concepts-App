"""
Select the demo recordings shown in the web app.

Only HELD-OUT data is eligible, so the app never replays a patient the model
learned from:
  * synthetic: the test split produced by the same split_case_ids() function
    Version 5 training used (src/models/train.py).
  * vitaldb:   data/processed/vitaldb_finetune_test_cases.txt from Version 5
    real-data fine-tuning.

Within the held-out pool, recordings are picked for a clear clinical story
(e.g. a septic-shock patient whose MAP actually crosses 65 mmHg). Selection
rationale and provenance are written to demo_data/manifest.json.

Usage:  python scripts/build_demo_data.py --version5 "/home/kyei/Document/Version 5"
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.models.train import split_case_ids  # noqa: E402

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rolling_map(abp: np.ndarray, fs: float, sec: float = 10.0) -> np.ndarray:
    n = max(1, int(sec * fs))
    x = np.nan_to_num(abp.astype(np.float64), nan=np.nanmedian(abp))
    kernel = np.ones(n) / n
    return np.convolve(x, kernel, mode="valid")


def synthetic_pool(version5: str):
    files = sorted(glob.glob(os.path.join(version5, "data/synthetic/patient_*.npz")))
    by_case = {}
    for path in files:
        pid = int(re.match(r"patient_(\d+)_", os.path.basename(path)).group(1))
        by_case[f"synthetic_{pid:03d}"] = path
    _, _, test_ids = split_case_ids(list(by_case.keys()))
    return {cid: by_case[cid] for cid in test_ids}


def describe_synthetic(path: str):
    d = np.load(path, allow_pickle=True)
    fs = float(d["fs"])
    m = rolling_map(d["abp_clean"], fs)
    start = float(np.mean(m[: int(10 * fs)]))
    vaso_on = np.where(d["u_vasopressor"] > 0.05)[0]
    fluid_on = np.where(d["u_fluid"] > 0.05)[0]
    return {
        "scenario": str(d["scenario"]),
        "start_map": start,
        "min_map": float(np.min(m)),
        "end_map": float(np.mean(m[-int(10 * fs):])),
        "frac_below_65": float(np.mean(m < 65.0)),
        "vaso_onset": float(vaso_on[0] / fs) if vaso_on.size else None,
        "fluid_onset": float(fluid_on[0] / fs) if fluid_on.size else None,
    }


def normotensive_start(s, lo=75.0, hi=95.0):
    """Representative patients start in a normal MAP range, not at an extreme."""
    return lo <= s["start_map"] <= hi


# Story rules per scenario: (score function, human-readable reason). Scores pick
# a representative patient with a clear story; 0 means "not eligible".
STORIES = {
    "septic_shock": (
        lambda s: normotensive_start(s) * (s["min_map"] < 60) * (100 - abs(s["min_map"] - 48)),
        "Starts normotensive and deteriorates below MAP 65 - demonstrates early deterioration alerts.",
    ),
    "vasopressor_rescue": (
        lambda s: normotensive_start(s) * (s["vaso_onset"] is not None) * (s["min_map"] < 65)
        * max(0.0, s["end_map"] - s["min_map"]),
        "Hypotension followed by vasopressor rescue - demonstrates intervention-response tracking.",
    ),
    "mixed_shock_stiff": (
        lambda s: normotensive_start(s) * (s["vaso_onset"] is not None) * (s["min_map"] < 60)
        * max(0.0, s["end_map"] - s["min_map"] + 1),
        "Mixed shock with stiff vessels treated with vasopressor and fluid - most complex physiology.",
    ),
    "fluid_challenge": (
        lambda s: normotensive_start(s) * (s["fluid_onset"] is not None) * max(0.0, s["end_map"] - s["min_map"] + 0.1),
        "Fluid bolus in a normotensive patient - tests response assessment when the MAP change is small.",
    ),
    "hemorrhage_resuscitation": (
        lambda s: normotensive_start(s) * (s["fluid_onset"] is not None) * max(0.0, s["start_map"] - s["min_map"] + 0.1),
        "Haemorrhage compensated by vasoconstriction, then fluid - tests R/C tracking when MAP is maintained.",
    ),
    "stable": (
        lambda s: normotensive_start(s) * (s["min_map"] >= 72) * (100 - abs(s["start_map"] - 85)),
        "Haemodynamically stable throughout - checks the system does not raise false alerts.",
    ),
    "low_compliance": (
        lambda s: normotensive_start(s) * (s["min_map"] >= 65) * (100 - abs(s["start_map"] - 85)),
        "Reduced arterial compliance with stable MAP - checks compliance (C) tracking.",
    ),
}


def vitaldb_pool(version5: str):
    split_file = os.path.join(version5, "data/processed/vitaldb_finetune_test_cases.txt")
    with open(split_file, "r", encoding="utf-8") as f:
        test_cases = [line.strip() for line in f if line.strip()]
    pool = {}
    for case in test_cases:
        num = int(case.split("_")[1])
        path = os.path.join(version5, "data/raw/vitaldb", f"case_{num:04d}.npz")
        if os.path.exists(path):
            pool[case] = path
    return pool


def describe_vitaldb(path: str):
    d = np.load(path, allow_pickle=True)
    fs = float(d["fs"])
    abp = d["abp"].astype(np.float64)
    valid = np.isfinite(abp) & (abp > 20) & (abp < 220)
    abp_c = np.where(valid, abp, np.nan)
    m = rolling_map(np.where(np.isfinite(abp_c), abp_c, np.nanmedian(abp_c)), fs)
    interv = np.nan_to_num(d["intervention"].astype(np.float64), nan=0.0)
    return {
        "valid_abp_fraction": float(np.mean(valid)),
        "min_map": float(np.nanpercentile(m, 1)),
        "median_map": float(np.nanmedian(m)),
        "frac_below_65": float(np.mean(m < 65.0)),
        "has_intervention": bool(np.any(interv > 0)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version5", default="/home/kyei/Document/Version 5")
    args = ap.parse_args()

    out_root = os.path.join(APP_ROOT, "demo_data")
    recordings = []

    syn = synthetic_pool(args.version5)
    print(f"synthetic held-out test pool: {len(syn)} patients")
    described = {cid: describe_synthetic(p) for cid, p in syn.items()}
    for scenario, (score_fn, reason) in STORIES.items():
        candidates = [(score_fn(s), cid) for cid, s in described.items() if s["scenario"] == scenario]
        candidates = [c for c in candidates if c[0] > 0]
        if not candidates:
            print(f"  ! no suitable held-out patient for {scenario}")
            continue
        _, best = max(candidates)
        src = syn[best]
        dst = os.path.join(out_root, "synthetic", os.path.basename(src))
        shutil.copy2(src, dst)
        s = described[best]
        recordings.append({
            "id": best,
            "source": "synthetic",
            "path": os.path.relpath(dst, out_root),
            "title": f"Simulated: {scenario.replace('_', ' ')}",
            "description": reason,
            "split": "held-out synthetic test split (never used in training)",
            "stats": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()},
        })
        print(f"  {scenario:26s} -> {best}  start {s['start_map']:.0f}  min {s['min_map']:.0f}  end {s['end_map']:.0f}")

    vit = vitaldb_pool(args.version5)
    print(f"vitaldb held-out test pool: {len(vit)} cases")
    vdesc = {}
    for cid, p in vit.items():
        try:
            vdesc[cid] = describe_vitaldb(p)
        except Exception as exc:  # unreadable case: skip, keep going
            print(f"  skip {cid}: {exc}")
    good = {c: s for c, s in vdesc.items() if s["valid_abp_fraction"] >= 0.95}
    picks = []
    # 1-2: vasopressor-treated cases with real hypotension (richest story)
    treated = sorted((c for c, s in good.items() if s["has_intervention"] and s["frac_below_65"] > 0.05),
                     key=lambda c: -good[c]["frac_below_65"])
    picks += [(c, "Real surgical case with hypotension and vasopressor infusion.") for c in treated[:2]]
    # 3: hypotension without vasopressor
    untreated = sorted((c for c, s in good.items() if not s["has_intervention"] and s["frac_below_65"] > 0.10),
                       key=lambda c: -good[c]["frac_below_65"])
    picks += [(c, "Real surgical case with hypotensive periods and no recorded vasopressor.") for c in untreated[:1]]
    # 4: stable real case (false-alert check)
    stable = sorted((c for c, s in good.items() if s["frac_below_65"] < 0.01 and s["min_map"] > 70),
                    key=lambda c: -good[c]["valid_abp_fraction"])
    picks += [(c, "Real surgical case that stays normotensive - false-alert check.") for c in stable[:1]]

    for cid, reason in picks:
        src = vit[cid]
        dst = os.path.join(out_root, "vitaldb", os.path.basename(src))
        shutil.copy2(src, dst)
        s = vdesc[cid]
        recordings.append({
            "id": cid,
            "source": "vitaldb",
            "path": os.path.relpath(dst, out_root),
            "title": f"Real (VitalDB): case {cid.split('_')[1]}",
            "description": reason,
            "split": "held-out VitalDB fine-tuning test split (never used in training)",
            "stats": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()},
        })
        print(f"  {cid}: below65 {s['frac_below_65']:.2f}  min {s['min_map']:.0f}  interv {s['has_intervention']}")

    manifest = {
        "description": "Demo recordings for the Hemodynamic Concepts App. All held out from Version 5 training.",
        "version5_source": args.version5,
        "recordings": recordings,
    }
    with open(os.path.join(out_root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {len(recordings)} recordings to demo_data/manifest.json")


if __name__ == "__main__":
    main()
