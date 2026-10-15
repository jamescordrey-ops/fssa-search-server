#!/usr/bin/env python3
"""Paired flip list: legacy vs v2 retrieval over the offline case
sets (tools/retrieval_cases.jsonl + probe-cases-staging/
probe_cases.jsonl at the tree root). REPORTING ONLY, per the v22
plan: pass->fail flips are named individually; nothing blocks on
the result.

Method (identical for both engines): the server module is imported
the harness way; retrieval runs through hybrid_search with the
query rewrite and the LLM rerank disabled (neither is available
offline) and n=8. A case passes on its own file's semantics:
expect_any (any listed substring in a served title) or the
expected title/URL substring in the served top-k. On this VM the
vector channels are off for BOTH arms (no embeddings file exists
here; the legacy arm's own vector channel is likewise empty
offline), so the comparison isolates the text/section/pin
machinery. The PC deploy re-run, with vectors live, is the fuller
comparison.

Usage: python3 tools/flip_list.py   (runs both phases as
subprocesses and writes tools/flip_list.json)
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
CASE_FILES = [HERE / "retrieval_cases.jsonl",
              HERE.parent / "probe-cases-staging" / "probe_cases.jsonl"]
OUT = HERE / "flip_list.json"


def load_cases():
    cases, seen = [], set()
    for path in CASE_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            key = (c["question"], c.get("corpus", "in"))
            if key in seen:
                continue
            seen.add(key)
            c["_source"] = path.name
            cases.append(c)
    return cases


def case_pass(case, hits):
    """The case files' own semantics: expect_any = any listed
    substring in any served title; otherwise the expected title
    substring or expected URL substring in the served top-k."""
    k = case.get("k", 8)
    top = hits[:k]
    titles = [(h.get("title") or "").lower() for h in top]
    urls = [(h.get("url") or "").lower() for h in top]
    if case.get("expect_any"):
        return any(str(w).lower() in t
                   for w in case["expect_any"] for t in titles)
    ok = False
    if case.get("expect_title_contains"):
        ok = ok or any(case["expect_title_contains"].lower() in t
                       for t in titles)
    if case.get("expect_url_contains"):
        ok = ok or any(case["expect_url_contains"].lower() in u
                       for u in urls)
    return ok


def run_phase(phase):
    sys.path.insert(0, str(HERE))
    import retrieval_regression as rr
    mod = rr.load_server(str(HERE.parent))
    mod.REWRITE_ENABLED = False
    mod.RERANK_ENABLED = False
    results = {}
    for case in load_cases():
        corpus = mod.CORPUS_FED if case.get("corpus") == "federal" \
            else mod.CORPUS_IN
        try:
            hits = mod.hybrid_search(case["question"], corpus=corpus,
                                     n=8, use_rewrite=False,
                                     use_rerank=False)
            ok = case_pass(case, hits)
            top = (hits[0].get("title") if hits else "") or ""
        except Exception as e:
            ok, top = False, f"ERROR {type(e).__name__}: {e}"
        results[case["question"]] = {
            "pass": ok, "top1": top[:90],
            "corpus": case.get("corpus", "in"),
            "source": case["_source"]}
    out = HERE / f"flip_phase_{phase}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    log.info("phase %s: %d/%d pass", phase,
             sum(1 for r in results.values() if r["pass"]),
             len(results))
    return 0


def main():
    phase = os.environ.get("FSSA_FLIP_PHASE")
    if phase:
        return run_phase(phase)
    env = dict(os.environ)
    for ph, ret in (("legacy", "legacy"), ("v2", "v2")):
        env["FSSA_FLIP_PHASE"] = ph
        env["RETRIEVAL"] = ret
        r = subprocess.run([sys.executable, str(Path(__file__))],
                           env=env, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        if r.returncode != 0:
            log.error("phase %s failed (exit %d)", ph, r.returncode)
            return 1
    legacy = json.loads((HERE / "flip_phase_legacy.json").read_text())
    v2 = json.loads((HERE / "flip_phase_v2.json").read_text())
    flips_down, flips_up, both_pass, both_fail = [], [], 0, 0
    for q, lr in legacy.items():
        vr = v2.get(q, {"pass": False, "top1": ""})
        if lr["pass"] and not vr["pass"]:
            flips_down.append({"question": q, "corpus": lr["corpus"],
                               "source": lr["source"],
                               "legacy_top1": lr["top1"],
                               "v2_top1": vr["top1"]})
        elif vr["pass"] and not lr["pass"]:
            flips_up.append({"question": q, "corpus": lr["corpus"],
                             "source": lr["source"],
                             "legacy_top1": lr["top1"],
                             "v2_top1": vr["top1"]})
        elif lr["pass"]:
            both_pass += 1
        else:
            both_fail += 1
    report = {
        "method": ("hybrid_search, rewrite+rerank off, n=8; "
                   "case files' own expectation semantics; "
                   "vector channels empty on the VM for both arms"),
        "cases": len(legacy),
        "legacy_pass": sum(1 for r in legacy.values() if r["pass"]),
        "v2_pass": sum(1 for r in v2.values() if r["pass"]),
        "pass_to_pass": both_pass, "fail_to_fail": both_fail,
        "fail_to_pass": flips_up, "pass_to_fail": flips_down}
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    log.info("flip list: legacy %d/%d, v2 %d/%d, pass->fail %d, "
             "fail->pass %d (full detail in %s)",
             report["legacy_pass"], report["cases"],
             report["v2_pass"], report["cases"],
             len(flips_down), len(flips_up), OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
