#!/usr/bin/env python3
"""Convert the v21 _TOPIC_BRIDGES table into declarative pins.json.

The v22 retrieval path reads pins as DATA ({trigger, target, reason,
case_id}) instead of a code table, per the consult design: every pin
names why it exists, and a pin whose case passes without it across
two generation builds is proposed for retirement. The legacy path
keeps using _TOPIC_BRIDGES untouched; this file is the v2 source.

Target spec per pin: the bridge's (phrase, pin_term) pairs plus its
program scope. The v2 resolver runs the legacy choice logic over the
generation store: FTS the phrases, prefer the chunk carrying the
pin term (title+text), then a chunk of the program scope, then the
top hit.

Usage: python3 tools/bridges_to_pins.py [--out pins.json]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(SERVER_DIR / "pins.json"))
    args = ap.parse_args()
    sys.path.insert(0, str(HERE))
    import retrieval_regression as rr
    mod = rr.load_server(str(SERVER_DIR))
    pins = []
    for i, (tokens, texts, phrases, prog_scope, req) in \
            enumerate(mod._TOPIC_BRIDGES):
        req_tokens, req_texts = (list(req[0]), list(req[1])) if req \
            else ([], [])
        pins.append({
            "id": f"bridge-{i:02d}",
            "trigger_tokens": sorted(tokens),
            "trigger_texts": list(texts),
            "require_tokens": req_tokens,
            "require_texts": req_texts,
            "target": {
                "phrases": [{"phrase": p, "pin_term": t}
                            for p, t in phrases],
                "program_scope": prog_scope},
            "reason": "converted from the v21 _TOPIC_BRIDGES table "
                      "(deterministic topic bridge for a chronic "
                      "retrieval gap)",
            "case_id": None,
            "source": "_TOPIC_BRIDGES",
        })
    Path(args.out).write_text(json.dumps(
        {"version": "v22.1", "pins": pins}, indent=1),
        encoding="utf-8")
    log.info("wrote %d pins to %s", len(pins), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
