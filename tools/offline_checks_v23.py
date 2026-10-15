#!/usr/bin/env python3
"""v23 offline checks, carried into v23.1: no network, no
live models, no PC.

 WS1  phase transitions against a stubbed ask run: a real
      _run_unit driven with stubbed transports moves the job
      phase retrieving -> drafting -> extracting -> checking
      -> final checks -> done, in order, with round numbers
      and the ONE fact-check line naming who served (v23.1
      WS-F; the panel era's per-seat states are gone);
      per-phase timing lands on the ask context (phase_ms).
 WS2  the checks stack, proven by replaying the v22.2 CSRA
      ask's claim set (verbatim from ask 07d907a1fc52, via
      the v22.3 checks fixture) against _check_round with
      stubbed judge transports (this replay REPLACES the
      v22.3-era refutation replay, per BUG-5(c)):
        A Lite serves -> judge mode, judge_complete, every
          claim's coverage is the google family, badge
          outcome checked
        B Lite down -> the DeepSeek V3.1 backup serves
          (judge_backup mode, backup_used recorded), badge
          outcome checked
        C Lite + backup down -> det mode: finals from the
          deterministic checks alone, judge_complete False,
          badge outcome checked_det
        D a figure-swapped claim the judge calls supported
          is contradicted by the deterministic figure check
          (checks sit UNDER the judge)
        E the backup fires at most once per ask: a second
          round in the same ask context never re-calls it
      plus static asserts: JUDGE_BACKUP_MODEL is the V3.1
      slug, the panel machinery (SEATS, _judge_round,
      _panel_state) is absent, and no V4 slug appears in
      the server source.
 WS3  deletion asserts: no _diverge_model / nim_ask /
      _TOPIC_BRIDGES; hybrid_search is the v2 engine only;
      the drafting ladder walks xAI Grok -> Groq GPT-OSS
      and then raises into the caller's local tier (the
      OpenRouter drafting tier is deleted).

Run: python3 tools/offline_checks_v23.py
Exit 0 only when every check passes.
"""
import importlib.util
import inspect
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
    tmp = tempfile.mkdtemp(prefix="fssa-v23-checks-")
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

# ---------------------------------------------------------------
# The v22.2 CSRA ask's claim set (ask 07d907a1fc52, verbatim
# from the v22.3 offline-checks fixture): the question, the
# four extracted claims, the draft, and one evidence hit.
QUESTION = ("If I go into a nursing home, how much of our "
            "savings can my wife keep?")
CLAIMS = [
    "Your wife is guaranteed to keep at least $32,532 of the "
    "couple's countable assets.",
    "She may keep up to half (1/2) of the couple's countable "
    "assets, but that share cannot exceed $162,660.",
    "The nursing home spouse is allowed to retain only $2,000 "
    "in non-exempt assets to be eligible for Medicaid.",
    "All assets of a married couple are generally considered "
    "to be jointly-owned no matter in whose name they have "
    "been placed.",
]
DRAFT = (
    "- Minimum protection: Your wife is guaranteed to keep "
    "at least $32,532 of the couple's countable (non-exempt) "
    "assets, even if half of your combined assets would be "
    "lower than that amount [1].\n"
    "- Maximum share: She may keep up to half (1/2) of the "
    "couple's countable assets, but that share cannot exceed "
    "$162,660 [1].\n"
    "- Nursing-home spouse limit: The spouse who moves into "
    "the nursing home is allowed to retain only $2,000 in "
    "non-exempt assets to stay Medicaid-eligible [1].\n"
    "- Asset ownership: All assets of a married couple are "
    "treated as jointly owned for this calculation, "
    "regardless of whose name they are in [1].\n")
HITS = [{"title": "ILTCP: Spousal Impoverishment",
         "url": "https://www.in.gov/medicaid/files/ilTCP.pdf",
         "excerpt": "The community spouse may keep one half "
                    "of the couple's countable assets, not "
                    "less than $32,532 and not more than "
                    "$162,660. The institutionalized spouse "
                    "retains $2,000."}]

EXTRACT_JSON = json.dumps({"claims": [
    {"text": t, "load_bearing": True, "verdict": "supported",
     "note": "", "sources": [1]} for t in CLAIMS]})
VERDICTS_JSON = json.dumps({
    "verdicts": [{"i": i + 1, "verdict": "supported",
                  "note": ""} for i in range(14)],
    "missed": []})

# The draft's citation-derived claims, exactly as _run_unit
# builds them (they are the det mode's claim source when no
# judge can serve).
EXTRA_CLAIMS = [dict(cc, span=cc["text"]) for cc in
                mod._parse_citations(DRAFT, HITS)["cited_claims"]]

OR_CALLS = {"n": 0}


def _down(*a, **k):
    raise RuntimeError("stubbed down")


def install_stubs(gemini="ok", orr="ok"):
    """Script the judge transports. gemini = Lite (extraction
    + verdicts); orr = the OpenRouter backup (extraction when
    it serves, verdicts likewise). Extraction calls are told
    apart from verdict calls by the prompt's own words."""
    def gemini_text(model, system, user, **kw):
        if gemini == "down":
            raise RuntimeError("stubbed down")
        if "Extract the draft" in user:
            return EXTRACT_JSON
        return VERDICTS_JSON

    def or_text(model, system, user, **kw):
        OR_CALLS["n"] += 1
        if orr == "down":
            raise RuntimeError("stubbed down")
        if "Extract the draft" in user:
            return EXTRACT_JSON
        return VERDICTS_JSON

    mod._gemini_text = gemini_text
    mod._or_text = or_text
    mod._groq_available = lambda: True
    mod._xai_available = lambda: True
    mod._or_available = lambda: True


def run_round(reset=True):
    if reset:
        mod._events_reset()
        OR_CALLS["n"] = 0
    return mod._check_round(QUESTION, HITS, DRAFT, True, "",
                            corp=None, mechanisms=None,
                            extra_claims=EXTRA_CLAIMS,
                            local_only=False,
                            use_cache=False, deadline=None)


def badge_for(jr):
    """_run_unit's shipped-badge math, restated (v23.1 WS-C):
    checked when the judge pass covered every substantive
    claim, else checked_det."""
    return "checked" if jr.get("judge_complete") \
        else "checked_det"


# ---------------------------------------------------------------
# WS2 statics: the roster is code, the panel is gone.
check("WS2 backup judge is DeepSeek Chat V3.1",
      mod.JUDGE_BACKUP_MODEL == "deepseek/deepseek-chat-v3.1",
      mod.JUDGE_BACKUP_MODEL)
check("WS2 panel machinery absent (SEATS / _judge_round / "
      "_panel_state / _seat_pick)",
      not hasattr(mod, "SEATS")
      and not hasattr(mod, "_judge_round")
      and not hasattr(mod, "_panel_state")
      and not hasattr(mod, "_seat_pick"))
with open(os.path.join(ROOT, "fssa_search_server.py"),
          encoding="utf-8") as _f:
    _SRC = _f.read()
check("WS2 no V4 slug anywhere in the server source",
      "deepseek-v4" not in _SRC and "deepseek/deepseek-v4"
      not in _SRC)

# ---------------------------------------------------------------
# WS2 replay, shape A: Lite serves.
install_stubs(gemini="ok", orr="ok")
jr = run_round()
check("WS2-A mode is judge", jr["mode"] == "judge", jr["mode"])
check("WS2-A round passes", jr["pass"] is True)
check("WS2-A judge covered every substantive claim",
      jr["judge_complete"] is True)
check("WS2-A every claim's coverage is the google family",
      all(c.get("coverage") == ["google"]
          for c in jr["claims"] if not c.get("meta")),
      str([c.get("coverage") for c in jr["claims"]]))
check("WS2-A checks record names Lite as the judge",
      jr["checks"]["judge"]["served_by"] == "Gemini Lite"
      and jr["checks"]["judge"]["model"] == mod.GEMINI_CHAIN[0],
      str(jr["checks"]["judge"]))
check("WS2-A badge outcome: checked",
      badge_for(jr) == "checked", badge_for(jr))

# WS2 replay, shape B: Lite down -> the V3.1 backup serves.
install_stubs(gemini="down", orr="ok")
jr = run_round()
check("WS2-B mode is judge_backup",
      jr["mode"] == "judge_backup", jr["mode"])
check("WS2-B backup use recorded",
      jr["checks"]["judge"]["backup_used"] is True)
check("WS2-B checks record names the backup",
      "DeepSeek V3.1" in (jr["checks"]["judge"]["served_by"]
                          or ""),
      str(jr["checks"]["judge"]))
check("WS2-B round passes, badge outcome: checked",
      jr["pass"] is True and badge_for(jr) == "checked",
      f"pass={jr['pass']} badge={badge_for(jr)}")

# WS2 replay, shape E: the backup fires at most once per ask.
# (Same ask context as shape B — no reset: round 2 with Lite
# still down must NOT call OpenRouter again and lands in det
# mode on the citation-derived claims.)
jr2 = run_round(reset=False)
check("WS2-E second round never re-calls the backup",
      OR_CALLS["n"] == 1, f"or calls={OR_CALLS['n']}")
check("WS2-E second round degrades to det mode",
      jr2["mode"] == "det", jr2["mode"])

# WS2 replay, shape C: Lite + backup both down -> det mode.
install_stubs(gemini="down", orr="down")
jr = run_round()
check("WS2-C mode is det", jr["mode"] == "det", jr["mode"])
check("WS2-C det finals pass on the traced figures",
      jr["pass"] is True
      and all(c["final"] == "supported" for c in jr["claims"]),
      str([(c["text"][:40], c["final"]) for c in jr["claims"]]))
check("WS2-C judge_complete False, badge outcome: checked_det",
      jr["judge_complete"] is False
      and badge_for(jr) == "checked_det")
check("WS2-C checks record: no judge served",
      jr["checks"]["judge"]["served_by"] is None)

# WS2 replay, shape D: the deterministic figure check sits
# UNDER the judge — a swapped figure the judge waves through
# is contradicted anyway.
SWAPPED = ("Your wife is guaranteed to keep at least "
           "$99,999 of the couple's countable assets.")
SWAP_JSON = json.dumps({"claims": [
    {"text": SWAPPED, "load_bearing": True,
     "verdict": "supported", "note": "", "sources": [1]}]})


def swap_gemini(model, system, user, **kw):
    if "Extract the draft" in user:
        return SWAP_JSON
    return VERDICTS_JSON


install_stubs(gemini="ok", orr="ok")
mod._gemini_text = swap_gemini
jr = run_round()
_sw = [c for c in jr["claims"] if "99,999" in c["text"]]
check("WS2-D swapped figure contradicted despite the judge",
      len(_sw) == 1 and _sw[0]["final"] == "contradicted"
      and _sw[0]["judge"] == "supported",
      str([(c["final"], c.get("judge")) for c in _sw]))
check("WS2-D figure failure lands in the checks record",
      any("99,999" in f["claim"]
          for f in jr["checks"]["figures"]["failed"]),
      str(jr["checks"]["figures"]))
check("WS2-D round fails on the swapped claim",
      jr["pass"] is False)

# ---------------------------------------------------------------
# WS3: deletions + the drafting ladder's new order.
install_stubs()
check("WS3 oracle + NIM ask + topic bridges deleted",
      not hasattr(mod, "_diverge_model")
      and not hasattr(mod, "nim_ask")
      and not hasattr(mod, "_TOPIC_BRIDGES"))
_hs = inspect.getsource(mod.hybrid_search)
check("WS3 hybrid_search is the v2 engine only",
      "return _hybrid_search_v2" in _hs
      and "_TOPIC_BRIDGES" not in _hs
      and "vector_search(" not in _hs)
check("WS3 no RETRIEVAL flag read in _v2_open",
      '"RETRIEVAL", "v2"' not in inspect.getsource(mod._v2_open)
      and '_V2["requested"] = "v2"'
      in inspect.getsource(mod._v2_open))


def _draft_stub(name, behavior):
    def _f(model, question, hits, instructions="",
           timeout=None, deadline=None):
        if behavior == "down":
            raise RuntimeError("stubbed down")
        return (f"draft by {name}", "prompt")
    return _f


mod._generate.__globals__["xai_ask"] = _draft_stub("xai", "ok")
mod._generate.__globals__["groq_ask"] = \
    _draft_stub("groq", "ok")
mod._generate.__globals__["or_ask"] = _draft_stub("or", "ok")
_ans = mod._generate("nim", mod.XAI_HARD_MODEL, "q?", HITS, "")
check("WS3 ladder: xAI Grok serves tier 1",
      _ans[2].startswith(mod.XAI_HARD_MODEL), _ans[2])
mod._generate.__globals__["xai_ask"] = \
    _draft_stub("xai", "down")
_ans = mod._generate("nim", mod.XAI_HARD_MODEL, "q?", HITS, "")
check("WS3 ladder: xAI down -> Groq GPT-OSS serves tier 2",
      _ans[2].startswith(mod.GROQ_HARD_MODEL)
      and "(Groq API)" in _ans[2], _ans[2])
mod._generate.__globals__["groq_ask"] = \
    _draft_stub("groq", "down")
_raised = False
try:
    mod._generate("nim", mod.XAI_HARD_MODEL, "q?", HITS, "")
except RuntimeError:
    _raised = True
check("WS3 ladder: both hosted tiers down -> raises into "
      "the caller's local tier (no OpenRouter drafting)",
      _raised)

# ---------------------------------------------------------------
# WS1: phase transitions on a stubbed ask run (a real
# _run_unit; drafting stubbed, judging real on stubbed
# transports that sleep briefly so the poller can observe).
install_stubs()


# WS1's extractor returns only 3 of the 4 claims on purpose
# (it omits the $2,000 claim): that claim's cited bullet then
# stays an unmerged extra (its figure matches no extracted
# claim, so the dedupe cannot merge it), the checking
# round's verdict stage runs (slowly, below), and the poller
# can observe the "checking" phase — with all 4 extracted,
# the deterministic stages finish inside one poller tick
# and the phase is real but unobservable.
EXTRACT_JSON_3 = json.dumps({"claims": [
    {"text": t, "load_bearing": True, "verdict": "supported",
     "note": "", "sources": [1]}
    for t in (CLAIMS[0], CLAIMS[1], CLAIMS[3])]})


def slow_gemini(model, system, user, **kw):
    time.sleep(0.15)
    if "Extract the draft" in user:
        return EXTRACT_JSON_3
    return VERDICTS_JSON


mod._gemini_text = slow_gemini
_real_generate = mod._generate


def slow_generate(*a, **k):
    time.sleep(0.06)  # drafting stays observable to the poller
    return (DRAFT, "prompt", "grok-4.7 (xAI API)",
            False, False)


mod._generate = slow_generate
rec = mod._job_create()
mod._STAGE_TLS.job = rec
seen = []
stop = threading.Event()


def poll():
    while not stop.is_set():
        seen.append(rec["phase"]["name"])
        time.sleep(0.01)


pt = threading.Thread(target=poll, daemon=True)
pt.start()
mod._events_reset()
mod._set_phase("retrieving")
unit = mod._run_unit({
    "question": QUESTION, "unit_q": QUESTION, "hits": HITS,
    "engine": "local", "draft_model": mod.LOCAL_MODEL,
    "instructions": "", "mechanisms": [], "allowed_extra": [],
    "csra_comp": None, "hard": True, "corp": None,
    "deadline": time.monotonic() + 120,
    "registry_entries": [], "reg_in_instructions": True,
    "local_mode": False, "fresh": True,
    "explicit_model": False, "full_question": None})
time.sleep(0.05)  # let the poller observe final checks
mod._set_phase("done")
stop.set()
pt.join(timeout=2)
mod._generate = _real_generate
mod._STAGE_TLS.job = None
seq = [p for i, p in enumerate(seen)
       if i == 0 or p != seen[i - 1]]
order = list(mod._PHASE_ORDER)
check("WS1 phases observed in canonical order",
      seq == sorted(seq, key=order.index), " -> ".join(seq))
check("WS1 drafting/extracting/checking/final checks all "
      "observed", all(p in seq for p in
                      ("drafting", "extracting", "checking",
                       "final checks")), " -> ".join(seq))
check("WS1 phase carries the checking round number",
      rec["phase"]["round"] == 1, str(rec["phase"]))
check("WS1 fact-check line names who served (Lite)",
      rec["phase"]["check"] == "Fact check: Gemini Lite",
      str(rec["phase"].get("check")))
_pms = mod._phase_ms_snapshot()
check("WS1 phase_ms accumulated the run's phases",
      all(k in _pms for k in ("retrieving", "drafting",
                              "extracting", "checking")),
      str(sorted(_pms)))
check("WS1 stubbed unit ran the checks and did not IDK",
      unit.get("judging", {}).get("mode") == "judge"
      and unit.get("badge") != "idk",
      str(unit.get("badge")))
check("WS1 page renders the stepper (elapsed, never an ETA)",
      "renderPhase" in mod.PAGE and "stepList" in mod.PAGE
      and "Elapsed: " in mod.PAGE
      and "% complete" not in mod.PAGE)
check("WS1 page stepper has a Checking step, no Judging step",
      "'checking','Checking'" in mod.PAGE
      and "'judging'" not in mod.PAGE)

print()
if FAILS:
    print(f"summary: FAILURES: {FAILS}")
    sys.exit(1)
print("summary: all v23 offline checks passed")
