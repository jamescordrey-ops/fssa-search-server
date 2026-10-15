#!/usr/bin/env python3
"""Build a v22 corpus generation (g0000) from the versioned corpora.

A generation is an immutable directory:

    generations/g0000/corpus_g0000.db    SQLite: docs, chunks, sections,
                                         FTS5 tables, enrichment, registry
    generations/g0000/manifest_g0000.json
    generations/g0000/vector_reconciliation.json
    generations/CURRENT                  one line: the generation id

Chunking replicates the GENERATION pipeline (gen_embeddings_stream.py)
exactly: 600-char windows, 100 overlap, section-aware over the same
_split_sections, heading prefixed onto every window piece, documents
under 200 chars skipped, first document per URL wins (IN file first,
then federal, the load order of gen_embeddings_stream.load_docs).

Chunk identity is content-hashed:
    chunk_id = sha1(url | section_key | ordinal | sha1(text))
so a rebuilt generation addresses the same chunk by the same id
whenever its text is unchanged (the v19 dedupe-resurrection class of
bug came from ordinal addressing into a changing build file).

Sections group a document's chunks by their section heading (the
generation chunker's own split). Retrieval finds sections; serving
cites chunks.

The vector layer is reconciled by finalize_vectors.py where the
embeddings file and the nomic embedder live (the PC). This builder
writes vector_reconciliation.json describing the exact classes that
step must resolve; meta.vectors_state records "pending" until it
runs. Nothing in this builder invents a vector.

Usage:
  python3 tools/build_generation.py [--out generations] [--gen g0000]
"""
import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent
MIN_TEXT = 200
CHUNK_SIZE = 600
OVERLAP = 100

_SECTION_HEAD_RX = re.compile(
    r"(?m)^[ \t]*(\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?)[ \t]*\n"
    r"[ \t]*([A-Z][A-Z0-9 /&()\-',]{2,78}?)[ \t]*$")


def _split_sections(text):
    """Replica of gen_embeddings_stream._split_sections (keep in sync)."""
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


def _window(text, size=CHUNK_SIZE, overlap=OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def chunk_text_sections(text):
    """The generation chunker, returning (section_heading, chunk_text)
    pairs. chunk_text matches gen_embeddings_stream.chunk_text output
    exactly (heading prefixed onto each window piece)."""
    secs = _split_sections(text)
    if not secs:
        return [("", piece) for piece in _window(text)]
    out = []
    for heading, body in secs:
        prefix = f"{heading}\n" if heading else ""
        for piece in _window(body):
            if piece.strip():
                out.append((heading, prefix + piece))
    return out or [("", piece) for piece in _window(text)]


def sha1_text(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
]


def jurisdiction_of(title, url, text):
    """Jurisdiction from content and host, never from which JSON file
    carried the document (the INvestABLE booklet sits in the federal
    file and is an Indiana document)."""
    host = url.lower()
    head = (title + "\n" + text[:3000]).lower()
    if "in.gov" in host:
        return "IN"
    if "indiana" in head or "investable" in head:
        return "IN"
    for st in US_STATES:
        if st in head:
            return "other-state"
    return "US"


def doc_type_of(title, url):
    t = title.lower()
    u = url.lower()
    if "promod" in t or "provider reference module" in t:
        return "provider module"
    if "poms" in t or "poms" in u:
        return "POMS operations manual"
    if "policy manual" in t or "medicaid_pm" in u or "ihcppm" in t \
            or t.startswith("ihcppm"):
        return "policy manual"
    if "law.cornell.edu" in u or "usc" in t or "cfr" in t:
        return "statute/regulation"
    if "federalregister.gov" in u:
        return "regulation"
    if "press release" in t or "news release" in t:
        return "press release"
    if "bulletin" in t:
        return "bulletin"
    return "agency page"


AUTHORITY = {
    "provider module": 3, "policy manual": 3, "POMS operations manual": 3,
    "statute/regulation": 3, "regulation": 3, "bulletin": 2,
    "press release": 2, "agency page": 2,
}


def load_docs():
    docs = []
    seen = set()
    for name, tag in (("corpus_v19.json", "in"),
                      ("corpus_federal_v19.json", "federal")):
        data = json.loads((SERVER_DIR / name).read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("docs", [])
        for d in items:
            text = d.get("text", d.get("content", d.get("body", "")))
            url = d.get("url", "")
            if len(text) < MIN_TEXT or not url or url in seen:
                continue
            seen.add(url)
            docs.append({"title": d.get("title", ""), "url": url,
                         "text": text, "source_file": tag,
                         "tags": d.get("tags")})
    return docs


def load_registry():
    """VERIFIED_FIGURES (+ v21 topic metadata) from the server module,
    imported the harness way (temp CWD with corpus symlinks)."""
    sys.path.insert(0, str(HERE))
    import retrieval_regression as rr
    mod = rr.load_server(str(SERVER_DIR))
    entries = []
    for e in mod.VERIFIED_FIGURES:
        meta = getattr(mod, "_V21_REGISTRY_META", {}).get(e["id"], {})
        entries.append({
            "id": e["id"], "title": e.get("title", ""),
            "figures": [float(x) for x in e.get("figures", ())],
            "topic": meta.get("topic") or e.get("topic"),
            "source": e.get("source", ""),
        })
    return entries


def figure_forms(value):
    forms = set()
    if value == int(value):
        iv = int(value)
        forms.add(f"${iv:,}")
        forms.add(f"{iv:,}")
        forms.add(str(iv))
    else:
        forms.add(f"${value:,.2f}")
        forms.add(f"{value:,.2f}")
        forms.add(str(value))
    return forms


def lint_registry(conn, entries):
    """Resolve each entry's source doc by its topic doc_hint and lint:
    every figure must appear in the doc text; extract the first
    figure-bearing sentence as the entry's verbatim quote."""
    rows = []
    doc_rows = list(conn.execute(
        "SELECT doc_id, title, url FROM docs ORDER BY doc_id"))
    texts = {}
    for e in entries:
        hint = (e.get("topic") or {}).get("doc_hint") or {}
        ht, hu = (hint.get("title") or "").lower(), \
            (hint.get("url") or "").lower()
        # Score the hint: a title phrase/word match outweighs a URL
        # substring (a bare 'ssi' url hint matches every fssa.gov
        # URL; a bare 'able' title substring matches 'Disabled').
        # Among the hint candidates, the carrier is then VERIFIED:
        # the doc containing the most of the entry's figures wins
        # (the ABLE hint alone lands on pages that never state the
        # entry's figures).
        scored = []
        for did, title, url in doc_rows:
            score = 0
            if ht:
                if re.search(r"(?<![a-z0-9])" + re.escape(ht) +
                             r"(?![a-z0-9])", title.lower()):
                    score += 2
                elif ht in title.lower():
                    score += 1
            if hu and hu in url.lower():
                score += 1
            if score:
                scored.append((score, did))
        scored.sort(key=lambda x: (-x[0], x[1]))
        candidates = [did for _s, did in scored[:6]]

        def _text_of(did):
            if did not in texts:
                row = conn.execute(
                    "SELECT text FROM doc_texts WHERE doc_id=?",
                    (did,)).fetchone()
                texts[did] = row[0] if row else ""
            return texts[did]

        doc_id = None
        if candidates:
            if e["figures"]:
                def _nfig(did):
                    low = _text_of(did).lower()
                    return sum(1 for fig in e["figures"]
                               if any(f.lower() in low
                                      for f in figure_forms(fig)))
                doc_id = max(candidates, key=lambda d: (_nfig(d), -d))
                if _nfig(doc_id) == 0:
                    # No hint candidate carries the figures: scan the
                    # whole corpus for the doc that carries the most
                    # of them (the CSRA figures live on the CMS/ILTCP
                    # pages, not in the hinted MMMNA section).
                    best_n = 0
                    for did, _t, _u in doc_rows:
                        n = _nfig(did)
                        if n > best_n:
                            best_n, doc_id = n, did
            else:
                doc_id = candidates[0]
        figures_found, figures_missing, quote = [], [], None
        if doc_id is not None:
            if doc_id not in texts:
                row = conn.execute(
                    "SELECT text FROM doc_texts WHERE doc_id=?",
                    (doc_id,)).fetchone()
                texts[doc_id] = row[0] if row else ""
            text = texts[doc_id]
            low = text.lower()
            for fig in e["figures"]:
                hit_form = next((f for f in figure_forms(fig)
                                 if f.lower() in low), None)
                if hit_form:
                    figures_found.append(fig)
                    if quote is None:
                        idx = low.find(hit_form.lower())
                        start = max(0, text.rfind(". ", 0, idx) + 1)
                        end = text.find(". ", idx)
                        quote = text[start:(end + 1 if end > 0
                                            else idx + 200)].strip()
                else:
                    figures_missing.append(fig)
        rows.append({
            "entry_id": e["id"], "doc_id": doc_id,
            "figures_found": figures_found,
            "figures_missing": figures_missing,
            "verbatim_quote": quote,
            "status": ("unresolved-doc" if doc_id is None else
                       "figures-missing" if figures_missing else
                       "ok" if e["figures"] else "no-figures"),
        })
    return rows


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE docs (
  doc_id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT,
  source_file TEXT, jurisdiction TEXT, doc_type TEXT,
  authority_tier INTEGER, program_tags TEXT, content_hash TEXT,
  text_len INTEGER, edition_date TEXT, superseded_by INTEGER);
CREATE TABLE doc_texts (doc_id INTEGER PRIMARY KEY, text TEXT);
CREATE TABLE chunks (
  row INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE, doc_id INTEGER,
  ordinal INTEGER, section_id TEXT, section_key TEXT, heading TEXT,
  text TEXT, text_hash TEXT, origin TEXT DEFAULT 'corpus');
CREATE TABLE sections (
  row INTEGER PRIMARY KEY, section_id TEXT UNIQUE, doc_id INTEGER,
  section_key TEXT, heading TEXT, title TEXT, first_ordinal INTEGER,
  chunk_count INTEGER, text TEXT);
CREATE TABLE enrich (
  section_id TEXT PRIMARY KEY, questions TEXT, aliases TEXT,
  figures TEXT, scope_tags TEXT, writer TEXT, version INTEGER,
  updated TEXT);
CREATE TABLE registry_sources (
  entry_id TEXT PRIMARY KEY, doc_id INTEGER, figures_found TEXT,
  figures_missing TEXT, verbatim_quote TEXT, status TEXT);
CREATE VIRTUAL TABLE chunk_fts USING fts5(title, headings, body);
CREATE VIRTUAL TABLE section_fts USING fts5(
  title, headings, body, synth_q, aliases);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);
CREATE INDEX idx_chunks_section ON chunks(section_id);
CREATE INDEX idx_sections_doc ON sections(doc_id);
"""


def build(out_dir, gen_id):
    gen_dir = out_dir / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    db_path = gen_dir / f"corpus_{gen_id}.db"
    if db_path.exists():
        db_path.unlink()
    docs = load_docs()
    log.info("docs (manifest order, first-URL-wins, >=%d chars): %d",
             MIN_TEXT, len(docs))
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    chunk_row = 0
    section_row = 0
    total_chunks = 0
    for doc in docs:
        cur = conn.execute(
            "INSERT INTO docs (url, title, source_file, jurisdiction,"
            " doc_type, authority_tier, program_tags, content_hash,"
            " text_len) VALUES (?,?,?,?,?,?,?,?,?)",
            (doc["url"], doc["title"], doc["source_file"],
             jurisdiction_of(doc["title"], doc["url"], doc["text"]),
             doc_type_of(doc["title"], doc["url"]),
             AUTHORITY[doc_type_of(doc["title"], doc["url"])],
             json.dumps(doc.get("tags") or []),
             sha1_text(doc["text"]), len(doc["text"])))
        doc_id = cur.lastrowid
        conn.execute("INSERT INTO doc_texts VALUES (?,?)",
                     (doc_id, doc["text"]))
        pieces = chunk_text_sections(doc["text"])
        by_section = {}
        for ordinal, (heading, chunk_text) in enumerate(pieces):
            section_key = heading
            section_id = sha1_text(f"{doc['url']}|{section_key}")
            chunk_id = sha1_text(
                f"{doc['url']}|{section_key}|{ordinal}|"
                f"{sha1_text(chunk_text)}")
            chunk_row += 1
            total_chunks += 1
            conn.execute(
                "INSERT INTO chunks (row, chunk_id, doc_id, ordinal,"
                " section_id, section_key, heading, text, text_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (chunk_row, chunk_id, doc_id, ordinal, section_id,
                 section_key, heading, chunk_text,
                 sha1_text(chunk_text)))
            conn.execute(
                "INSERT INTO chunk_fts (rowid, title, headings, body)"
                " VALUES (?,?,?,?)",
                (chunk_row, doc["title"], heading, chunk_text))
            by_section.setdefault(section_id, {
                "section_key": section_key, "heading": heading,
                "first": ordinal, "bodies": []})
            body = chunk_text[len(heading) + 1:] \
                if heading and chunk_text.startswith(heading + "\n") \
                else chunk_text
            by_section[section_id]["bodies"].append(body)
        for section_id, s in by_section.items():
            section_row += 1
            text = "\n".join(s["bodies"])
            conn.execute(
                "INSERT INTO sections (row, section_id, doc_id,"
                " section_key, heading, title, first_ordinal,"
                " chunk_count, text) VALUES (?,?,?,?,?,?,?,?,?)",
                (section_row, section_id, doc_id, s["section_key"],
                 s["heading"], doc["title"], s["first"],
                 len(s["bodies"]), text))
            conn.execute(
                "INSERT INTO section_fts (rowid, title, headings, body,"
                " synth_q, aliases) VALUES (?,?,?,?,?,?)",
                (section_row, doc["title"], s["heading"], text, "", ""))
    log.info("chunks: %d; sections: %d", total_chunks, section_row)
    # Typo vocabulary, materialized from fts5vocab at build time
    # (a runtime fts5vocab table cannot be created against the
    # read-only store: temp-schema resolution looks the source up
    # in temp). One row per term with its chunk count.
    conn.execute("CREATE TABLE vocab (term TEXT PRIMARY KEY,"
                 " doc_count INTEGER)")
    conn.execute("CREATE VIRTUAL TABLE _vocab_src"
                 " USING fts5vocab(chunk_fts, 'row')")
    conn.execute("INSERT INTO vocab SELECT term, COUNT(*)"
                 " FROM _vocab_src GROUP BY term")
    conn.execute("DROP TABLE _vocab_src")
    vocab_n = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
    log.info("vocab terms: %d", vocab_n)
    entries = load_registry()
    lint = lint_registry(conn, entries)
    for r in lint:
        conn.execute(
            "INSERT INTO registry_sources VALUES (?,?,?,?,?,?)",
            (r["entry_id"], r["doc_id"],
             json.dumps(r["figures_found"]),
             json.dumps(r["figures_missing"]),
             r["verbatim_quote"], r["status"]))
    meta = {
        "generation": gen_id,
        "chunker": {"size": CHUNK_SIZE, "overlap": OVERLAP,
                    "min_text": MIN_TEXT, "section_aware": True},
        "docs": len(docs), "chunks": total_chunks,
        "sections": section_row,
        "vectors_state": "pending",
        "embedding_model": "nomic-embed-text",
    }
    for k, v in meta.items():
        conn.execute("INSERT INTO meta VALUES (?,?)",
                     (k, json.dumps(v)))
    conn.commit()
    manifest = dict(meta)
    manifest["registry_lint"] = lint
    (gen_dir / f"manifest_{gen_id}.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    recon = {
        "vectors_state": "pending",
        "why": "embeddings.npz/embeddings.json and the nomic embedder "
               "(Ollama) live on the PC; this build ran on the VM "
               "under the stage's no-PC-access boundary.",
        "recorded_live_state": {
            "source": "v21 deploy /health record, 2026-10-14",
            "embeddings_rows": 73014,
            "manifest_rows_expected": total_chunks,
            "delta_rows": 73014 - total_chunks,
            "missing_urls": 134, "mismatched_urls": 393,
            "orphan_urls": 1},
        "finalize": "tools/finalize_vectors.py, run where the "
                    "embeddings file and Ollama live: joins vector "
                    "rows to chunks by sha1(text); vector-file chunk "
                    "texts are canonical for mismatched URLs (they "
                    "replace the replica chunks for those docs, "
                    "marked origin='vector_file'); chunks of missing "
                    "URLs are embedded with nomic-embed-text and "
                    "appended; orphan rows are dropped; synthetic-"
                    "question vectors are embedded into "
                    "synthq_vectors; the result must satisfy exact "
                    "consistency (every chunk has exactly one "
                    "vector row) before meta.vectors_state flips "
                    "to 'ready'.",
    }
    (gen_dir / "vector_reconciliation.json").write_text(
        json.dumps(recon, indent=1), encoding="utf-8")
    (out_dir / "CURRENT").write_text(gen_id + "\n", encoding="utf-8")
    conn.close()
    log.info("generation %s built at %s", gen_id, gen_dir)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(SERVER_DIR / "generations"))
    ap.add_argument("--gen", default="g0000")
    args = ap.parse_args()
    return build(Path(args.out), args.gen)


if __name__ == "__main__":
    sys.exit(main())
