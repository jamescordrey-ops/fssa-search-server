#!/usr/bin/env python3
"""Rebuild embeddings.json from embeddings.jsonl honoring the v11
dedupe skip list (dedupe_skip.json: {"url", "index"} where index is
the chunk's ordinal within its URL in JSONL file order).

Why: gen_embeddings_stream.py's finalize() writes EVERY jsonl line
into embeddings.json. The v11 build that produced the live 72,722
chunks had applied this skip list (74,975 raw - 2,253 skipped). The
v19 deploy's finalize skipped that step and resurrected the
near-duplicates. This pass restores the deduped set, keeping the
newly appended chunks (their URLs postdate the skip list, so none
of their ordinals are in it). Streams throughout; writes via a tmp
file + atomic replace, mirroring finalize().
"""
import json
from pathlib import Path

BASE = Path(r"C:\Users\james\odysseus\data\fssa-server")
JSONL = BASE / "embeddings.jsonl"
SKIP = BASE / "dedupe_skip.json"
OUT = BASE / "embeddings.json"

skip = {(e["url"], e["index"])
        for e in json.loads(SKIP.read_text(encoding="utf-8"))}
print(f"skip entries: {len(skip)}", flush=True)

dim = 0
with JSONL.open(encoding="utf-8") as f:
    for line in f:
        try:
            dim = len(json.loads(line)["vec"])
            break
        except Exception:
            continue
print(f"dim: {dim}", flush=True)

ordinals = {}
kept = dropped = 0
tmp = OUT.with_suffix(".json.tmp")
with JSONL.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
    out.write('{"model": "nomic-embed-text", "dim": %d, "chunks": [' % dim)
    first = True
    for line in src:
        try:
            ch = json.loads(line)
        except Exception:
            continue
        url = ch["url"]
        idx = ordinals.get(url, 0)
        ordinals[url] = idx + 1
        if (url, idx) in skip:
            dropped += 1
            continue
        ch["id"] = kept
        if not first:
            out.write(",")
        out.write(json.dumps(ch, separators=(",", ":")))
        first = False
        kept += 1
    out.write("]}")
tmp.replace(OUT)
print(f"kept {kept}, dropped {dropped}", flush=True)
