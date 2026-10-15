#!/usr/bin/env python3
"""v21 offline unit checks (no models, no network).

Covers the v21 changes that the retrieval harness cannot:
  WS4a  _nim_text leg deadlines (stubbed transport): a leg
        is cut at its deadline; the internal retry cannot
        double it; the retry survives when budget remains.
  WS2   effective-dated registry: _question_date, the
        divisor pair's date exclusivity, ABLE history values,
        a 2026-windowed entry staying silent for 2027.
  WS3a  gap rows carry ask_id + class_guess.
  WS4b  seat_skip events fire (and dedupe) on a pick that
        serves nobody.
  WS1a  _rescue_search ranks the two failure docs first.
  WS3c  growth_terms.json loads and expands.

Imports the real server module exactly like
retrieval_regression.py (temp CWD, symlinked corpora).
Exit 0 only when every check passes.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(HERE)

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load_server():
    tmp = tempfile.mkdtemp(prefix="fssa-offline-checks-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json"),
            ("growth_terms.json", "growth_terms.json")):
        src = os.path.join(SERVER_DIR, target)
        if os.path.exists(src):
            os.symlink(src, os.path.join(tmp, link))
    old_cwd, old_argv = os.getcwd(), sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(SERVER_DIR, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        sys.argv = [old_argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
    return mod, tmp


def main():
    mod, tmp = load_server()

    # ---------------- WS3c: growth terms -------------------
    check("WS3c growth_terms.json loaded",
          len(mod._GROWTH_TERMS) == 5,
          f"{len(mod._GROWTH_TERMS)} registrations")
    check("WS3c expansion maps divisor -> section",
          "3006.00.00" in mod._growth_expansions(
              "what is the divisor"))

    # ---------------- WS2: registry dating -----------------
    check("WS2 _question_date month+year",
          mod._question_date("the standards in March 2027")
          == "2027-03-15")
    check("WS2 _question_date bare year -> June 1",
          mod._question_date("the 2027 amounts") == "2027-06-01")
    check("WS2 _question_date no signal -> None",
          mod._question_date("how much can she keep") is None)

    ids = [e["id"] for e in mod._registry_matches(
        "What is Indiana's Medicaid transfer penalty "
        "divisor?")]
    check("WS2 divisor today -> current entry only",
          ids == ["transfer_penalty_divisor_current"],
          f"{ids}")
    ids = [e["id"] for e in mod._registry_matches(
        "What was the transfer penalty divisor for an "
        "application filed in March 2026?")]
    check("WS2 divisor March 2026 -> legacy entry only",
          ids == ["transfer_penalty_divisor_legacy"],
          f"{ids}")
    ids = [e["id"] for e in mod._registry_matches(
        "What was the transfer penalty divisor in 2026?")]
    check("WS2 divisor bare 2026 (June 1) -> legacy only",
          ids == ["transfer_penalty_divisor_legacy"],
          f"{ids}")

    # v22 registry fix: the contribution figures moved out
    # of able_onset_age into the dated pair
    # able_contribution_limit (+ _legacy). The first question
    # is this suite's original (it smuggles onset words). In
    # v21 it matched able_onset_age ONLY because the
    # contribution history was squatting in that entry; with
    # the history moved, the legacy pair entry serves the
    # 2025 figure and the onset entry is silent for a
    # 2025-dated question by design (its onset rule is a 2026
    # rule). The rest are natural phrasings only, including
    # the operator's verbatim 2026-10-14 live failure
    # question.
    able_q = ("For an ABLE account, the onset age question: "
              "what was the annual contribution limit in "
              "2025?")
    entries = mod._registry_matches(able_q)
    check("WS2 ABLE 2025 mixed question -> legacy pair entry",
          [e["id"] for e in entries] == [
              "able_contribution_limit_legacy"],
          f"{[e['id'] for e in entries]}")
    vals_2025 = mod._registry_values(
        entries, on_date=mod._question_date(able_q))
    check("WS2 ABLE values for 2025 include $19,000",
          19000.0 in vals_2025, f"{vals_2025}")
    check("WS2 ABLE values for 2025 exclude $20,000",
          20000.0 not in vals_2025, f"{vals_2025}")

    james_q = ("What was the maximum able account "
               "contribution for 2025?")
    entries = mod._registry_matches(james_q)
    check("WS2 ABLE contribution 2025 (natural) -> legacy only",
          [e["id"] for e in entries] == [
              "able_contribution_limit_legacy"],
          f"{[e['id'] for e in entries]}")
    vals = mod._registry_values(
        entries, on_date=mod._question_date(james_q))
    check("WS2 ABLE contribution 2025 values = $19,000 only",
          19000.0 in vals and 20000.0 not in vals, f"{vals}")
    entries = mod._registry_matches(
        "What is the ABLE account contribution limit?")
    check("WS2 ABLE contribution no-date -> current only",
          [e["id"] for e in entries] == ["able_contribution_limit"],
          f"{[e['id'] for e in entries]}")
    vals_today = mod._registry_values(entries)
    check("WS2 ABLE values today include $20,000, exclude $19,000",
          20000.0 in vals_today and 19000.0 not in vals_today,
          f"{vals_today}")
    onset_q = ("How old can someone be when they became "
               "disabled and still open an ABLE account?")
    entries = mod._registry_matches(onset_q)
    check("WS2 onset question -> able_onset_age only",
          [e["id"] for e in entries] == ["able_onset_age"],
          f"{[e['id'] for e in entries]}")
    vals = mod._registry_values(entries)
    check("WS2 onset values keep disregard/credit, no cap",
          100000.0 in vals and 500.0 in vals
          and 20000.0 not in vals and 19000.0 not in vals,
          f"{vals}")

    ids = [e["id"] for e in mod._registry_matches(
        "How much can my community spouse keep in savings "
        "in 2027?")]
    check("WS2 CSRA (2026 window) silent for a 2027 question",
          "csra" not in ids, f"{ids}")

    # ---------------- v22.1: registry trigger audit ------------
    # One plain-language question per registry entry, the way a
    # layperson asks it (spouse = "my wife"/"my husband", no
    # source-document jargon, never the entry's own trigger
    # phrases). EVERY entry must hit, and hit ALONE: a widened
    # group that cross-matches a neighboring entry's question
    # fails here. Full record: REGISTRY_TRIGGER_AUDIT.md. (In
    # the v22 base, 11 of these 14 questions matched NOTHING —
    # the two live failures, ABLE contribution and CSRA, were
    # this same disease.)
    audit_cases = {
        "backpay_exclusion":
            "Social Security owed me money from earlier months "
            "and just paid it all at once. How long before that "
            "money counts against me?",
        "able_onset_age":
            "What is the oldest age my disability could have "
            "started for me to open an ABLE account?",
        "snap_resource_limit_in":
            "How much can I have in the bank and still get EBT "
            "in Indiana?",
        "csra":
            "If I go into a nursing home how much of our "
            "savings can my wife keep?",
        "mmmna":
            "If my husband is in a nursing home, how much of "
            "his monthly check can I keep to live on?",
        "home_equity_limit":
            "If our house is worth a lot, can I still get "
            "Medicaid help to pay for a nursing home?",
        "asset_limit_2000":
            "How much money can I have in the bank and still "
            "get Medicaid?",
        "waiver_income_cap":
            "I get help at home through Medicaid. How much "
            "money can I make each month and still qualify?",
        "ssa_2027_figures":
            "How much will my Social Security check go up "
            "next year?",
        "difficulty_of_care":
            "My wife gets paid to take care of me at home. "
            "Does that money count if she applies for Healthy "
            "Indiana?",
        "transfer_penalty_divisor_current":
            "If I give property to my kids now and apply for "
            "Medicaid after July 2026, how long will I be "
            "penalized?",
        "transfer_penalty_divisor_legacy":
            "If I gave property to my kids and applied for "
            "Medicaid in March 2026, how long would I be "
            "penalized?",
        "able_contribution_limit":
            "How much money can I contribute to an ABLE "
            "account each year?",
        "able_contribution_limit_legacy":
            "How much money could I contribute to an ABLE "
            "account in 2025?",
    }
    for eid, q in audit_cases.items():
        got = [e["id"] for e in mod._registry_matches(q)]
        check(f"v22.1 audit: {eid} hits alone on its plain "
              f"question", got == [eid], f"{got}")
    got = [e["id"] for e in mod._registry_matches(
        "What is the community spouse resource allowance "
        "for 2026?")]
    check("v22.1 audit: CSRA stemming probe (singular "
          "'resource' token)", got == ["csra"], f"{got}")

    # ---------------- v22.2: mechanisms trigger audit -------
    # One plain-language question per MECHANISMS entry (the
    # v14 twins of the figures registry; same matcher, same
    # disease). EVERY entry must hit, and hit ALONE. In the
    # v22.1 base, ALL FIVE questions matched NOTHING. Full
    # record: MECHANISMS_TRIGGER_AUDIT.md.
    mech_audit_cases = {
        "qit_over_cap":
            "My dad's pension and Social Security add up to "
            "more than Medicaid allows each month, so he "
            "doesn't qualify. Is there anything that can be "
            "done so he can still get help paying for his care?",
        "backpay_nine_months":
            "Social Security owed me money from earlier months "
            "and just paid it all at once. How long before that "
            "money counts against me?",
        "snap_lump_sum":
            "I just got one big check for months I was owed. "
            "Will that count as income for my food stamps?",
        "csra_half_clamp":
            "If I go into a nursing home how much of our "
            "savings can my wife keep?",
        "difficulty_of_care_exclusion":
            "My wife gets paid to take care of me at home. "
            "Does that money count if she applies for Healthy "
            "Indiana?",
    }
    for eid, q in mech_audit_cases.items():
        got = [m["id"] for m in mod._mechanism_matches(q)]
        check(f"v22.2 mech audit: {eid} hits alone on its "
              f"plain question", got == [eid], f"{got}")
    got = [m["id"] for m in mod._mechanism_matches(
        "What is the community spouse resource allowance "
        "for 2026?")]
    check("v22.2 mech audit: CSRA stemming probe (singular "
          "'resource' token)", got == ["csra_half_clamp"],
          f"{got}")
    got = [m["id"] for m in mod._mechanism_matches(
        "My father gets $3,400 a month from a pension and "
        "Social Security, which puts him over the Medicaid "
        "income limit. He lives in Indiana. Is there anything "
        "that can be done so he can qualify?")]
    check("v22.2 mech audit: Miller scenario still hits "
          "qit_over_cap alone", got == ["qit_over_cap"],
          f"{got}")
    got = [m["id"] for m in mod._mechanism_matches(
        "I'm on Indiana Medicaid with an HCBS waiver, my wife "
        "is my paid caregiver through Structured Family "
        "Caregiving, her father passed and she'll inherit "
        "about $60,000 once the estate settles; does it affect "
        "my eligibility and what should we do before the "
        "money arrives?")]
    check("v22.2 mech audit: inheritance scenario still "
          "matches no mechanism", got == [], f"{got}")

    # ---------------- WS3a: gap log ------------------------
    gap_path = os.path.join(tmp, "gap_test.jsonl")
    mod.GAP_LOG_PATH = gap_path
    mod._events_reset()
    ctx = mod._ask_ctx()
    rec = mod.log_gap("Will the 2027 standards change this?",
                      "idk", "judging", ["nothing found"])
    check("WS3a gap row carries the ask id",
          rec.get("ask_id") == ctx.ask_id,
          f"ask_id={rec.get('ask_id')}")
    check("WS3a class_guess newer_edition for a future year",
          rec.get("class_guess") == "newer_edition")
    rec2 = mod.log_gap("What does the manual say?", "idk",
                       "sufficiency", ["no governing text"])
    check("WS3a class_guess missing_document for sufficiency",
          rec2.get("class_guess") == "missing_document")
    with open(gap_path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    check("WS3a rows persisted with both new fields",
          len(rows) == 2 and all(
              "ask_id" in r and "class_guess" in r
              for r in rows))

    # ---------------- WS4b: seat_skip ----------------------
    # v23: retargeted from the deleted judge_nim1 seat to
    # judge_seat2 (the same skip-reason machinery serves the
    # two-seat panel's one seat-machinery job).
    mod._events_reset()
    mod._note_seat_skips("judge_seat2", None, [])
    skips = [e for e in mod._ask_ctx().events
             if e.get("kind") == "seat_skip"]
    check("WS4b seat_skip events emitted for an unserved pick",
          len(skips) >= 1,
          f"{len(skips)} events; first reason: "
          f"{skips[0]['reason'] if skips else None}")
    check("WS4b skip reason names the cause (no key on this "
          "machine)", bool(skips) and "no key" in
          skips[0]["reason"])
    n0 = len(mod._ask_ctx().events)
    mod._note_seat_skips("judge_seat2", None, [])
    check("WS4b seat_skip dedupes per ask",
          len(mod._ask_ctx().events) == n0)

    # ---------------- WS1a: rescue ranking -----------------
    corp = mod.get_corpus("both")
    hits, _t = mod._rescue_search(
        "What is Indiana's Medicaid transfer penalty "
        "divisor?", corp, limit=4)
    check("WS1a rescue ranks IHCPPM 3006.00.00 first",
          bool(hits) and "3006.00.00" in hits[0]["title"],
          hits[0]["title"][:60] if hits else "no hits")
    hits, _t = mod._rescue_search(
        "Under Indiana Medicaid fee-for-service, how often "
        "are eyeglasses covered for adults?", corp, limit=4)
    check("WS1a rescue surfaces the Vision module",
          any("Vision Services" in h["title"] for h in hits),
          "; ".join(h["title"][:40] for h in hits))

    # ---------------- WS4a: NIM deadlines ------------------
    mod._nim_key_for = lambda env, file: "test-key"
    calls = []

    def fake_post(host, path, body, headers, timeout=60):
        calls.append(timeout)
        time.sleep(min(timeout, 5.0))
        raise TimeoutError("read timed out")

    mod._https_post = fake_post

    calls.clear()
    t0 = time.monotonic()
    err = ""
    try:
        mod._nim_text("https://example.invalid/v1", "E", "F",
                      "m", "s", "u", timeout=30,
                      provider="nim")
    except RuntimeError as e:
        err = str(e)
    wall = time.monotonic() - t0
    check("WS4a no deadline: retry doubles the timeout "
          "(pre-v21 shape, kept as the control)",
          len(calls) == 2 and calls == [30, 30],
          f"calls={calls}")

    calls.clear()
    t0 = time.monotonic()
    err = ""
    try:
        mod._nim_text("https://example.invalid/v1", "E", "F",
                      "m", "s", "u", timeout=30,
                      provider="nim",
                      deadline=time.monotonic() + 0.3)
    except RuntimeError as e:
        err = str(e)
    wall = time.monotonic() - t0
    check("WS4a deadline cuts the leg at its budget "
          "(one attempt, ~0.3s, not 60s)",
          len(calls) == 1 and calls[0] <= 0.31 and wall < 2.0,
          f"calls={calls} wall={wall:.2f}s")
    check("WS4a deadline error names the spent budget",
          "deadline" in err, err[:90])

    calls.clear()
    err = ""
    try:
        mod._nim_text("https://example.invalid/v1", "E", "F",
                      "m", "s", "u", timeout=0.2,
                      provider="nim",
                      deadline=time.monotonic() + 10)
    except RuntimeError as e:
        err = str(e)
    check("WS4a retry survives when budget remains",
          len(calls) == 2 and all(c == 0.2 for c in calls),
          f"calls={calls}")

    # ---------------- v23: nimx checks REMOVED ---------------
    # The two v22.1 nimx checks (deadline threading + budget
    # shed on the divergence oracle's B-draft leg) are gone
    # with the oracle itself (v23 WS3.1). The deadline
    # discipline they proved now rides every remaining leg
    # via _leg_timeout + deadline threading in _judge_seat_text
    # and _generate; the v23 panel-shape checks in
    # offline_checks_v23.py assert the replacement behavior
    # (seat legs shed at the budget edge; relief legs are
    # deadline-bounded). Listed in v23/STATUS.md.

    # ---------------- v22.1 secondary (b): shape guard ---------
    memo = ("Corrections and citations: the previous draft "
            "stated the monthly maintenance needs allowance "
            "without a citation. The draft should add citations "
            "for each claim and correct the figures.")
    check("v22.1 shape guard rejects a corrections memo",
          mod._is_memo_shaped_draft(memo))
    cited = ("The community spouse may keep one half of the "
             "couple's countable savings, clamped between "
             "$32,532 and $162,660 [1].")
    check("v22.1 shape guard accepts a normal cited answer",
          not mod._is_memo_shaped_draft(cited))
    plain = ("The community spouse may keep one half of the "
             "couple's countable savings, no less than $32,532 "
             "and no more than $162,660.")
    check("v22.1 shape guard accepts an uncited plain answer "
          "(citation gate owns that failure)",
          not mod._is_memo_shaped_draft(plain))

    # --- Cap raise (operator decision, 2026-10-14) --------------
    check("caps: daily ledger cap is $10.00 (v21 raise)",
          mod.OPENROUTER_DAILY_CAP_USD == 10.0,
          f"got {mod.OPENROUTER_DAILY_CAP_USD}")
    check("caps: per-ask ceiling is $1.00 (v21 raise)",
          mod.PER_ASK_CEILING_USD == 1.0,
          f"got {mod.PER_ASK_CEILING_USD}")

    # --- WS1a title channel (probe-set grounding) ---------------
    # "divisor" appears in the IHCPPM 3006.00.00 document's TITLE
    # only; its chunk text never uses the word. The rescue must
    # still rank the section first on the bare term, which is
    # only possible through the title match channel.
    doc3006 = next(d for d in mod.CORPUS_IN
                   if "3006.00.00" in (d.get("title") or ""))
    body_has = any("divisor" in ch.lower()
                   for ch in mod._doc_chunks(doc3006))
    check("WS1a title channel: §3006 chunk text alone never "
          "says 'divisor'", not body_has)
    check("WS1a title channel: the doc title does",
          "divisor" in (doc3006.get("title") or "").lower())
    r, _rterms = mod._rescue_search(
        "What is Indiana's Medicaid transfer penalty divisor?",
        corpus=mod.CORPUS_IN)
    check("WS1a title channel: rescue ranks §3006 first on "
          "the title-only term",
          bool(r) and "3006.00.00" in (r[0].get("title") or ""),
          (r[0].get("title") or "")[:60] if r else "no hits")

    print()
    if FAILURES:
        print(f"summary: FAILURES: {FAILURES}")
        return 1
    print("summary: all offline checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
