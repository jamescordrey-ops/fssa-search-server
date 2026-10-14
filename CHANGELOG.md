# Changelog

One section per released version, newest first. Full build notes for
each version are in `docs/`. Corpus updates are manual: new documents
are collected, verified against their sources, and merged alongside a
version bump rather than synced automatically.

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
