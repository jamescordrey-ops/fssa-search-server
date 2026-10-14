"""Offline proofs for the v18 section E judge-only replay
harness (replay_harness.py): record loading, the seat-2
override, and a full re-judge of a persisted draft against
mocked judge transports (no network, no spend). Live use of
the harness is post-gate by design.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
V18 = os.path.join(HERE, "..")
SERVER = os.path.join(V18, "fssa_search_server.py")
HARNESS = os.path.join(V18, "replay_harness.py")

CLAIM = "The waiver covers pest control"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record():
    return {
        "kind": "ask", "ts": 1760000000.0, "wall_s": 12.3,
        "question": "Does the waiver cover pest control?",
        "corpus": "in", "local": False, "badge": "verified",
        "cost_usd": 0.01, "served": "fake/drafter",
        "panel": {"judges": ["lite", "nim", "nim2"]},
        "annotation": "Judges: fake",
        "units": [{
            "part": None,
            "unit_q": "Does the waiver cover pest control?",
            "badge": "verified",
            "draft_text": "The waiver covers pest control [1].",
            "claims": [{
                "text": CLAIM, "final": "supported",
                "verdicts": {"lite": "supported",
                             "nim": "supported",
                             "nim2": "supported", "local": None,
                             "flash": None, "refute_verdict": None},
                "coverage": ["deepseek", "google"],
                "models": {"lite": "gemini-3.5-flash-lite"},
                "origin": "extracted", "meta": False,
                "load_bearing": True, "sources": [0]}],
            "hits": [{"title": "Doc A", "url": "https://x.gov/a",
                      "excerpt": "The waiver covers pest control "
                                 "services for members."}],
        }],
    }


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fssa_v18_replay_")
        for name in ("corpus.json", "corpus_federal.json"):
            with open(os.path.join(cls.dir, name), "w",
                      encoding="utf-8") as f:
                json.dump([], f)
        with open(os.path.join(cls.dir, "openrouter_key.txt"),
                  "w", encoding="utf-8") as f:
            f.write("sk-test-dummy-not-a-real-key")
        cls.harness = _load(HARNESS, "replay_harness")

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        sys.argv = ["fssa_search_server.py"]
        self.server = _load(SERVER, "fssa_v18_replay_server")

    def tearDown(self):
        os.chdir(self._cwd)

    def test_load_records_filters_to_asks(self):
        path = os.path.join(self.dir, "log.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "job", "job_id": "x"}) + "\n")
            f.write(json.dumps(_record()) + "\n")
            f.write("not json\n")
        recs = self.harness.load_records(path)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["badge"], "verified")

    def test_seat2_override(self):
        m = self.server
        self.harness.apply_seat2_override(m, "candidate/model-x")
        self.assertEqual(m.JUDGE_SEAT2_MODEL, "candidate/model-x")
        self.assertEqual(
            m.SEATS["judge_nim2"]["candidates"][0][0],
            "candidate/model-x")
        self.assertIn("candidate/model-x", m._OR_ROUTES)

    def test_replay_plumbing_uses_no_cache(self):
        seen = {}

        def stub_round(question, hits, answer, hard, mech_block="",
                       **kw):
            seen.update(kw)
            seen["question"] = question
            return {"mode": "panel", "claims": [{
                "text": CLAIM, "final": "supported", "meta": False,
                "coverage": ["deepseek", "google"], "models": {}}]}
        orig = self.server._judge_round
        self.server._judge_round = stub_round
        try:
            out = self.harness.replay_record(self.server, _record())
        finally:
            self.server._judge_round = orig
        self.assertFalse(seen["use_cache"])
        self.assertEqual(seen["question"],
                         "Does the waiver cover pest control?")
        self.assertEqual(len(out), 1)
        c = out[0]["claims"][0]
        self.assertEqual(c["recorded_final"], "supported")
        self.assertEqual(c["replay_final"], "supported")
        self.assertTrue(c["agree"])

    def test_replay_with_mocked_judges(self):
        m = self.server
        m._gemini_text = lambda model, system, user: json.dumps(
            {"claims": [{"text": CLAIM, "load_bearing": True,
                         "verdict": "supported", "note": "",
                         "sources": [1]}]})

        def seat(job, system, user):
            m._note_seat(job, "deepseek/deepseek-chat-v3.1"
                         if job == "judge_nim1" else
                         "qwen/qwen3-235b-a22b-instruct-2507", False)
            return json.dumps({"verdicts": [{
                "i": 1, "verdict": "supported", "note": ""}],
                "missed": []})
        m._judge_seat_text = seat
        m._refutation_retrieve = \
            lambda corp, hits, claims, mechanisms=None: 0
        m._flash_arbitrate = lambda q, h, c, i: None
        out = self.harness.replay_record(m, _record())
        self.assertEqual(len(out), 1)
        c = out[0]["claims"][0]
        self.assertEqual(c["replay_final"], "supported")
        self.assertTrue(c["agree"])
        self.assertGreaterEqual(len(c["replay_coverage"]), 2)


if __name__ == "__main__":
    unittest.main()
