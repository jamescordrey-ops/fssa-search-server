"""Offline proofs for the v18 section B hard stop (the
reservation gate), plus the F9/F10/F11 fixes inside that step.

Everything here is mocked: the transport (_https_post) is a
fake, no key is real, and no network call of any kind is made.
The server module is loaded fresh from a temp working directory
holding tiny stand-in corpora and a dummy key file.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "fssa_search_server.py")
MODEL = "openai/gpt-oss-120b"  # price row (0.10, 0.50) per 1M


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
    sys.argv = ["fssa_search_server.py"]  # the module reads argv[1]
    try:                                  # as its port at import
        spec = importlib.util.spec_from_file_location(
            "fssa_v18_hardstop", os.path.abspath(SERVER))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
        sys.argv = argv
    return mod


def _ok_response(cost=0.001):
    body = {"choices": [{"message": {"content": "ok"}}]}
    if cost is not None:
        body["usage"] = {"cost": cost}
    return 200, json.dumps(body).encode()


class HardStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fssa_v18_gate_")
        cls.mod = _load_server(cls.dir)
        cls.orig_https_post = cls.mod._https_post

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(self.dir)
        m = self.mod
        for name in ("openrouter_spend.json", "flash_state.json",
                     "tavily_state.json"):
            p = os.path.join(self.dir, name)
            if os.path.exists(p):
                os.remove(p)
        m._OR_STATE.update({"date": "", "spend": 0.0,
                            "reservations": {}, "broken": False,
                            "loaded": False})
        m._FLASH_STATE.update({"date": "", "count": 0,
                               "loaded": False})
        m._OR_KEY_STATE.update({"checked": 0.0, "limit": None,
                                "usage": None, "data": None})
        m._events_reset()
        # NB: via type(self) — instance access would bind the
        # TestCase as the function's first argument.
        m._https_post = type(self).orig_https_post

    def tearDown(self):
        os.chdir(self._cwd)

    # -- helpers -------------------------------------------------
    def or_call(self, max_tokens=200000, system="sys", user="u" * 100):
        m = self.mod
        return m._nim_text(m.OPENROUTER_BASE_URL,
                           m.OPENROUTER_KEY_ENV,
                           m.OPENROUTER_KEY_FILE,
                           MODEL, system, user, temperature=0,
                           max_tokens=max_tokens, timeout=5,
                           provider="openrouter", or_route=True)

    # -- the mandatory concurrency proof -------------------------
    def test_concurrency_never_exceeds_ceiling(self):
        m = self.mod
        peaks = []

        def fake_post(host, path, body, headers, timeout=60):
            peaks.append(m._or_committed())
            time.sleep(0.02)  # hold the reservation open
            return _ok_response(cost=0.001)

        m._https_post = fake_post
        errors = []

        def worker():
            for _ in range(6):
                try:
                    self.or_call()
                except RuntimeError as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=worker)
                   for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Each admitted call reserves ~$0.10+ (200k output tokens
        # at $0.50/1M); 40 threads x 6 calls race for a $2.00
        # ceiling, so refusals MUST have happened...
        self.assertTrue(errors, "gate never refused under load")
        self.assertTrue(any("ceiling" in e for e in errors))
        # ...and the committed figure must never have crossed it,
        # at any sampled instant or at the end.
        self.assertTrue(peaks)
        self.assertLessEqual(max(peaks), 2.00 + 1e-9)
        self.assertLessEqual(m._or_committed(), 2.00 + 1e-9)
        self.assertLessEqual(m._or_spend_today(), 2.00 + 1e-9)

    # -- F11-1: timeout / failure settlement ----------------------
    def test_timeout_settles_at_reserved_maximum_per_attempt(self):
        m = self.mod

        def fake_post(host, path, body, headers, timeout=60):
            raise TimeoutError("timed out")

        m._https_post = fake_post
        max_cost = m._or_max_cost(MODEL, "sys", "u" * 100, 200000)
        with self.assertRaises(RuntimeError):
            self.or_call()
        # Two HTTP attempts, each settled at its full maximum.
        self.assertAlmostEqual(m._or_spend_today(),
                               2 * max_cost, places=5)
        with m._OR_LOCK:
            self.assertEqual(m._OR_STATE["reservations"], {})

    def test_5xx_then_success_settles_max_then_actual(self):
        m = self.mod
        calls = {"n": 0}

        def fake_post(host, path, body, headers, timeout=60):
            calls["n"] += 1
            if calls["n"] == 1:
                return 503, b""
            return _ok_response(cost=0.001)

        m._https_post = fake_post
        max_cost = m._or_max_cost(MODEL, "sys", "u" * 100, 200000)
        self.assertEqual(self.or_call(), "ok")
        self.assertAlmostEqual(m._or_spend_today(),
                               max_cost + 0.001, places=5)

    def test_missing_usage_cost_settles_at_maximum(self):
        m = self.mod
        m._https_post = lambda *a, **k: _ok_response(cost=None)
        max_cost = m._or_max_cost(MODEL, "sys", "u" * 100, 200000)
        self.assertEqual(self.or_call(), "ok")
        self.assertAlmostEqual(m._or_spend_today(), max_cost,
                               places=5)

    # -- F11-2: corrupt ledger fails closed -------------------------
    def test_corrupt_ledger_fails_closed(self):
        m = self.mod
        with open(os.path.join(self.dir, "openrouter_spend.json"),
                  "w", encoding="utf-8") as f:
            f.write("not json {{{")
        m._OR_STATE.update({"loaded": False})
        self.assertFalse(m._or_available())
        self.assertTrue(m._or_cap_tripped())
        self.assertIsNone(m._or_reserve(0.01))

    def test_ledger_file_is_valid_json_after_operations(self):
        m = self.mod
        m._https_post = lambda *a, **k: _ok_response(cost=0.002)
        self.assertEqual(self.or_call(max_tokens=1000), "ok")
        with open(os.path.join(self.dir, "openrouter_spend.json"),
                  encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["date"], m._ledger_today())
        self.assertAlmostEqual(st["spend"], 0.002, places=6)
        self.assertEqual(st["reservations"], {})
        leftovers = [n for n in os.listdir(self.dir)
                     if ".tmp" in n]
        self.assertEqual(leftovers, [])

    # -- crash safety: unsettled reservations persist -------------
    def test_unsettled_reservation_counts_after_restart(self):
        m = self.mod
        rid = m._or_reserve(0.50)
        self.assertIsNotNone(rid)
        # Simulate a crash + restart: in-memory state is wiped,
        # only the ledger file survives.
        m._OR_STATE.update({"date": "", "spend": 0.0,
                            "reservations": {}, "broken": False,
                            "loaded": False})
        self.assertGreaterEqual(m._or_committed(), 0.50)
        m._or_settle(rid, 0.10)
        self.assertAlmostEqual(m._or_committed(), 0.10, places=6)

    # -- F11-3: usage_daily reconciliation --------------------------
    def test_usage_daily_floor_blocks_reservations(self):
        m = self.mod
        m._OR_KEY_STATE["data"] = {"usage_daily": [
            {"date": m._ledger_today(), "usage": 1.80}]}
        self.assertGreaterEqual(m._or_committed(), 1.80)
        self.assertIsNone(m._or_reserve(0.30))   # 1.80+0.30 > 2.00
        rid = m._or_reserve(0.15)                # fits
        self.assertIsNotNone(rid)
        m._or_settle(rid, 0.15)

    def test_scalar_usage_daily_does_not_gate(self):
        # Live finding (2026-10-14): /auth/key can return
        # usage_daily as a scalar equal to the key's TOTAL
        # (cumulative) usage. An undated scalar cannot be
        # attributed to the ledger day, so it must not floor:
        # with a small ledger spend the cap stays untripped.
        m = self.mod
        rid = m._or_reserve(0.10)
        self.assertIsNotNone(rid)
        m._or_settle(rid, 0.10)                  # ledger: $0.10
        m._OR_KEY_STATE["data"] = {
            "usage_daily": 4.094341471}          # cumulative
        self.assertEqual(m._or_reconciled_floor(), 0.0)
        self.assertAlmostEqual(m._or_committed(), 0.10, places=6)
        self.assertFalse(m._or_cap_tripped())

    def test_dated_row_above_spend_can_trip(self):
        # Dated rows still reconcile as a floor: a row for the
        # ledger day above the ledger spend raises committed
        # past the ceiling and trips the cap.
        m = self.mod
        rid = m._or_reserve(0.10)
        self.assertIsNotNone(rid)
        m._or_settle(rid, 0.10)                  # ledger: $0.10
        self.assertFalse(m._or_cap_tripped())
        m._OR_KEY_STATE["data"] = {"usage_daily": [
            {"date": m._ledger_today(), "usage": 2.50}]}
        self.assertAlmostEqual(m._or_reconciled_floor(), 2.50,
                               places=6)
        self.assertGreaterEqual(m._or_committed(), 2.50)
        self.assertTrue(m._or_cap_tripped())

    # -- ledger day frame (pass 2 item F6) -------------------------
    def test_ledger_day_is_indianapolis(self):
        import calendar as _cal
        m = self.mod
        real_time = time.time
        try:
            # 2026-01-15 04:30 UTC = 2026-01-14 23:30 EST.
            time.time = lambda: _cal.timegm(
                (2026, 1, 15, 4, 30, 0, 0, 0, 0))
            self.assertEqual(m._ledger_today(), "2026-01-14")
            # 2026-07-01 03:30 UTC = 2026-06-30 23:30 EDT.
            time.time = lambda: _cal.timegm(
                (2026, 7, 1, 3, 30, 0, 0, 0, 0))
            self.assertEqual(m._ledger_today(), "2026-06-30")
            # Midday UTC is the same date in Indianapolis.
            time.time = lambda: _cal.timegm(
                (2026, 7, 1, 16, 0, 0, 0, 0, 0))
            self.assertEqual(m._ledger_today(), "2026-07-01")
        finally:
            time.time = real_time

    # -- F10: per-ask cost/events survive pool threads -------------
    def test_ask_context_propagates_into_pool_workers(self):
        m = self.mod
        m._events_reset()

        def worker():
            m._or_note_cost(0.25)
            m._note_seat("judge_nim1",
                         "deepseek/deepseek-chat-v3.1", False)
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut = m._submit(pool, worker)
            self.assertTrue(fut.result(timeout=10))
        self.assertEqual(m._or_ask_cost(), 0.25)
        kinds = [e["kind"] for e in m._events()]
        self.assertIn("seat", kinds)
        ctx = m._ask_ctx()
        self.assertEqual(ctx.verdict_families["judge_nim1"],
                         {"deepseek"})
        self.assertEqual(ctx.served_models["judge_nim1"],
                         "deepseek/deepseek-chat-v3.1")

    # -- F9: reused sockets get the caller's timeout ---------------
    def test_https_post_applies_timeout_to_reused_socket(self):
        m = self.mod

        class FakeSock:
            def __init__(self):
                self.timeouts = []

            def settimeout(self, t):
                self.timeouts.append(t)

        class FakeResp:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeConn:
            def __init__(self):
                self.sock = FakeSock()
                self.timeout = None

            def request(self, *a, **k):
                pass

            def getresponse(self):
                return FakeResp()

        conn = FakeConn()
        orig_acquire = m._pool_acquire
        m._pool_acquire = lambda host: conn
        try:
            status, _raw = m._https_post("example.invalid", "/x",
                                         b"{}", {}, timeout=37)
        finally:
            m._pool_acquire = orig_acquire
        self.assertEqual(status, 200)
        self.assertEqual(conn.sock.timeouts, [37])

    # -- R10b: the Flash counter persists across restarts ----------
    def test_flash_counter_persists(self):
        m = self.mod
        self.assertTrue(m._flash_spend())
        self.assertTrue(m._flash_spend())
        m._FLASH_STATE.update({"date": "", "count": 0,
                               "loaded": False})
        self.assertEqual(m._flash_calls_today(), 2)

    # -- the choke-point audit --------------------------------------
    def test_choke_point_audit(self):
        with open(os.path.abspath(SERVER), encoding="utf-8") as f:
            lines = f.read().splitlines()
        # Which functions call the raw transport?
        callers = set()
        current = None
        for line in lines:
            if line.startswith("def "):
                current = line[4:].split("(")[0]
            elif line and not line[0].isspace():
                current = None  # dedent: module level
            if "_https_post(" in line and current != "_https_post":
                callers.add(current)
        self.assertLessEqual(
            callers,
            {"_gemini_call", "_tavily_post", "_nim_text",
             "_or_key_state"})
        # The OpenRouter base URL is built only at its sanctioned
        # sites (the constant, _or_text, the key-state check).
        users = set()
        current = None
        for line in lines:
            if line.startswith("def "):
                current = line[4:].split("(")[0]
            elif line and not line[0].isspace():
                current = None  # dedent: module level
            if "OPENROUTER_BASE_URL" in line:
                users.add(current)
        self.assertLessEqual(
            users, {None, "_or_text", "_or_key_state"})
        # No other paid host is dialed anywhere.
        src = "\n".join(lines)
        self.assertNotIn("api.openai.com", src)
        self.assertNotIn("api.anthropic.com", src)


if __name__ == "__main__":
    unittest.main()
