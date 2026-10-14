#!/usr/bin/env python3
"""Judge-only replay harness (v18, §E).

Re-judges a PERSISTED draft (from ask_records.jsonl, written by
the server's §E ask-record persistence) against the current —
or a candidate — seat/prompt configuration, WITHOUT re-running
retrieval or drafting, and diffs the fresh verdicts against the
verdicts recorded at ask time.

Purpose: when a judge seat or a judge prompt changes, the
question "would the new panel have ruled the same on the asks
that actually happened?" is answered on real drafts for the
price of judging alone, instead of a full battery re-run.

LIVE USE SPENDS MONEY (the judge seats are hosted APIs). This
tool is POST-GATE: run it only under the same authorization as
any other live ask. Its plumbing is offline-tested in
tests/test_replay.py with mocked judges.

Usage:
    python3 replay_harness.py [--log ask_records.jsonl]
        [--index N | --last] [--seat2 MODEL_SLUG]
        [--server PATH_TO_fssa_search_server.py]
"""
import argparse
import importlib.util
import json
import os
import sys


def load_records(path):
    """All ask records (kind == 'ask') from a §E ask log, in
    file order. Job records and malformed lines are skipped."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("kind") == "ask":
                out.append(rec)
    return out


def load_server(path):
    """Import the server module from a file path (its __main__
    block does not run on import)."""
    spec = importlib.util.spec_from_file_location(
        "fssa_server_replay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply_seat2_override(module, slug):
    """Point judge seat 2 at a candidate model slug: the named
    constant, the seat's lead candidate, and its route entry.
    The seat's other candidates (failover, NIM) are kept."""
    module.JUDGE_SEAT2_MODEL = slug
    cands = list(module.SEATS["judge_nim2"]["candidates"])
    if cands:
        cands[0] = (slug, cands[0][1])
        module.SEATS["judge_nim2"]["candidates"] = cands
    module._OR_ROUTES.setdefault(
        slug, {"allow_fallbacks": True, "data_collection": "deny"})
    module._OR_PRICES.setdefault(
        slug, module._OR_PRICES.get(
            "qwen/qwen3-235b-a22b-instruct-2507", (0.30, 1.20)))


def replay_record(module, record, seat2_model=None):
    """Re-judge every persisted unit draft in one ask record.
    Returns a list of per-unit dicts: {part, question, claims:
    [{text, recorded_final, replay_final, agree, recorded_models,
    replay_coverage}]}. Judging runs with the verdict cache
    bypassed (use_cache=False): a replay must measure the panel
    in force, not the cache."""
    if seat2_model:
        apply_seat2_override(module, seat2_model)
    out = []
    for unit in record.get("units") or ():
        draft = unit.get("draft_text") or ""
        if not draft:
            continue
        question = unit.get("unit_q") or record.get("question") or ""
        hits = [{"title": h.get("title") or "",
                 "url": h.get("url") or "",
                 "excerpt": h.get("excerpt") or ""}
                for h in unit.get("hits") or ()]
        jr = module._judge_round(
            question, hits, draft, bool(record.get("hard")),
            "", corp=None, mechanisms=[], extra_claims=[],
            local_only=bool(record.get("local")), use_cache=False)
        recorded = {}
        for c in unit.get("claims") or ():
            recorded[module._norm_claim_text(c.get("text"))] = c
        claims = []
        for c in jr.get("claims", ()):
            if c.get("meta"):
                continue
            rec_c = recorded.get(module._norm_claim_text(c["text"]))
            claims.append({
                "text": c["text"],
                "recorded_final": (rec_c or {}).get("final"),
                "replay_final": c.get("final"),
                "agree": bool(rec_c)
                    and rec_c.get("final") == c.get("final"),
                "recorded_models": (rec_c or {}).get("models"),
                "replay_coverage": c.get("coverage"),
                "replay_models": c.get("models"),
            })
        out.append({"part": unit.get("part"), "question": question,
                    "mode": jr.get("mode"), "claims": claims})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default="ask_records.jsonl")
    ap.add_argument("--index", type=int, default=None,
                    help="ask-record index (0-based, asks only)")
    ap.add_argument("--last", action="store_true",
                    help="replay the most recent ask record")
    ap.add_argument("--seat2", default=None,
                    help="candidate judge seat-2 model slug")
    ap.add_argument("--server", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "fssa_search_server.py"))
    args = ap.parse_args(argv)
    records = load_records(args.log)
    if not records:
        print(f"no ask records in {args.log}", file=sys.stderr)
        return 2
    idx = len(records) - 1 if (args.last or args.index is None) \
        else args.index
    record = records[idx]
    module = load_server(args.server)
    results = replay_record(module, record, seat2_model=args.seat2)
    n_agree = n_total = 0
    for unit in results:
        print(f"unit part={unit['part']!r} mode={unit['mode']} "
              f"q={unit['question'][:70]!r}")
        for c in unit["claims"]:
            n_total += 1
            n_agree += 1 if c["agree"] else 0
            mark = "AGREE" if c["agree"] else "DIFF "
            print(f"  [{mark}] recorded={c['recorded_final']} "
                  f"replay={c['replay_final']} :: {c['text'][:90]}")
    print(f"agreement: {n_agree}/{n_total} claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
