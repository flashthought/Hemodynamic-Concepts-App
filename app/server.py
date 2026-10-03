"""
Hemodynamic Concepts App - web server.

One web application hosting all three concepts:
  Concept A  Integrated deterioration alert, forecast dashboard and
             intervention-response monitor (original concepts 1 + 2 + 3)
  Concept B  Non-invasive early-warning tool (original concept 4)
  Concept C  Clinician decision-support review system (original concept 5)
plus the Design History File and the verification evidence.

Built on the Python standard library (http.server) so the only third-party
components are those the model itself needs - fewer SOUP items to manage
under IEC 62304. This is a design prototype (BME Design Ch. 7), not a
production deployment: it binds to localhost by default and serves
de-identified research data only.

Run:  scripts/run_app.sh [--port 8765]     (from the app root)
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from app import markdown_lite  # noqa: E402
from app.engine import config  # noqa: E402
from app.engine.model_service import ModelService  # noqa: E402
from app.engine.recordings import RecordingLibrary  # noqa: E402
from app.engine.review import report_html, run_review, timeline_csv  # noqa: E402
from app.engine.session import ReplaySession  # noqa: E402

STATIC_DIR = os.path.join(APP_ROOT, "app", "static")
DOCS_DIR = os.path.join(APP_ROOT, "docs")
RESULTS_DIR = os.path.join(APP_ROOT, "evaluation", "results")
MAX_BODY = 64 * 1024
MAX_SESSIONS = 24
SESSION_IDLE_SEC = 30 * 60
MAX_STEPS_PER_CALL = 60

CONCEPTS = {
    "A": {
        "name": "Integrated Deterioration Alert & Intervention Response Monitor",
        "short": "Integrated Monitor",
        "combines": ["Concept 1 - Real-time deterioration alert",
                     "Concept 2 - Haemodynamic forecast dashboard",
                     "Concept 3 - Intervention response monitor"],
        "inputs": "ECG, PPG, invasive ABP, intervention events",
        "setting": "ICU / operating room with an arterial line",
        "mode": "Real-time",
    },
    "B": {
        "name": "Non-Invasive Early Warning Tool",
        "short": "Non-Invasive Warning",
        "combines": ["Concept 4 - Non-invasive early warning tool"],
        "inputs": "ECG and PPG only (no arterial line)",
        "setting": "Step-down unit, recovery area, lower-resource monitoring",
        "mode": "Real-time",
    },
    "C": {
        "name": "Clinician Decision Support Review System",
        "short": "Episode Review",
        "combines": ["Concept 5 - Clinician decision support review system"],
        "inputs": "Stored ECG, PPG, ABP and intervention records",
        "setting": "Handoff, audit, quality review, research",
        "mode": "Retrospective",
    },
}


class AppState:
    def __init__(self, device: str):
        self.model = ModelService(APP_ROOT, device=device)
        self.library = RecordingLibrary(os.path.join(APP_ROOT, "demo_data"))
        self.sessions = {}
        self.reviews = {}
        self.lock = threading.Lock()

    def prune(self):
        now = time.time()
        with self.lock:
            for sid in [s for s, v in self.sessions.items() if now - v.last_used > SESSION_IDLE_SEC]:
                del self.sessions[sid]

    def review(self, rec_id: str):
        with self.lock:
            cached = self.reviews.get(rec_id)
        if cached is None:
            cached = run_review(self.library.get(rec_id), self.model, step_sec=2.0)
            with self.lock:
                self.reviews[rec_id] = cached
        return cached


STATE: AppState = None  # set in main()


def _verification_summary():
    path = os.path.join(RESULTS_DIR, "verification_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _doc_list():
    items = []
    for name in sorted(os.listdir(DOCS_DIR)):
        if name.endswith(".md"):
            with open(os.path.join(DOCS_DIR, name), "r", encoding="utf-8") as f:
                first = f.readline().strip().lstrip("# ").strip()
            items.append({"name": name[:-3], "title": first or name})
    return items


class Handler(BaseHTTPRequestHandler):
    server_version = "HemodynamicConceptsApp/1.0"

    def log_message(self, fmt, *args):  # quieter console: one line per API call
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # -- response helpers --------------------------------------------------------
    def _send(self, status, body: bytes, ctype: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK):
        self._send(status, json.dumps(_clean(obj), allow_nan=False, default=_json_default).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request body too large.")
        if length == 0:
            return {}
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON object expected.")
        return data

    def _file(self, root, rel):
        path = os.path.realpath(os.path.join(root, rel))
        if not path.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(path):
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(HTTPStatus.OK, f.read(), ctype)

    # -- routing -----------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                return self._file(STATIC_DIR, "index.html")
            if path.startswith("/static/"):
                return self._file(STATIC_DIR, path[len("/static/"):])
            if path.startswith("/docs/figures/"):
                return self._file(os.path.join(DOCS_DIR, "figures"), path[len("/docs/figures/"):])
            if path == "/api/meta":
                return self._json({
                    "model": STATE.model.info(),
                    "concepts": CONCEPTS,
                    "thresholds": {"target": config.MAP_TARGET, "critical": config.MAP_CRITICAL,
                                   "watch": config.MAP_WATCH, "rapid_drop": config.RAPID_DROP_MMHG,
                                   "escalate_persist": config.ESCALATE_PERSIST,
                                   "deescalate_persist": config.DEESCALATE_PERSIST},
                    "window_sec": config.WINDOW_SEC, "horizon_sec": config.FORECAST_HORIZON_SEC,
                    "recommended": "A",
                })
            if path == "/api/recordings":
                return self._json({"recordings": STATE.library.summaries()})
            if path == "/api/verification":
                summary = _verification_summary()
                return self._json(summary if summary else {"error": "Run evaluation/evaluate_concepts.py first."})
            if path == "/api/selection":
                sel = os.path.join(RESULTS_DIR, "concept_selection.json")
                if not os.path.exists(sel):
                    return self._json({"error": "Run evaluation/concept_selection.py first."})
                with open(sel, "r", encoding="utf-8") as f:
                    return self._json(json.load(f))
            if path == "/api/docs":
                return self._json({"docs": _doc_list()})
            m = re.fullmatch(r"/api/docs/([\w\-]+)", path)
            if m:
                doc = os.path.join(DOCS_DIR, m.group(1) + ".md")
                if not os.path.isfile(doc):
                    return self._error(HTTPStatus.NOT_FOUND, "No such document.")
                with open(doc, "r", encoding="utf-8") as f:
                    return self._json({"name": m.group(1), "html": markdown_lite.render(f.read(), base="/docs/")})
            m = re.fullmatch(r"/api/review/([\w\-]+)(/report\.html|/timeline\.csv|/review\.json)?", path)
            if m:
                review = STATE.review(m.group(1))
                kind = m.group(2)
                if kind == "/report.html":
                    body = report_html(review, STATE.model.info()).encode("utf-8")
                    return self._send(HTTPStatus.OK, body, "text/html; charset=utf-8",
                                      {"Content-Disposition": f'inline; filename="review_{m.group(1)}.html"'})
                if kind == "/timeline.csv":
                    return self._send(HTTPStatus.OK, timeline_csv(review).encode("utf-8"), "text/csv; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="timeline_{m.group(1)}.csv"'})
                if kind == "/review.json":
                    body = json.dumps(_clean(review), indent=2, default=_json_default).encode("utf-8")
                    return self._send(HTTPStatus.OK, body, "application/json; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="review_{m.group(1)}.json"'})
                return self._json(review)
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        except KeyError as exc:
            return self._error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:  # report, never crash the server
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            STATE.prune()
            if path == "/api/session":
                concept = str(body.get("concept", "A")).upper()
                if concept not in ("A", "B"):
                    return self._error(HTTPStatus.BAD_REQUEST, "Live sessions are available for concepts A and B.")
                rec = STATE.library.get(str(body.get("recording_id", "")))
                start = body.get("start_sec")
                with STATE.lock:
                    if len(STATE.sessions) >= MAX_SESSIONS:
                        oldest = min(STATE.sessions, key=lambda s: STATE.sessions[s].last_used)
                        del STATE.sessions[oldest]
                    sid = uuid.uuid4().hex
                    STATE.sessions[sid] = ReplaySession(rec, STATE.model, concept,
                                                        start_sec=None if start is None else float(start))
                return self._json({"session_id": sid, "concept": concept, "recording": rec.summary()})
            m = re.fullmatch(r"/api/session/([0-9a-f]{32})/(step|event)", path)
            if m:
                session = STATE.sessions.get(m.group(1))
                if session is None:
                    return self._error(HTTPStatus.NOT_FOUND, "Session expired - start playback again.")
                if m.group(2) == "event":
                    kind = str(body.get("type", "intervention"))
                    if kind not in ("vasopressor", "fluid", "intervention"):
                        return self._error(HTTPStatus.BAD_REQUEST, "Unknown intervention type.")
                    return self._json({"record": session.add_manual_event(kind)})
                n = max(1, min(int(body.get("n", 1)), MAX_STEPS_PER_CALL))
                ticks = []
                for i in range(n):
                    tick = session.step(include_waveforms=(i == n - 1))
                    if tick is None:
                        break
                    ticks.append(tick)
                if ticks and "waveforms" not in ticks[-1]:
                    ticks[-1]["waveforms"] = None
                return self._json({"ticks": ticks, "finished": session.finished})
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        except (ValueError, KeyError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def do_DELETE(self):
        m = re.fullmatch(r"/api/session/([0-9a-f]{32})", urlparse(self.path).path)
        if not m:
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")
        with STATE.lock:
            STATE.sessions.pop(m.group(1), None)
        return self._json({"ok": True})


def _clean(obj):
    """Replace NaN/inf with None so the browser always receives valid JSON."""
    if isinstance(obj, float):
        return obj if obj == obj and obj not in (float("inf"), float("-inf")) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _json_default(obj):
    try:
        import numpy as np
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj)


def main():
    global STATE
    ap = argparse.ArgumentParser(description="Hemodynamic Concepts App")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default localhost only)")
    ap.add_argument("--port", type=int, default=8765)  # 8000 is taken by a Docker service on the lab host
    ap.add_argument("--device", default="auto", help="auto, cpu or cuda")
    args = ap.parse_args()
    os.chdir(APP_ROOT)
    print("Loading model and demo recordings ...", flush=True)
    STATE = AppState(args.device)
    info = STATE.model.info()
    print(f"Model {info['model_version']} on {info['device']} (sha256 {info['sha256'][:12]}...)", flush=True)
    print(f"Calibration: {info['calibration_source']}", flush=True)
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno == 98:  # EADDRINUSE
            raise SystemExit(f"Port {args.port} is already in use (another program, or the app is already running). "
                             f"Try: scripts/run_app.sh --port {args.port + 1}")
        raise
    httpd.daemon_threads = True
    print(f"Serving on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
