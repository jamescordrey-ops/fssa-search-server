# FSSA Server v18 — Build Status

Build started 2026-10-14 (VM-side only). Authorization: full §§A–E build, offline proofs only.
Hard limits honored throughout: NO paid/quota API calls (no live OpenRouter, Gemini, Tavily, NIM),
NO PC access, NO git commits/pushes, NO battery, NO live asks.

Source: copied from ~/workspace/fssa-qa/v173/fssa_search_server.py (v17.3, 6,982 lines,
byte-identical to live) to ~/workspace/fssa-qa/v18/fssa_search_server.py. The v173 copy is
never edited.

## Step 1 — §B hard stop (gates everything)

- [x] Choke-point audit (all outbound paid/quota surfaces enumerated): raw transport is called only from `_gemini_call`, `_tavily_post`, `_nim_text`, `_or_key_state` (asserted in tests); OpenRouter dollars ride the reservation gate, Gemini rides its quota chain + persisted Flash budget, Tavily rides its monthly credit counter
- [x] Reservation gate: $2.00/day (default raised from $1.00), provable-max reservations (prompt bytes as input bound, max_tokens at price rows set to the most expensive reachable backend), per-HTTP-attempt, crash-safe persistent ledger counting unsettled reservations at full value, ledger day in America/Indiana/Indianapolis (arithmetic DST rules, no tzdata)
- [x] F11-1 timeout/5xx/cost-less responses settle at the reserved max; F11-2 atomic writes (temp+fsync+os.replace) + corrupt ledger fails closed (never $0); F11-3 usage_daily reconciliation via max (cached key state only, never a fetch inside the gate)
- [x] F9 pooled socket settimeout (`conn.sock.settimeout` in `_https_post`/`_https_get`)
- [x] F10 explicit per-ask context (`_AskContext` in a ContextVar + `_submit()` propagation at all pool sites) replacing _OR_TLS / _ASK_EVENTS / _RETR_TLS; per-ask cost now counts judge seats; `_panel_info` takes `served=` so records name the model that actually served
- [x] Also inside this step: R10a `_gemini_space` lock; R10b Flash counter persisted atomically (corrupt state reads as spent); R10c `_cache_put` eviction lock; Tavily state writes atomic
- [x] PROOF: tests/test_hard_stop.py — 13 tests, ALL PASS (2026-10-14): 40-thread × 6-call mocked hammer at ~$0.10 max/call (committed never exceeded $2.00 at any sampled instant; refusals occurred and settled spend stayed under), per-hole unit tests, socket/context/Flash tests, static choke-point audit

## Step 2 — §A accuracy — COMPLETE (proofs: tests/test_verifier.py)

- [x] F1 unruled non-supported refutation blocks Verified — the refutation verdict now STANDS as the final whenever Flash was asked and did not rule (the old `"supported" not in vs` fold dropped it exactly when the panel had said supported). Proof: TestF1RefutationStands (real `_judge_round`, mocked transports; refuted claim finals "contradicted", affirmed control ships)
- [x] F2 per-claim coverage; single-verdict claim cannot carry panel badge — badges derive from per-claim distinct-family coverage computed in `_judge_round`, not the round's mode string. Proof: TestF2BadgeCoverage (coverage ("google",) → verified_single; ("google","deepseek") → verified)
- [x] F3 final-rung Flash cannot overturn majority contradicted / code-forced finals — the final rung's failed list excludes conflict-origin claims and claims with ≥2 "contradicted" seat verdicts; the code-only conflict gate (F16) also blocks the rung. Proof: TestF3FlashExclusions (Flash never called on a majority-contradicted claim; a genuine split still reaches it and ships)
- [x] F6 arithmetic as a gate (both ship paths incl. capped partial) — `_arithmetic_errors` extracted as a pure helper; `_run_unit` folds arithmetic failures into the quantities objection channel on every rung and the strip re-check; `_assemble_capped_partial` runs it too. Proof: TestF6ArithmeticGate (wrong equation IDKs through the ladder despite a passing panel; right equation ships) + TestF4AllowList arithmetic case
- [x] F7 strip by exact span, never qualifiers, re-judge after strip, no ship on cap_hit/gen_error — cited claims carry verbatim spans; `_sentence_maps_to_claim` honors spans; strip eligibility refuses limiting-language/figure/duration sentences; the strip is skipped after cap_hit/gen_error/cost_capped and the stripped draft is RE-JUDGED (fresh `_judge_round`) before the ship test reads any pass. Proof: TestF7Strip (re-judge call observed with the stripped text; limiting sentence never stripped; span mapping)
- [x] F4 capped-partial allow-list (exact claim-text identity) + corrected note — construction reversed from deny-list to allow-list: a sentence ships only when its citation-stripped normalized text IS a supported claim of the round and was cited in the draft; returns (text, min_coverage). Proof: TestF4AllowList (unjudged and contradicted sentences excluded; nothing supported → None)
- [x] F5 figure-checker holes (1)-(4) — provenance scoped per sentence (question + registry/extra + derived + the sentence's own cited excerpts only); equation operands must already be in scope before the result derives; bare amounts ("910 dollars", "910.80 per month") checked; tolerance ±0.011. Proof: TestF5Figures (all four holes, each with a control)
- [x] F8 judge evidence parity (judges see what the drafter saw) — `_judge_excerpts` cap 1000→3000; the three `[:1000]` slices in `_claim_evidence` → `[:3000]`. Proof: TestF8EvidenceParity (2500-char excerpts reach judges whole)
- [x] F13 bundled parts get the full question — units carry `full_question`; judging, verdict-cache keys, arbitration, and the divergence check use the full question with the part scope stated; the full question also rides the part's drafting instructions. Proof: TestF2BadgeCoverage.test_f13
- [x] F14 required points: two-model confirmation + relevance test; objections text corrected — B-draft points become required only when a second model's judgment confirms them (unconfirmed points never block); `_points_missing` relevance test (≥2 shared content words with the question, or a figure); objection wording now matches the mechanism. Proof: TestF14PointRelevance (relevance half; the confirmation half is inside `_run_unit`'s divergence block, code-reviewed — exercised end-to-end only by the battery)
- [x] R5 arbitration prompt shows all verdicts with correct served-model labels + refutation material — lite/nim/nim2/local verdicts and the refutation verdict printed per claim, seats labeled from the ask context's served-model record, evidence via `_claim_evidence` (refutations included) in both scoped and unscoped paths. Proof: TestR5ArbitrationPrompt (both paths)
- [x] F15 spouse priming conditioned on the question (prompt blocks, SFC bonus, bridge trigger) — SYSTEM_PROMPT split: SPOUSE INCOME paragraph + CSRA share sentence moved to SPOUSE_PROMPT_BLOCK, appended by `_system_prompt_for(question)` at all 4 drafting call sites only when `_question_involves_spouse`; `search()`'s SFC boost requires a spouse-bearing query; the spouse topic bridge no longer triggers on "caregiver"/"caregiving" alone (the "structured family caregiving" phrase trigger is kept). Proof: TestF15SpousePriming
- [x] F16 conflict detection generalized beyond dollars; conflict claims resolved in code only — `conflict_numbers` spec (kind + pattern + stale-value watch list) added to `_conflict_scan`; ABLE registry entry added (onset before 46 eff. 1/1/2026, $19,000 cap, $100,000 SSI disregard, INvestABLE exempt) with an age-26 watch; conflict claims never enter the judge-facing claim set — `conflict_fail` is computed in code at every draft state and gates shipping, redrafts, and the final rung. Proof: TestF16Conflicts (age scan, both-values rule, code-only gate through `_run_unit`; judges never see an origin="conflict" claim)
- [x] F20 citation parse: bullet splitting, all markers read, URL maps to all chunks — `_split_sentences` splits on lines and strips bullet markers first; `_parse_citations` reads [n] markers anywhere in the sentence; a markdown-linked URL credits every retrieved chunk from that URL. Proof: TestF20AndR3Parsing
- [x] R3 factual-sentence detector widened (modal/future claim shapes) — will/can/cannot/can't/won't/will not/is the/are the added to `_FACTUAL_RX`. Proof: TestF20AndR3Parsing.test_modal_shapes_are_load_bearing
- [x] R4 confusables scoped to sentences asserting the period — a confusable fails only in a sentence containing it AND an assertion word AND no disclaimer marker, and only for mechanisms the caller passes (units pass their own). Proof: TestR4Quantities (assertion fails; disclaimer passes; out-of-scope mechanism never fails)
- [x] qit_over_cap canonical claim 3 rewrite (trust payback vs estate recovery) — claim 3 now states the death-time repayment as a REQUIRED TERM OF THE TRUST (Miller trust payback) and explicitly distinguishes it from Medicaid estate recovery (the state's separate claim against the estate). Proof: TestF16Conflicts.test_qit_claim3
- [x] Search-cache mutation bug fix (copy at boundary) + regression test — `hybrid_search` returns per-call copies (list and hit dicts) on both the cache-hit and fresh paths. Proof: TestCacheMutation (mutating a returned result does not pollute the next call)
- [x] Truth in labeling: stale docstring line removed; local mode made honest (F19 widened) — "No data leaves the PC." removed in Step 2's docstring edit; the honest local mode itself is Step 4 (F19)

## Step 3 — §C panel integrity — COMPLETE

- [x] Diversity guard: family identity, atomic seat picks before dispatch, covers all verdict paths — `_plan_panel()` plans both judge seats atomically on the ask context before stage 2 (distinct families; drafting family and already-served verdict families excluded); `_seat_pick` honors the plan (a planned seat that is excluded/keyless/benched yields NO same-family substitute); `_seat_exclusions` also excludes the drafting family dynamically at dispatch; the divergence oracle is family-distinct from both the active draft pick and the ask's drafting family. Proof: TestSeatPlan (draft-family exclusion; distinct-family plan; seat constants)
- [x] Badge rule (pass 2 §D): per-claim family coverage; badge = minimum over claims; webapp annotation line naming the panel that sat — coverage computed per claim in `_judge_round`; `_run_unit` badges verified only when every substantive claim has ≥2 families; capped partials badge by their minimum kept-claim coverage; multi-unit assembly uses an explicit rank order (verified_web ≈ verified > verified_single > partial > partial_single > idk; any IDK unit caps the assembly at the partial tier); `_panel_annotation` ("Judges: <models>" + single-family claim count) rides every unit's judging record, the aggregate response, the §E record, and the webapp's verify note. Proof: TestF2BadgeCoverage + TestF4AllowList coverage return

## Step 4 — §B remainder — COMPLETE (proofs: tests/test_pipeline.py + test_verifier.py)

- [x] Seat 2 swap: Qwen-family non-thinking instruct (slug = single named constant `JUDGE_SEAT2_MODEL = "qwen/qwen3-235b-a22b-instruct-2507"`, max_tokens capped at 1500); failover chain Llama-4-Maverick (fifth family) then NIM Ultra; routes and price rows registered for both new slugs; dynamic draft-family exclusion at dispatch (Step 3); Qwen out of seat 1's candidates (DeepSeek V3.1 → NIM Super). Proof: TestSeatPlan.test_seat2_constant_and_seat1_has_no_qwen. Slug+price verification is a pre-battery checklist item (below) — no live catalog read was made during the build
- [x] F12 verdict-cache fixes (5 defects + pass-2 key unification) — one `_verdict_cache_key` for get and put (claim text + question + mechanism block + registry fingerprint + the claim's OWN scoped evidence; refutations excluded by design and handled at the join); a fresh primary "contradicted" never resolves from cache; cached claims are refutation-checked like live ones (the join's invalidation branch is alive); the put requires ≥2 distinct verdict FAMILIES or a Flash ruling (hunt-promoted and conflict-origin verdicts never stored); fresh/nocache asks bypass the cache entirely (`use_cache`). Also fixed during proofing: `_registry_block_hash` iterated `.items()` on the list-shaped registry and silently produced a constant hash — it now fingerprints id+statement+figures+conflict specs per entry. Proof: TestF12VerdictCache (6 tests)
- [x] F17 answer-cache hygiene (no degraded stores; replay cost handling) — only verified/verified_web results enter the answer cache; results carry `ts`; a replay returns a copy with `cost_usd: 0.0` and `cached: true`. Proof: TestF17CacheHygiene (verified replay semantics; an IDK ask leaves no cache entry)
- [x] F18 pre-call deadline checks; webapp never re-POSTs sync after a job failure; server-side identical in-flight question dedupe — the evidence hunt is skipped inside 90s of the deadline; the webapp tracks its job id and NEVER falls back to a synchronous re-POST once a job exists (poll errors are tolerated and polling continues; `window._asking` blocks re-entrant submits); the server dedupes identical in-flight asks (same cache key): the duplicate waits up to cap+180s and replays the owner's result with `deduped: true`. Proof: TestF18InflightDedupe (two concurrent identical POSTs, one pipeline run, second response deduped at $0.00)
- [x] R6 branch-out hardening: post-redirect whitelist re-check, other-state filter, script/style strip — the direct-fetch fallback re-checks the whitelist against the POST-REDIRECT URL; page text is built by `_html_to_text` (script/style/noscript/template blocks removed before tag stripping); medicaid.gov/cms.gov pages naming a non-Indiana state (and never Indiana) are dropped. Proof: TestR6BranchOut (3 tests)
- [x] R7 precomputed chunks at load — `_doc_chunks` caches each document's chunks on the document dict; `search()` uses it (the corpus is no longer re-chunked per call). Proof: TestR7Chunks
- [x] R8 single-depth shared bundled retrieval; parallel units where state allows — `_retrieve_bundled` produces the merged hits and the per-part map from ONE pass (per-part depth 6); do_POST's parts take their hits from that map instead of fresh per-part searches; bundled units run in parallel (max 3 workers via the context-propagating `_submit`); the branch-out re-run stays serial (documented in code: per-unit events in parallel mode are the units' full ask events; the shared stage line is last-writer-wins)
- [x] Per-ask cost ceiling: ship best partial past the line (depends on F4 + F10) — `PER_ASK_CEILING_USD` (env `FSSA_PER_ASK_CEILING_USD`, default $0.50 — a build-time default for the operator to confirm, see the gate section): past the line the ladder stops buying redrafts and the best judged draft goes to the F4 capped-partial path (badge partial/partial_single by kept-claim coverage); the IDK record names the ceiling. Proof: the capped path itself is TestF4AllowList; the ceiling constant and rung check are code-reviewed (a live-fire proof would need judge spend, which is post-gate)

## Step 5 — §E observability — COMPLETE (proofs: tests/test_pipeline.py + tests/test_replay.py)

- [x] Ask-record + failed-draft persistence (per-claim verdicts + serving models + job-layer timestamps) — `_record_ask()` appends one JSON line per completed ask to `ask_records.jsonl` (env `FSSA_ASK_LOG`): wall time, question, corpus, local flag, badge, cost, panel, annotation, and PER UNIT the judged draft text (≤8,000 chars, including failed drafts, via the unit's new `draft_text`) plus every claim's final, per-seat verdicts, family coverage, serving models, and the unit's hits (excerpts ≤3,000 chars). Async jobs append a {kind: "job", job_id, created, finished, status} line (the P1 correction: total_ms was always honest; the job layer is what was missing). Append-only, lock-guarded, never raises. Proof: TestSectionERecords (ask line and job line verified field by field)
- [x] Judge-only replay harness (v18/replay_harness.py) + offline plumbing test with mocked judges — CLI loads §E ask records, re-judges each persisted unit draft via `_judge_round` with the verdict cache bypassed, optionally overriding seat 2 (`--seat2`), and prints recorded-vs-replay agreement per claim. Live use spends judge money: it is a POST-GATE tool and says so in its docstring. Proof: tests/test_replay.py (4 tests: record filtering, seat-2 override, cache-bypass plumbing, full mocked re-judge with agreement + coverage)

## Step 6 — §D: DEFERRED, out of this build

§D deferred to v19/v20 by the operator's direction 2026-10-14 04:18; retrieval continues
separately, incorporation does not. No corpus merges, no rider merges, no embedding
staging, no registry refresh, no COLA incorporation in this build. Code-side items
that existed only to serve the merge (registry effective-date machinery,
conflict-scan effective-date handling) are likewise out of scope. The F16 code-only
conflict resolution (Step 2) stays: it is a correctness fix independent of any merge.

### §D paperwork only (no data changed)

Gap-log cluster triage for the v19/v20 incorporation pass (clusters named from the
project record; nothing was merged, refreshed, or rescraped by this build):
1. Estate-recovery / Miller-trust claim family — the recurring Miller-incident killer
   (estate-recovery-at-death sentences judged contradicted). The registry-side half is
   already fixed in this build (qit claim 3, Step 2); the corpus half waits for §D.
2. Medicare Hospice Benefits booklet — one of the 4 v11 rider documents that never
   merged (with the IRS ABLE page and two IRS Tax Tips).
3. Indiana Medicaid adult vision frequency for the operator's exact category — still unpinned.
4. Medicare–Medicaid seam topics: Extra Help, QMB, the D-SNP landscape.
5. IMPACT, burial assistance, cross-waiver contamination — named gap suspects.
6. 2027 COLA figures — retrieval runs separately (the 2026-10-14 rescrape stages its
   delta outside this build); incorporation is §D work.

Growth-loop candidate (read-only listing; NOT approved, nothing drafted into the
server): ~/workspace/fssa-qa/v15/review_queue.json holds ONE candidate,
status "pending-approval", type "mechanism", drafted by collect-mode from 1 gap —
an inheritance-gap skeleton (id "unverified_claim_documents_provide", all rule
fields TODO) sourced from the spouse-inheritance question (wife's ~$60,000
inheritance via her father's estate; two claims judged not_addressed). It stays
pending for the v19/v20 pass or the operator's direct call.

## Test totals (2026-10-14, all offline, zero paid/quota calls)

- tests/test_hard_stop.py — 13 tests (Step 1)
- tests/test_verifier.py — 53 tests (Steps 2–4: §A, §C, F12/F19 plumbing, seat plan)
- tests/test_pipeline.py — 10 tests (F17, F18, F19, R6, R7, §E records)
- tests/test_replay.py — 4 tests (§E harness)
- FULL SUITE: 80 tests, ALL PASS (`python3 -m unittest discover -s tests`)
- `python3 -m py_compile` clean for the server, the harness, and all tests
- Offline start smoke: the built server starts with no key files and empty corpora,
  serves /health (cap reads $2.00), and its startup key-state warm-up reports
  "no key configured" without any network call
- The v173 source copy is untouched (md5 8a343802392ba16cd8edcdb80a35676c, unchanged)

## AT THE BATTERY GATE

The build is complete and halted here, per the authorization. Deployment and the
§F battery are later, separately approved steps. Before the battery, the operator must
decide or verify:

1. **Seat-2 slug + price verification (required):** `JUDGE_SEAT2_MODEL` is
   "qwen/qwen3-235b-a22b-instruct-2507" and its price row ($0.30/$1.20 per 1M) and
   the Llama-4-Maverick failover row ($0.60/$2.40) are PLACEHOLDERS taken from the
   plan text — no live catalog read was allowed during the build. Verify both slugs
   exist on OpenRouter at those prices (one read-only catalog check) before any
   live ask; the reservation gate prices at these rows, so a wrong row misprices
   the hard stop.
2. **§C badge rule confirmation (D3):** the pass-2 badge rule as built — badge =
   minimum per-claim family coverage; no new badge name; the annotation line names
   the panel that sat. Confirm this is the labeling wanted before the battery
   grades against it.
3. **Per-ask ceiling default:** $0.50 (env-tunable) is a build-time choice; the
   plan fixed no number. Confirm or retune before the battery (a Miller-class ask
   at v17.3 prices ran ~$0.15–0.20, so $0.50 binds only true grinds).
4. **Listener bind (0.0.0.0) decision:** the server still binds 0.0.0.0 with no
   auth (unchanged from v17.3; phone access depends on the non-loopback bind via
   Tailscale). The external review raised it; the build did not change it. the operator's
   call at deployment time.
5. **§D status:** deferred to v19/v20 (above). The 2026-10-14 rescrape delta
   stages separately; nothing from it is incorporated into this build. Embedding
   generation for any future merge remains a PC-side step needing its own go.
6. **Deployment itself:** copying v18 to the PC, restarting the server, and any
   live ask or battery run each need their own explicit go. Nothing has been
   deployed, committed, or pushed by this build.

**Parent gate check (Muse, 2026-10-14 04:47, read-only catalog verification):**
item 1 is RESOLVED with one correction. The built slug
"qwen/qwen3-235b-a22b-instruct-2507" does not exist in the OpenRouter catalog;
the real non-thinking instruct slug is "qwen/qwen3-235b-a22b-2507"
($0.09 in / $0.55 out per 1M, 262K ctx; the -thinking-2507 sibling is separate).
JUDGE_SEAT2_MODEL corrected in place; py_compile re-verified. The price rows
stand as built: they sit ABOVE the real prices (seat 2 real $0.09/$0.55,
Maverick real $0.1875/$0.6525), which is the safe direction for the reservation
gate (its provable-maximum proof is preserved, marginally conservative).

## Badge fix per the operator's 2026-10-14 ruling

the operator's ruling (2026-10-14 04:53): a degraded panel gets a visibly different
badge name (this overrides the pass-2 "no new badge name" proposal as originally
built). Implemented in fssa_search_server.py (now 8,556 lines):

- **Panel state** (new `_panel_state(judges, served)`, computed per judging
  round in `_judge_round` and returned as `jr["panel_state"]`; the no-judge
  early return reports "degraded"): FULL only when all three seats served
  (Lite, judge_nim1, judge_nim2), each by its PRIMARY (non-failover) candidate,
  from three distinct model families, with no local seat on the panel and no
  seat missing. Anything less is DEGRADED: a failover-served seat, a
  missing/unavailable seat, a local fallback seat, or fewer than three
  distinct families among the seats that served (the Miller-incident
  Lite + DeepSeek + DeepSeek shape is degraded on both the family count and,
  in that instance, the missing seat). The state also rides each unit's
  judging dict into the ask record.
- **Rename at the top classes only** (in `_run_unit`): a shipped answer whose
  badge would be "verified" on a degraded panel ships "verified_reduced"; a
  capped Partial whose badge would be "partial" ships "partial_reduced".
  `verified_single` / `partial_single` keep their names on any panel (per-claim
  coverage precedence: already visibly different and more specific). The
  `_panel_annotation` line is unchanged.
- **Web flag stays orthogonal**: `_apply_web_badge` maps verified_reduced ->
  "verified_web_reduced" exactly as verified -> verified_web. Webapp badgeMap
  displays: "Verified (reduced panel)", "Partial (reduced panel)",
  "Verified web (reduced panel)".
- **Aggregation**: `_BADGE_RANK` extended (reduced names rank just below their
  full counterparts, above the matching _single class); an IDK-capped assembly
  grades a reduced weakest unit to "partial_reduced"; the mechanism-failure
  downgrade maps the reduced verified names to "partial_reduced". Old-name
  outcomes are unchanged.
- **F17 cache**: the verified-tier write gate now lists verified_reduced and
  verified_web_reduced alongside verified/verified_web (same tier, new names;
  behavior preserved). The partial tier, reduced or not, still never caches.

**Tests:** 18 new (15 in tests/test_verifier.py `TestReducedPanelBadge`:
round-level panel state for full / failover seat 1 / failover seat 2 /
missing seat / one-family-two-seats / local fallback; end-to-end unit badges
for full, failover, and missing-seat panels; the rename, the _single
precedence, capped partial_reduced vs partial, and the webapp label map;
3 in tests/test_pipeline.py `TestReducedBadgeCache`: reduced verified badges
cache and replay, partial_reduced never caches). Full suite:
**98/98 pass** (`python3 -m unittest discover -s tests`), py_compile clean,
all offline/mocked, zero paid or quota API calls. One pre-existing failure
was fixed along the way, unrelated to badges: `TestSeatPlan` still asserted
the plan-text seat-2 slug ("-instruct-2507"); aligned to the gate-corrected
"qwen/qwen3-235b-a22b-2507" recorded above. No deployment, no PC access,
no git operations.

## Cap-floor fix, post-deploy 2026-10-14

**Live finding:** minutes after the v18 deploy, a live check of the
deployed server key's /auth/key state showed `usage_daily` returned as a
scalar Decimal EQUAL to the key's total `usage` (4.094341471, identical to
9 decimal places): a cumulative figure, not a daily one.
`_or_reconciled_floor()` treated any scalar as today's provider-side
usage, so committed = max(ledger 0.686891, floor 4.094) >= the $2.00
ceiling permanently: `_or_cap_tripped()` was always true and
`_or_available()` fenced off every OpenRouter seat on the otherwise
healthy deployed server.

**Fix:** `_or_reconciled_floor()` now counts only a list of dated rows
(rows dated the ledger day or the UTC day, as before). A scalar, or any
non-list shape, returns 0.0: an undated figure cannot be attributed to
the ledger day, and the reservation ledger is the daily source of
truth. The docstring says exactly this and why. Nothing else about the
gate changed: ledger, reservations, broken-ledger fail-closed, and
list-row reconciliation all untouched.

**Tests:** 2 new in tests/test_hard_stop.py
(`test_scalar_usage_daily_does_not_gate`: scalar 4.094341471 with a
$0.10 ledger spend -> floor 0.0, committed 0.10, cap untripped;
`test_dated_row_above_spend_can_trip`: a ledger-day row of 2.50 raises
committed and trips). The pre-existing dated-list test needed no
change; no test asserted scalar counting. Full suite: **100/100 pass**
(`python3 -m unittest discover -s tests`), py_compile clean, all
offline/mocked, zero paid calls. No deployment, no PC access, no git:
this fix is VM-side only until a redeploy is directed.

## Jobpath fix + closeout verification (2026-10-14)
- Jobpath fix (commit 98d71b6): the async job path re-serialized request bodies at the original Content-Length, truncating them; dedupe handle is now bound before the try so errors are never masked. Deployed file MD5 6426c5a4aeee416ae8835ba85a95f19a (VM source identical, verified 2026-10-14 by certutil on the PC + md5sum on the VM copy).
- Push VERIFIED 2026-10-14 via the GitHub connector (not the job log): repo head = 98d71b6c7b44b2364701a2d4f8582d37bb7f96f4, chain 2ac94e8 (v18) -> 1bfef77 (cap-floor fix) -> 98d71b6 (jobpath fix). The push itself ran at 05:28 EDT during the PC network outage window (push_v18.log, exit 0).
- Outage root cause (2026-10-14): the PC's WiFi driver crashed ~05:30 EDT, taking the PC off the network; the server process was healthy throughout. the operator restored the driver at ~12:37; /health then verified green (docs 1,281 + 3,953, embeddings 72,722, hybrid on, spend_today $0.686891 carried, cap $2.00, cap_tripped false).
- Cleanup done 2026-10-14: one-off tasks PushV18Once and RestartV18Once deleted (verified gone); diag_task.ps1 and keycheck_v18.ps1 confirmed absent.
- NOT done: the live pest-control re-run through the job path. the operator's v19 instruction (2026-10-14 12:47) imposed no-API-calls / no-money-spent, and a live ask spends; deferred until he lifts the constraint or runs it in his own grading.
