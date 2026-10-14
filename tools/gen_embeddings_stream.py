#!/usr/bin/env python3
"""Regenerate embeddings for all corpus docs, streaming to JSONL first.

Why: the old whole-file approach held the entire embeddings set in RAM
and died with MemoryError mid-save (2026-10-12), truncating
embeddings.json to 0 bytes. This version keeps memory flat:

- Each doc's chunks are embedded, then appended to embeddings.jsonl in a
  single write + flush. Progress survives interruption.
- On startup, docs already present in the JSONL (by URL) are skipped.
- Finalize streams the JSONL into embeddings.json in the server format
  {"model", "dim", "chunks": [...]} without loading it all.

Run on the PC (Ollama at localhost). Safe to re-run after a crash.
"""
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HOME = Path(__file__).resolve().parent  # scripts run from the repo/server directory
CORPUS_IN = HOME / "corpus.json"
CORPUS_FED = HOME / "corpus_federal.json"
JSONL_PATH = HOME / "embeddings.jsonl"
EMB_PATH = HOME / "embeddings.json"

OLLAMA = "http://localhost:11434"
MODEL = "nomic-embed-text"
CHUNK_SIZE = 600
OVERLAP = 100
MIN_TEXT = 200


_SECTION_HEAD_RX = re.compile(
    r"(?m)^[ \t]*(\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?)[ \t]*\n"
    r"[ \t]*([A-Z][A-Z0-9 /&()\-',]{2,78}?)[ \t]*$")


def _split_sections(text):
    """Split DFR-manual-style text into (heading, body) sections.
    KEEP IN SYNC with the copy in fssa_search_server.py."""
    ms = list(_SECTION_HEAD_RX.finditer(text))
    if len(ms) < 3:
        return []
    secs = []
    preface = text[:ms[0].start()]
    if preface.strip():
        secs.append(("", preface))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        heading = f"{m.group(1)} {m.group(2).strip()}"
        secs.append((heading, text[m.end():end]))
    return secs


def _window(text, size, overlap):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    """Section-aware chunking (2026-10-12): manual-style documents are
    windowed within each numbered section and every chunk is prefixed
    with its section heading, so embeddings carry the heading ('2615.60.00
    VEHICLES') alongside the rule text. Other documents window as before."""
    secs = _split_sections(text)
    if not secs:
        return _window(text, size, overlap)
    chunks = []
    for heading, body in secs:
        prefix = f"{heading}\n" if heading else ""
        for piece in _window(body, size, overlap):
            if piece.strip():
                chunks.append(prefix + piece)
    return chunks or _window(text, size, overlap)


def embed(text):
    payload = json.dumps({"model": MODEL, "prompt": text[:2000]}).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["embedding"]


def load_docs():
    """All corpus docs with enough text, IN first then federal."""
    docs = []
    for path in (CORPUS_IN, CORPUS_FED):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        items = (
            data
            if isinstance(data, list)
            else data.get("docs", data.get("documents", []))
        )
        for d in items:
            text = d.get("text", d.get("content", d.get("body", "")))
            if len(text) < MIN_TEXT:
                continue
            docs.append(
                {"title": d.get("title", ""), "url": d.get("url", ""), "text": text}
            )
    return docs


def done_urls():
    urls = set()
    if not JSONL_PATH.exists():
        return urls
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            try:
                urls.add(json.loads(line)["url"])
            except Exception:  # noqa: BLE001 - tolerate a truncated last line
                continue
    return urls


def peek_dim():
    """Vector dimension from the first valid JSONL line."""
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            try:
                return len(json.loads(line)["vec"])
            except Exception:  # noqa: BLE001 - keep looking
                continue
    return 0


def finalize():
    """Stream embeddings.jsonl into embeddings.json (server format)."""
    dim = peek_dim()
    tmp = EMB_PATH.with_suffix(".json.tmp")
    count = 0
    with JSONL_PATH.open(encoding="utf-8") as src, tmp.open(
        "w", encoding="utf-8"
    ) as out:
        out.write('{"model": "%s", "dim": %d, "chunks": [' % (MODEL, dim))
        first = True
        for line in src:
            try:
                ch = json.loads(line)
            except Exception:  # noqa: BLE001 - skip truncated line
                continue
            ch["id"] = count
            if not first:
                out.write(",")
            out.write(json.dumps(ch, separators=(",", ":")))
            first = False
            count += 1
        out.write("]}")
    tmp.replace(EMB_PATH)
    log.info("Finalized embeddings.json: %d chunks, dim %d", count, dim)
    return count


def main():
    docs = load_docs()
    done = done_urls()
    todo = [(i, d) for i, d in enumerate(docs) if d["url"] not in done]
    log.info("Docs total %d, already done %d, to embed %d",
             len(docs), len(done), len(todo))

    with JSONL_PATH.open("a", encoding="utf-8") as out:
        for n, (doc_idx, doc) in enumerate(todo, 1):
            pieces = chunk_text(doc["text"])
            lines = []
            for ci, piece in enumerate(pieces):
                try:
                    vec = embed(f"{doc['title']} {piece}")
                except Exception as e:  # noqa: BLE001 - log, skip chunk
                    log.info("  chunk %d failed: %s", ci, e)
                    continue
                lines.append(json.dumps({
                    "doc_idx": doc_idx,
                    "chunk_idx": ci,
                    "title": doc["title"],
                    "url": doc["url"],
                    "text": piece,
                    "vec": vec,
                }, separators=(",", ":")))
            if lines:
                out.write("\n".join(lines) + "\n")
                out.flush()
            if n % 50 == 0 or n == len(todo):
                log.info("[%d/%d] docs embedded", n, len(todo))

    finalize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
