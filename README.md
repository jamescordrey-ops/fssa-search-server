![An AI question-and-answer server: a question flows through retrieval, drafting, and an independent judge panel before it is answered](assets/hero.webp)

# Verified-Answer Q&A Server

A self-hosted question-and-answer server over a curated corpus of public
Medicaid / FSSA policy documents. It answers benefits and eligibility
questions from source text it can cite, checks every answer with an
independent panel of models before the answer ships, and says
"I don't know" when the verification does not hold up.

The design goal is simple: an answer you can trust more than a chatbot's
first draft, running on an ordinary PC, at a cost bounded by hard caps
you set.

## How it works

1. **Corpus.** Public policy documents (program manuals, provider
   bulletins, federal guidance, agency memos) are collected, parsed to
   plain text, and stored as JSON records. Each record keeps its title,
   source URL, and full text, so every claim can be traced back to the
   document it came from.
2. **Hybrid retrieval.** Documents are chunked and embedded with a local
   embedding model. A question is matched against the corpus with a
   hybrid of keyword and vector search, and the best-matching excerpts
   are pulled into context. Nothing is answered from a model's memory
   when the corpus can answer it.
3. **Drafting.** A drafting model writes an answer using only the
   retrieved excerpts, with citation markers tying each claim to its
   source excerpt.
4. **Independent judge panel.** The draft is decomposed into individual
   claims. Judges from *different model families* than the drafter
   check each claim against the excerpts: supported, contradicted, or
   unsupported. A refutation pass actively tries to find what the draft
   got wrong.
5. **Gates.** Two mechanical gates sit between the panel and the user:
   a citation gate (every claim-bearing sentence must carry a citation
   to corpus text) and a figure checker (every dollar amount and number
   must trace to source text or to arithmetic over sourced numbers).
   Claims that fail are repaired once, then dropped; if too much of the
   answer fails, the whole answer fails.
6. **Visible badges.** Every answer ships with a verification badge
   that says how it was checked: `verified` (full panel agreed),
   `verified_single` / `verified_reduced` (a smaller or degraded panel),
   `partial` variants (some claims dropped), `verified_web` (answered
   from live web sources when the corpus had nothing), or `idk` —
   the fail-closed outcome. The server never silently presents a
   weakly-checked answer as a strongly-checked one.
7. **Provider layering with hard spend caps.** Models are reached
   through *seats* (drafter, judge seats, utilities), and each seat has
   an ordered list of candidate providers. The shipped configuration
   prefers free tiers first (fast free inference, free daily quotas),
   falls back through additional free capacity, and only then touches
   prepaid providers. All paid usage runs through a reservation ledger
   with a hard daily cap and a per-ask ceiling: when the cap trips,
   paid seats stop being called, period. Local models (via Ollama) are
   the final fallback.

## Repository layout

```
server/    the Q&A server (single file, standard library + numpy)
data/      the document corpus (JSON; see Corpus format below)
tools/     corpus pipeline: parse, embed, dedupe, plus analysis tools
tests/     the offline test suite and the golden question set
docs/      per-version build/status notes
assets/    icons and the README hero image
```

The runtime layout is flat: when the server runs, it expects
`fssa_search_server.py`, the corpus files, the embeddings file, and any
key files together in one directory. The repository groups files by
role; deployment copies `server/fssa_search_server.py` into the runtime
directory alongside `data/`.

## Recreate it yourself

The server is provider-agnostic. Every model call goes to an
OpenAI-compatible chat endpoint (or a local Ollama), so you can staff
the seats with whatever models you have access to: free tiers, prepaid
API credits, or local models only. What matters is the shape: at least
two model families for the judge panel, and a drafting family different
from the families that judge it.

### Prerequisites

- Python 3.12 or newer, with `numpy` (see `requirements.txt`;
  `pymupdf` is only needed for the PDF ingest tool).
- [Ollama](https://ollama.com) running locally with the
  `nomic-embed-text` embedding model pulled
  (`ollama pull nomic-embed-text`). A local chat model (for example a
  9B-class instruct model) is optional but recommended as the
  last-resort fallback seat.
- API keys for whichever hosted providers you want in your seats.
  No account is required to run retrieval-only mode.

### 1. Build a corpus

Collect public documents: save web pages as text, and extract PDFs
with `tools/parse_pdfs.py`. Assemble two JSON files:

- `corpus.json` — your primary (for example, state) documents
- `corpus_federal.json` — a second collection (for example, federal)

Each file is a JSON list of records:

```json
{"title": "Document title",
 "url": "https://example.gov/source-page",
 "source": "Publishing agency",
 "verified": "True",
 "text": "Full plain text of the document ..."}
```

Then chunk and embed, from the runtime directory:

```sh
python tools/gen_embeddings_stream.py   # chunks -> embeddings via Ollama
python tools/dedupe_chunks.py           # drop near-duplicate chunks
python tools/apply_dedupe.py            # rebuild honoring the skip list
python tools/make_npz.py                # pack embeddings.npz for fast loads
```

Re-running the embedding step after adding documents is safe; it skips
URLs that are already embedded. Corpus updates in this project are
manual and deliberate: new documents are collected, verified against
their sources, and merged in one reviewed pass, usually alongside a
version bump.

### 2. Configure providers and models

Seats are configured in constants near the top of
`server/fssa_search_server.py` (the `SEATS` table and the provider
slot definitions). For each seat, list candidate `(model, provider
slot)` pairs in the order you want them tried. Provider slots map to
base URLs for OpenAI-compatible endpoints and to key files.

Keys live in plain-text files in the runtime directory, one key per
file, and are never committed (`.gitignore` covers `*_key.txt`):

```
gemini_key.txt       Google AI Studio key (free tier works)
openrouter_key.txt   OpenRouter key (prepaid)
nvidia_key.txt       NVIDIA NIM key (free developer tier)
groq_key.txt         Groq key (free tier works)
xai_key.txt          xAI key (prepaid)
tavily_key.txt       Tavily key (optional; live-web branch-out search)
```

Leave out a key file and that provider simply stays dark; every chain
falls through to its next candidate.

Spend safety is configured in the same place: the daily ledger cap and
the per-ask ceiling (shipped defaults: $2.00/day, $0.50/ask) and the
per-provider daily request guards for free tiers.

### 3. Run the server

From the runtime directory (server file, corpora, `embeddings.npz`,
and key files together):

```sh
python fssa_search_server.py 8471
```

Endpoints:

- `GET /` — a small web UI for asking questions
- `GET /search?q=...` — retrieval only: ranked excerpts, no model calls
- `POST /ask` — the full verified-answer pipeline
  (`{"question": "..."}`); long answers can take minutes on free tiers
- `GET /health` — corpus counts, embeddings loaded, provider
  configuration, guard states, and today's spend against the caps

### 4. Check your build

```sh
python -m pytest tests/        # offline suite: gates, panel, ledger
```

`tests/golden_set.jsonl` is a set of questions with known-good answers
drawn from the corpus; use it (and the replay harness in `tools/`) to
grade a new model in a seat before trusting it.

## Verification badges, in one place

| Badge | Meaning |
|---|---|
| `verified` | Full panel, all claims supported and cited |
| `verified_single` | Verified by a single-family panel on a simple question |
| `verified_reduced` | Verified, but the panel was degraded (a seat fell back) |
| `partial` / `partial_single` / `partial_reduced` | Some claims were dropped; what remains is supported and cited |
| `verified_web` / `verified_web_reduced` | Answered from live web sources because the corpus had no coverage |
| `idk` | Verification failed or coverage was absent; the server declines to answer |

## A note on scope

The corpus is public policy material. The verification machinery is
general, but it was built for questions where a wrong number has real
consequences: income limits, asset rules, coverage frequencies. If you
adapt it, keep the fail-closed behavior. An honest "I don't know" is
the feature, not a bug.

See `CHANGELOG.md` for the version history and `docs/` for the
per-version build notes this design was iterated through.
