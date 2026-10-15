"""v22.3 offline checks, carried into v23 at the rebase:
the three fixes from the 2026-10-15 CSRA retest diagnosis
(ask 07d907a1fc52), ported onto the v23 panel.

  fix 1 - claim dedupe: a draft sentence carried both as a
          normalized extracted claim and as a raw-form extra
          claim (markdown, [registry]/[n] markers) becomes ONE
          load-bearing claim that inherits the verdict.
  fix 2 - registry-carrying claims are judged FIRST, and the
          figure/quantity checks recognize registry-admitted
          values as verified quantities.
  fix 3 - a judge seat that hits a full leg timeout is
          benched for the rest of the ask.

v23 adaptations (the fixes are unchanged; the panel they
land on is): the fix-3 seat is judge_seat2 (Groq Qwen
primary, slot 5), the bound constant is JUDGE_LEG_TIMEOUT,
and a planned pick that fails FALLS THROUGH to the seat's
next candidate inside the same call (the v23 _seat_pick
rule) — so round 1's call returns via the relief bench
instead of raising, with the benched Qwen never re-seated.

Run: python3 tools/offline_checks_v223.py
Deterministic; no network (seat transports are stubbed; the
judge panel is stubbed at the module seams)."""
import importlib.util
import os
import sys
import tempfile
import time

FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def load_server():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    tmp = tempfile.mkdtemp(prefix="fssa-v223-checks-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json"),
            ("growth_terms.json", "growth_terms.json")):
        src = os.path.join(root, target)
        if os.path.exists(src):
            os.symlink(src, os.path.join(tmp, link))
    cwd, argv = os.getcwd(), sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(root, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        sys.argv = [argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
        os.chdir(cwd)
    return mod


mod = load_server()

# The record's own texts (ask 07d907a1fc52): the four
# normalized claims the extractor produced, and the three raw
# draft bullets that ALSO arrived as extra claims.
QUESTION = ("If I go into a nursing home, how much of our "
            "savings can my wife keep?")
EXTRACTED = [
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
EXTRA = [
    "**Minimum protection:**\u202fYour wife is guaranteed to "
    "keep **at least\u202f$32,532** of the couple\u2019s countable "
    "(non\u2011exempt) assets, even if half of your combined "
    "assets would be lower than that amount\u202f[1][registry].",
    "**Maximum share:**\u202fShe may keep **up to half (\u00bd) of "
    "the couple\u2019s countable assets**, but that share cannot "
    "exceed **$162,660**\u202f[1][registry].",
    "**Nursing\u2011home spouse limit:**\u202fThe spouse who moves "
    "into the nursing home (you) is allowed to retain only "
    "**$2,000** in non\u2011exempt assets to stay "
    "Medicaid\u2011eligible\u202f[1].",
]
DRAFT = (
    "**What your wife can keep**\n\n"
    "- **Minimum protection:** Your wife is guaranteed to keep "
    "**at least $32,532** of the couple's countable "
    "(non-exempt) assets, even if half of your combined assets "
    "would be lower than that amount [1][registry].\n"
    "- **Maximum share:** She may keep **up to half (1/2) of "
    "the couple's countable assets**, but that share cannot "
    "exceed **$162,660** [1][registry].\n"
    "- **Nursing-home spouse limit:** The spouse who moves "
    "into the nursing home (you) is allowed to retain only "
    "**$2,000** in non-exempt assets to stay Medicaid-eligible "
    "[1].\n"
    "- **Asset ownership:** All assets of a married couple are "
    "treated as jointly owned for this calculation, regardless "
    "of whose name they are in [1].\n")

# ----------------------------------------------------------------
# FIX 1 (unit): the matcher merges each raw bullet with its own
# normalized twin, and nothing else.
for i, e in enumerate(EXTRA):
    scored = [j for j, c in enumerate(EXTRACTED)
              if mod._claim_dupe_score(c, e) is not None]
    check(f"fix1: raw extra {i + 1} merges with extracted "
          f"{i + 1} only", scored == [i], f"matched {scored}")

for i in range(len(EXTRACTED)):
    for j in range(i + 1, len(EXTRACTED)):
        check(f"fix1: extracted {i + 1} x {j + 1} stay apart",
              mod._claim_dupe_score(EXTRACTED[i],
                                    EXTRACTED[j]) is None)

# Genuinely distinct similar sentences keep both claims.
NEGATIVES = [
    # same figure, a different rule about it
    ("The community spouse is guaranteed to keep at least "
     "$32,532 of the couple's countable assets.",
     "**Reporting:** A spouse who keeps $32,532 must still "
     "report every asset she owns on the application form [1]."),
    # same figure, opposite polarity
    ("The institutionalized spouse is allowed to retain only "
     "$2,000 in countable assets.",
     "The $2,000 asset limit does not apply to the community "
     "spouse's own income at all."),
    # same words, different figure
    (EXTRACTED[0],
     "**Minimum protection:** Your wife is guaranteed to keep "
     "**at least $33,000** of the couple's countable assets "
     "[1][registry]."),
    # a two-figure sentence never merges with a one-figure one
    (EXTRACTED[0],
     "If the couple has $40,000, the wife keeps $32,532 of "
     "the couple's countable assets and the rest is spent "
     "down [1]."),
]
for k, (a, b) in enumerate(NEGATIVES):
    check(f"fix1: distinct pair {k + 1} keeps both claims",
          mod._claim_dupe_score(a, b) is None)

# ----------------------------------------------------------------
# REPLAY of the diagnosis shape + FIX 2 ordering: 4 extracted
# claims (deliberately scrambled: the registry-figure claims
# LAST) + the 3 raw extras, one serving judge (Lite) that
# supports everything, the second family unavailable.
mod._events_reset()
HITS = [{"url": "https://www.in.gov/iltcp/medicaid/"
               "spousal-impoverishment-protection-law",
         "title": "Spousal Impoverishment Protection Law",
         "excerpt": "maximum of half (1/2) of the non-exempt "
                    "assets up to a total of $162,660 ... at "
                    "least a minimum of $32,532",
         "n": 1, "quoted": "", "provider": "tavily"}]
SCRAMBLED = [EXTRACTED[3], EXTRACTED[2], EXTRACTED[1],
             EXTRACTED[0]]


def _stub_primary(engine, question, hits, answer, mech_block):
    return {"available": True,
            "claims": [{"text": t, "load_bearing": True,
                        "verdict": "supported", "note": ""}
                       for t in SCRAMBLED],
            "error": None}


def _stub_second(engine, question, hits, claims, mech_block):
    return {"available": False, "verdicts": {}, "missed": [],
            "error": "stubbed: second family down"}


_orig_primary, _orig_second = (mod._primary_judgment,
                               mod._second_verdicts)
_orig_refute = mod._refutation_retrieve
mod._refutation_retrieve = lambda *a, **k: 0
mod._primary_judgment = _stub_primary
mod._second_verdicts = _stub_second
try:
    jr = mod._judge_round(
        QUESTION, HITS, DRAFT, False, mech_block="",
        corp=None, mechanisms=[],
        extra_claims=[{"text": t, "load_bearing": True,
                       "sources": [0]} for t in EXTRA],
        use_cache=False)
finally:
    mod._primary_judgment = _orig_primary
    mod._second_verdicts = _orig_second
    mod._refutation_retrieve = _orig_refute

claims = jr["claims"]
check("replay: 7 claim forms resolve to 4 claims",
      len(claims) == 4, f"got {len(claims)}")
check("replay: all 4 surviving claims are load-bearing",
      all(c["load_bearing"] for c in claims))
check("replay: every claim judged, final supported",
      all(c["final"] == "supported" for c in claims),
      str([(c["text"][:40], c["final"]) for c in claims]))
check("replay: round passes at single-family level",
      jr["pass"] is True and jr["mode"] == "gemini-only",
      f"pass={jr['pass']} mode={jr['mode']}")
merged = [c for c in claims
          if any("dedupe" in n for n in c["notes"])]
check("replay: exactly the 3 duplicated claims carry the "
      "dedupe note", len(merged) == 3, f"got {len(merged)}")
check("replay: merged claims inherited the extras' sources",
      all(0 in c["sources"] for c in merged))
# fix 2: the two registry-figure claims were judged first
# (they entered the round LAST in the scrambled stub order).
_lead_figs = [mod._claim_numbers(c["text"]) for c in claims[:2]]
_tail_figs = [f for c in claims[2:]
              for f in mod._claim_numbers(c["text"])]
_lead_flat = [f for figs in _lead_figs for f in figs]
check("fix2: registry-figure claims lead the judged queue",
      any(abs(f - 32532.0) <= 0.011 for f in _lead_flat)
      and any(abs(f - 162660.0) <= 0.011 for f in _lead_flat)
      and 32532.0 not in _tail_figs and 162660.0 not in _tail_figs,
      f"lead={_lead_figs} tail={_tail_figs}")

# fix 2 (marker branch): a [registry]-carrying claim leads
# even when no figure matches an admitted value.
mod._events_reset()


def _stub_primary2(engine, question, hits, answer, mech_block):
    return {"available": True,
            "claims": [
                {"text": "The couple's assets are counted "
                         "together for the assessment.",
                 "load_bearing": True, "verdict": "supported",
                 "note": ""},
                {"text": "The state publishes the protected "
                         "amounts for the year [registry].",
                 "load_bearing": True, "verdict": "supported",
                 "note": ""}],
            "error": None}


mod._refutation_retrieve = lambda *a, **k: 0
mod._primary_judgment = _stub_primary2
mod._second_verdicts = _stub_second
try:
    jr2 = mod._judge_round(
        QUESTION, HITS, DRAFT, False, mech_block="",
        corp=None, mechanisms=[], extra_claims=[],
        use_cache=False)
finally:
    mod._primary_judgment = _orig_primary
    mod._second_verdicts = _orig_second
    mod._refutation_retrieve = _orig_refute
check("fix2: [registry]-marked claim is judged first",
      "[registry]" in jr2["claims"][0]["text"],
      jr2["claims"][0]["text"][:60])

# ----------------------------------------------------------------
# FIX 2 (quantity recognition): a figure admitted ONLY by the
# registry block is a verified quantity for the figure check;
# without the registry values in scope it is (correctly)
# flagged. The real CSRA draft produces no quantity failures.
_entries = mod._registry_matches(QUESTION)
_admitted = mod._registry_values(
    _entries, on_date=mod._question_date(QUESTION))
check("fix2: csra entry admits the 2026 floor and ceiling",
      32532.0 in _admitted and 162660.0 in _admitted,
      str(_admitted))
_one = ("Your wife is guaranteed to keep at least $32,532 "
        "of the couple's countable assets.")
_out, _ung = mod._check_figures(_one, QUESTION, [],
                                extra_allowed=_admitted)
check("fix2: registry-only figure passes the figure check",
      _ung == [] and _out == _one, f"ung={_ung}")
_out2, _ung2 = mod._check_figures(_one, QUESTION, [],
                                  extra_allowed=())
check("fix2: same figure without registry scope is flagged "
      "(control)", _ung2 == [32532.0], f"ung={_ung2}")
_q = mod._check_quantities(DRAFT, QUESTION)
check("fix2: the record's draft has no quantity failures",
      not _q, str(_q))

# ----------------------------------------------------------------
# FIX 3: one full leg timeout benches the candidate for the
# rest of the ask; a budget-clamped shed does not. (v23
# panel: judge_seat2, the Groq Qwen primary on slot 5; a
# failed planned pick falls through to the relief bench
# inside the same call.)
mod._events_reset()
_ctx = mod._ask_ctx()
QWEN_J = mod.GROQ_QWEN_MODEL
_ctx.panel_plan["judge_seat2"] = (QWEN_J, 5)
_attempts = []


def _fake_groq_text(model, system, user, **kw):
    _attempts.append(model)
    if model == QWEN_J:
        time.sleep(min(kw.get("timeout") or 1, 2) + 0.05)
        raise TimeoutError("read timed out")
    return "ok"


def _fake_xai_text(model, system, user, **kw):
    return "ok"


_saved = (mod._groq_text, mod._xai_text,
          mod.JUDGE_LEG_TIMEOUT, mod._groq_guard_tripped,
          mod._groq_available, mod._xai_available,
          mod._or_available)
mod._groq_text = _fake_groq_text
mod._xai_text = _fake_xai_text
mod.JUDGE_LEG_TIMEOUT = 1
mod._groq_guard_tripped = lambda: False
mod._groq_available = lambda: True
mod._xai_available = lambda: True
mod._or_available = lambda: True
try:
    # Round 1: the planned Qwen leg eats the full timeout
    # and earns the bench; the v23 fall-through rule then
    # serves the SAME call from the relief bench (xAI).
    out1 = mod._judge_seat_text("judge_seat2", "s", "u")
    check("fix3: round 1 call is served by failover after "
          "the planned candidate's full timeout",
          out1 == "ok" and _attempts == [QWEN_J],
          f"out={out1} attempts={_attempts}")
    bench = _ctx.judge_benched.get(("judge_seat2", QWEN_J), "")
    check("fix3: full timeout benches the candidate for "
          "the ask", "full leg timeout" in bench, bench)
    ev = [e for e in _ctx.events
          if e.get("kind") == "seat_bench"
          and e.get("model") == QWEN_J]
    check("fix3: bench emits its reason as a seat_bench event",
          len(ev) == 1 and "full leg timeout" in
          ev[0].get("reason", ""), str(ev))
    out2 = mod._judge_seat_text("judge_seat2", "s", "u")
    check("fix3: next call never re-seats the benched "
          "candidate",
          out2 == "ok" and _attempts.count(QWEN_J) == 1,
          f"attempts={_attempts}")
    skips = [e for e in _ctx.events
             if e.get("kind") == "seat_skip"
             and e.get("model") == QWEN_J]
    check("fix3: the skip is explained in the ask record",
          any("benched for this ask" in (e.get("reason") or "")
              for e in skips), str(skips))
    check("fix3: _seat_pick never returns the benched model",
          mod._seat_pick("judge_seat2",
                         exclude_families=())[0] != QWEN_J)

    # Control: same timeout shape, but the leg was clamped
    # by the unit budget (never granted its full bound) ->
    # the seat is NOT benched; the budget takes the blame.
    mod._events_reset()
    _ctx2 = mod._ask_ctx()
    _ctx2.panel_plan["judge_seat2"] = (QWEN_J, 5)
    _ctx2.unit_deadline = time.monotonic() + 0.4
    _attempts.clear()
    try:
        mod._judge_seat_text("judge_seat2", "s", "u")
        _shed = False
    except RuntimeError:
        _shed = True
    check("fix3: budget-clamped leg sheds the seat call "
          "(the failover leg cannot fit the remainder "
          "either)", _shed and _attempts == [QWEN_J],
          f"shed={_shed} attempts={_attempts}")
    check("fix3: budget-clamped timeout does NOT bench",
          ("judge_seat2", QWEN_J) not in _ctx2.judge_benched,
          str(_ctx2.judge_benched))
finally:
    (mod._groq_text, mod._xai_text,
     mod.JUDGE_LEG_TIMEOUT, mod._groq_guard_tripped,
     mod._groq_available, mod._xai_available,
     mod._or_available) = _saved

print()
if FAILS:
    print(f"v22.3 checks: {len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("v22.3 offline checks (v23 rebase): ALL PASS")
