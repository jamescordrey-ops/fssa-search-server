# FSSA Search Server v22 — Build Status

Built 2026-10-14 (evening, EDT) on the VM from the deployed v21 tree.
Build stage only: no PC access, no live /ask, no paid calls.

## ⚠ TOP ITEM: vectors are PENDING, by environment, not by omission

**There is no embeddings file and no embedder on this VM.** The
embeddings (embeddings.npz / embeddings.json) and Ollama live on
the PC, and this stage's boundary was no-PC-access. Consequences,
all designed-for and fail-soft:

- Generation g0000 is fully built from the two corpus JSONs
  (texts, chunks, sections, FTS5 indexes, enrichment, registry
  resolutions, vocabulary). Its vector state is `pending`.
- The server's V1 (chunk vector) and V2 (synthetic-question
  vector) channels are OFF until `tools/finalize_vectors.py` runs
  where the embeddings file and Ollama live (the deploy stage).
  Channels T / R / X carry retrieval meanwhile; /health reports
  `vectors: "pending"` until then.
- The −440 reconciliation is computed and recorded
  (generations/g0000/vector_reconciliation.json) from the v21
  deploy's measured state (73,014 live rows vs 73,454 manifest;
  134 missing URLs, 393 mismatched, 1 orphan). The finalize tool
  performs the actual reconciliation on the PC and replaces the
  recorded classes with MEASURED ones, asserting exactness (every
  chunk exactly one vector) before flipping state to `ready`.
  Deploy order: finalize BEFORE the server first serves v2, or
  re-run `tools/enrich_sections.py --refresh-fts` after finalize
  (vector-canonical chunk replacement rebuilds affected sections
  and resets their FTS enrichment fields; the refresh restores
  them from the enrich table).

## What v22 is

The consult design (CONSULT_RAW.md), adjudicated in PLAN.md: the
failures were vocabulary failures, so v22 rebuilds retrieval
in-process around corpus enrichment instead of swapping in an
external engine. Typesense remains the named fallback; nothing
Typesense was downloaded.

Server file: `fssa_search_server.py`, **11,922 lines, MD5
8299084c15033250d097a7dd84855164**. The v21 path is intact and is
the default: retrieval v2 serves only when the environment sets
RETRIEVAL=v2 AND the generation store opens AND FTS5 is present;
otherwise the effective engine is legacy and /health says why.

## 1. Corpus generation g0000 (tools/build_generation.py)

- Immutable dir `generations/g0000/` + `generations/CURRENT`
  pointer ("g0000"); the server reads through the pointer.
- 5,241 docs (first-URL-wins dedupe, ≥200 chars, Indiana file
  then federal), 73,454 chunks, 6,095 sections. The chunker
  replica (600/100 windows, section-aware, heading-prefixed)
  reproduces the server's consistency-manifest total EXACTLY
  (73,454), so once vectors finalize, the boot consistency check
  can read ok:true against the same arithmetic.
- Chunk IDs are content hashes:
  sha1(url | section_key | ordinal | sha1(text)).
- Section layer: sections keyed by (doc, numbered heading /
  title / module name); retrieval finds sections, serving cites
  chunk windows (anchor chunk ± 1 same-section neighbour).
- Jurisdiction is derived from content/host, never from which
  file a doc arrived in (the INvestABLE booklet sits in the
  federal file and is Indiana's). Authority tiers assigned by
  document-pattern (manuals/statutes 3, program pages 2, news 1).
- SQLite store: docs, doc_texts, chunks, sections, enrich,
  registry_sources, vocab, chunk_fts (title/headings/body),
  section_fts (title/headings/body/synth_q/aliases).
- Registry verbatim-quote lint at build: 9 of 12 entries fully
  verified (every figure found in the resolved source doc, plus
  an extracted verbatim quote sentence); 2 entries carry no
  figures by design; 1 partial: mmmna's CMS exact values
  ($4,066.50 / $811.50) do not appear in the corpus — Indiana's
  own document prints $4,067 / $812, and both were found. The
  lint's resolutions double as the R channel's doc IDs (the
  divisor pair resolves to doc 1282, §3006.00.00).
- Typo vocabulary materialized at build time from fts5vocab:
  94,230 terms with chunk counts (see §3 for why build-time).

## 2. Retrieval v2 (server, behind RETRIEVAL=v2|legacy)

- **FTS5 verified present** in this VM's sqlite3 (Python 3.12.3,
  sqlite 3.45.1): fts5 and fts5vocab both create. The server also
  probes FTS5 at open and /health reports it; absent → legacy.
- Channels: **T** = FTS5 weighted BM25F (section index [5,3,1,2,3]
  over title/headings/body/synth_q/aliases + chunk index [5,3,1]
  mapped up to sections; query rewrites, synonyms, growth terms,
  and typo alternates all feed it). **V1** = chunk vectors
  (numpy matmul over the generation matrix). **V2** = synthetic-
  question vectors. **R** = registry: the server's date-aware
  registry matching, resolved to source sections BY DOC ID from
  registry_sources. **X** = exact: section numbers and PROMOD
  ids found in the question's own text, quoted-phrase matches,
  and (round 2) the v21 rescue's best documents.
- Fusion: weighted RRF (k=60; weights T 1.0, V1 1.0, V2 1.1,
  R 1.2, X 1.2) in versioned `retrieval_profile.json` (v22.1),
  section aggregation with a channel-agreement bonus (+0.004 per
  extra channel), and small multiplicative priors (authority tier,
  jurisdiction-from-content match, frequency/figure intent when
  the section's extracted figures match the intent).
- **All title-word demotions deleted**: the −15 keyword provider
  penalty in `search()` and the −0.03 provider-title demotion in
  hybrid_search's lift loop (with its now-moot module exemption).
  Grep-asserted gone by the offline checks. The v21 offline suite
  passes unchanged against the v22 file.
- Pins: `pins.json` — all 10 v21 topic bridges converted
  mechanically (tools/bridges_to_pins.py) to declarative
  {trigger, target, reason, case_id} records (case_id null: the
  bridges predate case IDs). Applied in the legacy served order
  (pins.json bridges, then registry sections).
- Typo handling: a query term that is out-of-vocabulary or rare
  (df ≤ 3 in the build-time fts5vocab table) earns OR-expansion
  candidates within edit distance 2 that are ≥20× more frequent
  AND co-occur with another query term in some chunk. Expansion
  only, never replacement. Measured: "devisor" → expands to
  "divisor" (verified in the offline checks).
- Round 2 (the answerability loop): when the fused band is flat
  and low (the divisor pool's signature), the v21 rescue runs
  once, its best documents' best sections join channel X, and
  the pool re-fuses. Bounded to one round; recorded in the pool
  note (`round2`), which also carries per-hit channel ranks
  (T/V1/V2/R/X) and the engine + generation id.
- Boot embedding-drift canary: once vectors are ready, a daemon
  thread re-embeds 5 stored chunks and compares cosine against
  the stored rows; any measured cosine < 0.999 sets
  embedding_drift, disables the vector channels (text channels
  continue), and reports in /health. An unreachable embedder is
  "unrun", never a drift verdict.
- /health gains a `retrieval` block: {engine_requested,
  engine_effective, fts5, generation, vectors, embedding_canary,
  embedding_drift, fail_reason, profile}.

Design decisions taken inside the plan's latitude:

- The generations directory resolves from the server file's own
  directory (env FSSA_GENERATIONS_DIR overrides), NOT the process
  CWD: the server's CWD is its data dir, and the offline harness
  imports it from a temp dir. Deploy places `generations/`
  beside the server file.
- fts5vocab runs at BUILD time, not runtime: a runtime
  fts5vocab table cannot attach to the read-only store (SQLite
  resolves the source in the temp schema). The vocab table in
  the generation IS the fts5vocab output, materialized.
- Rescue-to-section mapping is by document (the legacy chunker
  and the generation chunker differ), documented in code.

## 3. Enrichment (tools/enrich_sections.py, tools/enrich_qa.py)

Three layers per section, all retrieval-keys-only (they land in
the enrich table and section_fts synth_q/aliases; never served,
never cited):

- **Deterministic (all 6,095 sections)**: figures extracted by
  regex and stored as verbatim substrings WITH offsets
  (validation is by construction; the QA pass re-verifies);
  aliases from a curated concept map (both directions: CSRA ↔
  spousal impoverishment, Miller trust ↔ QIT, backpay ↔ lump sum
  ↔ retroactive benefits, …) plus acronym pairs mined from the
  text; scope tags (program, payer incl. FFS/MHS where the text
  says so); G1-style template questions from the heading and the
  section's figure kinds.
- **Golden sample**: 50 hand-written exemplar rows (the highest-
  priority sections: SNAP income/expungement, spousal
  impoverishment, Miller/QIT §2615.75.15, divisor §3006,
  MMMNA §3010.20.10, home equity §3005.10.05, Vision module,
  burial, IMPACT) at tools/enrichment_batches/golden_sample.jsonl.
- **Fleet (Muse's agent fleet, the operator's decision; no paid
  APIs)**: two waves, 14 writers, priority order (figure-bearing
  rule documents first, navigation pages last). **1,400 of 6,095
  sections (23%) carry fleet-written natural questions**;
  27,987 questions total across all rows. The job is resumable:
  tools/enrichment_checkpoint.json records sections done/total;
  the remaining batch files (007–030) are already generated, so
  any later run continues where this stopped. Deterministic
  coverage means unenriched sections still carry figures,
  aliases, scope tags, and template questions.

Checking pass (tools/enrich_qa.py, report at
tools/enrichment_qa.json):

- Deterministic validation over EVERY row: **0 figure failures**
  (every stored figure re-verified as a substring at its stored
  offsets), **0 question failures**, **0 alias failures**.
- Judgment sampling: 80 fleet sections / 782 questions graded
  against their sections (≥5% of the fleet). **Fleet questions:
  0 off-topic.** One defect class found, all in the template
  layer: POMS titles ending in an effective date made the date
  the whole topic ("What are the rules for 03/25/2025?"), 65 of
  782 sampled questions, 232 sections corpus-wide. Fixed in the
  generator, the deterministic pass re-run (it is idempotent by
  construction: template-form questions are stripped and
  regenerated), post-fix corpus-wide count of the pattern: **0**.
- Caveat recorded in the grades: fleet questions embedding
  figures were graded for topicality against 700-char excerpts;
  the writers worked from 1,400 chars. Figure digits inside
  question text were not independently re-verified.

## 4. Flip list (tools/flip_list.py → tools/flip_list.json)

Reporting only, per plan. 22 unique cases (the 16 probe cases
are a subset of the 22 retrieval cases). Method: hybrid_search,
rewrite + rerank off, n=8, each case on its own file's
expectation semantics; **vector channels empty on the VM for
both arms** (the legacy arm's vector channel is equally empty
offline), so this compares the text/section/pin machinery only.

- Legacy 20/22, **v2 19/22**. Pass→pass 19, fail→fail 2,
  fail→pass 0, **pass→fail 1**.
- The one pass→fail, named: "What is the substantial gainful
  activity limit for 2027, and what is the trial work period
  threshold?" The case carries no corpus field, so BOTH engines
  search the Indiana corpus for an SSA question; its expect_any
  is ["Trial Work Period (TWP)", "2027"] in any served title.
  Legacy's top-8 happens to include a stray "2027"-titled
  Indiana doc; v2's does not. Neither engine's #1 is the SSA
  document. The flip is real under the case's own semantics and
  is substantially a case-scoping artifact; reported as-is.
- The 2 fail→fail: the spouse-inheritance scenario and the
  Miller father scenario (long scenario questions; both engines
  miss their expected titles offline without vectors/rerank).
- First flip run had two tool bugs (wrong probe-cases path;
  expect_any unsupported) that made both engines score 14/22
  with zero flips; both were found, fixed, and the run repeated.
  The numbers above are the corrected run.

Offline anchor smoke (v2, models off): divisor question →
IHCPPM 3006.00.00 at #0; eyeglasses → Vision Services
(PROMOD00051) at #0; QIT → IHCPPM 2615.75.15 at #0. All three
historical retrieval failures land first on the v2 text path.

## 5. Vocab audit retarget

tools/vocab_audit.py now defaults to this tree (it hardcoded
the v21 tree's path expression, which resolves per-tree) and
the audited engine follows RETRIEVAL. Two v2-mode sample runs
completed (vectors off in both): a 54-unit run — 224 phrasings,
49 pass / 5 vocabulary_gap, per-generator failures G1 4/136,
G2-typo 2/54, G4 5/34, 5 cards staged (preserved at
tools/vocab_staging_v22_smoke/) — and a smaller 17-unit run:
11 pass, 6 retrieval_miss (Boarder Cost of Business Q1; SSI
computation Q1/Q2; HUD housing Q3; excluded income Q1; TANF
Q1; DFR Ch 4600 chapter intro Q1). Real signal for the growth
queue. The full audit incl. G3 remains a post-deploy step,
as in v21.

## 6. Verification summary

- `python3 -m py_compile`: server + all v22 tools OK.
- `tools/offline_checks_v22.py`: **20/20 PASS** — FTS5 presence;
  profile/pins validity; generation counts vs manifest;
  reconciliation record; registry lint 9 ok / 2 no-figures /
  1 partial; both demotions grep-asserted gone; enrichment rows
  6,095/6,095 with a 200-row figure revalidation sample clean;
  channel units (T finds §3006 on the title-only term "divisor";
  X finds a section number; R resolves the divisor entry to doc
  1282; typo expansion measured: "devisor" → ["divisor"]; pins
  serve the Vision module top-3); v2 anchors top-3; legacy path
  intact with RETRIEVAL unset.
- `tools/offline_checks.py` (the v21 suite) against the v22
  file: **all passed**.
- Enrichment QA: 0 deterministic failures; judgment rates in §3.
- Flip list: §4.

## 7. Not done / honest leftovers

- **Vectors (V1/V2 channels, drift canary live-fire, and the
  consistency ok:true flip) wait for the PC-side finalize at
  deploy.** The canary code is written and its logic reviewed,
  but it has never run (no embedder here).
- **Fleet enrichment covers 1,400/6,095 sections (23%)** with
  natural questions; the deterministic layer covers 100%. The
  resumable job (checkpoint + pre-generated batches 007–030)
  finishes it whenever a later run picks it up. Synthetic-
  question VECTORS (V2) additionally wait for finalize, which
  embeds the enriched questions with nomic.
- The v2 round-2 flat-band thresholds (top < 0.045, spread
  < 0.003) are calibrated to the text-only channel mix seen on
  this VM; with V1/V2 live at deploy the fused scale changes
  and the thresholds should be re-measured (they are constants
  in `_hybrid_search_v2`, noted there).
- First v2 ask pays the one-time vocabulary/caches warm-up
  (a background warm thread starts at open; measured full
  offline check suite ≈ 20–90s per process depending on load).
- pins.json case_id fields are null (the v21 bridges predate
  case IDs); reasons are carried from the bridge comments.
- Deploy itself (file placement incl. generations/ beside the
  server file, finalize run, RETRIEVAL=v2 flip, boot + /health
  verification) is the next stage, per the run's authorization.

## 8. FIX (2026-10-14, post-build): ABLE contribution registry split

**The bug (proven live on v21, inherited unchanged by v22):**
the operator's live v21 test "What was the maximum able
account contribution for 2025?" shipped verified_single while
answering "I don't know the 2025 figure." The dated-registry
machinery was healthy, but the registry never ENGAGED: the
contribution figures lived inside `able_onset_age`, whose two
trigger groups are ANDed and whose group 2 requires onset
vocabulary ("onset age", "became disabled", ...). Any pure
contribution question hits group 1 and misses group 2, so
`_registry_matches` returned [] in every year. The v21 offline
check missed it because its ABLE question smuggled the words
"onset age" into the question.

**The fix:** the contribution limit is now its own DATED PAIR,
the WS2 pattern of the divisor pair: `able_contribution_limit`
($20,000, valid_from 2026-01-01, figures (20000.0,)) and
`able_contribution_limit_legacy` ($19,000, 2025-01-01 to
2025-12-31, figures (19000.0,)), family
"able_contribution_limit", sharing contribution-vocabulary
trigger groups (group 1: able / able account / 529a /
investable; group 2: contribution / limit / maximum / annual
tokens + "contribution limit", "annual contribution",
"how much can ... contribute" phrases). A pair rather than one
entry + history because `_registry_values` admits an entry's
base figures for every date; only the family/date machinery
keeps $20,000 out of a 2025 question's allowed set.

**What moved:** $20,000.00 came out of `able_onset_age.figures`
and the $19,000 / 2025 history row came out of its
`_V21_REGISTRY_META` entry (the legacy pair entry now carries
that figure inside its own window). The $100,000 SSI disregard
and the $500 Indiana tax credit STAY in `able_onset_age`, as
does its full statement prose. Topics (WS1b): the current
entry pins SSA POMS SI 01130.740 (the year table; doc_hint
title "01130.740" / url "0501130740"); the legacy entry pins
the IRS newsroom page ("The contribution limit for 2025 is
$19,000"; doc_hint title "able savings account" / url
"newsroom/people-paying-disability-related").

**Verification (offline, module-level, natural phrasings):**
- "What was the maximum able account contribution for 2025?"
  (verbatim) -> matches [able_contribution_limit_legacy] only;
  `_registry_values` on 2025-06-01 = [19000.0] ($20,000
  excluded).
- "What is the ABLE account contribution limit?" -> matches
  [able_contribution_limit] only; values include 20000.0,
  exclude 19000.0.
- Onset phrasing ("How old can someone be when they became
  disabled and still open an ABLE account?") -> matches
  [able_onset_age] only; values [100000.0, 500.0], no
  contribution figures.
- Legacy-path pins verified against the full 5,246-doc
  corpus: the 2025 question pins the IRS newsroom carrier;
  the no-date question pins POMS SI 01130.740.
- Suites: `tools/offline_checks.py` 36/36 PASS (its WS2 ABLE
  block was updated for the split and extended with the
  checks above; the block's own comment records why its
  premise changed); `tools/offline_checks_v22.py` 20/20 PASS
  ("ALL PASS").
- the operator's verbatim question added to
  `tools/retrieval_cases.jsonl` as a registry case (23 cases
  total) so flip reporting covers it.

**Generation note:** g0000's `registry_sources` table and
registry lint predate the pair, so the v2 R channel fails
soft (skips) for the two new entries until the next
generation build (build_generation.py lints every
VERIFIED_FIGURES entry from the live module, so a rebuild
picks them up automatically). The legacy hybrid pin path
(above) works today.

**Server file after the fix:** 12,017 lines, MD5
`1a0e9c6517471dabce6df23022cd9e1d`.

---

## v22.1 — registry trigger audit + two secondary fixes (2026-10-14, VM only)

Built in ~/workspace/fssa-qa/v22.1/ as a copy of this v22
tree; the v22 tree itself was NOT modified (its server file
still MD5 `1a0e9c6517471dabce6df23022cd9e1d`, and the v22
deploy in flight against it was not disturbed). Deploy of
v22.1 is a later, separate step. Full record:
REGISTRY_TRIGGER_AUDIT.md in the v22.1 tree.

**Why:** the operator's CSRA live test (2026-10-14 ~22:52 EDT)
failed the ABLE way — retrieval was perfect (ILTCP page
pinned br=0, served sel=0, figures in the excerpt) but
`_registry_matches` returned []: the csra entry's groups
are written in source vocabulary ("community spouse",
"spousal resource"), and "my wife" matched nothing. A
stemming wrinkle rode along: the exact-token matcher never
matches the word "resource" to the token "resources", so
even the manual's own question phrasing failed.

**Audit:** every one of the 14 VERIFIED_FIGURES entries got
one plain-language lay question (no source jargon, never
the entry's own trigger phrases), run before and after.
Before: **3/14 hit** (ssa_2027_figures + the v22 ABLE pair).
After: **14/14 hit, each ALONE** — the full cross-match
matrix is clean, and the spouse/money cluster spot-checks
on the standing cases stay clean (case 19's SNAP question
also matching asset_limit_2000 is PRE-EXISTING in the base
via the bare "resource limit" phrase; not widened here).

**What changed:** added plain phrasing / tokens to trigger
groups only (lay spouse words for csra + mmmna, EBT +
grocery help for SNAP, house-worth phrases for home
equity, Medicaid-specific bank phrases for the $2,000
limit, home-help phrases + earn/make tokens for the waiver
cap, give-property-to-kids phrases for the divisor pair,
owed-money phrases for backpay, disability-started phrases
for ABLE onset, paid-to-take-care phrases for difficulty
of care, and singular/plural token pairs where the
matcher is token-based, csra group 2 above all). No
figures, dates, topics, or statements changed; the AND
structure was not loosened. The 14 audit questions are
locked in as checks in tools/offline_checks.py.

**Cases:** the operator's verbatim CSRA question added to
tools/retrieval_cases.jsonl as a registry-class case
(24 total; the ABLE registry case confirmed present, not
duplicated).

**Secondary (a) — NIM draft deadlines:** the main draft
legs and judge seats are correctly deadline-threaded in
v22 (a ~60.12s bench timeout is the configured 60s bound
with the retry correctly suppressed). ONE bypass found:
the divergence oracle's B draft (engine "nimx") called
nim_ask with NO deadline, so _nim_text's retry ran at the
full timeout again — the WS4a bug class. FIXED: the leg
now clamps via _leg_timeout, sheds on a spent budget, and
passes timeout + deadline; proven offline with a
capturing stub. (_utility_text, the orlite extractor, and
ollama_ask's 300s transport remain on their own timeout
disciplines — recorded in the audit file, not changed.)

**Secondary (b) — local-fallback memo draft:** the local
fallback receives the standard drafter prompt (system +
instructions incl. any CORRECTIONS REQUIRED block; user =
question + excerpts; never the previous draft), and
through v22 no shape check stood between its output and
draft_text. FIXED with a narrow guard
(_is_memo_shaped_draft, applied in _run_unit right after
generation): a corrections-heading first line, or no
citation marker plus >= 2 meta markers, rejects the draft
as a generation failure with the reason recorded. Genuine
uncited answers are NOT rejected (the citation gate owns
that failure).

**Verification:** py_compile OK; tools/offline_checks.py
**56/56 PASS** (36 base + 20 new); tools/offline_checks_
v22.py **20/20 PASS** (legacy path intact; the g0000
registry_sources soft spot for the ABLE pair from §8 is
unchanged — the widened groups here are runtime matching
data, and the generation's R-channel resolutions still
refresh at the next generation build).

**Server file (v22.1):** 12,183 lines, MD5
`d36959344a3c2c450d41ad455953c7dc`.

---

# v22.2 — build record (2026-10-14, EDT)

Built on the VM from the v22.1 tree. No PC access, no live
asks; deploy is a later, separate step. the operator's direction:
"finish up all the fixes" — v22.2 carries the two known
remainders from the v22/v22.1 records.

## Workstream A — MECHANISMS registry twins (audit + widen)

Full record: MECHANISMS_TRIGGER_AUDIT.md.

Runtime use (read first, all live, no dead entries): a
`_mechanism_matches` hit arms (1) the retrieval sufficiency
retry (`_sufficiency_missing`/`_sufficiency_retry` with the
entry's `retry_query`), (2) the REQUIRED MECHANISMS block in
the drafter prompt (entry `prompt` + `canonical_claims`), and
(3) the answer gate (`_mech_required_failures`: required
elements present, no forbidden substitutes; the period
checker fails an asserted confusable period for
`backpay_nine_months`). A miss therefore loses all three —
the inverse failure surface of the figures registry.

Audit (one plain-language question per entry, asker words,
never the entry's trigger phrases; matched via
`_mechanism_matches`): **before 0/5 hit -> after 5/5 hit,
each entry ALONE** (qit_over_cap, backpay_nine_months,
snap_lump_sum, csra_half_clamp,
difficulty_of_care_exclusion). Worse than the figures
registry's 3/14: no mechanism engaged on any plainly-phrased
question. Fixes are added plain phrasing/tokens only,
mirroring the v22.1 figures twins where they exist (incl.
the csra singular-token stemming fix); no figures, dates,
required elements, prompts, or AND structure changed.
Guards asserted in the checks: Miller scenario still hits
qit_over_cap alone; inheritance still matches no mechanism;
CSRA formal phrasing (stemming probe) miss -> hit; SFC
question matches none. 8 new checks in
tools/offline_checks.py ("v22.2 mech audit" block).

## Workstream B — binary-text sections (corpus repair)

Full record: g0002_delta/README.md (+ manifest.json,
repaired_docs.json, remains_binary.json there).

52 docs in corpus_federal_v19.json held raw file/response
bytes as stored text (none in corpus_v19.json): 22
medicare.gov publication files (15 EPUB, 6 PDF, 1 MOBI,
stored as truncated raw prefixes) and 30 ssa.gov /
choosework / yourtickettowork pages stored as raw (mostly
gzip) response bodies — the docs behind g0001's ~53 flagged
binary sections. All 52 had a local original in the crawl
snapshots (36 by exact URL hash, 16 by same-path manifest
variant). **Repaired 48, remains-binary 4.** Repaired text:
110,116 chars of garbage -> 2,062,588 chars of readable
text; embedding chunks for these docs 251 -> 4,151
(server `_emb_chunk_count` replica; authoritative section/
chunk derivation happens at the deploy generation rebuild —
no existing corpus or generation was mutated; the delta
ships as replacement docs keyed by URL).

By method: EPUB spine extraction (zipfile+lxml) 15; PDF
pdftotext (poppler) 6 (incl. Gujarati + Amharic booklets
accepted on native-script character evidence, 3,621 /
2,396 chars); HTML via the corpus builder's own
parse_ssa_crawl.extract on gzip-decompressed snapshots 21;
HTML `<main>` extraction 5 (the SSA theme nests `<main>`
inside the `//header` subtree the builder's extractor
drops; the five disability/SSI pages' captures hold their
content there); single re-download 1 (choosework /about/ —
its Wayback capture is a 404 page; the live page extracted
cleanly). Two first-pass extractions were rejected by QA
as Wayback 404 captures, not content.

Remains-binary (4): the MOBI of publication 11306
(PalmDOC-compressed; no MOBI extractor on this VM; the
publication itself IS repaired via its EPUB twin doc — not
substituted into the MOBI doc); choosework
perc-common-ui/cors/ (a cross-domain iframe shim, not a
content page — 45 chars is all that exists there);
yourtickettowork about-ticket page (capture is a 404 page
and the single re-download returns 404 — the page is
gone); ssa.gov redbook/eng/nav.html (the capture file is
itself binary garbage and the single re-download returned
403). Observed, out of scope: 5 choosework pages are
mojibake-readable (UTF-8 misdecoded as latin-1), content
present; recorded in the delta README, not folded in.

## Rebase onto the deployed v22.1 file

The combined deploy landed its RETRIEVAL code-default
change in ~/workspace/fssa-qa/v22.1/ during this build
(MD5 d3695934... -> 1f137b19634d4b3c6ed3a2a79a9b509d,
12,183 -> 12,187 lines). Diff confirmed it is exactly the
one-line default (`"RETRIEVAL", "legacy"` -> `"v2"`) plus
its explanatory comment (2 hunks, lines ~1336-1341 and
1458). The five v22.2 mechanisms hunks were re-applied on
top of the deployed file; the final v22.2 file diffs from
the deployed v22.1 file by ONLY those five hunks (54 diff
lines) and is a strict superset of it.

## Verification

- `python3 -m py_compile fssa_search_server.py` — OK.
- `tools/offline_checks.py` — **64/64 PASS** (56 v22.1
  checks + 8 v22.2 mech-audit checks).
- `tools/offline_checks_v22.py` — **20/20 PASS** (incl.
  legacy-intact with the new v2 code default in place; the
  suite selects legacy explicitly).

**Server file (v22.2):** 12,229 lines, MD5
`878fcf448eb8a0d32305e01b809832e5`.

---

## DEPLOY — v22.2 + g0002 live (2026-10-15, ~00:40–01:15 EDT)

Executed end-to-end under the operator's authorization
("finish up all the fixes... .2 as well"), sequenced right
behind the v22.1 combined deploy. Verification boundary:
/health + server-local probes ONLY against the serving
process; the v2 ask path was verified out-of-process with
the deployed file (rewrite/rerank OFF, no paid model
calls); NO live /ask was made — the operator tests himself.

### Pre-check (v22.1 live, server-local /health)

ok=true; live MD5 1F137B19… (the v22.1 file); CURRENT
g0001; g0001 manifest vectors_state ready, digest
0a109f422b47… (the current nomic build); consistency
ok=false delta −440 (the calibrated legacy floor,
warn-only, unchanged semantics); caps $10/day + $1/ask
untripped; OpenRouter spend_today $0.000793, key remaining
$5.001726944. Backup: live file ->
`fssa_search_server_v22.1.py.bak` (MD5 1F137B19…,
verified). PC C: free 2.01 GB at prep.

### g0002 build (VM)

Base: the g0001 corpora (v22 tree's corpus_v19.json +
corpus_federal_v19.json, MD5-identical to this tree's
copies) with the 48 repaired docs from g0002_delta/
repaired_docs.json swapped into the federal corpus BY URL
(asserted: exactly 48 replacements; every untouched doc
byte-identical; 3,955 federal docs before and after).
Built with tools/build_generation.py against this tree's
server file (so the registry lint ran over all 14
VERIFIED_FIGURES entries: 11 ok, 2 no-figures by design,
mmmna partial — CMS $4,066.50/$811.50 absent from the
corpus, Indiana's $4,067/$812 found; IDENTICAL to the
g0001 build's lint). The MECHANISMS registry has no
generation-side lint (it is runtime matching data in the
server file); its engagement is probe-verified below.

Measured delta vs g0001 (set-compared chunk/section IDs):
docs 5,241 -> 5,241 (URL set identical); **chunks 73,454
-> 77,354 (4,151 added, 251 removed)**; **sections 6,095
-> 6,095 (the section ID set is identical; 48 sections
carry new text)**. Every added/removed chunk ID and every
text-changed section belongs to one of the 48 repaired
docs — verified by set comparison, zero leakage. The
repaired docs' own chunks went 251 -> 4,151, matching the
delta manifest's replica count exactly. (46 of the 48 docs
changed chunk count; 2 changed text at equal count.)

Enrichment: the completed g0001 enrich rows were
transplanted by section ID for the 6,047 sections whose
text is byte-identical between the generations (asserted
per section), then tools/enrich_sections.py
--deterministic ran over the whole store — the g0001
finalization recipe: fleet rows keep their questions with
code layers refreshed; the 48 repaired sections received
the deterministic layer only (template+code: figures,
aliases, scope tags, template questions computed from the
repaired text). Final: 6,095 enrich rows (6,047 fleet+code,
48 template+code), **55,472 questions**. Fleet-written
questions for the repaired sections are a LATER pass, not
this deploy (recorded as the standing follow-up).

Staged at generations/g0002_staging/ (manifest_g0002.json
there is the staging record; the deployed post-finalize
manifest was pulled back beside it as
manifest_g0002_deployed.json).

### PC finalize — the fast path, with one real bug found

Placed generations/g0002/ (DB renamed corpus_g0002.db on
placement, MD5 E7DA99FA… VM->PC verified; the v22.2 file
staged beside as .new, MD5 878FCF44…). The finalize ran
tools/finalize_g0002.py (a deploy variant of
finalize_vectors.py, committed with this version): the
embeddings input is the DEPLOYED g0001 store (DB + npy +
indexes), sound because g0001's vectors were embedded
~1 hour earlier under the same current digest; the 48
repaired docs were excluded from the input so they took
the EMBEDDED path fresh. A pre-flight pass classified
every doc from text hashes alone and would have aborted
on any vector-canonical or orphan outcome. Measured
(first completed run): source store 73,203 usable chunk
rows (73,454 − the repaired docs' 251), synthq reuse map
52,523 unique questions, 0 conflicts; **classes {matched
5,193, vector_canonical 0, embedded 48,
orphan_rows_dropped 0}**; vectors_g0002.npy **77,354 x
768** (exactness assert passed: 77,354 chunks = 77,354
vectors); **synthq 55,277 reused / 195 embedded fresh /
55,472 total**; meta vectors_state -> ready, generation
label -> g0002, embedding_digest recorded = 0a109f422b47….

**Bug found by the exactness assert, fixed in the
variant:** the FIRST finalize run failed exactness
(77,354 chunks, 77,352 assigned) and flipped nothing.
Cause: two unchanged docs (a Medicaid Transmittal PDF and
a medicaid.gov waiver download) each contain two chunks
with IDENTICAL text; the stock matched-path assignment,
keyed by text hash, assigns one vector per hash and
silently drops the twin row. (The latent stock bug never
fired before: g0000's finalize matched different docs, and
g0001's finalize embedded everything.) The variant now
assigns the vector to EVERY chunk row carrying the hash
(identical text => identical embedding). Re-run exit 0.
The g0002 DB was byte-untouched by the failed run (MD5
re-verified before the re-run).

### Canary proof — BEFORE the flip (the hard gate)

Out-of-process with the deployed v22.2 file, a probe
CURRENT pointing at g0002, RETRIEVAL absent (the code
default alone): open true; health block {requested v2,
effective v2, fts5 true, generation g0002, vectors ready,
profile v22.1}. The module's own canary thread reported
**ok**, embedding_drift false; manual replication on its
five picks (rows 1, 1001, 30001, 60001, 77354 — the last a
freshly embedded repaired-doc chunk): **cosine 1.000000
on every pick**. Store read directly: 77,354 chunks =
77,354 vector rows; 55,472 synthq rows = 55,472 index
entries. Had any of this failed, the flip would not have
happened.

### Flip + swap + restart — the standard path

CURRENT -> "g0002"; the v22.2 file moved into place (live
MD5 878FCF44…, verified); restart via the standard
`restart_fssa.ps1` (killed the v22.1 instance, PID 22704;
the 'FSSA Search Server' logon task launched the fresh
instance). No direct launch, no environment variable held
— v2 arms from the code default, proven against the LIVE
layout after the restart by re-running the probe harness
against the live file + the real generations dir: open
true, generation g0002, vectors ready, canary ok, drift
false, min cosine 1.000000. Live /health after boot:
ok=true, docs 1291/3955, caps $10/$1 untripped. As in
v22/v22.1, the live process's own retrieval block reads
requested=null until its first ask-path dispatch arms v2
(lazy-open semantics, unchanged).

### Probes (no /ask; v2 ask path, rewrite/rerank off, vectors LIVE)

Run pre-flip against the probe CURRENT and repeated
post-flip against the live file + layout; identical
results. Divisor ("What is Indiana's Medicaid transfer
penalty divisor?") -> IHCPPM 3006.00.00 **#0**; eyeglasses
-> Vision Services PROMOD00051 **#0**; QIT -> IHCPPM
2615.75.15 **#0**; ABLE-2025 (federal) -> the IRS newsroom
ABLE page **#0**, SSA POMS SI 01130.740 in the pool at #5.
CSRA, the operator's verbatim question ("If I go into a
nursing home how much of our savings can my wife keep?"):
`_registry_matches` -> **[csra]** AND
`_mechanism_matches` -> **[csra_half_clamp]** (the v22.2
mechanisms widening engaging on the deployed file); the
ILTCP Spousal Impoverishment Protection Law page is the
fused pool's **#0** (IHCPPM 3010.20.10 at #1). ABLE-2025
registry check -> [able_contribution_limit_legacy].
Repaired-corpus probe: "How many days of skilled nursing
facility care does Medicare cover in a benefit period?"
(federal) -> **"Medicare Coverage of Skilled Nursing
Facility Care" (publication 10153, the repaired EPUB) at
#0** — in g0001 this doc's stored text was raw EPUB bytes
and its title was its own URL; the repaired text (which
states the 100-day figure verbatim) now surfaces first,
and the sibling repaired 11359 booklet surfaces at #2.
Plain /search probes against the serving process (legacy
dispatch path, for the record): the first hits were
generic member pages and §3006 did not appear in the
returned list — the legacy /search path (boosts off) is
not the ask path and was not a deploy gate in v22/v22.1
either; the ask-path harness above is the authoritative
probe set. The three plain probes cost their known
~$0.00024 each (see ledger).

### Consistency field (unchanged semantics)

/health "consistency" remains the legacy npz-vs-manifest
check (ok=false, −440, warn-only) because the legacy data
files were deliberately left untouched. g0002's exactness
is the finalize assert + the direct store reads above.

### Spend across the deploy window

OpenRouter key remaining read $5.001726944 at the
pre-check and the same $5.001726944 after the restart and
again at the final read. spend_today moved $0.000793 ->
$0.001512 (+$0.000719), entirely the three plain /search
record probes at their known ~$0.00024 each. The finalize
and both v2 harness runs made no paid calls (local
embedder; rewrite/rerank off).

### Repo

Pushed to jamescordrey-ops/fssa-search-server on top of
6a54368 (v22.1): server/fssa_search_server.py (the
deployed v22.2 file), tools/offline_checks.py (64-check
suite), tools/finalize_g0002.py (the finalize variant),
data/enrichment.jsonl (the g0002 export),
data/generations/g0002/manifest_g0002.json (post-finalize)
+ vector_reconciliation.json (measured) +
g0002_delta_manifest.json + remains_binary.json,
docs/STATUS-v22.2.md + docs/MECHANISMS_TRIGGER_AUDIT-
v22.2.md + docs/G0002_DELTA-v22.2.md (scrub-swept),
CHANGELOG v22.2 section. Tag v22.2. Head SHA verified via
the GitHub connector read tools after the push and
recorded in the deploy report to the parent (the repo doc
intentionally does not name its own commit).
repaired_docs.json itself is NOT pushed: it is corpus
content, and corpora are versioned manually per the repo
format (the delta README + manifest in the repo record
the repair; the docs live in the VM's v22.2 tree).

### Cleanup

All one-off tasks (DeployV222FinalizeOnce,
DeployV222ProbeOnce) and every v222_* scratch file (ps1
runners, probe scripts + results + logs, finalize log,
finalize_g0002.py + repaired_urls.json in the server dir,
the .new staging file, the v222_probe dir incl. its
junction) were deleted from the PC. Kept: the rollback
fssa_search_server_v22.1.py.bak, generations/g0002 beside
g0000/g0001, and the v22 support set.

### Follow-ups on record (not this deploy)

- Fleet-written enrichment questions for the 48 repaired
  sections (they carry the deterministic layer only).
- The stock finalize_vectors.py matched path carries the
  duplicate-text twin-row bug fixed here in the variant;
  folding the fix back into the stock tool is a v23
  consolidation item.
