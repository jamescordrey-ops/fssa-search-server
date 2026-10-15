# FSSA server v21 — STATUS (VM-side build)

Built 2026-10-14 (EDT) from the v20.2 tree. Scope of THIS stage: the
build only. No PC access, no deploy, no live model/API calls, no
money spent. Verification is py_compile plus the offline checks
below; nothing in this file claims a live result.

Server file: `fssa_search_server.py`, 11,117 lines (v20.2: 10,059),
MD5 `b69216e62ba4a7e53ed6817645a45403`. New files: `growth_terms.json`
(server dir), `tools/growth_loop.py`, `tools/offline_checks.py`,
`tools/vocab_audit.py`, `tools/fixtures/gap_fixtures.jsonl`, the
demonstrated staging output under `tools/staging/`, and the
vocabulary-audit output under `tools/vocab_staging/`.

The two failures this version answers, restated from the records:
(1) v20 eyeglasses ask: the IHCP Vision Services module (PROMOD00051)
held the exact answer and never entered the candidate pool.
(2) v20.2 divisor ask (the operator's own test, 2026-10-14 19:23 EDT):
fast IDK in 26.1s, badge verified_reduced; the 16-candidate pool was
all generic in.gov pages (fused 0.0602–0.0700), IHCPPM 3006.00.00 was
nowhere in the pool, and VERIFIED_FIGURES never engaged because no
draft claimed a figure for the registry to check.

---

## CAP RAISE — the operator's config change (2026-10-14), read this first

The server's real-money ledger caps rise in v21: the daily cap
goes from $2.00 to **$10.00** (`OPENROUTER_DAILY_CAP_USD`
default, L5919) and the per-ask ceiling from $0.50 to **$1.00**
(`PER_ASK_CEILING_USD` default, L499). This is a directed config
change, NOT a safety removal: the reservation ledger, the
single choke point, the trip behavior, and the /health
reporting are all unchanged — the fence still exists, just
higher. Reason (the operator's): protect grading and testing
sessions from a mid-session cap trip silently gating off the
paid legs. Every user-visible reference follows the constants
(/health openrouter block, cap-trip messages, the boot warm
print); comments that still mention $2.00/$0.50 are annotated
as the through-v20.2 values. Env overrides still work, so the
operator can retune without a rebuild. The offline checks pin
both new defaults.

---

## WS1 — Retrieval accuracy stack

### 1a. Exact-term rescue pass (`_rare_terms` L1160, `_rescue_search` L1200, hybrid integration ~L1640-1730, handler re-run ~L10826)

- `_rare_terms(question)`: the question's rare/content terms with
  per-class caps — single content words first (cap 8, STOP and a
  generic-program stoplist removed), then bigrams (6), then
  trigrams (4), then growth-loop mapped terms (WS3c). First offline
  iteration starved the singles (the CSRA question's list was 14
  trigrams, zero singles); the caps are the fix.
- `_rescue_search(question, corpus, exclude_keys, limit=4)`: one
  pass over docs × `_doc_chunks` computing document frequency and
  candidates together. A chunk qualifies with ≥2 distinct matched
  terms, or one term with df ≤ 5. Dedupe ≤2 chunks per document.
- Scoring deviation, with evidence: the spec's order was
  co-occurrence, then rarity, then authority. Under that literal
  order the offline check FAILED: a generic Indiana page matches
  five common terms (fee, adults, "indiana medicaid", …) and beat
  the Vision module chunk's three rare ones (eyeglasses, vision,
  promod00051). Scoring is therefore rarity-weighted
  co-occurrence first (sum of matched terms' log rarity), then
  distinct count, then source authority (the `_classify_hit` dtype
  table, +0.5 for Indiana sources). Co-occurrence still gates
  entry; rarity now counts for what it is worth.
- Trigger (i), inside `hybrid_search` (boosts=True only, so plain
  /search is untouched): after the diversity filter, coverage of
  the question's rare terms is measured over the rerank window's
  title+excerpt text. The pool is weak when ≥1 uncovered
  single/bigram/growth term exists, or ≥2 uncovered trigrams, or
  the window's top fused scores sit in a flat low band (top 8
  within 0.02 of each other and none above 0.09 — the divisor
  pool's shape). On a weak pool the rescue runs and its chunks
  are PROMOTED to the head of the pool with `_fused` set just
  above the window top, so the rerank judges them; the pool
  snapshot gains an `rs` rank channel, and a `rescue` event
  (trigger `weak_pool`, uncovered terms, count of chunks new to
  the fused selection) lands on the ask record via
  `_note_rescue`.
  - CALIBRATION (the operator's direction, 2026-10-14: "we
    have some latency to burn now"): the trigger fires on
    SUSPICION of a weak pool, not only on proof of one — the
    trigram bar and the flat-band condition above are that
    calibration. Accuracy over shaving seconds; the per-class
    unit budgets (simple 150s / hard 285s inside the 600s cap)
    remain the outer fence and leg-shedding still applies at
    the edge.
  - Match text is TITLE-INCLUSIVE (probe-set grounding,
    2026-10-14): each chunk's match text is its document title
    plus the chunk text, and `_chunks` already prefixes manual
    chunks with their section heading. This is load-bearing:
    the word "divisor" appears in the IHCPPM 3006.00.00
    document's TITLE only — its body never uses the word — so a
    body-only matcher could never rescue the divisor question
    on its key term. `tools/offline_checks.py` proves the
    channel: §3006's chunk text contains no "divisor", its
    title does, and the rescue ranks §3006 first on the bare
    question.
  - Promotion, not exclusion: the rescue runs with NO exclusion
    set inside hybrid_search. Reproduced reason (QIT anchor):
    IHCPPM 2615.75.15 sat at keyword rank 0, inside the window AND
    inside the fused selection when an exclusion-based rescue
    ran, was therefore skipped as "already in hand", and was
    then pushed out of the final 8 by the pin blocks that run
    afterwards. Promotion (move the pool object to the head when
    the rescue re-finds it) is immune to that ordering.
- Trigger (ii), in the ask handler: when a unit ends IDK — or in
  the PARTIAL tier, where some claims demonstrably lacked
  evidence (same eligibility frame as the existing branch-out
  block: gate != "time", judging ran) — the rescue runs for the
  unit's question over the ask corpus EXCLUDING chunks the unit
  already had. When the rescue injects new chunks the unit
  ALWAYS re-runs once with the enlarged hits (corpus first,
  same union shape as branch-out) BEFORE any Tavily branch-out
  is attempted; re-drafting on injection is the rule, not the
  exception (same latency calibration as trigger (i)). The
  re-run is adopted only when its badge strictly outranks the
  original's, so the rescue can complete a partial but never
  downgrade one. The outcome rides the unit record as `rescue`
  {terms, injected, urls} and as a `rescue` event with trigger
  `post_idk`. Refutation retrieval is unaffected (it uses
  plain `search()`).
- Cost note: a firing rescue is a full chunk scan (~73k chunks,
  measured 1–4s per call in offline runs over the 5,246-doc
  corpus). It supplements the fused pool; it never replaces it.

### 1b. Registry-driven retrieval (`_registry_pin_hits` L1288, fusion + pins in hybrid_search)

- Every VERIFIED_FIGURES entry gained a `topic` field:
  `{"phrases": ((search phrase, pin term), ...), "doc_hint":
  {"title": substr, "url": substr}}` (metadata block L3413; see
  WS2 for why it is an augmentation block). When a question
  matches an entry (date-aware, WS2), `_registry_pin_hits`
  searches the topic phrases and picks the carrying chunk in
  bridge order: doc-hint match first, then pin-term in
  title+excerpt, then top hit.
- The pin candidates fuse as their own RRF list (`rg` channel in
  the pool snapshot) and pin after rerank: at most 2 registry
  pins, then the rescue's best chunk (cap 1), applied BEFORE the
  bridge pin block so the served order is bridges, then registry,
  then rescue (bridge cap 3 unchanged).
- Correction to the build spec's premise, stated plainly: the
  spec listed "divisor §3006" and "QIT" among the 10 existing
  registry entries. NEITHER existed in VERIFIED_FIGURES (verified
  by reading the registry: exactly 10 entries — backpay_exclusion,
  able_onset_age, snap_resource_limit_in, csra, mmmna,
  home_equity_limit, asset_limit_2000, waiver_income_cap,
  ssa_2027_figures, difficulty_of_care). The divisor is NEW in
  v21 as a dated pair (WS2). QIT is a MECHANISM (`qit_over_cap`
  in MECHANISMS) plus the v14 topic bridge, not a figures entry,
  so it has no registry topic; its retrieval help is the bridge
  (unchanged) plus the growth-terms seed (below), whose
  expansion the offline checks cover. Topics were seeded for the 10
  real entries plus the 2 new divisor entries, each grounded in
  the entry's own source line and the corpus doc verified by
  grep (3006.00.00 / Medicaid_PM_3000.pdf, 2615.75.15, Vision
  PROMOD00051, 3005.10.05, 3010.20.10, SSA SSI Resources page,
  FNS BBCE page (host fna.usda.gov is correct as printed in the
  corpus), IRS ABLE and difficulty-of-care pages, SSA 2027 press
  release + fact sheet, the H&W waiver manual reference and the
  Indiana Eligibility Guide for the waiver cap).
- Topic/trigger vocabulary corrections (probe-set grounding,
  2026-10-14; the staged probe set under
  `probe-cases-staging/` carries the evidence): the exact
  phrase "community spouse resource allowance" appears
  NOWHERE in either corpus — the CSRA rule lives under
  "spousal impoverishment" naming (the ILTCP page and
  §3010.20.10). The csra and mmmna topics therefore pin on
  spousal-impoverishment vocabulary, not on the CSRA phrase.
  The mmmna trigger groups were widened with the vocabulary
  spouse-income questions actually use ("spouse of a medicaid
  applicant", "spousal income", "how much income can the
  spouse", "maintenance needs allowance"): both original
  groups demanded wording ("community spouse" AND "income")
  that natural phrasings split across the two. The SNAP
  topic's pin phrases hunted federal BBCE vocabulary; in the
  Indiana corpus the plain statement ("Most households are
  limited to $5,000 in resources") lives on the DFR SNAP
  member page, so the topic now points at that Indiana
  carrier (title/url hint "SNAP (Food Assistance)" /
  "snap-food-assistance").

### 1c. Retrieval regression harness — DE-SCOPED (the operator's decision, 2026-10-14)

A harness and a merged case file were built during this stage
and remain in the tree (`tools/retrieval_regression.py`,
`tools/retrieval_cases.jsonl`) UNUSED: the operator has
decided the v22 direction (an engine swap for the retrieval
core) directly, without waiting for harness numbers, so the
harness is not a build gate and not a decision tool. It is
noted here only to record the de-scope. The probe-case
staging (`probe-cases-staging/`) is untouched and stays for
possible later use; its grounding did drive real server
changes, recorded under 1a (title-inclusive rescue match
text) and 1b (topic vocabulary) above. Retrieval behavior in
this build is verified by `tools/offline_checks.py` (below).

## WS2 — Effective-dated registry (metadata L3413, divisor pair L3548/L3577, `_question_date` L3630, `_registry_matches` L3687, `_registry_values` L3733)

Schema (added to every entry):
- `valid_from` / `valid_to`: ISO date strings or None (None =
  no bound in the record, never an invented date).
- `family`: entries sharing a key are date-exclusive
  alternatives of one figure; matching keeps exactly one
  (in-force first, then latest valid_from) as a belt-and-braces
  rule on top of the windows.
- `history`: [{value, valid_from, valid_to, note}] superseded
  figures. An entry matches a question about a past period when
  a history window contains the question's date (its statement
  is date-aware prose), and `_registry_values(entries, on_date)`
  admits the historical value for that date only.
- `successor`: prose note about the next period's UNCONFIRMED
  value. Rendered in the registry prompt block as a Note; it is
  never a figure and never enters the allowed-values set.

Date selection: `_question_date(question)` — explicit month+year
→ the 15th; bare year (2019–2030) → June 1 of that year (a
year-only question sees the mid-year value: this is what makes
"the divisor in 2026" resolve to the $7,651 legacy entry, in
force on 2026-06-01); "next year"/"last year" relative to
today; no signal → None → today. `_registry_matches(question,
on_date=None)` filters by the windows. The handler's two
`_registry_values` call sites now pass the question's (or
part's) date. `_conflict_scan` (L2970) compares only against
entries in force TODAY. `_registry_block_hash` (L7880) now
fingerprints windows, history, and successor notes, so a dating
edit invalidates cached verdicts like a figure edit.

Design choice: the metadata is applied by an augmentation
block (`_V21_REGISTRY_META`, L3365) directly beneath the
registry, and the divisor pair is appended after it, so the 10
hand-verified v13–v19 entry statements stay byte-identical.
The block is the single place a future editor dates an entry.

Backfill (each from the entry's own source line / the verified
record; nothing invented):

| entry | valid_from | valid_to | notes |
|---|---|---|---|
| backpay_exclusion | None | None | rule has no dated figure in the record |
| able_onset_age | 2026-01-01 | None | history: $19,000, 2025-01-01→2025-12-31 ("2025 ABLE annual contribution cap") |
| snap_resource_limit_in | None | None | BBCE $5,000, no bound in the record |
| csra | 2026-01-01 | 2026-12-31 | CMS 2026 spousal standards |
| mmmna | 2026-07-01 | 2026-12-31 | the stated $2,705.00 minimum took effect 2026-07-01; the pre-July minimum is NOT in the record, so no entry was fabricated for Jan–Jun 2026 |
| home_equity_limit | 2026-01-01 | None | Indiana $752,000 eff. 1/1/2026 per its own statement |
| asset_limit_2000 | None | None | long-standing SSA limit |
| waiver_income_cap | 2026-01-01 | 2026-12-31 | successor note: 2027 cap NOT posted by Indiana as of 2026-10-14; ~$3,087 implied by 300% × $1,029 FBR is marked UNCONFIRMED in the note and is not a figure |
| ssa_2027_figures | 2027-01-01 | 2027-12-31 | its trigger groups already require a 2027 signal, so dating does not change when it fires in practice |
| difficulty_of_care | None | None | record names IRS Notice 2014-7 with no effective date; open window rather than a fabricated day |
| transfer_penalty_divisor_current (NEW) | 2026-07-01 | None | $8,027, family transfer_penalty_divisor, source IHCPPM 3006.00.00 |
| transfer_penalty_divisor_legacy (NEW) | None | 2026-06-30 | $7,651, same family |

Known limitation, stated deliberately: `compute_csra` remains
2026-anchored (its constants are the 2026 CMS bounds). With
date-aware matching, a question dated outside 2026 no longer
matches the csra entry, so no CSRA is computed for it (the
pipeline IDKs/hedges rather than serving 2026 math for another
year). Other years' bounds were NOT invented.

Verified by `tools/offline_checks.py` (WS2 block): month+year
and bare-year date parsing; divisor resolves to current only
today, legacy only for March 2026 and for bare-2026; ABLE 2025
question matches via history and admits $19,000 for that date
but not today; the csra entry stays silent for a 2027 question.

---

## WS3 — Growth loop

### 3a. Server gap rows are complete queue candidates (`_gap_class_guess` L4379, `log_gap` L4400)

Every `log_gap` row now also carries:
- `ask_id`: the ask's id (`_AskContext.ask_id`, minted in
  `_events_reset` per ask; the ask record in `_respond` carries
  the same `ask_id`, so a queue candidate joins back to its
  ask record exactly).
- `class_guess` from `_gap_class_guess(gate, missing,
  question)`: a question naming a future year →
  `newer_edition`; gate branch_out/sufficiency →
  `missing_document`; everything else → `unknown`. The
  vocabulary is deliberately honest: the retrieval-miss class
  is assigned by the RUNNER's coverage proof, not guessed
  server-side, and the figure class is refined runner-side too.

### 3b. Runner (`tools/growth_loop.py`, 632 lines)

Evolves the v15 tool (read in full as the base). What changed:
the v15 job (cluster gaps, NIM-draft mechanism candidates into
review_queue.json) is REPLACED by the staging pass; the
NIM-drafting path is dropped, not ported — v21's growth
surface is corpus + retrieval, not new mechanisms. The
pending-approval discipline carries over: every card is
`staged — pending human merge`, and the runner never writes
outside its staging dir.

Behavior: reads gap rows (path-configurable, default the
fixtures); classifies each (precedence: explicit
newer-edition/superseded signals → corpus-coverage proof
(retrieval_miss) → gate meaning (missing_document) →
unknown); the coverage check mirrors the server rescue's
scoring (distinct terms, rarity, Indiana/manual authority)
plus light stemming and the server's growth-terms
registrations — two calibration bugs were found and fixed
against the eyeglasses fixture during the build (a generic
medicaid.gov screening page first out-covered the IHCP
module; the module's "covers / aged 21 and older" wording
needed the stemming + registrations to win). For
missing_document / newer_edition with a source URL in the
row: allowlisted domains only (in.gov, fssa.in.gov, ssa.gov,
cms.gov, fns.usda.gov, usda.gov, federalregister.gov,
law.cornell.edu) may be fetched (urllib, HTML stripped,
chunked, staged under `fetched/` in corpus shape); any other
domain stages a link-only card and is never fetched. Dedup by
URL against the corpus and prior staged cards. Retrieval-miss
items become regression proposals
(`staging/regression_proposals.jsonl`) and lay-term
suggestions (`staging/terms_proposals.json`), never fetches.
The merge interface is documented in the module docstring and
written into every staging dir as README.md.

Dry mode is fully offline and is how the demonstration ran:
`python3 tools/growth_loop.py --dry-run --watchlist` against
`tools/fixtures/gap_fixtures.jsonl`:

```
staged gap-a086c09746: retrieval_miss — eyeglasses question
staged gap-5549b82235: retrieval_miss — divisor question
staged gap-c57ccf5007: newer_edition — 2027 CMS spousal standards question
staged watch-34cb703e91: watchlist — CMS 2027 spousal impoverishment standards
staged watch-214d20b1c2: watchlist — 2027 Medicare Part B premium
staged watch-58a834ebcd: watchlist — Indiana 2027 HCBS waiver income cap
gaps read: 3; cards staged: 6; regression proposals: 2; term proposals: 6
mode: dry-run (offline, nothing fetched)
```

The eyeglasses card matches the IHCP Vision Services module
(PROMOD00051, matched terms eyeglasses/covered/vision/
promod00051, 27 chunks by the runner's chunker) and the
divisor card matches IHCPPM 3006.00.00 (transfer/penalty/
divisor) — i.e. both real fixtures classify as retrieval_miss
against the VM corpora, as designed.

Fixture provenance, honestly: `tools/fixtures/gap_fixtures.
jsonl` rows carry an in-file `_fixture_note`. The eyeglasses
and divisor rows are RECONSTRUCTED from the real 2026-10-14
failure records (the live asks shipped as verified_reduced
meta-IDKs and wrote no gap rows; the VM's gap_log.jsonl copy
ends at the 13:02 EDT SFC ask). The third row is CONSTRUCTED
for the documented CMS-2027-spousal-standards open item. No
row pretends to be a copied log line.

### 3c. Lay-term registration (`growth_terms.json`, loader ~L2235)

Format: concept (substring-matched against the lowercased
query) → [corpus terms / section numbers]. The server loads it
once at boot from `growth_terms.json` next to the server file
(env `FSSA_GROWTH_TERMS` overrides); absence is tolerated and
logged only when present-but-unreadable. Consumption is live,
not staged-only: `search()` expands registered concepts like
SYNONYMS, and `_rare_terms` appends the mapped terms (so the
rescue hunts the section numbers directly). Seed (hand-written
from the two failures + the QIT bridge vocabulary):
divisor → [3006.00.00]; penalty divisor → [divisor,
3006.00.00, average monthly cost]; eyeglasses → [vision,
promod00051]; qualified income trust → [2615.75.15, miller
trust]; miller trust → [2615.75.15, qualified income trust].
The runner's staged term proposals merge into this file by a
human act (merge interface, above).

---

## WS4 — Timeout + logging fixes

### 4a. NIM legs honor the leg deadline (`_nim_text` L6751, deadline block ~L6790-6830; `_judge_seat_text` L5536)

Root cause, confirmed in the v20.2 code: `_nim_text` retried
ONCE on any exception with the FULL timeout again, so a 30s
judge leg read-timing-out at 30s ran a second 30s attempt
(seat_fail latencies 60.19s/60.16s in the v20.2 retest).
Fix: `_nim_text(..., deadline=None)` — each attempt's socket
timeout is min(timeout, deadline − now); no attempt (the
retry included) starts once the deadline has passed, and a
failure that consumes the budget raises with the deadline
named (the judge path's timed_out heuristic still trips on
the latency). `_or_text` / `_xai_text` / `_groq_text` forward
`deadline`; `_judge_seat_text` computes one deadline per leg
(now + the `_leg_timeout`-clamped budget) and passes it to
slots 0/4/5 and the NIM dispatch; the draft wrappers
(nim_ask/or_ask/xai_ask/groq_ask) accept and forward it, and
`_generate` passes per-leg deadlines for the Groq primary,
each bench leg, the xAI leg, and the OpenRouter leg. No
budget VALUE changed (THIRD_JUDGE_TIMEOUT 30, NIM_DRAFTER_
TIMEOUT 60, etc. untouched).
Not threaded, stated plainly: slot 6 (the rationed Gemini
Flash rung) runs through the Gemini client, which keeps its
own spacing/timeout discipline; and `_utility_text`
(rewrite/rerank) is not a seat leg and keeps its own 30–45s
timeouts.
Verified offline (`tools/offline_checks.py`, stubbed
transport that sleeps its full socket timeout then raises):
no-deadline control still retries at [30, 30] (the pre-v21
shape); with deadline = now+0.3 and timeout=30 the transport
is called ONCE with timeout ≈0.299 and the wall is 0.30s with
a deadline-named error; with a generous deadline the retry
survives at [0.2, 0.2].

### 4b. Every seat outcome path emits its reason (`_note_seat_skips` L5438 + call sites)

The helper (added at the start of this build) records a
`seat_skip` event for each passed-over candidate worth
explaining — the configured lead, the round's planned pick,
and any candidate sorting ahead of the pick in the
layer-major walk — with the reason resolved as: attempted
(its seat_fail speaks; skipped here) / no key configured /
benched (seat-health cooldown) / model family excluded this
round / the round's planned pick leads / planned pick
unusable, walk fell through / passed over by the seat walk.
Deduped per (job, model) per ask; display-only picks
(`_panel_info`, `_seat_health_block`) deliberately do NOT
call it.
New call sites: `_judge_seat_text` — on success, on the
no-candidate return, and on full exhaustion. `_generate` —
bench success, bench pick-None; plus the previously silent
failure paths now emit v20.2 events: bench leg failures get
`seat_fail` (they recorded only aggregate seat health
before), bench exhaustion gets a `seat_down` whose reason
distinguishes all-failed vs budget-spent vs none-available,
the Groq primary / xAI / OpenRouter leg failures get
`seat_fail` (they only printed before), and the chain's final
raise is preceded by a `seat_down`.
Honest limit: why DeepSeek V4.1-Flash did not serve in the
operator's divisor ask is genuinely ambiguous from the v20.2
record (the ~18:05 retest's 600s bench would have expired
before the 19:23 ask; the atomic panel plan may also have led
with Nemotron Super on measured latency). v21 does not assert
which fired there; the skip reasons cover every branch so the
next such ask cannot be reason-less.
Verified offline: `_note_seat_skips("judge_nim1", None, [])`
on a keyless machine emits seat_skip events whose first
reason is "no key configured (slot 2)", and a second call in
the same ask adds none (dedupe).

---

## WS5 — 2027 delta readiness (runner watchlist)

`tools/growth_loop.py --watchlist` stages standing cards for
the three not-yet-published items, each with expected source
domains and status "awaiting publication": CMS 2027 spousal
impoverishment standards (cms.gov), 2027 Medicare Part B
premium (cms.gov; expected late November 2026), Indiana 2027
HCBS waiver income cap (in.gov / fssa.in.gov; the card repeats
that the ~$3,087 implied figure is UNCONFIRMED). Nothing was
fetched; the watchlist is data about targets, not data.
Already covered, so NOT on the watchlist: SSA's 2027 COLA
material published 2026-10-14 and incorporated into the
corpus in v19 — the SSA press release (3.6% COLA), the SSA
2027 fact sheet (FBR $1,029/$1,544, SGA $1,770/$2,950, TWP
$1,270), and the FNS SNAP FY27 COLA memo; the registry's
ssa_2027_figures entry carries the same figures, dated
2027-01-01→2027-12-31.

---

## WS6 — Vocabulary audit (`tools/vocab_audit.py`; output `tools/vocab_staging/`)

The tool hunts the v20/v20.2 failure class directly: the
corpus states a rule in its words, the question arrives in
the asker's words, and retrieval never bridges the two.
(1) Unit extraction scans both corpora for answerable units
(chunks carrying a rule/figure by regex cues: $ amounts,
"every <n> <time unit>", per month/year, "limit", section
numbers), document titles as identity, and VERIFIED_FIGURES
entries; each unit carries rarity-weighted key terms from a
document-frequency pass. (2) Phrasing generators: G1 template
transforms (amount → "what is the limit…", frequency → "how
often…", a generic rules form, and the key-term control
query); G2 mechanical substitutions (built-in lay-synonym
map both ways, acronym expansion and contraction, number
flips digit↔word and $8,027↔8027, single-edit typo on the
rarest term); G4 real questions from the canonical case file
with their grounded docs; G3 LLM cross-examination is
implemented as a pluggable mode (`--llm nim`, env key,
model sees only the chunk) and defaults OFF — this build
made no live calls. (3) Each phrasing dry-runs the server's
retrieval in-process (retrieve_ask_hits; keyword/bridge/
registry/rescue channels; vector NOT exercised, no
embeddings file on the VM) and records the unit doc's rank.
(4) Classification emits growth-loop-shaped staging cards:
vocabulary_gap → concept-map card, total_miss → case
proposal, typo_only_fail → fuzzy-rescue note in the report,
wrong-program winner → scope-collision card, plus a coverage
report (report.json / report.md).

VERIFICATION RUN (this build; deterministic generators
G1/G2/G4 only, no live calls). Stated coverage: all registry
units the extractor resolved (8 of 12 entries; the mmmna,
home-equity, and divisor-pair entries share carrying docs
with units the implicated pass extracted — §3006.00.00 and
§3010.20.10 are covered as implicated units — except the
§3005.10.05 home-equity doc, which this sample did not
reach), all units from documents implicated in past failures
that the title sweep matched (46), and a seeded random
sample of the remaining cue-chunks (10) — 64 units total,
261 phrasings (4.08 per unit).

Headline results: 60 of 64 units pass outright. G1: 0 of 161
phrasings failed. G4 (real questions): 4 of 36 failed
(11.1%). G2 produced typo variants only (64 run, 0 failed):
the acronym/lay-synonym/number transforms found no purchase
on the generated base phrasings, which rarely contain
acronyms, lay-pair phrases, or $ figures — their real
exercise needs natural phrasings, i.e. the post-deploy G3
run. Classes: vocabulary_gap 4, total_miss 0,
scope_collision 0, typo_only_fail 0. The 4 gap cards, read
honestly: two are chunk units of the DFR SNAP /about-snap
subpage, where the real SNAP question retrieves the
same-title main SNAP page instead (the audit scores by exact
URL; a sibling-page nuance, not a wording gap), and two are
out-of-state medicaid.gov demonstration-list docs (Minnesota
spousal impoverishment, New Hampshire dental) swept in by
the implicated title match, which Indiana questions
correctly do not retrieve. This sample therefore confirms no
new Indiana vocabulary gap; it proves the machinery
end-to-end and leaves 4 cards for the human merge decision.
The first FULL run (all units, G3 on) is the post-deploy
tail step (see Not done).

---

## Verification summary (all offline, this build)

- `python3 -m py_compile` — server + all tools: OK.
- `tools/offline_checks.py` — 30/30 PASS, exit 0 (WS2 dating,
  WS3a gap fields, WS3c loading/expansion, WS4a deadline
  behavior against a stubbed transport, WS4b skip events +
  dedupe, WS1a rescue ranking for both failure docs, the
  title-channel proof for §3006, and both raised cap
  defaults pinned at $10.00 / $1.00).
- `tools/growth_loop.py --dry-run --watchlist` — end-to-end
  offline; staging output under `tools/staging/` (6 cards,
  regression_proposals.jsonl, terms_proposals.json, README).
- `tools/vocab_audit.py` — verification run (G1/G2/G4 only,
  64 units, 261 phrasings): 60 units pass, 4 vocabulary-gap
  cards staged, no total misses; see WS6 above.

## Not done / not claimed

- No deploy, no PC contact, no live ask, no model/API call,
  no spend (this stage's constraints). Live behavior of the
  rescue under the LLM rerank, and of the deadline under real
  NIM latency, is unproven until the deploy stage runs the
  operator's tests.
- Stage 3 grading (anchor battery vs the v19 panel) remains
  gated on the operator's separate go; v21 does not touch it.
- The rescue's handler trigger (ii) inherits the branch-out
  block's eligibility (it does not fire for time-gated units,
  discovery surveys, or judging-never-ran units).
- `growth_terms.json` ships as a server-dir file; the deploy
  stage must place it next to the server file on the PC (or
  set FSSA_GROWTH_TERMS), or the registrations silently stay
  off (boot tolerates its absence by design).
- V21 TAIL STEP (post-deploy): the first FULL vocabulary
  audit, including G3 (`tools/vocab_audit.py --llm nim` over
  the full unit set), runs as a background job after deploy
  to seed the growth queue. This build ran the deterministic
  generators (G1/G2/G4) over a stated sample only (WS6).
