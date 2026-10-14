"""Offline proofs for the v18 pipeline-level items: F17
answer-cache hygiene, F18 in-flight dedupe, F19 the honest
local mode, R6 branch-out hardening, R7 precomputed chunks,
and section E ask/job record persistence.

do_POST is driven directly on handler instances with captured
_json output; retrieval and units are faked where the item
under test is not retrieval itself. No network call is made.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "fssa_search_server.py")
MODEL = "qwen3.5:9b"
HIT = {"title": "Doc A", "url": "https://example.gov/a",
       "excerpt": "The waiver covers pest control services."}


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
            "fssa_v18_pipeline", os.path.abspath(SERVER))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
        sys.argv = argv
    return mod


def _fake_unit():
    return {"answer": "Yes, it does [1].", "badge": "verified",
            "served": "fake/drafter", "fallback": False,
            "downgraded": False, "prompt": "p", "rungs": 1,
            "judge_ms": 1, "refute_ms": 0, "cap_hit": False,
            "cost_capped": False, "draft_text": "Yes, it does [1].",
            "figure_check": {"ungrounded": [],
                             "regenerated": False},
            "judging": {"mode": "panel", "flash_used": False,
                        "errors": [], "annotation": "Judges: fake",
                        "panel": {"judges": ["lite"],
                                  "third": {"status": "ok"}}},
            "claims": [], "missing": [], "gate": None,
            "divergence": None}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fssa_v18_pipeline_")
        cls.mod = _load_server(cls.dir)

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        m = self.mod
        m._ASK_CTX_VAR.set(m._AskContext())
        m._ASK_CACHE.clear()
        with m._INFLIGHT_LOCK:
            m._INFLIGHT.clear()
        self.log_path = os.path.join(self.dir, "ask_records.jsonl")
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
        m.ASK_LOG_PATH = self.log_path
        self._saved = {}

    def tearDown(self):
        for name, fn in self._saved.items():
            setattr(self.mod, name, fn)
        os.chdir(self._cwd)

    def patch(self, name, fn):
        if name not in self._saved:
            self._saved[name] = getattr(self.mod, name)
        setattr(self.mod, name, fn)

    def post(self, payload):
        """Run one synchronous do_POST; return the JSON object."""
        m = self.mod
        body = json.dumps(payload).encode()
        h = object.__new__(m.Handler)
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.path = "/ask"
        captured = []
        h._json = lambda obj, code=200: captured.append(
            (obj, code))
        h.do_POST()
        self.assertEqual(len(captured), 1)
        return captured[0][0]


class TestF17CacheHygiene(_Base):
    """F17: only verified-tier answers are replayed from the
    answer cache, and a replay reports zero new spend."""

    Q = "What does the waiver cover for pest control today?"

    def _seed_and_post(self, badge):
        m = self.mod
        routed, _r = m.route_local_model(self.Q, MODEL)
        key = (self.Q, routed, "in", m.REWRITE_ENABLED,
               m.RERANK_ENABLED, False)
        seeded = {"answer": "seeded", "badge": badge,
                  "cost_usd": 0.05, "hits": [], "timing": {}}
        m._cache_put(m._ASK_CACHE, key, seeded)
        out = self.post({"question": self.Q, "model": MODEL,
                         "corpus": "in"})
        return out, seeded

    def test_verified_replay_reports_zero_cost(self):
        out, seeded = self._seed_and_post("verified")
        self.assertTrue(out.get("cached"))
        self.assertEqual(out["cost_usd"], 0.0)
        self.assertEqual(seeded["cost_usd"], 0.05)  # copy, not alias

    def test_partial_is_not_served_from_cache(self):
        # A partial seeded the way v17.3 would have cached it is
        # still in the dict, but the WRITE side no longer puts
        # it there; prove the write side via a live IDK ask:
        # an IDK result must not enter the cache at all.
        m = self.mod
        self.patch("search", lambda q, corpus=None, **kw: [])
        self.patch("_branch_out_hits",
                   lambda q, c: ([], {"queries": [], "urls": [],
                                      "credits": 0,
                                      "fetched": "2026-10-14",
                                      "unavailable": "test"}))
        out = self.post({"question": self.Q, "model": MODEL,
                         "corpus": "in"})
        self.assertEqual(out["badge"], "idk")
        routed, _r = m.route_local_model(self.Q, MODEL)
        key = (self.Q, routed, "in", m.REWRITE_ENABLED,
               m.RERANK_ENABLED, False)
        self.assertIsNone(m._cache_get(m._ASK_CACHE, key))


class TestF18InflightDedupe(_Base):
    """F18: two concurrent identical submissions run ONE
    pipeline; the second waits and replays the first's result."""

    Q = "What does the waiver cover for pest control today?"

    def test_duplicate_waits_and_replays(self):
        m = self.mod
        calls = []

        def slow_search(q, corpus=None, **kw):
            calls.append(q)
            time.sleep(1.5)
            return []
        self.patch("search", slow_search)
        self.patch("_branch_out_hits",
                   lambda q, c: ([], {"queries": [], "urls": [],
                                      "credits": 0,
                                      "fetched": "2026-10-14",
                                      "unavailable": "test"}))
        results = {}

        def run(name, delay):
            if delay:
                time.sleep(delay)
            results[name] = self.post(
                {"question": self.Q, "model": MODEL,
                 "corpus": "in"})

        t1 = threading.Thread(target=run, args=("first", 0))
        t2 = threading.Thread(target=run, args=("second", 0.4))
        t1.start()
        t2.start()
        t1.join(30)
        t2.join(30)
        self.assertEqual(len(calls), 1)  # one pipeline ran
        self.assertEqual(results["first"]["badge"], "idk")
        self.assertTrue(results["second"].get("deduped"))
        self.assertEqual(results["second"]["cost_usd"], 0.0)


class TestF19LocalMode(_Base):
    """F19: local=true threads local_mode into the unit and
    keys the answer cache separately from hosted asks."""

    Q = "Does the waiver cover pest control?"

    def test_local_flag_threads_and_separates(self):
        m = self.mod
        seen = []

        def fake_unit(ctx):
            seen.append(dict(ctx))
            return _fake_unit()
        self.patch("search", lambda q, corpus=None, **kw:
                   [dict(HIT)])
        self.patch("_run_unit", fake_unit)
        out1 = self.post({"question": self.Q, "model": MODEL,
                          "corpus": "in"})
        n_hosted = len(seen)
        self.assertGreaterEqual(n_hosted, 1)
        self.assertTrue(all(not s["local_mode"] for s in seen))
        out2 = self.post({"question": self.Q, "model": MODEL,
                          "corpus": "in", "local": True})
        self.assertGreater(len(seen), n_hosted)  # no cross replay
        self.assertTrue(all(s["local_mode"]
                            for s in seen[n_hosted:]))
        self.assertNotIn("cached", out2)
        self.assertEqual(out1["badge"], "verified")
        self.assertEqual(out2["badge"], "verified")


class TestSectionERecords(_Base):
    """Section E: completed asks and job lifecycles persist
    as JSON lines with per-unit draft + claim records."""

    Q = "Does the waiver cover pest control?"

    def _records(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_ask_record_written(self):
        m = self.mod
        self.patch("search", lambda q, corpus=None, **kw:
                   [dict(HIT)])
        self.patch("_run_unit", lambda ctx: _fake_unit())
        self.post({"question": self.Q, "model": MODEL,
                   "corpus": "in"})
        asks = [r for r in self._records() if r["kind"] == "ask"]
        self.assertEqual(len(asks), 1)
        rec = asks[0]
        self.assertEqual(rec["question"], self.Q)
        self.assertEqual(rec["badge"], "verified")
        self.assertIn("cost_usd", rec)
        self.assertEqual(rec["units"][0]["draft_text"],
                         "Yes, it does [1].")
        self.assertEqual(rec["units"][0]["hits"][0]["title"],
                         "Doc A")

    def test_job_record_written(self):
        m = self.mod
        self.patch("search", lambda q, corpus=None, **kw:
                   [dict(HIT)])
        self.patch("_run_unit", lambda ctx: _fake_unit())
        rec = m._job_create()
        body = json.dumps({"question": self.Q, "model": MODEL,
                           "corpus": "in"}).encode()
        m._run_job(rec, body,
                   {"Content-Length": str(len(body))})
        jobs = [r for r in self._records() if r["kind"] == "job"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], rec["id"])
        self.assertEqual(jobs[0]["status"], "done")
        self.assertGreaterEqual(jobs[0]["finished"],
                                jobs[0]["created"])


class TestR6BranchOut(_Base):
    """R6: post-redirect whitelist re-check, script/style
    stripping, and other-state filtering on federal pages."""

    def setUp(self):
        super().setUp()
        m = self.mod
        self.patch("BRANCH_OUT_ENABLED", True)
        self.patch("_tavily_key", lambda: "test-key")
        self.patch("_tavily_credits", lambda: {"credits": 0})
        self.patch("_branch_queries", lambda q, c: ["q1"])

    def _branch(self, final_url, html, title="Medicaid page"):
        m = self.mod

        def tavily(path, payload):
            if path == "/search":
                return {"results": [{
                    "url": "https://www.medicaid.gov/page",
                    "title": title}]}
            return {"results": []}  # extract empty: fallback fetch
        self.patch("_tavily_post", tavily)

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode()

            def geturl(self):
                return final_url

        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: Resp()):
            return m._branch_out_hits("indiana medicaid question",
                                      [])

    def test_redirect_off_whitelist_is_dropped(self):
        hits, _rec = self._branch(
            "https://evil.example.com/landing",
            "<html><body>Indiana Medicaid covers this.</body>"
            "</html>")
        self.assertEqual(hits, [])

    def test_other_state_federal_page_is_dropped(self):
        hits, _rec = self._branch(
            "https://www.medicaid.gov/page",
            "<html><body>Ohio Medicaid program details for Ohio "
            "residents and Ohio providers.</body></html>",
            title="Ohio Medicaid")
        self.assertEqual(hits, [])

    def test_indiana_page_kept_and_scripts_stripped(self):
        hits, _rec = self._branch(
            "https://www.medicaid.gov/page",
            "<html><head><script>var junk = 'zqxj';</script>"
            "<style>.x{color:red}</style></head><body>Indiana "
            "Medicaid covers pest control for Indiana members "
            "under the waiver program rules.</body></html>",
            title="Indiana Medicaid")
        self.assertEqual(len(hits), 1)
        self.assertNotIn("zqxj", hits[0]["excerpt"])
        self.assertNotIn("color:red", hits[0]["excerpt"])
        self.assertIn("pest control", hits[0]["excerpt"])


class TestR7Chunks(_Base):
    """R7: a document's chunks are computed once and cached
    on the document dict."""

    def test_chunks_cached_on_the_document(self):
        m = self.mod
        doc = {"title": "T", "url": "u",
               "text": "Alpha beta gamma delta epsilon zeta. "
                       "Eta theta iota kappa lambda mu."}
        c1 = m._doc_chunks(doc)
        doc["text"] = "completely different content now"
        c2 = m._doc_chunks(doc)
        self.assertIs(c1, c2)


class TestReducedBadgeCache(_Base):
    """Badge ruling 2026-10-14 + F17: the _reduced names are
    the same verified tier under the new naming, so they enter
    (and replay from) the answer cache exactly as "verified"
    did; the partial tier, reduced or not, still never does."""

    Q = "Does the waiver cover pest control?"

    def _post_with_badge(self, badge):
        m = self.mod
        unit = _fake_unit()
        unit["badge"] = badge
        self.patch("search", lambda q, corpus=None, **kw:
                   [dict(HIT)])
        self.patch("_run_unit", lambda ctx: unit)
        return self.post({"question": self.Q, "model": MODEL,
                          "corpus": "in"})

    def _cache_key(self):
        m = self.mod
        routed, _r = m.route_local_model(self.Q, MODEL)
        return (self.Q, routed, "in", m.REWRITE_ENABLED,
                m.RERANK_ENABLED, False)

    def test_verified_reduced_caches_and_replays(self):
        out = self._post_with_badge("verified_reduced")
        self.assertEqual(out["badge"], "verified_reduced")
        self.assertIsNotNone(
            self.mod._cache_get(self.mod._ASK_CACHE,
                                self._cache_key()))
        out2 = self.post({"question": self.Q, "model": MODEL,
                          "corpus": "in"})
        self.assertTrue(out2.get("cached"))
        self.assertEqual(out2["badge"], "verified_reduced")
        self.assertEqual(out2["cost_usd"], 0.0)

    def test_verified_web_reduced_caches(self):
        self._post_with_badge("verified_web_reduced")
        self.assertIsNotNone(
            self.mod._cache_get(self.mod._ASK_CACHE,
                                self._cache_key()))

    def test_partial_reduced_never_caches(self):
        out = self._post_with_badge("partial_reduced")
        self.assertEqual(out["badge"], "partial_reduced")
        self.assertIsNone(
            self.mod._cache_get(self.mod._ASK_CACHE,
                                self._cache_key()))


if __name__ == "__main__":
    unittest.main()
