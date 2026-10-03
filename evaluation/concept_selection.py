"""
Concept selection (BME Design, Chapter 6), computed from verification evidence.

Implements, in order:
  6.5.1  Critical-specification gate  - a concept failing a must-meet (*)
         specification "will not solve the problem".
  6.4.2  Weighted decision matrix      - Likert 1-5 scores x criterion weights.
  6.4.4  Scoring system                - every score's meaning is DEFINED BEFORE
         scoring (RUBRIC below) and anchored to measured metrics where possible.
  6.4.3  Decision support matrix (DSM) - +1 / 0 / -1 against a datum, repeated
         with four different datums to reduce bias (Section 6.7).
  6.4.5  House of Quality roof         - coupled criteria adjust the totals.
  Weight-sensitivity analysis          - alternative weightings and 10,000
         random weightings, to show the result does not hinge on our weights.

Inputs : evaluation/results/verification_summary.json, ticks_synthetic_test.csv,
         specifications.json, held-out VitalDB cases (for the datum's own metrics).
Output : evaluation/results/concept_selection.json
"""

import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
from app.engine import config  # noqa: E402
from app.engine.alerting import AlertStateMachine  # noqa: E402
from app.engine.recordings import load_vitaldb  # noqa: E402

RESULTS = os.path.join(APP_ROOT, "evaluation", "results")
CONCEPTS = ["DATUM", "A", "B", "C"]
NAMES = {
    "DATUM": "Conventional threshold monitor (current practice)",
    "A": "Concept A - Integrated alert + forecast + intervention response",
    "B": "Concept B - Non-invasive early warning",
    "C": "Concept C - Decision-support review system",
}

# Criteria and weights (sum 100 %). Weights follow the need priorities in
# docs/02 (must-meet safety/performance needs weighted highest) and are tested
# for robustness below rather than assumed correct.
CRITERIA = [
    ("C1", "Early detection of deterioration", 20, "SPEC-02, 05, 06"),
    ("C2", "Hazard profile (residual risk after controls)", 20, "SPEC-07, 15; FMEA"),
    ("C3", "Accuracy on real patients", 15, "SPEC-01, 11"),
    ("C4", "Alarm burden", 10, "SPEC-03, 04"),
    ("C5", "Bedside actionability (incl. treatment response)", 15, "SPEC-09; concept briefs"),
    ("C6", "Breadth of care settings", 5, "Concept 4 brief"),
    ("C7", "Documentation, handoff and audit", 5, "SPEC-13"),
    ("C8", "Regulatory and development risk (low risk = high score)", 10, "Ch. 6.3.1, Ch. 12"),
]

# Score anchors, written BEFORE scoring (BME Design 6.4.4, heuristic 3).
RUBRIC = {
    "C1": {5: "Real-data sensitivity >= 0.90 AND warns >= 10 s earlier than a conventional monitor",
           4: "Real-data sensitivity >= 0.85 AND warns earlier than a conventional monitor (> 0 s)",
           3: "Real-data sensitivity >= 0.80 (marginal spec), no earlier than a conventional monitor",
           2: "Real-data sensitivity >= 0.50",
           1: "Sensitivity < 0.50, or no real-time detection at all"},
    "C2": {5: "Minimal hazard: not time-critical, cannot falsely reassure in real time",
           4: "Gate handles >= 99 % of faults AND under-alerts < 1 % of critical-hypotension time (ALARP)",
           3: "Gate >= 95 % AND under-alert <= 5 %",
           2: "Under-alert <= 50 %",
           1: "Under-alert > 50 % of critical-hypotension time (unacceptable risk)"},
    "C3": {5: "Real-patient MAE <= 5 mmHg", 4: "<= 10 mmHg", 3: "<= 15 mmHg", 2: "<= 20 mmHg", 1: "> 20 mmHg"},
    "C4": {5: "No real-time alarms, or <= 1 false alarm per patient-hour with specificity >= 0.90",
           4: "<= 3 false alarms per hour with specificity >= 0.85",
           3: "<= 6 false alarms per hour, or specificity >= 0.80",
           2: "<= 12 false alarms per hour",
           1: "> 12 false alarms per hour, or alarming most of the time (WATCH specificity < 0.5)"},
    "C5": {5: "Real-time alerts + explanations + treatment response with agreement >= 0.90",
           4: "Real-time alerts + explanations + treatment response with agreement >= 0.75",
           3: "Real-time measured values and alarms, no prediction or treatment response",
           2: "Low-confidence real-time estimate only, or retrospective insight only",
           1: "No clinically actionable output"},
    "C6": {5: "Invasive and non-invasive settings, or any setting with monitor data",
           4: "Any setting with stored monitor records (retrospective)",
           3: "Arterial-line settings only (ICU / operating room)",
           2: "Narrow niche", 1: "Not deployable"},
    "C7": {5: "Exportable episode report (printable + data export) and full timeline",
           4: "Stored timeline with partial export",
           3: "Stored trends, no export",
           2: "Event log only",
           1: "No record"},
    "C8": {5: "No new development or clearance (existing cleared product)",
           4: "Lower-risk retrospective decision support; simpler validation",
           3: "Class II with a predicate (Hypotension Prediction Index, DEN160044 -> 510(k))",
           2: "Class II with new claims and no supporting performance evidence",
           1: "Class III / pathway unclear"},
}


def band(value, bands, default=1):
    for score, test in bands:
        if test(value):
            return score
    return default


# ------------------------------------------------------------------ datum metrics
def datum_metrics(ver):
    """The datum's own measured performance (not taken from the device)."""
    rows = list(csv.DictReader(open(os.path.join(RESULTS, "ticks_synthetic_test.csv"), encoding="utf-8")))
    by_pt = defaultdict(list)
    for r in rows:
        if r["concept"] == "A":
            by_pt[r["patient"]].append(r)
    crit = under = 0
    # Like-for-like persistence benchmark on the same ticks: current measured MAP as the 15 s forecast.
    em, ep, emc, epc = [], [], [], []
    for ticks in by_pt.values():
        for r in ticks:
            if r["forecast_map"] in ("", "None") or r["current_map"] in ("", "None") or r["true_future_map"] in ("", "None"):
                continue
            fut, cur = float(r["true_future_map"]), float(r["true_current_map"])
            e_m, e_p = abs(float(r["forecast_map"]) - fut), abs(float(r["current_map"]) - fut)
            em.append(e_m); ep.append(e_p)
            if abs(fut - cur) > 5:
                emc.append(e_m); epc.append(e_p)
    for ticks in by_pt.values():
        ticks.sort(key=lambda r: float(r["t"]))
        sm = AlertStateMachine()
        for r in ticks:
            cm = float(r["current_map"]) if r["current_map"] not in ("", "None") else None
            lvl = sm.update(float(r["t"]), cm, None, cm is not None, 0.0, "High")["level"]
            if float(r["true_current_map"]) < config.MAP_CRITICAL:
                crit += 1
                under += int(lvl is not None and lvl <= 1)
    # Persistence error on REAL patients: current measured MAP used as the "forecast".
    errs = []
    v5 = ver["vitaldb_test"]
    root = os.environ.get("VERSION5", "/home/kyei/Document/Version 5")
    for case in v5["cases"]:
        path = os.path.join(root, "data/raw/vitaldb", f"case_{int(case.split('_')[1]):04d}.npz")
        if not os.path.exists(path):
            continue
        rec = load_vitaldb(path, case)
        abp, fs = rec.abp.astype(np.float64), rec.fs
        good = np.where(np.isfinite(abp) & (abp > 20) & (abp < 220), abp, np.nan)
        for t in np.arange(config.WINDOW_SEC, rec.last_window_end(), 1.0):
            i = int(t * fs)
            cur = good[i - int(4 * fs):i]; fut = good[i:i + int(config.FORECAST_HORIZON_SEC * fs)]
            if np.isfinite(cur).sum() > 10 and np.isfinite(fut).sum() > 10:
                errs.append(abs(np.nanmean(cur) - np.nanmean(fut)))
    return {"under_alert_rate": round(under / crit, 4) if crit else None, "critical_ticks": crit,
            "persistence_mae_real": round(float(np.mean(errs)), 2) if errs else None,
            "synthetic_model_mae": round(float(np.mean(em)), 2), "synthetic_persistence_mae": round(float(np.mean(ep)), 2),
            "synthetic_changing_n": len(emc), "synthetic_changing_model_mae": round(float(np.mean(emc)), 2),
            "synthetic_changing_persistence_mae": round(float(np.mean(epc)), 2)}


# ------------------------------------------------------------------ scoring
def score_all(ver, specs, dm):
    syn, vit, gate = ver["synthetic_test"], ver["vitaldb_test"], ver["quality_gate"]
    ev = {c: {} for c in CONCEPTS}
    S = {c: {} for c in CONCEPTS}

    # C1 Early detection
    for c in ("A", "B"):
        sens = vit[c]["alerts"]["sensitivity_deteriorating"] or 0.0
        lead = syn[c]["lead_vs_datum"]["deteriorating"]["median_advantage_s"]
        lead_w = syn[c]["lead_vs_datum"]["watch"]["median_advantage_s"]
        lead_eff = max(v for v in (lead, lead_w) if v is not None) if (lead is not None or lead_w is not None) else None
        s = 1
        if sens >= 0.90 and lead_eff is not None and lead_eff >= 10: s = 5
        elif sens >= 0.85 and lead_eff is not None and lead_eff > 0: s = 4
        elif sens >= 0.80: s = 3
        elif sens >= 0.50: s = 2
        S[c]["C1"] = s
        ev[c]["C1"] = (f"real-data sensitivity {sens:.2f}; AUROC synthetic {syn[c]['alerts']['auroc']}, real {vit[c]['alerts']['auroc']}; "
                       f"lead vs conventional monitor {lead} s (DETERIORATING), {lead_w} s (WATCH)")
    d_sens = vit["DATUM"]["alerts"]["sensitivity_deteriorating"]
    S["DATUM"]["C1"] = 3 if d_sens >= 0.80 else 2
    ev["DATUM"]["C1"] = f"real-data sensitivity {d_sens:.2f}; reactive by definition (lead 0 s)"
    S["C"]["C1"] = 1
    ev["C"]["C1"] = "retrospective - no real-time detection"

    # C2 Hazard profile
    g = gate["all_corruptions_handled_fraction"]
    for c in ("A", "B"):
        ua = syn[c]["under_alert"].get("under_alert_rate", 1.0)
        if g >= 0.99 and ua < 0.01: s = 4
        elif g >= 0.95 and ua <= 0.05: s = 3
        elif ua <= 0.50: s = 2
        else: s = 1
        S[c]["C2"] = s
        ev[c]["C2"] = f"faults handled {g:.2f}; under-alert during true MAP<55: {ua:.3f}"
    ua_d = dm["under_alert_rate"]
    S["DATUM"]["C2"] = 4 if (ua_d is not None and ua_d < 0.01) else 3
    ev["DATUM"]["C2"] = f"under-alert during true MAP<55: {ua_d}; established, well-characterised risks"
    S["C"]["C2"] = 5
    ev["C"]["C2"] = "retrospective, not time-critical: cannot falsely reassure in real time"

    # C3 Accuracy on real patients
    acc_bands = [(5, lambda v: v <= 5), (4, lambda v: v <= 10), (3, lambda v: v <= 15), (2, lambda v: v <= 20)]
    for c in ("A", "B"):
        mae = vit[c]["accuracy_calibrated"]["mae"]
        S[c]["C3"] = band(mae, acc_bands)
        ev[c]["C3"] = f"real-patient MAE {mae} mmHg (synthetic {syn[c]['accuracy']['mae']})"
    S["C"]["C3"] = S["A"]["C3"]; ev["C"]["C3"] = "same model pipeline as A: " + ev["A"]["C3"]
    pm = dm["persistence_mae_real"]
    S["DATUM"]["C3"] = band(pm, acc_bands)
    ev["DATUM"]["C3"] = f"current measured MAP as a 15 s predictor: MAE {pm} mmHg (persistence)"

    # C4 Alarm burden (real data)
    for c in ("A", "B", "DATUM"):
        a = vit[c]["alerts"]
        fa, sp, spw = a["false_alarms_per_hour_any"], a["specificity_deteriorating"] or 0, a["specificity_watch"] or 0
        if spw < 0.5: s = 1
        elif fa <= 1 and sp >= 0.90: s = 5
        elif fa <= 3 and sp >= 0.85: s = 4
        elif fa <= 6 or sp >= 0.80: s = 3
        elif fa <= 12: s = 2
        else: s = 1
        S[c]["C4"] = s
        ev[c]["C4"] = f"real data: {fa} false alarms/h, specificity {sp:.2f} (DETERIORATING), {spw:.2f} (WATCH)"
    S["C"]["C4"] = 5; ev["C"]["C4"] = "no real-time alarms"

    # C5 Bedside actionability
    agree = syn["A"].get("intervention_agreement_unconfounded") or 0
    S["A"]["C5"] = 5 if agree >= 0.90 else 4 if agree >= 0.75 else 3
    ev["A"]["C5"] = f"real-time alerts with plain-language reasons, forecast, R/C trends; treatment-response agreement {agree:.2f}"
    S["B"]["C5"] = 2; ev["B"]["C5"] = "low-confidence estimate capped at WATCH; no treatment response"
    S["C"]["C5"] = 2; ev["C"]["C5"] = "retrospective insight only"
    S["DATUM"]["C5"] = 3; ev["DATUM"]["C5"] = "measured values and threshold alarms; no prediction or treatment response"

    # C6 Breadth of care settings
    S["A"]["C6"], ev["A"]["C6"] = 3, "requires an arterial line (ICU / operating room)"
    S["B"]["C6"], ev["B"]["C6"] = 5, "ECG + PPG only: step-down, recovery and lower-resource settings"
    S["C"]["C6"], ev["C"]["C6"] = 4, "any setting with stored monitor records"
    S["DATUM"]["C6"], ev["DATUM"]["C6"] = 3, "continuous MAP alarms need an arterial line"

    # C7 Documentation
    S["A"]["C7"], ev["A"]["C7"] = 2, "event log during the session; no export"
    S["B"]["C7"], ev["B"]["C7"] = 2, "event log during the session; no export"
    S["C"]["C7"], ev["C"]["C7"] = 5, "printable report with charts + CSV + JSON export, full timeline"
    S["DATUM"]["C7"], ev["DATUM"]["C7"] = 3, "monitors store trends; limited export"

    # C8 Regulatory and development risk
    S["A"]["C8"], ev["A"]["C8"] = 3, "Class II; predicate exists (Hypotension Prediction Index, 21 CFR 870.2210); three subsystems to validate"
    S["B"]["C8"], ev["B"]["C8"] = 2, "Class II with new non-invasive claims that current evidence does not support"
    S["C"]["C8"], ev["C"]["C8"] = 4, "still a device (processes physiological signals) but retrospective and lower risk"
    S["DATUM"]["C8"], ev["DATUM"]["C8"] = 5, "existing cleared product - nothing to develop"
    return S, ev


def weighted_totals(S, weights):
    w = {cid: weights[cid] / 100.0 for cid in weights}
    return {c: round(sum(S[c][cid] * w[cid] for cid in w), 3) for c in S}


def dsm(S, datum, weights):
    """Decision Support Matrix (BME Design 6.4.3, Table 6.7 scale): +1 better, 0 same, -1 worse than the datum."""
    table = {}
    for c in CONCEPTS:
        if c == datum:
            continue
        marks = {cid: int(np.sign(S[c][cid] - S[datum][cid])) for cid, *_ in CRITERIA}
        table[c] = {"marks": marks, "total": int(sum(marks.values())),
                    "weighted_total": round(sum(marks[cid] * weights[cid] / 100.0 for cid in marks), 3)}
    return table


# House of Quality roof: coupled criteria (BME Design 6.4.5).
ROOF = [
    ("C1", "C4", -1, "More sensitive alerting raises alarm burden"),
    ("C3", "C1", +1, "Accurate forecasts enable earlier, correct alerts"),
    ("C3", "C4", +1, "Accurate forecasts reduce false alarms"),
    ("C2", "C1", +1, "Fail-safe floor/hold controls protect detection"),
    ("C6", "C3", -1, "Wider (non-invasive) settings cost accuracy"),
]


def house_of_quality(S, totals):
    """
    Operationalisation of the roof (documented in docs/04): a supporting (+)
    pair adds +1 when a concept scores >= 4 on BOTH criteria (the synergy is
    realised); an opposing (-) pair adds -1 when a concept scores >= 4 on one
    and <= 2 on the other (the trade-off is hurting it). Marks enter at 0.1
    each so the roof acts as the tie-breaker the book intends, not a new matrix.
    """
    out = {}
    for c in CONCEPTS:
        net, notes = 0, []
        for a, b, sign, why in ROOF:
            sa, sb = S[c][a], S[c][b]
            if sign > 0 and sa >= 4 and sb >= 4:
                net += 1; notes.append(f"+ {a}/{b}: {why}")
            if sign < 0 and ((sa >= 4 and sb <= 2) or (sb >= 4 and sa <= 2)):
                net -= 1; notes.append(f"- {a}/{b}: {why}")
        out[c] = {"net_marks": net, "modified_total": round(totals[c] + 0.1 * net, 3), "notes": notes}
    return out


def sensitivity(S, base):
    alt = {
        "Base weights": base,
        "Equal weights": {cid: 12.5 for cid in base},
        "Safety-led (C1 + C2 = 60 %)": {"C1": 30, "C2": 30, "C3": 10, "C4": 8, "C5": 10, "C6": 4, "C7": 4, "C8": 4},
        "Setting-breadth-led (favours B)": {"C1": 15, "C2": 15, "C3": 10, "C4": 10, "C5": 10, "C6": 30, "C7": 5, "C8": 5},
        "Documentation-led (favours C)": {"C1": 12, "C2": 15, "C3": 10, "C4": 10, "C5": 8, "C6": 5, "C7": 30, "C8": 10},
        "Regulatory-led (low risk)": {"C1": 15, "C2": 15, "C3": 10, "C4": 10, "C5": 10, "C6": 5, "C7": 5, "C8": 30},
    }
    scenarios = {}
    for name, w in alt.items():
        t = weighted_totals({c: S[c] for c in ("A", "B", "C")}, w)
        scenarios[name] = {"totals": t, "winner": max(t, key=t.get)}
    rng = np.random.default_rng(42)
    wins = defaultdict(int)
    n = 10000
    keys = [cid for cid, *_ in CRITERIA]
    for _ in range(n):
        w = dict(zip(keys, 100 * rng.dirichlet(np.ones(len(keys)))))
        t = weighted_totals({c: S[c] for c in ("A", "B", "C")}, w)
        wins[max(t, key=t.get)] += 1
    return {"scenarios": scenarios,
            "random_weightings": {"draws": n, "win_share": {c: round(wins[c] / n, 4) for c in ("A", "B", "C")}}}


def break_even(S, base, a="A", b="C"):
    """
    Weight on each criterion at which concept `a` and `b` tie, with the other
    weights rescaled proportionally. Answers: "how much would our weights have
    to change before the decision flips?"
    """
    out = {}
    for cid in base:
        rest = 100.0 - base[cid]
        flip = None
        for w in np.arange(0.0, 100.01, 0.5):
            ww = {k: (w if k == cid else base[k] * (100.0 - w) / rest) for k in base}
            t = weighted_totals({c: S[c] for c in (a, b)}, ww)
            winner = a if t[a] >= t[b] else b
            if flip is None:
                flip = {"start_winner": winner, "flips_at_weight": None}
            elif winner != flip["start_winner"] and flip["flips_at_weight"] is None:
                flip["flips_at_weight"] = float(w)
        flip["base_weight"] = base[cid]
        out[cid] = flip
    return out


def scoring_uncertainty(S, weights, ver):
    """
    Re-score judgements that the evidence leaves open (BME Design 6.4.4 '?' scores)
    at their pessimistic value and check whether the decision survives.
    """
    vit = ver["vitaldb_test"]["A"]["lead_vs_datum"]
    cases = {
        "A C1 = 3: on real patients the MEDIAN lead over a conventional monitor is "
        f"{vit['watch']['median_advantage_s']} s (mean {vit['deteriorating']['mean_advantage_s']} s)": {"A": {"C1": 3}},
        "A C4 = 2: real-data alarm burden counted at WATCH level "
        f"({ver['vitaldb_test']['A']['alerts']['false_alarms_per_hour_any']}/h)": {"A": {"C4": 2}},
        "C C5 = 3: retrospective review credited with bedside value at handoff": {"C": {"C5": 3}},
        "C C8 = 5: review tool treated as non-device CDS (FDA CDS criteria)": {"C": {"C8": 5}},
    }
    cases["All pessimistic for A and optimistic for C together"] = {"A": {"C1": 3, "C4": 2}, "C": {"C5": 3, "C8": 5}}
    out = {}
    for name, change in cases.items():
        S2 = {c: dict(S[c]) for c in S}
        for c, upd in change.items():
            S2[c].update(upd)
        t = weighted_totals({c: S2[c] for c in ("A", "B", "C")}, weights)
        out[name] = {"totals": t, "winner": max(t, key=t.get)}
    return out


def critical_gate(ver, specs):
    must = [s for s in specs if s["priority"] == "*"]
    rows = {r["id"]: r for r in ver["spec_verification"]}
    gate = {}
    for c in ("A", "B", "C"):
        fails = [s["id"] for s in must if rows.get(s["id"], {}).get(f"result_{c}") == "FAIL"]
        gate[c] = {"failed_must_meet": fails, "passes": not fails}
    return gate


def main():
    ver = json.load(open(os.path.join(RESULTS, "verification_summary.json"), encoding="utf-8"))
    specs = json.load(open(os.path.join(APP_ROOT, "evaluation", "specifications.json"), encoding="utf-8"))["specs"]
    print("computing datum metrics from held-out data ...")
    dm = datum_metrics(ver)
    S, ev = score_all(ver, specs, dm)
    weights = {cid: w for cid, _, w, _ in CRITERIA}
    totals = weighted_totals(S, weights)
    hoq = house_of_quality(S, totals)
    dsms = {d: dsm(S, d, weights) for d in CONCEPTS}
    sens = sensitivity(S, weights)
    gate = critical_gate(ver, specs)
    be = break_even(S, weights)
    unc = scoring_uncertainty(S, weights, ver)
    # Gate first (6.5.1): only concepts meeting every must-meet spec are eligible;
    # the matrix then ranks the eligible ones. If none passes, rank all.
    eligible = [c for c in ("A", "B", "C") if gate[c]["passes"]] or ["A", "B", "C"]
    candidates = {c: hoq[c]["modified_total"] for c in eligible}
    selected = max(candidates, key=candidates.get)
    matrix_rank = sorted(("A", "B", "C"), key=lambda c: -hoq[c]["modified_total"])

    syn, vit = ver["synthetic_test"], ver["vitaldb_test"]
    vl = vit["A"]["lead_vs_datum"]["deteriorating"]
    rationale = [
        f"Critical-specification gate (6.5.1): only {', '.join(eligible)} meets every must-meet (*) specification. "
        f"B fails {', '.join(gate['B']['failed_must_meet']) or 'none'}; C fails {', '.join(gate['C']['failed_must_meet']) or 'none'} "
        "(a retrospective tool cannot detect deterioration at the bedside, which is the problem statement).",
        f"Weighted decision matrix: A {totals['A']:.2f}, C {totals['C']:.2f}, B {totals['B']:.2f}, current practice {totals['DATUM']:.2f} of 5 "
        f"(House of Quality adjusted A {hoq['A']['modified_total']:.2f}).",
        f"Scoring uncertainty (6.4.4): A stays ahead of C under each single pessimistic re-scoring "
        f"({sum(1 for k, u in unc.items() if u['winner'] == 'A' and not k.startswith('All'))} of {len(unc) - 1}); only if all four "
        f"judgements go against A at once does C edge ahead ({unc[list(unc)[-1]]['totals']['C']:.2f} vs {unc[list(unc)[-1]]['totals']['A']:.2f}).",
        f"Weight sensitivity is the weak point: C wins {100 * sens['random_weightings']['win_share']['C']:.0f}% of 10,000 random weightings "
        f"and the equal-weight case, because C is low-risk and documents well. With the other weights kept in their base proportions, A wins once early detection (C1) carries "
        f">= {be['C1']['flips_at_weight'] if be['C1']['start_winner'] == 'C' else 0} % weight; the project need puts it at {weights['C1']} %.",
        f"Discrimination: AUROC {syn['A']['alerts']['auroc']:.2f} simulated / {vit['A']['alerts']['auroc']:.2f} real for A vs "
        f"{syn['B']['alerts']['auroc']:.2f} / {vit['B']['alerts']['auroc']:.2f} for B.",
        f"Real-patient accuracy {vit['A']['accuracy_calibrated']['mae']:.1f} mmHg MAE against the arterial line.",
        f"Safety after risk controls: {100 * ver['quality_gate']['all_corruptions_handled_fraction']:.0f}% of signal faults handled; "
        f"under-alert during critical hypotension {100 * syn['A']['under_alert']['under_alert_rate']:.1f}% (Version 5 alone: 53%).",
        f"Treatment response tracked in real time with {100 * syn['A']['intervention_agreement_unconfounded']:.0f}% agreement (unconfounded events).",
        f"Honest limit: on real patients A warns earlier than a conventional monitor at {100 * vl['fraction_concept_earlier']:.0f}% of onsets "
        f"(mean +{vl['mean_advantage_s']} s, median {vl['median_advantage_s']} s) and detects {vl['detected_by_concept_only']} onsets the monitor misses; "
        "the 15 s forecast horizon caps the lead time, and A is only modestly ahead of current practice overall.",
        f"Benchmark against persistence (Ch. 4.4): carrying the current measured MAP forward 15 s is MORE accurate overall "
        f"({dm['synthetic_persistence_mae']} vs {dm['synthetic_model_mae']} mmHg simulated; {dm['persistence_mae_real']} vs "
        f"{vit['A']['accuracy_calibrated']['mae']:.1f} mmHg real); the model is better only while pressure is changing "
        f"> 5 mmHg in 15 s ({dm['synthetic_changing_model_mae']} vs {dm['synthetic_changing_persistence_mae']} mmHg) - "
        "which is when an early warning matters. A longer forecast horizon is the main design iteration.",
        "Recommendation (recombination, Ch. 6): adopt A and fold C's review/report in as A's documentation module (fixes A's C7 score); "
        "keep B as a future research concept until non-invasive accuracy improves.",
    ]
    out = {
        "method": "BME Design Ch. 6: critical-spec gate (6.5.1), weighted decision matrix (6.4.2) with predefined "
                  "rubric (6.4.4), decision support matrix with rotated datums (6.4.3, 6.7), House of Quality (6.4.5), "
                  "weight-sensitivity analysis",
        "names": NAMES,
        "criteria": [{"id": cid, "name": n, "weight": w, "evidence": e} for cid, n, w, e in CRITERIA],
        "rubric": RUBRIC,
        "scores": S,
        "evidence": ev,
        "datum_metrics": dm,
        "weighted_totals": totals,
        "house_of_quality": {"roof": [{"a": a, "b": b, "sign": s, "why": why} for a, b, s, why in ROOF], "result": hoq},
        "dsm_by_datum": dsms,
        "sensitivity": sens,
        "critical_spec_gate": gate,
        "eligible_after_gate": eligible,
        "matrix_rank_ignoring_gate": matrix_rank,
        "break_even_A_vs_C": be,
        "scoring_uncertainty": unc,
        "selected": selected,
        "rationale": rationale,
    }
    with open(os.path.join(RESULTS, "concept_selection.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print("\nWeighted decision matrix (scores 1-5)")
    print(f"{'criterion':52s} {'wt':>4s}  " + "  ".join(f"{c:>5s}" for c in CONCEPTS))
    for cid, name, w, _ in CRITERIA:
        print(f"{cid} {name[:48]:48s} {w:>4d}  " + "  ".join(f"{S[c][cid]:>5d}" for c in CONCEPTS))
    print(f"{'Weighted total':52s} {'100':>4s}  " + "  ".join(f"{totals[c]:>5.2f}" for c in CONCEPTS))
    print(f"{'House of Quality modified total':52s} {'':>4s}  " + "  ".join(f"{hoq[c]['modified_total']:>5.2f}" for c in CONCEPTS))
    for d, table in dsms.items():
        print(f"DSM datum={d:5s}: " + ", ".join(f"{c} {v['total']:+d} ({v['weighted_total']:+.2f})" for c, v in table.items()))
    print("sensitivity:", {k: v["winner"] for k, v in sens["scenarios"].items()})
    print("random weightings win share:", sens["random_weightings"]["win_share"])
    print("critical gate:", gate)
    print("break-even A vs C:", {k: (v["start_winner"], v["flips_at_weight"], v["base_weight"]) for k, v in be.items()})
    for k, v in unc.items():
        print(f"  uncertainty: {v['winner']}  {v['totals']}  <- {k[:70]}")
    print("\n".join("  - " + r for r in rationale))
    print(f"\nSELECTED: Concept {selected}")


if __name__ == "__main__":
    main()
