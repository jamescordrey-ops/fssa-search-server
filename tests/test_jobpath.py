"""Regression tests for the live-found 2026-10-14 job-path
defect: _run_job re-serialized the request body (growing it)
while the shim re-read only the original Content-Length
bytes, truncating the JSON; and do_POST's except handler
referenced the dedupe handles before they were bound,
masking the real error behind an UnboundLocalError
('_inf_entry'). These tests pin both halves.

Offline: Handler.do_POST is stubbed for the transport test;
the masking test runs the real do_POST only as far as its
body-parse failure, which needs no network or models.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "fssa_search_server.py")


def _load_server(workdir):
    for name in ("corpus.json", "corpus_federal.json"):
        with open(os.path.join(workdir, name), "w",
                  encoding="utf-8") as f:
            json.dump([], f)
    cwd = os.getcwd()
    argv = sys.argv
    os.chdir(workdir)
    sys.argv = ["fssa_search_server.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_v18_jobpath", os.path.abspath(SERVER))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
        sys.argv = argv
    return mod


class JobPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="fssa_v18_jobpath_")
        cls.mod = _load_server(cls.dir)

    def test_job_body_survives_reserialization(self):
        """A compact webapp body whose re-dump is LONGER than
        the original Content-Length must still parse whole
        inside the job shim."""
        m = self.mod
        question = ("Does the health and wellness waiver "
                    "cover pest control?")
        body = json.dumps(
            {"question": question, "model": "", "corpus": "both",
             "rewrite": True, "rerank": True, "fresh": False,
             "local": False},
            separators=(",", ":")).encode()
        headers = {"Content-Length": str(len(body)),
                   "Content-Type": "application/json"}
        seen = {}

        def stub_do_post(shim):
            length = int(shim.headers.get("Content-Length", 0))
            raw = shim.rfile.read(length)
            req = json.loads(raw)  # raises under the bug
            seen["question"] = req.get("question")
            shim._json({"ok": True})

        orig = m.Handler.do_POST
        m.Handler.do_POST = stub_do_post
        try:
            rec = {"id": "test-job", "ts": 0, "stage": "new"}
            m._run_job(rec, body, headers)
        finally:
            m.Handler.do_POST = orig
        self.assertEqual(rec.get("status"), "done")
        self.assertEqual(seen.get("question"), question)

    def test_unparseable_body_reports_real_error(self):
        """A garbage body must surface the JSON error itself,
        never an unbound '_inf_entry' masking error."""
        m = self.mod
        captured = {}
        shim = type("_Shim", (), {})()
        raw = b"this is not json"
        shim.headers = {"Content-Length": str(len(raw))}
        shim.rfile = io.BytesIO(raw)
        shim.path = "/ask"
        shim.wfile = io.BytesIO()
        shim._json = lambda obj, code=200: captured.update(
            {"obj": obj, "code": code})
        shim.send_response = lambda *a, **k: None
        shim.send_header = lambda *a, **k: None
        shim.end_headers = lambda *a, **k: None
        m.Handler.do_POST(shim)
        self.assertEqual(captured.get("code"), 500)
        err = str(captured.get("obj"))
        self.assertNotIn("_inf_entry", err)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
