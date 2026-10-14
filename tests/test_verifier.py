"""Offline proofs for the v18 section A fail-open fixes and
the section C panel/badge rules, plus F8/F12/F13/F14/F15/F16/
F19/F20 and R3/R4/R7 at the function and pipeline level.

Everything is mocked (judge transports, generators, retrieval):
no network call of any kind is made. The server module is
loaded fresh from a temp working directory holding tiny
stand-in corpora and a dummy key file (the pattern from
tests/test_hard_stop.py).

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server", "fssa_search_server.py")

HIT_A = {"title": "Doc A", "url": "https://example.gov/a",
         "excerpt": "The waiver covers pest control services "
                    "for members."}
HIT_B = {"title": "Doc B", "url": "https://example.gov/b",
         "excerpt": "The fee is $900 and the deposit is $10."}


def _load_server(workdir):
    for name in ("corpus.json", "corpus_federal.json"):
        with open(os.path.join(workdir, name), "w",
                  encoding="utf-8") as f:
            json.dump([], f)
    with open(os.path.join(workdir, "openrouter_key.txt"), "w",
              encoding="utf-8") as f:
        f.write("sk-test-dummy-not-a-real-key")
    cwd = os.getcwd()
    argv = sys.argv
    os.chdir(workdir)
    sys.argv = ["fssa_search_server.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_v18_verifier", os.path.abspath(SERVER))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
        sys.argv = argv
    return mod


class _Patched(unittest.TestCase):
    """Base: fresh module state per test + a patch/restore kit."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fssa_v18_verifier_")
        cls.mod = _load_server(cls.dir)

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        m = self.mod
        m._ASK_CTX_VAR.set(m._AskContext())
        with m._VERDICT_LOCK:
            m._VERDICT_CACHE.clear()
        self._saved = {}

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(self.mod, name, fn)
        os.chdir(self._cwd)

    def patch(self, name, fn):
        if name not in self._saved:
            self._saved[name] = getattr(self.mod, name)
        setattr(self.mod, name, fn)

    def ctx(self, **over):
        base = {
            "question": "Does the waiver cover pest control?",
            "unit_q": "Does the waiver cover pest control?",
            "hits": [dict(HIT_A)], "engine": "nim",
            "draft_model": "qwen3.5:9b", "instructions": "",
            "registry_entries": [], "mechanisms": [],
            "csra_comp": None, "allowed_extra": [], "hard": False,
            "reg_in_instructions": False, "corp": None,
            "deadline": time.monotonic() + 600, "diverge": False,
            "explicit_model": False, "local_mode": False,
            "fresh": False, "full_question": None,
        }
        base.update(over)
        return base

    def fake_generate(self, draft):
        def _gen(engine, model, question, hits, instructions):
            return draft, "prompt", "fake/drafter-x", False, False
        return _gen

    def fake_jr(self, claims, passed=True, record=None,
                panel_state="full"):
        def _jr(question, hits, answer, hard, mech_block="",
                **kw):
            if record is not None:
                record.append({"question": question,
                               "answer": answer, "kw": kw})
            return {"mode": "panel", "claims": claims,
                    "flash_used": False, "pass": passed,
                    "errors": [], "refute_ms": 0,
                    "panel_state": panel_state,
                    "panel": {"judges": ["lite", "nim", "nim2"]}}
        return _jr

    @staticmethod
    def claim(text, final="supported", coverage=("google",
                                                 "deepseek"),
              **verdicts):
        c = {"text": text, "final": final, "lite": None,
             "nim": None, "nim2": None, "local": None,
             "flash": None, "meta": False, "load_bearing": True,
             "origin": "extracted", "coverage": list(coverage),
             "models": {"lite": "gemini-3.5-flash-lite",
                        "nim": "deepseek/deepseek-chat-v3.1"},
             "refutations": [], "sources": [0],
             "notes": []}
        c.update(verdicts)
        return c


class TestF1RefutationStands(_Patched):
    """F1: an unruled refutation 'contradicted' is the final,
    even when every seat said supported (v17.3 shipped it)."""

    CLAIM = "The waiver covers pest control"

    def _install(self, refute_verdict):
        m = self.mod
        self.patch("_gemini_text", lambda model, system, user:
                   json.dumps({"claims": [{
                       "text": self.CLAIM, "load_bearing": True,
                       "verdict": "supported", "note": "",
                       "sources": [1]}]}))

        def seat(job, system, user):
            m._note_seat(job, "deepseek/deepseek-chat-v3.1"
                         if job == "judge_nim1" else
                         "qwen/qwen3-235b-a22b-instruct-2507",
                         False)
            v = refute_verdict if \
                "Possibly contradicting material" in user \
                else "supported"
            return json.dumps({"verdicts": [{
                "i": 1, "verdict": v, "note": ""}], "missed": []})
        self.patch("_judge_seat_text", seat)

        def refute(corp, hits, claims, mechanisms=None):
            for c in claims:
                c["refutations"] = [dict(HIT_B)]
            return 3
        self.patch("_refutation_retrieve", refute)
        self.patch("_flash_arbitrate",
                   lambda q, h, c, idx: None)  # Flash cannot rule

    def test_refuted_claim_fails_closed(self):
        self._install("contradicted")
        jr = self.mod._judge_round(
            "q-f1-refuted", [dict(HIT_A)], "draft [1]", True, "",
            corp=None, mechanisms=[], extra_claims=[])
        self.assertEqual(jr["claims"][0]["final"], "contradicted")
        self.assertFalse(jr["pass"])

    def test_affirmed_refutation_check_ships(self):
        self._install("supported")
        jr = self.mod._judge_round(
            "q-f1-affirmed", [dict(HIT_A)], "draft [1]", True, "",
            corp=None, mechanisms=[], extra_claims=[])
        self.assertEqual(jr["claims"][0]["final"], "supported")
        self.assertTrue(jr["pass"])


class TestF12VerdictCache(_Patched):
    """F12: one key both ways; scoped evidence; the fresh
    primary's contradiction beats the cache; fresh asks skip;
    the put requires two families (or Flash)."""

    CLAIM = "The waiver covers pest control"

    def _key(self, q, hits, sources=(0,)):
        return self.mod._verdict_cache_key(
            q, hits, {"text": self.CLAIM, "sources": list(sources)},
            "")

    def test_key_scoped_to_the_claims_own_evidence(self):
        h1 = [dict(HIT_A), dict(HIT_B)]
        h2 = [dict(HIT_A), dict(HIT_B, excerpt="CHANGED $1.")]
        self.assertEqual(self._key("q", h1, (0,)),
                         self._key("q", h2, (0,)))
        self.assertNotEqual(self._key("q", h1, (1,)),
                            self._key("q", h2, (1,)))

    def test_key_binds_question_and_registry(self):
        h = [dict(HIT_A)]
        self.assertNotEqual(self._key("q one", h),
                            self._key("q two", h))
        before = self._key("q one", h)
        saved = self.mod._REGISTRY_HASH
        try:
            self.mod._REGISTRY_HASH = "edited-registry"
            after = self._key("q one", h)
        finally:
            self.mod._REGISTRY_HASH = saved
        self.assertNotEqual(before, after)

    def _round(self, lite_verdict, seats_down, use_cache=True,
               question="q-f12"):
        m = self.mod
        self.patch("_gemini_text", lambda model, system, user:
                   json.dumps({"claims": [{
                       "text": self.CLAIM, "load_bearing": False,
                       "verdict": lite_verdict, "note": "",
                       "sources": [1]}]}))
        if seats_down:
            def seat(job, system, user):
                raise RuntimeError("seat down")
        else:
            def seat(job, system, user):
                m._note_seat(job, "deepseek/deepseek-chat-v3.1"
                             if job == "judge_nim1" else
                             "qwen/qwen3-235b-a22b-instruct-2507",
                             False)
                return json.dumps({"verdicts": [{
                    "i": 1, "verdict": "supported", "note": ""}],
                    "missed": []})
        self.patch("_judge_seat_text", seat)
        self.patch("_refutation_retrieve",
                   lambda corp, hits, claims, mechanisms=None: 0)
        return m._judge_round(question, [dict(HIT_A)], "d [1]",
                              False, "", corp=None, mechanisms=[],
                              extra_claims=[], use_cache=use_cache)

    def test_fresh_contradiction_beats_cached_supported(self):
        key = self._key("q-f12", [dict(HIT_A)])
        self.mod._verdict_cache_put(key, {
            "verdict": "supported", "coverage": ["google"],
            "models": {}})
        jr = self._round("contradicted", seats_down=True)
        self.assertEqual(jr["claims"][0]["final"], "contradicted")

    def test_use_cache_false_skips_the_cache(self):
        key = self._key("q-f12-nocache", [dict(HIT_A)])
        self.mod._verdict_cache_put(key, {
            "verdict": "supported", "coverage": ["google"],
            "models": {}})
        jr = self._round("not_addressed", seats_down=True,
                         use_cache=False, question="q-f12-nocache")
        self.assertEqual(jr["claims"][0]["final"], "not_addressed")

    def test_put_requires_two_families(self):
        key = self._key("q-f12-put", [dict(HIT_A)])
        self._round("supported", seats_down=True,
                    question="q-f12-put")
        self.assertIsNone(self.mod._verdict_cache_get(key))
        self._round("supported", seats_down=False,
                    question="q-f12-put")
        got = self.mod._verdict_cache_get(key)
        self.assertIsNotNone(got)
        self.assertEqual(got["verdict"], "supported")
        self.assertGreaterEqual(len(got["coverage"]), 2)

    def test_cached_claim_is_refutation_checked(self):
        # F12c: the join's invalidation branch is alive — a
        # cached claim whose refutation returns material loses
        # the cached verdict and is re-judged.
        m = self.mod
        key = self._key("q-f12c", [dict(HIT_A)])
        m._verdict_cache_put(key, {"verdict": "supported",
                                   "coverage": ["google", "deepseek"],
                                   "models": {}})
        self.patch("_gemini_text", lambda model, system, user:
                   json.dumps({"claims": [{
                       "text": self.CLAIM, "load_bearing": True,
                       "verdict": "not_addressed", "note": "",
                       "sources": [1]}]}))

        def seat(job, system, user):
            m._note_seat(job, "deepseek/deepseek-chat-v3.1"
                         if job == "judge_nim1" else
                         "qwen/qwen3-235b-a22b-instruct-2507", False)
            v = "contradicted" if \
                "Possibly contradicting material" in user \
                else "supported"
            return json.dumps({"verdicts": [{
                "i": 1, "verdict": v, "note": ""}], "missed": []})
        self.patch("_judge_seat_text", seat)

        def refute(corp, hits, claims, mechanisms=None):
            for c in claims:
                c["refutations"] = [dict(HIT_B)]
            return 3
        self.patch("_refutation_retrieve", refute)
        self.patch("_flash_arbitrate", lambda q, h, c, i: None)
        jr = m._judge_round("q-f12c", [dict(HIT_A)], "d [1]", True,
                            "", corp=None, mechanisms=[],
                            extra_claims=[])
        self.assertEqual(jr["claims"][0]["final"], "contradicted")


class TestF2BadgeCoverage(_Patched):
    """F2/section C: the badge follows per-claim family
    coverage, not the round's mode string."""

    DRAFT = "The waiver covers pest control [1]."

    def _run(self, coverage, **ctx_over):
        record = []
        self.patch("_generate", self.fake_generate(self.DRAFT))
        self.patch("_judge_round", self.fake_jr(
            [self.claim("The waiver covers pest control",
                        coverage=coverage)], record=record))
        unit = self.mod._run_unit(self.ctx(**ctx_over))
        return unit, record

    def test_single_family_coverage_degrades_the_badge(self):
        unit, _ = self._run(("google",))
        self.assertEqual(unit["badge"], "verified_single")
        self.assertIn("Judges:", unit["judging"]["annotation"])
        self.assertIn("one judge family only",
                      unit["judging"]["annotation"])

    def test_two_family_coverage_verifies(self):
        unit, _ = self._run(("google", "deepseek"))
        self.assertEqual(unit["badge"], "verified")

    def test_local_and_fresh_thread_into_the_round(self):
        _unit, record = self._run(("google", "deepseek"),
                                  local_mode=True, fresh=True)
        self.assertTrue(record[0]["kw"]["local_only"])
        self.assertFalse(record[0]["kw"]["use_cache"])

    def test_f13_judges_see_the_full_question(self):
        _unit, record = self._run(
            ("google", "deepseek"),
            full_question="Full question with two parts?",
            unit_q="part two?")
        self.assertTrue(record[0]["question"].startswith(
            "Full question with two parts?"))
        self.assertIn("part two?", record[0]["question"])


class TestF3FlashExclusions(_Patched):
    """F3: Flash never rules on claims a majority of seats
    contradicted, nor on conflict claims; it still rules a
    genuine split."""

    DRAFT = "The waiver covers pest control [1]."

    def _run(self, claim):
        calls = []
        self.patch("_generate", self.fake_generate(self.DRAFT))
        self.patch("_judge_round",
                   self.fake_jr([claim], passed=False))

        def flash(q, hits, claims, idx):
            calls.append(list(idx))
            return {i: "supported" for i in idx}
        self.patch("_flash_arbitrate", flash)
        unit = self.mod._run_unit(self.ctx())
        return unit, calls

    def test_majority_contradicted_never_reaches_flash(self):
        c = self.claim("X", final="contradicted",
                       lite="contradicted", nim="contradicted")
        unit, calls = self._run(c)
        self.assertEqual(calls, [])
        self.assertEqual(unit["badge"], "idk")

    def test_genuine_split_still_reaches_flash(self):
        c = self.claim("X", final="not_addressed",
                       lite="contradicted", nim="not_addressed")
        unit, calls = self._run(c)
        self.assertEqual(len(calls), 1)
        self.assertEqual(unit["badge"], "verified")


class TestF6ArithmeticGate(_Patched):
    """F6: the arithmetic gate runs on the ladder (and, per
    TestF4, on the capped path), as pure code."""

    def test_pure_helper(self):
        m = self.mod
        self.assertTrue(m._arithmetic_errors(
            "The total is ($900 + $10 = $950)."))
        self.assertFalse(m._arithmetic_errors(
            "The total is ($900 + $10 = $910)."))

    def _run(self, draft):
        self.patch("_generate", self.fake_generate(draft))
        self.patch("_judge_round", self.fake_jr(
            [self.claim("The total is computed")]))
        return self.mod._run_unit(self.ctx(hits=[dict(HIT_B)]))

    def test_wrong_equation_blocks_the_ship(self):
        unit = self._run("The total is ($900 + $10 = $950) [1].")
        self.assertEqual(unit["badge"], "idk")

    def test_right_equation_ships(self):
        unit = self._run("The total is ($900 + $10 = $910) [1].")
        self.assertEqual(unit["badge"], "verified")


class TestF7Strip(_Patched):
    """F7: the strip re-judges before shipping; limiting
    sentences are never strip-eligible; span mapping works."""

    HITS = [{"title": "Doc A", "url": "https://example.gov/a",
             "excerpt": "The waiver covers pest control; the "
                        "copay is $0."}]
    DRAFT = ("The waiver covers pest control with a $0 copay [1]. "
             "The plan must cover pest control.")

    def test_stripped_draft_is_rejudged_before_shipping(self):
        record = []
        self.patch("_generate", self.fake_generate(self.DRAFT))
        self.patch("_judge_round", self.fake_jr(
            [self.claim("The waiver covers pest control")],
            record=record))
        unit = self.mod._run_unit(self.ctx(hits=self.HITS))
        self.assertEqual(unit["badge"], "verified")
        self.assertNotIn("must cover", unit["answer"])
        self.assertTrue(any("must cover" not in r["answer"]
                            for r in record))
        self.assertGreaterEqual(len(record), 2)

    def test_limiting_sentence_is_never_an_aside(self):
        text = ("The copay is $0 [1]. "
                "The limit applies only if enrolled.")
        parsed = self.mod._parse_citations(text, self.HITS)
        self.assertEqual(parsed["uncited_lb"],
                         ["The limit applies only if enrolled."])
        new, stripped = self.mod._strip_uncited_asides(
            text, parsed, [self.claim("The copay is $0")])
        self.assertEqual(stripped, [])
        self.assertIsNone(new)

    def test_span_mapping(self):
        self.assertTrue(self.mod._sentence_maps_to_claim(
            "Some sentence text.",
            [{"text": "unrelated claim",
              "span": "Some sentence text."}]))


class TestF4AllowList(_Patched):
    """F4: the capped Partial is an allow-list: only sentences
    that ARE supported claims ship; the arithmetic gate runs."""

    def test_only_supported_claim_sentences_ship(self):
        fees = {"title": "Fees", "url": "u9",
                "excerpt": "The monthly limit is $100 and the "
                           "yearly cap is $200."}
        jr = {"claims": [
            self.claim("The monthly limit is $100"),
            self.claim("The yearly cap is $200",
                       final="contradicted")]}
        draft = ("The monthly limit is $100 [1]. "
                 "The weather is nice today. "
                 "The yearly cap is $200 [1].")
        out = self.mod._assemble_capped_partial(
            draft, jr, "q?", [fees], [])
        self.assertIsNotNone(out)
        text, min_cov = out
        self.assertEqual(text, "The monthly limit is $100 [1].")
        self.assertEqual(min_cov, 2)

    def test_arithmetic_error_kills_the_assembly(self):
        jr = {"claims": [
            self.claim("The total is ($900 + $10 = $950)")]}
        draft = "The total is ($900 + $10 = $950) [1]."
        out = self.mod._assemble_capped_partial(
            draft, jr, "q?", [dict(HIT_B)], [])
        self.assertIsNone(out)

    def test_nothing_supported_returns_none(self):
        jr = {"claims": [self.claim("Some claim",
                                     final="not_addressed")]}
        out = self.mod._assemble_capped_partial(
            "Some claim [1].", jr, "q?", [dict(HIT_A)], [])
        self.assertIsNone(out)


class TestF5Figures(_Patched):
    """F5: provenance is scoped to the sentence's own cited
    excerpts; equations cannot self-ground; bare amounts are
    checked; tolerance is tight."""

    def test_figure_in_an_uncited_doc_does_not_ground(self):
        hits = [{"title": "A", "url": "u1",
                 "excerpt": "The amount $910.80 appears here."},
                {"title": "B", "url": "u2",
                 "excerpt": "Nothing relevant."}]
        _a, ung = self.mod._check_figures(
            "The monthly amount is $910.80 per month [2].",
            "q?", hits)
        self.assertIn(910.80, ung)

    def test_equation_operand_must_be_in_scope(self):
        hits = [{"title": "A", "url": "u1",
                 "excerpt": "The limit is $2,982."}]
        _a, ung = self.mod._check_figures(
            "The headroom is ($2,982 - $2,071.20 = $910.80) [1].",
            "q?", hits)
        self.assertIn(2071.20, ung)
        hits[0]["excerpt"] = "The limit is $2,982 and pay is $2,071.20."
        _a, ung = self.mod._check_figures(
            "The headroom is ($2,982 - $2,071.20 = $910.80) [1].",
            "q?", hits)
        self.assertEqual(ung, [])

    def test_bare_amounts_are_checked(self):
        hits = [{"title": "A", "url": "u1", "excerpt": "No figures."}]
        _a, ung = self.mod._check_figures(
            "You pay 910 dollars [1].", "q?", hits)
        self.assertIn(910.0, ung)

    def test_tolerance_is_tight(self):
        hits = [{"title": "A", "url": "u1",
                 "excerpt": "The max is $4,066.50."}]
        _a, ung = self.mod._check_figures(
            "The max is $4,066.505 [1].", "q?", hits)
        self.assertEqual(ung, [])
        _a, ung = self.mod._check_figures(
            "The max is $4,066.53 [1].", "q?", hits)
        self.assertIn(4066.53, ung)


class TestF8EvidenceParity(_Patched):
    """F8: judges see 3000 chars of a claim's evidence, the
    v17.3 1000-char truncation is gone."""

    def test_claim_evidence_uses_3000(self):
        hits = [{"title": "A", "url": "u1",
                 "excerpt": "0123456789" * 250}]  # 2500 chars
        ev = self.mod._claim_evidence(
            hits, {"sources": [0], "text": "c"})
        self.assertIn("0123456789" * 200, ev)

    def test_judge_excerpts_use_3000(self):
        hits = [{"title": "A", "url": "u1",
                 "excerpt": "0123456789" * 250}]
        ex = self.mod._judge_excerpts(hits)
        self.assertIn("0123456789" * 200, ex)


class TestF15SpousePriming(_Patched):
    """F15: spouse priming is conditional on the question."""

    def test_gate(self):
        m = self.mod
        self.assertFalse(m._question_involves_spouse(
            "Does the waiver cover pest control?"))
        self.assertTrue(m._question_involves_spouse(
            "Can my wife be my caregiver?"))
        self.assertTrue(m._question_involves_spouse(
            "What is structured family caregiving?"))

    def test_prompt_block_only_for_spouse_questions(self):
        m = self.mod
        self.assertNotIn("SPOUSE INCOME",
                         m._system_prompt_for(
                             "Does the waiver cover pest control?"))
        self.assertIn("SPOUSE INCOME",
                      m._system_prompt_for(
                          "Can my wife be my caregiver?"))
        self.assertNotIn("SPOUSE INCOME", m.SYSTEM_PROMPT)

    def test_bridge_no_longer_triggers_on_caregiver_alone(self):
        for tokens, phrases, *_rest in self.mod._TOPIC_BRIDGES:
            if "structured family caregiving" in phrases:
                self.assertNotIn("caregiver", tokens)
                self.assertNotIn("caregiving", tokens)


class TestF16Conflicts(_Patched):
    """F16: non-dollar conflicts (the ABLE age), code-only
    resolution, and the qit claim-3 de-conflation."""

    ABLE_HIT = {"title": "ABLE accounts", "url": "u1",
                "excerpt": "An ABLE account requires disability "
                           "onset before age 26 for eligibility."}

    def test_age_conflict_scans(self):
        recs = self.mod._conflict_scan([dict(self.ABLE_HIT)])
        ages = [r for r in recs if r.get("kind") == "age"]
        self.assertEqual(len(ages), 1)
        self.assertEqual(ages[0]["registry_value"], 46.0)
        self.assertEqual(ages[0]["corpus_value"], 26.0)

    def test_conflict_claims_both_values(self):
        recs = self.mod._conflict_scan([dict(self.ABLE_HIT)])
        _c, forced = self.mod._conflict_claims(
            recs, "Eligibility starts at age 46.")
        self.assertTrue(forced)
        _c, forced = self.mod._conflict_claims(
            recs, "One source says age 46; the manual says age 26.")
        self.assertFalse(forced)

    def test_qit_claim3_distinguishes_payback_from_recovery(self):
        mech = next(m for m in self.mod.MECHANISMS
                    if m["id"] == "qit_over_cap")
        claim3 = mech["canonical_claims"][2]
        self.assertIn("REQUIRED TERM OF THE TRUST", claim3)
        self.assertIn("estate recovery", claim3)
        self.assertNotIn("is subject to Medicaid estate "
                         "recovery", claim3)

    def test_conflict_gate_is_code_only(self):
        record = []
        self.patch("_generate", self.fake_generate(
            "ABLE eligibility starts at age 46 [1]."))
        self.patch("_judge_round", self.fake_jr(
            [self.claim("ABLE eligibility starts at age 46")],
            record=record))
        unit = self.mod._run_unit(
            self.ctx(hits=[dict(self.ABLE_HIT)],
                     question="Who can open an ABLE account?"))
        self.assertEqual(unit["badge"], "idk")
        for r in record:
            for ec in r["kw"].get("extra_claims") or ():
                self.assertNotEqual(ec.get("origin"), "conflict")


class TestF20AndR3Parsing(_Patched):
    """F20: bullets are sentences; markers read anywhere; a
    URL credits every chunk from it. R3: modal/future factual
    shapes are load-bearing."""

    def test_bullets_split(self):
        s = self.mod._split_sentences(
            "- First point [1]\n- Second point [2]")
        self.assertEqual(len(s), 2)

    def test_mid_sentence_marker_counts(self):
        hits = [dict(HIT_A), dict(HIT_B)]
        p = self.mod._parse_citations(
            "The deposit [2] is ten dollars.", hits)
        self.assertEqual(p["sentences"][0]["sources"], [1])

    def test_url_credits_all_chunks(self):
        hits = [{"title": "A", "url": "https://x.gov/d",
                 "excerpt": "one"},
                {"title": "A2", "url": "https://x.gov/d",
                 "excerpt": "two"}]
        p = self.mod._parse_citations(
            "See [Doc](https://x.gov/d) for the rule.", hits)
        self.assertEqual(sorted(p["sentences"][0]["sources"]),
                         [0, 1])

    def test_modal_shapes_are_load_bearing(self):
        p = self.mod._parse_citations(
            "The trust will hold the money. "
            "You can appeal the decision.", [dict(HIT_A)])
        self.assertEqual(len(p["uncited_lb"]), 2)


class TestR4Quantities(_Patched):
    """R4: a period confusable fails only in a sentence that
    asserts it as the rule, for a mechanism in scope;
    disclaimers and out-of-scope mentions pass."""

    MECH = {"id": "test_mech",
            "period": {"count": 9,
                       "note": "the exclusion lasts nine months",
                       "confusables": ("six months",)}}

    def test_assertion_fails(self):
        out = self.mod._check_quantities(
            "The benefits are excluded for six months.", "q?",
            mechanisms=[self.MECH])
        self.assertEqual(len(out), 1)

    def test_disclaimer_passes(self):
        out = self.mod._check_quantities(
            "The six months figure is not the exclusion period.",
            "q?", mechanisms=[self.MECH])
        self.assertEqual(out, [])

    def test_out_of_scope_mechanism_never_fails(self):
        out = self.mod._check_quantities(
            "The benefits are excluded for six months.", "q?",
            mechanisms=[])
        self.assertEqual(out, [])


class TestSeatPlan(_Patched):
    """Section C: atomic panel planning with family identity;
    the drafting family is excluded; seat 2 is the Qwen
    instruct; Qwen is out of seat 1."""

    def test_seat2_constant_and_seat1_has_no_qwen(self):
        m = self.mod
        # The slug was corrected at the v18 battery gate (the
        # plan-text "-instruct-2507" slug does not exist; the
        # real catalog slug is the one below).
        self.assertEqual(m.JUDGE_SEAT2_MODEL,
                         "qwen/qwen3-235b-a22b-2507")
        for model, _slot in m.SEATS["judge_nim1"]["candidates"]:
            self.assertNotIn("qwen", model)

    def test_plan_excludes_the_drafting_family(self):
        m = self.mod
        ctx = m._ask_ctx()
        ctx.draft_family = "deepseek"
        m._plan_panel()
        plan = ctx.panel_plan
        self.assertIsNone(plan.get("judge_nim1"))
        self.assertEqual(plan["judge_nim2"][0],
                         m.JUDGE_SEAT2_MODEL)

    def test_plan_seats_are_distinct_families(self):
        m = self.mod
        ctx = m._ask_ctx()
        m._plan_panel()
        plan = ctx.panel_plan
        fams = {m._model_family(v[0]) for v in plan.values() if v}
        self.assertEqual(len(fams), len([v for v in plan.values()
                                         if v]))


class TestCacheMutation(_Patched):
    """The external-review cache bug: hybrid_search hands out
    copies, so callers cannot pollute the shared cache entry."""

    CORP = [{"title": "Pest Control Doc", "url": "https://x.gov/p",
             "text": "The Health and Wellness Waiver covers pest "
                     "control for members. Pest control is a "
                     "covered service."}]

    def test_mutation_does_not_pollute(self):
        m = self.mod
        r1 = m.hybrid_search("does the waiver cover pest control",
                              corpus=[dict(d) for d in self.CORP],
                              n=3)
        self.assertTrue(r1)
        r1[0]["title"] = "MUTATED"
        r1.append({"title": "EXTRA", "url": "z", "excerpt": "e"})
        r2 = m.hybrid_search("does the waiver cover pest control",
                              corpus=[dict(d) for d in self.CORP],
                              n=3)
        self.assertEqual([h["title"] for h in r2],
                         ["Pest Control Doc"])


class TestR5ArbitrationPrompt(_Patched):
    """R5: Flash sees EVERY verdict on record, labeled with
    the model that actually served the seat, plus the
    refutation verdict and the refutation material, in both
    the scoped and unscoped prompt paths."""

    def _arbitrate(self, sources):
        m = self.mod
        captured = {}
        self.patch("_flash_spend", lambda: True)

        def fake_gemini(model, system, user):
            captured["user"] = user
            return json.dumps({"rulings": [
                {"i": 1, "verdict": "supported"}]})
        self.patch("_gemini_text", fake_gemini)
        ctx = m._ask_ctx()
        ctx.served_models.update({
            "judge_nim1": "deepseek/deepseek-chat-v3.1",
            "judge_nim2": "qwen/qwen3-235b-a22b-instruct-2507"})
        claim = {"text": "The waiver covers pest control",
                 "lite": "supported", "nim": "contradicted",
                 "nim2": "not_addressed", "local": None,
                 "refute_verdict": "contradicted",
                 "notes": ["a judge note"], "sources": sources,
                 "refutations": [{
                     "title": "RefDoc", "url": "https://x.gov/r",
                     "excerpt": "REFUTATION-SOURCE-TEXT here."}]}
        hits = [dict(HIT_A)]
        out = m._flash_arbitrate("q?", hits, [claim], [0])
        self.assertEqual(out, {0: "supported"})
        return captured["user"]

    def _assert_prompt(self, user):
        self.assertIn("deepseek/deepseek-chat-v3.1", user)
        self.assertIn("qwen/qwen3-235b-a22b-instruct-2507", user)
        self.assertIn("Refutation check: contradicted", user)
        self.assertIn("REFUTATION-SOURCE-TEXT", user)

    def test_scoped_prompt(self):
        self._assert_prompt(self._arbitrate([0]))

    def test_unscoped_prompt(self):
        self._assert_prompt(self._arbitrate([]))


class TestF14PointRelevance(_Patched):
    """F14 (relevance half): a divergence point blocks only
    when it is relevant to the question (shared content words)
    or carries a figure; covered points never block."""

    P = "The trust must be funded monthly with excess income"

    def test_relevant_absent_point_blocks(self):
        out = self.mod._points_missing(
            [self.P], "Nothing relevant here.",
            "How is the trust funded with income?")
        self.assertEqual(out, [self.P])

    def test_irrelevant_point_never_blocks(self):
        out = self.mod._points_missing(
            ["The office in Hawaii processes applications yearly"],
            "Nothing relevant here.",
            "How is the trust funded with income?")
        self.assertEqual(out, [])

    def test_figure_point_is_always_relevant(self):
        out = self.mod._points_missing(
            ["The fee is $500 in Hawaii"], "Nothing here.",
            "How is the trust funded with income?")
        self.assertEqual(len(out), 1)

    def test_covered_point_does_not_block(self):
        out = self.mod._points_missing(
            [self.P],
            "The trust must be funded monthly with excess "
            "income deposited.",
            "How is the trust funded with income?")
        self.assertEqual(out, [])


class TestReducedPanelBadge(_Patched):
    """Section C (James's ruling 2026-10-14): a DEGRADED panel
    renames the TOP badge classes (verified -> verified_reduced,
    partial -> partial_reduced, and verified_web ->
    verified_web_reduced in the web translation); the _single
    classes keep precedence (already visibly different, more
    specific). Panel state is FULL only when all three seats
    served, each by its primary candidate, from three distinct
    families, with no local seat and no seat missing."""

    CLAIM = "The waiver covers pest control"
    DRAFT = "The waiver covers pest control [1]."

    # -- the round-level panel state --------------------------

    def _install_panel(self, seats):
        """seats: job -> (model, failover) to serve, or None
        for a seat whose transport raises. The judge transports
        are faked; every serving seat supports every claim."""
        m = self.mod

        def seat(job, system, user):
            spec = seats.get(job)
            if spec is None:
                raise RuntimeError("seat down")
            model, failover = spec
            m._note_seat(job, model, failover)
            return json.dumps({"verdicts": [
                {"i": i, "verdict": "supported", "note": ""}
                for i in range(1, 7)], "missed": []})
        self.patch("_judge_seat_text", seat)
        self.patch("_gemini_text", lambda model, system, user:
                   json.dumps({"claims": [{
                       "text": self.CLAIM, "load_bearing": True,
                       "verdict": "supported", "note": "",
                       "sources": [1]}]}))
        self.patch("_refutation_retrieve",
                   lambda corp, hits, claims, mechanisms=None: 0)
        self.patch("_flash_arbitrate",
                   lambda q, h, c, idx: None)

    def _primaries(self):
        m = self.mod
        return {"judge_nim1": (m.SEATS["judge_nim1"]
                               ["candidates"][0][0], False),
                "judge_nim2": (m.JUDGE_SEAT2_MODEL, False)}

    def _round(self, seats, question):
        self._install_panel(seats)
        return self.mod._judge_round(
            question, [dict(HIT_A)], "draft [1]", True, "",
            corp=None, mechanisms=[], extra_claims=[])

    def test_full_panel_state(self):
        jr = self._round(self._primaries(), "q-badge-full")
        self.assertEqual(jr["panel_state"], "full")
        self.assertTrue(jr["pass"])
        self.assertGreaterEqual(
            len(jr["claims"][0]["coverage"]), 3)

    def test_failover_seat1_is_degraded(self):
        seats = self._primaries()
        seats["judge_nim1"] = (self.mod.SEATS["judge_nim1"]
                               ["candidates"][1][0], True)
        jr = self._round(seats, "q-badge-fo1")
        self.assertEqual(jr["panel_state"], "degraded")

    def test_failover_seat2_is_degraded(self):
        seats = self._primaries()
        seats["judge_nim2"] = (self.mod.SEAT2_FAILOVER_MODEL,
                               True)
        jr = self._round(seats, "q-badge-fo2")
        self.assertEqual(jr["panel_state"], "degraded")

    def test_missing_seat_is_degraded(self):
        seats = self._primaries()
        seats["judge_nim2"] = None
        jr = self._round(seats, "q-badge-missing")
        self.assertEqual(jr["panel_state"], "degraded")

    def test_two_seats_one_family_is_degraded(self):
        # The Miller-incident shape: two seats served by one
        # model family are one voice, not a full panel.
        seats = self._primaries()
        seats["judge_nim2"] = ("deepseek/deepseek-chat-v3.1",
                               False)
        jr = self._round(seats, "q-badge-onefam")
        self.assertEqual(jr["panel_state"], "degraded")

    def test_local_fallback_round_is_degraded(self):
        m = self.mod
        self.patch("_ollama_chat_raw",
                   lambda model, system, user, **kw: json.dumps(
                       {"claims": [{
                           "text": self.CLAIM,
                           "load_bearing": True,
                           "verdict": "supported", "note": "",
                           "sources": [1]}]}))
        self.patch("_refutation_retrieve",
                   lambda corp, hits, claims, mechanisms=None: 0)
        jr = m._judge_round("q-badge-local", [dict(HIT_A)],
                            "draft [1]", True, "", corp=None,
                            mechanisms=[], extra_claims=[],
                            local_only=True)
        self.assertEqual(jr["panel_state"], "degraded")

    # -- the badge rename through a real round ----------------

    def _unit_with_panel(self, seats):
        self._install_panel(seats)
        self.patch("_generate", self.fake_generate(self.DRAFT))
        return self.mod._run_unit(self.ctx())

    def test_full_panel_badges_verified(self):
        unit = self._unit_with_panel(self._primaries())
        self.assertEqual(unit["judging"]["panel_state"], "full")
        self.assertEqual(unit["badge"], "verified")

    def test_failover_seat_badges_verified_reduced(self):
        seats = self._primaries()
        seats["judge_nim2"] = (self.mod.SEAT2_FAILOVER_MODEL,
                               True)
        unit = self._unit_with_panel(seats)
        self.assertEqual(unit["judging"]["panel_state"],
                         "degraded")
        self.assertEqual(unit["badge"], "verified_reduced")

    def test_missing_seat_badge_is_visibly_different(self):
        seats = self._primaries()
        seats["judge_nim2"] = None
        unit = self._unit_with_panel(seats)
        self.assertEqual(unit["judging"]["panel_state"],
                         "degraded")
        # Per the precedence rule, the claims the missing seat
        # never judged may land on single-family coverage
        # (verified_single) or on the renamed top class
        # (verified_reduced); either way the badge must NOT
        # read as a full-panel "verified".
        self.assertIn(unit["badge"],
                      ("verified_reduced", "verified_single"))

    # -- the rename and precedence with a faked round ---------

    def _unit_fake_round(self, claims, panel_state, passed=True):
        self.patch("_generate", self.fake_generate(self.DRAFT))
        self.patch("_judge_round", self.fake_jr(
            claims, passed=passed, panel_state=panel_state))
        return self.mod._run_unit(self.ctx())

    def test_reduced_rename_applies_to_the_top_class(self):
        unit = self._unit_fake_round(
            [self.claim("The waiver covers pest control")],
            "degraded")
        self.assertEqual(unit["badge"], "verified_reduced")

    def test_single_coverage_keeps_precedence_on_degraded(self):
        unit = self._unit_fake_round(
            [self.claim("The waiver covers pest control",
                        coverage=("google",))],
            "degraded")
        self.assertEqual(unit["badge"], "verified_single")
        self.assertEqual(unit["judging"]["panel_state"],
                         "degraded")

    def test_full_panel_keeps_the_verified_name(self):
        unit = self._unit_fake_round(
            [self.claim("The waiver covers pest control")],
            "full")
        self.assertEqual(unit["badge"], "verified")

    # -- the capped Partial rename ----------------------------

    def _capped_unit(self, panel_state):
        # The draft sentences carry figures so they are
        # load-bearing by the citation parser's rule (the same
        # shape TestF4 proves the assembly against).
        fees = {"title": "Fees", "url": "u9",
                "excerpt": "The monthly limit is $100 and the "
                           "yearly cap is $200."}
        draft = ("The monthly limit is $100 [1]. "
                 "The yearly cap is $200 [1].")
        claims = [self.claim("The monthly limit is $100"),
                  self.claim("The yearly cap is $200",
                             final="contradicted")]
        self.patch("_generate", self.fake_generate(draft))
        self.patch("_judge_round", self.fake_jr(
            claims, passed=False, panel_state=panel_state))
        self.patch("_flash_arbitrate",
                   lambda q, h, c, idx: None)
        self.patch("_or_ask_cost", lambda: 999.0)
        return self.mod._run_unit(self.ctx(hits=[fees]))

    def test_capped_partial_on_a_degraded_panel(self):
        unit = self._capped_unit("degraded")
        self.assertEqual(unit["badge"], "partial_reduced")

    def test_capped_partial_on_a_full_panel(self):
        unit = self._capped_unit("full")
        self.assertEqual(unit["badge"], "partial")

    # -- the webapp labels -------------------------------------

    def test_webapp_badge_label_map(self):
        page = self.mod.PAGE
        self.assertIn("verified_reduced", page)
        self.assertIn("Verified (reduced panel)", page)
        self.assertIn("partial_reduced", page)
        self.assertIn("Partial (reduced panel)", page)
        self.assertIn("verified_web_reduced", page)
        self.assertIn("Verified web (reduced panel)", page)


if __name__ == "__main__":
    unittest.main()
