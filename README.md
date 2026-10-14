# FSSA Search Server

A local search and question-answering system for Medicaid and benefits
policy. The rules that govern waiver and benefits life are scattered
across hundreds of manuals, PDFs, and agency pages that do not talk to
each other; this pulls them into one corpus and answers plain-language
questions with citations, running on a home machine. The models that
draft and judge the answers can run locally on Ollama or on hosted
API services; see "Two ways to run it".

> **About (for the repo sidebar):** Local search and Q&A over Indiana
> Medicaid/FSSA and federal benefits policy documents. Hybrid keyword +
> vector retrieval, cited answers checked by a multi-model judge
> panel, figures verified against the sources. Runs fully local on
> Ollama, or on hosted API seats (OpenRouter, Google Gemini, NVIDIA
> NIM, Tavily) with the local models as fallback.

## Two ways to run it

Every job in the pipeline (drafting answers, judging them, the
retrieval utilities, the web branch-out) is a **seat**: an ordered
list of candidate models with a latency budget and a failover order.
A seat is dormant until its provider is configured, via a key file in
the server directory or the matching environment variable; a dormant
seat simply never runs, and the pipeline degrades to the seats that
can run. That gives two complete configurations:

**Local mode** runs everything on Ollama. qwen3.5:9b is the default
answering model; qwen3:14b takes the questions the router classifies
as hard (contested figures, bundled multi-part) and serves as the
local judge. Nothing leaves the machine. This is the original design,
and it is also the last-resort fallback chain inside API mode: the
local models load only when the hosted seats genuinely cannot run, or
when a caller explicitly picks a local model, and every local load is
recorded in the answer metadata.

**API mode** puts the seats on hosted services. This is the
configuration the server currently runs in:

- **OpenRouter** is the primary provider. GPT-OSS-120B (routing
  pinned to its Cerebras backend first, Groq next) drafts hard
  questions; GPT-OSS-20B drafts simple questions and runs the
  retrieval utilities (query rewrite and reranking). Routing sets
  `data_collection: "deny"`, so only backends that do not collect
  prompts may serve. Every OpenRouter response reports its cost: the
  server accumulates it into each answer's `cost_usd` and a
  persistent daily ledger (`openrouter_spend.json`), with a hard
  guardrail: at `OPENROUTER_DAILY_CAP_USD` (default $1.00/day) the
  OpenRouter seats are treated as unavailable and the fallback chain
  takes over. The key's own dashboard-side limit is read at startup
  and reported in `/health` next to the code-side cap.
- **Google Gemini** provides the primary judge: gemini-3.5-flash-lite
  extracts each draft's claim list and delivers verdicts on it.
  gemini-3.8-flash is a capped reserve (18 calls/day in code, under
  the free tier's 20/day) used for arbitration.
- **NVIDIA NIM** carries the fallback depth: measured drafting seats
  (Kimi K3, Nemotron Ultra, GLM-5.3, and DeepSeek V4.1 Flash for hard
  questions; GPT-OSS-20B, GLM-5.3 Flash, and Nemotron Lightning for
  simple ones) that take over when OpenRouter fails; failover for the
  two hosted judge seats (Nemotron Super, Nemotron Ultra); and the
  divergence oracle (below).
- **Judge seats**: two further judges verdict the same claim list as
  Gemini. Their primaries ride OpenRouter (DeepSeek V3.1 chat on one
  seat, Qwen3-32B on the other, so the standing panel is three model
  families from three providers), each with a NIM failover behind it.
- **Tavily** powers branch-out: when a question's honest disposition
  from the corpus is IDK, the server searches the live official web
  (in.gov, medicaid.gov, cms.gov, ssa.gov, irs.gov only), fetches the
  top pages as temporary excerpts, and re-runs the pipeline against
  them. A passing answer ships under the `verified_web` badge with
  the fetch provenance in the record. A monthly credit counter (800)
  disables the branch before the pool runs out mid-ask.

In local mode the judge panel degrades to the judges that can run on
the machine, and an answer only one judge could check is badged
`verified_single` / `partial_single`, so the weaker check is visible
rather than hidden. Either mode is fully functional on its own: local
mode needs no accounts or keys at all, and API mode still answers (on
the local models) if every hosted provider is down.

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
- **Verification panel**: a draft is not shipped on its author's word.
  Independent judges extract its claims and rule, claim by claim,
  whether the cited material actually says that; refutation retrieval
  hunts for excerpts that contradict the load-bearing claims; and
  deterministic gates check figures and arithmetic. A draft that
  fails is repaired or redrafted up a bounded ladder; what cannot be
  verified ships as a Partial of only the verified claims, or an
  honest IDK. See Architecture.
- **Speed**: embeddings ship as a binary `embeddings.npz` and vector
  search is a single matrix multiply; the server is healthy about
  10 seconds after start. `/ask` responses carry per-stage timing
  ({retrieve_ms, generate_ms, judge_ms, total_ms}).

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

An `/ask` flows through the pipeline like this:

1. **Retrieval**: hybrid search (above) with query rewrite and
   reranking, per sub-question for bundled questions. A sufficiency
   gate decides whether the retrieved material can answer at all.
2. **Drafting**: the drafting seat for the question's difficulty
   drafts an answer that must cite excerpt numbers at birth
   ([1], [2], ...). Citation dialects the drafting models emit are
   normalized to the canonical form at birth.
3. **Judging**: the primary judge extracts the draft's claims; every
   judge on the panel then verdicts the same numbered list against
   the excerpts (supported / contradicted / not addressed). Code, not
   a model, resolves the verdicts into pass/fail per claim. For
   contested questions an independent second draft, the divergence
   oracle, is generated concurrently by a flagship model from a
   different family than the first drafter, and the disagreement is
   surfaced in the record rather than averaged away. Disputes that
   cannot be resolved fail closed: the claim does not ship as
   verified.
4. **Gates**: citations at birth (every load-bearing sentence must
   carry a citation; a persistently uncited sentence that carries no
   extracted claim, an aside, is cut from the draft and recorded in
   the response's `stripped_sentences` instead of sinking the whole
   answer, while an uncited claim-bearing sentence still fails the
   draft), the figure checker, and the arithmetic checker.
5. **Disposition**: the answer ships badged `verified` (or
   `verified_web` when its support came from branch-out web pages),
   `partial` when only some claims verified, or `idk`. Failed
   dispositions are written to a gap log (`GET /gaps`) that drives
   later corpus rescrapes.
6. **Cap**: a per-ask wall-clock cap (600s) stops the ladder
   mid-climb; only already-verified claims ship, else IDK.

## Prerequisites

Shared, both modes:

- Python 3.11+ (developed on 3.12). The server itself uses the
  standard library plus numpy (vector search). `pip install -r
  requirements.txt` (PyMuPDF is only needed by `parse_pdfs.py`).
- [Ollama](https://ollama.com) running locally with
  `nomic-embed-text` pulled: embeddings are built and served locally
  in both modes; vector search never leaves the machine.

Local mode adds:

- `ollama pull qwen3.5:9b` (default answering model)
- `ollama pull qwen3:14b` (routed hard questions; the local judge
  when the Gemini judge is unavailable)

API mode adds (each provider is optional and independent; a missing
key benches only that provider's seats):

- An OpenRouter API key (openrouter.ai) for the primary seats.
- A Google AI Studio API key (aistudio.google.com) for the Gemini
  judge.
- An NVIDIA NIM API key (build.nvidia.com) for the NIM seats.
- A Tavily API key (tavily.com) for branch-out.

Keys are read from files in the server directory (Path B below) or
from the matching environment variables (`OPENROUTER_API_KEY`,
`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `TAVILY_API_KEY`). Key files are
gitignored (`*_key*.txt`); never commit them.

## Setup

### Shared base (both paths)

```bash
git clone https://github.com/jamescordrey-ops/fssa-search-server.git
cd fssa-search-server
pip install -r requirements.txt

# Build embeddings from the shipped corpora (takes a while on
# first run; safe to interrupt and re-run, progress is kept).
# Requires Ollama running with nomic-embed-text pulled.
python gen_embeddings_stream.py        # -> embeddings.jsonl, then embeddings.json
python dedupe_chunks.py                # -> dedupe_skip.json (chunks to omit)
python build_embeddings_v11.py         # -> embeddings.json with dedupe applied
python make_npz.py                     # -> embeddings.npz (fast server startup)
```

### Path A: local mode

```bash
ollama pull qwen3.5:9b
ollama pull qwen3:14b

# Make sure no key files are present in this directory
# (openrouter_key.txt, gemini_key.txt, nvidia_key*.txt,
# tavily_key.txt) and none of the *_API_KEY environment variables
# are set: with nothing configured, every hosted seat stays
# dormant and the local models do all the work.

python fssa_search_server.py 8471
```

### Path B: API mode

```bash
# Create one key file per provider you want to enable, in this
# directory. Each file holds just the API key, nothing else.
# All of them are gitignored; never commit them.
#   openrouter_key.txt - OpenRouter: drafting, utilities, and the
#                        two paid judge seats
#   gemini_key.txt     - Google AI Studio: the primary judge
#   nvidia_key.txt     - NVIDIA NIM: drafting failover seats, judge
#                        failover, the divergence oracle
#   tavily_key.txt     - Tavily: branch-out web search
# Optional: nvidia_key2.txt and nvidia_key3.txt spread the NIM
# seats across extra key slots; a seat whose slot has no key
# simply stays dormant. (Environment variables work instead of
# files; see Prerequisites.)

# Optional: set the daily OpenRouter spend cap (default $1.00).
# At the cap the OpenRouter seats bench and the NIM/local
# fallback chain takes over.
export OPENROUTER_DAILY_CAP_USD=1.00

python fssa_search_server.py 8471
```

Then open http://localhost:8471/ for the Q&A webapp. Check
`curl http://localhost:8471/health`: it reports the seat
configuration, today's OpenRouter spend against the cap, and whether
branch-out is enabled, so you can confirm the mode you intended is
the mode that is running.

To add new documents later: append them to a corpus file (same
{title, url, source, text} shape), stamp tags with
`python tag_docs.py corpus_in.json corpus_out.json`, and embed just
the additions with `python gen_embeddings_incremental.py`, then
re-run `make_npz.py`.

## Endpoints

- `GET /` - the Q&A webapp. There is no model picker: the default
  ask runs the configured seats and judge panel, and a "run locally"
  toggle is the only model choice (it pins the ask to the local
  models).
- `GET /health` - status, document counts, embeddings loaded,
  per-seat health, the OpenRouter spend-vs-cap state, and the
  branch-out state.
- `GET /search?q=...&corpus=in|federal|both` - hybrid search results
- `POST /ask` - JSON `{question, corpus}` (`corpus` defaults to
  `in`). Returns the answer with its badge (`verified`,
  `verified_web`, `partial`, or `idk`; `verified_single` /
  `partial_single` when only one judge could run), the cited source
  excerpts, the judge panel record (which judges ran, with their
  models and providers), a per-claim verification breakdown,
  per-stage timing, and `cost_usd` (the ask's OpenRouter spend; 0 in
  local mode). Pass `"fresh": true` (or `nocache`) to bypass the
  answer cache, which is what you want when testing changes. The
  webapp uses the job form of the same endpoint: `POST /ask?async=1`
  returns a job id, and `GET /ask/result/<id>` reports live stage
  text, then the same payload.
- `GET /gaps` - the gap log: questions the server could not verify,
  with the gate that failed. Drives the corpus growth loop.

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
- `golden_set.jsonl` - the golden question set: the seat assignments
  were pinned by a build-time bake-off over these questions, and
  retrieval/prompt changes are validated against them
- `growth_loop.py` - the corpus growth loop: clusters the server's
  gap log and drafts candidate registry entries and rescrape topics
  into a review queue; nothing is added without human approval

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

The grading machinery is built into the server: every answer is
judged claim by claim at runtime by the panel described in
Architecture, so a bad answer is caught by the same system that would
have shipped it. Changes are validated against the golden set in this
repo (`golden_set.jsonl`), which pinned the seat assignments in the
v16 bake-off, and against a situational question suite (inheritance
and burial trusts, vehicle purchase and caregiver pay, retroactive
coverage, disaster payments, backpay, community spouse figures,
lookback gifts, estate recovery, and similar) plus probe questions
for newly added material, graded by hand against the source
documents. The situational suite and its graded answer sets are
working files and are not part of this repo.

## Limits

The corpus is a snapshot and ages; program figures change yearly.
Anything affecting benefits or eligibility should be confirmed with
a caseworker or the agency. A static, browser-only version of the
search lives in the companion repo
[fssa-medicaid-search](https://github.com/jamescordrey-ops/fssa-medicaid-search).
