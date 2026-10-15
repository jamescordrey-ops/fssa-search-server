# Changelog

One section per released version, newest first. Full build notes for
each version are in `docs/`. Corpus updates are manual: new documents
are collected, verified against their sources, and merged alongside a
version bump rather than synced automatically.

## v22 — 2026-10-14

Retrieval rebuilt in-process around corpus enrichment (the
consult design): a versioned corpus generation with a section
layer, weighted FTS5 search, registry pins resolved by document
id, declarative pins, vocabulary-driven typo expansion, and a
synthetic-question channel — behind a RETRIEVAL=v2 flag with the
v21 path intact as the fail-soft. No external engine; nothing
was downloaded. Deployed the same day; see docs/STATUS-v22.md
for the full build + deploy record, including the canary
finding below.

- Corpus: immutable generation g0000 (5,241 docs / 73,454
  replica chunks / 6,095 sections) in a SQLite store (docs,
  chunks, sections, enrichment, FTS5 indexes with column
  weights), content-hash chunk ids, jurisdiction from content,
  registry verbatim-quote lint at build. At deploy the vector
  layer was finalized where the embeddings live: the recorded
  -440 delta reconciled exactly as measured (4,714 docs matched,
  393 vector-canonical, 134 embedded fresh, 1 orphan URL
  dropped) — 74,135 chunks, each with exactly one vector.
- Enrichment: every section carries substring-validated
  figures, aliases/acronyms both directions, scope tags, and
  template questions; 1,400 sections (23%) also carry
  fleet-written natural questions (27,987 total), QA'd with
  zero deterministic failures. Enrichment is retrieval-keys
  only, never served or cited.
- Retrieval v2: channels T (weighted BM25F) / V1 (chunk
  vectors) / V2 (synthetic-question vectors) / R (registry by
  doc id) / X (exact + rescue), weighted RRF fusion from a
  versioned profile, section aggregation, declarative
  pins.json. All title-word demotions deleted. Registry fix
  shipped in this version: the ABLE contribution limit is its
  own dated entry pair with contribution-vocabulary triggers
  (it previously sat inside the onset-age entry, whose
  triggers required onset vocabulary).
- Known state at ship: the boot embedding-drift canary
  measures the stored vectors as not bit-reproducible by the
  current embedder build (cosine 0.933 vs the 0.999 gate), so
  V1/V2 disable themselves by design and v2 serves on its
  text channels until the corpus is re-embedded under the
  current digest. /health's legacy consistency field is
  unchanged (warn-only, -440) by design; generation exactness
  is asserted by the finalize step.

## v21 — 2026-10-14

Retrieval accuracy, an effective-dated figures registry, a staged
growth loop, and judge-timeout discipline. Ledger caps raised
($10/day, $1/ask) as an operator config change; the ledger, choke
point, and trip behavior are unchanged.

- Retrieval: an exact-term rescue pass fires on suspicion of a weak
  pool (and again after an IDK or partial draft, always re-drafting
  on injection) and promotes matching chunks into the rerank window;
  match text includes document titles and section headings, which is
  load-bearing where a key term lives in a title only. Registry
  entries pin their source chunk into the pool by topic. Lay-term
  registrations (server/growth_terms.json, loaded at boot) expand
  both search and the rescue.
- Registry: every verified figure gained valid_from/valid_to
  windows, a family key, dated history, and prose successor notes
  for unconfirmed next-period values (notes are never served as
  figures). The transfer penalty divisor is a dated pair: $8,027
  for applications on or after 2026-07-01, $7,651 before.
- Growth loop: server gap rows are complete queue candidates
  (ask id + class guess). tools/growth_loop.py classifies gaps,
  fetches only from allowlisted official domains into a staging
  area, and writes review cards (staged output in tools/staging/);
  merges are human-approved. Retrieval misses become regression
  and term proposals, never fetches. A 2027 watchlist (CMS spousal
  standards, Part B premium, Indiana waiver cap) stages standing
  cards; nothing unpublished is fetched or asserted.
- Vocabulary audit: tools/vocab_audit.py generates phrasings per
  corpus unit (templates, substitutions, real questions; LLM
  cross-examination pluggable, default off) and stages gap cards
  from offline retrieval dry-runs (output in tools/vocab_staging/).
- Judges: NIM legs honor the leg deadline across retries (a 30s
  leg can no longer burn 60s); every seat outcome path (served,
  skipped, failed, shed) records its reason.
- tools/retrieval_regression.py + retrieval_cases.jsonl are in the
  tree unused: the retrieval-core direction was decided directly,
  so the harness is not a gate.

## v20.2 — 2026-10-14

Retrieval fixes, observability, and a comment scrub.

- Retrieval: a topic bridge now pins the IHCP Vision Services module
  for eyeglasses and vision questions (the module held the exact
  answer but never reached the drafter on the natural phrasing);
  "how often" and "how frequently" now fire the duration/frequency
  boost; the fused-ranking demotion for titles containing "provider"
  no longer applies to Provider Reference Modules, which are primary
  sources.
- Observability: ask records now persist per-seat failure reasons
  (seat_fail / seat_down events carrying the error, latency, and
  timeout flag for every failed judge leg) and the retrieval
  candidate pool (per-entry ranks across the keyword, vector,
  rewrite, and bridge lists, rerank-window membership, and final
  selection), so a degraded panel and a retrieval miss are
  diagnosable from the record alone.
- A boot-time consistency check compares the loaded embeddings
  against the corpora manifest and reports the verdict in /health.
  It warns only; it never refuses to boot.
- Code comments scrubbed of personal attributions; the dedupe
  tool's hardcoded working path became a parameter.
- The offline test suite's server path constants now point at the
  repository layout (`server/fssa_search_server.py`).

## v20 — 2026-10-14

The free-first layer route.

- Provider seats rewired into strict layers: free and fast first
  (Groq free tier, Gemini free tier), then a rationed Gemini 3.8 Flash
  rescue rung (10/day sub-cap that protects its final-rung reserve),
  then free hosted open models (NVIDIA NIM), then prepaid providers
  (OpenRouter, xAI), then local models last.
- All three judge primaries now sit on free tiers: Gemini Flash-Lite,
  DeepSeek V4.1-Flash (NIM), and Qwen3.8-27B (Groq). The previously
  proven paid judges remain as fallbacks behind them.
- Hard drafting leads with GPT-OSS-120B on the Groq free tier;
  Grok 4.7 (xAI, prepaid) is the quality fallback behind free capacity.
- Per-provider daily guards: the Groq request counter sheds load to
  fallbacks before the free-tier daily wall instead of failing mid-ask.
- Per-class time budgets tightened to the acceptance criterion of
  "under 5 minutes and accurate": 150s simple / 285s hard inside the
  600s ask cap, with per-leg timeouts that shed slow fallbacks at the
  budget edge. No verification gate was weakened to buy speed.
- Uniform prepaid accounting: every prepaid provider reserves and
  settles through the same ledger gate ($2.00/day, $0.50/ask).
- Model IDs verified against live provider catalogs before shipping
  as defaults; a pre-deploy catalog re-check is part of the release
  checklist.
- v20.1 fix pass: restored the Qwen seat's fallback tail (Nemotron
  Ultra, Qwen3-235B, Llama-4-Maverick) for depth, added the rationed
  Flash rung to the extraction seat, and documented the drift protocol
  for preview models.

## v19 — 2026-10-14

Registry corrections and the speed build.

- Corpus incorporation (live set): Indiana collection to 1,291
  documents, federal to 3,955 — new provider bulletins, the qualified
  income trust manual section, current transfer-penalty and spousal
  impoverishment standards, vision and dental service modules, the
  SSA 2027 COLA release and fact sheet, and the federal SNAP FY27
  memo. (The repository corpus is the scrubbed variant and is updated
  in reviewed passes.)
- Verified-figure registry corrected: ABLE annual contribution limit
  $20,000 for 2026; the home-equity limit stated as the state's own
  $752,000 figure; the spousal shelter standard noted at the $812
  state print value; the waiver income cap hedged until the 2027
  figure is posted; a new entry for the SSA 2027 figures.
- Speed build: completeness repair for claims a judge seat's batched
  reply omitted; latency-ordered panel planning; judge timeout cut
  to 30s; per-class unit budgets; a fast lane for simple questions
  (two seats, still two families); an offline route report over the
  ask records and gap log.
- The citation gate was deliberately not loosened; answers that
  cannot be fully cited still fail closed.

## v18 — 2026-10-14

The spend hard stop and panel integrity release.

- Reservation-based spend control: a fail-closed daily ledger
  ($2.00/day) with provable-maximum reservations per call and a
  $0.50 per-ask ceiling; usage reconciled against dated ledger rows.
- Degraded panels get visibly different badge names
  (`verified_reduced`, `partial_reduced`, and siblings); a full
  `verified` requires two distinct judge families per claim.
- Panel planned atomically per round: three distinct families, and a
  failed seat is not silently replaced mid-round.
- Observability: per-ask records (`ask_records.jsonl`) and an offline
  replay harness for re-grading recorded asks.
- Offline test suite (102 tests) covering the verifier, the pipeline,
  the spend hard stop, the job path, and replay.
- Fix: the job path no longer truncates re-serialized request bodies
  at the original Content-Length.

## Earlier versions

Development before v18 iterated quickly (retrieval, hybrid search,
panel composition, gate design). Those iterations predate this
changelog; their design outcomes are folded into the current code and
described in the README.
