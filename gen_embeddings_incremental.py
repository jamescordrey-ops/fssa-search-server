#!/usr/bin/env python3
"""Incrementally add embeddings for new corpus docs to embeddings.json.

Matches the format produced by gen_embeddings.py: embeddings.json is
{"model": ..., "dim": ..., "chunks": [{"id", "doc_idx", "chunk_idx",
"title", "url", "text", "vec"}]} and each chunk is embedded as
"title + ' ' + chunk". Docs already present (by URL) are skipped.
Saves after every doc so an interrupted run keeps its progress.

Run on the PC (Ollama at localhost) from the fssa-server directory.
"""
import json
import logging
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HOME = Path(__file__).resolve().parent  # scripts run from the repo/server directory
CORPUS_IN = HOME / "corpus.json"
CORPUS_FED = HOME / "corpus_federal.json"
EMB_PATH = HOME / "embeddings.json"

OLLAMA = "http://localhost:11434"
MODEL = "nomic-embed-text"
CHUNK_SIZE = 600
OVERLAP = 100
MIN_TEXT = 200


def chunk_text(text, size=CHUNK_SIZE, overlap=OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def embed(text):
    payload = json.dumps(
        {"model": MODEL, "prompt": text[:2000]}
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["embedding"]


def load_docs():
    """All corpus docs with enough text, in full-regen order (IN then FED)."""
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


def main():
    data = json.loads(EMB_PATH.read_text(encoding="utf-8"))
    chunks = data.get("chunks", [])
    embedded_urls = {c.get("url") for c in chunks}
    next_id = max((c.get("id", -1) for c in chunks), default=-1) + 1
    log.info("Existing chunks: %d", len(chunks))

    docs = load_docs()
    todo = [
        (idx, d) for idx, d in enumerate(docs) if d["url"] not in embedded_urls
    ]
    log.info("New docs to embed: %d", len(todo))

    added = 0
    for n, (doc_idx, doc) in enumerate(todo, 1):
        pieces = chunk_text(doc["text"])
        log.info("[%d/%d] %s - %d chunks", n, len(todo), doc["title"][:50], len(pieces))
        for ci, piece in enumerate(pieces):
            try:
                vec = embed(f"{doc['title']} {piece}")
            except Exception as e:  # noqa: BLE001 - log and continue per chunk
                log.info("  chunk %d failed: %s", ci, e)
                continue
            chunks.append(
                {
                    "id": next_id,
                    "doc_idx": doc_idx,
                    "chunk_idx": ci,
                    "title": doc["title"],
                    "url": doc["url"],
                    "text": piece,
                    "vec": vec,
                }
            )
            next_id += 1
            added += 1
        if n % 25 == 0:
            data["chunks"] = chunks
            EMB_PATH.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
            log.info("  saved, total chunks: %d", len(chunks))

    data["chunks"] = chunks
    EMB_PATH.write_text(
        json.dumps(data, separators=(",", ":")), encoding="utf-8"
    )
    log.info("Done. Added %d chunks. Total: %d", added, len(chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
