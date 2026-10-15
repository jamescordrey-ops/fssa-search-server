#!/usr/bin/env python3
"""BUG-11 replay battery (v23.2, permanent regression).

Replays the SHAPE of the three real v23.1 failure asks
(recorded in tools/fixtures/bug11_records.json, extracted
read-only from the PC's ask_records.jsonl on 2026-10-15)
through the FIXED v23.2 pipeline with stub providers:
no network, no paid calls, $0.

  CSRA  0eb4d5dd94d4 — live: 416.875s, partial fragment,
        budget_cut FALSE. Shape: ladder drafts + lead
        repairs in unit 1 -> IDK -> rescue -> 5 more
        drafts (11 drafting calls in all).
  Pest  95f121e97b4b — live: 349.063s, partial,
        budget_cut at checking. Shape: 3 slow IDK drafts
        eat the 150s simple budget; the rescue unit then
        drafted 5 more times under a re-armed clock.
  ABLE  87f5d6c5db39 — live: 306.141s, partial, judge
        never served and the once-per-ask V3.1 backup was
        never attempted (the round started spent). Shape:
        a fully-passing draft, Lite down at checking.

Faithfulness: recorded per-call latencies are replayed by
shifting the ask clock's anchors backward (a simulated
clock), so the fixed pipeline's deadline arithmetic sees
the same elapsed-time shape the live asks produced. The
rescue step is driven through the same gates do_POST uses
(_call_may_start on the class deadline + the shared
draft-call counter), because the full do_POST path needs
the retrieval stack; _run_unit itself is the real code.

Assertions per case (the plan's Phase-2 gate):
  (a) simulated wall <= the class cap (150 simple /
      285 hard) plus a small processing allowance, and NO
      provider call starts after the class deadline;
  (b) a binding budget constraint engages and is recorded
      (budget_cut + cut_phase, or the draft cap with the
      rescue skip recorded — whichever the shape hits);
  (c) total drafting calls <= 4 (the shared per-ask cap);
  (d) the shipped text is the draft that passed checks
      (verbatim / supported core), never the live
      failure shape (a 1-sentence fragment, or hours of
      drafting behind a partial).

Run: python3 tools/replay_bug11.py
Exit 0 only when every assertion passes.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FX = json.load(open(os.path.join(
    HERE, "fixtures", "bug11_records.json"), encoding="utf-8"))
FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def load_server():
    tmp = tempfile.mkdtemp(prefix="fssa-replay-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json"),
            ("growth_terms.json", "growth_terms.json")):
        src = os.path.join(ROOT, target)
        if os.path.exists(src):
            os.symlink(src, os.path.join(tmp, link))
    cwd, argv = os.getcwd(), sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(ROOT, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        sys.argv = [argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
        os.chdir(cwd)
    return mod


mod = load_server()
CALL_STARTS = []          # simulated elapsed at each call start
GEN_CALLS = {"n": 0}


def advance(seconds):
    """Move the simulated clock: every anchor shifts back,
    so elapsed time (now - ask_t0) grows by `seconds` and
    every deadline shrinks by the same amount."""
    c = mod._ask_ctx()
    if c.ask_t0:
        c.ask_t0 -= seconds
    if c.ask_deadline:
        c.ask_deadline -= seconds
    if c.class_deadline:
        c.class_deadline -= seconds


def elapsed():
    c = mod._ask_ctx()
    return time.monotonic() - c.ask_t0


def unit_ctx(rec, hits, hard, mechanisms, allowed_extra):
    c = mod._ask_ctx()
    return {
        "question": rec["question"], "unit_q": rec["question"],
        "hits": hits, "engine": "local",
        "draft_model": mod.LOCAL_MODEL, "instructions": "",
        "mechanisms": mechanisms,
        "allowed_extra": allowed_extra, "csra_comp": None,
        "hard": hard, "corp": None,
        "deadline": c.ask_deadline,
        "class_deadline": c.class_deadline,
        "registry_entries": [], "reg_in_instructions": True,
        "local_mode": False, "fresh": True,
        "explicit_model": False, "full_question": None}


def rescue_gate():
    """The do_POST post-IDK rescue gates, verbatim logic:
    returns None when the rescue may run, else the skip
    record do_POST stores on the unit."""
    c = mod._ask_ctx()
    if not mod._call_may_start(c.class_deadline):
        mod._note_budget_cut("rescue")
        return {"skipped": "the class budget is spent; "
                           "the rescue re-run would start "
                           "past the deadline"}
    if c.draft_calls >= mod.DRAFT_CALL_CAP:
        return {"skipped": "draft-call cap reached in "
                           "unit 1"}
    return None


# ================================================================
# CASE 1 — CSRA 0eb4d5dd94d4 (hard, class cap 285s)
# Live shape: unit 1 = 3 rungs + 3 lead repairs (the drafts
# pass their check rounds but carry no mechanism lead
# sentence), then IDK; rescue unit = 5 more drafts. Recorded
# drafting total 317.702s over 11 calls (~28.88s/call);
# checking+extracting ~89.6s (~6s per judge call).
REC = FX["0eb4d5dd94d4"]
# Unit 1's drafts (per the live shape) pass their check
# rounds but ship nothing: no citation markers (the ship
# test's uncited gate) and no mechanism lead sentence, so
# every rung ALSO burns a lead-repair call — 3 rungs + 3
# repairs = the 6 pre-rescue seat events in the record.
D1 = ("Your wife keeps a protected share of the couple's "
      "savings when you enter a nursing home.\n"
      "That share is set by the spousal impoverishment "
      "rules for 2026.\n"
      "It is based on the couple's combined countable "
      "assets on the snapshot date.\n"
      "The share is at least $32,532 and at most $162,660.")
D1_CLAIMS = list(D1.split("\n"))
D1_EXTRACT = json.dumps({"claims": [
    {"text": t, "load_bearing": True, "verdict": "supported",
     "note": "", "sources": [1]} for t in D1_CLAIMS]})

mod._events_reset()
CALL_STARTS.clear()
GEN_CALLS["n"] = 0
mod._ask_clock_begin(time.monotonic())
mod._ask_clock_class(True)
advance(REC["phase_ms"]["retrieving"] / 1000.0)


def csra_generate(*a, **k):
    CALL_STARTS.append(elapsed())
    GEN_CALLS["n"] += 1
    advance(317.702 / 11.0)
    return (D1, "prompt", "grok-4.7 (xAI API)", False, False)


def csra_gemini(*a, **k):
    CALL_STARTS.append(elapsed())
    advance(6.0)
    return D1_EXTRACT


_saved = (mod._generate, mod._gemini_text, mod._det_claim_check)
mod._generate, mod._gemini_text = csra_generate, csra_gemini
mod._det_claim_check = lambda *a, **k: ([], True)
try:
    u1 = mod._run_unit(unit_ctx(
        REC, REC["unit"]["hits"], True,
        mod._mechanism_matches(REC["question"]),
        [32532.0, 162660.0]))
    skip = rescue_gate()
    wall = elapsed()
    starts_after = [s for s in CALL_STARTS if s > 285.0]
finally:
    mod._generate, mod._gemini_text, mod._det_claim_check = _saved
c = mod._ask_ctx()
print(f"CSRA replay: wall={wall:.1f}s (live 416.9s), "
      f"draft_calls={c.draft_calls}, badge={u1['badge']}, "
      f"draft_capped={c.draft_capped}, "
      f"rescue={skip}")
check("CSRA (a) wall within the 285s hard cap",
      wall <= 285.0 + 20.0 and not starts_after,
      f"wall={wall:.1f} late_starts={len(starts_after)}")
check("CSRA (b) draft cap engaged + recorded, rescue "
      "skipped on the cap",
      c.draft_capped is True and u1.get("draft_capped")
      is True and skip is not None
      and "cap" in (skip.get("skipped") or ""),
      f"capped={c.draft_capped} skip={skip}")
check("CSRA (c) drafting calls <= 4 (live: 11)",
      GEN_CALLS["n"] <= 4 and c.draft_calls <= 4,
      f"calls={GEN_CALLS['n']}")
_ans = u1.get("answer") or ""
check("CSRA (d) best judged draft ships whole, no fragment",
      "$32,532" in _ans and "$162,660" in _ans
      and len(_ans) >= 0.8 * len(D1),
      f"badge={u1['badge']} answer_len={len(_ans)} "
      f"draft_len={len(D1)}")

# ================================================================
# CASE 2 — Pest 95f121e97b4b (simple, class cap 150s)
# Live shape: unit 1 = 3 slow drafts ending IDK (drafting
# 305.907s across the ask's ~7 drafting events, ~43.7s per
# call), rescue under a re-armed clock drafted on to 349s.
REC = FX["95f121e97b4b"]
mod._events_reset()
CALL_STARTS.clear()
GEN_CALLS["n"] = 0
mod._ask_clock_begin(time.monotonic())
mod._ask_clock_class(False)
advance(REC["phase_ms"]["retrieving"] / 1000.0)


PEST_DRAFT = ("The Health and Wellness Waiver covers pest "
              "control services for members [1].")
PEST_EXTRACT = json.dumps({"claims": [
    {"text": "The Health and Wellness Waiver covers pest "
             "control services for members.",
     "load_bearing": True, "verdict": "contradicted",
     "note": "", "sources": [1]}]})


def pest_generate(*a, **k):
    CALL_STARTS.append(elapsed())
    GEN_CALLS["n"] += 1
    advance(305.907 / 7.0)
    return (PEST_DRAFT, "prompt", "grok-4.7 (xAI API)",
            False, False)


def pest_gemini(*a, **k):
    CALL_STARTS.append(elapsed())
    advance(4.0)
    return PEST_EXTRACT


_saved = (mod._generate, mod._gemini_text)
mod._generate = pest_generate
mod._gemini_text = pest_gemini
try:
    u1 = mod._run_unit(unit_ctx(
        REC, REC["unit"]["hits"], False, [], []))
    skip = rescue_gate()
    u2 = None
    if skip is None:
        advance(6.0)  # rescue re-retrieval
        u2 = mod._run_unit(unit_ctx(
            REC, REC["unit"]["hits"], False, [], []))
    wall = elapsed()
    starts_after = [s for s in CALL_STARTS if s > 150.0]
finally:
    mod._generate, mod._gemini_text = _saved
c = mod._ask_ctx()
print(f"Pest replay: wall={wall:.1f}s (live 349.1s), "
      f"draft_calls={c.draft_calls}, "
      f"budget_cut={c.budget_cut}, cut_phase={c.cut_phase}, "
      f"rescue={skip}, unit2={None if u2 is None else u2['badge']}")
check("Pest (a) wall within the 150s simple cap",
      wall <= 150.0 + 20.0 and not starts_after,
      f"wall={wall:.1f} late_starts={len(starts_after)}")
check("Pest (b) budget_cut engaged + recorded",
      c.budget_cut is True and c.cut_phase is not None,
      f"budget_cut={c.budget_cut} cut={c.cut_phase}")
check("Pest (c) drafting calls <= 4 (live: ~7+)",
      GEN_CALLS["n"] <= 4 and c.draft_calls <= 4,
      f"calls={GEN_CALLS['n']}")
check("Pest (d) no fragment ships: honest IDK at the cap",
      (u2 is None or u2.get("badge") == "idk")
      and u1.get("badge") == "idk",
      f"u1={u1.get('badge')} u2="
      f"{None if u2 is None else u2.get('badge')}")

# ================================================================
# CASE 3 — ABLE 87f5d6c5db39 (simple, class cap 150s)
# Live shape: the recorded draft is correct and complete
# (figures 3/3 clean) but at checking, Lite never served
# and the V3.1 backup was never attempted (the round
# started spent). Replay: Lite down, backup attempted
# exactly once, backup also down -> deterministic finals
# (stubbed to the live outcome: figures clean, claims
# cited) -> the draft that passed checks ships checked_det.
REC = FX["87f5d6c5db39"]
DRAFT = REC["unit"]["draft_text"]
mod._events_reset()
CALL_STARTS.clear()
GEN_CALLS["n"] = 0
BACKUP_CALLS = {"n": 0}
mod._ask_clock_begin(time.monotonic())
mod._ask_clock_class(False)
advance(REC["phase_ms"]["retrieving"] / 1000.0)


def able_generate(*a, **k):
    CALL_STARTS.append(elapsed())
    GEN_CALLS["n"] += 1
    advance(226.109 / 6.0)
    return (DRAFT, "prompt", "grok-4.7 (xAI API)",
            False, False)


def able_gemini(*a, **k):
    raise RuntimeError("Lite unavailable (stub, the live "
                       "failure mode)")


def able_or(model, system, user, **kw):
    if model == mod.JUDGE_BACKUP_MODEL:
        BACKUP_CALLS["n"] += 1
        raise RuntimeError("backup unavailable (stub: "
                           "neither judge serves)")
    raise AssertionError("unexpected OpenRouter call")


_saved = (mod._generate, mod._gemini_text, mod._or_text,
          mod._det_claim_check, mod._check_round)
mod._generate, mod._gemini_text = able_generate, able_gemini
mod._or_text = able_or
mod._det_claim_check = lambda *a, **k: ([], True)
ROUND_ERRORS = []
_real_round = mod._check_round


def _collecting_round(*a, **k):
    _r = _real_round(*a, **k)
    ROUND_ERRORS.extend(_r.get("errors") or [])
    return _r


mod._check_round = _collecting_round
try:
    u1 = mod._run_unit(unit_ctx(
        REC, REC["unit"]["hits"], False, [], []))
    wall = elapsed()
    starts_after = [s for s in CALL_STARTS if s > 150.0]
finally:
    (mod._generate, mod._gemini_text, mod._or_text,
     mod._det_claim_check, mod._check_round) = _saved
c = mod._ask_ctx()
_jr = (u1.get("judging") or {})
_errors = " | ".join(ROUND_ERRORS)
_ans = u1.get("answer") or ""
print(f"ABLE replay: wall={wall:.1f}s (live 306.1s), "
      f"draft_calls={c.draft_calls}, badge={u1['badge']}, "
      f"backup_attempts={BACKUP_CALLS['n']}, "
      f"errors={_errors[:160]}")
check("ABLE (a) wall within the 150s simple cap",
      wall <= 150.0 + 20.0 and not starts_after,
      f"wall={wall:.1f}")
check("ABLE (b) backup attempted exactly once + recorded",
      BACKUP_CALLS["n"] == 1
      and c.judge_backup_used is True
      and "backup judge:" in _errors,
      f"attempts={BACKUP_CALLS['n']} "
      f"claimed={c.judge_backup_used}")
check("ABLE (c) drafting calls <= 4 (live: 6)",
      GEN_CALLS["n"] <= 4 and c.draft_calls <= 4,
      f"calls={GEN_CALLS['n']}")
check("ABLE (d) passing draft + no judge ships checked_det "
      "verbatim (not partial)",
      u1.get("badge") == "checked_det"
      and _ans.startswith(DRAFT[:120])
      and "$19,000" in _ans
      and len(_ans) >= 0.95 * len(DRAFT),
      f"badge={u1.get('badge')} answer_len={len(_ans)} "
      f"draft_len={len(DRAFT)}")

print()
if FAILS:
    print(f"summary: FAILURES: {FAILS}")
    sys.exit(1)
print("summary: BUG-11 replay battery passed (3 cases)")
