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

---

## DEPLOY — v22 live (2026-10-14, ~22:15–23:20 EDT)

Executed end-to-end under the operator's authorization (deploy,
restart, healthy, no gates). Verification boundary: /health +
server-local /search probes ONLY against the serving process;
no live /ask was made by Muse — the operator tests v22 himself.
One mid-run hold was observed: the file swap waited for a
registry fix (the ABLE contribution figures lived inside
able_onset_age, whose ANDed triggers require onset vocabulary,
so a pure contribution question never engaged the registry; the
fix split able_contribution_limit + able_contribution_limit_legacy
into their own dated entry pair with contribution triggers and
pins at SSA POMS SI 01130.740 and the IRS newsroom page). The
deployed file is the FIXED file: **12,017 lines, MD5
1a0e9c6517471dabce6df23022cd9e1d** (parent-verified figure
re-verified on the VM and on the PC staging copy; py_compile OK).

### Snapshot + staging (before any swap)

MD5 snapshot of the deployed set, taken on the VM and re-verified
on the PC after copy, all exact: fssa_search_server.py (fixed,
above), pins.json fc90fde7…, retrieval_profile.json 654edaa8…,
enrichment.jsonl 758e77ec…, growth_terms.json 4e49199b… (byte-
identical to the v21 copy already live), generations/CURRENT
("g0000"), generations/g0000/corpus_g0000.db c09368df… (363MB),
manifest_g0000.json, vector_reconciliation.json, plus
tools/finalize_vectors.py + tools/enrich_sections.py (placed in a
tools/ subdir of the server dir; the enrich tool resolves its DB
script-relative, so the layout matches its expectation). g0000
was re-checked against the snapshot immediately before transfer:
unchanged (the parallel g0001 staging work does not touch it).

### Pre-check (v21 live, server-local /health)

- ok=true; live MD5 B69216E62BA4A7E53ED6817645A45403 = v21.
- docs_in 1291, docs_federal 3955, embeddings_loaded 73,014.
- Consistency: ok=false, delta_rows −440 (orphan 1, missing 134,
  mismatched 393) — the calibrated healthy floor.
- openrouter: cap $10.00, cap_tripped false, spend_today_usd
  $0.755349, key_limit_remaining $5.004058994.

### PC FTS5 check (decides the flag)

PC Python 3.12.10, sqlite 3.49.1: CREATE VIRTUAL TABLE … USING
fts5 and fts5vocab both succeed; numpy 2.5.3 present; Ollama at
127.0.0.1:11434 serves nomic-embed-text (digest 0a109f422b47…).
FTS5 is PRESENT, so the v2 flip was permitted (subject to
finalize below).

### Finalize (tools/finalize_vectors.py, on the PC, before the swap)

Ran detached against the live embeddings.npz + Ollama. Exit 0;
its exactness assert passed (every chunk exactly one vector)
before any state flipped. MEASURED classes (replacing the
recorded expectations): matched 4,714 docs; vector_canonical
393 docs (the recorded 393 mismatched URLs); embedded 134 docs
(the recorded 134 missing URLs, embedded fresh with nomic);
orphan_rows_dropped 2 (the recorded 1 orphan URL held 2 vector
rows). Outputs: vectors_g0000.npy (74,135 × 768),
synthq_vectors_g0000.npy (27,987 × 768 — every enrichment
question embedded), meta vectors_state → ready, manifest +
vector_reconciliation.json updated with the measured classes and
the embedder digest. Note the chunk total: 74,135, not 73,454 —
vector-canonical replacement re-chunks the 393 docs from the
vector file's own texts (+681). Section count moved 6,095 →
6,029 for the same reason. Follow-up per the build record:
tools/enrich_sections.py --refresh-fts ran after finalize
(exit 0; section_fts enrichment restored for all sections) so
the rebuilt sections carry their synth_q/aliases again.
v21's data files (corpora, embeddings.npz) were not touched.

### Swap + the RETRIEVAL flag (one deviation, worked around)

- Backup: live file → fssa_search_server_v21.py.bak (MD5
  B69216E6…, = v21; rollback is this file + RETRIEVAL unset).
- Fixed file copied into place; live MD5 = 1A0E9C65…, exact.
- DEVIATION: the logon task ("FSSA Search Server", direct
  pythonw launch) did NOT inherit the RETRIEVAL=v2 user
  environment variable, although the variable was set and
  read back in HKCU. Two task restarts both booted with
  /health retrieval.requested = null (legacy). The running
  instance was therefore launched directly (pythonw, same
  arguments and working directory as the task) from a process
  with RETRIEVAL=v2 in its environment; launch record kept
  with the deploy logs. The user-level variable REMAINS set,
  so a future logon may arm the task path on its own — but as
  of this deploy, a manual restart via restart_fssa.ps1 (the
  task path) is EXPECTED to come back on legacy until the
  variable propagates. Recorded here so nobody is surprised;
  the durable cure (a launcher that sets the variable, or a
  reboot) is the operator's call, not taken unilaterally.

### Live state + v2 verification (no /ask used)

- The serving process (launched 22:55 EDT with RETRIEVAL=v2)
  answers /health ok=true. Its retrieval block still reads
  requested=null: _v2_open() is LAZY — it runs on the first
  ask-path dispatch (plain /search passes boosts=False and by
  design never dispatches v2), so the live process arms v2 on
  the operator's first /ask, not at boot.
- v2 was therefore verified OUT OF PROCESS on the PC with the
  identical file, environment, and data (import the deployed
  module, RETRIEVAL=v2, rewrite/rerank OFF, no model calls):
  _v2_open() → true; health block {requested v2, effective v2,
  fts5 true, generation g0000, vectors ready, profile v22.1};
  store exactness read directly (chunks 74,135 = vectors rows;
  synthq 27,987 rows; vectors_state ready).
- Ask-path ranks in that harness (top hit): divisor natural
  phrasing → IHCPPM 3006.00.00 #0; eyeglasses → Vision
  Services PROMOD00051 #0 (legacy /search puts it #1); QIT →
  IHCPPM 2615.75.15 #0; ABLE-2025 (federal) → IRS ABLE
  newsroom page #0 with SSA POMS SI 01130.740 at #1.
- Plain /search probes against the serving (legacy-dispatch)
  path, for the record: divisor #0 §3006.00.00; eyeglasses
  PROMOD00051 at #1; QIT §2615.75.15 #0; ABLE-2025 docs surface
  on the federal corpus (INvestABLE booklet #0, IRS pages).

### FINDING — embedding canary reports DRIFT; V1/V2 dark by design

The boot canary (run in the verification harness; the live
process will run the same check at its first v2 open) measured
cosine 0.9330 < 0.999 re-embedding chunk row 8 with the PC's
current nomic-embed-text, and disabled the vector channels, as
designed. Reading: the stored npz vectors are semantically
sound (0.933 is a near-match, not noise) but were produced by
an earlier embedder build/preprocessing and are not
bit-reproducible by today's model digest, so the strict
reproducibility gate fails closed. Consequences: v2 serves on
channels T/R/X (+ pins, rescue) — which produced every anchor
rank above with vectors already off — and V1/V2 stay dark
until the corpus is re-embedded under the current digest (a
maintenance decision for the operator/parent; NOT improvised
during this deploy). /health will show embedding_drift=true
in the live process after its first v2 open.

### Consistency field — what it does and does not say in v22

/health "consistency" is the UNCHANGED legacy check (npz rows
vs the corpus chunk manifest): it still reads ok=false,
delta_rows −440, warn-only, because v21's data files were
deliberately left untouched. The generation's exactness is a
different fact, proven by the finalize assert + the measured
manifest (74,135 chunks = 74,135 vectors, 0 unassigned). Any
expectation that /health consistency flips to ok:true in v22
does not match the built code; recorded as a build/brief gap,
not a deploy failure.

### Parent-noted soft spot (recorded, not a blocker)

g0000's registry_sources table predates the ABLE pair, so the
v2 R channel skips able_contribution_limit(_legacy) until the
g0001 generation build; the legacy hybrid pin path (and the
entries' own topic pins) covers them meanwhile.

### Spend across the deploy window

Server ledger (spend_today_usd): $0.755349 at pre-check →
$0.755712 after the swap boots → $0.756941 after the five
legacy /search probes. Total movement +$0.001592: ~$0.000363
around the boots and ~$0.001229 from the probes (~$0.00025
each, the known v21+ /search utility-work pattern). The v2
harness made no model calls (rewrite/rerank off; the canary
uses the local embedder). Key remaining ≈ $5.0037 of $10.
Caps remain $10/day + $1/ask, untripped.

### Repo

Pushed to jamescordrey-ops/fssa-search-server on top of
9ceb5f5 (v21): server/fssa_search_server.py (the fixed file),
server/pins.json, server/retrieval_profile.json, the v22
tools (build_generation, finalize_vectors, enrich_sections,
enrich_qa, bridges_to_pins, flip_list + flip_list.json,
offline_checks_v22, the retargeted vocab_audit), data/
enrichment.jsonl + generations/g0000 manifest + measured
reconciliation, docs/STATUS-v22.md (scrub-swept), CHANGELOG
v22 section. Tag v22. Head SHA verified via the GitHub
connector read tools after the push and recorded in the
deploy report to the parent (the repo doc intentionally does
not name its own commit).

### Cleanup

All one-off tasks (DeployV22FinalizeOnce, DeployV22RefreshOnce,
DeployV22SwapOnce, DeployV22LaunchOnce, DeployV22V2CheckOnce,
PushV22Once) and every v22_* probe/runner script + log created
for this deploy were deleted from the PC. Kept, as v22 support:
tools/finalize_vectors.py, tools/enrich_sections.py,
generations/, pins.json, retrieval_profile.json,
enrichment.jsonl, growth_terms.json, and the rollback file
fssa_search_server_v21.py.bak.

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
