"""
Verification tests for the three concepts (BME Design Ch. 6.5.1 feasibility
tests and Ch. 9 design verification: "did we make the product right?").

Every number in the concept-selection matrix (docs/04) comes from this script.
It runs the SAME engine code as the web app (ReplaySession, AlertStateMachine,
InterventionTracker), so it verifies the deployed behaviour, not a re-implementation.

Phases
  1. Calibration  - synthetic VALIDATION patients: per-regime forecast bias and
                    residual RMSE -> evaluation/results/calibration.json.
  2. Verification - synthetic TEST patients (never used for training or
                    calibration): accuracy, discrimination, alerts, lead time,
                    false-alert burden, R/C tracking, intervention response.
  3. Real data    - held-out VitalDB test cases against the arterial line.
  4. Safety       - signal-quality gate fed deliberately corrupted windows.
  5. Performance  - CPU and GPU latency; Concept C batch throughput.

Usage:
  python evaluation/evaluate_concepts.py --version5 "/home/kyei/Document/Version 5"
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

import numpy as np
from sklearn.metrics import roc_auc_score

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from app.engine import config  # noqa: E402
from app.engine.alerting import AlertStateMachine  # noqa: E402
from app.engine.intervention import classify_response  # noqa: E402
from app.engine.model_service import ModelService  # noqa: E402
from app.engine.recordings import load_synthetic, load_vitaldb  # noqa: E402
from app.engine.review import run_review  # noqa: E402
from app.engine.session import ReplaySession  # noqa: E402
from src.inference.schemas import MonitorWindow  # noqa: E402
from src.models.train import split_case_ids  # noqa: E402

RESULTS = os.path.join(APP_ROOT, "evaluation", "results")


# --------------------------------------------------------------------------- data
def synthetic_splits(version5: str):
    files = sorted(glob.glob(os.path.join(version5, "data/synthetic/patient_*.npz")))
    by_case = {}
    for path in files:
        pid = int(re.match(r"patient_(\d+)_", os.path.basename(path)).group(1))
        by_case[f"synthetic_{pid:03d}"] = path
    _, val_ids, test_ids = split_case_ids(list(by_case.keys()))
    return by_case, val_ids, test_ids


def stratified_sample(by_case, ids, per_scenario, seed):
    rng = np.random.default_rng(seed)
    groups = defaultdict(list)
    for cid in ids:
        scenario = re.match(r"patient_\d+_(.+)\.npz", os.path.basename(by_case[cid])).group(1)
        groups[scenario].append(cid)
    chosen = []
    for scenario in sorted(groups):
        pool = sorted(groups[scenario])
        k = min(per_scenario, len(pool))
        chosen += list(rng.choice(pool, size=k, replace=False))
    return sorted(chosen)


# ------------------------------------------------------------------- metrics
def auroc(scores, labels):
    labels = np.asarray(labels, dtype=bool)
    if labels.all() or (~labels).all():
        return None
    return float(roc_auc_score(labels, scores))


def runs(flags):
    """Start/end indices of contiguous True runs."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def alert_metrics(patients, step):
    """
    patients: list of dicts with per-tick arrays: t, level, true_future, true_current,
    p_below, conv (conventional alarm flag or None).
    """
    tp = fp = tn = fn = 0
    tp_w = fn_w = fp_w = tn_w = 0
    false_episodes = total_episodes = 0          # DETERIORATING-or-higher alarms
    false_any = total_any = 0                    # any alarm (WATCH or higher): all drive alarm fatigue
    hours = 0.0
    lead_det, lead_watch, lead_conv, missed = [], [], [], 0
    onsets = 0
    p_all, y_all = [], []
    for pt in patients:
        level = np.array([np.nan if v is None else v for v in pt["level"]], dtype=float)
        tf = np.array([np.nan if v is None else v for v in pt["true_future"]], dtype=float)
        tc = np.array([np.nan if v is None else v for v in pt["true_current"]], dtype=float)
        t = np.array(pt["t"], dtype=float)
        hours += len(t) * step / 3600.0
        valid = np.isfinite(level) & np.isfinite(tf)
        event = tf < config.MAP_TARGET
        alert = level >= 2
        watch = level >= 1
        tp += int(np.sum(valid & event & alert)); fn += int(np.sum(valid & event & ~alert))
        fp += int(np.sum(valid & ~event & alert)); tn += int(np.sum(valid & ~event & ~alert))
        tp_w += int(np.sum(valid & event & watch)); fn_w += int(np.sum(valid & event & ~watch))
        fp_w += int(np.sum(valid & ~event & watch)); tn_w += int(np.sum(valid & ~event & ~watch))
        for i in np.where(np.isfinite(np.array([np.nan if v is None else v for v in pt["p_below"]], dtype=float)) & np.isfinite(tf))[0]:
            p_all.append(pt["p_below"][i]); y_all.append(bool(event[i]))

        # Alert episodes: an episode is TRUE if hypotension is present or follows within the horizon.
        hypo_any = (np.nan_to_num(tf, nan=999) < config.MAP_TARGET) | (np.nan_to_num(tc, nan=999) < config.MAP_TARGET)
        horizon_ticks = int(config.FORECAST_HORIZON_SEC / step)
        for a, b in runs(np.nan_to_num(level, nan=-1) >= 2):
            total_episodes += 1
            if not hypo_any[a:min(len(t), b + horizon_ticks + 1)].any():
                false_episodes += 1
        for a, b in runs(np.nan_to_num(level, nan=-1) >= 1):
            total_any += 1
            if not hypo_any[a:min(len(t), b + horizon_ticks + 1)].any():
                false_any += 1

        # Hypotension onsets (true current MAP crosses below 65 after >= 10 s above it).
        quiet_ticks = max(1, int(10.0 / step))
        below = np.nan_to_num(tc, nan=999) < config.MAP_TARGET
        for i in range(quiet_ticks, len(t)):
            if below[i] and not below[i - quiet_ticks:i].any():
                onsets += 1
                lo, hi = t[i] - 60.0, t[i] + 30.0
                win = (t >= lo) & (t <= hi)
                det = np.where(win & (np.nan_to_num(level, nan=-1) >= 2))[0]
                wat = np.where(win & (np.nan_to_num(level, nan=-1) >= 1))[0]
                if det.size:
                    lead_det.append(t[i] - t[det[0]])
                else:
                    missed += 1
                if wat.size:
                    lead_watch.append(t[i] - t[wat[0]])
                if pt.get("conv") is not None:
                    conv = np.array([bool(c) if c is not None else False for c in pt["conv"]])
                    cv = np.where(win & conv)[0]
                    if cv.size:
                        lead_conv.append(t[i] - t[cv[0]])

    def safe(num, den):
        return None if den == 0 else round(num / den, 4)

    return {
        "ticks": tp + fp + tn + fn,
        "event_prevalence": safe(tp + fn, tp + fp + tn + fn),
        "sensitivity_deteriorating": safe(tp, tp + fn),
        "specificity_deteriorating": safe(tn, tn + fp),
        "ppv_deteriorating": safe(tp, tp + fp),
        "sensitivity_watch": safe(tp_w, tp_w + fn_w),
        "specificity_watch": safe(tn_w, tn_w + fp_w),
        "fraction_time_alarming_any": safe(tp_w + fp_w, tp_w + fp_w + tn_w + fn_w),
        "auroc": None if not p_all else (None if auroc(p_all, y_all) is None else round(auroc(p_all, y_all), 4)),
        "alert_episodes": total_episodes,
        "false_alert_episodes": false_episodes,
        "monitored_hours": round(hours, 3),
        "false_alerts_per_hour": None if hours == 0 else round(false_episodes / hours, 3),
        "alert_episodes_per_hour": None if hours == 0 else round(total_episodes / hours, 3),
        "alarm_episodes_any": total_any,
        "false_alarm_episodes_any": false_any,
        "false_alarms_per_hour_any": None if hours == 0 else round(false_any / hours, 3),
        "hypotension_onsets": onsets,
        "onsets_detected_deteriorating": len(lead_det),
        "onsets_missed": missed,
        "median_lead_deteriorating_s": None if not lead_det else round(float(np.median(lead_det)), 1),
        "median_lead_watch_s": None if not lead_watch else round(float(np.median(lead_watch)), 1),
        "median_lead_conventional_s": None if not lead_conv else round(float(np.median(lead_conv)), 1),
    }


def datum_levels(ticks):
    """
    The datum (BME Design 6.4.3): a conventional threshold monitor. It uses the
    SAME thresholds and the SAME escalation/de-escalation persistence as the
    device, but is driven by the MEASURED current MAP instead of a forecast, so
    any difference isolates the value of forecasting itself.
    """
    sm = AlertStateMachine()
    levels = []
    for k in ticks:
        cm = k["current_map"]
        levels.append(sm.update(k["t"], cm, None, cm is not None, 0.0, "High")["level"])
    return levels


def onset_alert_times(pt, tier, step):
    """For each true hypotension onset: (onset time, first alert >= tier in [onset-60 s, onset+30 s])."""
    t = np.array(pt["t"], dtype=float)
    level = np.array([np.nan if v is None else v for v in pt["level"]], dtype=float)
    tc = np.array([np.nan if v is None else v for v in pt["true_current"]], dtype=float)
    below = np.nan_to_num(tc, nan=999) < config.MAP_TARGET
    quiet = max(1, int(10.0 / step))
    out = []
    for i in range(quiet, len(t)):
        if below[i] and not below[i - quiet:i].any():
            win = (t >= t[i] - 60.0) & (t <= t[i] + 30.0) & (np.nan_to_num(level, nan=-1) >= tier)
            hit = np.where(win)[0]
            out.append((t[i], t[hit[0]] if hit.size else None))
    return out


def paired_lead(concept_pts, datum_pts, step):
    """Median of (datum alert time - concept alert time) over hypotension onsets: >0 = concept earlier."""
    result = {}
    for tier, name in ((1, "watch"), (2, "deteriorating")):
        diffs, concept_only, datum_only, neither = [], 0, 0, 0
        for cp, dp in zip(concept_pts, datum_pts):
            for (onset, tc), (_, td) in zip(onset_alert_times(cp, tier, step), onset_alert_times(dp, tier, step)):
                if tc is not None and td is not None:
                    diffs.append(td - tc)
                elif tc is not None:
                    concept_only += 1
                elif td is not None:
                    datum_only += 1
                else:
                    neither += 1
        result[name] = {
            "paired_onsets": len(diffs),
            "median_advantage_s": None if not diffs else round(float(np.median(diffs)), 1),
            "mean_advantage_s": None if not diffs else round(float(np.mean(diffs)), 1),
            "fraction_concept_earlier": None if not diffs else round(float(np.mean(np.array(diffs) > 0)), 3),
            "detected_by_concept_only": concept_only,
            "detected_by_datum_only": datum_only,
            "detected_by_neither": neither,
        }
    return result


def accuracy(pred, truth):
    p = np.array(pred, dtype=float); y = np.array(truth, dtype=float)
    ok = np.isfinite(p) & np.isfinite(y)
    if not ok.any():
        return {"n": 0}
    e = p[ok] - y[ok]
    return {
        "n": int(ok.sum()),
        "mae": round(float(np.mean(np.abs(e))), 3),
        "rmse": round(float(np.sqrt(np.mean(e ** 2))), 3),
        "bias": round(float(np.mean(e)), 3),
        "sd": round(float(np.std(e)), 3),
        "within_5": round(float(np.mean(np.abs(e) <= 5)), 4),
        "within_10": round(float(np.mean(np.abs(e) <= 10)), 4),
    }


def within_patient_corr(per_patient):
    rs = []
    for pred, truth in per_patient:
        p = np.array(pred, dtype=float); y = np.array(truth, dtype=float)
        ok = np.isfinite(p) & np.isfinite(y)
        if ok.sum() >= 10 and np.std(p[ok]) > 1e-6 and np.std(y[ok]) > 1e-6:
            rs.append(float(np.corrcoef(p[ok], y[ok])[0, 1]))
    return None if not rs else round(float(np.median(rs)), 4), len(rs)


# ------------------------------------------------------------------- phases
def run_patient(rec, model, concept, step, calibrate=True):
    s = ReplaySession(rec, model, concept, step_sec=step, calibrate=calibrate)
    ticks = s.run_to_end(include_waveforms=False)
    return ticks


def _fit(errs):
    regimes = {}
    for regime, e in errs.items():
        e = np.array(e)
        bias = float(np.mean(e))
        regimes[regime] = {
            "n": int(e.size),
            "bias_map": round(bias, 3),
            "rmse_map": round(float(np.sqrt(np.mean((e - bias) ** 2))), 3),
            "mae_uncalibrated": round(float(np.mean(np.abs(e))), 3),
            "mae_calibrated": round(float(np.mean(np.abs(e - bias))), 3),
        }
        print(f"      {regime:26s} n={e.size:6d} bias={bias:+7.2f} mae {regimes[regime]['mae_uncalibrated']:6.2f}"
              f" -> {regimes[regime]['mae_calibrated']:6.2f}  rmse(after)={regimes[regime]['rmse_map']:6.2f}")
    return regimes


def vitaldb_cases(version5, split_name):
    path = os.path.join(version5, "data/processed", f"vitaldb_finetune_{split_name}_cases.txt")
    with open(path, "r", encoding="utf-8") as f:
        cases = [line.strip() for line in f if line.strip()]
    eligible = []
    for c in cases:
        p = os.path.join(version5, "data/raw/vitaldb", f"case_{int(c.split('_')[1]):04d}.npz")
        if not os.path.exists(p):
            continue
        abp = np.load(p, allow_pickle=True)["abp"].astype(np.float64)
        if np.mean(np.isfinite(abp) & (abp > 20) & (abp < 220)) >= 0.95:
            eligible.append((c, p))
    return eligible


def calibrate(model, version5, by_case, val_ids, n_real, step, real_step, seed):
    """Domain-specific calibration on VALIDATION data only (test splits stay untouched)."""
    print(f"[1/5] calibration - synthetic: {len(val_ids)} validation patients")
    errs = defaultdict(list)
    for n, cid in enumerate(val_ids, 1):
        rec = load_synthetic(by_case[cid], cid)
        for concept in ("A", "B"):
            for k in run_patient(rec, model, concept, step, calibrate=False):
                if k["forecast_map_uncalibrated"] is not None and k["truth"].get("future_map") is not None:
                    errs[k["regime"]].append(k["forecast_map_uncalibrated"] - k["truth"]["future_map"])
        if n % 35 == 0:
            print(f"      {n}/{len(val_ids)}")
    synthetic = _fit(errs)

    eligible = vitaldb_cases(version5, "val")
    rng = np.random.default_rng(seed + 7)
    picks = [eligible[i] for i in sorted(rng.choice(len(eligible), size=min(n_real, len(eligible)), replace=False))]
    print(f"      calibration - real: {len(picks)} VitalDB validation cases ({len(eligible)} eligible)")
    errs = defaultdict(list)
    for cid, path in picks:
        rec = load_vitaldb(path, cid)
        for concept in ("A", "B"):
            for k in run_patient(rec, model, concept, real_step, calibrate=False):
                if k["forecast_map_uncalibrated"] is not None and k["truth"].get("future_map") is not None:
                    errs[k["regime"]].append(k["forecast_map_uncalibrated"] - k["truth"]["future_map"])
    real = _fit(errs)

    cal = {
        "source": (f"validation splits only - synthetic: {len(val_ids)} patients; "
                   f"real: {len(picks)} VitalDB validation cases"),
        "domains": {
            "synthetic": {"patients": val_ids, "step_sec": step, "regimes": synthetic},
            "real": {"cases": [c for c, _ in picks], "step_sec": real_step, "regimes": real},
        },
    }
    with open(os.path.join(RESULTS, "calibration.json"), "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=2)
    model.clear_calibration()
    model.reload_calibration()
    return cal


def verify_synthetic(model, by_case, test_ids, step):
    print(f"[2/5] verification on {len(test_ids)} synthetic test patients")
    per = {"A": [], "B": [], "DATUM": []}
    acc = {"A": ([], []), "B": ([], [])}
    rc = {"A": {"r": [], "c": []}, "B": {"r": [], "c": []}}
    ua = {"A": [], "B": []}
    agreements = []
    tick_rows = []
    latencies = []
    for n, cid in enumerate(test_ids, 1):
        rec = load_synthetic(by_case[cid], cid)
        for concept in ("A", "B"):
            ticks = run_patient(rec, model, concept, step)
            ua[concept].append(under_alert_stats(ticks))
            latencies += [k["latency_ms"] for k in ticks]
            per[concept].append({
                "t": [k["t"] for k in ticks],
                "level": [k["alert"]["level"] for k in ticks],
                "true_future": [k["truth"]["future_map"] for k in ticks],
                "true_current": [k["truth"]["current_map"] for k in ticks],
                "p_below": [k["p_below_target"] for k in ticks],
            })
            if concept == "A":
                per["DATUM"].append(dict(per["A"][-1], level=datum_levels(ticks), p_below=[None] * len(ticks)))
            acc[concept][0].extend(k["forecast_map"] if k["forecast_map"] is not None else np.nan for k in ticks)
            acc[concept][1].extend(k["truth"]["future_map"] for k in ticks)
            rc[concept]["r"].append(([k["r"] if k["r"] is not None else np.nan for k in ticks], [k["truth"]["r"] for k in ticks]))
            rc[concept]["c"].append(([k["c"] if k["c"] is not None else np.nan for k in ticks], [k["truth"]["c"] for k in ticks]))
            for k in ticks:
                tick_rows.append({
                    "patient": cid, "scenario": rec.scenario, "concept": concept, "t": k["t"],
                    "regime": k["regime"], "quality": k["quality"], "forecast_map": k["forecast_map"],
                    "true_future_map": round(k["truth"]["future_map"], 2), "current_map": k["current_map"],
                    "true_current_map": round(k["truth"]["current_map"], 2), "level": k["alert"]["level_name"],
                    "p_below_target": k["p_below_target"], "r": k["r"], "true_r": round(k["truth"]["r"], 4),
                    "c": k["c"], "true_c": round(k["truth"]["c"], 4), "conventional_alarm": k["conventional_alarm"],
                })
            if concept == "A" and ticks:
                # Intervention-response agreement against ground truth physiology.
                times = np.array([k["t"] for k in ticks])
                tmap = np.array([k["truth"]["current_map"] for k in ticks])
                tr = np.array([k["truth"]["r"] for k in ticks])
                for iv in ticks[-1]["interventions"]:
                    if not iv["final"]:
                        continue
                    t0, ta = iv["onset_t"], iv["assessed_t"]
                    base = (times >= t0 - config.RESPONSE_BASELINE_SEC) & (times < t0)
                    post = (times >= ta - 10.0) & (times <= ta)
                    if base.sum() < 2 or post.sum() < 1:
                        continue
                    d_true = float(np.median(tmap[post]) - np.median(tmap[base]))
                    r_true = float((np.median(tr[post]) - np.median(tr[base])) / np.median(tr[base]))
                    truth_status = classify_response(iv["type"], d_true, r_true)
                    agreements.append({
                        "patient": cid, "scenario": rec.scenario, "type": iv["type"], "onset_t": t0,
                        "app_status": iv["status"], "truth_status": truth_status,
                        "app_d_map": iv["d_map"], "true_d_map": round(d_true, 2),
                        "app_r_change_pct": iv["r_change_pct"], "true_r_change_pct": round(100 * r_true, 2),
                        "confounded": bool(iv.get("confounded_with")), "agree": iv["status"] == truth_status,
                    })
        if n % 25 == 0:
            print(f"      {n}/{len(test_ids)}")

    out = {}
    for concept in ("A", "B"):
        r_med, r_n = within_patient_corr(rc[concept]["r"])
        c_med, c_n = within_patient_corr(rc[concept]["c"])
        all_r = np.concatenate([np.array(p, float) for p, _ in rc[concept]["r"]])
        all_rt = np.concatenate([np.array(t, float) for _, t in rc[concept]["r"]])
        all_c = np.concatenate([np.array(p, float) for p, _ in rc[concept]["c"]])
        all_ct = np.concatenate([np.array(t, float) for _, t in rc[concept]["c"]])
        okr, okc = np.isfinite(all_r) & np.isfinite(all_rt), np.isfinite(all_c) & np.isfinite(all_ct)
        out[concept] = {
            "accuracy": accuracy(*acc[concept]),
            "alerts": alert_metrics(per[concept], step),
            "r_within_patient_corr": r_med, "c_within_patient_corr": c_med, "corr_patients": min(r_n, c_n),
            "r_pooled_corr": round(float(np.corrcoef(all_r[okr], all_rt[okr])[0, 1]), 4),
            "c_pooled_corr": round(float(np.corrcoef(all_c[okc], all_ct[okc])[0, 1]), 4),
        }
    # The datum: conventional threshold monitor with identical alarm management.
    out["DATUM"] = {"alerts": alert_metrics(per["DATUM"], step)}
    out["A"]["lead_vs_datum"] = paired_lead(per["A"], per["DATUM"], step)
    out["B"]["lead_vs_datum"] = paired_lead(per["B"], per["DATUM"], step)
    out["A"]["under_alert"] = merge_under_alert(ua["A"])
    out["B"]["under_alert"] = merge_under_alert(ua["B"])
    out["hazard_h09"] = hazard_h09(model, by_case, test_ids, step)

    unconf = [a for a in agreements if not a["confounded"]]
    out["A"]["intervention_agreement"] = None if not agreements else round(float(np.mean([a["agree"] for a in agreements])), 4)
    out["A"]["intervention_agreement_unconfounded"] = None if not unconf else round(float(np.mean([a["agree"] for a in unconf])), 4)
    out["A"]["interventions_assessed"] = len(agreements)
    out["latency_gpu_ms"] = {"p50": round(float(np.percentile(latencies, 50)), 2),
                             "p95": round(float(np.percentile(latencies, 95)), 2),
                             "max": round(float(np.max(latencies)), 2)}

    _write_csv(os.path.join(RESULTS, "ticks_synthetic_test.csv"), tick_rows)
    _write_csv(os.path.join(RESULTS, "intervention_agreement.csv"), agreements)
    return out


def verify_vitaldb(model, version5, n_cases, step, seed):
    eligible = vitaldb_cases(version5, "test")
    rng = np.random.default_rng(seed)
    picks = [eligible[i] for i in sorted(rng.choice(len(eligible), size=min(n_cases, len(eligible)), replace=False))]
    print(f"[3/5] real-data verification on {len(picks)} held-out VitalDB test cases ({len(eligible)} eligible)")
    per = {"A": [], "B": [], "DATUM": []}
    acc = {"A": ([], [], []), "B": ([], [], [])}
    rows = []
    for n, (cid, path) in enumerate(picks, 1):
        rec = load_vitaldb(path, cid)
        for concept in ("A", "B"):
            ticks = run_patient(rec, model, concept, step)
            per[concept].append({
                "t": [k["t"] for k in ticks],
                "level": [k["alert"]["level"] for k in ticks],
                "true_future": [k["truth"]["future_map"] for k in ticks],
                "true_current": [k["truth"]["current_map"] for k in ticks],
                "p_below": [k["p_below_target"] for k in ticks],
            })
            if concept == "A":
                per["DATUM"].append(dict(per["A"][-1], level=datum_levels(ticks), p_below=[None] * len(ticks)))
            acc[concept][0].extend(np.nan if k["forecast_map"] is None else k["forecast_map"] for k in ticks)
            acc[concept][1].extend(np.nan if k["forecast_map_uncalibrated"] is None else k["forecast_map_uncalibrated"] for k in ticks)
            acc[concept][2].extend(np.nan if k["truth"]["future_map"] is None else k["truth"]["future_map"] for k in ticks)
            rows += [{"case": cid, "concept": concept, "t": k["t"], "regime": k["regime"],
                      "forecast_map": k["forecast_map"], "forecast_map_uncalibrated": k["forecast_map_uncalibrated"],
                      "true_future_map": k["truth"]["future_map"], "level": k["alert"]["level_name"]} for k in ticks]
        if n % 5 == 0:
            print(f"      {n}/{len(picks)}")
    out = {"cases": [c for c, _ in picks]}
    for concept in ("A", "B"):
        out[concept] = {
            "accuracy_calibrated": accuracy(acc[concept][0], acc[concept][2]),
            "accuracy_uncalibrated": accuracy(acc[concept][1], acc[concept][2]),
            "alerts": alert_metrics(per[concept], step),
        }
    out["DATUM"] = {"alerts": alert_metrics(per["DATUM"], step)}
    out["A"]["lead_vs_datum"] = paired_lead(per["A"], per["DATUM"], step)
    out["B"]["lead_vs_datum"] = paired_lead(per["B"], per["DATUM"], step)
    _write_csv(os.path.join(RESULTS, "ticks_vitaldb_test.csv"), rows)
    return out


def under_alert_stats(ticks):
    """Hazard H-09 metrics: how the display behaves while TRUE MAP is critically low (< 55 mmHg)."""
    crit = [k for k in ticks if k["truth"].get("current_map") is not None and k["truth"]["current_map"] < config.MAP_CRITICAL]
    if not crit:
        return {"critical_ticks": 0}
    lv = [k["alert"]["level"] for k in crit]
    unsafe = 0
    for a, b in zip(ticks, ticks[1:]):
        la, lb = a["alert"]["level"], b["alert"]["level"]
        tm = b["truth"].get("current_map")
        if la is not None and lb is not None and la >= 2 and lb <= 1 and tm is not None and tm < config.MAP_CRITICAL:
            unsafe += 1
    return {
        "critical_ticks": len(crit),
        "under_alert": int(sum(1 for v in lv if v is not None and v <= 1)),
        "technical_only": int(sum(1 for v in lv if v is None)),
        "abp_rejected": int(sum(1 for k in crit if "abp" not in (k["regime"] or ""))),
        "unsafe_deescalations": unsafe,
        "abs_err": [abs(k["forecast_map"] - k["truth"]["future_map"]) for k in crit if k["forecast_map"] is not None],
    }


def merge_under_alert(parts):
    n = sum(p.get("critical_ticks", 0) for p in parts)
    if n == 0:
        return {"critical_ticks": 0}
    errs = [e for p in parts for e in p.get("abs_err", [])]
    return {
        "critical_ticks": n,
        "under_alert_rate": round(sum(p.get("under_alert", 0) for p in parts) / n, 4),
        "technical_only_rate": round(sum(p.get("technical_only", 0) for p in parts) / n, 4),
        "abp_rejected_rate": round(sum(p.get("abp_rejected", 0) for p in parts) / n, 4),
        "unsafe_deescalations": int(sum(p.get("unsafe_deescalations", 0) for p in parts)),
        "forecast_mae_in_critical": None if not errs else round(float(np.mean(errs)), 2),
    }


def hazard_h09(model, by_case, test_ids, step):
    """
    Hazard H-09 (false reassurance during profound hypotension), measured layer
    by layer on the shock-scenario test patients so each risk control's
    contribution is visible.
    """
    shock = [c for c in test_ids if re.search(r"(septic_shock|vasopressor_rescue|mixed_shock_stiff)", os.path.basename(by_case[c]))]
    print(f"      hazard H-09 on {len(shock)} shock-scenario test patients (4 configurations)")
    layers = [
        ("Version 5 baseline", False, False, False),
        ("+ RC-08 fail-safe hold", False, True, False),
        ("+ RC-10 measured-pressure floor", False, True, True),
        ("+ RC-09 shock-aware ABP check (all controls)", True, True, True),
    ]
    out = []
    for name, shock_aware, hold, floor in layers:
        model.shock_aware_abp = shock_aware
        parts = []
        for cid in shock:
            s = ReplaySession(load_synthetic(by_case[cid], cid), model, "A", step_sec=step)
            s.alerts = AlertStateMachine(fail_safe_hold=hold, use_measured_floor=floor)
            parts.append(under_alert_stats(s.run_to_end(include_waveforms=False)))
        out.append(dict(layer=name, **merge_under_alert(parts)))
        print(f"        {name:48s} under-alert {out[-1].get('under_alert_rate')}  "
              f"ABP rejected {out[-1].get('abp_rejected_rate')}  unsafe de-escalations {out[-1].get('unsafe_deescalations')}")
    model.shock_aware_abp = True
    return {"patients": len(shock), "layers": out}


def corrupt(window_kwargs, kind, fs):
    w = {k: (None if v is None else np.array(v, dtype=np.float32)) for k, v in window_kwargs.items()}
    n = len(w["ppg"])
    if kind == "ppg_flatline":
        w["ppg"][:] = w["ppg"][0]
    elif kind == "ppg_dropout":
        w["ppg"][: int(0.4 * n)] = np.nan
    elif kind == "abp_line_flush":
        s = int(0.5 * n)
        w["abp"][s:s + int(0.4 * fs)] += 150.0
    elif kind == "ecg_flatline":
        w["ecg"][:] = 0.0
    elif kind == "abp_damped":
        # Over-damped transducer / air in the line: pulsatility is lost.
        mean = float(np.nanmean(w["abp"]))
        w["abp"][:] = mean + 0.6 * np.sin(np.linspace(0, 6 * np.pi, n))
    return {k: (None if v is None else v.tolist()) for k, v in w.items()}


def shock_windows(by_case, test_ids, n, seed):
    """Artifact-free windows where the TRUE MAP is profoundly low (< 45 mmHg): genuine shock, not noise."""
    rng = np.random.default_rng(seed + 3)
    out = []
    shock = [c for c in test_ids if re.search(r"(septic_shock|vasopressor_rescue|mixed_shock_stiff)", os.path.basename(by_case[c]))]
    for cid in rng.permutation(shock):
        rec = load_synthetic(by_case[cid], cid)
        fs = rec.fs
        w = int(config.WINDOW_SEC * fs)
        for t_end in np.arange(rec.first_window_end(), rec.last_window_end(), 5.0):
            i1 = int(t_end * fs)
            if np.mean(rec.truth_abp[i1 - w:i1]) < 45 and np.all(np.isfinite(rec.abp[i1 - w:i1])):
                out.append((cid, rec, float(t_end)))
                break
        if len(out) >= n:
            break
    return out


def verify_quality_gate(model, by_case, test_ids, n_windows, seed):
    print(f"[4/5] signal-quality gate on {n_windows} windows x 5 corruptions + clean controls + genuine shock")
    rng = np.random.default_rng(seed)
    expected = {
        "clean": "usable",
        "ppg_flatline": "abstain",
        "ppg_dropout": "abstain",
        "abp_line_flush": "abp_dropped",
        "abp_damped": "abp_dropped",
        "ecg_flatline": "ecg_dropped",
    }
    rows, phys_alerts_on_abstain = [], 0
    pick = rng.choice(test_ids, size=min(n_windows, len(test_ids)), replace=False)
    for cid in pick:
        rec = load_synthetic(by_case[cid], cid)
        t_end = float(rng.uniform(rec.first_window_end(), rec.last_window_end()))
        i1 = int(t_end * rec.fs); i0 = i1 - int(config.WINDOW_SEC * rec.fs)
        base = {"ppg": rec.ppg[i0:i1].tolist(), "ecg": rec.ecg[i0:i1].tolist(),
                "abp": rec.abp[i0:i1].tolist(), "intervention": rec.intervention[i0:i1].tolist()}
        if not np.all(np.isfinite(base["abp"])) or not np.all(np.isfinite(base["ppg"])):
            continue  # need a clean starting window so the corruption is the only fault
        for kind, expect in expected.items():
            kw = base if kind == "clean" else corrupt(base, kind, rec.fs)
            res = model.predict(MonitorWindow(fs=rec.fs, **kw))
            cv = res.metadata.get("channel_valid", {}) if res.metadata else {}
            usable = res.status in ("ok", "low_confidence")
            if expect == "abstain":
                handled = not usable
            elif expect == "abp_dropped":
                handled = usable and not cv.get("abp", True) and res.regime in ("ppg_ecg", "ppg_only")
            elif expect == "ecg_dropped":
                handled = usable and not cv.get("ecg", True)
            else:
                handled = usable
            if not usable:
                sm = AlertStateMachine()
                alert = sm.update(t_end, None, None, False)
                if not alert["technical"] or alert["level"] is not None:
                    phys_alerts_on_abstain += 1
            rows.append({"patient": cid, "t_end": round(t_end, 1), "corruption": kind, "expected": expect,
                         "status": res.status, "regime": res.regime, "handled_safely": handled,
                         "warnings": " | ".join(res.warnings)})
    # Genuine profound shock is NOT an artifact: the arterial line must be kept.
    for cid, rec, t_end in shock_windows(by_case, test_ids, max(20, n_windows // 3), seed):
        i1 = int(t_end * rec.fs); i0 = i1 - int(config.WINDOW_SEC * rec.fs)
        res = model.predict(MonitorWindow(fs=rec.fs, ppg=rec.ppg[i0:i1].tolist(), ecg=rec.ecg[i0:i1].tolist(),
                                          abp=rec.abp[i0:i1].tolist(), intervention=rec.intervention[i0:i1].tolist()))
        kept = res.status in ("ok", "low_confidence") and "abp" in (res.regime or "")
        rows.append({"patient": cid, "t_end": round(t_end, 1), "corruption": "genuine_shock", "expected": "abp_retained",
                     "status": res.status, "regime": res.regime, "handled_safely": kept,
                     "warnings": " | ".join(res.warnings)})
    summary = {}
    for kind in list(expected) + ["genuine_shock"]:
        sub = [r for r in rows if r["corruption"] == kind]
        summary[kind] = {"n": len(sub), "handled_fraction": None if not sub else round(float(np.mean([r["handled_safely"] for r in sub])), 4)}
    faults = [r for r in rows if r["corruption"] not in ("clean", "genuine_shock")]
    summary["all_corruptions_handled_fraction"] = round(float(np.mean([r["handled_safely"] for r in faults])), 4)
    summary["physiological_alerts_from_abstained_windows"] = phys_alerts_on_abstain
    _write_csv(os.path.join(RESULTS, "quality_gate.csv"), rows)
    return summary


def performance(app_root, by_case, test_ids, gpu_model, n_windows, seed):
    print(f"[5/5] latency (CPU, {n_windows} windows) and Concept C throughput")
    cpu = ModelService(app_root, device="cpu")
    rng = np.random.default_rng(seed)
    times = []
    for cid in rng.choice(test_ids, size=min(n_windows, len(test_ids)), replace=True):
        rec = load_synthetic(by_case[cid], cid)
        t_end = float(rng.uniform(rec.first_window_end(), rec.last_window_end()))
        i1 = int(t_end * rec.fs); i0 = i1 - int(config.WINDOW_SEC * rec.fs)
        w = MonitorWindow(fs=rec.fs, ppg=rec.ppg[i0:i1].tolist(), ecg=rec.ecg[i0:i1].tolist(),
                          abp=rec.abp[i0:i1].tolist(), intervention=rec.intervention[i0:i1].tolist())
        t0 = time.perf_counter(); cpu.predict(w); times.append((time.perf_counter() - t0) * 1000)
    times = times[5:]  # discard warm-up
    rec = load_synthetic(by_case[test_ids[0]], test_ids[0])
    t0 = time.perf_counter(); rv = run_review(rec, gpu_model, step_sec=2.0); dt = time.perf_counter() - t0
    hours = rv["summary"]["monitored_sec"] / 3600.0
    return {
        "latency_cpu_ms": {"p50": round(float(np.percentile(times, 50)), 2),
                           "p95": round(float(np.percentile(times, 95)), 2),
                           "max": round(float(np.max(times)), 2), "n": len(times)},
        "concept_c_seconds_per_monitored_hour": round(dt / hours, 2),
    }


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------- spec check
def check(spec, value):
    if value is None:
        return "n/a"
    if spec["direction"] == "bool":
        return "PASS (ideal)" if bool(value) == bool(spec["ideal"]) else "FAIL"
    lower = spec["direction"] == "lower"
    meets = (lambda target: value <= target) if lower else (lambda target: value >= target)
    if meets(spec["ideal"]):
        return "PASS (ideal)"
    if meets(spec["marginal"]):
        return "PASS (marginal)"
    return "FAIL"


def spec_table(syn, vit, gate, perf):
    with open(os.path.join(APP_ROOT, "evaluation", "specifications.json"), "r", encoding="utf-8") as f:
        specs = {s["id"]: s for s in json.load(f)["specs"]}

    def measured(sid, concept):
        a = syn[concept] if concept in syn else None
        if sid == "SPEC-01":
            return None if a is None else a["accuracy"]["mae"]
        if sid == "SPEC-02":
            return None if a is None else a["alerts"]["sensitivity_deteriorating"]
        if sid == "SPEC-03":
            return None if a is None else a["alerts"]["specificity_deteriorating"]
        if sid == "SPEC-04":
            return None if a is None else a["alerts"]["false_alarms_per_hour_any"]
        if sid == "SPEC-05":
            return None if a is None else a["lead_vs_datum"]["watch"]["median_advantage_s"]
        if sid == "SPEC-06":
            return None if a is None else a["alerts"]["auroc"]
        if sid == "SPEC-07":
            return gate["all_corruptions_handled_fraction"]
        if sid == "SPEC-08":
            if a is None or a["r_within_patient_corr"] is None:
                return None
            return min(a["r_within_patient_corr"], a["c_within_patient_corr"])
        if sid == "SPEC-09":
            return syn["A"].get("intervention_agreement_unconfounded") if concept in ("A", "C") else None
        if sid == "SPEC-10":
            return perf["latency_cpu_ms"]["p95"]
        if sid == "SPEC-11":
            v = vit.get(concept)
            return None if v is None else v["accuracy_calibrated"]["mae"]
        if sid == "SPEC-15":
            return None if a is None else a["under_alert"].get("under_alert_rate")
        return None

    rows = []
    for sid, spec in specs.items():
        row = {"id": sid, "need": spec["need"], "metric": spec["metric"], "unit": spec["unit"],
               "ideal": spec["ideal"], "marginal": spec["marginal"]}
        for concept, alias in (("A", "A"), ("B", "B"), ("C", "A")):
            val = measured(sid, alias)
            # Concept C never alerts in real time: its real-time alert specs cannot be met.
            if concept == "C" and sid in ("SPEC-02", "SPEC-04", "SPEC-05", "SPEC-15"):
                val = {"SPEC-02": 0.0, "SPEC-04": 0.0, "SPEC-05": None, "SPEC-15": None}[sid]
            if concept == "C" and sid == "SPEC-10":
                val = perf["latency_cpu_ms"]["p95"]
            row[f"measured_{concept}"] = val
            row[f"result_{concept}"] = check(spec, val) if val is not None else "n/a"
        rows.append(row)
    _write_csv(os.path.join(RESULTS, "spec_verification.csv"), rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version5", default="/home/kyei/Document/Version 5")
    ap.add_argument("--val-per-scenario", type=int, default=20)
    ap.add_argument("--test-per-scenario", type=int, default=30)
    ap.add_argument("--vitaldb-cases", type=int, default=30)
    ap.add_argument("--real-cal-cases", type=int, default=25)
    # Verification runs at the deployed 1 s update rate so alarm persistence
    # (counted in updates) behaves exactly as it does at the bedside.
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--vitaldb-step", type=float, default=1.0)
    ap.add_argument("--real-cal-step", type=float, default=5.0)
    ap.add_argument("--gate-windows", type=int, default=120)
    ap.add_argument("--latency-windows", type=int, default=205)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)

    t_start = time.time()
    model = ModelService(APP_ROOT)
    # Start from an uncalibrated state so calibration is fitted fresh.
    model.clear_calibration()
    by_case, val_ids, test_ids = synthetic_splits(args.version5)
    val = stratified_sample(by_case, val_ids, args.val_per_scenario, args.seed)
    test = stratified_sample(by_case, test_ids, args.test_per_scenario, args.seed + 1)

    cal = calibrate(model, args.version5, by_case, val, args.real_cal_cases, args.step, args.real_cal_step, args.seed)
    syn = verify_synthetic(model, by_case, test, args.step)
    vit = verify_vitaldb(model, args.version5, args.vitaldb_cases, args.vitaldb_step, args.seed)
    # Run the gate twice: Version 5 behaviour alone (precheck off) and with the
    # app-layer risk control RC-07 (precheck on), to show the defect and its fix.
    model.raw_precheck, model.shock_aware_abp = False, False
    gate_before = verify_quality_gate(model, by_case, test_ids, args.gate_windows, args.seed)
    os.replace(os.path.join(RESULTS, "quality_gate.csv"), os.path.join(RESULTS, "quality_gate_before_rc07.csv"))
    model.raw_precheck, model.shock_aware_abp = True, True
    gate = verify_quality_gate(model, by_case, test_ids, args.gate_windows, args.seed)
    gate["before_rc07"] = gate_before
    perf = performance(APP_ROOT, by_case, test_ids, model, args.latency_windows, args.seed)
    specs = spec_table(syn, vit, gate, perf)

    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_min": round((time.time() - t_start) / 60, 1),
        "model": model.info(),
        "design": {"val_patients": len(val), "test_patients": len(test), "step_sec": args.step,
                   "vitaldb_step_sec": args.vitaldb_step, "seed": args.seed},
        "calibration": {d: b["regimes"] for d, b in cal["domains"].items()},
        "synthetic_test": syn,
        "vitaldb_test": vit,
        "quality_gate": gate,
        "performance": perf,
        "spec_verification": specs,
    }
    with open(os.path.join(RESULTS, "verification_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"done in {summary['runtime_min']} min -> evaluation/results/verification_summary.json")
    for row in specs:
        print(f"  {row['id']} {row['need'][:42]:42s} A:{row['result_A']:16s} B:{row['result_B']:16s} C:{row['result_C']}")


if __name__ == "__main__":
    main()
