# v22.2 — Mechanisms Registry Trigger Audit

Built 2026-10-14 (EDT) on the VM, from the v22.1 tree
(server MD5 `d36959344a3c2c450d41ad455953c7dc`). No PC
access, no API calls, no live asks. Deploy is a later,
separate step.

## Why

The v22.1 audit (REGISTRY_TRIGGER_AUDIT.md) fixed the
VERIFIED_FIGURES registry (3/14 -> 14/14 plain-language
hits) and deliberately left the v14 MECHANISMS registry
out of scope, flagging that its twins (csra, backpay)
carry the same source-vocabulary trigger groups. This
audit covers those twins, at the operator's direction ("finish
up all the fixes").

## How the MECHANISMS registry is used at runtime

Read from the server file before auditing (all paths
verified live, no dead entries):

- Matching: `_mechanism_matches(question)` runs the
  shared `_groups_hit` matcher over `MECHANISMS` — the
  SAME matcher and group format as the figures registry,
  so the layers cannot drift in mechanics, only in
  vocabulary. The ask pipeline matches once on the full
  question (`mechanisms_all`, in the ask path) and again
  per part for bundled multi-part questions.
- Retrieval: `_sufficiency_missing` + `_sufficiency_retry`
  — if a matched mechanism's `sufficiency_terms` appear in
  NO served hit, targeted searches with the entry's
  `retry_query` run and governing chunks are merged ahead
  of the set; a still-missing mechanism is recorded with
  the entry's `missing` text.
- Drafting: `_mechanism_block(...)` injects a REQUIRED
  MECHANISMS block into the drafter prompt (the entry's
  `prompt` text plus any `canonical_claims` verbatim).
- Answer gate: `_mech_required_failures` — every
  `required` element of every matched mechanism must be
  present in the answer (the CSRA element is the
  server-computed figure, required only when the question
  supplied an amount); any `forbidden` substitute fails
  the answer. The period checker additionally re-matches
  mechanisms and, for `backpay_nine_months`, fails an
  answer that ASSERTS a confusable six-month period as
  the exclusion rule.

So a match is consequential in both directions: it arms
retrieval help, drafting instructions, and completeness
enforcement. A MISS is the failure surface this audit
targets: the question gets none of the three, and an
answer can omit the governing mechanism entirely and
still pass the gate. (The inverse risk — an over-broad
entry forcing its elements onto questions where the
mechanism is not governing — is why the additions below
stay narrow asker shapes, and why every audit question
is asserted to hit its own entry ALONE.)

## Method

Same as v22.1: for EVERY entry in `MECHANISMS` (5), one
plain-language question written the way a layperson asks
it (spouse = "my wife", savings = "savings", no source
jargon, never copying the entry's own trigger phrases),
run through `_mechanism_matches` against the unmodified
v22.1 base (before) and against v22.2 (after). The five
questions plus four guard checks are locked in as checks
in `tools/offline_checks.py` (the "v22.2 mech audit"
block: each entry hits alone; the CSRA stemming probe;
the Miller scenario still hits `qit_over_cap` alone; the
inheritance scenario still matches no mechanism).

## Results

| Entry | Plain question | Before | After |
|---|---|---|---|
| qit_over_cap | My dad's pension and Social Security add up to more than Medicaid allows each month, so he doesn't qualify. Is there anything that can be done so he can still get help paying for his care? | miss | hit |
| backpay_nine_months | Social Security owed me money from earlier months and just paid it all at once. How long before that money counts against me? | miss | hit |
| snap_lump_sum | I just got one big check for months I was owed. Will that count as income for my food stamps? | miss | hit |
| csra_half_clamp | If I go into a nursing home how much of our savings can my wife keep? | miss | hit |
| difficulty_of_care_exclusion | My wife gets paid to take care of me at home. Does that money count if she applies for Healthy Indiana? | miss | hit |

**Before: 0/5 hit. After: 5/5 hit, each alone.** Worse
than the figures registry's 3/14: NO mechanism engaged
on a plainly-phrased question. Two of the five audit
questions are the same strings the v22.1 figures audit
used (backpay, difficulty-of-care) — the figures twins
now engage on them while the mechanism twins silently
did not, which is the drift the plan warned about. A
third (CSRA) is the operator's verbatim live-test question:
on v21 the figures side of that ask was only half the
story; the `csra_half_clamp` mechanism (half-share +
server-computed figure requirements) could never have
engaged on it either.

Guard checks (unchanged by the widening):

- Miller scenario (the operator's H1, verbatim) -> `qit_over_cap`
  alone, before and after (it happens to contain the
  source phrasing "over the Medicaid income limit"; the
  audit question above is the plainer shape that missed).
- Inheritance scenario (the operator, verbatim) -> no mechanism,
  before and after. (Its "my wife is my paid caregiver"
  hits difficulty group 1 before and after, but group 2
  still requires a HIP/MAGI signal the question lacks —
  the AND structure is what keeps it clean.)
- CSRA formal phrasing ("What is the community spouse
  resource allowance for 2026?") -> miss before,
  `csra_half_clamp` after: group 2's exact-token set had
  "resources" but never the singular "resource" — the
  same stemming bug class v22.1 fixed in the figures
  entry.
- SFC plain question ("What is structured family
  caregiving and who qualifies?") -> no mechanism, before
  and after.

## What widened (per entry)

Added plain phrasing / tokens only. No figures, dates,
required elements, prompts, structure, or AND logic
changed. Where a figures-registry twin exists, the
additions mirror v22.1's so the layers stay consistent.

- **qit_over_cap** (group 1): + "more than medicaid
  allows", "make too much", "makes too much", "making
  too much", "earn too much", "earns too much", "too much
  to qualify", "puts him over", "puts her over", "puts me
  over", "because of his income", "because of her
  income", "because of my income". Group 2 (Medicaid /
  waiver / nursing home) still scopes it.
- **backpay_nine_months**: + "owed me money", "owed me
  money from earlier months", "money from earlier
  months", "social security owed me", "paid it all at
  once" — identical to the v22.1 figures twin's additions.
- **snap_lump_sum** (group 2): + "one big check", "big
  check", "one big payment", "big payment", "single
  payment", "one-time payment", "one time payment",
  "money all at once", "paid in one payment", "lump-sum
  payment", "lump payment". Group 1 (SNAP / food stamps /
  food assistance) still scopes it; the SNAP audit
  question was checked against the widened backpay group
  and does NOT cross-match it ("owed" alone is not a
  backpay phrase).
- **csra_half_clamp**: group 1 + "my wife", "my
  husband", "my spouse", "wife can keep", "husband can
  keep", "spouse can keep", "spouse resource", "spouse
  resources"; group 2 + singular tokens "resource",
  "asset", "saving" (singular/plural pairs). Mirrors the
  v22.1 figures `csra` widening, including the stemming
  fix.
- **difficulty_of_care_exclusion** (group 1): + "gets
  paid to take care", "paid to take care", "take care of
  me at home", "care for me at home", "paid to care
  for", "wife gets paid", "husband gets paid", "spouse
  gets paid" — identical to the v22.1 figures twin's
  additions.

## False-positive check

Every audit question matches exactly its own entry and
no other mechanism (asserted in the offline checks).
Cross-cluster risks checked by construction: the CSRA
question reaches qit group 2 ("nursing home") but none
of qit's widened group-1 phrases; the difficulty
question reaches csra group 1 ("my wife") but none of
csra's group-2 tokens ("count" is not the token
"countable"; no savings/assets/resource words); the
SNAP question reaches no other entry's first group.
The widening touches only `MECHANISMS`; `_registry_
matches` results for all 14 v22.1 audit questions are
unchanged (the v22.1 checks in the suite still pass
verbatim).

## Verification

- `python3 -m py_compile fssa_search_server.py` — OK.
- `tools/offline_checks.py` — 64/64 PASS (the 56 v22.1
  checks + 8 new v22.2 mech-audit checks).
- `tools/offline_checks_v22.py` — 20/20 PASS.
