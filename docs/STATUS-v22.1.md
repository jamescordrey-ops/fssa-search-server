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

Built in fssa-qa/v22.1/ as a copy of this v22
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

## DEPLOY — v22.1 + g0001 + full re-embed live (2026-10-14/15, ~23:15–00:35 EDT)

Executed end-to-end under the operator's authorization ("finish
up all the fixes, go for 22.1"). Verification boundary: /health
+ server-local probes ONLY against the serving process; the v2
ask path was verified out-of-process with the deployed file
(rewrite/rerank OFF, no paid model calls); no live /ask was
made by Muse — the operator tests himself.

### Step 0 — restart-proofing the file (VM, before deploy)

The v22 deploy found the logon task does not inherit the
RETRIEVAL=v2 user environment variable (the live v22 instance
had to be launched directly holding it). Cure taken in the
code: `_v2_open` now defaults the request to **v2 when the
variable is ABSENT**; RETRIEVAL=legacy in the environment
still selects the v21 path, and every fail-soft below that
(FTS5 absent, store missing) is unchanged. Proven offline
three ways: RETRIEVAL absent -> requested v2 / effective v2;
RETRIEVAL=legacy -> legacy; RETRIEVAL=v2 -> v2. Suites
re-run against the changed file: tools/offline_checks.py
**56/56 PASS**, tools/offline_checks_v22.py **20/20 PASS**
(its legacy phase already pinned RETRIEVAL=legacy explicitly;
its docstring premise was corrected to say so — no check was
weakened). **Server file deployed: 12,187 lines, MD5
1f137b19634d4b3c6ed3a2a79a9b509d** (py_compile OK).

### Sources (what was deployed)

- Server file: this tree, post-Step-0 (above).
- Generation: the v22 tree's `generations/g0001_staging/`
  (completed fleet enrichment: 6,095/6,095 sections,
  55,593 questions; chunk/section ID sets identical to
  g0000). Placed as `generations/g0001/`; the DB was renamed
  to `corpus_g0001.db` on placement (the staging build's
  internal name is corpus_g0000.db) and its meta generation
  label set to "g0001" on the deployed copy. DB MD5 verified
  VM->PC: 1b8d1e8c….
- enrichment.jsonl: the completed file (6,095 rows, MD5
  b4c0d692…) replaced the g0000-era copy in the server dir.
- pins.json / retrieval_profile.json / growth_terms.json:
  byte-identical between the v22 and v22.1 trees (MD5s
  fc90fde7…, 654edaa8…, 4e49199b…); the v22.1 copies were
  placed and re-verified on the PC.
- g0000 was NOT disturbed; CURRENT pointed at it until the
  flip below.

### Pre-check (v22 live, server-local /health)

ok=true; live MD5 1A0E9C65… (v22 fixed file); docs
1291/3955; embeddings_loaded 73,014; consistency ok=false
delta −440 (the calibrated floor, legacy check); openrouter
spend_today $0.756941, key_limit_remaining $5.002520069,
caps $10/day + $1/ask untripped. Backup: live file ->
`fssa_search_server_v22.py.bak` (MD5 1A0E9C65…, verified).
PC C: free space was 2.9 GB at prep (the g0001 set is
~0.75 GB; no other large copies were made).

### Full re-embed (the vector cure) — finalize on the PC

**The trap, avoided by construction:** finalize_vectors.py's
"matched" class REUSES stored vectors wherever text hashes
match — run against the live embeddings file it would have
kept the old-build vectors and the canary would have failed
again (the v22 finding repeating). This run therefore fed
the tool an embeddings file with NO rows, so every one of
the 5,241 docs took its EMBEDDED path and every chunk text
was embedded fresh under the current nomic-embed-text
(digest 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947
efcf3c45e59f, read from Ollama at run time). The
vector-canonical detour is unnecessary in this end state:
the generation's own chunks are the chunks, 73,454 of them,
sections unchanged at 6,095. Measured result (exit 0, the
tool's exactness assert passed before any state flipped):
classes {matched 0, vector_canonical 0, **embedded 5,241**,
orphan_rows_dropped 0}; vectors_g0001.npy **73,454 x 768**;
synthq_vectors_g0001.npy **55,593** (every enrichment
question, fresh); meta vectors_state -> ready;
embedding_digest recorded = the current digest. Throughput,
measured not assumed: a warm single-text probe ran
20.9 ms/call, but the sustained rate over Ollama's request
log was ~30 ms/call; the full 129,047 texts took ~65
minutes. v21's/v22's data files (corpora, embeddings.npz,
g0000) were not touched.

### Canary proof — BEFORE the flip (the hard gate)

With a probe CURRENT pointing at g0001, the deployed file
was imported out-of-process with RETRIEVAL ABSENT (the code
default alone): open true; health block {requested v2,
effective v2, fts5 true, generation g0001, vectors ready,
profile v22.1}. Manual replication of the boot canary on
its own five chunk picks (rows 1, 1001, 30001, 60001,
73454): **cosine 1.000000 on every pick (min 1.000000 >=
0.999)**; the module's own canary thread then reported
"ok (5 chunks measured)" and embedding_drift = false.
Store read directly: 73,454 chunks = 73,454 vector rows;
55,593 synthq rows = 55,593 index entries. Had any of
this failed, the flip would not have happened.

### Flip + swap + restart — the standard path

CURRENT -> "g0001"; the v22.1 file moved into place (live
MD5 1F137B19…, verified); restart via the standard
`restart_fssa.ps1` (killed the v22 instance, PID 13656;
the 'FSSA Search Server' logon task launched the fresh
instance, PID 22704, started 00:33:44 EDT). No direct
launch, no environment variable held by the deploy — the
restart-proofing is the code default, and it was proven
against the deployed file + layout (same harness as the
canary proof, this time through the real generations/
CURRENT, RETRIEVAL absent): requested v2, effective v2,
generation g0001, canary ok, drift false. Live /health
after boot: ok=true, docs 1291/3955, caps $10/$1. As in
v22, the live process's own retrieval block reads
requested=null until its first ask-path dispatch arms v2
(the lazy-open semantics are unchanged); what changed is
that the arming no longer depends on the environment.

### Probes (no /ask; rewrite/rerank off; vectors LIVE)

v2 ask-path harness on the deployed file + g0001:
divisor ("What is Indiana's Medicaid transfer penalty
divisor?") -> IHCPPM 3006.00.00 **#0**; eyeglasses ->
Vision Services PROMOD00051 **#0**; QIT -> IHCPPM
2615.75.15 **#0**; ABLE-2025 (federal corpus) -> the IRS
newsroom ABLE page **#0**, SSA POMS SI 01130.740 in the
pool at #6. CSRA, the operator's verbatim question ("If I
go into a nursing home how much of our savings can my
wife keep?"): `_registry_matches` -> **[csra]** (the
v22.1 widening engaging on plain phrasing); the R
channel's top section is the ILTCP Spousal
Impoverishment Protection Law page, which is also the
fused pool's **#0** (IHCPPM 3010.20.10 at #1). Plain
/search probes against the serving process (legacy
dispatch path, for the record): divisor -> §3006 #0;
eyeglasses -> PROMOD00051 #1; QIT -> §2615.75.15 #0.

### Consistency field (unchanged semantics)

/health "consistency" remains the legacy npz-vs-manifest
check (ok=false, −440, warn-only) because the legacy data
files were deliberately left untouched. g0001's exactness
is the finalize assert + the direct store reads above.

### Spend across the deploy window

Zero paid movement: the OpenRouter key's remaining
balance read $5.002520069 at the pre-check and the same
$5.002520069 after the restart (~00:34). Oct 14's ledger
closed at spend_today $0.756941; Oct 15's opened at $0.0
(the midnight rollover fell inside the re-embed window).
The only model work in the window was the local embedder
(free) and the harnesses (rewrite/rerank off). The three
plain /search probes afterwards add their known ~$0.00025
each on Oct 15's ledger.

### Repo

Pushed to jamescordrey-ops/fssa-search-server on top of
cfdde71 (v22): server/fssa_search_server.py (the deployed
file), tools/offline_checks.py + tools/offline_checks_v22.py
(the v22.1 suites), tools/retrieval_cases.jsonl (24 cases),
data/enrichment.jsonl (completed), data/generations/g0001/
manifest + measured reconciliation (post-finalize),
docs/STATUS-v22.1.md (scrub-swept), docs/
REGISTRY_TRIGGER_AUDIT-v22.1.md, CHANGELOG v22.1 section.
Tag v22.1. Head SHA verified via the GitHub connector read
tools after the push and recorded in the deploy report to
the parent (the repo doc intentionally does not name its
own commit).

### Cleanup

All one-off tasks (DeployV221FinalizeOnce, PushV221Once)
and every v221_* scratch file (probe, harnesses, runner,
log, the empty embeddings npz, the probe generations
junction dir) were deleted from the PC. Kept: the rollback
fssa_search_server_v22.py.bak, generations/g0001 beside
g0000, and the v22 support set (tools/finalize_vectors.py,
tools/enrich_sections.py, pins.json, retrieval_profile
.json, enrichment.jsonl, growth_terms.json).
