#!/usr/bin/env python3
"""FSSA v15 growth loop (plan item 7) — VM-side tooling.

Reads the server's gap log (a gap_log.jsonl file, or the JSON from
GET /gaps via --gaps-url), clusters the gaps by gate and missing
text, optionally folds in the operator's graded answers (--grades, a
JSONL with {question, grade, notes} lines), and drafts up to
--max (default 5) NEW registry candidates for the monthly review:
mechanism entries in the server's exact registry shape, or
rescrape watchlist topics when no mechanism fits.

NOTHING here ships: candidates land in review_queue.json with
status "pending-approval" and a digest is printed. Adding an
entry to the server's registries stays a human decision.

Drafting: with a NIM key available (NVIDIA_API_KEY env or
--key-file), candidates are drafted by the NIM Nemotron seat and
schema-validated. Without a key the tool runs in COLLECT MODE:
candidates are labeled skeletons carrying the gap evidence, for
the operator or a later keyed run to complete. (This VM holds no NIM
key by design; keys live on the PC and in the Secure Vault.)

Cadence (standing decision, 2026-10-13): monthly, aligned with
the corpus rescrape; at most 5 candidates per run.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# The server mechanism registry's shape (mirrors MECHANISMS in
# fssa_search_server.py): a candidate is shippable only if it
# validates against this.
MECH_FIELDS = {"id": str, "title": str, "groups": list,
               "required": list, "forbidden": (list, tuple),
               "prompt": str, "sufficiency_terms": (list, tuple),
               "retry_query": str, "missing": str}


def validate_mechanism(entry):
    """Schema check mirroring the server's registry fields."""
    errs = []
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    for field, types in MECH_FIELDS.items():
        if field not in entry:
            errs.append(f"missing field: {field}")
        elif not isinstance(entry[field], types):
            errs.append(f"field {field} has the wrong type")
    if errs:
        return errs
    if not re.fullmatch(r"[a-z0-9_]+", entry["id"]):
        errs.append("id must be snake_case")
    for g in entry["groups"]:
        if not isinstance(g, dict) or not (
                set(g) <= {"tokens", "phrases"}
                and ("tokens" in g or "phrases" in g)):
            errs.append("each group needs tokens and/or phrases")
            break
    for r in entry["required"]:
        if not isinstance(r, dict) or "label" not in r or not (
                "any" in r or "computed" in r):
            errs.append("each required element needs a label and "
                        "an 'any' list or 'computed' marker")
            break
    return errs


def _norm(text):
    return re.sub(r"[^a-z0-9 ]+", " ",
                  (text or "").lower()).split()


def cluster_gaps(gaps):
    """Group gap records by (gate, signature of their first
    missing item). Returns clusters sorted by frequency."""
    clusters = {}
    for g in gaps:
        missing = g.get("missing") or []
        first = missing[0] if missing else ""
        sig_words = [w for w in _norm(first) if len(w) > 3][:6]
        key = (g.get("gate") or "judging", " ".join(sig_words))
        c = clusters.setdefault(key, {
            "gate": key[0], "signature": key[1], "count": 0,
            "questions": [], "missing": [], "badges": {}})
        c["count"] += 1
        q = g.get("question") or ""
        if q and q not in c["questions"]:
            c["questions"].append(q)
        for m in missing:
            if m not in c["missing"]:
                c["missing"].append(m)
        b = g.get("badge") or "idk"
        c["badges"][b] = c["badges"].get(b, 0) + 1
    return sorted(clusters.values(), key=lambda c: -c["count"])


def skeleton_candidate(cluster, known_ids):
    """Collect-mode candidate: an honest skeleton carrying the
    cluster's evidence, with the drafting left marked as TODO."""
    words = [w for w in cluster["signature"].split() if w]
    slug = "_".join(words[:4]) or "gap"
    base = slug
    n = 2
    while slug in known_ids:
        slug = f"{base}_{n}"
        n += 1
    return {
        "type": "mechanism",
        "status": "pending-approval",
        "drafted_by": "collect-mode (no NIM key on this machine)",
        "source_gate": cluster["gate"],
        "gap_count": cluster["count"],
        "evidence_questions": cluster["questions"][:5],
        "evidence_missing": cluster["missing"][:5],
        "entry": {
            "id": slug,
            "title": "TODO: name the governing mechanism",
            "groups": [],
            "required": [],
            "forbidden": [],
            "prompt": "TODO: the governing rule, stated plainly, "
                      "from the source excerpts in the evidence.",
            "sufficiency_terms": [],
            "retry_query": " ".join(cluster["questions"][:1]),
            "missing": cluster["missing"][0]
            if cluster["missing"] else "",
        },
        "validation_errors": ["skeleton: fields marked TODO are "
                              "incomplete by design"],
    }


def nim_draft(cluster, key):
    """Draft one mechanism entry with the NIM seat. Returns the
    parsed entry dict, or None when the seat cannot run or the
    reply is not usable JSON."""
    prompt = (
        "You draft entries for a Medicaid Q&A server's mechanism "
        "registry. A mechanism entry forces future answers to "
        "establish a governing rule. Given this cluster of "
        "logged answer gaps (questions the server could not "
        "verify, and what was missing), draft ONE mechanism "
        "entry as JSON with EXACTLY these fields: id "
        "(snake_case), title, groups (list of {tokens: [words], "
        "phrases: [strings]}; a question matches when EVERY "
        "group hits), required (list of {label, any: [accepted "
        "phrasings]}), forbidden (list of wrong substitute "
        "phrasings), prompt (the governing rule stated plainly), "
        "sufficiency_terms (list), retry_query (a search query "
        "that finds the governing passage), missing (what to "
        "report when the material is absent). Only use facts "
        "present in the gap evidence; do not invent figures. "
        "Reply with ONLY the JSON object.\n\nGAP CLUSTER:\n"
        + json.dumps(cluster, indent=1)[:6000])
    payload = json.dumps({
        "model": NIM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2, "max_tokens": 2048}).encode()
    req = urllib.request.Request(
        NIM_URL, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gaps", help="path to gap_log.jsonl")
    ap.add_argument("--gaps-url", help="URL of GET /gaps JSON")
    ap.add_argument("--grades", help="optional grades JSONL")
    ap.add_argument("--registry-ids", default="",
                    help="comma-separated existing mechanism ids")
    ap.add_argument("--key-file", help="file holding a NIM key")
    ap.add_argument("--max", type=int, default=5,
                    help="max candidates per run (default 5)")
    ap.add_argument("--out", default=os.path.dirname(
        os.path.abspath(__file__)),
        help="output directory (default: this script's dir)")
    args = ap.parse_args()

    gaps = []
    if args.gaps:
        with open(args.gaps, encoding="utf-8") as f:
            gaps = [json.loads(line) for line in f if line.strip()]
    elif args.gaps_url:
        with urllib.request.urlopen(args.gaps_url,
                                    timeout=30) as resp:
            gaps = json.loads(resp.read()).get("gaps", [])
    else:
        ap.error("one of --gaps or --gaps-url is required")
    grades = []
    if args.grades and os.path.exists(args.grades):
        with open(args.grades, encoding="utf-8") as f:
            grades = [json.loads(line) for line in f if line.strip()]
    known_ids = {s.strip() for s in args.registry_ids.split(",")
                 if s.strip()}

    key = os.environ.get("NVIDIA_API_KEY") or None
    if not key and args.key_file and os.path.exists(args.key_file):
        with open(args.key_file, encoding="utf-8") as f:
            key = f.read().strip() or None

    clusters = cluster_gaps(gaps)
    # Graded failures join the candidate pool at top priority:
    # a human-graded miss is the strongest growth signal there is.
    for gr in grades:
        if str(gr.get("grade", "")).lower() in (
                "fail", "failed", "partial", "idk"):
            clusters.insert(0, {
                "gate": "graded", "signature": "graded answer",
                "count": 1, "questions": [gr.get("question", "")],
                "missing": [gr.get("notes", "")], "badges": {}})

    queue, watchlist = [], []
    for c in clusters:
        if len(queue) >= args.max:
            watchlist.append({
                "type": "watchlist", "status": "pending-approval",
                "gate": c["gate"], "gap_count": c["count"],
                "signature": c["signature"],
                "questions": c["questions"][:3]})
            continue
        cand = None
        if key:
            entry = nim_draft(c, key)
            if entry is not None:
                errs = validate_mechanism(entry)
                cand = {"type": "mechanism",
                        "status": "pending-approval",
                        "drafted_by": NIM_MODEL,
                        "source_gate": c["gate"],
                        "gap_count": c["count"],
                        "evidence_questions": c["questions"][:5],
                        "evidence_missing": c["missing"][:5],
                        "entry": entry, "validation_errors": errs}
        if cand is None:
            cand = skeleton_candidate(c, known_ids)
        known_ids.add(cand["entry"]["id"])
        queue.append(cand)

    out_path = os.path.join(args.out, "review_queue.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_from": {"gaps": len(gaps),
                                      "grades": len(grades)},
                   "candidates": queue, "watchlist": watchlist},
                  f, indent=1)
    print(f"gaps read: {len(gaps)}; clusters: {len(clusters)}; "
          f"candidates: {len(queue)} (cap {args.max}); "
          f"watchlist: {len(watchlist)}")
    print(f"mode: {'NIM-drafted' if key else 'collect (no key)'}")
    for cand in queue:
        print(f"- [{cand['source_gate']}] {cand['entry']['id']}: "
              f"{cand['gap_count']} gap(s), drafted by "
              f"{cand['drafted_by']}, validation: "
              f"{cand['validation_errors'] or 'clean'}")
    print(f"queue written to {out_path} — status pending-approval; "
          "nothing ships without the operator's review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
