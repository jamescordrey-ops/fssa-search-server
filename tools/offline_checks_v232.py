#!/usr/bin/env python3
"""v23.2 offline checks: the full v23.1 suite (below, unchanged)
plus the BUG-11 section (V232 at the end): ONE ask clock (no
per-invocation re-arming), the shared draft-call cap of 4
(lead repairs count), the strip-only-flagged rule, and the
assembly guard that ships the checked draft verbatim on
material loss. No network, no live models, no PC.

--- original v23.1 header ---
 ABS   the panel/V4 absence greps: the deleted machinery
       (_judge_round, seats, bench, refutation, Flash
       arbitration, verdict cache) is not defined or called,
       no V4 slug appears anywhere in the server source, and
       the retired badge names survive nowhere as code.
 DET   the deterministic checks under the judge: scoped
       figure check with the derivation whitelist (half /
       double / sum / difference of scoped figures), the
       citation check failing an unmapped claim in det mode,
       and the checks annotation naming who served.
 BUD   WS-A budget enforcement (BUG-10):
         - a judge leg with a spent deadline never starts;
         - a Gemini leg's transport timeout clamps to the
           budget remaining (stubbed transport that uses its
           full granted timeout ends the leg at the edge);
         - a unit whose checking provider hangs to its
           granted timeout finalizes at the budget with
           budget_cut + cut_phase recorded;
         - the job-thread backstop abandons an over-cap job
           and the abandoned child cannot overwrite the
           record.
 ASK   BUG-1 + BUG-5(a): a FULL stub ask through _run_job /
       do_POST (retrieval, drafting, judging all stubbed at
       their seams): the ask record's phase_ms contains
       every phase INCLUDING "retrieving" (the BUG-1 fix)
       and the phase times sum to >= 99% of the ask's wall
       time (the phase-completeness acceptance).
 PAGE  the stepper + roster surface: STEP_DEFS carry the
       checking step and no seat widgets; the badge map
       carries checked / checked_det; /health's roster
       block names Grok drafting, Lite judging, V3.1 backup.

Run: python3 tools/offline_checks_v232.py
Exit 0 only when every check passes.
"""
import importlib.util
import inspect
import io
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def load_server():
    tmp = tempfile.mkdtemp(prefix="fssa-v231-checks-")
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
with open(os.path.join(ROOT, "fssa_search_server.py"),
          encoding="utf-8") as _f:
    SRC = _f.read()

QUESTION = ("If I go into a nursing home, how much of our "
            "savings can my wife keep?")
HITS = [{"title": "ILTCP: Spousal Impoverishment",
         "url": "https://www.in.gov/medicaid/files/ilTCP.pdf",
         "excerpt": "The community spouse may keep one half "
                    "of the couple's countable assets, not "
                    "less than $32,532 and not more than "
                    "$162,660. The institutionalized spouse "
                    "retains $2,000."}]
DRAFT = (
    "- Minimum protection: Your wife is guaranteed to keep "
    "at least $32,532 of the couple's countable (non-exempt) "
    "assets, even if half of your combined assets would be "
    "lower than that amount [1].\n"
    "- Maximum share: She may keep up to half (1/2) of the "
    "couple's countable assets, but that share cannot exceed "
    "$162,660 [1].\n")
CLAIMS = [
    "Your wife is guaranteed to keep at least $32,532 of the "
    "couple's countable assets.",
    "She may keep up to half (1/2) of the couple's countable "
    "assets, but that share cannot exceed $162,660.",
]
EXTRACT_JSON = json.dumps({"claims": [
    {"text": t, "load_bearing": True, "verdict": "supported",
     "note": "", "sources": [1]} for t in CLAIMS]})
VERDICTS_JSON = json.dumps({
    "verdicts": [{"i": i + 1, "verdict": "supported",
                  "note": ""} for i in range(14)],
    "missed": []})

# ---------------------------------------------------------------
# ABS: absence greps over the server source.
DELETED_DEFS = [
    "_judge_round", "_panel_state", "_panel_info", "_seat_pick",
    "_plan_panel", "_seat_health_block", "_judge_seat_text",
    "_seat2_judge_text", "_note_seat_skips", "_flash_arbitrate",
    "_refutation_retrieve", "_refutation_queries",
    "_hunt_evidence", "_ask_judge_benched", "_seat_report",
    "_seat_exclusions", "_seat_median", "_verdict_cache_get",
    "_verdict_cache_put", "_registry_block_hash",
    "_panel_annotation", "_slot_provider_name", "_slot_has_key",
    "_candidate_layer", "_set_seat_phase", "_seat_state",
    "_worse_verdict", "_majority_final",
]
for _fn in DELETED_DEFS:
    check(f"ABS def {_fn} is gone",
          f"def {_fn}(" not in SRC
          and not hasattr(mod, _fn))
for _const in ("OR_RELIEF_MODEL", "SEATS", "SEAT_BENCH_COOLDOWN_S",
               "FLASH_MODEL", "GROQ_QWEN_MODEL"):
    check(f"ABS const {_const} is gone",
          not hasattr(mod, _const))
check("ABS no V4 slug anywhere",
      "deepseek-v4" not in SRC)
for _badge in ('"verified_single"', '"verified_reduced"',
               '"partial_single"', '"partial_reduced"',
               '== "verified"'):
    check(f"ABS retired badge literal {_badge} gone from code",
          _badge not in SRC)
check("ABS new badge literals present",
      '"checked"' in SRC and '"checked_det"' in SRC
      and '"partial"' in SRC)
check("ABS backup slug is the only DeepSeek slug",
      mod.JUDGE_BACKUP_MODEL == "deepseek/deepseek-chat-v3.1")

# ---------------------------------------------------------------
# DET: the deterministic checks.
_c1 = {"text": "She may keep $81,330 of the assets.",
       "sources": [0], "meta": False}
_bad, _cited = mod._det_claim_check(_c1, HITS, QUESTION, "", [])
check("DET derivation whitelist: half of a scoped figure "
      "passes", _bad == [] and _cited, f"bad={_bad}")
_c2 = {"text": "The couple keeps $195,192 in total.",
       "sources": [0], "meta": False}
_bad, _ = mod._det_claim_check(_c2, HITS, QUESTION, "", [])
check("DET derivation whitelist: sum of two scoped figures "
      "passes", _bad == [], f"bad={_bad}")
_c3 = {"text": "She may keep $50,000 of the assets.",
       "sources": [0], "meta": False}
_bad, _ = mod._det_claim_check(_c3, HITS, QUESTION, "", [])
check("DET unscoped figure fails", _bad == [50000.0],
      f"bad={_bad}")
_c4 = {"text": "The allowance is $32,532 [registry].",
       "sources": [], "meta": False}
_bad, _cited = mod._det_claim_check(
    _c4, HITS, QUESTION, "", [32532.0])
check("DET registry-admitted figure passes + registry "
      "citation counts", _bad == [] and _cited, f"bad={_bad}")

# DET in det mode: an unmapped, uncited extra claim fails
# the citation check; a cited one passes.
mod._events_reset()


def _down(*a, **k):
    raise RuntimeError("stubbed down")


_saved_gt, _saved_ot = mod._gemini_text, mod._or_text
mod._gemini_text = _down
mod._or_text = _down
try:
    jr = mod._check_round(
        QUESTION, HITS, DRAFT, True, "", corp=None,
        mechanisms=None,
        extra_claims=[
            {"text": "The form is filed with the county "
                     "office.", "load_bearing": True,
             "sources": [], "origin": "extra"},
            {"text": CLAIMS[0], "load_bearing": True,
             "sources": [0], "origin": "extra"}],
        local_only=False, use_cache=False, deadline=None)
finally:
    mod._gemini_text, mod._or_text = _saved_gt, _saved_ot
check("DET det-mode round runs on extras",
      jr["mode"] == "det", jr["mode"])
_unc = [c for c in jr["claims"] if "county office" in c["text"]]
_cit = [c for c in jr["claims"] if "32,532" in c["text"]]
check("DET uncited unmapped claim is not_addressed",
      len(_unc) == 1 and _unc[0]["final"] == "not_addressed",
      str([(c["final"]) for c in _unc]))
check("DET citation failure recorded in the checks block",
      any("county office" in t
          for t in jr["checks"]["citations"]["failed"]),
      str(jr["checks"]["citations"]))
check("DET cited claim passes deterministically",
      len(_cit) == 1 and _cit[0]["final"] == "supported")
check("DET annotation: judge unavailable wording",
      mod._checks_annotation(jr)
      == "Checks: figures + citations only "
         "(judge unavailable)",
      mod._checks_annotation(jr))
check("DET annotation: Lite wording",
      mod._checks_annotation(
          {"mode": "judge", "checks": {"judge": {
              "served_by": "Gemini Lite"}}})
      == "Checks: figures + citations + Gemini Lite judge")

# ---------------------------------------------------------------
# BUD: deadline threading + the budget cut.
# (1) A spent deadline starts no leg.
_calls = {"gemini": 0, "or": 0}


def _counting_gemini(model, system, user, **kw):
    _calls["gemini"] += 1
    return EXTRACT_JSON


def _counting_or(model, system, user, **kw):
    _calls["or"] += 1
    return EXTRACT_JSON


_saved_gt, _saved_ot = mod._gemini_text, mod._or_text
mod._gemini_text = _counting_gemini
mod._or_text = _counting_or
try:
    _past = time.monotonic() - 1
    r1 = mod._primary_judgment("lite", QUESTION, HITS, DRAFT,
                               "", deadline=_past)
    r2 = mod._primary_judgment("backup", QUESTION, HITS, DRAFT,
                               "", deadline=_past)
    r3 = mod._second_verdicts("backup", QUESTION, HITS,
                              [{"text": CLAIMS[0]}], "",
                              deadline=_past)
finally:
    mod._gemini_text, mod._or_text = _saved_gt, _saved_ot
check("BUD spent deadline: no judge leg starts",
      _calls == {"gemini": 0, "or": 0}
      and not r1["available"] and not r2["available"]
      and not r3["available"]
      and "budget spent" in (r1["error"] or ""),
      f"calls={_calls} r1={r1['error']} r3={r3['error']}")

# (2) The Gemini transport timeout clamps to the budget left:
# a transport that sleeps its FULL granted timeout ends the
# leg at the budget edge, not at the 45s default.
_granted = {}


def _sleepy_post(host, path, payload, headers, timeout=45):
    _granted["timeout"] = timeout
    time.sleep(timeout)
    body = {"candidates": [{"content": {"parts": [
        {"text": EXTRACT_JSON}]}}]}
    return 200, json.dumps(body).encode()


_saved_post = mod._https_post
_saved_key = mod.GEMINI_API_KEY
mod._https_post = _sleepy_post
mod.GEMINI_API_KEY = "stub-key"
try:
    _t0 = time.monotonic()
    _dl = _t0 + 3.0
    r = mod._primary_judgment("lite", QUESTION, HITS, DRAFT, "",
                              deadline=_dl)
    _wall = time.monotonic() - _t0
finally:
    mod._https_post = _saved_post
    mod.GEMINI_API_KEY = _saved_key
check("BUD Gemini leg timeout clamped to the budget left",
      _granted.get("timeout") is not None
      and _granted["timeout"] <= 3.05,
      f"granted={_granted.get('timeout')}")
check("BUD hanging leg ends at the budget edge (not 45s)",
      r["available"] and _wall < 6.0, f"wall={_wall:.2f}")

# (3) A unit whose judge hangs to its granted timeout
# finalizes at the budget with budget_cut recorded.
mod._events_reset()


def _hang_gemini(model, system, user, **kw):
    _dl = kw.get("deadline")
    _rem = max(0.0, _dl - time.monotonic()) if _dl else 30
    time.sleep(_rem + 0.05)  # uses its whole granted window
    return EXTRACT_JSON


_saved_gt = mod._gemini_text
_saved_gen = mod._generate
mod._gemini_text = _hang_gemini
mod._generate = lambda *a, **k: (
    DRAFT, "prompt", "grok-4.7 (xAI API)", False, False)
try:
    _t0 = time.monotonic()
    unit = mod._run_unit({
        "question": QUESTION, "unit_q": QUESTION, "hits": HITS,
        "engine": "local", "draft_model": mod.LOCAL_MODEL,
        "instructions": "", "mechanisms": [],
        "allowed_extra": [], "csra_comp": None, "hard": False,
        "corp": None, "deadline": _t0 + 4.0,
        "registry_entries": [], "reg_in_instructions": True,
        "local_mode": False, "fresh": True,
        "explicit_model": False, "full_question": None})
    _uwall = time.monotonic() - _t0
finally:
    mod._gemini_text = _saved_gt
    mod._generate = _saved_gen
check("BUD unit finalizes at the budget + processing slack",
      _uwall < 10.0, f"wall={_uwall:.2f}")
check("BUD budget_cut recorded on the unit",
      unit.get("budget_cut") is True
      and unit.get("cap_hit") is True,
      f"budget_cut={unit.get('budget_cut')}")
check("BUD cut_phase recorded",
      unit.get("cut_phase") in ("drafting", "extracting",
                                "checking", "final checks"),
      f"cut_phase={unit.get('cut_phase')}")
check("BUD unit still ships its best state (no IDK)",
      unit.get("badge") in ("checked", "checked_det",
                            "partial"),
      f"badge={unit.get('badge')}")

# (4) The job-thread backstop: an over-cap job is abandoned
# and the child cannot overwrite the finalized record.
_saved_cap, _saved_slack = mod.ASK_TIME_CAP_S, \
    mod.JOB_BACKSTOP_SLACK_S
_saved_do_post = mod.Handler.do_POST
_saved_record = mod._record_ask
mod._record_ask = lambda r: None
mod.ASK_TIME_CAP_S = 1
mod.JOB_BACKSTOP_SLACK_S = 1


def _slow_do_post(self):
    time.sleep(5)
    self._json({"answer": "late"}, 200)


mod.Handler.do_POST = _slow_do_post
try:
    _rec = mod._job_create()
    _t0 = time.monotonic()
    mod._run_job(_rec, json.dumps(
        {"question": "q", "async": True}).encode(),
        {"Content-Length": "0"})
    _jwall = time.monotonic() - _t0
    _status_at_abandon = _rec["status"]
    time.sleep(3.5)  # let the abandoned child finish
finally:
    mod.Handler.do_POST = _saved_do_post
    mod.ASK_TIME_CAP_S = _saved_cap
    mod.JOB_BACKSTOP_SLACK_S = _saved_slack
    mod._record_ask = _saved_record
check("BUD backstop abandons the over-cap job at cap+slack",
      _jwall < 4.0 and _status_at_abandon == "error"
      and "backstop" in (_rec.get("error") or ""),
      f"wall={_jwall:.2f} status={_status_at_abandon} "
      f"err={_rec.get('error')}")
check("BUD abandoned child cannot overwrite the record",
      _rec["status"] == "error" and _rec.get("result") is None)

# ---------------------------------------------------------------
# ASK: BUG-1 + BUG-5(a) — a full stub ask through _run_job.
mod2 = load_server()
_records = []
mod2._record_ask = lambda r: _records.append(r)


def _slow_retrieve(question, corp, n_ex, use_rewrite=True,
                   use_rerank=True):
    time.sleep(0.2)
    return [dict(h) for h in HITS]


def _ask_gemini(model, system, user, **kw):
    time.sleep(0.2)
    if "Extract the draft" in user:
        return EXTRACT_JSON
    return VERDICTS_JSON


# Retrieval + drafting + judging stubbed at their seams;
# mechanisms stubbed off (covered by the mechanism suites —
# an engaged mechanism would trip the sufficiency gate on
# this one-hit fixture and IDK before generation).
mod2.retrieve_ask_hits = _slow_retrieve
mod2._mechanism_matches = lambda *a, **k: []
mod2._gemini_text = _ask_gemini
mod2._generate = lambda *a, **k: (
    DRAFT, "prompt", "grok-4.7 (xAI API)", False, False)
mod2._groq_available = lambda: True
mod2._xai_available = lambda: True
mod2._or_available = lambda: True
_rec = mod2._job_create()
_body = json.dumps({"question": QUESTION, "model": "qwen3.5:9b",
                    "corpus": "in", "rewrite": False,
                    "rerank": False, "async": True}).encode()
mod2._run_job(_rec, _body, {"Content-Length": str(len(_body))})
check("ASK stub ask completes through the job path",
      _rec["status"] == "done",
      f"status={_rec['status']} err={_rec.get('error')}")
_res = _rec.get("result") or {}
check("ASK stub ask ships a checked answer",
      _res.get("badge") == "checked", f"badge={_res.get('badge')}")
_ask_recs = [r for r in _records if r.get("kind") == "ask"]
check("ASK ask record written", len(_ask_recs) == 1)
_ar = _ask_recs[0] if _ask_recs else {}
_pms = _ar.get("phase_ms") or {}
check("ASK phase_ms includes retrieving (BUG-1)",
      "retrieving" in _pms, f"keys={sorted(_pms)}")
check("ASK phase_ms complete (all five phases)",
      all(k in _pms for k in ("retrieving", "drafting",
                              "extracting", "checking",
                              "final checks")),
      f"keys={sorted(_pms)}")
_wall = _ar.get("wall_s") or 0
_sum = sum(_pms.values())
check("ASK phase times sum to >= 99% of wall",
      _wall > 0 and _sum >= 0.99 * _wall,
      f"sum={_sum:.3f} wall={_wall:.3f}")
check("ASK record carries the checks block + budget_cut",
      (_ar.get("checks") or {}).get("judge", {}).get(
          "served_by") == "Gemini Lite"
      and _ar.get("budget_cut") is False,
      f"checks={(_ar.get('checks') or {}).get('judge')} "
      f"budget_cut={_ar.get('budget_cut')}")

# ---------------------------------------------------------------
# PAGE: stepper + roster surface.
check("PAGE stepper has Checking, no Judging, no seat widgets",
      "'checking','Checking'" in mod.PAGE
      and "'judging'" not in mod.PAGE
      and "Seat 1 (Gemini)" not in mod.PAGE
      and "seatNames" not in mod.PAGE)
check("PAGE renders the one fact-check line",
      "phase.check" in mod.PAGE)
check("PAGE badge map carries the new badges only",
      "checked: ['ok', 'Checked']" in mod.PAGE
      and "checked_det:" in mod.PAGE
      and "verified_single" not in mod.PAGE
      and "partial_reduced" not in mod.PAGE)
_roster = mod._roster_health_block()
_rj = json.dumps(_roster).lower()
check("PAGE/health roster names Grok + Lite + V3.1 backup",
      "grok-4.7" in _rj and "gemini-3.5-flash-lite" in _rj
      and "deepseek-chat-v3.1" in _rj, _rj[:200])
check("PAGE/health roster has no V4",
      "v4" not in _rj.replace("v23.1", ""), _rj[:200])

# ---------------------------------------------------------------
# WSE: BUG-5(b) — the registry-vocab audit wired in as a
# floor check (floor semantics, not exact-match: parse the
# counts, never key on the exit code alone). The floor is
# the WS-E achievement on the enriched staging store that
# ships as g0004 (WSE_RECORD.md §4/§6).
# floor set by WS-E (v23.1): WSE_RECORD.md §4 —
# do not lower without a recorded reason
FLOOR_FIGURES = 14
FLOOR_MECHANISMS = 5
import re
import subprocess
_stage = os.path.join(ROOT, "generations", "g0004_staging")
if os.path.isdir(_stage):
    _out = tempfile.mkdtemp(prefix="fssa-v231-vocab-")
    _r = subprocess.run(
        [sys.executable,
         os.path.join(ROOT, "tools", "registry_vocab.py"),
         "--gen", _stage, "--out", _out],
        capture_output=True, text=True, timeout=120)
    _m = re.search(r"figures (\d+)/14, mechanisms (\d+)/5",
                   _r.stdout)
    check("WSE vocab floor on the g0004 staging store",
          _m is not None
          and int(_m.group(1)) >= FLOOR_FIGURES
          and int(_m.group(2)) >= FLOOR_MECHANISMS,
          (_m.group(0) if _m else _r.stdout[-200:]))
else:
    check("WSE vocab floor on the g0004 staging store", False,
          "generations/g0004_staging missing")

# ---------------------------------------------------------------
# V232: BUG-11 — one clock, the shared draft cap, ship-what-passed.
check("V232 constants: budgets + draft cap",
      mod.ASK_BUDGET_SIMPLE_S == 150
      and mod.ASK_BUDGET_HARD_S == 285
      and mod.ASK_TIME_CAP_S == 600
      and mod.DRAFT_CALL_CAP == 4)

# The ask clock anchors at ask start; the class deadline is
# ask-start + budget, and claiming is one shared counter.
mod._events_reset()
_t0 = time.monotonic()
mod._ask_clock_begin(_t0)
_dl = mod._ask_clock_class(True)
check("V232 clock: class deadline anchored at ask start",
      abs(_dl - (_t0 + 285)) < 0.05
      and abs(mod._ask_deadline() - (_t0 + 285)) < 0.05,
      f"dl-now={_dl - _t0:.2f}")
_claims = [mod._claim_draft_call() for _ in range(5)]
check("V232 draft counter: 4 claims pass, 5th refused",
      _claims == [True, True, True, True, False]
      and mod._ask_ctx().draft_calls == 4
      and mod._ask_ctx().draft_capped is True)

# NO RE-ARM (the F1 mechanism): an ask whose class deadline
# is already spent drafts ZERO times and records the cut —
# v23.1 re-armed a fresh 285s here and drafted happily.
mod._events_reset()
mod._ask_clock_begin(time.monotonic())
mod._ask_ctx().class_deadline = time.monotonic() - 1
_gen_calls = {"n": 0}


def _counting_generate(*a, **k):
    _gen_calls["n"] += 1
    return (DRAFT, "prompt", "grok-4.7 (xAI API)",
            False, False)


_saved_gen = mod._generate
mod._generate = _counting_generate
try:
    _t1 = time.monotonic()
    _u = mod._run_unit({
        "question": QUESTION, "unit_q": QUESTION, "hits": HITS,
        "engine": "local", "draft_model": mod.LOCAL_MODEL,
        "instructions": "", "mechanisms": [],
        "allowed_extra": [], "csra_comp": None, "hard": True,
        "corp": None, "deadline": time.monotonic() + 600,
        "registry_entries": [], "reg_in_instructions": True,
        "local_mode": False, "fresh": True,
        "explicit_model": False, "full_question": None})
    _uwall = time.monotonic() - _t1
finally:
    mod._generate = _saved_gen
check("V232 spent class deadline: no draft call starts",
      _gen_calls["n"] == 0 and _u.get("draft_calls") == 0,
      f"gen={_gen_calls['n']}")
check("V232 spent class deadline: budget_cut recorded, IDK",
      _u.get("budget_cut") is True and _u.get("badge") == "idk"
      and _uwall < 5.0,
      f"badge={_u.get('badge')} wall={_uwall:.2f}")

# DRAFT CAP with lead repairs counted: an always-uncited
# draft on a mechanism question burns draft+repair per rung;
# the shared counter stops the ladder at exactly 4.
mod._events_reset()
mod._ask_clock_begin(time.monotonic())
mod._ask_clock_class(True)
_UNCITED = ("The community spouse keeps half of the "
            "combined assets with no citation at all.\n"
            "She keeps $32,532 of the assets, again with "
            "no citation.")
_gen2 = {"n": 0}


def _uncited_generate(*a, **k):
    _gen2["n"] += 1
    return (_UNCITED, "prompt", "grok-4.7 (xAI API)",
            False, False)


_saved_gen, _saved_gt = mod._generate, mod._gemini_text
mod._generate = _uncited_generate
mod._gemini_text = lambda *a, **k: EXTRACT_JSON
try:
    _u2 = mod._run_unit({
        "question": QUESTION, "unit_q": QUESTION, "hits": HITS,
        "engine": "local", "draft_model": mod.LOCAL_MODEL,
        "instructions": "",
        "mechanisms": mod._mechanism_matches(QUESTION),
        "allowed_extra": [32532.0, 162660.0],
        "csra_comp": None, "hard": True, "corp": None,
        "deadline": time.monotonic() + 600,
        "class_deadline": mod._ask_ctx().class_deadline,
        "registry_entries": mod._registry_matches(QUESTION),
        "reg_in_instructions": True, "local_mode": False,
        "fresh": True, "explicit_model": False,
        "full_question": None})
finally:
    mod._generate, mod._gemini_text = _saved_gen, _saved_gt
check("V232 draft cap: ladder+repairs stop at 4 calls",
      _gen2["n"] == 4 and _u2.get("draft_calls") == 4
      and _u2.get("draft_capped") is True,
      f"gen={_gen2['n']} unit={_u2.get('draft_calls')} "
      f"capped={_u2.get('draft_capped')}")

# ASSEMBLY GUARD on the recorded CSRA shape (fixture): the
# draft's check round passes, the assembly would ship a
# fragment — the guard ships the checked draft verbatim.
_FX = json.load(open(os.path.join(
    HERE, "fixtures", "bug11_records.json"),
    encoding="utf-8"))
_REC = _FX["0eb4d5dd94d4"]
_REC_DRAFT = _REC["unit"]["draft_text"]
_REC_HITS = _REC["unit"]["hits"]
_REC_EXTRACT = json.dumps({"claims": [
    {"text": c["text"], "load_bearing": True,
     "verdict": "supported", "note": "", "sources": [1]}
    for c in _REC["unit"]["claims"]]})
mod._events_reset()
mod._ask_clock_begin(time.monotonic())
mod._ask_clock_class(True)
_gen3 = {"n": 0}


def _rec_generate(*a, **k):
    _gen3["n"] += 1
    return (_REC_DRAFT, "prompt", "grok-4.7 (xAI API)",
            False, False)


_saved_gen, _saved_gt = mod._generate, mod._gemini_text
_saved_det = mod._det_claim_check
mod._generate = _rec_generate
mod._gemini_text = lambda *a, **k: _REC_EXTRACT
mod._det_claim_check = lambda *a, **k: ([], True)
try:
    _u3 = mod._run_unit({
        "question": _REC["question"],
        "unit_q": _REC["question"], "hits": _REC_HITS,
        "engine": "local", "draft_model": mod.LOCAL_MODEL,
        "instructions": "", "mechanisms": [],
        "allowed_extra": [32532.0, 162660.0],
        "csra_comp": None, "hard": True, "corp": None,
        "deadline": time.monotonic() + 600,
        "class_deadline": mod._ask_ctx().class_deadline,
        "registry_entries": [], "reg_in_instructions": True,
        "local_mode": False, "fresh": True,
        "explicit_model": False, "full_question": None})
finally:
    mod._generate, mod._gemini_text = _saved_gen, _saved_gt
    mod._det_claim_check = _saved_det
check("V232 guard: shipped text is the checked draft",
      (_u3.get("answer") or "").startswith(
          (_u3.get("draft_text") or "")[:120])
      and "$4,067" in (_u3.get("answer") or "")
      and len(_u3.get("answer") or "") > 1500,
      f"answer_len={len(_u3.get('answer') or '')}")
check("V232 guard: fired + logged, badge checked-family",
      (_u3.get("assembly_guard") or {}).get("fired") is True
      and _u3.get("badge") in ("checked", "checked_det"),
      f"guard={_u3.get('assembly_guard')} "
      f"badge={_u3.get('badge')}")
check("V232 guard case: draft calls within the cap",
      _gen3["n"] <= 4 and (_u3.get("draft_calls") or 0) <= 4,
      f"gen={_gen3['n']}")

# STRIP only flagged sentences (direct): two eligible
# uncited sentences, only one flagged — only it may go.
_S_DRAFT = ("The office will forward the form to the "
            "county [1].\n"
            "The office will also mail a copy home.\n"
            "The office will keep a receipt on file.")
_S_PARSED = mod._parse_citations(_S_DRAFT, HITS)
_S_CLAIMS = [{"text": "The office will forward the form "
                      "to the county.", "load_bearing": True,
              "sources": [0]}]
_out, _removed = mod._strip_uncited_asides(
    _S_DRAFT, _S_PARSED, _S_CLAIMS, None,
    flagged={"The office will also mail a copy home."})
check("V232 strip: unflagged sentence is never stripped",
      _removed == ["The office will also mail a copy home."]
      and "keep a receipt on file" in (_out or ""),
      f"removed={_removed}")

# ABLE shape: a SPENT round never claims the backup, records
# the skip, and still finals deterministically; a LIVE round
# with Lite down attempts the backup exactly once.
mod._events_reset()
_calls2 = {"gemini": 0, "or": 0}


def _cg2(model, system, user, **kw):
    _calls2["gemini"] += 1
    return EXTRACT_JSON


def _co2(model, system, user, **kw):
    _calls2["or"] += 1
    return EXTRACT_JSON


_saved_gt, _saved_ot = mod._gemini_text, mod._or_text
mod._gemini_text, mod._or_text = _cg2, _co2
try:
    _jr = mod._check_round(
        QUESTION, HITS, DRAFT, True, "", corp=None,
        mechanisms=None,
        extra_claims=[{"text": CLAIMS[0],
                       "load_bearing": True, "sources": [0],
                       "origin": "extra"}],
        local_only=False, use_cache=False,
        deadline=time.monotonic() - 1)
finally:
    mod._gemini_text, mod._or_text = _saved_gt, _saved_ot
check("V232 ABLE: spent round starts no judge leg",
      _calls2 == {"gemini": 0, "or": 0}, f"calls={_calls2}")
check("V232 ABLE: backup skip recorded, claim not burned",
      _jr["checks"]["judge"].get("backup_skip")
      == "budget spent"
      and _jr["checks"]["judge"]["backup_used"] is False
      and mod._ask_ctx().judge_backup_used is False
      and any("backup judge: not attempted" in e
              for e in _jr["errors"]),
      f"errors={_jr['errors']}")
check("V232 ABLE: spent round finals on det checks",
      _jr["mode"] == "det" and _jr["pass"] is True,
      f"mode={_jr['mode']} pass={_jr['pass']}")
mod._events_reset()
_calls3 = {"or": 0}


def _down3(*a, **k):
    raise RuntimeError("lite down (stub)")


def _co3(model, system, user, **kw):
    _calls3["or"] += 1
    return EXTRACT_JSON


_saved_gt, _saved_ot = mod._gemini_text, mod._or_text
mod._gemini_text, mod._or_text = _down3, _co3
try:
    _jr3 = mod._check_round(
        QUESTION, HITS, DRAFT, True, "", corp=None,
        mechanisms=None, extra_claims=None,
        local_only=False, use_cache=False,
        deadline=time.monotonic() + 120)
finally:
    mod._gemini_text, mod._or_text = _saved_gt, _saved_ot
check("V232 backup: attempted exactly once when Lite down",
      _calls3["or"] == 1 and _jr3["mode"] == "judge_backup"
      and _jr3["checks"]["judge"]["backup_used"] is True
      and _jr3["checks"]["judge"].get("backup_skip") is None,
      f"or={_calls3['or']} mode={_jr3['mode']}")

print()
if FAILS:
    print(f"summary: FAILURES: {FAILS}")
    sys.exit(1)
print("summary: all v23.2 offline checks passed")
