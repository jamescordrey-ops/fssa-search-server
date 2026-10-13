#!/usr/bin/env python3
"""v11 chunk near-duplicate dedupe (runs on the PC, stdlib + numpy).

Reads embeddings.jsonl (the embedder's append log; chunk identity is
(url, ordinal within that URL in file order)), finds duplicate chunks,
and writes:
  - dedupe_skip.json: [{"url":..., "index":...}] chunks to omit when
    embeddings.json is rebuilt (corpora and the JSONL stay untouched,
    so the pass is fully reversible by rebuilding without the list)
  - dedupe_log.jsonl: one line per dropped chunk with its keeper

Rules (conservative by design):
  1. Exact duplicates: identical normalized text.
  2. Near duplicates: word 8-gram shingle Jaccard >= 0.6 (prefilter)
     AND embedding cosine >= 0.985 AND same program tag (a chunk is
     never merged across programs: an SSI rule must not swallow a
     similar Medicaid rule).
Keeper choice inside a group: prefer the longer source document
(complete beats truncated), then the newer year in the title, then a
primary host over web.archive.org mirrors.
"""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
JSONL = BASE / "embeddings.jsonl"
SKIP_OUT = BASE / "dedupe_skip.json"
LOG_OUT = BASE / "dedupe_log.jsonl"
COS_MIN = 0.985
JACC_MIN = 0.6


def program_of(url, title):
    u, t = (url or "").lower(), (title or "").lower()
    if "poms.nsf" in u or "secure.ssa.gov" in u or "ssa.gov" in u:
        return "SSI/SSDI"
    if "law.cornell.edu" in u or "ecfr.gov" in u:
        return "SNAP" if "/273." in u else "Medicaid" if "/435." in u \
            else "General"
    if "fns.usda.gov" in u:
        return "SNAP"
    if "medicare.gov" in u or "medicareadvantage.com" in u:
        return "Medicare"
    if "irs.gov" in u or "/hip" in u or "healthy indiana" in t:
        return "MAGI"
    if "medicaid.gov" in u or "cms.gov" in u or "iltcp" in u:
        return "Medicaid"
    return "General"


def year_of(title):
    ys = [int(y) for y in re.findall(r"\b(20[2-9]\d)\b", title or "")
          if 2020 <= int(y) <= 2027]
    return max(ys) if ys else 0


def norm_text(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def shingles(text, k=8):
    words = norm_text(text).split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def main():
    chunks = []           # (url, chunk_idx, title, text)
    vecs = []
    doc_len = Counter()
    with JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                c = json.loads(line)
            except Exception:
                continue
            url = c.get("url", "")
            chunks.append((url, c.get("chunk_idx", 0),
                           c.get("title", ""), c.get("text", "")))
            doc_len[url] += len(c.get("text", ""))
            vecs.append(c["vec"])
    V = np.array(vecs, dtype=np.float32)
    V /= (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    n = len(chunks)
    print(f"loaded {n} chunks", flush=True)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    progs = [program_of(u, t) for (u, _i, t, _x) in chunks]

    # pass 1: exact normalized-text duplicates (same program only)
    seen_hash = {}
    exact_pairs = 0
    for i, (u, _idx, _t, text) in enumerate(chunks):
        h = hashlib.md5(norm_text(text).encode()).hexdigest()
        key = (progs[i], h)
        if key in seen_hash:
            union(i, seen_hash[key])
            exact_pairs += 1
        else:
            seen_hash[key] = i
    print(f"exact dupe links: {exact_pairs}", flush=True)

    # pass 2: shingle-prefiltered near duplicates
    shingle_sets = [shingles(text) for (_u, _i, _t, text) in chunks]
    inv = defaultdict(list)
    for i, ss in enumerate(shingle_sets):
        for s in ss:
            inv[s].append(i)
    cand = Counter()
    for s, ids in inv.items():
        if 1 < len(ids) <= 25:
            for a in range(len(ids)):
                for b in range(a + 1, len(ids)):
                    cand[(ids[a], ids[b])] += 1
    near_pairs = 0
    for (a, b), shared in cand.items():
        if progs[a] != progs[b] or find(a) == find(b):
            continue
        sa, sb = shingle_sets[a], shingle_sets[b]
        if not sa or not sb:
            continue
        jacc = shared / max(1, len(sa | sb))
        if jacc < JACC_MIN:
            continue
        if float(V[a] @ V[b]) >= COS_MIN:
            union(a, b)
            near_pairs += 1
    print(f"near dupe links: {near_pairs}", flush=True)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    def keeper_key(i):
        u, _idx, t, _x = chunks[i]
        return (doc_len[u], year_of(t), "web.archive.org" not in u)

    skips, logs = [], []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        keeper = max(members, key=keeper_key)
        ku, kidx, _kt, _kx = chunks[keeper]
        for i in members:
            if i == keeper:
                continue
            u, idx, _t, _x = chunks[i]
            skips.append({"url": u, "index": idx})
            logs.append({"url": u, "index": idx,
                         "keeper_url": ku, "keeper_index": kidx,
                         "program": progs[i]})
    SKIP_OUT.write_text(json.dumps(skips), encoding="utf-8")
    with LOG_OUT.open("w", encoding="utf-8") as f:
        for row in logs:
            f.write(json.dumps(row) + "\n")
    print(f"groups={sum(1 for v in groups.values() if len(v) > 1)} "
          f"dropped={len(skips)} kept={n - len(skips)} of {n}")


if __name__ == "__main__":
    sys.exit(main())
