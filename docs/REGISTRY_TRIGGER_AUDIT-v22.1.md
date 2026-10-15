# v22.1 — Registry Trigger Audit

Built 2026-10-14 (EDT) on the VM, from the v22 tree
(server MD5 `1a0e9c6517471dabce6df23022cd9e1d`). No PC access,
no API calls, no live asks. Deploy is a later, separate step.

## Why

Two proven live failures, same disease: registry entries'
ANDed trigger groups were written in SOURCE-document
vocabulary, not asker vocabulary, so `_registry_matches`
returned `[]` and the verified figures never engaged.

- ABLE (v21 live, 2026-10-14): contribution figures sat in
  `able_onset_age` (onset words required). Fixed in v22 by
  the `able_contribution_limit` dated pair; NOT redone here.
- CSRA (v21 live, 2026-10-14 ~22:52 EDT): "If I go into a
  nursing home how much of our savings can my wife keep?" —
  retrieval was PERFECT (ILTCP spousal-impoverishment page
  pinned br=0, served sel=0, excerpt carries $32,532 /
  $162,660 / half) but the registry never matched: group 1
  required "community spouse" / csra / "spousal resource"
  and "my wife" matched nothing. A second, smaller bug rode
  along: the matcher is exact-token, so the word "resource"
  in a question never hit the group token "resources" —
  even "What is the community spouse resource allowance for
  2026?" failed in the v22 base.

## Method

For EVERY entry in `VERIFIED_FIGURES` (14, including the
divisor pair and the ABLE pair), one plain-language question
was written the way a layperson asks it: spouse = "my wife"
/ "my husband", savings = "savings", no source-document
jargon, and never copying the entry's own trigger phrases.
Each question was run through `_registry_matches` against
the unmodified v22 base (before) and against v22.1 (after).
The 14 questions are also locked in as checks in
`tools/offline_checks.py` (the "v22.1 audit" block asserts
each entry hits, and hits ALONE).

## Results

| Entry | Plain question | Before | After |
|---|---|---|---|
| backpay_exclusion | Social Security owed me money from earlier months and just paid it all at once. How long before that money counts against me? | miss | hit |
| able_onset_age | What is the oldest age my disability could have started for me to open an ABLE account? | miss | hit |
| snap_resource_limit_in | How much can I have in the bank and still get EBT in Indiana? | miss | hit |
| csra | If I go into a nursing home how much of our savings can my wife keep? | miss | hit |
| mmmna | If my husband is in a nursing home, how much of his monthly check can I keep to live on? | miss | hit |
| home_equity_limit | If our house is worth a lot, can I still get Medicaid help to pay for a nursing home? | miss | hit |
| asset_limit_2000 | How much money can I have in the bank and still get Medicaid? | miss | hit |
| waiver_income_cap | I get help at home through Medicaid. How much money can I make each month and still qualify? | miss | hit |
| ssa_2027_figures | How much will my Social Security check go up next year? | hit | hit |
| difficulty_of_care | My wife gets paid to take care of me at home. Does that money count if she applies for Healthy Indiana? | miss | hit |
| transfer_penalty_divisor_current | If I give property to my kids now and apply for Medicaid after July 2026, how long will I be penalized? | miss | hit |
| transfer_penalty_divisor_legacy | If I gave property to my kids and applied for Medicaid in March 2026, how long would I be penalized? | miss | hit |
| able_contribution_limit | How much money can I contribute to an ABLE account each year? | hit | hit |
| able_contribution_limit_legacy | How much money could I contribute to an ABLE account in 2025? | hit | hit |

**Before: 3/14 hit. After: 14/14 hit.** The stemming probe
("What is the community spouse resource allowance for
2026?") also went miss → hit (`csra`).

## What widened (per entry)

Added plain phrasing / tokens only. No figures, dates,
topics, or statements changed; the AND structure was not
loosened anywhere.

- **backpay_exclusion**: + phrases "owed me money", "owed
  me money from earlier months", "money from earlier
  months", "social security owed me", "paid it all at once".
- **able_onset_age** (group 2): + "oldest age", "disability
  could have started", "age my disability could have
  started", "disability started", "when my disability
  started", "disability began".
- **snap_resource_limit_in**: group 1 + token `ebt`,
  phrases "grocery help", "help buying groceries", "food
  help", "help with groceries"; group 2 + tokens `bank`,
  `money`, `savings`, `saving`.
- **csra**: group 1 + "my wife", "my husband", "my spouse",
  "wife can keep", "husband can keep", "spouse can keep",
  "spouse resource", "spouse resources"; group 2 +
  singular tokens `resource`, `asset`, `saving`
  (singular/plural pairs — the stemming fix).
- **mmmna**: group 1 + "my wife", "my husband", "my
  spouse", "husband's check", "wife's check", "spouse's
  check", "husband's income", "wife's income", "spouse's
  income"; group 2 + tokens `check`, `checks`, phrases
  "monthly check", "his monthly check", "her monthly
  check", "keep to live on".
- **home_equity_limit**: + "house is worth", "my house is
  worth", "our house is worth", "home is worth", "how much
  my house is worth", "value of my house", "value of our
  house", "my home's value", "worth of my house".
- **asset_limit_2000**: + Medicaid-specific bank phrases
  ("have in the bank and still get medicaid", "money in
  the bank and still get medicaid", "in the bank and still
  qualify for medicaid", "bank account and still get
  medicaid", "how much savings can i have and still get
  medicaid", "money can i have in the bank and still get
  medicaid"). Kept Medicaid-specific so the SNAP bank
  question cannot cross-match.
- **waiver_income_cap**: group 1 + "help at home", "care
  at home", "home care", "in-home care", "in-home help",
  "help in my home", "care in my home"; group 2 + tokens
  `earn`, `earns`, `make`, `makes`, `earning`, `earnings`,
  phrases "how much money can i make", "money can i make
  each month", "make each month and still qualify", "earn
  each month and still qualify".
- **difficulty_of_care** (group 1): + "gets paid to take
  care", "paid to take care", "take care of me at home",
  "care for me at home", "paid to care for", "wife gets
  paid", "husband gets paid", "spouse gets paid".
- **transfer_penalty_divisor pair** (shared
  `_DIVISOR_GROUPS`): + "give property to my kids", "gave
  property to my kids", "give away property", "gave away
  property", "giving away property", "property to my kids",
  "how long will i be penalized", "how long would i be
  penalized", "penalty for giving away property". The
  family/date machinery still selects exactly one of the
  pair per question date.
- **ssa_2027_figures, able_contribution_limit pair**:
  unchanged (already hit; the ABLE pair is the v22 fix).

## False-positive check

Full matrix, not just the cluster: after the widening,
EVERY audit question above matches exactly its own entry
and no other (`matched == [target]` for all 14, asserted
in the offline checks). Spot-checks on the standing case
questions:

- Case 5 (CSRA formula, $190,000) → `csra` only.
- Case 13 (CSRA, plain) → `csra` only.
- Case 8 (MMMNA, "spouse of a Medicaid applicant") →
  `mmmna` only. Case 14 (MMMNA maximum) → `mmmna` only.
- Inheritance scenario → no registry match (unchanged).
- Miller scenario → no registry match (unchanged).
- Backpay case → `backpay_exclusion` (unchanged).
- Case 20 (Medicaid asset limit) → `asset_limit_2000`
  only; SNAP does not cross-match it.
- Case 19 (SNAP resource limit) → `snap_resource_limit_in`
  AND `asset_limit_2000`. This overlap is PRE-EXISTING in
  the v22 base (the asset entry's group always contained
  the bare phrase "resource limit"); v22.1 did not
  introduce it and did not widen it further.

## Secondary findings

### (a) NIM draft-leg deadlines — ONE bypass found and FIXED

The main draft paths are correctly threaded in v22: the
`_generate` bench / Groq / xAI / OpenRouter legs all compute
`_leg_timeout(...)` and pass `deadline=` into `nim_ask` /
`_nim_text`, and the judge seats thread theirs (WS4a). A
~60.12s NIM bench timeout on the CSRA ask is, by itself,
the configured `NIM_DRAFTER_TIMEOUT` (60s) first attempt
with the retry correctly suppressed at the deadline — not
a bypass.

The exception: the divergence oracle's B draft (engine
`"nimx"` in `_generate`) called `nim_ask` with NO deadline
at all, so `_nim_text`'s internal retry ran at the full
60s timeout again after a first read timeout — the exact
WS4a bug class, surviving on this one path. FIXED: the
branch now computes `_leg_timeout(NIM_DRAFTER_TIMEOUT)`,
sheds the leg when the unit budget is spent, and passes
`timeout=_lt, deadline=now+_lt` to `nim_ask`. Proven
offline in `tools/offline_checks.py` (capturing stub: the
timeout clamps to a 5s unit budget and a deadline is
passed; a spent budget starts no call).

Noted, NOT changed (out of the brief's scope): `_utility_
text`'s NIM call and `_primary_judgment`'s orlite call run
without a unit deadline under their own fixed timeouts
(30–60s; the v21 STATUS already recorded `_utility_text`
as deliberately unthreaded), and the local fallback's
`ollama_ask` keeps its own 300s transport timeout.

### (b) Local-fallback memo draft — shape guard ADDED

What the local fallback receives: the same prompt shape as
every drafter — system = `SYSTEM_PROMPT` + the unit's
instructions (on redraft rungs those instructions include
the CITATION_CONTRACT and the CORRECTIONS REQUIRED
objections block), user = question + numbered excerpts.
The previous draft itself is never shown. A weak local
model can therefore answer the memo instead of the
question; through v22 nothing checked the draft's shape
before it became `draft_text` and burned judging budget.

FIXED with a narrow shape guard (`_is_memo_shaped_draft`,
applied in `_run_unit` immediately after generation): a
draft is rejected as a generation failure — the unit IDKs
on the generation gate with the reason recorded — when
its first line is a corrections heading, or when it
carries no citation marker and at least two meta markers
("correction", "citation(s)", "previous draft", "the
draft", "uncited", ...). An uncited but genuine answer is
NOT rejected (the citation gate owns that failure); both
directions are asserted in `tools/offline_checks.py`.

## Observation (out of scope, flagged)

The v14 MECHANISMS registry shares the trigger-group
format and some group text with the figures registry
(e.g. the `csra` and `backpay_nine_months` mechanisms
carry the same source-vocabulary groups). Those twins
were deliberately NOT widened here: the brief scopes this
audit to the figures registry, and mechanism matching
gates answer completeness on already-matched questions —
a different failure surface that deserves its own audit
before anyone widens it.

## Cases added

`tools/retrieval_cases.jsonl`: the operator's verbatim CSRA
question added as a `registry`-class case (24 cases total;
the ABLE registry case from v22 confirmed present,
not duplicated).

## Verification

- `python3 -m py_compile fssa_search_server.py` — OK.
- `tools/offline_checks.py` — **56/56 PASS** (36 base
  checks + 15 v22.1 audit checks + 2 nimx checks + 3
  shape-guard checks), including the ABLE pair's three
  checks and the WS2 registry cases.
- `tools/offline_checks_v22.py` — **20/20 PASS** (incl.
  v2 channels, registry lint, legacy path intact).
- Server file: **12,183 lines, MD5
  `d36959344a3c2c450d41ad455953c7dc`**.
- The v22 tree was not modified (its server file still
  MD5 `1a0e9c6517471dabce6df23022cd9e1d`).
