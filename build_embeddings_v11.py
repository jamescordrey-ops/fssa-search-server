#!/usr/bin/env python3
"""v11 finalize: stream embeddings.jsonl into embeddings.json in the
server format, omitting chunks listed in dedupe_skip.json
({"url","index"} where index is the JSONL chunk_idx). Mirrors the
embedder's own finalize() otherwise."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
JSONL = BASE / "embeddings.jsonl"
EMB = BASE / "embeddings.json"
SKIP = BASE / "dedupe_skip.json"
MODEL = "nomic-embed-text"


def main():
    skips = set()
    if SKIP.exists():
        for s in json.loads(SKIP.read_text(encoding="utf-8")):
            skips.add((s["url"], s["index"]))
    tmp = EMB.with_suffix(".json.tmp")
    count, skipped, dim = 0, 0, 0
    with JSONL.open(encoding="utf-8") as src, tmp.open(
            "w", encoding="utf-8") as out:
        first, head_written = True, False
        for line in src:
            try:
                ch = json.loads(line)
            except Exception:
                continue
            if not dim:
                dim = len(ch["vec"])
                out.write('{"model": "%s", "dim": %d, "chunks": ['
                          % (MODEL, dim))
                head_written = True
            if (ch.get("url", ""), ch.get("chunk_idx", 0)) in skips:
                skipped += 1
                continue
            ch["id"] = count
            if not first:
                out.write(",")
            out.write(json.dumps(ch, separators=(",", ":")))
            first = False
            count += 1
        if not head_written:
            out.write('{"model": "%s", "dim": 0, "chunks": [' % MODEL)
        out.write("]}")
    tmp.replace(EMB)
    print(f"embeddings.json: {count} chunks ({skipped} skipped), "
          f"dim {dim}")


if __name__ == "__main__":
    main()
