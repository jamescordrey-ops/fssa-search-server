# FSSA Search Server

A local search and question-answering system for Medicaid and benefits
policy. The rules that govern waiver and benefits life are scattered
across hundreds of manuals, PDFs, and agency pages that do not talk to
each other; this pulls them into one corpus and answers plain-language
questions with citations, running entirely on a home machine.

> **About (for the repo sidebar):** Local search and Q&A over Indiana
> Medicaid/FSSA and federal benefits policy documents. Hybrid keyword +
> vector retrieval, cited answers, figures checked against the sources.
> Runs fully local with Ollama; an optional Gemini chain can front it.

## What it does

- **Corpus**: 5,228 documents in two files: `corpus.json` (1,279 Indiana
  sources: FSSA and DFR policy manuals, Medicaid program pages, waiver
  documents, PathWays, IHCP bulletins) and `corpus_federal.json` (3,949
  federal sources: Medicaid.gov and CMS guidance, SSA material including
  SSI rules, the Red Book, Ticket to Work / Choose Work, representative
  payee and overpayment pages, and 529 sections of SSA's POMS operations
  manual; IRS difficulty-of-care material; SNAP categorical-eligibility
  rules; Medicare Savings Program pages).
- **Hybrid retrieval**: documents are chunked (600 chars, 100 overlap)
  and indexed two ways at once: keyword search and vector search
  (nomic-embed-text embeddings), fused with reciprocal rank fusion and
  an LLM reranking pass. Queries are rewritten into policy-manual
  phrasing before searching, and deterministic "topic bridges" map lay
  terms to manual language for known-hard topics (disaster assistance,
  lump sums, exempt vehicles, spousal impoverishment, retroactive
  coverage); a bridge's top chunk is pinned into the answer excerpts so
  the operative rule statement is in front of the answering model.
- **Source ranking**: on Indiana questions, Indiana sources outrank
  federal generic pages, and program manuals outrank administrative
  pages, using per-document {program, doc_type, year} tags.
- **Verified figures**: a small registry of hand-verified, frequently
  contested figures (for example the nine-month exclusion for
  retroactive benefits, Indiana's $5,000 SNAP resource limit under
  BBCE, the community spouse resource allowance formula and its
  current range) is injected with top priority when a question touches
  one, and every dollar figure in a draft answer is checked against
  the excerpts and the registry before the answer is returned; an
  unsupported figure triggers one regeneration with a correction note.
- **Deterministic math**: formula questions the server can compute
  itself (the community spouse resource allowance is the built-in
  case) are computed in code, not by the model, and answers must show
  calculations as explicit equations, which the server recomputes and
  visibly corrects when wrong.
- **Bundled questions**: multi-part questions are decomposed into
  sub-questions, retrieved per part in parallel, and answered part by
  part so no part is silently dropped.
- **Discovery mode**: open-ended questions ("what programs are
  available to me") get a survey across five areas (health coverage,
  food, cash and disability benefits, home and community services,
  work supports) with an explicit "not covered by the documents
  provided" line.
- **Models**: local first. qwen3.5:9b via Ollama is the default
  (nothing leaves the machine). Questions the router classifies as
  hard (contested figures, bundled multi-part) go to qwen3:14b with
  the smaller model unloaded first, falling back to the 9B on error.
  An optional Gemini chain (3.5 Flash Lite primary, 3.8 Flash
  reserve, local fallback) can be enabled with your own API key; the
  response always names the model that actually answered.
- **Speed**: embeddings ship as a binary `embeddings.npz` and vector
  search is a single matrix multiply; the server is healthy about
  10 seconds after start. `/ask` responses carry per-stage timing
  ({retrieve_ms, generate_ms, total_ms}).

Answers quote dollar figures directly, flag contradictions between
sources instead of blending them, lead with an applicable exclusion
before the general rule, and say plainly when the documents do not
contain the answer.

## Architecture

```
public sources (in.gov/fssa, medicaid.gov, ssa.gov, medicare.gov, irs.gov, fns.usda.gov)
        |  polite crawl + PDF extraction (parse_pdfs.py)
        v
corpus.json + corpus_federal.json        (documents: title, url, source, text)
        |  tag_docs.py stamps {program, doc_type, year}
        v
gen_embeddings_stream.py  ->  embeddings.jsonl   (nomic-embed-text via Ollama)
dedupe_chunks.py          ->  drops near-duplicate chunks
build_embeddings_v11.py   ->  embeddings.json    (server format)
make_npz.py               ->  embeddings.npz     (float32 matrix + chunk metadata)
        v
fssa_search_server.py     ->  hybrid search + /ask Q&A + webapp on port 8471
```

## Prerequisites

- Python 3.11+ (developed on 3.12). The server itself uses the
  standard library plus numpy (vector search). `pip install -r
  requirements.txt` (PyMuPDF is only needed by `parse_pdfs.py`).
- [Ollama](https://ollama.com) running locally, with models:
  - `ollama pull qwen3.5:9b` (default answering model)
  - `ollama pull nomic-embed-text` (embeddings)
  - `ollama pull qwen3:14b` (optional; routed hard questions)
- Optional: a Google AI Studio API key if you want the Gemini chain.

## Rebuild from scratch

```bash
git clone https://github.com/jamescordrey-ops/fssa-search-server.git
cd fssa-search-server
pip install -r requirements.txt

# 1. Build embeddings from the shipped corpora (takes a while on
#    first run; safe to interrupt and re-run, progress is kept)
python gen_embeddings_stream.py        # -> embeddings.jsonl, then embeddings.json
python dedupe_chunks.py                # -> dedupe_skip.json (chunks to omit)
python build_embeddings_v11.py         # -> embeddings.json with dedupe applied
python make_npz.py                     # -> embeddings.npz (fast server startup)

# 2. (Optional) enable the Gemini chain: put your own API key in a
#    file named gemini_key.txt in this directory. It is gitignored;
#    never commit it.

# 3. Run the server
python fssa_search_server.py 8471
```

Then open http://localhost:8471/ for the Q&A webapp.

To add new documents later: append them to a corpus file (same
{title, url, source, text} shape), stamp tags with
`python tag_docs.py corpus_in.json corpus_out.json`, and embed just
the additions with `python gen_embeddings_incremental.py`, then
re-run `make_npz.py`.

## Endpoints

- `GET /` - the Q&A webapp
- `GET /health` - status, document counts, embeddings loaded
- `GET /search?q=...&corpus=in|federal|both` - hybrid search results
- `POST /ask` - JSON `{question, model, corpus}`; returns an answer
  with citations, a confidence line, the source excerpts, per-stage
  timing, and metadata naming the model that actually answered.
  Pass `"fresh": true` (or `nocache`) to bypass the answer cache,
  which is what you want when testing changes.

## Files

- `fssa_search_server.py` - the server and webapp (single file)
- `corpus.json`, `corpus_federal.json` - the document corpora
- `gen_embeddings_stream.py` - streaming embedding generation (never
  whole-file-write embeddings.json; it does not fit in memory)
- `gen_embeddings_incremental.py` - embed only newly added documents
- `dedupe_chunks.py` - near-duplicate chunk detection
- `build_embeddings_v11.py` - finalize embeddings.json with dedupe applied
- `make_npz.py` - embeddings.json -> embeddings.npz converter
- `tag_docs.py` - ingest-time {program, doc_type, year} tagging
- `parse_pdfs.py` - download and extract PDFs into the corpus
  (reads a `pdf_urls.json` list you provide)

Generated files (embeddings, logs, answer caches) are gitignored and
are never committed; the corpora in the repo are the only data files.

## Corpus notes

The corpora are snapshots of public documents from October 2026.
Two hand-written reference notes that shipped in early versions of
`corpus.json` were removed from the public corpus: they summarized
public sources (the DDARS HCBS waiver provider manual and the FSSA
site), and a rebuilder who wants that material can re-add the
underlying public documents from in.gov. A small number of document
texts quoted third-party URLs containing long-expired access tokens;
those token values are redacted in the shipped corpus.

## Quality approach

Retrieval and prompt changes are validated against a situational
question suite (inheritance and burial trusts, vehicle purchase and
caregiver pay, retroactive coverage, disaster payments, backpay,
community spouse figures, lookback gifts, estate recovery, and
similar) plus probe questions for newly added material, graded by
hand against the source documents. The suite and its graded answer
sets are working files and are not part of this repo.

## Limits

The corpus is a snapshot and ages; program figures change yearly.
Anything affecting benefits or eligibility should be confirmed with
a caseworker or the agency. A static, browser-only version of the
search lives in the companion repo
[fssa-medicaid-search](https://github.com/jamescordrey-ops/fssa-medicaid-search).
