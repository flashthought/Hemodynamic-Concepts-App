"""
Generate every figure used in the Design History File and slides (docs/figures/).
All numbers are read from evaluation/results/*.json and evaluation/*.json, so
figures always match the verification run that produced them.

    python scripts/make_figures.py
"""

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
RES = os.path.join(APP_ROOT, "evaluation", "results")
OUT = os.path.join(APP_ROOT, "docs", "figures")
os.makedirs(OUT, exist_ok=True)

COL = {"A": "#1565c0", "B": "#ef6c00", "C": "#2e7d32", "DATUM": "#757575"}
LVL = {"STABLE": "#2e7d32", "WATCH": "#0277bd", "DETERIORATING": "#f2a900", "CRITICAL": "#c62828", "SIGNAL CHECK": "#546e7a"}  # same as the app (IEC 60601-1-8)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10.5, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.dpi": 110, "savefig.dpi": 180, "savefig.bbox": "tight"})


def load(name, base=RES):
    with open(os.path.join(base, name), encoding="utf-8") as f:
        return json.load(f)


VER = load("verification_summary.json")
SEL = load("concept_selection.json")
FMEA = load("fmea.json", os.path.join(APP_ROOT, "evaluation"))


def save(fig, name):
    fig.savefig(os.path.join(OUT, name))
    plt.close(fig)
    print("  wrote", name)


def box(ax, x, y, w, h, text, fc, ec="#263238", fs=9.5, weight="normal", tc="#102027"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02", fc=fc, ec=ec, lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, weight=weight, color=tc, wrap=True)


def arrow(ax, x0, y0, x1, y1, colour="#455a64"):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="-|>", color=colour, lw=1.4))


# ---------------------------------------------------------------- 1 confluence
def fig_confluence():
    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.set_xlim(-0.01, 1.01); ax.set_ylim(0, 1); ax.axis("off")
    briefs = ["1  Real-time deterioration alert", "2  Hemodynamic forecast dashboard", "3  Intervention response monitor",
              "4  Non-invasive early warning", "5  Retrospective decision-support review"]
    ys = [0.82, 0.66, 0.50, 0.30, 0.10]
    for t, y in zip(briefs, ys):
        box(ax, 0.01, y - 0.06, 0.36, 0.11, t, "#eceff1", fs=8.8)
    box(ax, 0.49, 0.50, 0.27, 0.32, "Concept A\nIntegrated alert + forecast\n+ intervention response\n(ABP + ECG + PPG + events)", "#e3f2fd", ec=COL["A"], weight="bold", fs=9)
    box(ax, 0.49, 0.24, 0.27, 0.13, "Concept B\nNon-invasive early warning\n(ECG + PPG only)", "#fff3e0", ec=COL["B"], weight="bold", fs=9)
    box(ax, 0.49, 0.03, 0.27, 0.13, "Concept C\nDecision-support review\n(retrospective)", "#e8f5e9", ec=COL["C"], weight="bold", fs=9)
    for y in ys[:3]:
        arrow(ax, 0.375, y, 0.485, 0.66)
    arrow(ax, 0.375, ys[3], 0.485, 0.305); arrow(ax, 0.375, ys[4], 0.485, 0.095)
    box(ax, 0.81, 0.30, 0.18, 0.40, "Shared engine\nVersion 5 physics-\ninformed model\n(frozen, SHA-256\nverified)", "#f3e5f5", ec="#6a1b9a")
    for y in (0.66, 0.305, 0.095):
        arrow(ax, 0.81, 0.5, 0.765, y, "#6a1b9a")
    ax.text(0.185, 0.97, "Divergence: 5 concept briefs", ha="center", weight="bold")
    ax.text(0.62, 0.97, "Confluence: 3 concepts (Ch. 5)", ha="center", weight="bold")
    ax.text(0.43, 0.86, "1-3: same user,\nsame moment,\nsame data -> merge", ha="center", fontsize=8, color="#455a64")
    save(fig, "fig01_concept_confluence.png")


# ---------------------------------------------------------------- 2 architecture
def fig_architecture():
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    box(ax, 0.0, 0.40, 0.155, 0.22, "Recordings\nsimulated test split\n+ VitalDB test\n(held-out only)", "#eceff1", fs=8.5)
    box(ax, 0.20, 0.66, 0.17, 0.16, "RC-07 raw-signal\npre-check\n(before interpolation)", "#ffebee", ec="#c62828", fs=8.5)
    box(ax, 0.20, 0.40, 0.17, 0.18, "Version 5 preprocessing\n+ RC-09 shock-aware\nABP check", "#ffebee", ec="#c62828", fs=8.5)
    box(ax, 0.42, 0.40, 0.17, 0.22, "Version 5 model\n(frozen SOUP,\nIEC 62304)\nforecast, R, C, sigma", "#f3e5f5", ec="#6a1b9a", weight="bold", fs=8.5)
    box(ax, 0.42, 0.14, 0.17, 0.16, "Domain-specific\ncalibration\n(validation splits)", "#f3e5f5", ec="#6a1b9a")
    box(ax, 0.64, 0.58, 0.17, 0.24, "Alert state machine\npersistence, hysteresis,\nconfidence cap,\nRC-08 hold, RC-10 floor", "#fff8e1", ec="#f9a825", fs=8.5)
    box(ax, 0.64, 0.28, 0.17, 0.20, "Intervention tracker\n(event log, baseline,\nconfounding flags)", "#fff8e1", ec="#f9a825", fs=8.5)
    box(ax, 0.86, 0.66, 0.13, 0.15, "Concept A\nlive monitor", "#e3f2fd", ec=COL["A"], weight="bold")
    box(ax, 0.86, 0.44, 0.13, 0.15, "Concept B\nnon-invasive", "#fff3e0", ec=COL["B"], weight="bold")
    box(ax, 0.86, 0.22, 0.13, 0.15, "Concept C\nreview + report", "#e8f5e9", ec=COL["C"], weight="bold")
    for (a, b, c, d) in [(0.15, 0.51, 0.20, 0.72), (0.15, 0.51, 0.20, 0.49), (0.37, 0.72, 0.42, 0.55), (0.37, 0.49, 0.42, 0.49),
                         (0.505, 0.30, 0.505, 0.40), (0.59, 0.55, 0.64, 0.68), (0.59, 0.45, 0.64, 0.38),
                         (0.81, 0.70, 0.86, 0.73), (0.81, 0.66, 0.86, 0.52), (0.81, 0.38, 0.86, 0.30), (0.81, 0.62, 0.86, 0.32)]:
        arrow(ax, a, b, c, d)
    ax.text(0.5, 0.96, "Software architecture: one engine, three concepts (stdlib HTTP server + browser UI, no external services)", ha="center", weight="bold")
    ax.text(0.5, 0.04, "Red = app-layer risk controls added after verification found Version 5 hazards; purple = frozen Version 5 code and its calibration; "
            "amber = app logic.", ha="center", fontsize=8.5, color="#455a64")
    save(fig, "fig02_architecture.png")


# ---------------------------------------------------------------- 3 spec scorecard
def fig_scorecard():
    rows = VER["spec_verification"]
    specs = {s["id"]: s for s in load("specifications.json", os.path.join(APP_ROOT, "evaluation"))["specs"]}
    colours = {"PASS (ideal)": "#2e7d32", "PASS (marginal)": "#9ccc65", "FAIL": "#c62828", "n/a": "#cfd8dc"}
    fig, ax = plt.subplots(figsize=(10.5, 6.6))
    for i, r in enumerate(rows):
        y = len(rows) - 1 - i
        star = specs[r["id"]]["priority"]
        ax.text(-0.1, y, f"{r['id']} {star:2s} {r['need'][:58]}", ha="right", va="center", fontsize=9)
        for j, c in enumerate("ABC"):
            res = r[f"result_{c}"]
            ax.add_patch(plt.Rectangle((j, y - 0.42), 0.94, 0.84, color=colours[res]))
            val = r[f"measured_{c}"]
            label = "n/a" if val is None else (("yes" if val else "no") if isinstance(val, bool) else f"{val:g}")
            ax.text(j + 0.47, y, f"{res.split(' ')[0] if res != 'n/a' else ''} {label}".strip(), ha="center", va="center",
                    fontsize=8.5, color="white" if res in ("FAIL", "PASS (ideal)") else "#102027")
    ax.set_xlim(-0.1, 2.95); ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xticks([0.47, 1.47, 2.47]); ax.set_xticklabels(["Concept A", "Concept B", "Concept C"], weight="bold")
    ax.xaxis.tick_top(); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=v) for v in colours.values()]
    ax.legend(handles, list(colours), loc="upper center", bbox_to_anchor=(0.35, -0.01), ncol=4, frameon=False, fontsize=9)
    ax.set_title("Verification against engineering specifications  (* must meet, ** trade-off acceptable)", pad=28)
    save(fig, "fig03_spec_scorecard.png")


# ---------------------------------------------------------------- 4 decision matrix
def fig_matrix():
    crit = SEL["criteria"]; S = SEL["scores"]; cols = ["DATUM", "A", "B", "C"]
    M = np.array([[S[c][k["id"]] for c in cols] for k in crit])
    fig, ax = plt.subplots(figsize=(9, 5.4))
    ax.imshow(M, cmap="RdYlGn", vmin=1, vmax=5, aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, M[i, j], ha="center", va="center", weight="bold")
    ax.set_yticks(range(len(crit)))
    ax.set_yticklabels([f"{k['id']} {k["name"][:52]} ({k["weight"]}%)" for k in crit], fontsize=9)
    T = SEL["weighted_totals"]; H = SEL["house_of_quality"]["result"]
    ax.set_xticks(range(4))
    ax.set_xticklabels([f"{'Current practice' if c == 'DATUM' else 'Concept ' + c}\n{T[c]:.2f}  (HoQ {H[c]['modified_total']:.2f})" for c in cols])
    ax.xaxis.tick_top()
    ax.set_title("Weighted decision matrix (1-5, rubric defined before scoring)", pad=36)
    save(fig, "fig04_decision_matrix.png")


# ---------------------------------------------------------------- 5 DSM rotated datum
def fig_dsm():
    D = SEL["dsm_by_datum"]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.4), sharey=True)
    for ax, (datum, table) in zip(axes, D.items()):
        names = list(table)
        vals = [table[n]["weighted_total"] for n in names]
        ax.bar(names, vals, color=[COL[n] for n in names])
        ax.axhline(0, color="#263238", lw=0.8)
        for k, (n, v) in enumerate(zip(names, vals)):
            ax.text(k, v + (0.03 if v >= 0 else -0.08), f"{table[n]['total']:+d} / {v:+.2f}", ha="center", fontsize=8.5)
        ax.set_title(f"datum = {'current practice' if datum == 'DATUM' else datum}", fontsize=10)
    axes[0].set_ylabel("weighted +1/0/-1 vs datum")
    fig.suptitle("Decision support matrix with the datum rotated 4 times (BME Design 6.4.3, 6.7)", y=1.04)
    save(fig, "fig05_dsm_rotated_datum.png")


# ---------------------------------------------------------------- 6 sensitivity
def fig_sensitivity():
    sens = SEL["sensitivity"]; be = SEL["break_even_A_vs_C"]; unc = SEL["scoring_uncertainty"]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    ax = axes[0]
    sc = sens["scenarios"]
    y = np.arange(len(sc))
    for k, c in enumerate("ABC"):
        ax.barh(y + (k - 1) * 0.26, [v["totals"][c] for v in sc.values()], 0.26, color=COL[c], label=f"Concept {c}")
    ax.set_yticks(y); ax.set_yticklabels(list(sc), fontsize=8.5); ax.invert_yaxis(); ax.set_xlim(1, 5)
    ax.set_title("Weighted total under alternative weightings"); ax.legend(fontsize=8, loc="lower right")
    ax = axes[1]
    ws = sens["random_weightings"]["win_share"]
    ax.bar(list(ws), [100 * v for v in ws.values()], color=[COL[c] for c in ws])
    for k, v in enumerate(ws.values()):
        ax.text(k, 100 * v + 1.5, f"{100 * v:.0f}%", ha="center")
    ax.set_ylabel("% of 10,000 random weightings won"); ax.set_title("Random-weight robustness (no gate)")
    ax = axes[2]
    w = np.arange(0, 41, 1)
    S = SEL["scores"]; base = {k["id"]: k["weight"] for k in SEL["criteria"]}
    rest = 100 - base["C1"]
    for c in ("A", "C", "B"):
        tot = [sum(S[c][k] * ((x if k == "C1" else base[k] * (100 - x) / rest) / 100) for k in base) for x in w]
        ax.plot(w, tot, color=COL[c], lw=2, label=f"Concept {c}")
    ax.axvline(be["C1"]["flips_at_weight"], color="#455a64", ls="--", lw=1)
    ax.axvline(base["C1"], color=COL["A"], ls=":", lw=1.5)
    ax.text(be["C1"]["flips_at_weight"] + 0.5, 2.1, f"break-even\n{be['C1']['flips_at_weight']}%", fontsize=8.5)
    ax.text(base["C1"] + 0.5, 3.15, f"our weight\n{base['C1']}%", fontsize=8.5, color=COL["A"])
    ax.set_xlabel("weight on C1 Early detection (%)"); ax.set_ylabel("weighted total")
    ax.set_title("Break-even: how much detection must matter"); ax.legend(fontsize=8)
    save(fig, "fig06_weight_sensitivity.png")


# ---------------------------------------------------------------- 7 hazard H-09
def fig_h09():
    layers = VER["synthetic_test"]["hazard_h09"]["layers"]
    names = [l["layer"].replace(" (all controls)", "\n(all controls)").replace("+ ", "+ ") for l in layers]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, key, title, scale in [(axes[0], "under_alert_rate", "Under-alert during true MAP < 55 (%)", 100),
                                  (axes[1], "abp_rejected_rate", "Genuine ABP rejected (%)", 100),
                                  (axes[2], "unsafe_deescalations", "Unsafe de-escalations (count)", 1)]:
        v = [scale * l[key] for l in layers]
        ax.bar(range(len(v)), v, color=["#c62828", "#ef6c00", "#f9a825", "#2e7d32"])
        for k, x in enumerate(v):
            ax.text(k, x + max(v) * 0.02, f"{x:.1f}" if scale == 100 else f"{x:.0f}", ha="center", fontsize=9)
        ax.set_xticks(range(len(v))); ax.set_xticklabels(names, fontsize=7.5, rotation=18, ha="right")
        ax.set_title(title, fontsize=10)
    fig.suptitle(f"Hazard H-09: Version 5 rejects real arterial pressure in profound shock - risk controls layered "
                 f"({VER['synthetic_test']['hazard_h09']['patients']} shock-scenario test patients)", y=1.02)
    save(fig, "fig07_hazard_h09_layers.png")


# ---------------------------------------------------------------- 8 quality gate
def fig_gate():
    g = VER["quality_gate"]; b = g["before_rc07"]
    keys = ["ppg_flatline", "ppg_dropout", "abp_line_flush", "abp_damped", "ecg_flatline", "genuine_shock"]
    labels = ["PPG flat line", "PPG 40% drop-out", "ABP line flush", "ABP damped", "ECG flat line", "Genuine shock\n(should be KEPT)"]
    x = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(x - 0.2, [100 * b[k]["handled_fraction"] for k in keys], 0.4, color="#b0bec5", label="Version 5 alone")
    ax.bar(x + 0.2, [100 * g[k]["handled_fraction"] for k in keys], 0.4, color=COL["A"], label="With RC-07 + RC-09")
    for k in range(len(keys)):
        ax.text(x[k] - 0.2, 100 * b[keys[k]]["handled_fraction"] + 2, f"{100 * b[keys[k]]['handled_fraction']:.0f}", ha="center", fontsize=8.5)
        ax.text(x[k] + 0.2, 100 * g[keys[k]]["handled_fraction"] + 2, f"{100 * g[keys[k]]['handled_fraction']:.0f}", ha="center", fontsize=8.5)
    ax.axhline(95, color="#c62828", ls="--", lw=1); ax.text(5.6, 96, "SPEC-07 marginal 95%", fontsize=8, ha="right", color="#c62828")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9); ax.set_ylim(0, 112)
    ax.set_ylabel("% of windows handled correctly"); ax.legend(loc="lower left", fontsize=9)
    ax.set_title("Signal-quality gate: corrupted windows handled safely (abstain or drop channel); genuine shock retained")
    save(fig, "fig08_quality_gate.png")


# ---------------------------------------------------------------- 9 calibration
def fig_calibration():
    cal = VER["calibration"]
    regs = ["ppg_ecg", "ppg_ecg_abp", "ppg_ecg_abp_intervention"]
    lab = ["ECG + PPG\n(Concept B)", "+ ABP", "+ ABP + intervention"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for ax, dom in zip(axes, ("synthetic", "real")):
        x = np.arange(len(regs))
        ax.bar(x - 0.2, [cal[dom][r]["mae_uncalibrated"] for r in regs], 0.4, color="#b0bec5", label="before calibration")
        ax.bar(x + 0.2, [cal[dom][r]["mae_calibrated"] for r in regs], 0.4, color=COL["A"], label="after bias calibration")
        for k, r in enumerate(regs):
            ax.text(k, max(cal[dom][r]["mae_uncalibrated"], cal[dom][r]["mae_calibrated"]) + 0.6,
                    f"bias {cal[dom][r]['bias_map']:+.1f}", ha="center", fontsize=8.5)
        ax.set_xticks(x); ax.set_xticklabels(lab); ax.set_ylabel("MAE (mmHg), validation split")
        ax.set_title(f"{'Simulated' if dom == 'synthetic' else 'Real (VitalDB)'} validation data"); ax.legend(fontsize=8.5)
    fig.suptitle("Domain-specific calibration (fitted on validation splits only; test data never used)", y=1.03)
    save(fig, "fig09_calibration.png")


# ---------------------------------------------------------------- 10 performance comparison
def fig_performance():
    syn, vit = VER["synthetic_test"], VER["vitaldb_test"]
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.9))
    groups = ["DATUM", "A", "B"]
    names = ["Current\npractice", "A", "B"]
    for ax, key, title, ylim in [(axes[0], "sensitivity_deteriorating", "Sensitivity (DETERIORATING+)", (0, 1.1)),
                                 (axes[1], "specificity_deteriorating", "Specificity (DETERIORATING+)", (0, 1.1)),
                                 (axes[2], "false_alarms_per_hour_any", "False alarm episodes / h (any level)", None)]:
        x = np.arange(3)
        for k, (dom, d) in enumerate((("simulated", syn), ("real", vit))):
            v = [d[g]["alerts"][key] or 0 for g in groups]
            ax.bar(x + (k - 0.5) * 0.38, v, 0.38, color=[COL[g] for g in groups], alpha=0.55 if k == 0 else 1.0,
                   label=dom, edgecolor="#263238", lw=0.4)
            for j, val in enumerate(v):
                ax.text(x[j] + (k - 0.5) * 0.38, val * 1.02 + 0.01, f"{val:.2f}" if val < 5 else f"{val:.1f}", ha="center", fontsize=7.5)
        ax.set_xticks(x); ax.set_xticklabels(names); ax.set_title(title, fontsize=10)
        if ylim: ax.set_ylim(*ylim)
    axes[0].text(0.02, 1.02, "pale = simulated, solid = real", transform=axes[0].transAxes, fontsize=8)
    ax = axes[3]
    v = [syn["A"]["accuracy"]["mae"], vit["A"]["accuracy_calibrated"]["mae"], syn["B"]["accuracy"]["mae"], vit["B"]["accuracy_calibrated"]["mae"],
         SEL["datum_metrics"]["persistence_mae_real"]]
    ax.bar(["A sim", "A real", "B sim", "B real", "Persist.\nreal"], v, color=[COL["A"], COL["A"], COL["B"], COL["B"], COL["DATUM"]])
    for k, val in enumerate(v):
        ax.text(k, val + 0.3, f"{val:.1f}", ha="center", fontsize=8.5)
    ax.axhline(5, color="#2e7d32", ls="--", lw=1); ax.axhline(10, color="#f9a825", ls="--", lw=1)
    ax.set_title("15 s forecast MAE (mmHg)", fontsize=10)
    fig.suptitle("Measured performance on held-out data (210 simulated patients, 30 real VitalDB cases)", y=1.03)
    save(fig, "fig10_performance_comparison.png")


# ---------------------------------------------------------------- 11 example case
def fig_example():
    from app.engine.model_service import ModelService
    from app.engine.recordings import RecordingLibrary
    from app.engine.session import ReplaySession
    model = ModelService(APP_ROOT)
    lib = RecordingLibrary(os.path.join(APP_ROOT, "demo_data"))
    rec = lib.get("synthetic_4993")
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    for ax, concept in zip(axes, ("A", "B")):
        ticks = ReplaySession(rec, model, concept, step_sec=1.0).run_to_end()
        t = np.array([k["t"] for k in ticks])
        f = np.array([np.nan if k["forecast_map"] is None else k["forecast_map"] for k in ticks])
        s = np.array([np.nan if k["sigma"] is None else k["sigma"] for k in ticks])
        truth = np.array([np.nan if k["truth"]["future_map"] is None else k["truth"]["future_map"] for k in ticks])
        lv = [k["alert"]["level_name"] for k in ticks]
        for i in range(len(t) - 1):
            if lv[i] in LVL:
                ax.axvspan(t[i], t[i + 1], color=LVL[lv[i]], alpha=0.16, lw=0)
        ax.fill_between(t, f - s, f + s, color=COL[concept], alpha=0.15)
        ax.plot(t, f, color=COL[concept], lw=1.8, label=f"Concept {concept} forecast MAP (±σ)")
        ax.plot(t, truth, color="#263238", lw=1.2, ls="--", label="true future MAP")
        ax.axhline(65, color="#ef6c00", ls=":", lw=1); ax.axhline(55, color="#c62828", ls=":", lw=1)
        ax.set_ylabel("MAP (mmHg)"); ax.legend(loc="upper right", fontsize=8.5)
        ax.set_title(f"Concept {concept}: {'ABP + ECG + PPG' if concept == 'A' else 'ECG + PPG only (confidence-capped)'}", fontsize=10)
    axes[1].set_xlabel("time (s)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c, alpha=0.4) for c in LVL.values()]
    fig.legend(handles, list(LVL), loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.04), frameon=False, fontsize=8.5)
    fig.suptitle(f"Storyboard case: {rec.title} (held-out simulated patient) - background = displayed alert level", y=1.0)
    save(fig, "fig11_example_case.png")


# ---------------------------------------------------------------- 12 FMEA risk chart
def fig_fmea():
    sev = list(FMEA["severity_scale"]); prob = list(FMEA["probability_scale"])[::-1]
    reg = FMEA["acceptability"]; colour = {"U": "#ef9a9a", "A": "#ffffff", "B": "#a5d6a7"}
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True)
    for ax, when in zip(axes, ("before", "after")):
        for i, p in enumerate(prob):
            for j, s in enumerate(sev):
                ax.add_patch(plt.Rectangle((j, len(prob) - 1 - i), 1, 1, fc=colour[reg[p][j]], ec="#90a4ae"))
        cell = {}
        for r in FMEA["risks"]:
            p = r["p_before"] if when == "before" else r["p_after"]
            key = (sev.index(r["severity"]), len(prob) - 1 - prob.index(p))
            cell.setdefault(key, []).append(r["id"])
        for (x, y), ids in cell.items():
            ax.text(x + 0.5, y + 0.5, "\n".join(", ".join(ids[k:k + 2]) for k in range(0, len(ids), 2)),
                    ha="center", va="center", fontsize=7.8, weight="bold")
        ax.set_xlim(0, 5); ax.set_ylim(0, 5)
        ax.set_xticks(np.arange(5) + 0.5); ax.set_xticklabels([f"{s}\n({FMEA['severity_scale'][s]})" for s in sev], fontsize=8.5)
        ax.set_yticks(np.arange(5) + 0.5); ax.set_yticklabels(prob[::-1], fontsize=9)
        ax.set_title(f"{'Before' if when == 'before' else 'After'} risk controls", fontsize=11)
        for sp in ax.spines.values():
            sp.set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, fc=c, ec="#90a4ae") for c in colour.values()]
    fig.legend(handles, ["Unacceptable", "ALARP", "Broadly acceptable"], loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.05), frameon=False)
    fig.suptitle("Risk acceptability (ISO 14971; BME Design Fig. 8.13, severity Table 8.3) - R-04 (Concept B) stays unacceptable", y=1.01)
    save(fig, "fig12_fmea_risk_chart.png")


if __name__ == "__main__":
    for fn in (fig_confluence, fig_architecture, fig_scorecard, fig_matrix, fig_dsm, fig_sensitivity, fig_h09,
               fig_gate, fig_calibration, fig_performance, fig_fmea, fig_example):
        fn()
