"""
Concept C - Clinician Decision Support Review System (original Concept 5).

Replays a whole episode in batch with Concept A's channel configuration, then
condenses the timeline into what a clinician needs for handoff, audit or
quality review: alert episodes, time in each state, intervention responses,
R and C trends, signal-quality history and (where ground truth exists) how
well the forecasts matched what actually happened.

It is retrospective by design: it does not alert anyone in real time.
"""

import csv
import html
import io
from typing import Dict, List, Optional

import numpy as np

from app.engine import config
from app.engine.alerting import LEVELS
from app.engine.session import ReplaySession


def _fmt_t(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _num(value: Optional[float], fmt: str) -> str:
    """Format an optional number for a table cell; blank when missing."""
    return "" if value is None else fmt.format(value)


def _episodes(ticks: List[Dict[str, object]], step: float) -> List[Dict[str, object]]:
    """Contiguous stretches where the displayed alert was WATCH or higher."""
    episodes, cur = [], None
    for k in ticks:
        lvl = k["alert"]["level"]
        active = lvl is not None and lvl >= 1
        if active:
            if cur is None:
                cur = {"start": k["t"], "end": k["t"], "peak": lvl, "min_forecast_map": k["forecast_map"]}
            cur["end"] = k["t"]
            cur["peak"] = max(cur["peak"], lvl)
            if k["forecast_map"] is not None:
                cur["min_forecast_map"] = min(cur["min_forecast_map"], k["forecast_map"])
        elif cur is not None and lvl is not None:
            episodes.append(cur)
            cur = None
    if cur is not None:
        episodes.append(cur)
    for e in episodes:
        e["duration_sec"] = round(e["end"] - e["start"] + step, 1)
        e["peak_level"] = LEVELS[e.pop("peak")]
    return episodes


def _trend(values: List[Optional[float]], times: List[float], span: float = 30.0) -> Dict[str, Optional[float]]:
    pairs = [(t, v) for t, v in zip(times, values) if v is not None]
    if len(pairs) < 2:
        return {"start": None, "end": None, "change_pct": None}
    t0, t1 = pairs[0][0], pairs[-1][0]
    start = float(np.median([v for t, v in pairs if t <= t0 + span]))
    end = float(np.median([v for t, v in pairs if t >= t1 - span]))
    return {"start": round(start, 3), "end": round(end, 3),
            "change_pct": round(100 * (end - start) / start, 1) if start else None}


def run_review(recording, model, step_sec: float = 2.0) -> Dict[str, object]:
    session = ReplaySession(recording, model, "A", step_sec=step_sec)
    ticks = session.run_to_end(include_waveforms=False)
    times = [k["t"] for k in ticks]
    monitored = len(ticks) * step_sec

    time_in = {name: 0.0 for name in LEVELS}
    time_in["SIGNAL CHECK"] = 0.0
    for k in ticks:
        time_in[k["alert"]["level_name"]] += step_sec

    usable = [k for k in ticks if k["forecast_map"] is not None]
    fmaps = [k["forecast_map"] for k in usable]
    cmaps = [k["current_map"] for k in ticks if k["current_map"] is not None]
    episodes = _episodes(ticks, step_sec)
    interventions = ticks[-1]["interventions"] if ticks else []

    warnings_count: Dict[str, int] = {}
    for k in ticks:
        for w in k["warnings"]:
            warnings_count[w] = warnings_count.get(w, 0) + 1

    verification: Dict[str, object] = {}
    pairs = [(k["forecast_map"], k["truth"].get("future_map")) for k in usable if k["truth"].get("future_map") is not None]
    if pairs:
        err = np.array([p - q for p, q in pairs])
        verification = {
            "truth_kind": ticks[0]["truth"].get("kind"),
            "windows": len(pairs),
            "map_mae": round(float(np.mean(np.abs(err))), 2),
            "map_bias": round(float(np.mean(err)), 2),
        }
        if recording.has_truth:
            r_pred = [k["r"] for k in usable]
            r_true = [k["truth"]["r"] for k in usable]
            c_pred = [k["c"] for k in usable]
            c_true = [k["truth"]["c"] for k in usable]
            if np.std(r_pred) > 1e-6 and np.std(r_true) > 1e-6:
                verification["r_correlation"] = round(float(np.corrcoef(r_pred, r_true)[0, 1]), 3)
            if np.std(c_pred) > 1e-6 and np.std(c_true) > 1e-6:
                verification["c_correlation"] = round(float(np.corrcoef(c_pred, c_true)[0, 1]), 3)

    summary = {
        "monitored_sec": round(monitored, 1),
        "time_in_state_sec": {k: round(v, 1) for k, v in time_in.items()},
        "time_in_state_pct": {k: round(100 * v / monitored, 1) if monitored else 0.0 for k, v in time_in.items()},
        "alert_episodes": len(episodes),
        "min_forecast_map": round(min(fmaps), 1) if fmaps else None,
        "min_current_map": round(min(cmaps), 1) if cmaps else None,
        "abstention_pct": round(100 * (len(ticks) - len(usable)) / len(ticks), 1) if ticks else 0.0,
        "r_trend": _trend([k["r"] for k in ticks], times),
        "c_trend": _trend([k["c"] for k in ticks], times),
        "median_latency_ms": round(float(np.median([k["latency_ms"] for k in ticks])), 2) if ticks else None,
    }
    result = {
        "recording": recording.summary(),
        "step_sec": step_sec,
        "summary": summary,
        "episodes": episodes,
        "interventions": interventions,
        "warnings": warnings_count,
        "verification": verification,
        "timeline": [{
            "t": k["t"], "current_map": k["current_map"], "forecast_map": k["forecast_map"],
            "level": k["alert"]["level"], "level_name": k["alert"]["level_name"],
            "r": k["r"], "c": k["c"], "p_below_target": k["p_below_target"], "quality": k["quality"],
            "regime": k["regime"], "truth_future_map": k["truth"].get("future_map"),
        } for k in ticks],
    }
    result["narrative"] = narrative(result)
    return result


def narrative(review: Dict[str, object]) -> List[str]:
    """Plain-language handoff summary (Nielsen: speak the user's language)."""
    s = review["summary"]
    pct = s["time_in_state_pct"]
    lines = [
        f"Monitored {_fmt_t(s['monitored_sec'])} (mm:ss) of {review['recording']['title']}.",
        f"Time at DETERIORATING or CRITICAL: {pct['DETERIORATING'] + pct['CRITICAL']:.0f}% "
        f"(CRITICAL {pct['CRITICAL']:.0f}%); time STABLE: {pct['STABLE']:.0f}%.",
        f"{s['alert_episodes']} alert episode(s) at WATCH or above."
        + (f" Lowest forecast MAP {s['min_forecast_map']:.0f} mmHg." if s["min_forecast_map"] is not None else ""),
    ]
    for iv in review["interventions"]:
        detail = ""
        if iv.get("d_map") is not None:
            detail = f" (MAP {iv['d_map']:+.0f} mmHg"
            if iv.get("r_change_pct") is not None:
                detail += f", R {iv['r_change_pct']:+.0f}%"
            if iv.get("c_change_pct") is not None:
                detail += f", C {iv['c_change_pct']:+.0f}%"
            detail += ")"
        lines.append(f"{iv['label']} at {_fmt_t(iv['onset_t'])}: {iv['status']}{detail}.")
    rt, ct = s["r_trend"], s["c_trend"]
    if rt["change_pct"] is not None and ct["change_pct"] is not None:
        lines.append(f"Vascular resistance R changed {rt['change_pct']:+.0f}% and compliance C "
                     f"{ct['change_pct']:+.0f}% from start to end of the episode.")
    if s["abstention_pct"] > 0:
        lines.append(f"Forecasts were withheld for {s['abstention_pct']:.0f}% of windows due to signal quality.")
    lines.append("Decision-support summary only - all treatment decisions rest with the clinical team.")
    return lines


def timeline_csv(review: Dict[str, object]) -> str:
    buf = io.StringIO()
    cols = ["t", "current_map", "forecast_map", "truth_future_map", "level_name", "p_below_target",
            "r", "c", "quality", "regime"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for row in review["timeline"]:
        w.writerow(row)
    return buf.getvalue()


# --- printable report with server-side SVG charts ----------------------------
LEVEL_COLOURS = {0: "#2e7d32", 1: "#0277bd", 2: "#b26a00", 3: "#c62828", None: "#757575"}


def _svg_series(timeline, keys, colours, height=180, width=760, ylabel="", ref_lines=()):
    ts = [row["t"] for row in timeline]
    vals = [row[k] for row in timeline for k in keys if row[k] is not None]
    if not ts or not vals:
        return "<p>No data.</p>"
    lo, hi = min(vals + [r for r, _ in ref_lines]), max(vals + [r for r, _ in ref_lines])
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    t0, t1 = ts[0], ts[-1] if ts[-1] > ts[0] else ts[0] + 1
    ml, mr, mt, mb = 48, 10, 10, 26
    def x(t): return ml + (t - t0) / (t1 - t0) * (width - ml - mr)
    def y(v): return mt + (hi - v) / (hi - lo) * (height - mt - mb)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{html.escape(ylabel)}">',
             f'<rect x="{ml}" y="{mt}" width="{width-ml-mr}" height="{height-mt-mb}" fill="#fafafa" stroke="#ddd"/>']
    for val, label in ref_lines:
        parts.append(f'<line x1="{ml}" x2="{width-mr}" y1="{y(val):.1f}" y2="{y(val):.1f}" stroke="#999" stroke-dasharray="4 3"/>'
                     f'<text x="{width-mr-2}" y="{y(val)-3:.1f}" font-size="10" text-anchor="end" fill="#666">{label}</text>')
    for k, colour in zip(keys, colours):
        pts = [f"{x(row['t']):.1f},{y(row[k]):.1f}" for row in timeline if row[k] is not None]
        if pts:
            parts.append(f'<polyline fill="none" stroke="{colour}" stroke-width="1.6" points="{" ".join(pts)}"/>')
    for frac in (0, 0.5, 1):
        v = lo + frac * (hi - lo)
        parts.append(f'<text x="{ml-6}" y="{y(v)+3:.1f}" font-size="10" text-anchor="end" fill="#555">{v:.1f}</text>')
    parts.append(f'<text x="{ml}" y="{height-6}" font-size="10" fill="#555">{_fmt_t(t0)}</text>'
                 f'<text x="{width-mr}" y="{height-6}" font-size="10" text-anchor="end" fill="#555">{_fmt_t(t1)}</text>'
                 f'<text x="{ml+4}" y="{mt+12}" font-size="11" fill="#333">{html.escape(ylabel)}</text></svg>')
    return "".join(parts)


def _svg_alert_strip(timeline, width=760, height=34):
    ts = [row["t"] for row in timeline]
    if len(ts) < 2:
        return ""
    t0, t1 = ts[0], ts[-1]
    ml, mr = 48, 10
    def x(t): return ml + (t - t0) / (t1 - t0) * (width - ml - mr)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Alert level timeline">']
    for a, b in zip(timeline, timeline[1:]):
        parts.append(f'<rect x="{x(a["t"]):.1f}" y="6" width="{max(0.5, x(b["t"]) - x(a["t"])):.1f}" height="16" '
                     f'fill="{LEVEL_COLOURS[a["level"]]}"/>')
    parts.append(f'<text x="{ml-6}" y="19" font-size="10" text-anchor="end" fill="#555">Alert</text></svg>')
    return "".join(parts)


def report_html(review: Dict[str, object], model_info: Dict[str, object]) -> str:
    s = review["summary"]
    rec = review["recording"]
    tl = review["timeline"]
    esc = html.escape
    rows_ep = "".join(
        f"<tr><td>{_fmt_t(e['start'])}</td><td>{_fmt_t(e['end'])}</td><td>{e['duration_sec']:.0f} s</td>"
        f"<td>{esc(e['peak_level'])}</td><td>{_num(e['min_forecast_map'], '{:.0f}')}</td></tr>"
        for e in review["episodes"]) or '<tr><td colspan="5">No alert episodes.</td></tr>'
    rows_iv = "".join(
        f"<tr><td>{_fmt_t(i['onset_t'])}</td><td>{esc(i['label'])}</td><td>{esc(i['origin'])}</td>"
        f"<td><b>{esc(i['status'])}</b></td><td>{_num(i['d_map'], '{:+.0f} mmHg')}</td>"
        f"<td>{_num(i['r_change_pct'], '{:+.0f}%')}</td></tr>"
        for i in review["interventions"]) or '<tr><td colspan="6">No interventions recorded.</td></tr>'
    state_rows = "".join(f"<tr><td>{esc(k)}</td><td>{v:.0f} s</td><td>{s['time_in_state_pct'][k]:.0f}%</td></tr>"
                         for k, v in s["time_in_state_sec"].items())
    ver = review["verification"]
    ver_html = ""
    if ver:
        ver_html = (f"<h2>Forecast check against {esc(str(ver.get('truth_kind')))}</h2><p>"
                    f"MAP MAE {ver['map_mae']} mmHg, bias {ver['map_bias']:+} mmHg over {ver['windows']} windows"
                    + (f"; R correlation {ver['r_correlation']}" if "r_correlation" in ver else "")
                    + (f"; C correlation {ver['c_correlation']}" if "c_correlation" in ver else "") + ".</p>")
    narrative_html = "".join(f"<li>{esc(line)}</li>" for line in review["narrative"])
    map_chart = _svg_series(tl, ["current_map", "forecast_map"], ["#37474f", "#6a1b9a"],
                            ylabel="MAP (mmHg): current (grey) and 15 s forecast (purple)",
                            ref_lines=((config.MAP_TARGET, "65 target"), (config.MAP_CRITICAL, "55 critical")))
    r_chart = _svg_series(tl, ["r"], ["#00695c"], height=140, ylabel="Vascular resistance R (mmHg·s/mL)")
    c_chart = _svg_series(tl, ["c"], ["#ad1457"], height=140, ylabel="Arterial compliance C (mL/mmHg)")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Episode review - {esc(rec['title'])}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1c2833;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.45}}
h1{{font-size:22px;margin-bottom:2px}} h2{{font-size:16px;margin-top:22px;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #ddd;padding:5px 7px;text-align:left}}
th{{background:#f3f5f7}} .meta{{color:#566573;font-size:12px}} .banner{{background:#fff8e1;border:1px solid #ffe082;padding:8px 10px;font-size:13px;border-radius:4px}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<h1>Haemodynamic episode review</h1>
<div class="meta">{esc(rec['title'])} · source: {esc(rec['source'])} · duration {_fmt_t(rec['duration_sec'])} ·
model {esc(str(model_info.get('model_version')))} (sha256 {esc(str(model_info.get('sha256',''))[:12])}…)</div>
<p class="banner">Clinical decision-support summary for review, handoff and audit. It is not a diagnosis and does not
recommend treatment; all treatment decisions rest with the clinical team.</p>
<h2>Summary</h2><ul>{narrative_html}</ul>
<h2>Timeline</h2>{_svg_alert_strip(tl)}{map_chart}{r_chart}{c_chart}
<h2>Time in each state</h2><table><tr><th>State</th><th>Time</th><th>Share</th></tr>{state_rows}</table>
<h2>Alert episodes (WATCH or higher)</h2><table><tr><th>Start</th><th>End</th><th>Duration</th><th>Peak level</th>
<th>Lowest forecast MAP</th></tr>{rows_ep}</table>
<h2>Interventions and response</h2><table><tr><th>Onset</th><th>Event</th><th>Recorded by</th><th>Response</th>
<th>&Delta; MAP</th><th>&Delta; R</th></tr>{rows_iv}</table>
{ver_html}
<p class="meta">Generated by the Hemodynamic Concepts App (Concept C). Monitored {s['monitored_sec']:.0f} s at
{review['step_sec']:.0f} s resolution; forecasts withheld on {s['abstention_pct']:.0f}% of windows for signal quality.</p>
<p class="noprint"><button onclick="window.print()">Print / save as PDF</button></p>
</body></html>"""
