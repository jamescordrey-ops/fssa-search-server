#!/usr/bin/env python3
"""Finalize g0002's vector layer on the PC (v22.2 deploy variant
of tools/finalize_vectors.py).

Differences from the stock tool, by design of this deploy:
- The embeddings input is the DEPLOYED g0001 generation store
  (its DB + vectors_g0001.npy + vectors_index.json), not an
  embeddings file. g0001's vectors were embedded under the same
  current nomic digest ~1 hour earlier, so hash-verified reuse
  is sound.
- Docs listed in --exclude-urls (the 48 repaired docs) contribute
  NO source rows: they take the EMBEDDED path and their repaired
  chunks are embedded fresh.
- A PRE-FLIGHT pass classifies every doc from text hashes
  alone and ABORTS (exit 3, nothing written, vectors_state
  untouched) if any doc would hit the vector-canonical path or
  any source row would be orphaned. By construction every
  non-excluded doc must be hash-exact matched.
- Synthetic-question vectors REUSE the source store's rows by
  sha1(question text) (the embedding is a pure function of the
  text under the same model build); only questions with no
  source row (the repaired sections' new template questions)
  embed fresh. Reused/fresh counts are logged.

Everything else (exactness assert, normalized matrix write,
meta/manifest/reconciliation updates) matches the stock tool.
"""
import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

TIMEOUT = 60


def sha1_text(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def embed(ollama, model, text):
    payload = json.dumps({"model": model,
                          "prompt": text[:2000]}).encode()
    req = urllib.request.Request(
        ollama + "/api/embeddings", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())["embedding"]


def embed_retry(ollama, model, text):
    try:
        return embed(ollama, model, text)
    except Exception:
        time.sleep(2)
        return embed(ollama, model, text)


def model_digest(ollama, model):
    try:
        req = urllib.request.Request(ollama + "/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            for m in json.loads(resp.read()).get("models", []):
                if m.get("name", "").split(":")[0] == model:
                    return m.get("digest")
    except Exception:
        return None
    return None


def load_source_store(src_dir, exclude_urls):
    """Return (rows, synthq_map) from a finalized generation
    store. rows: [{url, title, text, vec}] for every chunk whose
    doc URL is not excluded. synthq_map: sha1(question) -> vec,
    rebuilt from the source synthq matrix + index + enrich
    table, with section alignment asserted."""
    db = src_dir / f"corpus_{src_dir.name}.db"
    conn = sqlite3.connect(str(db))
    docs = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT doc_id, url, title FROM docs")}
    chunks = {}   # chunk row -> (url, title, text)
    for row, doc_id, text in conn.execute(
            "SELECT row, doc_id, text FROM chunks"):
        url, title = docs[doc_id]
        chunks[row] = (url, title, text)
    idx = json.loads((src_dir / "vectors_index.json")
                     .read_text(encoding="utf-8"))
    mat = np.load(src_dir / f"vectors_{src_dir.name}.npy")
    assert mat.shape[0] == len(idx["rows"]) == len(chunks), \
        "source store shape mismatch"
    rows = []
    for i, crow in enumerate(idx["rows"]):
        url, title, text = chunks[crow]
        if url in exclude_urls:
            continue
        rows.append({"url": url, "title": title, "text": text,
                     "vec": mat[i].astype(np.float32)})
    # synthq reuse map
    sq_idx = json.loads((src_dir / "synthq_index.json")
                        .read_text(encoding="utf-8"))
    sq_mat = np.load(src_dir / f"synthq_vectors_{src_dir.name}.npy")
    assert sq_mat.shape[0] == len(sq_idx), "source synthq shape"
    enrich = conn.execute(
        "SELECT section_id, questions FROM enrich").fetchall()
    qmap = {}
    pos = 0
    conflicts = 0
    for section_id, questions_json in enrich:
        for q in json.loads(questions_json or "[]"):
            assert sq_idx[pos] == section_id, \
                f"synthq alignment broke at row {pos}"
            h = sha1_text(q)
            v = sq_mat[pos].astype(np.float32)
            if h in qmap and np.max(np.abs(qmap[h] - v)) > 1e-5:
                conflicts += 1
            else:
                qmap[h] = v
            pos += 1
    assert pos == len(sq_idx), (pos, len(sq_idx))
    conn.close()
    log.info("source store: %d chunk rows usable (%d excluded "
             "docs), synthq map %d unique questions, %d conflicts",
             len(rows), len(exclude_urls), len(qmap), conflicts)
    return rows, qmap


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", required=True)
    ap.add_argument("--source-gen", required=True)
    ap.add_argument("--exclude-urls", required=True,
                    help="JSON file: list of doc URLs to embed "
                         "fresh (no source rows)")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="nomic-embed-text")
    args = ap.parse_args()

    gen_dir = Path(args.gen)
    src_dir = Path(args.source_gen)
    db_path = gen_dir / f"corpus_{gen_dir.name}.db"
    if not db_path.exists():
        log.error("generation db not found: %s", db_path)
        return 2
    exclude_urls = set(json.loads(Path(args.exclude_urls)
                                  .read_text(encoding="utf-8")))
    rows, qmap = load_source_store(src_dir, exclude_urls)
    by_url = {}
    for r in rows:
        by_url.setdefault(r["url"], []).append(r)

    conn = sqlite3.connect(str(db_path))
    docs = conn.execute(
        "SELECT doc_id, url, title FROM docs ORDER BY doc_id"
    ).fetchall()
    gen_urls = {u for _d, u, _t in docs}

    def doc_chunks(doc_id):
        return conn.execute(
            "SELECT row, chunk_id, text, text_hash FROM chunks"
            " WHERE doc_id=? ORDER BY ordinal", (doc_id,)).fetchall()

    # PRE-FLIGHT: classify from hashes; abort on canonical/orphan
    pre = {"matched": 0, "embedded": 0}
    for doc_id, url, _t in docs:
        vrows = by_url.get(url, [])
        g_hashes = {h for _r, _c, _t2, h in doc_chunks(doc_id)}
        v_hashes = {sha1_text(r["text"]) for r in vrows}
        if vrows and v_hashes == g_hashes:
            pre["matched"] += 1
        elif not vrows:
            pre["embedded"] += 1
        else:
            log.error("PRE-FLIGHT ABORT: doc %s would hit the "
                      "vector-canonical path (gen %d hashes, "
                      "source %d)", url, len(g_hashes),
                      len(v_hashes))
            return 3
    orphans = [u for u in by_url if u not in gen_urls]
    if orphans:
        log.error("PRE-FLIGHT ABORT: %d orphan source URLs",
                  len(orphans))
        return 3
    log.info("pre-flight classes: %s", pre)

    assigned = {}
    classes = {"matched": 0, "vector_canonical": 0,
               "embedded": 0, "orphan_rows_dropped": 0}
    for doc_id, url, title in docs:
        vrows = by_url.get(url, [])
        gchunks = doc_chunks(doc_id)
        if vrows:
            # hash -> ALL chunk rows carrying it: two chunks in
            # one doc can share identical text (measured: 2 docs
            # corpus-wide); every such row gets the vector.
            g_hashes = {}
            for row, cid, _t, h in gchunks:
                g_hashes.setdefault(h, []).append(row)
            for r in vrows:
                for row in g_hashes[sha1_text(r["text"])]:
                    assigned[row] = r["vec"]
            classes["matched"] += 1
            continue
        for row, _cid, text, _h in gchunks:
            assigned[row] = np.array(
                embed_retry(args.ollama, args.model, text),
                dtype=np.float32)
        classes["embedded"] += 1
    log.info("chunk classes measured: %s", classes)

    total_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks").fetchone()[0]
    if len(assigned) != total_chunks:
        log.error("exactness FAILED: %d chunks, %d vectors "
                  "assigned", total_chunks, len(assigned))
        return 1
    dim = len(next(iter(assigned.values())))
    mat = np.zeros((total_chunks, dim), dtype=np.float32)
    row_order = [r for (r,) in conn.execute(
        "SELECT row FROM chunks ORDER BY row")]
    for i, row in enumerate(row_order):
        mat[i] = assigned[row]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = (mat / norms).astype(np.float32)
    np.save(gen_dir / f"vectors_{gen_dir.name}.npy", mat)
    (gen_dir / "vectors_index.json").write_text(json.dumps(
        {"rows": row_order, "dim": dim}), encoding="utf-8")
    log.info("vectors written: %s rows x %d", mat.shape, dim)

    # synthetic-question vectors (V2 channel): reuse by text
    enrich = conn.execute(
        "SELECT section_id, questions FROM enrich").fetchall()
    q_rows, q_index = [], []
    n_reuse = n_fresh = 0
    for section_id, questions_json in enrich:
        for q in json.loads(questions_json or "[]"):
            h = sha1_text(q)
            if h in qmap:
                q_rows.append(qmap[h])
                n_reuse += 1
            else:
                q_rows.append(np.array(
                    embed_retry(args.ollama, args.model, q),
                    dtype=np.float32))
                n_fresh += 1
            q_index.append(section_id)
    log.info("synthq: %d reused, %d embedded fresh, %d total",
             n_reuse, n_fresh, len(q_rows))
    if q_rows:
        qmat = np.array(q_rows, dtype=np.float32)
        qn = np.linalg.norm(qmat, axis=1, keepdims=True)
        qn[qn == 0] = 1.0
        np.save(gen_dir / f"synthq_vectors_{gen_dir.name}.npy",
                (qmat / qn).astype(np.float32))
        (gen_dir / "synthq_index.json").write_text(
            json.dumps(q_index), encoding="utf-8")

    digest = model_digest(args.ollama, args.model)
    conn.execute(
        "UPDATE meta SET value=? WHERE key='vectors_state'",
        (json.dumps("ready"),))
    conn.execute(
        "UPDATE meta SET value=? WHERE key='generation'",
        (json.dumps(gen_dir.name),))
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('embedding_digest', ?)",
        (json.dumps(digest),))
    conn.commit()
    manifest_path = gen_dir / f"manifest_{gen_dir.name}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["vectors_state"] = "ready"
    manifest["vector_reconciliation_measured"] = classes
    manifest["vector_reconciliation_measured"]["synthq_reused"] = \
        n_reuse
    manifest["vector_reconciliation_measured"]["synthq_embedded"] = \
        n_fresh
    manifest["embedding_digest"] = digest
    manifest_path.write_text(json.dumps(manifest, indent=1),
                             encoding="utf-8")
    recon_path = gen_dir / "vector_reconciliation.json"
    recon = json.loads(recon_path.read_text(encoding="utf-8"))
    recon["vectors_state"] = "ready"
    recon["measured"] = classes
    recon_path.write_text(json.dumps(recon, indent=1),
                          encoding="utf-8")
    conn.close()
    log.info("generation %s vector layer READY", gen_dir.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
