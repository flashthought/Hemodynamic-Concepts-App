"""
Build a static, server-free copy of the app for public hosting (e.g. Vercel).

The model is run here, once, on every held-out demo recording and every start
position the UI offers, through exactly the same pipeline as the live server
(ReplaySession, risk controls, calibration). The browser then replays these
pre-computed outputs (app/static/js/static-api.js), so visitors see the same
alerts, forecasts and reviews as the live app without a Python/PyTorch server.

    python scripts/build_static.py            -> site/   (deploy this folder)

What differs from the live server (stated on the site):
  * Interventions cannot be marked by hand; the recorded pump/EHR events drive
    the response tracker exactly as in the live app.
  * Displayed per-update latency is the latency measured when the site was built.
"""

import json
import os
import shutil
import sys

import numpy as np

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
from app import markdown_lite  # noqa: E402
from app.engine import config  # noqa: E402
from app.engine.model_service import ModelService  # noqa: E402
from app.engine.recordings import RecordingLibrary  # noqa: E402
from app.engine.review import report_html, run_review, timeline_csv  # noqa: E402
from app.engine.session import ReplaySession  # noqa: E402
from app.server import CONCEPTS, _clean, _doc_list, _json_default  # noqa: E402

OUT = os.path.join(APP_ROOT, "site")
DATA = os.path.join(OUT, "data")
# Tick fields the browser never reads; dropped to keep the download small.
DROP = ("forecast_map_uncalibrated", "bias_correction", "channels_used", "u_now", "concept", "status")


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(obj), f, separators=(",", ":"), default=_json_default)


def start_options(rec):
    """Must match populateStarts() in app/static/js/live.js."""
    s = rec.summary()
    opts = [None]
    for ev in s["events"][:3]:
        opts.append(max(s["first_window_end"], ev["t"] - 60))
    if s["duration_sec"] > 600:
        opts.append(round(s["duration_sec"] / 2))
    return opts


def start_key(v):
    return "begin" if v is None else f"{round(float(v), 1):g}"  # must match startKey() in static-api.js


def waves(rec):
    def dec(x):
        arr = np.nan_to_num(np.asarray(x, dtype=np.float64)[::2], nan=0.0)
        return [round(float(v), 2) for v in arr]
    return {"fs": rec.fs / 2, "ecg": dec(rec.ecg), "ppg": dec(rec.ppg), "abp": dec(rec.abp)}


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(os.path.join(APP_ROOT, "app", "static"), os.path.join(OUT, "static"))
    shutil.copytree(os.path.join(APP_ROOT, "docs", "figures"), os.path.join(OUT, "docs", "figures"))

    # index.html: same page, switched to static mode before the app module loads.
    with open(os.path.join(APP_ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace('<script type="module" src="/static/js/main.js"></script>',
                        '<script>window.HEMO_STATIC = true;</script>\n  <script type="module" src="/static/js/main.js"></script>')
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    model = ModelService(APP_ROOT)
    lib = RecordingLibrary(os.path.join(APP_ROOT, "demo_data"))
    info = model.info()
    info["device"] = f"pre-computed ({info['device']})"
    dump(os.path.join(DATA, "meta.json"), {
        "model": info, "concepts": CONCEPTS, "static": True,
        "thresholds": {"target": config.MAP_TARGET, "critical": config.MAP_CRITICAL, "watch": config.MAP_WATCH,
                       "rapid_drop": config.RAPID_DROP_MMHG, "escalate_persist": config.ESCALATE_PERSIST,
                       "deescalate_persist": config.DEESCALATE_PERSIST},
        "window_sec": config.WINDOW_SEC, "horizon_sec": config.FORECAST_HORIZON_SEC, "recommended": "A"})
    summaries = lib.summaries()
    dump(os.path.join(DATA, "recordings.json"), {"recordings": summaries})
    for name in ("verification_summary.json", "concept_selection.json"):
        with open(os.path.join(APP_ROOT, "evaluation", "results", name), encoding="utf-8") as f:
            dump(os.path.join(DATA, "verification.json" if name.startswith("verification") else "selection.json"), json.load(f))
    docs = _doc_list()
    dump(os.path.join(DATA, "docs", "index.json"), {"docs": docs})
    for d in docs:
        with open(os.path.join(APP_ROOT, "docs", d["name"] + ".md"), encoding="utf-8") as f:
            dump(os.path.join(DATA, "docs", d["name"] + ".json"),
                 {"name": d["name"], "html": markdown_lite.render(f.read(), base="/docs/")})

    sessions = {}
    for s in summaries:
        rec = lib.get(s["id"])
        dump(os.path.join(DATA, "waves", rec.rec_id + ".json"), waves(rec))
        for concept in ("A", "B"):
            for start in start_options(rec):
                ticks = ReplaySession(rec, model, concept, start_sec=start).run_to_end(include_waveforms=False)
                for k in ticks:
                    for key in DROP:
                        k.pop(key, None)
                key = f"{rec.rec_id}_{concept}_{start_key(start)}"
                dump(os.path.join(DATA, "sessions", key + ".json"), {"ticks": ticks})
                sessions[key] = len(ticks)
        review = run_review(rec, model, step_sec=2.0)
        rdir = os.path.join(DATA, "review", rec.rec_id)
        dump(os.path.join(rdir, "index.json"), review)
        with open(os.path.join(rdir, "report.html"), "w", encoding="utf-8") as f:
            f.write(report_html(review, model.info()))
        with open(os.path.join(rdir, "timeline.csv"), "w", encoding="utf-8") as f:
            f.write(timeline_csv(review))
        with open(os.path.join(rdir, "review.json"), "w", encoding="utf-8") as f:
            json.dump(_clean(review), f, indent=2, default=_json_default)
        print(f"  {rec.rec_id}: {len(start_options(rec))} start positions x 2 concepts + review", flush=True)

    dump(os.path.join(DATA, "sessions", "index.json"), sessions)
    shutil.copy(os.path.join(APP_ROOT, "deploy", "vercel.json"), os.path.join(OUT, "vercel.json"))
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(OUT) for f in fs)
    print(f"site/ built: {len(sessions)} pre-computed sessions, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
