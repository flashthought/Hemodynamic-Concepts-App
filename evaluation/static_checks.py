"""
Verification by inspection and analysis (BME Design Ch. 9) for the specifications
that cannot be measured from forecast accuracy:

  SPEC-12  Nielsen heuristics satisfied      <- evaluation/heuristic_evaluation.json
  SPEC-13  Exportable episode report         <- exercises the real report/CSV/JSON code paths
  SPEC-14  No treatment recommendations      <- runs every demo recording through A, B and C
                                                and scans every user-facing sentence

Writes evaluation/results/static_checks.json and fills the SPEC-12/13/14 rows of
spec_verification.csv / verification_summary.json so the concept-selection gate
sees them.
"""

import csv
import json
import os
import re
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
from app.engine.model_service import ModelService  # noqa: E402
from app.engine.recordings import RecordingLibrary  # noqa: E402
from app.engine.review import report_html, run_review, timeline_csv  # noqa: E402
from app.engine.session import ReplaySession  # noqa: E402

RESULTS = os.path.join(APP_ROOT, "evaluation", "results")

# Prescriptive language: imperative treatment verbs, or advice phrasing.
PRESCRIPTIVE = re.compile(
    r"\b(give|administer|start(ing)? (a |the )?(vasopressor|fluid|norepinephrine|bolus)|increase the|decrease the|"
    r"titrate|you should|we recommend|recommended dose|consider (giving|starting|a bolus))\b", re.I)


def spec12():
    he = json.load(open(os.path.join(APP_ROOT, "evaluation", "heuristic_evaluation.json"), encoding="utf-8"))
    live = sum(h["live"]["result"] == "met" for h in he["heuristics"])
    review = sum(h["review"]["result"] == "met" for h in he["heuristics"])
    return {"A": live, "B": live, "C": review,
            "partial": [{"n": h["n"], "name": h["name"], "view": v, "evidence": h[v]["evidence"]}
                        for h in he["heuristics"] for v in ("live", "review") if h[v]["result"] != "met"]}


def scan(texts):
    hits = []
    for src, text in texts:
        for m in PRESCRIPTIVE.finditer(text or ""):
            hits.append({"source": src, "match": m.group(0), "text": text})
    return hits


def main():
    model = ModelService(APP_ROOT)
    lib = RecordingLibrary(os.path.join(APP_ROOT, "demo_data"))
    texts = {"A": [], "B": [], "C": []}
    exports = {}
    for summary in lib.summaries():
        rec = lib.get(summary["id"])
        for concept in ("A", "B"):
            sess = ReplaySession(rec, model, concept, step_sec=2.0)
            for tick in sess.run_to_end(include_waveforms=False):
                texts[concept].append((f"{rec.rec_id}/{concept}/alert", tick["alert"].get("reason", "")))
                texts[concept] += [(f"{rec.rec_id}/{concept}/warning", w) for w in tick.get("warnings", [])]
                for iv in tick.get("interventions", []):
                    texts[concept].append((f"{rec.rec_id}/{concept}/intervention", str(iv.get("status", ""))))
        review = run_review(rec, model, step_sec=2.0)
        texts["C"] += [(f"{rec.rec_id}/C/narrative", line) for line in review["narrative"]]
        html, csv_text = report_html(review, model.info()), timeline_csv(review)
        texts["C"].append((f"{rec.rec_id}/C/report", re.sub(r"<[^>]+>", " ", html)))
        exports[rec.rec_id] = {"report_html_bytes": len(html), "csv_rows": csv_text.count("\n") - 1,
                               "json_keys": sorted(review.keys())}
        print(f"  {rec.rec_id}: A/B/C texts scanned")

    hits = {c: scan(t) for c, t in texts.items()}
    s12 = spec12()
    export_ok = all(e["report_html_bytes"] > 1000 and e["csv_rows"] > 0 for e in exports.values())
    out = {
        "SPEC-12": {"A": s12["A"], "B": s12["B"], "C": s12["C"], "not_fully_met": s12["partial"]},
        "SPEC-13": {"A": False, "B": False, "C": export_ok,
                    "note": "A and B keep an on-screen event log only; C produces a printable HTML report, timeline CSV and full JSON.",
                    "exports_checked": exports},
        "SPEC-14": {c: len(h) for c, h in hits.items()},
        "SPEC-14_sentences_scanned": {c: len(t) for c, t in texts.items()},
        "SPEC-14_hits": {c: h[:20] for c, h in hits.items()},
    }
    with open(os.path.join(RESULTS, "static_checks.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    # Fold into the specification table.
    def check(spec, val):
        if spec["direction"] == "bool":
            return "PASS (ideal)" if val else "FAIL"
        if spec["direction"] == "higher":
            return "PASS (ideal)" if val >= spec["ideal"] else "PASS (marginal)" if val >= spec["marginal"] else "FAIL"
        return "PASS (ideal)" if val <= spec["ideal"] else "PASS (marginal)" if val <= spec["marginal"] else "FAIL"

    summary_path = os.path.join(RESULTS, "verification_summary.json")
    ver = json.load(open(summary_path, encoding="utf-8"))
    for row in ver["spec_verification"]:
        spec = {"direction": {"SPEC-12": "higher", "SPEC-13": "bool", "SPEC-14": "lower"}.get(row["id"]),
                "ideal": row["ideal"], "marginal": row["marginal"]}
        if row["id"] in ("SPEC-12", "SPEC-13", "SPEC-14"):
            for c in ("A", "B", "C"):
                val = out[row["id"]][c]
                row[f"measured_{c}"] = val
                row[f"result_{c}"] = check(spec, val)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(ver, f, indent=2)
    with open(os.path.join(RESULTS, "spec_verification.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(ver["spec_verification"][0].keys()))
        w.writeheader()
        w.writerows(ver["spec_verification"])

    print("SPEC-12 heuristics met:", {c: out["SPEC-12"][c] for c in "ABC"})
    print("SPEC-13 export:", {c: out["SPEC-13"][c] for c in "ABC"})
    print("SPEC-14 prescriptive phrases:", out["SPEC-14"], "of", out["SPEC-14_sentences_scanned"], "sentences")


if __name__ == "__main__":
    main()
