#!/usr/bin/env python3
"""v23 offline checks: no network, no live models, no PC.

 WS1  phase transitions against a stubbed ask run: a real
      _run_unit driven with stubbed transports moves the job
      phase retrieving -> drafting -> extracting -> judging
      -> final checks -> done, in order, with round numbers
      and per-seat judge states; per-phase timing lands on
      the ask context (phase_ms).
 WS2  the judge switch, proven by replaying the v22.2 CSRA
      ask's claim set (verbatim from ask 07d907a1fc52, via
      the v22.3 checks fixture) against the new two-seat
      panel with stubbed seat transports:
        A both seats serve -> panel mode, FULL panel state,
          every claim covered by google + qwen (two families)
        B seat 2's free primary down -> the relief bench
          serves (Grok 4.7), coverage google + xai, panel
          DEGRADED (relief is not the primary)
        C seat 2 + the whole relief bench down -> gemini-
          only mode, every claim capped at one family (the
          verified_single cap)
        D seat 1 down -> the relief bench extracts (the
          round's "relief" voice), seat 2 serves, coverage
          deepseek + qwen, panel DEGRADED
        E the drafting family owns seat 2's primary family
          (qwen) -> the seat skips it and relief serves
      plus static panel-shape asserts: SEATS is exactly the
      two-seat configuration, every candidate family
      distinct, no NIM slot anywhere.
 WS3  deletion asserts: no _diverge_model / nim_ask /
      _TOPIC_BRIDGES; hybrid_search is the v2 engine only;
      the drafting ladder walks Groq -> xAI -> OpenRouter
      and then raises into the caller's local tier.

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


def _down(*a, **k):
    raise RuntimeError("stubbed down")


def install_stubs(gemini="ok", groq="ok", xai="ok", orr="ok"):
    """Script the four transports. Each is 'ok' (answers per
    its role) or 'down' (raises). Extraction calls are told
    apart from verdict calls by the prompt's own words."""
    def gemini_text(model, system, user, **kw):
        if gemini == "down":
            raise RuntimeError("stubbed down")
        return EXTRACT_JSON

    def verdict_text(tag):
        def _f(model, system, user, **kw):
            if tag == "down":
                raise RuntimeError("stubbed down")
            if "Extract the draft" in user:
                return EXTRACT_JSON
            return VERDICTS_JSON
        return _f

    mod._gemini_text = gemini_text
    mod._groq_text = verdict_text(groq)
    mod._xai_text = verdict_text(xai)
    mod._or_text = verdict_text(orr)
    mod._refutation_retrieve = lambda *a, **k: 0
    mod._groq_available = lambda: True
    mod._xai_available = lambda: True
    mod._or_available = lambda: True


def run_round():
    mod._events_reset()
    return mod._judge_round(QUESTION, HITS, DRAFT, True, "",
                            corp=None, mechanisms=None,
                            extra_claims=None, local_only=False,
                            use_cache=False, deadline=None)


def badge_for(jr):
    """_run_unit's badge math, restated: verified only when
    every substantive claim carries >= 2 coverage families."""
    subs = [c for c in jr["claims"] if not c.get("meta")]
    b = "verified" if subs and all(
        len(c.get("coverage") or []) >= 2 for c in subs) \
        else "verified_single"
    if b == "verified" and jr.get("panel_state") == "degraded":
        b = "verified_reduced"
    return b


# ---------------------------------------------------------------
# WS2 static panel shape
check("WS2 SEATS is exactly the judge_seat2 job",
      set(mod.SEATS) == {"judge_seat2"}, str(set(mod.SEATS)))
_cands = mod.SEATS["judge_seat2"]["candidates"]
check("WS2 seat 2 chain is Qwen/Groq -> Grok/xAI -> relief/OR",
      _cands == [(mod.GROQ_QWEN_MODEL, 5),
                 (mod.XAI_HARD_MODEL, 4),
                 (mod.OR_RELIEF_MODEL, 0)], str(_cands))
_fams = [mod._model_family(m) for m, _s in _cands]
check("WS2 every seat-2 candidate a distinct family, none "
      "google", len(set(_fams)) == 3 and "google" not in _fams,
      str(_fams))
check("WS2 no NIM slot on any candidate",
      all(s in (0, 4, 5) for _m, s in _cands))
check("WS2 seat 1 primary is Gemini Lite",
      mod.GEMINI_CHAIN[0] == "gemini-3.5-flash-lite",
      mod.GEMINI_CHAIN[0])

# ---------------------------------------------------------------
# WS2 replay, shape A: both seats serve
install_stubs()
jr = run_round()
check("WS2-A mode is panel", jr["mode"] == "panel", jr["mode"])
check("WS2-A panel state FULL", jr["panel_state"] == "full",
      jr["panel_state"])
check("WS2-A round passes", jr["pass"] is True)
check("WS2-A every claim covered by google + qwen",
      all(c.get("coverage") == ["google", "qwen"]
          for c in jr["claims"] if not c.get("meta")),
      str([c.get("coverage") for c in jr["claims"]]))
check("WS2-A badge outcome: verified",
      badge_for(jr) == "verified", badge_for(jr))

# ---------------------------------------------------------------
# WS2 replay, shape B: seat 2's free primary down -> relief
install_stubs(groq="down")
jr = run_round()
served2 = mod._ask_ctx().served_models.get("judge_seat2")
check("WS2-B relief bench served seat 2 (Grok 4.7)",
      served2 == mod.XAI_HARD_MODEL, str(served2))
check("WS2-B coverage is google + xai",
      all(c.get("coverage") == ["google", "xai"]
          for c in jr["claims"] if not c.get("meta")),
      str([c.get("coverage") for c in jr["claims"]]))
check("WS2-B panel state DEGRADED (relief served)",
      jr["panel_state"] == "degraded", jr["panel_state"])
check("WS2-B badge outcome: verified_reduced",
      badge_for(jr) == "verified_reduced", badge_for(jr))

# ---------------------------------------------------------------
# WS2 replay, shape C: seat 2 + relief all down -> cap
install_stubs(groq="down", xai="down", orr="down")
jr = run_round()
check("WS2-C mode is gemini-only", jr["mode"] == "gemini-only",
      jr["mode"])
check("WS2-C every claim capped at the google family alone",
      all(c.get("coverage") == ["google"]
          for c in jr["claims"] if not c.get("meta")),
      str([c.get("coverage") for c in jr["claims"]]))
check("WS2-C badge outcome: verified_single (the cap)",
      badge_for(jr) == "verified_single", badge_for(jr))

# ---------------------------------------------------------------
# WS2 replay, shape D: seat 1 down -> relief extracts
install_stubs(gemini="down")
jr = run_round()
check("WS2-D relief voice carried the extraction verdicts",
      all(c.get("relief") == "supported"
          for c in jr["claims"] if not c.get("meta")))
check("WS2-D relief model recorded as-served",
      mod._ask_ctx().served_models.get("relief")
      == mod.OR_RELIEF_MODEL,
      str(mod._ask_ctx().served_models.get("relief")))
check("WS2-D coverage is deepseek + qwen",
      all(c.get("coverage") == ["deepseek", "qwen"]
          for c in jr["claims"] if not c.get("meta")),
      str([c.get("coverage") for c in jr["claims"]]))
check("WS2-D panel state DEGRADED", jr["panel_state"] == "degraded",
      jr["panel_state"])
check("WS2-D badge outcome: verified_reduced",
      badge_for(jr) == "verified_reduced", badge_for(jr))

# ---------------------------------------------------------------
# WS2 replay, shape E: drafting family = qwen -> seat skips it
install_stubs()
mod._events_reset()
mod._ask_ctx().draft_family = "qwen"
jr = mod._judge_round(QUESTION, HITS, DRAFT, True, "",
                      corp=None, mechanisms=None,
                      extra_claims=None, local_only=False,
                      use_cache=False, deadline=None)
served2 = mod._ask_ctx().served_models.get("judge_seat2")
check("WS2-E qwen never judged its own family's draft",
      served2 == mod.XAI_HARD_MODEL, str(served2))

# ---------------------------------------------------------------
# WS3 deletion + ladder asserts
check("WS3 oracle deleted (no _diverge_model, no nim_ask)",
      not hasattr(mod, "_diverge_model")
      and not hasattr(mod, "nim_ask"))
check("WS3 topic bridges deleted",
      not hasattr(mod, "_TOPIC_BRIDGES"))
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


install_stubs()
mod._generate.__globals__["groq_ask"] = _draft_stub("groq", "down")
mod._generate.__globals__["xai_ask"] = _draft_stub("xai", "ok")
mod._generate.__globals__["or_ask"] = _draft_stub("or", "ok")
_ans = mod._generate("nim", mod.GROQ_HARD_MODEL, "q?", HITS, "")
check("WS3 ladder: Groq down -> xAI serves tier 2",
      _ans[2].startswith(mod.XAI_HARD_MODEL), _ans[2])
mod._generate.__globals__["xai_ask"] = _draft_stub("xai", "down")
_ans = mod._generate("nim", mod.GROQ_HARD_MODEL, "q?", HITS, "")
check("WS3 ladder: xAI down -> OpenRouter serves tier 2",
      "(OpenRouter API)" in _ans[2], _ans[2])
mod._generate.__globals__["or_ask"] = _draft_stub("or", "down")
_raised = False
try:
    mod._generate("nim", mod.GROQ_HARD_MODEL, "q?", HITS, "")
except RuntimeError:
    _raised = True
check("WS3 ladder: all hosted tiers down -> raises into "
      "the caller's local tier", _raised)

# ---------------------------------------------------------------
# WS1: phase transitions on a stubbed ask run (a real
# _run_unit; drafting stubbed, judging real on stubbed
# transports that sleep briefly so the poller can observe).
install_stubs()


def slow_gemini(model, system, user, **kw):
    time.sleep(0.15)
    return EXTRACT_JSON


def slow_groq(model, system, user, **kw):
    time.sleep(0.15)
    return VERDICTS_JSON


mod._gemini_text = slow_gemini
mod._groq_text = slow_groq
_real_generate = mod._generate


def slow_generate(*a, **k):
    time.sleep(0.06)  # drafting stays observable to the poller
    return (DRAFT, "prompt", "openai/gpt-oss-120b (Groq API)",
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
check("WS1 drafting/extracting/judging/final checks all "
      "observed", all(p in seq for p in
                      ("drafting", "extracting", "judging",
                       "final checks")), " -> ".join(seq))
check("WS1 phase carries the judging round number",
      rec["phase"]["round"] == 1, str(rec["phase"]))
check("WS1 seat states end done/done",
      rec["phase"]["seats"].get("seat1") == "done"
      and rec["phase"]["seats"].get("seat2") == "done",
      str(rec["phase"]["seats"]))
_pms = mod._phase_ms_snapshot()
check("WS1 phase_ms accumulated the run's phases",
      all(k in _pms for k in ("retrieving", "drafting",
                              "extracting", "judging")),
      str(sorted(_pms)))
# The unit's badge is NOT asserted here: the CSRA draft's
# raw cited sentences ride as extra claims covered by seat
# 2 alone until the v22.3 claim-dedupe fix is rebased in
# (the pure-round badge outcomes are asserted in WS2-A..D).
check("WS1 stubbed unit ran the panel and did not IDK",
      unit.get("judging", {}).get("mode") == "panel"
      and unit.get("badge") != "idk",
      str(unit.get("badge")))
check("WS1 page renders the stepper (elapsed, never an ETA)",
      "renderPhase" in mod.PAGE and "stepList" in mod.PAGE
      and "Elapsed: " in mod.PAGE
      and "% complete" not in mod.PAGE)

print()
if FAILS:
    print(f"summary: FAILURES: {FAILS}")
    sys.exit(1)
print("summary: all v23 offline checks passed")
