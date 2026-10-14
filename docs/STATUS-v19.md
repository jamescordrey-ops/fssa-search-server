# FSSA server v19 — STATUS (built and deployed 2026-10-14)

the operator's order (12:47): run v19 immediately after v18, PC work
authorized for this run. Constraints for the whole run: NO tests,
NO api calls by Muse, NO money spent. Verification was therefore
py_compile + AST/data review, live /health, and server-local
/search probes only. The offline pytest suite was NOT run and the
live pest-control re-run was SKIPPED (a live ask spends money);
both remain owed when spend/test rules allow.

## Shipped

GitHub: **3c437d09dfd1e00167e8904c0127f15b53128cab** ("v19:
registry corrections ... + speed build ..."), verified via the
GitHub connector (list_commits), chain 98d71b6 -> 3c437d0.
Live server file MD5 c0b17f305051daf579071116f7975944 (certutil
on PC = md5sum of the VM source). Rollback on PC:
fssa_search_server_v18.py.bak, corpus_pre_v19.json.bak,
corpus_federal_pre_v19.json.bak, embeddings_pre_v19.npz.bak,
embeddings_pre_v19.jsonl.bak, plus embeddings_v19_undeduped.*
(see the embeddings note below).

### §D corpus incorporation (the deferred v18 item)
Corpora on VM (source of truth) and PC now: **IN 1,291 /
federal 3,955** (was 1,281 / 3,953). In: IHCP bulletins
BT2026168-BT2026171; IHCPPM sections 2615.75.15 (QIT body text:
monthly funding cadence, revocable-or-irrevocable), 3006.00.00
(transfer divisor $8,027), 3010.20.10 (MMMNA standards +
excess-shelter formula), 3005.10.05 (Indiana home equity
$752,000); IHCP Vision module (adult FFS: exams 1 per 2 years,
eyeglasses 1 pair per 5 years) and Dental module; SSA 2027
COLA press release + fact sheet; USDA FNS SNAP FY27 COLA memo.
Out: a 676-char Medicare Hospice epub stub (the full booklet,
27,818 chars, remains). All 20 v11 riders verified already
present; the "4 unmerged riders" item was moot. The v15
growth-loop candidate is an unapproved TODO skeleton; it was
NOT merged and stays pending the operator's approval.

Retrieval probes against the live server (server-local /search):
QIT section ranks #1 for "qualified income trust"; the FNS FY27
memo #1; the SSA fact sheet #1 and the COLA release in top 6;
the $8,027 divisor section #1; the $752,000 home-equity section
#1; the Vision module in top 2 for the eyeglasses frequency
query. BT2026168 is present in the corpus and embedded (string
checks) but does not rank top-6 on a bare number query (the
other bulletins outrank it on vector similarity; noted, not a
presence failure).

### Registry corrections (VERIFIED_FIGURES, now 10 entries)
- ABLE annual contribution limit: $19,000 -> **$20,000** (the
  chat battery's seeded-wrong correction; POMS SI 01130.740).
- home_equity_limit rewritten: Indiana's own limit is the
  **$752,000** floor (IHCPPM 3005.10.05), not an unpicked point
  in the federal range.
- MMMNA statement: notes IHCPPM prints the shelter standard as
  **$812** (CMS computes $811.50); 812.0 added to the figures.
- waiver_income_cap: adds that Indiana had not posted a 2027
  cap as of October 2026 (do not present the 2026 figure as
  the 2027 cap).
- NEW ssa_2027_figures: SSI $1,029/$1,544, SGA $1,770
  non-blind / $2,950 blind, TWP $1,270, resource limits
  unchanged $2,000/$3,000.

### §G speed build (the operator's add-on, 13:04: "figure out whats
the fastest route per question... in addition to the speed fix")
Diagnosis it answers: the operator's simple SFC ask took 535s and
$0.0587 under v18. The panel had supported ALL 7 claims; the
IDK came from the CITATION gate (gap log, gate "judging"):
three claim-bearing sentences carried no citation marker and,
per the operator's v17.3 ruling, could neither ship nor be stripped.
The ask had also detoured through a failed branch-out. Claims
2/5/6 were covered by the primary (Lite) alone because BOTH
stage-2 seats' batched replies silently omitted the same
three claims.
- G1 completeness repair: a seat that ran gets ONE follow-up
  call naming only the claims its batched reply omitted;
  repaired claims join the round before the join/arbitration.
- G2 latency-ordered panel planning: _plan_panel leads each
  seat with its fastest measured candidate (rolling median;
  unmeasured scored at 75% of the seat budget). Failover order
  inside a call unchanged.
- G3 judge-seat timeout 60s -> 30s (THIRD_JUDGE_TIMEOUT).
- G4 per-class unit budgets inside the 600s ask cap: 150s
  simple / 420s hard; at the budget the ladder stops and the
  capped-partial path composes from verdicts in hand. The
  budget value is what the time-budget message now prints.
- G5 fast lane: simple questions run primary + second seat
  only (two families can still earn full verified); the third
  seat sits out. Hard questions keep the full panel.
- G6 route_report.py (this dir): offline report over
  ask_records.jsonl + gap_log.jsonl (per-ask wall/cost/badge/
  judges/served, badge aggregates, seat-model usage, IDK gate
  counts). First run: 1 recorded ask (the SFC 535s IDK) and 54
  historical gap rows (refutation 27, branch_out 12, judging 9,
  grounding 6). It becomes the fastest-route evidence base as
  asks accumulate.

## Deploy notes / incident
- One-off tasks DeployV19Once / FixEmbV19Once /
  CountChunksOnce / PushV19Once ran the deploy and were all
  deleted after; diagnostic scripts removed from the PC.
- EMBEDDINGS: gen_embeddings_stream.py embedded the 13 new docs
  (+283 chunks) but its finalize() rewrites embeddings.json
  from the raw jsonl WITHOUT the v11 dedupe skip list,
  resurrecting 2,253 near-duplicate chunks (loaded count
  75,258). Caught in verification; fixed the same run with
  apply_dedupe.py (this dir), which rebuilds embeddings.json
  honoring dedupe_skip.json, then make_npz + restart. Final:
  **embeddings_loaded 73,014**, hybrid true. Residual parity
  note: 9 exact-duplicate chunk rows for one legacy in.gov PDF
  sit in the raw jsonl (pre-existing; the old build excluded
  one copy of each via the skip list, the new ordinals no
  longer match those entries); impact negligible.
- Post-deploy /health: docs_in 1291, docs_federal 3955,
  cap $2.00, cap_tripped false, spend_today $0.745606 (the
  day's ledger; includes the operator's own asks — the deploy itself
  made zero /ask calls and spent nothing).
- Push scope: fssa_search_server.py ONLY, mirroring v18 (the
  repo's corpora are the scrubbed variants and were not
  touched; the merged corpora deploy to the PC only).

## Still open after v19
- Pest-control live re-run through the job path (owed; needs
  a spend-allowed moment).
- §F server battery (needs the operator's own go; fresh-ledger-day
  protocol).
- Growth-loop candidate: pending the operator's approval.
- The citation gate's strictness is the operator's v17.3 ruling; v19
  did not loosen it. The SFC failure mode is documented above
  for his grading.
