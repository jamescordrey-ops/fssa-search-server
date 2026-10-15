#!/usr/bin/env python3
"""Finalize a generation's vector layer (run where the embeddings
file and the nomic embedder live — in practice the PC server dir).

Reconciliation, per the v22 consult, against the generation built
by build_generation.py:

- A doc whose vector-row text hashes exactly equal its generation
  chunk text hashes is MATCHED; rows join by sha1(text).
- A doc with vector rows that do not exactly match (the recorded
  393 mismatched URLs) is VECTOR-CANONICAL: its replica chunks are
  replaced by the vector file's chunk texts (marked
  origin='vector_file'), and its sections are rebuilt from them.
- A doc with no vector rows (the recorded 134 missing URLs) is
  EMBEDDED: its chunks are embedded with nomic-embed-text via
  Ollama and the rows appended.
- Vector rows whose URL is not a generation doc (the recorded 1
  orphan) are DROPPED.

Afterwards every chunk has exactly one vector row; the tool asserts
that before writing vectors_g0000.npy (float32, aligned to
chunks.row). Synthetic questions from the enrich table are embedded
into synthq_vectors_g0000.npy with synthq_index.json mapping rows to
section ids. meta.vectors_state flips to 'ready' only on success,
and the manifest records the exact class counts measured here (the
builder's recorded live numbers are expectations, not results).

Usage:
  python3 tools/finalize_vectors.py --gen generations/g0000 \
      --embeddings C:\\path\\to\\embeddings.npz \
      [--ollama http://127.0.0.1:11434] [--model nomic-embed-text]
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


def load_vector_rows(path):
    """Return [{url, title, text, vec}] from embeddings.npz or
    embeddings.json (the two server formats)."""
    if path.suffix == ".npz":
        with np.load(path) as z:
            vecs = z["vecs"]
            meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        return [{"url": m["url"], "title": m.get("title", ""),
                 "text": m["text"],
                 "vec": vecs[i].astype(np.float32)}
                for i, m in enumerate(meta)]
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{"url": c["url"], "title": c.get("title", ""),
             "text": c["text"],
             "vec": np.array(c["vec"], dtype=np.float32)}
            for c in data.get("chunks", [])]


def section_key_of(text):
    first = text.split("\n", 1)[0].strip()
    import re
    if re.match(r"^\d{4}\.\d{2}\.\d{2}(\.\d{2})? ", first):
        return first
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", required=True)
    ap.add_argument("--embeddings", required=True)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="nomic-embed-text")
    args = ap.parse_args()

    gen_dir = Path(args.gen)
    db_path = gen_dir / f"corpus_{gen_dir.name}.db"
    emb_path = Path(args.embeddings)
    if not db_path.exists():
        log.error("generation db not found: %s", db_path)
        return 2
    if not emb_path.exists():
        log.error("embeddings file not found: %s", emb_path)
        return 2

    rows = load_vector_rows(emb_path)
    log.info("vector rows loaded: %d", len(rows))
    by_url = {}
    for r in rows:
        by_url.setdefault(r["url"], []).append(r)

    conn = sqlite3.connect(str(db_path))
    docs = conn.execute(
        "SELECT doc_id, url, title FROM docs ORDER BY doc_id"
    ).fetchall()
    gen_urls = {u for _d, u, _t in docs}

    assigned = {}   # chunk row -> vec
    classes = {"matched": 0, "vector_canonical": 0, "embedded": 0,
               "orphan_rows_dropped": 0}
    next_row = conn.execute(
        "SELECT COALESCE(MAX(row), 0) FROM chunks").fetchone()[0]

    def doc_chunks(doc_id):
        return conn.execute(
            "SELECT row, chunk_id, text, text_hash FROM chunks"
            " WHERE doc_id=? ORDER BY ordinal", (doc_id,)).fetchall()

    for doc_id, url, title in docs:
        vrows = by_url.get(url, [])
        gchunks = doc_chunks(doc_id)
        g_hashes = {h: (row, cid) for row, cid, _t, h in gchunks}
        v_hashes = {sha1_text(r["text"]): r for r in vrows}
        if vrows and set(v_hashes) == set(g_hashes):
            for h, r in v_hashes.items():
                assigned[g_hashes[h][0]] = r["vec"]
            classes["matched"] += 1
            continue
        if vrows:
            # vector-canonical: replace this doc's chunks + sections
            conn.execute("DELETE FROM chunk_fts WHERE rowid IN"
                         " (SELECT row FROM chunks WHERE doc_id=?)",
                         (doc_id,))
            conn.execute("DELETE FROM chunks WHERE doc_id=?",
                         (doc_id,))
            conn.execute("DELETE FROM section_fts WHERE rowid IN"
                         " (SELECT row FROM sections WHERE doc_id=?)",
                         (doc_id,))
            conn.execute("DELETE FROM sections WHERE doc_id=?",
                         (doc_id,))
            by_section = {}
            for ordinal, r in enumerate(vrows):
                skey = section_key_of(r["text"])
                sid = sha1_text(f"{url}|{skey}")
                cid = sha1_text(
                    f"{url}|{skey}|{ordinal}|{sha1_text(r['text'])}")
                next_row += 1
                conn.execute(
                    "INSERT INTO chunks (row, chunk_id, doc_id,"
                    " ordinal, section_id, section_key, heading, text,"
                    " text_hash, origin) VALUES (?,?,?,?,?,?,?,?,?,"
                    " 'vector_file')",
                    (next_row, cid, doc_id, ordinal, sid, skey, skey,
                     r["text"], sha1_text(r["text"])))
                conn.execute(
                    "INSERT INTO chunk_fts (rowid, title, headings,"
                    " body) VALUES (?,?,?,?)",
                    (next_row, title, skey, r["text"]))
                assigned[next_row] = r["vec"]
                by_section.setdefault(sid, {
                    "key": skey, "first": ordinal, "bodies": []})
                by_section[sid]["bodies"].append(r["text"])
            for sid, s in by_section.items():
                cur = conn.execute(
                    "INSERT INTO sections (section_id, doc_id,"
                    " section_key, heading, title, first_ordinal,"
                    " chunk_count, text) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, doc_id, s["key"], s["key"], title,
                     s["first"], len(s["bodies"]),
                     "\n".join(s["bodies"])))
                conn.execute(
                    "INSERT INTO section_fts (rowid, title, headings,"
                    " body, synth_q, aliases) VALUES (?,?,?,?,?,?)",
                    (cur.lastrowid, title, s["key"],
                     "\n".join(s["bodies"]), "", ""))
            classes["vector_canonical"] += 1
            continue
        # no vector rows: embed every chunk
        for row, _cid, text, _h in gchunks:
            assigned[row] = np.array(
                embed_retry(args.ollama, args.model, text),
                dtype=np.float32)
        classes["embedded"] += 1

    for url, vrows in by_url.items():
        if url not in gen_urls:
            classes["orphan_rows_dropped"] += len(vrows)

    total_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks").fetchone()[0]
    if len(assigned) != total_chunks:
        log.error("exactness FAILED: %d chunks, %d vectors assigned",
                  total_chunks, len(assigned))
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
    log.info("vectors written: %s rows x %d; classes: %s",
             mat.shape, dim, classes)

    # synthetic-question vectors (V2 channel)
    enrich = conn.execute(
        "SELECT section_id, questions FROM enrich").fetchall()
    q_rows, q_index = [], []
    for section_id, questions_json in enrich:
        for q in json.loads(questions_json or "[]"):
            q_rows.append(embed_retry(args.ollama, args.model, q))
            q_index.append(section_id)
    if q_rows:
        qmat = np.array(q_rows, dtype=np.float32)
        qn = np.linalg.norm(qmat, axis=1, keepdims=True)
        qn[qn == 0] = 1.0
        np.save(gen_dir / f"synthq_vectors_{gen_dir.name}.npy",
                (qmat / qn).astype(np.float32))
        (gen_dir / "synthq_index.json").write_text(
            json.dumps(q_index), encoding="utf-8")
        log.info("synthq vectors written: %d", len(q_rows))

    digest = model_digest(args.ollama, args.model)
    conn.execute(
        "UPDATE meta SET value=? WHERE key='vectors_state'",
        (json.dumps("ready"),))
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('embedding_digest', ?)",
        (json.dumps(digest),))
    conn.commit()
    manifest_path = gen_dir / f"manifest_{gen_dir.name}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["vectors_state"] = "ready"
    manifest["vector_reconciliation_measured"] = classes
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
