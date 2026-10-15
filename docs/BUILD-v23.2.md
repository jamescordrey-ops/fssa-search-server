# FSSA server v23.2 — BUILD (BUG-11 fix)

Built file: `~/workspace/fssa-qa/v23.2/fssa_search_server.py`
- **MD5 0aae415808903e320afa8af88d0f1d50, 10,703 lines**
- Base: the deployed v23.1 file (MD5 c59c08401eb7d2e9c99f8cef234a4e90, 10,159 lines), copied and MD5-verified before editing.
- Built and tested entirely on the VM, $0 spent: stub providers / recorded data only. No live /ask, no restarts, no deploy (deploy is not this build's job). The only PC access was the authorized read-only fetch of `ask_records.jsonl` (saved at `records/ask_records_pc.jsonl`; the three failure records are extracted to `tools/fixtures/bug11_records.json`).

Unchanged per plan: Grok 4.7 drafting (Groq GPT-OSS backup, local last), Lite judge one pass + DeepSeek V3.1 once-per-ask backup, deterministic checks, badge set (checked / checked_det / partial), retrieval, budgets (150 simple / 285 hard / 600 cap), stepper/PAGE shape, registry/mechanisms.

---

## F1 — ONE CLOCK (BUG-11 defect 1)

### What was actually wrong in v23.1 (the mechanism)

WS-A's deadline existed but never bounded the ladder → IDK → rescue path, for three compounding reasons:

1. **Per-invocation deadline re-arming** in `_run_unit` (v23.1 lines 8164–8168): `deadline = min(ask_cap_600, time.monotonic() + budget_s)` was computed at EVERY `_run_unit` entry. The post-IDK rescue re-run (`do_POST` calling `_run_unit` on the rescue context) and the branch re-run each inherited only the 600s ask cap and armed a FRESH 150/285s class window. The class budget therefore bounded a single unit invocation, never the ask. CSRA 0eb4d5dd94d4: unit 1 drafted 6 times, the rescue unit 5 more, wall 416.875s, `budget_cut` False.
2. **Uncounted draft calls**: `_repair_lead` called `_generate` directly per rung (a full Grok generation per repair), invisible to any counter, so the ladder's true call count was unbounded even inside one unit.
3. **Unclamped transports**: `ollama_ask` hardcoded `timeout=300` with no deadline parameter (the local fallback leg), and `gemini_ask` called `_gemini_call` with NO deadline — in-flight calls could not be bounded by any deadline the stages consulted.

### The fix (all in `fssa_search_server.py`)

- Constants (lines 552–564): `ASK_BUDGET_SIMPLE_S = 150`, `ASK_BUDGET_HARD_S = 285` (the class budgets, unchanged values, now named), `DRAFT_CALL_CAP = 4`, `MIN_CALL_START_S = 2.0` (a new provider call may not start with less than this much budget left).
- The ask clock: `_ask_clock_begin(t0)` (line 5171) anchors the ask once — `ask_deadline = t0 + 600` — first anchor wins; `_ask_clock_class(hard)` (5182) sets `class_deadline = t0 + 150/285` from the SAME anchor; `_ask_deadline()` (5194) is the effective deadline, the min of the two. `do_POST` anchors the clock at ask start (line 9717, right after `deadline = t_start + ASK_TIME_CAP_S`) and sets the class deadline when the route is known (line 10182).
- `_run_unit` (line 8350) derives its deadline from the anchored clock (lines ~8390–8415): min of the ask deadline, the class deadline, and any caller deadline. The old `now + budget_s` re-arm survives ONLY as the fallback for standalone callers with no anchored clock (offline suites). Every stage consults the one deadline: the rung loop start gate and claim gate (line 8555), the lead-repair gate (line 8659), `_check_round`'s `_spent()` (now measured against the effective deadline with the `MIN_CALL_START_S` floor), and finalization.
- Rescue and branch re-runs are gated in `do_POST`: if the class budget cannot cover a new call, the re-run is skipped, `_note_budget_cut("rescue")` / `_note_budget_cut("branch")` (lines 10401 / 10459) records it, and the unit carries `budget_cut` / `cut_phase` / a `rescue` dict with the skip reason. `_branch_attempt` also returns early inside 60s of the class deadline.
- Transports clamped: `gemini_ask(..., deadline=None)` (line 4843) threads the deadline into `_gemini_call`; `ollama_ask(..., timeout=300)` (line 4876); `_generate` (line 8027) passes the unit deadline to the Gemini leg and clamps the local leg via `_leg_timeout(300)`, raising budget-spent below `MIN_CALL_START_S` on both the primary and fallback local legs. A call in flight at the cap ends AT the cap because its transport timeout is the remaining budget; the ask then finalizes with its best state. (No thread-kill: Python cannot kill threads; the 600+60 job backstop is unchanged.)
- Truthful recording: `_note_budget_cut(phase)` (line 5222, first cut wins) feeds the ask record's `budget_cut` / `cut_phase`; skipped judge legs in `_check_round` note the cut at "extracting".

## F2 — DRAFT-CALL CAP

- **Counter location**: `_AskContext.draft_calls` (slot + init on the per-ask context, class at line 5285), claimed through `_claim_draft_call()` (line 5236), which increments under the context lock and returns False at the cap, setting `draft_capped`. One counter per ask, shared by every unit of the ask because all units run under the same thread-local `_AskContext`.
- Every drafting call claims BEFORE it starts: ladder rungs in `_run_unit` (line 8555) and lead repairs in `_repair_lead`'s call site (line 8659) — repairs count, closing the v23.1 hole. All engines count (the claim sits above `_try_generate`, engine-agnostic).
- The rescue and branch re-runs check the counter in `do_POST` and skip when it is exhausted. At the cap, finalization proceeds with the best judged draft on hand (assembly / F3 guard below).
- Recorded: `draft_calls` and `draft_capped` on the unit record, the ask record (top level), and the per-unit serialization.

## F3 — SHIP WHAT PASSED

- **Strip only flagged sentences**: `_strip_uncited_asides` (line 7330) gained a `flagged=None` parameter. The `_run_unit` strip path computes the flagged set for the draft being stripped — the citation parser's uncited load-bearing sentences, sentences mapping to a claim whose final is not "supported", and sentences carrying an ungrounded figure (via `_claim_money`) — and passes it in. A candidate outside the flagged set is NEVER stripped. With `flagged=None` (other callers) behavior is unchanged.
- **Assembly guard** (in `_run_unit`'s capped-partial disposition, line ~8920): "the draft that passed checks" = `jr["pass"]` is true AND `jr["checks"].figures.failed` is empty AND `jr["checks"].citations.failed` is empty. **Material loss** (the concrete definition) = the assembled text carries fewer sentences than the passing round's supported substantive claim count, OR fewer than three quarters of the passing draft's sentences (Confidence: lines excluded on both sides; the round passed in full, so the draft's sentences are supported content — the ABLE ask's 5-of-10 shape sits exactly at this line, the CSRA ask's 1-of-24 far below it). On material loss the checked draft ships VERBATIM via `_check_arithmetic`, badge `checked` if `jr["judge_complete"]` else `checked_det`, and the firing is logged on the unit record as `assembly_guard = {fired, supported_claims, draft_sentences, assembled_sentences, action}` (serialized into the ask record).
- **Second placement** (line ~8990): the same passed-checks test where the assembly returned NOTHING and the unit would have IDK'd a passing draft — ships the checked draft verbatim instead of the IDK, same badge rule, `assembled_sentences: 0` in the guard record. This is the ABLE ask's fallthrough.
- Diagnosis evidence (offline, recorded CSRA draft through the v23.1 module): `_assemble_capped_partial` returned ONE sentence of 24 — its allow-list requires exact normalized-key identity between draft sentences and the judge's paraphrased claim texts. Contributing parser facts observed in the same diagnostic: the sentence splitter detaches a trailing "[1]" after "(2026)." so sentences read uncited, and `_arithmetic_errors` misreads dates like "7/1/2026" as division. Those deterministic-check quirks are unchanged (out of F3's scope); the guard makes them unable to shrink a passing answer into a fragment.

### Folded in with F3 (per the evidence addendum): the judge-leg failure path

ABLE ask 87f5d6c5db39 (306.141s, partial): the surviving unit's check round started already spent (drafting had consumed 226.109s under the re-armed clock), so Lite never ran — yet the record says `judge_errors: ['lite judge: not run']`, `backup_used: false`, and nothing records why the once-per-ask V3.1 backup never engaged. Mechanism found in `_check_round` stage 1 (v23.1): the engagement condition was `_claim_backup() and not _spent()` — Python evaluates the CLAIM first, so a spent round **burned the ask's single backup use without attempting it**, and no error or skip field recorded the non-engagement. The pest ask's backup DID engage because its rounds started unspent.

Fix (a distinct minimal fix inside the checking path, `_check_round` lines ~7740–7790, plus `_empty_checks_block` line 7536):
- The spent check now runs BEFORE the claim: a spent round never claims the backup.
- A spent round records `checks.judge.backup_skip = "budget spent"` and the error `backup judge: not attempted (budget spent)`, and notes the budget cut.
- The placeholder error "not run" is renamed to the truthful `judge leg budget spent (round started past the deadline)` in both the Lite and local branches.
- Badge semantics when neither judge serves: deterministic finals can still pass the round (mode "det"), and a fully-passing draft then ships **checked_det** — through the shipped exit when the ladder gates pass, or through the F3 guard when they do not. That is the code's intended badge for this state (the shipped exit's own rule: `checked` if `judge_complete` else `checked_det`).

## F4 — RE-TRIAGE (both bug reports, item by item)

The v23 report (BUG-1..BUG-10) and the v23.1 report (BUG-1..BUG-11, same numbering for 1–10) were both walked. Only items touching budgeting / drafting / rescue / finalization were folded in.

| Item | Disposition | Notes |
|---|---|---|
| BUG-1 (phase_ms drops "retrieving") | already-fixed-in-v23.1 | The phase-timing fix shipped in v23.1; the v232 suite's ASK checks re-prove it (all five phases present, summing to ≥99% of wall). Phase timings are also what the budget record reads. |
| BUG-2 (judge panel machinery) | already-fixed-in-v23.1 | Panel deleted in v23.1; absence greps pass in the v232 suite (ABS section). |
| BUG-3 (stepper/panel surface) | already-fixed-in-v23.1 | Stepper carries the single checking line; PAGE checks pass. |
| BUG-4 (shared Groq guard) | out-of-scope-per-plan / deferred-with-reason | Concerns the Groq seat guard, not budgeting/drafting/rescue/finalization; the plan's UNCHANGED list freezes the provider roster. |
| BUG-5(a) (phase completeness) | already-fixed-in-v23.1 | Same proof as BUG-1. |
| BUG-5(b) (registry vocabulary floor) | already-fixed-in-v23.1 | WS-E vocab floor is wired into the suite (WSE section: 14/14 figures, 5/5 mechanisms on the g0004 staging store) and passes against the v23.2 file. |
| BUG-5(c) (refutation machinery) | deferred-with-reason | The refutation machinery was deleted in v23.1 by design; nothing to fold in. |
| BUG-6 | already-fixed-in-v23.1 | Fixed in the v23.1 build; its suite checks pass unchanged against v23.2. |
| BUG-7 | already-fixed-in-v23.1 | Same. |
| BUG-8 (startup/retrieval) | out-of-scope-per-plan | Startup/retrieval path, not the ask budgeting path; retrieval is on the plan's UNCHANGED list and the flip run below shows zero retrieval movement. |
| BUG-9 (panel-era surface) | already-fixed-in-v23.1 | Panel deleted in v23.1; absence greps pass. |
| BUG-10 (WS-A budget enforcement) | **folded-in** | v23.1's WS-A was partial: deadlines existed per invocation but re-armed (see F1). v23.2 completes it with the one anchored ask clock. |
| BUG-11 (runaway asks: CSRA 416.9s / pest 349.1s) | **folded-in** | The release driver. F1 (one clock) + F2 (draft cap) + F3 (ship what passed). |
| Judge-leg failure path (addendum: ABLE 87f5d6c5db39) | **folded-in** | Backup claim burned by evaluation order + silent non-engagement; fixed in `_check_round` (see F3 section above). ABLE's 226.109s of drafting on a registry question is the F1/F2 failure shape and is bounded by the same fixes. |

---

## Phase 2 gates — results

### Standing offline suites (all run against the built v23.2 file)

| Suite | Result |
|---|---|
| tools/offline_checks.py | 60 PASS, 0 FAIL — "all offline checks passed" |
| tools/offline_checks_v22.py | 20 PASS, 0 FAIL — "ALL PASS" |
| tools/offline_checks_v223.py | 27 PASS, 0 FAIL — "ALL PASS" |
| tools/offline_checks_v23.py | 36 PASS, 0 FAIL — "all v23 offline checks passed" |
| tools/offline_checks_v231.py | 73 PASS, 0 FAIL — "all v23.1 offline checks passed" (incl. WSE vocab floor 14/14 + 5/5) |
| tools/offline_checks_v232.py (new; the v231 suite + the V232 section) | 87 PASS, 0 FAIL — "all v23.2 offline checks passed" |

The V232 section proves, behaviorally: the constants (150/285/600, cap 4); the class deadline anchored at ask start (t0+285 exactly); the shared counter (4 claims pass, the 5th is refused); the no-re-arm regression (a unit entered with a spent class deadline and a 600s caller deadline drafts ZERO times, records `budget_cut`, returns IDK in <0.01s — v23.1 re-armed and drafted); the cap counting lead repairs (exactly 4 `_generate` calls, `draft_capped`); the assembly guard on the recorded CSRA shape (guard fired: 8 supported claims, 23 draft sentences, 1 assembled sentence → verbatim ship, badge checked); strip-only-flagged (the unflagged eligible sentence survives); and the ABLE judge path (a spent round starts no judge leg, does NOT burn the backup claim, records `backup_skip`; a live round with Lite down attempts the backup exactly once, mode `judge_backup`).

### NEW replay battery — tools/replay_bug11.py (permanent regression)

All three recorded failure shapes replayed through the FIXED pipeline with stub providers and recorded per-call latencies on a simulated clock. **12/12 assertions pass.**

| Case | (a) wall vs cap | (b) binding constraint recorded | (c) draft calls (live) | (d) shipped text |
|---|---|---|---|---|
| CSRA 0eb4d5dd94d4 (hard, 285s) | **127.5s** (live 416.9s); 0 calls started after the deadline | Draft cap engaged + recorded (`draft_capped`); rescue skipped on the cap | **4** (live 11) | Whole judged draft ships verbatim via the assembly guard, badge **checked** (live: 1-sentence partial fragment) |
| Pest 95f121e97b4b (simple, 150s) | **149.1s** (live 349.1s); 0 late starts | **`budget_cut` True, `cut_phase` "drafting"** — the rescue unit's first rung may not start | **3** (live ~7+) | Honest IDK at the cap; no fragment (live: partial after 349s) |
| ABLE 87f5d6c5db39 (simple, 150s) | **113.1s** (live 306.1s) | Backup attempted **exactly once** and its failure recorded (`backup judge: …` in the round errors; claim marked used) | **3** (live 6) | Passing draft ships **checked_det verbatim**, 1590/1590 chars (live: partial, judge never served, backup never attempted) |

### Flip reporting vs v23.1 (24-case retrieval set, rewrite+rerank off, n=8)

`tools/flip_phase_v232.json` vs `~/workspace/fssa-qa/v23.1/tools/flip_phase_v231.json`:
- v23.1: **20/24** → v23.2: **20/24**
- pass→pass 20, fail→fail 4, fail→pass 0, **pass→fail 0** (no regressions; retrieval untouched, as the plan requires)
- Failing in both (unchanged): the SGA 2027/TWP case, the spouse-inheritance scenario, the Miller-trust father scenario, "What was the maximum able account contribution for 2025?"

## Files in this tree

- `fssa_search_server.py` — the built v23.2 server (MD5 above).
- `tools/offline_checks_v232.py` — the v23.1 suite adapted to this tree + the V232 BUG-11 section.
- `tools/replay_bug11.py` + `tools/fixtures/bug11_records.json` — the replay battery and its recorded fixtures.
- `tools/flip_phase_v232.json` — the flip run.
- `records/ask_records_pc.jsonl` — the PC ask records as fetched (read-only) for diagnosis.
- `PROGRESS.md` — the running build log.
