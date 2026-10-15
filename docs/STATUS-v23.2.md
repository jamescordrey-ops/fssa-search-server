# v23.2 STATUS — BUG-11 fix release

Plan: ~/workspace/fssa-qa/v23.2/PLAN.md (approved by James
2026-10-15 15:12 EDT, full chain: flip + build + deploy).
Build detail: BUILD.md. Live-failure evidence: BUG-11 in
~/workspace/fssa-qa/v23.1/BUG_REPORT.md + EVIDENCE_ADDENDUM.md
(three live v23.1 asks: CSRA 0eb4d5dd94d4, pest 95f121e97b4b,
ABLE 87f5d6c5db39).

## Phase 0 — g0004 flip: DONE (record in v23.1 STATUS.md)
CURRENT g0003 -> g0004; probe battery 29/31 (2 fail-to-fail
vs g0003 baseline, proven); MMMNA docs #0, audit 14/14 + 5/5.

## Phase 2 — code-read sign-off (coordinator, 2026-10-15)
The built file (MD5 0aae415808903e320afa8af88d0f1d50,
10,703 lines) was read line-by-line on the four changed
paths before deploy. What each read proves:

1. **Deadline threading (F1).** Constants lines 552-564:
   budgets 150/285 unchanged, ASK_TIME_CAP_S 600,
   DRAFT_CALL_CAP 4, MIN_CALL_START_S 2.0. `_ask_clock_begin`
   (5171) anchors t0 + the 600s cap idempotently at ask
   start (called in do_POST, 9717); `_ask_clock_class`
   (5182) fixes the class deadline FROM t0; `_ask_deadline`
   (5194) = min(class, cap). In `_run_unit` (8407-8418),
   when the clock is anchored the unit deadline IS the ask
   deadline (min of class/ask/ctx candidates); the v23.1
   `now + budget` re-arm survives ONLY for unanchored
   offline callers. This is the exact mechanism whose
   absence caused BUG-11 defect 1 (per-invocation fresh
   windows at v23.1 lines 8164-8168).
2. **Rung loop + counter (F1+F2).** Ladder rung top
   (8548-8560): `_call_may_start(deadline)` gate marks the
   cap and breaks; then `_claim_draft_call()` (5236, locked
   counter on _AskContext) must succeed before
   `_draft_choice` runs — every rung of every unit draws
   from the ONE per-ask counter. Lead micro-repair
   (8650-8668) claims identically (through v23.1 it called
   `_generate` directly, uncounted — the 3-rungs-to-6-calls
   leak). do_POST rescue gate (10395-10410) and branch gate
   (10452-10466): re-runs do not start when the clock or
   the counter is spent; `_note_budget_cut("rescue"/
   "branch")` records the cut on the surviving unit.
3. **Rescue entry (F1).** Same do_POST gates as (2): the
   rescue is no longer a separate road — it is gated by
   `_call_may_start(class_deadline)` and
   `draft_calls >= DRAFT_CALL_CAP` before it can start,
   and its internal `_run_unit` re-entry derives the SAME
   anchored deadline per (1).
4. **Finalize / strip / guard (F3) + judge leg.**
   `_strip_uncited_asides` (7330) takes `flagged=None`;
   with the flagged set passed, candidates outside it are
   never stripped. Assembly guard (8920, second placement
   8990): fires only when the round passed (judge pass +
   figures/citations clean) AND material loss (assembled
   sentences < supported claims, OR assembled x4 < draft
   x3, Confidence: lines excluded); ships the checked
   draft verbatim with badge checked (judge_complete) else
   checked_det, and records `assembly_guard` on the unit.
   Judge backup (7755-7795): the spent check now precedes
   `_claim_backup()` — a spent round records
   `checks.judge.backup_skip = "budget spent"` plus an
   explicit error and NEVER burns the once-per-ask backup
   (the ABLE 87f5d6c5db39 mechanism, fixed at its line).

Suites (worker-run, coordinator-accepted): offline 60 +
v22 20 + v223 27 + v23 36 + v231 73 + v232 87, 0 FAIL.
Replay battery (tools/replay_bug11.py) 12/12: CSRA wall
127.5s / 4 draft calls / guard ships the whole draft,
badge checked; pest wall 149.1s, budget_cut at drafting,
3 calls, honest IDK; ABLE wall 113.1s, backup attempted
exactly once with the failure recorded, passing draft
ships checked_det verbatim 1590/1590 chars.
Flip reporting vs v23.1: 20/24 -> 20/24, zero flips.
