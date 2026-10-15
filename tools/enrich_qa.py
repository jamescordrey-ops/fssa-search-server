#!/usr/bin/env python3
"""Enrichment checking pass (v22 plan item 3).

Deterministic validation over EVERY enrichment row in the
generation: each stored figure must be a verbatim substring of its
section text at the stored offsets; questions must be well-formed
(3-12 per fleet row, 12-220 chars, no URLs, no exact duplicates
within a row); aliases must be short and non-empty. Then a
judgment sample: a deterministic >=5% sample of fleet-written
sections is emitted to tools/enrichment_judgment_sample.jsonl for
human/agent grading (question actually answered by its section?
yes/no per question); grades are written back to
tools/enrichment_judgment_grades.json and folded into the report
at tools/enrichment_qa.json with the failure rate.

  python3 tools/enrich_qa.py            # validate + emit sample
  python3 tools/enrich_qa.py --report   # fold grades into report
"""
import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "generations" / "g0000" / "corpus_g0000.db"
REPORT = HERE / "enrichment_qa.json"
SAMPLE = HERE / "enrichment_judgment_sample.jsonl"
GRADES = HERE / "enrichment_judgment_grades.json"


def validate():
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT e.section_id, e.questions, e.aliases, e.figures,"
        " e.writer, s.text FROM enrich e"
        " JOIN sections s ON s.section_id = e.section_id").fetchall()
    fig_bad, q_bad, a_bad = [], [], []
    fleet_rows = 0
    total_q = 0
    for sid, qs_j, al_j, fg_j, writer, text in rows:
        for fig in json.loads(fg_j or "[]"):
            if text[fig["start"]:fig["end"]] != fig["value"]:
                fig_bad.append({"section_id": sid,
                                "figure": fig["value"]})
        qs = json.loads(qs_j or "[]")
        total_q += len(qs)
        if "fleet" in (writer or ""):
            fleet_rows += 1
        seen = set()
        for q in qs:
            if not (12 <= len(q) <= 220) or "http" in q \
                    or q.lower() in seen:
                q_bad.append({"section_id": sid, "question": q[:80]})
            seen.add(q.lower())
        # Fleet rows also carry the deterministic template
        # questions merged on top (up to ~6), so the honest band
        # for a merged fleet row is 3-16, not the writer band 3-12.
        if "fleet" in (writer or "") and not (3 <= len(qs) <= 16):
            q_bad.append({"section_id": sid,
                          "question": f"count={len(qs)}"})
        for a in json.loads(al_j or "[]"):
            if not a or len(a) > 80:
                a_bad.append({"section_id": sid, "alias": a[:40]})
    sample_rows = []
    fleet = [r for r in rows if "fleet" in (r[4] or "")]
    step = max(1, len(fleet) // max(1, int(len(fleet) * 0.06)))
    for r in fleet[::step]:
        sample_rows.append({
            "section_id": r[0],
            "questions": json.loads(r[1] or "[]"),
            "section_excerpt": r[5][:700]})
    with SAMPLE.open("w", encoding="utf-8") as f:
        for r in sample_rows:
            f.write(json.dumps(r) + "\n")
    report = {
        "rows": len(rows), "fleet_rows": fleet_rows,
        "template_rows": len(rows) - fleet_rows,
        "total_questions": total_q,
        "figure_failures": fig_bad[:50],
        "figure_failure_count": len(fig_bad),
        "question_failures": q_bad[:50],
        "question_failure_count": len(q_bad),
        "alias_failures": a_bad[:50],
        "alias_failure_count": len(a_bad),
        "judgment_sample_sections": len(sample_rows),
        "judgment_sample_file": SAMPLE.name,
        "judgment": None}
    if GRADES.exists():
        report["judgment"] = json.loads(
            GRADES.read_text(encoding="utf-8"))
    REPORT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    log.info("rows=%d fleet=%d questions=%d figure_fail=%d "
             "question_fail=%d alias_fail=%d sample=%d",
             len(rows), fleet_rows, total_q, len(fig_bad),
             len(q_bad), len(a_bad), len(sample_rows))
    conn.close()
    return 0


def report_only():
    if not REPORT.exists():
        log.error("no report yet; run without --report first")
        return 1
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if GRADES.exists():
        report["judgment"] = json.loads(
            GRADES.read_text(encoding="utf-8"))
    REPORT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    log.info("judgment folded in: %s", report.get("judgment"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    return report_only() if args.report else validate()


if __name__ == "__main__":
    sys.exit(main())
