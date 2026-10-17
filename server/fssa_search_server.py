"""FSSA Medicaid Q&A server.

Serves:
  GET  /                    -> Q&A web page (house design tokens)
  GET  /search?q=keywords   -> JSON {hits: [{title, url, source, verified, excerpt}]}
  GET  /health              -> {"ok": true, "docs": N}
  GET  /models              -> {"models": [...]} from Ollama
  POST /ask                 -> {"answer": ..., "hits": [...]} (JSON body: {question, model})
  GET  /icon.png            -> app icon

The /ask endpoint searches the local corpus, then asks the local Ollama
model to answer from the retrieved excerpts.

v13 (2026-10-13): verified-figures registry injected as an authoritative
prompt block; source ranking v2 (Indiana-state and manual-class sources
lifted, admin/payee pages demoted, /ask only); deterministic CSRA math;
the figure checker can force ONE regeneration naming the bad figure;
bundled-question decomposition also drives the prompt's part list; hard
questions asked of the local default route to qwen3:14b (9B unloaded
first, 9B fallback on error); /ask accepts fresh/nocache to bypass the
answer cache for manual retests. See v13/PLAN.md.

v14 (2026-10-13): verification-first pipeline, "no unverified answer
ships" (see v14/PLAN.md). Every /ask ends VERIFIED, PARTIAL (verified
parts + inline IDK parts), or IDK. Adds: a retrieval sufficiency gate
keyed to a mechanism registry (the v13 figures registry grown into
mechanisms with required elements checked in code; QIT/Miller trust
seeded from manual test #1); claim-level grounding (claims extracted
and judged claim-by-claim, extending the figure checker to rules);
dual judging (Gemini Lite primary, local qwen3:14b second, both must
pass) with Gemini Flash arbitration on disagreements (budgeted);
an escalation ladder (draft, judge, two 14B redrafts with the judges'
objections attached, Flash, IDK); Verified/Partial/IDK badges in the
API metadata and the webapp, with a visibly different badge when only
one judge could run (degraded); and a JSONL gap log (gap_log.jsonl,
GET /gaps) feeding the monthly rescrape and registry growth. Gemini
down degrades to local judging, never to unverified answers.

v14.1 (2026-10-13): no-loss judging speedups; verdict logic, badges,
gates, and the ladder are unchanged. The local judge now gets the
GPU to itself (every other resident chat model is unloaded before
14B judge/redraft work, not just the 9B), the Lite primary judgment
runs concurrently with the local judge's GPU preparation (unload +
model load), second-judge and arbitration prompts carry per-claim
scoped evidence (only the excerpts each claim rests on, mapped by
the primary judgment; full set as fallback), and local judge calls
size their context window to the prompt with a verdict-sized output
budget instead of the answer-generation settings.

v14.2 (2026-10-13): a THIRD judge seat joins the panel: a hosted
open-weight model on a free tier (shipped default: NVIDIA NIM's
Nemotron 3 Super 120B). It verdicts the same claim list as the
second judge, in parallel with it, with the same claim-scoped
evidence. Three verdicts resolve by majority (see
_majority_final); Flash is then spent only on genuine 3-way splits
and its final-rung role. The seat is config-driven: NVIDIA NIM,
Groq, and Cerebras configs all live at the top of the judging
section and one constant (THIRD_JUDGE_PROVIDER) picks the active
provider, so moving the seat is a one-line change. (The third
seat and every other panel seat are deleted in v23.1; see the
v23.1 entry at the end of this header.) The seat is
DORMANT without the active provider's key (its key file in the
server dir, or its env var): the seat then simply never runs and
judging is exactly v14.1 dual judging. It ships dormant; the key
arrives with the operator's provider signup. The panel (which judges
ran, per-judge ms, third-judge status) rides in the response's
judging metadata.

v15 (2026-10-13): attack the draft, not just the verdict (see
v15/PLAN.md). Refutation retrieval hunts contradicting excerpts
for load-bearing claims, in parallel with the first judge round,
with a skepticism-preserving join before resolution. NIM takes
the local 14B's three jobs: hard questions draft on
moonshotai/kimi-k3, the standing second judging seat is the NIM
Nemotron (the v14.2 third seat promoted), and ladder redrafts
generate on NIM; simple questions may draft on a fast NIM model
(nemotron-3.5-lightning) with automatic fallback to the local 9B.
The 14B is DEMOTED, not removed: it remains the last-resort
judge when both remote seats are down (behind
LOCAL_FALLBACK_JUDGE) and the fallback drafter/redrafter when
NIM as a whole is down. v15.1 narrows it further (the operator's
ruling, 2026-10-13; see V151_AUDIT.md): the local models stay
cold on the remote path — the retrieval utilities and the
divergence oracle run on NIM, a single failed remote seat
degrades instead of seating the 14B, disputes get no local
tiebreak, and every local call carries keep_alive 0.
Drafters must cite excerpt
numbers at birth; uncited load-bearing sentences are unverified
by construction. A quantity checker widens the figure checker
to periods and period conversions. Source-conflict claims force
answers to surface corpus disagreements (registry figure slots)
instead of silently picking a value. A per-ask wall-clock cap
(ASK_TIME_CAP_S, 600s) stops the ladder mid-climb and ships only
already-verified claims as a Partial, else IDK. Speedups:
per-claim verdict caching, keep-alive connection pooling for
Gemini/NIM calls, and a job-based webapp flow (POST /ask?async=1
+ GET /ask/result/<id>) with live stage text; the synchronous
/ask API is unchanged for other callers.

v16 (2026-10-13): two criteria, accuracy first, then speed (see
v16/PLAN.md). Every NIM seat is MEASURED into its job by a
build-time bake-off on the golden set (item 1): seats are pinned
per job (hard drafting, simple drafting, two NIM judge seats)
across THREE NIM key files so parallel seats stop sharing one
key's ceiling, and runtime seat health benches a seat that
times out repeatedly or drifts over its latency budget, failing
the job over to the bake-off runner-up — never silently, never
to local. The panel is Gemini Lite + two NIM judge seats; the
local 14B's dispute tiebreak is GONE (operator ruling: disputed
claims without a Flash ruling fail closed) and locals load only
on genuine remote unavailability or explicit user choice, with
every load recorded (item 5). Miller accuracy (item 4):
mechanism-led answers classify their lead sentence load-bearing
by construction, objections name the exact citation a failed
lead must carry, one micro-repair per rung rewrites only the
lead, and registry mechanisms may carry canonical claim
formulations the judges enforce. The webapp loses its model
dropdown for a single "run locally" toggle (item 6). Branch-out
(item 7): a unit whose honest disposition is IDK first searches
the live official web (Tavily, whitelist in.gov / medicaid.gov /
cms.gov / ssa.gov / irs.gov, basic searches only) and re-runs
against the fetched pages as temporary excerpts; verified
branch answers ship under the new badge verified_web with the
fetch provenance in the record, and every branch-out gap-logs
for rescrape absorption. Retrieval reports its sub-timings per
ask (item 8). The 600s cap is unchanged.

v17 (2026-10-13): the paid swap (operator go in chat): the
primary seats move to OpenRouter, hand-picked from the v16
bake-off measurements, NO new bake-off. GPT-OSS-120B (pinned
to its fastest backend, Cerebras-hosted, fallbacks allowed)
drafts hard questions; GPT-OSS-20B drafts simple questions
and takes over the retrieval utilities (the v16 split showed
rewrite + rerank were 80-95% of retrieval time); every
OpenRouter seat failure falls back to the seat's v16 NIM
assignment, and the local last-resort rules are untouched.
Judges are UNCHANGED (Gemini Lite + the two NIM Nemotron
seats), as are the divergence oracle and branch-out. Billing
guardrails (the operator's hard requirement): every OpenRouter
response reports its cost (usage include), accumulated into
the answer metadata (cost_usd) and a persistent daily counter
(openrouter_spend.json); at OPENROUTER_DAILY_CAP_USD (default
$1.00/day) the OpenRouter seats are treated as unavailable
and the fallback chain takes over, the trip logged. The
key's own limit state (GET /auth/key) is read at startup and
reported in /health, so a dashboard-side key cap is visible
next to the code-side one. The key lives only in
openrouter_key.txt (gitignored by name in both PC repos,
verified before the file was created).

v18 (2026-10-14): the money and verifier hardening build (see
v18/PLAN.md and v18/redteam/ADJUDICATION.md). The $1.00/day
preflight cap becomes a reservation-based HARD ceiling of
$2.00/day (v21: $10.00/day) at the single OpenRouter choke
point: every call
reserves its provable maximum (prompt bytes as the input bound,
max_tokens at the model's output price, priced at the most
expensive backend its route may fall back to) under a lock
before it is admitted, settles to the billed cost on a clean
response, and settles at the full reserved maximum on any
timeout, transport failure, or cost-less response, per HTTP
attempt. The ledger is written atomically, fails CLOSED when
unreadable, counts unsettled reservations at full value across
restarts, reconciles against the key's usage_daily, and keeps
its day in America/Indiana/Indianapolis. The verifier's
fail-open paths close: an unruled non-supported refutation
verdict becomes the claim's final; badges derive from per-claim
family coverage (two distinct judge families or it is
verified_single); final-rung Flash can no longer overturn a
majority contradiction or a code-forced final; arithmetic is a
gate on every ship path; stripped drafts are re-judged before
they can ship; the capped Partial is an allow-list of sentences
that ARE supported claims. Judge seat 2 is a Qwen-family
non-thinking instruct (GPT-OSS-20B was rejected: on simple
asks it is the identical model that drafted); a diversity
guard keeps one model family from voting twice on a panel,
with seats planned atomically per round and the drafting
family excluded from every verdict path. "Run locally" is
honest: it means local drafting, local judging, and local
utilities, with the mode in the request body and the cache
key. Every finished ask persists a full record (per-claim
verdicts with the model that served each, the judged draft,
job-layer timestamps) to ask_records.jsonl for post-mortems
and the judge-only replay harness. §D corpus work is deferred
to v19/v20 (operator direction, 2026-10-14).

v19 (2026-10-14): the §D corpus incorporation build plus the
registry corrections the chat battery and the 2027 COLA forced.
Corpus: +10 Indiana docs (IHCP bulletins BT2026168-BT2026171;
the IHCPPM body texts the battery found missing: 2615.75.15
trusts, 3006.00.00 penalty divisor, 3010.20.10/3455.15.10.10
spousal standards and shelter formula, 3005.10.05 home equity;
the IHCP Vision and Dental provider modules) and +3 federal
docs (SSA 2027 COLA release and fact sheet; the USDA FNS SNAP
FY2027 COLA memo), minus the 676-char hospice epub stub.
Registry: the ABLE annual contribution cap corrected to
$20,000 for 2026 (the battery proved $19,000 was the 2025
figure); the home-equity entry now states Indiana's own
$752,000 limit (IHCPPM 3005.10.05, the federal floor); the
MMMNA entry notes Indiana's manual prints the shelter
standard as $812; the waiver-cap entry notes Indiana had not
posted a 2027 cap; new ssa_2027_figures entry carries the
2027 SSI/SGA/TWP amounts. §G speed build (operator direction,
2026-10-14, after a simple SFC ask took 535s): (G1) a
completeness repair re-asks a verdict seat ONLY for the
claims its batched reply silently omitted, instead of
leaving them single-family; (G2) panel planning leads each
seat with its fastest measured candidate (rolling median
latency) instead of fixed list order; (G3) the judge-seat
timeout drops from 60s to 30s so a stalled provider fails
over sooner; (G4) each unit judges against a per-class time
budget (150s simple, 420s hard; v20: 285s hard) inside the ask's 600s
absolute cap, composing from verdicts in hand at the budget;
(G5) simple questions take a fast lane (primary judge plus
the second seat; the third seat sits out), while hard
questions keep the full panel. The offline companion is
v19/route_report.py, which reads ask_records.jsonl and
reports per-route wall time, cost, and badges.

v20 (2026-10-14): the free-first route rewire (the operator's
directive; see v20/STATUS.md). FREE + FAST providers become
the primaries; every PREPAID provider sits behind them as a
fallover. The final form is the operator's LAYER MODEL (later on
2026-10-14): strict free-before-paid layering in every
chain, with the v19 latency ordering applying INSIDE a
layer only (layer wins in _plan_panel and _seat_pick).
Layer 0 free + fast: Groq seats and Gemini Lite's free
path. Layer 1 free + strong + scarce: Gemini 3.8 Flash,
rationed mid-ladder (sub-cap 10/day protecting its
final-rung reserve) as a replacement judge in seats B and
C, recorded as-served. Layer 2 free + slow: the NIM seats.
Layer 3 paid: OpenRouter and xAI under the ledger caps.
Layer 4: local models last (cold-load rule), then
fail-closed IDK. Hard drafting: Groq GPT-OSS-120B, then
the NIM bench (Kimi K3, GLM-5.3 lead), then Grok 4.7 on
xAI, then GPT-OSS-120B on OpenRouter, then local. Simple
drafting + utilities: Groq GPT-OSS-20B, then the NIM
bench (gpt-oss-20b leads it), then OpenRouter, then
local. Judges: seat A (Google) is UNCHANGED (Gemini Lite
on the free Gemini path; v20 adds the same model via
OpenRouter as a paid fallover ahead of the NIM-seat
extractor); seat B (DeepSeek): NIM DeepSeek V4.1-Flash,
then NIM Nemotron Super, then the rationed Flash rung,
then the proven OpenRouter DeepSeek V3.1 — V4.1-Flash is
ungraded as a primary judge and Stage 3 compares this
panel against the v19 panel on the anchors before the
free-first panel is trusted; seat C (Alibaba): Groq
Qwen3.8-27B, then the rationed Flash rung, then the
proven Qwen3-235B on OpenRouter. Acceptance (the operator,
2026-10-14): a unit returns in under 5 minutes and
accurate — per-class budgets are 150s simple / 285s hard
inside the 600s ask cap, NIM legs carry tight per-call
timeouts (judges 30s, drafting ~60s), chain legs clamp to
the unit's remaining budget and shed at its edge, and NO
gate, panel rule, or citation rule was weakened to buy
the speed.
xAI is an ORDINARY PAID provider: the $150/mo data-sharing
free-credits program was sunset at the end of May 2025 and
the $25 signup promo never materialized, so xAI spend
counts against the real-money caps (the $2.00/day ledger
and the $0.50 per-ask ceiling through v20.2; $10.00/day and
$1.00 in v21) exactly like OpenRouter,
through the same reservation gate. The XAI_FREE_CREDITS
flag (default False, now a permanent fact) exists only so
a future grant program could switch xAI accounting to a
visibility-only credit ledger without code surgery. Groq
free-tier calls are guarded by a persistent daily request
counter (soft cap 900/day, Indiana ledger day): past it,
Groq entries shed to their fallovers until the next ledger
day, and every shed is logged in the ask record. All v19
verification machinery (gates, badges, the diversity
guard, panel planning, completeness repair, per-class
budgets) is unchanged.

v20.2 (2026-10-14): fixes + observability, from the
eyeglasses diagnosis (the first live v20 ask: the IHCP
Vision Services module held the exact answer and never
reached the draft). Retrieval: "how often"/"how
frequently" join the duration-boost triggers; a
vision/eyeglasses topic bridge pins the Vision Services
module (PROMOD00051); IHCP Provider Reference Modules
are exempt from the v13 provider-title demotion.
Observability: ask records now persist every judge
seat's per-leg failure reasons (seat_fail/seat_down
events), each unit's judge error strings, and the fused
retrieval candidate pool per retrieval call (per-list
ranks, rerank-window membership, final selection), so a
silent seat death or a rerank-window miss is diagnosable
from ask_records.jsonl alone. A boot-time consistency
check compares the loaded embeddings against the chunk
manifest the corpora produce under the GENERATION
chunker (replicated in the check; the retrieval chunker
is a different, coarser split) and reports in /health
as "consistency" — WARN only, never a boot refusal.
Plus the comment scrub:
first-name attributions in comments/docstrings replaced
with neutral wording (no logic, prompt, or behavior
change beyond the items above). See v20.2/STATUS.md.

v21 (2026-10-14): the retrieval-accuracy build (see
v21/STATUS.md). Two production IDKs (eyeglasses on v20,
the transfer divisor on v20.2) shared one shape: the
authoritative chunk was IN the corpus but never entered
the candidate pool on natural phrasing, and the panel then
verified a truthful-about-its-excerpts IDK. v21 answers
that class, not the instances: (1) an exact-term RESCUE
pass — when the fused pool shows zero coverage of the
question's rare terms, or a unit still ends IDK, the
corpus is scanned for the question's own rare terms and
phrases, hits are scored by co-occurrence then rarity
then source authority, and the best chunks join the pool
(rs channel; a post-IDK rescue re-runs the unit once,
corpus before web); (2) REGISTRY-DRIVEN retrieval —
VERIFIED_FIGURES entries carry topic phrases + a source
pointer, and a matched entry pins its source chunk into
the pool (rg channel); (3) the registry is EFFECTIVE-DATED
— entries carry valid_from/valid_to, figure selection
uses the date the question is about, and the transfer
divisor joins as a dated pair ($8,027 from 2026-07-01;
$7,651 before it); (4) the NIM transport honors per-leg
DEADLINES, so the retry inside a leg can no longer double
a 30s judge leg into the observed 60s (v20.2 retest:
60.19s/60.16s seat failures); (5) EVERY seat outcome path
emits its reason — picks that skip a candidate now record
seat_skip events (benched / keyless / family-excluded /
plan-led), so no seat result is reason-less again; (6)
gap-log rows carry the ask id and a classification guess,
feeding the growth-loop staging runner (tools/), whose
review queue and the 2027 watchlist ship in this version
as tooling — merging stays a human act. (7) CAP RAISE
(the operator's decision, 2026-10-14): the real-money
ledger caps rise — daily $2.00 -> $10.00, per-ask $0.50
-> $1.00 — so a grading or testing session cannot trip
the fence mid-session and silently gate off the paid
legs. The reservation mechanism, the ledger, and the
trip behavior are unchanged; the fence still exists,
just higher.

v23.1 (2026-10-15): the checks build (see v23.1/PLAN.md +
STATUS.md). The v23 first live test answered correctly and
still failed as a delivery: 623s wall, $0.31, a partial badge
— retrieval and the draft were right; the two-seat judge
panel burned 502s in Groq 413/429 retries and paid relief
legs. v23.1 replaces the panel with ONE judge pass plus
deterministic checks, and puts drafting on the provider
that worked. (1) CHECKS, not a panel: Gemini 3.5 Flash Lite
extracts + verdicts in one call; when Lite cannot serve,
DeepSeek Chat V3.1 (OpenRouter) backs it up AT MOST ONCE
per ask; when no judge can serve, the round runs on the
deterministic checks alone. The V4 models are out entirely
(the bake-off's two worst judges). Deterministic figure +
citation checks run UNDER the judge: a claim whose money
figures do not trace to its own cited excerpts, the
verified figures, or a simple derivation of them is
contradicted whatever the judge said. Deleted with the
panel: the judge-round loop + refutation passes, judge
seat 2, the relief bench, the seat/bench machinery, the
verdict cache, Flash arbitration, _panel_state. (2)
DRAFTING: xAI Grok 4.7 primary for every class, Groq
GPT-OSS 120B backup (free), local 9B last resort; the
OpenRouter drafting tier is deleted. (3) BUDGETS ENFORCED
(BUG-10): one deadline per ask threaded through every
provider call (each leg's timeout = min(its bound, the
budget left)); at the cap the job finalizes with its best
state and budget_cut + cut_phase recorded; the 600s cap is
also enforced inside the job thread (a daemon child under
a hard join). Budgets unchanged: 150s simple / 285s hard /
600s cap. (4) BADGES: checked (checks + judge green),
checked_det (checks green, judge unavailable or partial),
partial (one name; the _single/_reduced splits are gone),
the verified_web family for web-sourced answers, idk.
(5) THE STEPPER: phases are retrieving / drafting /
extracting / checking / final checks, with ONE fact-check
line naming who actually served (Lite, or the DeepSeek
V3.1 backup). (6) BUG FIXES from the v23 report: BUG-1
(phase_ms lost "retrieving" — the context reset now runs
before the phase opens), BUG-5(a) (the checks-stack stub
replay replaced the refutation replay), BUG-6 (stale
comments scrubbed), BUG-7 (phase_ms values are SECONDS;
comments now say so). BUG-2/3/4/9 died with the panel.

v25 (2026-10-15): Quick / Deep modes (see v25/PLAN.md).
Every ask carries a mode: "quick" (the DEFAULT — an absent
mode is a Quick ask) or "deep" (the v24 judged pipeline,
unchanged); anything else is a 400. Quick is a different
mechanism, not a weakened Deep: shared retrieval, ONE draft
call through the Drafting seat (_run_unit_quick), then the
deterministic final checks only — figures, quantities,
arithmetic, mechanism coverage, conflicts, and the citation
parse, with the v17.3 strip applied in code form
(_quick_strip_asides) and every other uncited factual
sentence failing closed. No extraction, no judge, no
redrafts, no rescue, no web branch (the branch-out helper
refuses Quick asks, including the zero-hit and sufficiency
IDK paths). A checked Quick answer ships under the new
badge "quick" ("Quick answer" on the page — never a
Checked-family string); a material check failure ships
the IDK result instead. Quick answers do not enter the
replay cache (the v23.1 checked_det precedent), and the
mode rides the answer-cache key so the lanes never replay
or dedupe into each other. Ask records carry the mode.

v25.1 (2026-10-15): the Quick lane redesign (see
v25.1/PLAN.md). The first live Quick ask failed closed on
a CORRECT draft: the citation parse stranded trailing
citation blocks on the wrong sentences, the arithmetic
checker read the date "(7/2026)" as an equation, and the
sentence's figure scope never saw its own excerpt. Fixes:
block citation attribution in _parse_citations (a citation
block credits the sentences since the previous block),
date masking in _arithmetic_errors, and a pooled figure
scope for the Quick gate call only (_check_figures
pool_all; Deep's per-sentence scoping is untouched). The
Quick draft now carries a self-audit contract, and a draft
that still fails the floor gets ONE repair call (same
seat, failures listed) before the unit IDKs — the Quick
draft cap is 2. Deep is behavior-identical to v25.

v26 (2026-10-15): the audit-fix release (see v26/BUILD.md
for the per-finding record; findings F1-F16 + N1/N2 from
v26-planning/V26_PATCH_PLAN.md, decisions D1-D6 ruled by
the operator 2026-10-15). Header correction carried from
Claude's review: the v25.1 line above ("Deep is
behavior-identical to v25") was never literally true —
block attribution and date masking are shared code and
changed Deep too; only the pooled figure scope is
Quick-only. v26 continues that pattern deliberately:
its shared-checker fixes (N1, F4, F6, F7) change Deep's
checking, and Deep's own seams change too (F1 ceiling,
F5 family exclusion). Money: the $1/ask ceiling is
consulted at every paid-leg start seam in both lanes
(F1); the Tavily counter claims before searching and
fails closed on a corrupt state file (F2). Wrong-answer
class: the legacy local mapping validates its model
against the seat allowlist (F3); both verbatim-ship
guards test the full ship test (F4); the draft-family
exclusion is enforced at intake and at the verdict legs
(F5); Deep's scoped figure grounding is sentence-local
again (own-marker sources; block attribution still
governs citation credit) (F6); the factual-sentence
pattern knows the coverage verbs (F7). Fail-closed
class: the ROUTED_MODEL route is retired (legacy local
judging defaults to the installed 9B) (F8); Quick units
get a per-part draft budget (F9); checker exceptions
final as recorded IDKs, not bare 500s (F10); the search
cache key carries use_rewrite (F11); a failed vector
store open retries on a cooldown (F12); Groq quota
counts HTTP attempts (F13). Cosmetic: a final cancel
check at response assembly (F14); abandoned jobs write
neither cache nor record (F15); dead code removed (F16);
request validation returns 400 before the async branch
(N2); the arithmetic checker no longer reads bare date
or fraction parentheticals as equations (N1).

v26.1 (2026-10-15): hotfix for a v26 regression found on
the first live test. F14's final cancel check read
`_ask_ctx().cancel` on the no-job branch of response
assembly; _AskContext holds `cancel_rec`, not `cancel`,
so every synchronous /ask raised AttributeError at
assembly (after the full pipeline had run) and returned
500. The async job route (the page) was unaffected. The
branch now calls _cancel_requested(), the same flag read
the rest of the cancel plumbing uses. Nothing else
changed; see v26.1/BUILD.md.

v26.2 (2026-10-16): the battery-driven checker and
honesty pass (see v26.2/DESIGN.md; evidence: the
2026-10-15/16 hole-finding battery). Eight fixes:
(1) derived arithmetic grounds through a bounded
derivation chaining of the existing half/double/sum/
difference whitelist, in _det_claim_check and in
_check_figures (two-step post-eligibility math —
$2,300 - $1,000 - $52 = $1,248 — no longer fails
grounding); (2) question-supplied figures are premises:
_check_figures reads the question's bare-form figures
too ("make 1300 a month"), and a claim whose figures
all come from the question is cited by the question;
(3) _check_figures and the claim check mask dates
before figure extraction, so "the July 2026 dollar
limits" no longer parses as a $2,026 figure (the
v25.1 _mask_dates precedent, extended to the figure
gate); (4) source-conflict claims engage only when
the question matched the entry or the draft writes
about the figure on-topic — a patient-liability ask
no longer inherits a forced shelter-allowance
conflict from its hits; (5) badge honesty: an answer
whose lead sentences state the documents do not
answer the question ships as an IDK, never under a
checked/quick/partial badge; (6) partial floor: a
one-sentence assembly ships only when its claim is
load-bearing, and a figure-seeking question never
ships a figureless partial; (7) fail-fast: the Deep
ladder stops after a second rung fails on the
identical deterministic signature, instead of
spending the whole class budget re-asking (the $1
per-ask ceiling is unchanged); (8) the CSRA and
MMMNA registry entries match vague spousal-worry
phrasing ("will my wife be okay financially") via
financial-worry group-2 terms; group 1 still
requires a named spouse.

v27.1 (2026-10-16): the front door (see v27/DESIGN.md +
v27/FRONT_DOOR_DATA.md). A code front door sits in front of
the AI lanes: facts live in front_door_data.json rows
(figure, source citation, effective window, verified-on,
review-by), five general engines compute answers
(split-and-clamp, liability, penalty, year-indexed caps,
coverage lookup), and a conservative router claims a
question only when a row provably covers it. Everything
else falls through to Quick/Deep untouched and is logged
by topic to front_door_log.jsonl as the expansion queue.
Code answers ship under the badge "code" with their
figures and source in the answer text, at $0.

v27.2 (2026-10-16): front-door trigger widening for all
21 rows + the router specificity guard (BUG-1).

v28 (2026-10-16): corpus generation g0006 (the v28 scrape:
WIC, childcare/children and behavioral-health member
layers, VR/BRS, safety & aging front doors, energy &
housing, child support, disaster, SHIP, school meals,
Tier 3 trees) plus the tax-credit registry entries
(decision f6: EITC as a dated TY2025/TY2026 pair, Child
Tax Credit / ACTC, and the Indiana ABLE contribution
credit) — registry only, no front-door rows for tax
credits, every figure traced to a source in v28/sources/.
"""

import datetime
import calendar
import contextvars
import hashlib
import http.client
import io
import uuid
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8471
OLLAMA = "http://127.0.0.1:11434"

with open("corpus.json", encoding="utf-8") as f:
    CORPUS_IN = json.load(f)

try:
    with open("corpus_federal.json", encoding="utf-8") as f:
        CORPUS_FED = json.load(f)
except FileNotFoundError:
    CORPUS_FED = []

# default for backward compat
CORPUS = CORPUS_IN

# --- Embeddings (hybrid search) ---
# v12: prefer embeddings.npz (binary; loads in seconds) over the
# embeddings.json text format (minutes to parse). EMB_MAT holds the
# L2-normalized float32 matrix aligned row-for-row with EMBEDDINGS so
# vector_search scores with one matmul instead of a Python loop over
# per-chunk numpy calls. The JSON path still works and also builds
# the matrix when numpy is available.
EMBEDDINGS = []
EMB_DIM = 0
EMB_MAT = None
if os.path.exists("embeddings.npz"):
    try:
        import numpy as _np
        with _np.load("embeddings.npz") as _z:
            _vecs = _z["vecs"]
            EMBEDDINGS = json.loads(bytes(_z["meta"]).decode("utf-8"))
        if EMBEDDINGS:
            EMB_DIM = int(_vecs.shape[1])
        _norms = _np.linalg.norm(_vecs, axis=1, keepdims=True)
        _norms[_norms == 0] = 1.0
        EMB_MAT = (_vecs / _norms).astype(_np.float32)
        print(f"Loaded {len(EMBEDDINGS)} embeddings (dim {EMB_DIM}) from npz",
              flush=True)
    except Exception as _e:
        print(f"npz load failed ({_e}); falling back to embeddings.json",
              flush=True)
        EMBEDDINGS = []
        EMB_MAT = None
if not EMBEDDINGS:
    try:
        with open("embeddings.json", encoding="utf-8") as f:
            _emb_data = json.load(f)
            EMBEDDINGS = _emb_data.get("chunks", [])
            if EMBEDDINGS:
                EMB_DIM = len(EMBEDDINGS[0]["vec"])
            print(f"Loaded {len(EMBEDDINGS)} embeddings (dim {EMB_DIM})",
                  flush=True)
        if EMBEDDINGS and EMB_MAT is None:
            try:
                import numpy as _np
                _m = _np.array([ch["vec"] for ch in EMBEDDINGS],
                               dtype=_np.float32)
                _norms = _np.linalg.norm(_m, axis=1, keepdims=True)
                _norms[_norms == 0] = 1.0
                EMB_MAT = (_m / _norms).astype(_np.float32)
            except Exception:
                EMB_MAT = None
    except FileNotFoundError:
        print("No embeddings file, vector search disabled", flush=True)

# Precomputed matrix row sets per corpus for the matmul fast path.
_ROWS_ALL = list(range(len(EMBEDDINGS)))
_IN_URLS = {d["url"] for d in CORPUS_IN}
_FED_URLS = {d["url"] for d in CORPUS_FED}
_ROWS_IN = [i for i, ch in enumerate(EMBEDDINGS) if ch["url"] in _IN_URLS]
_ROWS_FED = [i for i, ch in enumerate(EMBEDDINGS) if ch["url"] in _FED_URLS]

# v30 (generation-scoped embedder): the embed model is a
# property of the ACTIVE generation, recorded in its meta
# table at build time (embed_model; generations built
# before the key existed read as the historical default).
# _embed_query resolves the model — and the optional
# query instruction — from the open v2 store's state, so
# a CURRENT flip back to a nomic generation restores
# nomic queries with it: the store and the query embedder
# cannot silently disagree (the boot canary is the
# backstop if they ever do). Dimensions likewise come
# from the generation (meta embed_dim / the loaded
# matrix shape), never a hard-coded constant.
_EMBED_DEFAULT_MODEL = "nomic-embed-text"


def _active_embed_spec():
    """(model, query_instruction) for the active
    generation. Falls back to the historical nomic
    defaults whenever no v2 store is open or the
    generation predates the meta keys."""
    try:
        st = _V2
    except NameError:
        st = None
    if st:
        return (st.get("embed_model") or _EMBED_DEFAULT_MODEL,
                st.get("embed_query_instruction") or "")
    return _EMBED_DEFAULT_MODEL, ""


def _embed_query(text, document=False):
    """Get an embedding vector via Ollama's /api/embed,
    using the ACTIVE generation's embed model (see
    _active_embed_spec). Query/document asymmetry (the
    corpus workstream's fact sheet, Flag 1): the
    generation's recorded query instruction is a complete
    prefix (it ends with the Query marker) and is
    prepended VERBATIM to query prompts only;
    document=True embeds a stored-text probe (the boot
    canary) and never carries it, matching how the build
    embedded the stored side. Nomic generations record no
    instruction and get the raw text, as before."""
    model, instr = _active_embed_spec()
    prompt = text or ""
    if instr and not document:
        prompt = instr + prompt
    try:
        payload = json.dumps({"model": model,
                              "input": prompt[:2000]}).encode()
        req = urllib.request.Request(OLLAMA + "/api/embed", data=payload,
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        embs = data.get("embeddings") or []
        return embs[0] if embs else None
    except Exception:
        return None

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

def _cosine(a, b):
    if _HAS_NUMPY:
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        return float(np.dot(va, vb) / denom) if denom else 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0

# Config: single model for all local LLM work to avoid VRAM thrashing
LOCAL_MODEL = "qwen3.5:9b"
# v24 (§8.2): the Models panel's local picks are an ALLOWLIST,
# not the installed list — a benefits tool does not offer every
# model that happens to be on the PC. Offered: the two aligned
# qwen3.5 chat models. Never offered: nomic-embed-text
# (embeddings only), qwen2.5-coder:7b (code-tuned), and the
# abliterated 8Bs (safety tuning removed by design). New models
# join only by a deliberate edit here (e.g. after a bake-off).
LOCAL_SEAT_ALLOWLIST = ("qwen3.5:9b", "qwen3.5:4b")
# v26 (F8, D2 = RETIRE): the v13 ROUTED_MODEL route
# (qwen3:14b) is deleted — the constant, route_local_model,
# and every live-path reference. The 14B was deleted from
# the PC in the 2026-10-15 cleanup; local defaults now
# name the installed 9B (LOCAL_MODEL) explicitly.
# Feature toggles (can be overridden via /ask body: {"rewrite": false, "rerank": false})
REWRITE_ENABLED = True
RERANK_ENABLED = True
# v30 (cross-encoder rerank seat; contract per the
# reranker workstream's INTEGRATION.md): when the backend
# is "ce", the fused window is rescored by a local
# cross-encoder service (bge-reranker-v2-m3 class; the
# service itself is a separate seat) over the same
# title+excerpt view the LLM rerank reads. Contract: POST
# {RERANK_CE_URL}/score {query, passages} -> {"scores"}.
# ANY failure (unreachable, timeout, bad shape) fails
# open to rerank_with_llm, which itself fails open to
# fused order; no retry inside the ask. Default backend
# stays "llm": "ce" becomes the default only when the
# binding A/B ship rule passes (exported pools; see the
# pool export below). Kill switch: FSSA_RERANK_BACKEND=
# llm + restart. State surfaces in /health (rerank_ce).
RERANK_BACKEND = os.environ.get("FSSA_RERANK_BACKEND",
                                "llm")  # "llm" | "ce"
RERANK_CE_URL = os.environ.get(
    "FSSA_RERANK_CE_URL", "http://127.0.0.1:8472")
RERANK_CE_TIMEOUT = float(os.environ.get(
    "FSSA_RERANK_CE_TIMEOUT", "3"))
RERANK_CE_MAX_PASSAGES = 50
_RERANK_CE_STATE = {"last_ok": None, "last_error": None,
                    "consecutive_failures": 0}
# v30 (pool export for the binding rerank A/B): when
# FSSA_POOL_EXPORT names a file, each rerank-slot ask
# appends one JSON line there — the full pre-rerank pool
# (section id, title, url, passage), the fused order, the
# winning rerank order, and the backend used — in the
# A/B harness's fixture shape (gold ids are joined later
# from the battery keys). Log-only: unset (the default)
# means no export and zero behavior change; export
# errors are swallowed so an ask can never fail on it.
_POOL_EXPORT_PATH = os.environ.get("FSSA_POOL_EXPORT", "")
_POOL_EXPORT_LOCK = threading.Lock()
# Simple in-memory caches
_SEARCH_CACHE = {}
_ASK_CACHE = {}
_CACHE_MAX = 200

# Gemini API (optional, via /ask body: {"model": "gemini-..."})
GEMINI_API_KEY = None
try:
    with open("gemini_key.txt", encoding="utf-8") as f:
        GEMINI_API_KEY = f.read().strip()
except FileNotFoundError:
    pass

# (v23.1 comment scrub, BUG-6: the v14.2 note that stood here
# described a _third_judge_key() loader for the third judge
# seat. That machinery is deleted — provider keys load via
# _nim_key_for from each provider's gitignored key file in
# the server dir, the same pattern as the Gemini key above.)

# --- v15: pipeline config -------------------------------------------------
# Per-ask wall-clock ceiling (operator decision, 2026-10-13): when an
# ask hits the cap mid-ladder it stops climbing and ships the best
# state reached (a Partial built ONLY from claims that already
# passed the checks, else an IDK with its gap log entry); timing
# records cap_hit (v23.1: budget_cut). The cap can never make an
# unverified answer ship.
ASK_TIME_CAP_S = 600
# v23.1 (WS-A): the job-thread backstop's slack over the ask
# cap — _run_job joins its daemon child for cap + this many
# seconds before abandoning the job (see _run_job).
JOB_BACKSTOP_SLACK_S = 60
# v23.2 (BUG-11 / F1+F2): the class budgets, named once. ONE
# clock per ask is anchored at ask start (see _ask_clock_begin
# / _ask_clock_class): every unit, rescue re-run, and branch
# re-run of the ask shares the SAME class deadline instead of
# re-arming a fresh budget per _run_unit invocation (the
# v23.1 defect: deadline = min(ask cap, now + budget) at
# every _run_unit entry, so the ladder + rescue path could
# burn a full class budget per invocation and budget_cut
# never engaged on the 416.9s CSRA ask). The 600s ask cap
# (ASK_TIME_CAP_S) stays the outer bound.
ASK_BUDGET_SIMPLE_S = 150
ASK_BUDGET_HARD_S = 285
# v23.2 (F2): absolute cap on drafting calls per ask, shared
# by the ladder, the lead micro-repair, and every re-run
# (rescue / branch). At the cap the best judged draft on hand
# ships through normal finalization.
DRAFT_CALL_CAP = 4
# v26 (F9, D5 = PER-PART): a Quick unit's own draft budget
# (initial draft + the lane's one repair). Quick claims no
# longer draw on the shared DRAFT_CALL_CAP above — that
# cap stays exactly as BUG-11 set it for the Deep ladder.
QUICK_UNIT_DRAFT_CAP = 2
# v23.2 (F1): a provider call may start only when at least
# this much budget remains; below it the call cannot produce
# a usable result inside its bounded (deadline-clamped)
# timeout, so the stage is treated as spent and the budget
# cut is recorded instead of starting the call.
MIN_CALL_START_S = 2.0
# v18 (§B remainder): the per-ask OpenRouter cost ceiling. Past
# this line the ladder stops spending on redrafts and ships the
# best capped Partial from the claims already judged (or IDKs);
# it works alongside the time cap and the $10.00/day hard
# stop (v21: raised from $2.00/day and a $0.50 ceiling at the
# operator's direction, 2026-10-14, so a grading session
# cannot trip the fence mid-session; the fence itself stays).
# Tunable via env; the operator may retune after the first battery.
PER_ASK_CEILING_USD = float(
    os.environ.get("FSSA_PER_ASK_CEILING_USD", "1.00"))
# The demoted local 14B as LAST-RESORT judge. v15.1 (the operator's
# ruling, 2026-10-13): the flag gates ONLY the both-remotes-down
# degradation (Lite AND the NIM seat unable to extract -> the
# 14B judges alone, the v14 shape). A single failed remote seat
# degrades to the surviving remote judge alone, and disputes
# never go to a local tiebreak: the 14B is used only when it
# absolutely has to be (or the caller picked a local model).
LOCAL_FALLBACK_JUDGE = True

# --- v15 speedup S3: persistent remote connections -------------------------
# A small keep-alive pool for the server's HTTPS calls to the hosted
# providers (Gemini + NIM): one shared pool per host instead of a
# fresh TLS connection per call. ALL request semantics live in the
# callers (Gemini's 4s spacing and retry profile, NIM's one-retry /
# no-429-retry politeness); the pool only moves bytes. A connection
# that fails or goes stale is discarded and the caller retries per
# its own rules, exactly as with a fresh connection.
_CONN_POOL = {}
_CONN_POOL_LOCK = threading.Lock()
_CONN_POOL_MAX = 4


def _pool_acquire(host):
    with _CONN_POOL_LOCK:
        bucket = _CONN_POOL.setdefault(host, [])
        if bucket:
            return bucket.pop()
    return http.client.HTTPSConnection(host, timeout=60)


def _pool_release(host, conn, reusable):
    if reusable:
        with _CONN_POOL_LOCK:
            bucket = _CONN_POOL.setdefault(host, [])
            if len(bucket) < _CONN_POOL_MAX:
                bucket.append(conn)
                return
    try:
        conn.close()
    except Exception:
        pass


def _https_post(host, path, body, headers, timeout=60):
    """One POST over the keep-alive pool. Returns (status, body
    bytes). Raises on transport failure (the connection is then
    discarded, never returned to the pool)."""
    conn = _pool_acquire(host)
    conn.timeout = timeout
    # v18 (F9): http.client applies .timeout only in connect();
    # a REUSED pooled socket keeps its opener's timeout (a 30s
    # utility socket could kill a 120s draft, a 240s draft socket
    # could hang a 60s judge). Apply the timeout to the live
    # socket as well.
    if conn.sock is not None:
        try:
            conn.sock.settimeout(timeout)
        except Exception:
            pass
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    except Exception:
        _pool_release(host, conn, False)
        raise
    _pool_release(host, conn, True)
    return resp.status, data


def _https_get(host, path, headers, timeout=30):
    """One GET over the keep-alive pool (v17: the OpenRouter
    key-state read). Same contract as _https_post."""
    conn = _pool_acquire(host)
    conn.timeout = timeout
    if conn.sock is not None:  # v18 (F9): see _https_post
        try:
            conn.sock.settimeout(timeout)
        except Exception:
            pass
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    except Exception:
        _pool_release(host, conn, False)
        raise
    _pool_release(host, conn, True)
    return resp.status, data


# --- v15 speedup S5: job stage tracking ------------------------------------
# The webapp's async jobs report a live stage ("retrieving",
# "drafting", "judging round 2", "arbitrating", "done"). The stage
# lives in a thread-local the job runner points at its job record;
# on the synchronous path _set_stage is a no-op. The pipeline sets
# stages at real boundaries; the poller only observes.
_STAGE_TLS = threading.local()


def _set_stage(text):
    rec = getattr(_STAGE_TLS, "job", None)
    if rec is not None:
        rec["stage"] = text


# --- v23 (WS1): structured phase progress ----------------------------
# The job record carries a PHASE alongside the free-text stage:
# one of retrieving / drafting / extracting / checking /
# final checks / done, a round number for the looping phases
# (drafting and checking run once per ladder rung), and ONE
# fact-check line naming who is checking the draft (v23.1
# WS-F; the panel era's per-seat widgets are deleted — BUG-3).
# The page renders a stepper from this field ONLY:
# no percentage and no ETA anywhere (an ask's length is not
# predictable; elapsed seconds are shown, never a guess at
# what remains). The same transitions accumulate per-phase
# wall time on the ask context (phase_ms in the ask record —
# the values are SECONDS despite the name, BUG-7): the
# measurement the held 150/285s budget test reads.
_PHASE_ORDER = ("retrieving", "drafting", "extracting", "checking",
                "final checks", "done")


def _set_phase(name, round=None):
    rec = getattr(_STAGE_TLS, "job", None)
    if rec is not None:
        ph = rec.setdefault("phase", {"name": "retrieving",
                                      "round": None,
                                      "check": None})
        ph["name"] = name
        if round is not None:
            ph["round"] = round
    try:
        ctx = _ask_ctx()
    except Exception:
        return
    now = time.monotonic()
    cur = getattr(ctx, "phase_open", None)
    if cur is not None and cur[0] != name:
        ctx.phase_ms[cur[0]] = ctx.phase_ms.get(cur[0], 0.0) \
            + (now - cur[1])
        ctx.phase_open = (name, now)
    elif cur is None:
        ctx.phase_open = (name, now)
    # v24 (§6.4): the same phase events drive the progress
    # tracker's section states for the current unit.
    try:
        _trk_phase(name, round)
    except Exception:
        pass


def _phase_ms_snapshot():
    """The ask context's per-phase times (SECONDS, millisecond
    precision), with the still-open phase's elapsed included
    (non-mutating)."""
    try:
        ctx = _ask_ctx()
    except Exception:
        return {}
    out = dict(getattr(ctx, "phase_ms", {}) or {})
    cur = getattr(ctx, "phase_open", None)
    if cur is not None:
        out[cur[0]] = out.get(cur[0], 0.0) \
            + (time.monotonic() - cur[1])
    return {k: round(v, 3) for k, v in out.items()}


_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBS_MAX = 50


def _job_create():
    job_id = uuid.uuid4().hex[:16]
    rec = {"id": job_id, "status": "running", "stage": "retrieving",
           # v24 (§5.2): the Stop button's flag; the pipeline
           # reads it at its clock seams via the ask context.
           "cancel": False,
           "phase": {"name": "retrieving", "round": None,
                     "check": None},
           "result": None, "error": None, "ts": time.time()}
    with _JOBS_LOCK:
        _JOBS[job_id] = rec
        while len(_JOBS) > _JOBS_MAX:
            oldest = min(_JOBS.values(), key=lambda r: r["ts"])
            _JOBS.pop(oldest["id"], None)
    return rec


def _job_get(job_id):
    with _JOBS_LOCK:
        return _JOBS.get(job_id)

def _cache_get(cache, key):
    return cache.get(key)

# v18 (R10c): the caches are shared across handler and pipeline
# threads; the v17 check-then-pop eviction was unlocked.
_CACHE_LOCK = threading.Lock()

def _cache_put(cache, key, val):
    with _CACHE_LOCK:
        if len(cache) >= _CACHE_MAX:
            cache.pop(next(iter(cache)))
        cache[key] = val

def vector_search(query, n=6, corpus=None):
    """Pure vector similarity search over embedded chunks, respecting corpus filter."""
    if not EMBEDDINGS:
        return []
    _t_emb = time.monotonic()
    qvec = _embed_query(query)
    _retr_note("embed", time.monotonic() - _t_emb)
    if not qvec:
        return []
    _t_vec = time.monotonic()
    try:
        return _vector_search_scored(query, n, corpus, qvec)
    finally:
        _retr_note("vector", time.monotonic() - _t_vec)


def _vector_search_scored(query, n, corpus, qvec):
    if EMB_MAT is not None:
        # v12 fast path: one matmul over the corpus's rows.
        import numpy as np
        qv = np.array(qvec, dtype=np.float32)
        qn = float(np.linalg.norm(qv))
        if qn:
            qv = qv / qn
        if corpus is None or (corpus is not CORPUS_IN
                              and corpus is not CORPUS_FED):
            rows = _ROWS_ALL
        elif corpus is CORPUS_FED:
            rows = _ROWS_FED
        else:
            rows = _ROWS_IN
        if not rows:
            return []
        scores = EMB_MAT[rows] @ qv
        k = min(n, len(rows))
        idx = np.argpartition(-scores, k - 1)[:k] if k > 1 else np.array([0])
        idx = idx[np.argsort(-scores[idx])]
        return [{"title": EMBEDDINGS[rows[int(j)]]["title"],
                 "url": EMBEDDINGS[rows[int(j)]]["url"],
                 "source": "vector", "verified": True,
                 "excerpt": EMBEDDINGS[rows[int(j)]]["text"][:2000],
                 "_vscore": float(scores[j])} for j in idx]
    # Filter embeddings by corpus if specified
    allowed_urls = None
    if corpus is not None:
        allowed_urls = {d["url"] for d in corpus}
    scored = []
    for ch in EMBEDDINGS:
        if allowed_urls is not None and ch["url"] not in allowed_urls:
            continue
        sim = _cosine(qvec, ch["vec"])
        scored.append((sim, ch))
    scored.sort(key=lambda x: -x[0])
    return [{"title": ch["title"], "url": ch["url"], "source": "vector",
             "verified": True, "excerpt": ch["text"][:2000], "_vscore": sim}
            for sim, ch in scored[:n]]

def _clean_llm_list_output(text):
    """Extract the actual content from LLM output that may have conversational preamble.
    Looks for the first line containing a comma or digit, skipping preamble like
    'Here are the keywords:' or 'Ranking:'."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    for line in lines:
        # Heuristic: content lines have commas or are mostly digits/commas/spaces
        if "," in line or re.match(r"^[\d,\s]+$", line):
            return line
    return lines[0] if lines else ""

def _utility_text(prompt, max_tokens=256, timeout=30):
    """One bare chat call for the retrieval utilities (query
    rewrite, policy-phrase rewrite, rerank). Raises on any
    failure: the utilities degrade to their empty result,
    exactly as they did when the local call failed.
    v23 (WS2/WS3.1): the chain is Groq's free tier
    (GPT-OSS-20B) first, then the paid OpenRouter utility
    model — the NIM seat leg is DELETED with the rest of
    the NIM timed paths."""
    if _groq_available():
        try:
            return _groq_text(
                GROQ_UTILITY_MODEL,
                "You are a precise retrieval utility. Reply with "
                "only the requested output, no explanations.",
                prompt, temperature=0, max_tokens=max_tokens,
                timeout=timeout)
        except Exception:
            pass  # the OpenRouter attempt below takes over
    if _or_available():
        try:
            return _or_text(
                OPENROUTER_UTILITY_MODEL,
                "You are a precise retrieval utility. Reply with "
                "only the requested output, no explanations.",
                prompt, temperature=0, max_tokens=max_tokens,
                timeout=timeout)
        except Exception:
            pass
    raise RuntimeError("utility providers all down")


def rewrite_query(question):
    """Rewrite a question into better search terms (v15.1: NIM
    fast tier; see _utility_text)."""
    try:
        prompt = (f"Rewrite this question as 5-8 keywords for document search. "
                  f"Output ONLY the keywords, comma-separated, no other text.\n\n"
                  f"Question: {question}\nKeywords:")
        text = _clean_llm_list_output(
            _utility_text(prompt, max_tokens=128, timeout=30))
        return [t.strip() for t in text.split(",") if t.strip()][:8]
    except Exception:
        return []




# v23 (WS3.2): the deterministic topic bridges are DELETED
# with the legacy retrieval path that applied them. Their
# job (lay term -> the manual section that states the rule)
# is carried in v2 by the registry channel, the exact-phrase
# channel, and pins.json.

US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "south carolina", "north dakota", "south dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "puerto rico", "guam", "virgin islands", "american samoa",
    "northern mariana",
)






def _has_figure(text):
    """Chunk quotes an actual dollar figure (in prose or a table)."""
    return bool(re.search(r"\$\s?\d|\b\d[\d,]*\s+dollars\b", text))


_NUM_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
              "twelve|fifteen|eighteen|twenty|thirty|sixty")


def _has_duration(text):
    """Chunk states a duration in months or years (digits or words)."""
    return bool(re.search(
        r"\b(\d+|" + _NUM_WORDS + r")[\s-]+(months?|years?)\b",
        text, re.IGNORECASE))


def _is_narrative(question):
    """Long, story-like questions (the situational pattern) get a wider
    retrieval pool and more excerpts than one-line lookups."""
    return len(question) > 140 or len(question.split()) > 24


# --- v11: chunk program/type tagging -------------------------------------
# Tags are computed at search time from each hit's document identity
# (URL, title, text signals), so every corpus document is tagged with
# no ingest metadata and future scrapes are covered automatically.
# The program tag drives excerpt labels, program-scoped ranking, and
# bridge pin scoping: the v10 lesson was that SSI-program law (POMS)
# out-competes Medicaid sources where the two programs share words.

def _classify_hit(h):
    """Return (program, dtype) for a hit dict."""
    u = (h.get("url") or "").lower()
    t = (h.get("title") or "").lower()
    if "poms.nsf" in u or "secure.ssa.gov" in u:
        prog = "SSI/SSDI"
    elif "law.cornell.edu" in u or "ecfr.gov" in u:
        prog = "SNAP" if "/273." in u else "Medicaid" if "/435." in u \
            else "General"
    elif "fns.usda.gov" in u or "fna.usda.gov" in u:
        prog = "SNAP"
    elif "medicare.gov" in u or "medicareadvantage.com" in u:
        prog = "Medicare"
    elif "irs.gov" in u:
        prog = "MAGI"
    elif "/hip" in u or "healthy indiana" in t:
        prog = "MAGI"
    elif "iltcp" in u or "spousal impoverishment" in t:
        prog = "Medicaid"
    elif "ssa.gov" in u or "choosework" in u or "tickettowork" in u:
        prog = "SSI/SSDI"
    elif "medicaid.gov" in u or "cms.gov" in u:
        prog = "Medicaid"
    elif "waiver application" in t or "1915(c)" in t:
        prog = "HCBS"
    else:
        prog = "General"
    if "poms" in t or "poms.nsf" in u:
        dtype = "POMS operations manual"
    elif "bulletin" in t or re.search(r"\bbt20\d\d", t):
        dtype = "IHCP bulletin"
    elif "evidence of coverage" in t or "member handbook" in t:
        dtype = "plan handbook"
    elif "waiver application" in t or "1915(c)" in t:
        dtype = "waiver application"
    elif "policy manual" in t or "provider manual" in t:
        dtype = "policy manual"
    elif "law.cornell.edu" in u or "ecfr.gov" in u:
        dtype = "regulation"
    elif "understanding ssi" in t or "spotlight" in t:
        dtype = "SSA explainer"
    else:
        dtype = "program page"
    return prog, dtype




def _hit_year(h):
    """Newest plausible year (2020-2027) in a hit's title/URL/excerpt."""
    text = ((h.get("title") or "") + " " + (h.get("url") or "") + " "
            + (h.get("excerpt") or "")[:400])
    years = [int(y) for y in re.findall(r"\b(20[2-9]\d)\b", text)
             if 2020 <= int(y) <= 2027]
    return max(years) if years else None




def _is_indiana_source(h):
    """Indiana-state source: an in.gov page or an Indiana-titled doc
    (the FNS Indiana SNAP page is the canonical example)."""
    u = (h.get("url") or "").lower()
    t = (h.get("title") or "").lower()
    return "in.gov" in u or "indiana" in u or "indiana" in t


_MONEY = r"\$?\s?(\d[\d,]*(?:\.\d{1,2})?)"
_EQ_RE = re.compile(_MONEY + r"\s*([+\-−×x*/÷])\s*" + _MONEY +
                    r"\s*=\s*" + _MONEY)
_PAREN_EQ_RE = re.compile(_MONEY + r"\s*\(\s*" + _MONEY +
                          r"\s*([+\-−×x*/÷])\s*" + _MONEY + r"\s*\)")
# v29: a stated equation with 3+ terms ("$2,600 - $1,805 -
# $52 = $743"); folded left-to-right by _arithmetic_errors
# before the two-term scans run (see there).
_CHAIN_RE = re.compile(_MONEY + r"(?:\s*[+\-−×x*/÷]\s*" + _MONEY +
                       r"){2,}\s*=\s*" + _MONEY)


def _fmt_money(v):
    s = f"{v:,.2f}"
    return "$" + (s[:-3] if s.endswith(".00") else s)


def _eval_money(a, op, b):
    if op == "+":
        return a + b
    if op in ("-", "−"):
        return a - b
    if op in ("×", "x", "*"):
        return a * b
    return a / b if b else None


# v25.1: date-like tokens are masked before equation
# extraction. A standards citation like "$162,660 (7/2026)"
# otherwise parses as the equation $162,660 = ($7 / $2,026)
# and fails a correct draft (live Quick ask c3b7a02ec2c5).
_DATE_MASK_RX = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{1,2}/\d{4}\b"
    r"|\b(?:january|february|march|april|may|june|july|"
    r"august|september|october|november|december|jan|feb|"
    r"mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+"
    r"\d{4}\b", re.IGNORECASE)


def _mask_dates(text):
    """Replace date-like spans with spaces (length-preserving,
    so match offsets in the masked text stay the answer's)."""
    return _DATE_MASK_RX.sub(
        lambda m: " " * len(m.group(0)), text or "")


def _arithmetic_errors(answer):
    """v18 (F6): the pure check behind _check_arithmetic: the
    correction notes for the answer's explicit dollar equations,
    WITHOUT appending anything. _run_unit gates shipping on
    this (through v17.3 the only call ran after the ship
    decision and merely appended a note to a shipped answer).
    v25.1: the scan runs on date-masked text (see _DATE_MASK_RX)."""
    answer = _mask_dates(answer)
    notes, seen = [], set()

    def consider(a, op, b, c, raw):
        want = _eval_money(a, op, b)
        if want is None or abs(want - c) <= 0.51 or raw in seen:
            return
        seen.add(raw)
        notes.append(f"- {_fmt_money(a)} {op} {_fmt_money(b)} = "
                     f"{_fmt_money(want)} (the answer above says "
                     f"{_fmt_money(c)})")

    # v29: multi-term chains fold left-to-right FIRST. The
    # v26.2 B04-Quick draft stated its liability chain as one
    # three-term equation "($2,600 - $1,805 - $52 = $743)" —
    # correct arithmetic — but the two-term scan below read
    # the inner window "$1,805 - $52 = $743" and failed the
    # draft. A chain (3+ terms) is now reconciled as a whole,
    # and two-term matches inside a chain's span are skipped.
    chain_spans = []
    for m in _CHAIN_RE.finditer(answer or ""):
        raw = m.group(0)
        chain_spans.append((m.start(), m.end()))
        try:
            nums = [float(x.replace(",", ""))
                    for x in re.findall(_MONEY, raw)]
            ops = re.findall(r"[+\-−×x*/÷]", raw)
        except ValueError:
            continue
        if len(nums) < 4 or len(ops) != len(nums) - 2:
            continue
        acc = nums[0]
        ok = True
        for op, n in zip(ops, nums[1:-1]):
            acc = _eval_money(acc, op, n)
            if acc is None:
                ok = False
                break
        if not ok or abs(acc - nums[-1]) <= 0.51 or raw in seen:
            continue
        seen.add(raw)
        lhs = raw.rsplit("=", 1)[0].strip().rstrip(")").strip()
        notes.append(f"- {lhs} = {_fmt_money(acc)} (the answer "
                     f"above says {_fmt_money(nums[-1])})")

    def _in_chain(m):
        return any(s <= m.start() and m.end() <= e
                   for s, e in chain_spans)

    for m in _EQ_RE.finditer(answer or ""):
        if _in_chain(m):
            continue
        try:
            consider(float(m.group(1).replace(",", "")), m.group(2),
                     float(m.group(3).replace(",", "")),
                     float(m.group(4).replace(",", "")), m.group(0))
        except ValueError:
            continue
    for m in _PAREN_EQ_RE.finditer(answer or ""):
        if _in_chain(m):
            continue
        try:
            # v26 (N1; Claude's v25.2 handoff via plan §7): a
            # parenthetical holding a bare date or fraction
            # after a figure — (7/2026), (2026-07), (1/2) —
            # is not an equation, but the pattern reads it as
            # one and flags a false arithmetic error (which
            # then fails the quantity gate). When the
            # operator is a slash or dash and the
            # parenthetical carries no money marker (no $,
            # no thousands comma, no decimal point), skip
            # it; real parenthesized equations state money
            # and keep the check.
            _inner = m.group(0)[m.group(0).index("("):]
            if m.group(3) in ("/", "÷", "-", "−") \
                    and "$" not in _inner and "," not in _inner \
                    and not re.search(r"\d\.\d", _inner):
                continue
            consider(float(m.group(2).replace(",", "")), m.group(3),
                     float(m.group(4).replace(",", "")),
                     float(m.group(1).replace(",", "")), m.group(0))
        except ValueError:
            continue
    return notes


def _check_arithmetic(answer):
    """Verify the explicit dollar equations in a finished answer (v9).

    Small models write the equation and still botch the result
    ('($2,705 - $900 = $1,305)'). Python does not botch it: extract
    '$A op $B = $C' (and the '$C ($A op $B)' form), recompute, and
    append a visible correction when a stated result is off by more
    than rounding. Returns (answer, number_of_corrections)."""
    notes = _arithmetic_errors(answer)
    if not notes:
        return answer, 0
    return (answer.rstrip()
            + "\n\nArithmetic recheck (recomputed by the server):\n"
            + "\n".join(notes)), len(notes)

# --- v21 (WS1a): the exact-term rescue pass ------------------------------
# Two live failures share one shape: the authoritative chunk was
# IN the corpus but the fused pool never contained it on natural
# phrasing (IHCP Vision module on v20; IHCPPM 3006.00.00 on
# v20.2, where the pool's fused scores sat in a flat 0.0602-
# 0.0700 band and the key noun appeared in 0 of 16 candidates).
# Keyword scoring dilutes a rare term across a whole question;
# the rescue pass inverts the search: it scans chunk text for
# the question's own rare terms and ranks by how many DISTINCT
# terms co-occur in ONE chunk, then by term rarity, then by the
# source-authority order _classify_hit implies. It supplements
# the fused pool (its own rs rank channel in the pool
# snapshot); it never replaces it.
_RESCUE_GENERIC = set(
    "question answer ask asks tell covered coverage eligible "
    "eligibility qualify qualifies program state federal "
    "indiana medicaid medicare often frequently much many "
    "need needs know want wants person people individual "
    "family care health services service benefit benefits "
    "month monthly year years".split())


def _rare_terms(question):
    """The question's rare/content terms: multiword phrases of
    consecutive content words first, then the content words
    themselves; growth-loop registrations (WS3c) append their
    mapped corpus terms. Generic program vocabulary is
    excluded by _RESCUE_GENERIC; STOP words never appear."""
    ql = (question or "").lower()
    words = re.findall(r"[a-z]+", ql)
    stream = [w for w in words if w not in STOP]
    singles = []
    for w in stream:
        if w not in _RESCUE_GENERIC and len(w) >= 3 \
                and w not in singles:
            singles.append(w)
    phrases = {2: [], 3: []}
    for span in (2, 3):
        for i in range(len(stream) - span + 1):
            ph = " ".join(stream[i:i + span])
            if ph not in phrases[span]:
                phrases[span].append(ph)
    # Class-capped assembly: single content words first (the
    # strongest exact-match signal), then bigrams, then
    # trigrams; growth registrations ride along uncapped.
    terms = []
    for group in (singles[:8], phrases[2][:6], phrases[3][:4]):
        for t in group:
            if t not in terms:
                terms.append(t)
    for mapped in _growth_expansions(ql):
        if mapped not in terms:
            terms.append(mapped)
    return terms


_AUTHORITY = {"policy manual": 4, "POMS operations manual": 4,
              "regulation": 4, "waiver application": 3,
              "IHCP bulletin": 3, "SSA explainer": 2,
              "plan handbook": 1, "program page": 1}


def _rescue_search(question, corpus, exclude_keys=(), limit=4):
    """Scan the corpus's chunks for the question's rare terms.
    Returns (hits, terms): at most `limit` chunks, at most 2
    per document, excluding `exclude_keys` ((url, excerpt[:60])
    pairs already in hand). A chunk qualifies when it carries
    at least 2 distinct question terms, or a single term that
    is rare across the corpus (in at most 5 documents). One
    pass computes document frequency and candidates together;
    cost is a full chunk scan, paid only when the rescue
    actually fires."""
    terms = _rare_terms(question)
    if not terms:
        return [], terms
    docs = corpus if corpus is not None else CORPUS_IN
    total_docs = max(1, len(docs))
    df = {t: 0 for t in terms}
    cands = []
    for d in docs:
        # The match text for every chunk is the document
        # TITLE plus the chunk text (which itself carries
        # its section-heading prefix from _chunks for
        # manual-style docs). Title inclusion is load-
        # bearing, not cosmetic: the transfer divisor lives
        # in IHCPPM 3006.00.00's TITLE ("(Transfer Penalty
        # Divisor)") while its body text never uses the
        # word "divisor" at all — a body-only matcher can
        # never rescue that question on the term (probe-set
        # grounding, 2026-10-14).
        title_low = (d.get("title") or "").lower()
        doc_terms = set()
        for ch in _doc_chunks(d):
            low = title_low + " " + ch.lower()
            matched = [t for t in terms if t in low]
            if not matched:
                continue
            doc_terms.update(matched)
            key = (d["url"], ch[:60])
            if key in exclude_keys:
                continue
            cands.append((d, ch, matched))
        for t in doc_terms:
            df[t] += 1
    import math
    rarity = {t: math.log(1.0 + total_docs / (1.0 + df[t]))
              for t in terms}
    scored = []
    for d, ch, matched in cands:
        distinct = len(set(matched))
        if distinct < 2 and not any(df[t] <= 5 for t in matched):
            continue
        hit = {"title": d["title"], "url": d["url"],
               "source": d.get("source", "pdf"),
               "verified": d.get("verified", True),
               "excerpt": ch[:2000]}
        if d.get("tags"):
            hit["tags"] = d["tags"]
        _p, dtype = _classify_hit(hit)
        auth = _AUTHORITY.get(dtype, 1)
        if _is_indiana_source(hit):
            auth += 0.5
        # Scoring: RARITY-WEIGHTED co-occurrence first (the
        # sum of the matched terms' rarity), then the raw
        # distinct count, then authority. A raw distinct
        # count alone misranks: on the eyeglasses question a
        # generic Indiana page matches five COMMON terms
        # (fee, adults, "indiana medicaid", ...) and would
        # beat the Vision module chunk's three RARE ones
        # (eyeglasses, vision, promod00051) — the exact
        # failure this pass exists to fix, reproduced in
        # tools/offline_checks.py. Weighting by rarity keeps
        # co-occurrence in charge (a chunk must still carry
        # several of the question's terms) while making the
        # rare terms count for what they are worth.
        score = (round(sum(rarity[t] for t in
                           set(matched)), 3), distinct, auth)
        scored.append((score, hit))
    scored.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))
    out, per_doc = [], {}
    for _score, hit in scored:
        if len(out) >= limit:
            break
        if per_doc.get(hit["url"], 0) >= 2:
            continue
        per_doc[hit["url"]] = per_doc.get(hit["url"], 0) + 1
        out.append(hit)
    return out, terms





# ---------------------------------------------------------------------------
# v22 retrieval: generation-backed, in-process (SQLite FTS5 weighted
# BM25F + the existing nomic vectors + synthetic-question vectors).
# v23 (WS3.2): this is now the ONLY retrieval engine. The
# RETRIEVAL env flag and the legacy keyword/vector fusion path
# are DELETED; there is nothing behind this to fall back to,
# so a store that cannot open leaves the engine DOWN and
# retrieval calls fail closed (/health says why). Channels: T (FTS5 BM25F over sections and chunks,
# query rewrites included), V1 (chunk vectors), V2 (synthetic-
# question vectors), R (registry entries resolved to their source
# section BY DOC ID from the generation's registry_sources table),
# X (exact: section numbers, module ids, quoted phrases, and the
# v21 rescue scoring mapped to its document's best section).
# Weighted RRF fusion (k from retrieval_profile.json), section
# aggregation with a channel-agreement bonus, small priors
# (authority tier, jurisdiction from content, intent features).
# There are NO title-word demotions in this path.
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3

_V2_LOCK = threading.Lock()
_V2_QLOCK = threading.Lock()
_V2_CACHE = {}
_V2 = {"requested": None, "effective": "down", "fts5": None,
       "generation": None, "vectors": "unknown", "canary": "n/a",
       # v30: the active generation's embedder contract
       # (recorded from its meta table at open; see
       # _active_embed_spec).
       "embed_model": None, "embed_recipe": None,
       "embed_query_instruction": "",
       "embed_dim": None,
       "drift": False, "fail_reason": None, "profile": None,
       "conn": None, "sections": {}, "docs": {}, "enrich": {},
       "vec_mat": None, "vec_rows": None, "synth_mat": None,
       "synth_sections": None, "vocab": None, "pins": [],
       # v26 (F12): the bounded re-open bookkeeping.
       "fail_ts": None, "open_attempts": 0}
# v26 (F12): a failed store open may be retried after this
# cooldown, at most this many attempts per process. The
# bound keeps a persistently broken store failing closed
# (every ask 503s) without a restart being the only cure
# for a transient one (a generation still being written
# when the server started).
_V2_REOPEN_COOLDOWN_S = 30.0
_V2_REOPEN_MAX_ATTEMPTS = 4


def _v2_fts5_ok():
    try:
        _c = _sqlite3.connect(":memory:")
        _c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        _c.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'row')")
        _c.close()
        return True
    except Exception:
        return False


def _v2_health():
    """The /health retrieval block. Never raises."""
    try:
        return {"engine": "v2",
                "effective": _V2["effective"],
                "fts5": _V2["fts5"],
                "generation": _V2["generation"],
                "vectors": _V2["vectors"],
                "embed_model": _V2.get("embed_model"),
                "embed_recipe": _V2.get("embed_recipe"),
                "embed_dim": _V2.get("embed_dim"),
                "rerank_ce": {
                    "backend": RERANK_BACKEND,
                    "url": RERANK_CE_URL,
                    "last_ok":
                    _RERANK_CE_STATE["last_ok"],
                    "last_error":
                    _RERANK_CE_STATE["last_error"],
                    "consecutive_failures":
                    _RERANK_CE_STATE[
                        "consecutive_failures"]},
                "embedding_canary": _V2["canary"],
                "embedding_drift": bool(_V2["drift"]),
                "fail_reason": _V2["fail_reason"],
                "profile": (_V2["profile"] or {}).get("version")}
    except Exception:
        return {"engine": "v2", "effective": "down",
                "error": "state unavailable"}


def _v2_canary_thread():
    """Boot canary: re-embed 5 stored chunks and compare against
    their stored rows. A measured cosine < 0.999 on any of them
    sets embedding_drift and disables the vector channels (text
    channels continue). If the embedder is unreachable the canary
    is 'unrun', not a drift verdict."""
    try:
        conn = _V2["conn"]
        with _V2_QLOCK:
            picks = []
            for off in (0, 1000, 30000, 60000):
                r = conn.execute(
                    "SELECT row FROM chunks ORDER BY row"
                    " LIMIT 1 OFFSET ?", (off,)).fetchone()
                if r:
                    picks.append(r[0])
            r = conn.execute(
                "SELECT MAX(row) FROM chunks").fetchone()
            if r and r[0] is not None:
                picks.append(r[0])
            picks = list(dict.fromkeys(picks))[:5]
            texts = {}
            for r in picks:
                row = conn.execute(
                    "SELECT c.text, d.title, s.title FROM chunks c"
                    " JOIN docs d ON d.doc_id = c.doc_id"
                    " JOIN sections s"
                    " ON s.section_id = c.section_id"
                    " WHERE c.row=?", (r,)).fetchone()
                if row:
                    # v30.1: the probe must be embedded the
                    # way the build embedded the chunk. For a
                    # titlectx1 generation that is the title
                    # context line + the stored text; other
                    # generations stored raw-text vectors and
                    # keep the raw probe.
                    text = row[0]
                    if _V2.get("embed_recipe") == "titlectx1":
                        _doc = row[1] or ""
                        _sec = row[2] or ""
                        if _doc and _sec:
                            text = f"{_doc} > {_sec}\n{text}"
                        elif _doc:
                            text = f"{_doc}\n{text}"
                    texts[r] = text
        mat, vrows = _V2["vec_mat"], _V2["vec_rows"]
        measured = 0
        for r, text in texts.items():
            q = _embed_query(text, document=True)
            if q is None:
                continue
            i = vrows.index(r) if r in vrows else None
            if i is None:
                continue
            cos = _cosine(q, [float(x) for x in mat[i]])
            measured += 1
            if cos < 0.999:
                _V2["drift"] = True
                _V2["canary"] = "drift"
                print(f"[v22] embedding canary: DRIFT (cosine "
                      f"{cos:.4f} < 0.999 on chunk row {r}); "
                      f"vector channels disabled", flush=True)
                return
        _V2["canary"] = "ok" if measured else "unrun"
        print(f"[v22] embedding canary: {_V2['canary']} "
              f"({measured} chunks measured)", flush=True)
    except Exception as e:
        _V2["canary"] = "unrun"
        print(f"[v22] embedding canary failed to run: "
              f"{type(e).__name__}: {e}", flush=True)


def _v2_open():
    """Open the generation store. Returns True when the
    v2 path may serve. v23 (WS3.2): there is no RETRIEVAL
    flag and no legacy engine behind it; any failure leaves
    the engine DOWN (effective='down', fail_reason set) and
    retrieval calls fail closed. v26 (F12): a failed open
    is no longer single-attempt-forever — the failure time
    is recorded and the open retries after a short
    cooldown, a small number of attempts per process
    (_V2_REOPEN_*). While unopened, asks still fail closed
    exactly as before; a retry either completes the open
    and swaps the store in, or the down verdict stands."""
    with _V2_LOCK:
        if _V2["effective"] == "v2":
            return True
        if _V2["requested"] is not None:
            _now = time.monotonic()
            if _V2["open_attempts"] >= _V2_REOPEN_MAX_ATTEMPTS \
                    or _V2["fail_ts"] is None \
                    or (_now - _V2["fail_ts"]) \
                    < _V2_REOPEN_COOLDOWN_S:
                return False
            # A retry: drop any half-open connection from
            # the failed attempt before re-running the open.
            if _V2["conn"] is not None:
                try:
                    _V2["conn"].close()
                except Exception:
                    pass
                _V2["conn"] = None
        _V2["requested"] = "v2"
        _V2["open_attempts"] += 1
        _V2["fts5"] = _v2_fts5_ok()
        if not _V2["fts5"]:
            _V2["fail_reason"] = "FTS5 unavailable in this sqlite3"
            _V2["effective"] = "down"
            _V2["fail_ts"] = time.monotonic()
            print("[v23] FTS5 unavailable in this sqlite3; "
                  "the retrieval engine is DOWN", flush=True)
            return False
        try:
            base = os.environ.get("FSSA_GENERATIONS_DIR") or os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "generations")
            gen = open(os.path.join(base, "CURRENT"),
                       encoding="utf-8").read().strip()
            db = os.path.join(base, gen, f"corpus_{gen}.db")
            conn = _sqlite3.connect(f"file:{db}?mode=ro",
                                    uri=True, check_same_thread=False)
            conn.execute("SELECT COUNT(*) FROM sections").fetchone()
            _V2["conn"] = conn
            _V2["generation"] = gen
            # Meta values are JSON where the builder wrote
            # JSON (g0007) and raw strings where it did not
            # (g0008's embed keys) — parse tolerantly.
            def _meta_val(v):
                try:
                    return json.loads(v)
                except Exception:
                    return v
            meta = {k: _meta_val(v) for k, v in conn.execute(
                "SELECT key, value FROM meta")}
            _V2["vectors"] = meta.get("vectors_state", "unknown")
            # v30: the generation's embedder contract
            # (keys per the corpus workstream's build:
            # embed_model / embed_recipe / embed_dims /
            # embed_query_instruction). Generations built
            # before these keys existed read as the
            # historical nomic defaults, so a CURRENT flip
            # across the v30 boundary restores the matching
            # query embedder in both directions. The model
            # string is the pinned tag (quantization
            # included); embed_dims is the native
            # dimension (2560 for g0008, no truncation).
            _V2["embed_model"] = meta.get(
                "embed_model") or _EMBED_DEFAULT_MODEL
            _V2["embed_recipe"] = meta.get("embed_recipe")
            _V2["embed_query_instruction"] = meta.get(
                "embed_query_instruction") or ""
            _ed = meta.get("embed_dims")
            _V2["embed_dim"] = int(_ed) if _ed else None
            # v30 (fact-sheet Flag 5): an operator pin
            # (FSSA_EMBED_MODEL_PIN) refuses to serve a
            # generation whose recorded embedder differs
            # from the configured one — fail closed, like
            # every other open failure.
            _pin = os.environ.get("FSSA_EMBED_MODEL_PIN")
            if _pin and meta.get("embed_model") \
                    and str(meta["embed_model"]) != _pin:
                _V2["fail_reason"] = (
                    "embed_model pin mismatch: generation "
                    f"records {meta['embed_model']}, pin "
                    f"is {_pin}")
                _V2["effective"] = "down"
                _V2["fail_ts"] = time.monotonic()
                print("[v30] " + _V2["fail_reason"]
                      + "; the retrieval engine is DOWN",
                      flush=True)
                return False
            for (sid, did, skey, heading, title) in conn.execute(
                    "SELECT section_id, doc_id, section_key, heading,"
                    " title FROM sections"):
                _V2["sections"][sid] = {
                    "doc_id": did, "section_key": skey,
                    "heading": heading, "title": title}
            for (did, url, title, sf, jur, tier) in conn.execute(
                    "SELECT doc_id, url, title, source_file,"
                    " jurisdiction, authority_tier FROM docs"):
                _V2["docs"][did] = {
                    "url": url, "title": title, "source_file": sf,
                    "jurisdiction": jur, "authority_tier": tier}
            for sid, figs in conn.execute(
                    "SELECT section_id, figures FROM enrich"):
                try:
                    _V2["enrich"][sid] = json.loads(figs or "[]")
                except Exception:
                    _V2["enrich"][sid] = []
            sdir = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(sdir, "retrieval_profile.json"),
                      encoding="utf-8") as f:
                _V2["profile"] = json.load(f)
            try:
                with open(os.path.join(sdir, "pins.json"),
                          encoding="utf-8") as f:
                    _V2["pins"] = json.load(f).get("pins", [])
            except FileNotFoundError:
                _V2["pins"] = []
            if _V2["vectors"] == "ready" and _HAS_NUMPY:
                import numpy as _npy
                gdir = os.path.join(base, gen)
                _V2["vec_mat"] = _npy.load(
                    os.path.join(gdir, f"vectors_{gen}.npy"),
                    mmap_mode="r")
                # v30: the store's dimension is whatever
                # the generation carries (meta when the
                # build recorded it, else the matrix
                # itself) — never a literal.
                if not _V2["embed_dim"]:
                    _V2["embed_dim"] = int(
                        _V2["vec_mat"].shape[1])
                with open(os.path.join(gdir, "vectors_index.json"),
                          encoding="utf-8") as f:
                    _V2["vec_rows"] = json.load(f)["rows"]
                sq = os.path.join(gdir, f"synthq_vectors_{gen}.npy")
                if os.path.exists(sq):
                    _V2["synth_mat"] = _npy.load(sq, mmap_mode="r")
                    with open(os.path.join(
                            gdir, "synthq_index.json"),
                            encoding="utf-8") as f:
                        _V2["synth_sections"] = json.load(f)
                _V2["canary"] = "pending"
                threading.Thread(target=_v2_canary_thread,
                                 daemon=True).start()
            _V2["effective"] = "v2"
            print(f"[v22] retrieval v2 open: generation {gen}, "
                  f"vectors {_V2['vectors']}, "
                  f"{len(_V2['sections'])} sections", flush=True)
            # Warm the typo vocabulary in the background so the
            # first v2 ask does not pay the fts5vocab build.
            threading.Thread(target=lambda: _v2_vocab(),
                             daemon=True).start()
            return True
        except Exception as e:
            _V2["fail_reason"] = f"{type(e).__name__}: {e}"
            _V2["effective"] = "down"
            _V2["fail_ts"] = time.monotonic()
            print(f"[v23] generation store failed to open "
                  f"({_V2['fail_reason']}); the retrieval "
                  f"engine is DOWN", flush=True)
            return False


def _v2_scope(corpus):
    if corpus is None or corpus is CORPUS_IN:
        return "in"
    if corpus is CORPUS_FED:
        return "federal"
    return "both"


def _v2_match(terms):
    parts = []
    for t in terms:
        t = (t or "").strip().lower().replace('"', " ")
        if t and all(c.isalnum() or c in " .'-" for c in t):
            parts.append(f'"{t}"')
    return " OR ".join(parts)


def _v2_vocab():
    """Corpus vocabulary with chunk counts: the generation's vocab
    table, materialized from fts5vocab at generation build time
    (a runtime fts5vocab table cannot attach to the read-only
    store). Loaded lazily, once."""
    if _V2["vocab"] is not None:
        return _V2["vocab"]
    vocab = {}
    with _V2_QLOCK:
        for term, dc in _V2["conn"].execute(
                "SELECT term, doc_count FROM vocab"):
            vocab[term] = dc
    _V2["vocab"] = vocab
    return vocab


def _v2_lev(a, b, cap=2):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = cur[0]
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb))
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _v2_typo_expansions(terms):
    """fts5vocab typo handling (consult design): a query term that
    is out-of-vocabulary or rare (df <= 3) earns OR-expansion
    candidates within edit distance 2 that are at least 20x more
    frequent AND co-occur in some chunk with another query term.
    Expansion only; the original term is never replaced."""
    try:
        vocab = _v2_vocab()
    except Exception:
        return []
    out = []
    others = [t for t in terms if " " not in t]
    with _V2_QLOCK:
        conn = _V2["conn"]
        for t in others:
            if len(t) < 4:
                continue
            df = vocab.get(t, 0)
            if df > 3:
                continue
            cands = []
            for cand, cdf in vocab.items():
                if cand == t or abs(len(cand) - len(t)) > 2:
                    continue
                if cand[0] != t[0] or cdf < max(50, 20 * max(df, 1)):
                    continue
                if _v2_lev(t, cand) <= 2:
                    cands.append((cdf, cand))
            cands.sort(reverse=True)
            for _cdf, cand in cands[:4]:
                co = False
                for o in others:
                    if o == t:
                        continue
                    try:
                        hit = conn.execute(
                            "SELECT 1 FROM chunk_fts WHERE"
                            " chunk_fts MATCH ? LIMIT 1",
                            (f'"{cand}" AND "{o}"',)).fetchone()
                    except Exception:
                        hit = None
                    if hit:
                        co = True
                        break
                if co:
                    out.append(cand)
                    break
            if len(out) >= 6:
                break
    return out


def _v2_fts_sections(match, scope, limit, doc_id=None):
    """Section ids by section_fts BM25F, best first."""
    if not match:
        return []
    w = (_V2["profile"] or {}).get(
        "fts_weights", {}).get("section", [5.0, 3.0, 1.0, 2.0, 3.0])
    sql = ("SELECT s.section_id FROM section_fts f"
           " JOIN sections s ON s.row = f.rowid"
           " JOIN docs d ON d.doc_id = s.doc_id"
           " WHERE section_fts MATCH ?")
    args = [match]
    if doc_id is not None:
        sql += " AND s.doc_id = ?"
        args.append(doc_id)
    elif scope != "both":
        sql += " AND d.source_file = ?"
        args.append(scope)
    sql += (f" ORDER BY bm25(section_fts, {w[0]}, {w[1]}, {w[2]},"
            f" {w[3]}, {w[4]}) LIMIT ?")
    args.append(limit)
    with _V2_QLOCK:
        return [r[0] for r in _V2["conn"].execute(sql, args)]


def _v2_fts_chunk_sections(match, scope, limit):
    """Section ids via the best chunks (chunk_fts BM25F)."""
    if not match:
        return []
    w = (_V2["profile"] or {}).get(
        "fts_weights", {}).get("chunk", [5.0, 3.0, 1.0])
    sql = ("SELECT c.section_id FROM chunk_fts f"
           " JOIN chunks c ON c.row = f.rowid"
           " JOIN docs d ON d.doc_id = c.doc_id"
           " WHERE chunk_fts MATCH ?")
    args = [match]
    if scope != "both":
        sql += " AND d.source_file = ?"
        args.append(scope)
    sql += (f" ORDER BY bm25(chunk_fts, {w[0]}, {w[1]}, {w[2]})"
            f" LIMIT ?")
    args.append(limit * 3)
    out, seen = [], set()
    with _V2_QLOCK:
        for (sid,) in _V2["conn"].execute(sql, args):
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
                if len(out) >= limit:
                    break
    return out


def _v2_vector_sections(mat, index_sections, query, scope, limit,
                        chunk_level=False):
    """Dense channel: query vector against a stored matrix. When
    chunk_level, matrix rows are chunks (index maps row->chunk row)
    and the result is (section ids, {section_id: chunk_row})."""
    q = _embed_query(query)
    if q is None or mat is None:
        return [], {}
    import numpy as _npy
    qv = _npy.array(q, dtype=_npy.float32)
    nrm = float(_npy.linalg.norm(qv))
    if not nrm:
        return [], {}
    qv = qv / nrm
    if qv.shape[0] != mat.shape[1]:
        # v30: the query embedder disagrees with the
        # store's dimension (a mixed-generation state —
        # the embed_model scoping above exists to prevent
        # it, and the boot canary records the drift
        # verdict). The dense channel abstains instead of
        # raising on the matmul; text channels carry.
        return [], {}
    scores = mat @ qv
    order = _npy.argsort(-scores)
    out, seen, anchors = [], set(), {}
    conn = _V2["conn"]
    with _V2_QLOCK:
        for i in order:
            if chunk_level:
                crow = _V2["vec_rows"][int(i)]
                row = conn.execute(
                    "SELECT section_id, doc_id FROM chunks"
                    " WHERE row=?", (crow,)).fetchone()
                if not row:
                    continue
                sid, did = row
                anchors.setdefault(sid, crow)
            else:
                sid = index_sections[int(i)]
                row = conn.execute(
                    "SELECT doc_id FROM sections WHERE section_id=?",
                    (sid,)).fetchone()
                if not row:
                    continue
                did = row[0]
            if scope != "both" and \
                    _V2["docs"][did]["source_file"] != scope:
                continue
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
                if len(out) >= limit:
                    break
    return out, anchors


def _v2_registry_sections(question, scope="both"):
    """R channel: date-aware registry matches resolved to their
    source section BY DOC ID (the generation's registry_sources
    table), ranked inside the doc by the entry's topic phrases."""
    out = []
    try:
        entries = _registry_matches(question)
    except Exception:
        return out
    conn = _V2["conn"]
    for e in entries[:2]:
        with _V2_QLOCK:
            row = conn.execute(
                "SELECT doc_id FROM registry_sources"
                " WHERE entry_id=?", (e["id"],)).fetchone()
        if not row or row[0] is None:
            continue
        doc_id = row[0]
        if scope != "both" and \
                _V2["docs"][doc_id]["source_file"] != scope:
            continue
        topic = e.get("topic") or _V21_REGISTRY_META.get(
            e["id"], {}).get("topic") or {}
        phrases = [p for p, _t in topic.get("phrases", ())]
        secs = _v2_fts_sections(_v2_match(phrases), "both", 2,
                                doc_id=doc_id)
        if not secs:
            with _V2_QLOCK:
                secs = [r[0] for r in conn.execute(
                    "SELECT section_id FROM sections"
                    " WHERE doc_id=? ORDER BY first_ordinal"
                    " LIMIT 1", (doc_id,))]
        for sid in secs:
            if sid not in out:
                out.append(sid)
    return out


def _v2_exact_sections(question):
    """X channel: section numbers / module ids in the question,
    quoted phrases, and the v21 rescue's best document."""
    out = []
    conn = _V2["conn"]
    tokens = set(re.findall(
        r"\b\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?\b", question))
    tokens |= {m.upper() for m in re.findall(
        r"\bPROMOD\d+\b", question, re.IGNORECASE)}
    with _V2_QLOCK:
        for tok in tokens:
            for (sid,) in conn.execute(
                    "SELECT s.section_id FROM sections s"
                    " JOIN docs d ON d.doc_id = s.doc_id"
                    " WHERE s.section_key LIKE ? OR d.title LIKE ?"
                    " LIMIT 4",
                    (f"%{tok}%", f"%{tok}%")):
                if sid not in out:
                    out.append(sid)
    for phrase in re.findall(r'"([^"]{3,})"', question):
        for sid in _v2_fts_sections(f'"{phrase}"', "both", 3):
            if sid not in out:
                out.append(sid)
    return out


def _v2_section_hit(sid, match, anchors=None):
    """Build a served hit for a section: the anchor chunk (the
    vector hit chunk when known, else the section's best FTS
    chunk, else its first chunk) plus its same-section neighbours,
    as one evidence window. Returns None for an unknown section."""
    meta = _V2["sections"].get(sid)
    if not meta:
        return None
    conn = _V2["conn"]
    with _V2_QLOCK:
        chunks = conn.execute(
            "SELECT row, ordinal, text FROM chunks"
            " WHERE section_id=? ORDER BY ordinal", (sid,)).fetchall()
    if not chunks:
        return None
    anchor_row = (anchors or {}).get(sid)
    if anchor_row is None and match:
        w = (_V2["profile"] or {}).get(
            "fts_weights", {}).get("chunk", [5.0, 3.0, 1.0])
        with _V2_QLOCK:
            row = conn.execute(
                "SELECT c.row FROM chunk_fts f"
                " JOIN chunks c ON c.row = f.rowid"
                " WHERE c.section_id=? AND chunk_fts MATCH ?"
                f" ORDER BY bm25(chunk_fts, {w[0]}, {w[1]}, {w[2]})"
                " LIMIT 1", (sid, match)).fetchone()
        anchor_row = row[0] if row else None
    idx = 0
    if anchor_row is not None:
        for i, (r, _o, _t) in enumerate(chunks):
            if r == anchor_row:
                idx = i
                break
    lo = max(0, idx - 1)
    hi = min(len(chunks), idx + 2)
    excerpt = "\n".join(t for _r, _o, t in chunks[lo:hi])[:2000]
    doc = _V2["docs"][meta["doc_id"]]
    hit = {"title": doc["title"], "url": doc["url"],
           "source": "v2", "verified": True, "excerpt": excerpt,
           "_section": sid}
    return hit


# --- v27 (Change 3a): DDARS-manual rule prior --------------------
# When the question carries DD-waiver rule signals, sections of
# the DDARS HCBS Waivers Provider Manual get a multiplicative
# prior from the retrieval profile (priors.ddars_manual_rule_
# prior; absent key = 1.0, a no-op). The preference is a lift
# inside DD scope only: no other source is demoted, and the
# manual is identified from the loaded doc metadata (title /
# source_file in _V2["docs"]), never from URL patterns.
_DD_RULE_PHRASES = (
    "family supports", "community integration and habilitation",
    "fs waiver", "family supports waiver", "waiver waiting list",
    "waiting list", "structured family caregiving",
    "residential habilitation", "participant assistance and care",
    "vehicle modification", "vehicle modifications",
    "developmental disability", "developmental disabilities",
    "waiver slot", "reserved capacity", "priority criteria",
    "community integration", "habilitation waiver",
)
_DD_RULE_TOKENS = {"cih", "ddars", "respite", "bds"}


def _dd_rule_question(q_low):
    """True when the (lowercased) question carries a DD-waiver
    rule signal: a manual/program phrase, or a standalone DD
    token (exact-token, so 'cih' never fires inside a word)."""
    if any(p in q_low for p in _DD_RULE_PHRASES):
        return True
    toks = set(re.findall(r"[a-z]+", q_low))
    return bool(toks & _DD_RULE_TOKENS)


def _is_ddars_manual_doc(doc):
    """The DDARS HCBS Waivers Provider Manual, from the doc
    metadata recorded at corpus build: 'hcbs waivers' or
    'ddars' in the title or the source_file value."""
    title = ((doc or {}).get("title") or "").lower()
    sf = ((doc or {}).get("source_file") or "").lower()
    return ("hcbs waivers" in title or "ddars" in title
            or "hcbs waivers" in sf or "ddars" in sf)


def _hybrid_search_v2(query, corpus, n, use_rewrite, use_rerank):
    """The v22 ask-path retrieval. Same return contract as
    hybrid_search: the top-n served hits."""
    _t0 = time.monotonic()
    scope = _v2_scope(corpus)
    gen = _V2["generation"]
    # v26 (F11): use_rewrite rides the key — through v25.1
    # a rewrite-on pool could serve a rewrite-off ask (and
    # vice versa) whenever the other inputs matched.
    cache_key = (query, scope, n, use_rewrite, use_rerank, gen)
    cached = _cache_get(_V2_CACHE, cache_key)
    if cached:
        _retr_pool_note({"q": query[:80], "n": n, "cached": True,
                         "engine": "v2", "gen": gen,
                         # v27 (Change 2): a cached pool carries
                         # no fresh band/registry/pin signal; the
                         # fields ride as None (unknown) so the
                         # thin test never reads them as False.
                         "flat": None, "registry_hit": None,
                         "pin_fired": None,
                         "pool": [{"t": (h.get("title") or "")[:70],
                                   "u": h.get("url"), "sel": i}
                                  for i, h in enumerate(cached[:28])]})
        return [dict(h) for h in cached]
    profile = _V2["profile"] or {}
    weights = profile.get("channel_weights",
                          {"T": 1.0, "V1": 1.0, "V2": 1.1,
                           "R": 1.2, "X": 1.2})
    depths = profile.get("channel_depths", {})
    rrf_k = profile.get("rrf_k", 60)

    q_low = query.lower()
    terms = keywords(query)
    expansions = []
    for t in list(terms):
        for w in SYNONYMS.get(t, ()):  # acronym expansions, as legacy
            if w not in terms and w not in expansions:
                expansions.append(w)
    for gt in _growth_expansions(q_low):
        if gt not in terms and gt not in expansions:
            expansions.append(gt)
    typo_alts = _v2_typo_expansions(terms)
    expansions += [t for t in typo_alts if t not in expansions]
    all_terms = terms + expansions
    match = _v2_match(all_terms)
    rewrite_match = ""
    if use_rewrite and REWRITE_ENABLED:
        try:
            rw = rewrite_query(query)
        except Exception:
            rw = []
        rewrite_match = _v2_match(
            [t for t in rw if t not in all_terms])

    channels = {}   # name -> [section_id, best first]
    anchors = {}
    t_secs = _v2_fts_sections(match, scope,
                              depths.get("T_sections", 60))
    c_secs = _v2_fts_chunk_sections(match, scope,
                                    depths.get("T_sections", 60))
    merged_t, _seen = [], set()
    for sid in t_secs + c_secs:
        if sid not in _seen:
            _seen.add(sid)
            merged_t.append(sid)
    if rewrite_match:
        for sid in _v2_fts_sections(rewrite_match, scope,
                                    depths.get("T_sections", 60) // 2):
            if sid not in _seen:
                _seen.add(sid)
                merged_t.append(sid)
    channels["T"] = merged_t
    if not _V2["drift"] and _V2["vec_mat"] is not None:
        v1, anchors = _v2_vector_sections(
            _V2["vec_mat"], None, query, scope,
            depths.get("V1_chunks", 60), chunk_level=True)
        channels["V1"] = v1
    if not _V2["drift"] and _V2["synth_mat"] is not None:
        v2s, _a = _v2_vector_sections(
            _V2["synth_mat"], _V2["synth_sections"], query, scope,
            depths.get("V2_questions", 60))
        channels["V2"] = v2s
    channels["R"] = _v2_registry_sections(query, scope)
    channels["X"] = _v2_exact_sections(query)

    def fuse(channels):
        scores, present = {}, {}
        for name, lst in channels.items():
            w = weights.get(name, 1.0)
            for rank, sid in enumerate(lst):
                scores[sid] = scores.get(sid, 0.0) \
                    + w / (rank + rrf_k)
                present[sid] = present.get(sid, 0) + 1
        bonus = profile.get("agreement_bonus", 0.004)
        for sid, cnt in present.items():
            if cnt > 1:
                scores[sid] += bonus * (cnt - 1)
        return scores, present

    scores, present = fuse(channels)
    # Round 2 (the answerability loop): a flat, low fused band is
    # the divisor pool's signature. Widen once with the v21 rescue
    # (its best documents' best sections join channel X) and the
    # concept-map terms, then re-fuse. Bounded to one round.
    round2 = False
    ordered = sorted(scores.values(), reverse=True)
    flat = bool(ordered) and (
        ordered[0] < 0.045
        or (len(ordered) >= 4 and ordered[0] - ordered[3] < 0.003))
    if flat:
        try:
            rescue_hits, _rt = _rescue_search(
                query, corpus if corpus is not None else CORPUS_IN,
                exclude_keys=(), limit=4)
            extra = []
            conn = _V2["conn"]
            for h in rescue_hits:
                with _V2_QLOCK:
                    drow = conn.execute(
                        "SELECT doc_id FROM docs WHERE url=?",
                        (h["url"],)).fetchone()
                if not drow:
                    continue
                for sid in _v2_fts_sections(match, scope, 1,
                                            doc_id=drow[0]):
                    if sid not in extra:
                        extra.append(sid)
            if extra:
                channels["X"] = channels["X"] + [
                    s for s in extra if s not in channels["X"]]
                scores, present = fuse(channels)
                round2 = True
        except Exception:
            pass

    # Priors (small, multiplicative, from the profile).
    pr = profile.get("priors", {})
    indiana_scoped = "indiana" in q_low or "hoosier" in q_low
    freq_q = any(w in q_low for w in
                 ("how often", "how frequently", "how long",
                  "every ", "expire", "lookback", "look-back"))
    figure_q = any(w in q_low for w in
                   ("limit", "cost", "amount", "much", "income",
                    "pay", "rate", "dollar", "price", "fee",
                    "premium", "allowance", "keep"))
    # v27 (Change 3a): the DDARS-manual rule prior, computed
    # once per search (question signal + the profile value).
    dd_rule_q = _dd_rule_question(q_low)
    dd_prior = pr.get("ddars_manual_rule_prior", 1.0) \
        if dd_rule_q else 1.0
    for sid, sc in list(scores.items()):
        meta = _V2["sections"].get(sid)
        if not meta:
            continue
        doc = _V2["docs"][meta["doc_id"]]
        m = 1.0 + pr.get("authority_per_tier_above_2", 0.05) * max(
            0, doc["authority_tier"] - 2)
        if indiana_scoped:
            if doc["jurisdiction"] == "IN":
                m *= pr.get("jurisdiction_match", 1.05)
            elif doc["jurisdiction"] == "other-state":
                m *= pr.get("other_state_when_indiana_scoped", 0.8)
        figs = _V2["enrich"].get(sid) or []
        if freq_q and any(f.get("kind") == "frequency"
                           for f in figs):
            m *= pr.get("frequency_intent_with_frequency_figure", 1.1)
        if figure_q and figs:
            m *= pr.get("figure_intent_with_figures", 1.05)
        if dd_prior != 1.0 and _is_ddars_manual_doc(doc):
            m *= dd_prior
        scores[sid] = sc * m

    ranked = sorted(scores, key=lambda s: -scores[s])
    window = ranked[:max(24, n * 3)]
    hits, url_counts = [], {}
    for sid in window:
        meta = _V2["sections"][sid]
        url = _V2["docs"][meta["doc_id"]]["url"]
        if url_counts.get(url, 0) >= 2:
            continue
        h = _v2_section_hit(sid, match, anchors)
        if h is None:
            continue
        url_counts[url] = url_counts.get(url, 0) + 1
        hits.append(h)
    result = hits[:max(12, n * 2)]
    if use_rerank and RERANK_ENABLED and len(result) > n:
        _pool = list(result)
        ranked_hits = []
        _backend = "fused"
        if RERANK_BACKEND == "ce":
            ranked_hits = rerank_with_cross_encoder(
                query, result)
            if ranked_hits:
                _backend = "ce"
        if not ranked_hits:
            ranked_hits = rerank_with_llm(query, result)
            if ranked_hits:
                _backend = "llm"
        if ranked_hits:
            result = ranked_hits[:n]
        if _POOL_EXPORT_PATH:
            _export_rerank_pool(query, _pool, ranked_hits,
                                _backend, n)
    if len(result) > n:
        result = result[:n]

    # Pins, in the legacy served order: pins.json (the converted
    # bridges) first, then registry sections.
    def _front(hits_in):
        keys = {(h["url"], h["excerpt"][:60]) for h in hits_in}
        rest = [h for h in result
                if (h["url"], h["excerpt"][:60]) not in keys]
        return (hits_in + rest)[:n]

    q_tokens = set(re.findall(r"[a-z]+", q_low))
    pin_hits = []
    for pin in _V2["pins"]:
        if len(pin_hits) >= 3:
            break
        if not ((q_tokens & set(pin.get("trigger_tokens", ())))
                or any(t in q_low
                       for t in pin.get("trigger_texts", ()))):
            continue
        if pin.get("require_tokens") or pin.get("require_texts"):
            if not ((q_tokens & set(pin["require_tokens"]))
                    or any(t in q_low
                           for t in pin["require_texts"])):
                continue
        target = pin.get("target", {})
        chosen = None
        for spec in target.get("phrases", ()):
            cand_secs = _v2_fts_sections(
                _v2_match([spec["phrase"]]), scope, 6)
            cand_hits = []
            for sid in cand_secs:
                h = _v2_section_hit(sid, match, anchors)
                if h:
                    cand_hits.append(h)
            if not cand_hits:
                continue
            pt = (spec.get("pin_term") or "").lower()
            if pt:
                chosen = next(
                    (h for h in cand_hits
                     if pt in (h["title"] + " " + h["excerpt"]).lower()),
                    None)
            if chosen is None and target.get("program_scope"):
                chosen = next(
                    (h for h in cand_hits
                     if _classify_hit(h)[0]
                     == target["program_scope"]), None)
            if chosen is None:
                chosen = cand_hits[0]
            break
        if chosen is not None and all(
                (h["url"], h["excerpt"][:60])
                != (chosen["url"], chosen["excerpt"][:60])
                for h in pin_hits):
            pin_hits.append(chosen)
    if pin_hits:
        result = _front(pin_hits)
    reg_hits = []
    for sid in channels.get("R", [])[:2]:
        h = _v2_section_hit(sid, match, anchors)
        if h and all((x["url"], x["excerpt"][:60])
                     != (h["url"], h["excerpt"][:60])
                     for x in reg_hits + pin_hits):
            reg_hits.append(h)
    if reg_hits:
        result = _front(reg_hits)

    for h in result:
        prog, dtype = _classify_hit(h)
        h["program"] = prog
        h["dtype"] = dtype

    ranks = {name: {sid: i for i, sid in enumerate(lst)}
             for name, lst in channels.items()}
    sel = {(h["url"], h["excerpt"][:60]): i
           for i, h in enumerate(result)}
    pool = []
    for h in hits[:28]:
        k = (h["url"], h["excerpt"][:60])
        sid = h.get("_section")
        pool.append({
            "t": (h.get("title") or "")[:70], "u": h["url"],
            "s": round(scores.get(sid, 0.0), 4),
            "T": ranks.get("T", {}).get(sid),
            "V1": ranks.get("V1", {}).get(sid),
            "V2": ranks.get("V2", {}).get(sid),
            "R": ranks.get("R", {}).get(sid),
            "X": ranks.get("X", {}).get(sid),
            "w": True, "sel": sel.get(k)})
    _retr_pool_note({"q": query[:80], "n": n, "engine": "v2",
                     "gen": gen, "round2": round2,
                     # v27 (Change 2): the thin-result signals,
                     # surfaced mechanically on the pool note the
                     # function already writes (the v2 return
                     # contract is unchanged): the fused band
                     # was flat, a registry-channel section was
                     # in the fusion, a pin fired.
                     "flat": bool(flat),
                     "registry_hit": bool(channels.get("R")),
                     "pin_fired": bool(pin_hits),
                     "typo_expansions": typo_alts, "pool": pool})
    for h in result:
        h.pop("_section", None)
    _retr_note("v2", time.monotonic() - _t0)
    _cache_put(_V2_CACHE, cache_key, result)
    return [dict(h) for h in result]



def hybrid_search(query, corpus=None, n=6, use_rewrite=True,
                  use_rerank=True, boosts=True):
    """v23 (WS3.2): retrieval is the generation-backed v2
    path ONLY. The legacy keyword/vector fusion path and
    the RETRIEVAL env flag are DELETED: there is no second
    engine to fall back to, so a generation store that
    cannot open fails this call closed (RuntimeError) and
    /health reports the engine as down with the reason.
    Same return contract as always: the top-n served hits.
    The `boosts` argument is retained for caller
    compatibility; the v2 channels carry what the legacy
    boosts used to approximate (registry channel R, exact
    channel X, pins), for ask and plain search alike."""
    if not _v2_open():
        raise RuntimeError(
            "generation store unavailable: "
            + str(_V2["fail_reason"] or "not opened"))
    return _hybrid_search_v2(query, corpus, n, use_rewrite,
                             use_rerank)



def _export_rerank_pool(query, pool, ranked, backend, n):
    """v30: append one pool-export line for the binding
    rerank A/B (see _POOL_EXPORT_PATH). Candidates are
    exported in fused order, so fused_order is the
    identity and the winning rerank outcome is an index
    list into the candidates — the A/B harness's fixture
    shape (gold ids are joined later from battery keys).
    Log-only; never raises, never changes the ask."""
    try:
        pos = {id(h): i for i, h in enumerate(pool)}
        order = None
        if ranked:
            order = [pos[id(h)] for h in ranked
                     if id(h) in pos]
            if len(order) != len(pool):
                order = None
        rec = {
            "ts": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "query": query, "n": n, "backend": backend,
            "candidates": [
                {"section_id": h.get("_section")
                 or h.get("section_id"),
                 "title": h.get("title") or "",
                 "url": h.get("url") or "",
                 "passage": (h.get("title") or "") + ": "
                 + (h.get("excerpt") or "")[:400].replace(
                     "\n", " ")}
                for h in pool],
            "fused_order": list(range(len(pool))),
            "llm_order": order if backend == "llm"
            else None,
            "ce_order": order if backend == "ce"
            else None,
            "meta": {"fixture_quality": "exported_pool",
                     "pool_size": len(pool)},
        }
        line = json.dumps(rec) + "\n"
        with _POOL_EXPORT_LOCK:
            with open(_POOL_EXPORT_PATH, "a",
                      encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def rerank_with_cross_encoder(query, hits):
    """v30: cross-encoder rescoring of the fused window,
    per the reranker workstream's contract (POST
    {RERANK_CE_URL}/score, 3s default timeout, no retry).
    Returns the hits reordered by the service's scores
    (descending; fused order breaks ties), or [] on ANY
    failure — the caller then falls back to the LLM
    rerank, preserving the slot's fail-open contract.
    Every outcome is recorded in _RERANK_CE_STATE for
    /health: a silently dead reranker must be visible."""
    if not hits:
        return []
    try:
        scored = hits[:RERANK_CE_MAX_PASSAGES]
        rest = hits[RERANK_CE_MAX_PASSAGES:]
        passages = [
            (h.get("title") or "") + ": "
            + (h.get("excerpt") or "")[:400].replace(
                "\n", " ")
            for h in scored]
        payload = json.dumps(
            {"query": query, "passages": passages}).encode()
        req = urllib.request.Request(
            RERANK_CE_URL.rstrip("/") + "/score",
            data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(
                req, timeout=RERANK_CE_TIMEOUT) as resp:
            body = json.loads(resp.read())
        scores = body.get("scores") \
            if isinstance(body, dict) else body
        if not isinstance(scores, list) \
                or len(scores) != len(scored) \
                or any(not isinstance(s, (int, float))
                       or isinstance(s, bool)
                       or s != s
                       or s in (float("inf"), float("-inf"))
                       for s in scores):
            raise ValueError("bad scores shape")
        order = sorted(range(len(scored)),
                       key=lambda i: -float(scores[i]))
        _RERANK_CE_STATE.update(
            {"last_ok": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
             "last_error": None, "consecutive_failures": 0})
        return [scored[i] for i in order] + rest
    except Exception as e:
        _RERANK_CE_STATE.update(
            {"last_error":
             f"{type(e).__name__}: {e}"[:200],
             "consecutive_failures":
             _RERANK_CE_STATE["consecutive_failures"] + 1})
        return []


def rerank_with_llm(query, hits):
    """Rerank search hits by relevance to the query (v15.1: NIM
    fast tier; see _utility_text)."""
    try:
        # Build a compact list for the LLM to rank
        items = []
        for i, h in enumerate(hits):
            excerpt = h["excerpt"][:400].replace("\n", " ")
            items.append(f"{i+1}. {h['title']}: {excerpt}")
        prompt = (f"Question: {query}\n\n"
                  f"Rank these {len(items)} document excerpts by relevance to the question. "
                  f"Prefer documents whose title directly matches the question's topic, "
                  f"official policy manuals and program documents over announcements, "
                  f"and when the question names no US state, general/national documents "
                  f"over state-specific ones. "
                  f"Output ONLY the numbers in order from most to least relevant, comma-separated.\n\n"
                  + "\n".join(items) + "\n\nRanking:")
        text = _clean_llm_list_output(
            _utility_text(prompt, max_tokens=256, timeout=45))
        # Parse numbers
        order = []
        for part in text.replace(",", " ").split():
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(hits) and idx not in order:
                    order.append(idx)
            except ValueError:
                continue
        if len(order) >= 3:  # need at least 3 valid rankings
            # Add any missing indices at the end
            for i in range(len(hits)):
                if i not in order:
                    order.append(i)
            return [hits[i] for i in order]
    except Exception:
        pass
    return []

def get_corpus(name):
    if name == "federal":
        return CORPUS_FED
    if name == "both":
        return CORPUS_IN + CORPUS_FED
    return CORPUS_IN

STOP = set("a an the and or of to in on for with as at by from is are was were be been "
           "it its this that these those i you he she we they them his her their our your "
           "my me him us will would can could should shall may might must do does did done "
           "have has had not no yes if then than so such only also just into over under "
           "more most very per via all any each other which who whom whose what when where "
           "why how there here out up down off about into through during before after "
           "above below between within without".split())

# ---------------------------------------------------------------------------
# Program-discovery mode (2026-10-12): open-ended "what programs are
# available to me" questions fail under single-topic retrieval (one topic
# hogs every excerpt slot; the 2026-10-12 spot test surfaced 3 blind/VI
# pages and no Medicaid at all). Detect them, retrieve per program area
# with forced coverage, and answer as a survey that names its own gaps.
# ---------------------------------------------------------------------------
_DISCOVERY_PATTERNS = [
    r"what programs", r"what benefits", r"what help", r"what assistance",
    r"available to me", r"avaialbe", r"programs (are|is) (there|available)",
    r"what can i (get|qualify|apply)", r"what do i qualify",
    r"what are my options", r"what options (do|are)",
    r"benefits (are|is) (there|available)", r"what'?s out there",
    r"programs for (me|disabled|people like)",
]
# A question that also asks about a specific rule or mechanism is a
# specific question wearing discovery clothes; keep normal handling.
_SPECIFIC_MARKERS = [
    "appeal", "denied", "overpayment", "asset limit", "income limit",
    "miller trust", "lookback", "look-back", "penalty period",
    "spend down", "spenddown", "copay", "redetermination",
]


def is_discovery_question(question):
    q = (question or "").lower()
    if not any(re.search(p, q) for p in _DISCOVERY_PATTERNS):
        return False
    return not any(m in q for m in _SPECIFIC_MARKERS)


PROGRAM_AREAS = [
    ("Health coverage",
     "Medicaid health coverage eligibility disability HIP Healthy Indiana "
     "Plan waiver"),
    ("Food assistance",
     "SNAP food assistance eligibility disability household"),
    ("Cash and disability benefits",
     "SSDI SSI Social Security disability benefits Supplemental Security "
     "Income eligibility"),
    ("Home and community services",
     "HCBS waiver home community based services caregiver Structured "
     "Family Caregiving"),
    ("Work and other supports",
     "vocational rehabilitation disability employment work incentives "
     "assistive technology"),
]

SURVEY_INSTRUCTIONS = (
    "This is a program-discovery question. Answer as a complete survey "
    "grouped by these five categories, IN THIS ORDER: Health coverage; "
    "Food assistance; Cash and disability benefits; Home and community "
    "services; Work and other supports. Each excerpt is labeled with its "
    "program area. For each program named, say who it is for and the key "
    "eligibility facts in ONE or TWO sentences (brevity matters: the "
    "survey must fit), using ONLY programs supported by the excerpts. "
    "You MUST cover all five categories and you MUST finish with one "
    "line beginning 'Not covered by the documents provided:' listing "
    "every category for which the excerpts gave you no program (write "
    "'none' if every category had one). Do not invent programs to fill "
    "a category, and do not stop before the final line."
)


def discovery_hits(question, corpus=None, per_area=2):
    """Retrieve per program area so the excerpt set covers the landscape
    instead of one topic winning every slot. Returns (hits, missing).
    Area queries are already well-formed keyword queries, so the LLM
    rewrite and rerank passes stay off for speed."""
    profile_terms = " ".join(
        w for w in re.findall(r"[a-z0-9']+", (question or "").lower())
        if w in ("disabled", "disability", "veteran", "senior", "elderly",
                 "blind", "pregnant", "child", "children", "parent",
                 "caregiver", "indiana", "hoosier"))
    hits, missing, seen = [], [], set()
    for area, template in PROGRAM_AREAS:
        q = (profile_terms + " " + template).strip()
        try:
            found = hybrid_search(q, corpus=corpus, n=per_area,
                                  use_rewrite=False, use_rerank=False)
        except Exception:
            found = []
        kept = 0
        for h in found:
            key = (h.get("url", ""), h.get("excerpt", "")[:60])
            if key in seen:
                continue
            seen.add(key)
            hh = dict(h)
            hh["excerpt"] = (f"(Program area: {area})\n"
                             + h.get("excerpt", ""))
            hits.append(hh)
            kept += 1
            if kept >= per_area:
                break
        if not kept:
            missing.append(area)
    return hits, missing


SYSTEM_PROMPT = (
    "You answer questions about Indiana Medicaid, FSSA benefits, and the HCBS waiver "
    "using ONLY the document excerpts provided below. Each excerpt starts with its "
    "document title and URL. "
    "IMPORTANT: the excerpts were retrieved because they match the question. Read every "
    "excerpt in full before answering. Do NOT claim the documents lack the information "
    "unless you have checked all excerpts and none of them address it. A lazy "
    "'not mentioned' answer is a failure. Quote or paraphrase the relevant passages. "
    "Cite the document title when you use its information. Include the URL as a "
    "markdown link: [Document Title](URL). "
    "CRITICAL: Medicaid rules often have a general prohibition with specific exceptions. "
    "If one excerpt states a general rule (e.g., 'spouses cannot be caregivers') but "
    "another excerpt describes an exception or specific program allowing it (e.g., "
    "'Structured Family Caregiving allows spouses'), you MUST mention BOTH. Never "
    "present only the prohibition when an exception exists in the excerpts. "
    "EXCLUSIONS FIRST: If an excerpt states an exclusion or exception that "
    "applies to the specific item or situation in the question (for example, "
    "a vehicle exclusion when the question is about buying a vehicle), lead "
    "your answer with what that exclusion means for the person's situation, "
    "then give the general rule. Do not open with the general rule's most "
    "alarming reading when an applicable exclusion changes the outcome. "
    "MULTI-PART QUESTIONS: Answer each part of a multi-part question "
    "separately. A part may be answered 'not in the documents' only if no "
    "excerpt addresses that part; if ANY excerpt addresses a part, answer "
    "that part from it, even if briefly. "
    "If the excerpts genuinely do not contain the answer after a careful read, say so "
    "plainly instead of guessing. Say 'I don't know based on the available documents' "
    "when appropriate. "
    "For questions asking about dollar amounts, income limits, costs, or fees: if any "
    "excerpt contains specific dollar figures ($), quote them directly. Do not say "
    "the numbers are not listed when dollar amounts appear in the excerpts. "
    "CALCULATIONS: When you add, subtract, multiply, or divide dollar "
    "amounts, work one step at a time and write each calculation as an "
    "explicit equation in the form ($2,705 - $900 = $1,805). Never state "
    "a computed figure without showing the equation it came from. The "
    "server re-checks every equation you write and appends a correction "
    "when one is wrong, so exact equations matter. Quote dollar figures "
    "exactly as the excerpts state them, including cents; never round "
    "a stated figure (write $4,066.50 if that is what the source says). "
    "PROGRAM SCOPE: Each excerpt is labeled with the program it describes "
    "(for example [Program: SSI/SSDI] or [Program: Medicaid]). SSI, "
    "Medicaid, SNAP, and Medicare are different programs with different "
    "figures, exclusions, and timeframes. Never apply an SSI figure or "
    "timeframe (such as a 36-month transfer penalty or SSI resource "
    "counting rules) to a Medicaid question, or a Medicaid rule to an "
    "SSI question, when excerpts from the question's own program state "
    "the rule. If only another program's excerpts address a point, say "
    "which program they describe and that the rule may differ. "
    "CONTRADICTIONS: If excerpts disagree with each other, explicitly flag this. "
    "Say 'Sources disagree:' then present both sides with their citations. "
    "When two excerpts state different figures or time periods for the same "
    "rule, prefer the document that states the general program rule (a "
    "program explainer, manual section, or regulation) over "
    "program-administration pages (representative payee, provider, or "
    "operations pages), say which document your figure comes from, and note "
    "the other figure briefly rather than blending them. In particular, a "
    "timeframe or figure for retroactive or past-due benefits stated in "
    "POMS or a program manual (for example, the nine-month exclusion for "
    "past-due Social Security benefits) outranks any different figure in "
    "representative-payee lesson plans or FAQs; when both appear, lead "
    "with the POMS or manual figure. 'Retroactive' or 'past-due' Social "
    "Security benefits means backpay: a lump sum of benefits owed for "
    "past months, including SSDI backpay. "
    "APPLY THE RULE: When the excerpts state a formula or a fraction (for "
    "example, a community spouse keeps one half of the couple's countable "
    "assets, clamped to a stated minimum and maximum) and the question "
    "supplies the amounts, you MUST compute the result for the person's "
    "own numbers and lead with that result. Quoting the rule without "
    "applying it to their figures is a failure. Show the computation as "
    "explicit equations (see CALCULATIONS). Do not invent steps the "
    "excerpts do not state (there is no 'minimum plus half the remainder' "
    "step unless an excerpt says so). "
    "CONFIDENCE: End your answer with a confidence line: 'Confidence: High/Medium/Low' "
    "followed by a brief reason. High = 3+ sources agree with specific details. "
    "Medium = 1-2 sources or general info. Low = limited or conflicting info. "
    "DATES: If excerpts mention effective dates, program years, or 'as of' dates, "
    "include them. If the info might be outdated (e.g., 'FY2024' when it's 2026), "
    "warn the user. "
    "The excerpts come from a snapshot scraped 2026-10-10; remind the user to confirm "
    "anything affecting benefits or eligibility with their caseworker or FSSA. "
    "Keep answers concise and plain-spoken."
)


# v18 (F15): the spouse-specific priming is CONDITIONAL. Through
# v17.3 the SPOUSE INCOME paragraph and the CSRA share sentence sat
# in SYSTEM_PROMPT for EVERY question, priming spouse frames onto
# unrelated asks. They now live in a block appended only when the
# question itself involves a spouse (or the SFC program, whose
# subject is a spouse caregiver by construction).
SPOUSE_PROMPT_BLOCK = (
    "SPOUSE INCOME: Under spousal impoverishment rules, income belongs to "
    "the spouse whose name it is in. A non-applicant (community) spouse's "
    "own earnings, including pay for caregiving, are that spouse's income "
    "and are not counted toward the applicant spouse's income limit. Do "
    "not state that a caregiver spouse's pay counts toward the other "
    "spouse's limit unless an excerpt explicitly says so. Where the "
    "money is deposited does not change whose income it is: a spouse's "
    "pay deposited into a joint account remains that spouse's income, "
    "not joint income attributable to the applicant. "
    "For spousal resource cases, the community spouse's share is one "
    "half of the couple's combined countable assets at the snapshot "
    "date, clamped to the stated minimum and maximum; do not subtract "
    "the applicant's $2,000 allowance before halving.")

_SPOUSE_TOKENS = {"wife", "husband", "spouse", "spouses", "spousal",
                  "married", "widow", "widower"}


def _question_involves_spouse(q):
    """Does this question involve a spouse? Gates the spouse
    prompt block, the SFC retrieval boost (F15)."""
    ql = (q or "").lower()
    if "structured family caregiving" in ql \
            or "community spouse" in ql:
        return True
    return bool(set(re.findall(r"[a-z]+", ql)) & _SPOUSE_TOKENS)


def _system_prompt_for(question):
    """The drafting system prompt for one question (F15)."""
    if _question_involves_spouse(question):
        return SYSTEM_PROMPT + "\n\n" + SPOUSE_PROMPT_BLOCK
    return SYSTEM_PROMPT


def keywords(query):
    return [t for t in re.findall(r"[a-z0-9]{2,}", query.lower()) if t not in STOP]


CHUNK = 1500  # chars per chunk
OVERLAP = 300

# Domain synonyms: acronym -> expansion words added to the query.
# Applied to standalone tokens only.
SYNONYMS = {
    "sfc": ["structured", "family", "caregiving"],
    "hcbs": ["home", "community", "based", "services"],
    "hip": ["healthy", "indiana", "plan"],
    "snap": ["supplemental", "nutrition", "assistance", "program", "food"],
    "dfr": ["division", "family", "resources"],
    "ddars": ["division", "disability", "aging", "rehabilitative", "services"],
    "bds": ["bureau", "disabilities", "services"],
    "fssa": ["family", "social", "services", "administration"],
    "tbi": ["traumatic", "brain", "injury"],
    "hw": ["health", "wellness"],
    "chip": ["childrens", "health", "insurance", "program"],
    "ssi": ["supplemental", "security", "income"],
    "ssdi": ["social", "security", "disability", "insurance"],
    "tanf": ["temporary", "assistance", "needy", "families"],
    "ebt": ["electronic", "benefit", "transfer"],
    "abd": ["aged", "blind", "disabled"],
    "mro": ["medicaid", "rehabilitation", "option"],
    "am": ["attendant", "care"],
    "wife": ["spouse"],
    "husband": ["spouse"],
    "spouse": ["wife", "husband"],
    "caregiver": ["caretaker", "care", "provider"],
    "caretaker": ["caregiver"],
    "pathways": ["managed", "care", "mce"],
    "mce": ["managed", "care", "entity", "pathways"],
    "chi": ["community", "health", "integration"],
    # Situational vocabulary (2026-10-12): everyday words in life-situation
    # questions -> the manual's terms for the same thing. Added after the
    # situational test showed narrative phrasing missing the manual
    # sections that keyword phrasing finds (van/FEMA/inheritance cases).
    "car": ["vehicle"],
    "van": ["vehicle"],
    "truck": ["vehicle"],
    "automobile": ["vehicle"],
    "inheritance": ["lump", "sum", "nonrecurring"],
    "inherited": ["lump", "sum", "inheritance"],
    "funeral": ["burial"],
    "fema": ["disaster", "assistance", "payments"],
    "backpay": ["retroactive", "benefits", "lump", "sum"],
    "gift": ["transfer", "assets"],
}

# --- v21 (WS3c): growth-loop lay terms --------------------------------
# The growth loop's staging pass (tools/growth_loop.py) proposes
# lay-term registrations: concept -> the corpus terms/sections the
# concept lives under. Approved registrations land in
# growth_terms.json NEXT TO the server file (override the path
# with FSSA_GROWTH_TERMS). The file is read ONCE at boot; its
# absence is normal (no registrations merged yet) and never
# fatal. Consumption: search() expands a registered concept
# found in the query with its mapped terms (the SYNONYMS
# mechanism, generalized to phrases and section numbers), and
# _rare_terms() adds the mapped terms to the rescue pass's
# term list. Merging a staged registration stays a human act:
# nothing in the server writes this file.
GROWTH_TERMS_PATH = os.environ.get("FSSA_GROWTH_TERMS",
                                   "growth_terms.json")
_GROWTH_TERMS = {}
try:
    with open(GROWTH_TERMS_PATH, encoding="utf-8") as _gf:
        _raw_gt = json.load(_gf)
    if isinstance(_raw_gt, dict):
        for _gk, _gv in _raw_gt.items():
            if isinstance(_gv, (list, tuple)):
                _GROWTH_TERMS[str(_gk).lower()] = [
                    str(_gx).lower() for _gx in _gv]
    print(f"[growth] loaded {len(_GROWTH_TERMS)} lay-term "
          f"registration(s) from {GROWTH_TERMS_PATH}", flush=True)
except FileNotFoundError:
    pass
except Exception as _ge:
    print(f"[growth] {GROWTH_TERMS_PATH} unreadable ({_ge}); "
          "lay-term expansion off", flush=True)


def _growth_expansions(text_low):
    """The mapped corpus terms for every registered concept
    present in `text_low` (already lowercased)."""
    out = []
    for concept, mapped in _GROWTH_TERMS.items():
        if concept in text_low:
            for m in mapped:
                if m not in out:
                    out.append(m)
    return out


_SECTION_HEAD_RX = re.compile(
    r"(?m)^[ \t]*(\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?)[ \t]*\n"
    r"[ \t]*([A-Z][A-Z0-9 /&()\-',]{2,78}?)[ \t]*$")


def _split_sections(text):
    """Split DFR-manual-style text into (heading, body) sections.

    The manuals put each section number on its own line followed by an
    ALL-CAPS heading line. Returns [] when fewer than 3 headings are
    found (not a manual), so callers fall back to plain chunking.
    KEEP IN SYNC with the copy in gen_embeddings_stream.py: embedding
    chunks and runtime keyword chunks must be sectioned identically.
    """
    ms = list(_SECTION_HEAD_RX.finditer(text))
    if len(ms) < 3:
        return []
    secs = []
    preface = text[:ms[0].start()]
    if preface.strip():
        secs.append(("", preface))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        heading = f"{m.group(1)} {m.group(2).strip()}"
        secs.append((heading, text[m.end():end]))
    return secs


def _chunks_plain(text):
    """Split text into overlapping chunks on paragraph boundaries."""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 > CHUNK and cur:
            chunks.append(cur)
            # overlap: keep tail of cur
            cur = cur[-OVERLAP:] + "\n" + p if len(cur) > OVERLAP else cur + "\n" + p
        else:
            cur = (cur + "\n" + p).strip()
    if cur.strip():
        chunks.append(cur)
    return chunks or [text[:CHUNK]]


def _chunks(text):
    """Section-aware chunking (2026-10-12): for manual-style documents,
    chunk within each numbered section and prefix every chunk with its
    section heading, so a rule stays attached to the heading that names
    it ('2615.60.00 VEHICLES') in both keyword and vector retrieval."""
    secs = _split_sections(text)
    if not secs:
        return _chunks_plain(text)
    out = []
    for heading, body in secs:
        for ch in _chunks_plain(body):
            if ch.strip():
                out.append(f"{heading}\n{ch}" if heading else ch)
    return out or [text[:CHUNK]]


_DOC_CHUNKS_KEY = "_chunks_v18"


def _doc_chunks(d):
    """v18 (R7): a document's chunks, computed once and cached
    on the document dict. Through v17.3 search() re-chunked the
    whole corpus on EVERY call (and the rescue/retry paths
    call search repeatedly per ask). Corpus dicts are
    process-lifetime objects, so the cache rides on them."""
    chs = d.get(_DOC_CHUNKS_KEY)
    if chs is None:
        chs = _chunks(d["text"])
        try:
            d[_DOC_CHUNKS_KEY] = chs
        except Exception:
            pass
    return chs


# ---------------------------------------------------------------------------
# v20.2: boot-time consistency check. The loaded embeddings
# must correspond to the corpora. NOTE the two chunkers:
# the retrieval-side chunker above (_chunks) is NOT what
# built the embeddings — the generation pipeline
# (gen_embeddings_stream.py) windows text at 600 chars
# with 100 overlap, section-aware over the SAME
# _split_sections, skipping docs under 200 chars and URLs
# already embedded. The manifest therefore replicates the
# GENERATION chunker exactly; measured against the v19
# corpora it yields 73,454 chunks vs the live file's
# 73,014 rows (the ~0.6% delta is generation-vintage
# noise: per-chunk embed failures are skipped at build
# time, and the append-only build file carries chunks
# from earlier corpus vintages of the same URLs). The
# incident this check exists to catch is thousand-scale:
# the v19 finalize resurrecting the dedupe-skipped chunks
# (+2,244 rows), caught by hand hours later in v19.
# dedupe_skip.json, when present in the server dir, is
# reported in the detail block (its raw/adjusted counts),
# but the verdict compares against the raw manifest: on
# the live build the skip list's indices address an older
# build file, so adjusting by it would double-count the
# removal. A mismatch prints loudly and lands in /health
# as "consistency" — WARN ONLY: the server always boots
# (a stale-but-serving index beats a dark one).
# ---------------------------------------------------------------------------
CONSISTENCY = {"ok": None, "detail": "check not run"}


def _emb_window(text, size=600, overlap=100):
    """The generation pipeline's plain window (replica of
    gen_embeddings_stream._window; keep in sync)."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def _emb_chunk_count(text):
    """How many embedding chunks the generation pipeline
    produces for one document's text (replica of
    gen_embeddings_stream.chunk_text)."""
    secs = _split_sections(text)
    if not secs:
        return len(_emb_window(text))
    n = 0
    for _heading, body in secs:
        n += sum(1 for p in _emb_window(body) if p.strip())
    return n if n else len(_emb_window(text))


def _run_consistency_check():
    global CONSISTENCY
    try:
        manifest = {}
        for d in CORPUS_IN + CORPUS_FED:
            u = d.get("url")
            text = d.get("text") or ""
            if u and u not in manifest and len(text) >= 200:
                manifest[u] = _emb_chunk_count(text)
        skips = {}
        n_skips = 0
        try:
            with open("dedupe_skip.json", encoding="utf-8") as f:
                for e in json.load(f):
                    if int(e.get("index", -1)) \
                            < manifest.get(e.get("url"), 0):
                        skips[e["url"]] = skips.get(e["url"], 0) + 1
                        n_skips += 1
        except FileNotFoundError:
            pass
        actual = {}
        for c in EMBEDDINGS:
            actual[c["url"]] = actual.get(c["url"], 0) + 1
        orphans = sorted(u for u in actual if u not in manifest)
        missing = sorted(u for u, c in manifest.items()
                         if c > 0 and actual.get(u, 0) == 0)
        mismatched = sorted(
            u for u in manifest
            if u in actual and actual[u] != manifest[u])
        man_total = sum(manifest.values())
        detail = {
            "embeddings_rows": len(EMBEDDINGS),
            "manifest_rows": man_total,
            "delta_rows": len(EMBEDDINGS) - man_total,
            "dedupe_skip_entries_applicable": n_skips,
            "manifest_rows_after_dedupe_skips":
                man_total - n_skips,
            "docs_in": len(CORPUS_IN),
            "docs_federal": len(CORPUS_FED),
            "matrix_rows": (None if EMB_MAT is None
                            else int(EMB_MAT.shape[0])),
            "orphan_urls": len(orphans),
            "missing_urls": len(missing),
            "mismatched_urls": len(mismatched),
            "examples": {
                "orphan": orphans[:3], "missing": missing[:3],
                "mismatched": [
                    {"url": u, "expected": manifest[u],
                     "actual": actual[u]}
                    for u in mismatched[:3]]}}
        ok = (len(EMBEDDINGS) == man_total and not orphans
              and not missing and not mismatched
              and (EMB_MAT is None
                   or int(EMB_MAT.shape[0]) == len(EMBEDDINGS)))
        CONSISTENCY = {"ok": bool(ok), "detail": detail}
        if ok:
            print(f"[consistency] ok: {len(EMBEDDINGS)} embedding "
                  f"rows match the corpora chunk manifest "
                  f"({man_total} expected)", flush=True)
        else:
            print(f"[consistency] WARNING: embeddings do NOT "
                  f"match the corpora chunk manifest: "
                  f"{json.dumps(detail)}", flush=True)
    except Exception as e:
        CONSISTENCY = {"ok": False,
                       "detail": f"check failed: "
                                 f"{type(e).__name__}: {e}"}
        print(f"[consistency] WARNING: consistency check "
              f"failed to run: {type(e).__name__}: {e}",
              flush=True)


def search(query, corpus=None, n=6, extra_terms=None):
    corpus = corpus if corpus is not None else CORPUS_IN
    raw_terms = keywords(query)
    if not raw_terms:
        return []
    # expand acronyms: original term keeps its weight, expansion words are
    # added so docs using the long form also match
    terms = list(raw_terms)
    for t in raw_terms:
        if t in SYNONYMS:
            terms.extend(w for w in SYNONYMS[t] if w not in terms)
    # v21 (WS3c): growth-loop lay-term registrations expand the
    # query the same way (concept phrase -> corpus terms /
    # section numbers, matched as substrings like every term).
    for _gt in _growth_expansions(query.lower()):
        if _gt not in terms:
            terms.append(_gt)
    # Add rewritten terms separately (not concatenated, to avoid phrase corruption)
    if extra_terms:
        for t in extra_terms:
            t = t.lower().strip()
            if t and t not in terms and len(t) > 1:
                terms.append(t)
    # multi-word phrases from consecutive ORIGINAL query terms get a big bonus
    # (not from rewritten terms, to avoid synthetic cross-boundary n-grams)
    phrases = [" ".join(raw_terms[i:i+2]) for i in range(len(raw_terms)-1)]
    phrases += [" ".join(raw_terms[i:i+3]) for i in range(len(raw_terms)-2)]
    # when the question asks about money, boost chunks with dollar figures
    money_q = any(w in terms for w in
                  ("limit", "limits", "cost", "costs", "amount", "much", "income",
                   "asset", "assets", "pay", "payment", "rate", "dollar", "price",
                   "fee", "fees", "premium", "deductible", "copay"))
    dollar_pat = re.compile(r"\$[\d,]+")
    # v22: the provider title penalty that used to be computed
    # here is DELETED (consult design: authority is metadata, not
    # a title substring; the -15 demotion buried PROMOD00051 on
    # the 2026-10-14 eyeglasses ask and v20.2 only exempted the
    # modules). No title-word demotions remain in this function.
    # v18 (F15): the SFC spouse boost applies only when the
    # QUERY itself involves a spouse; through v17.3 chunk
    # content alone armed it for any query.
    q_spouse = _question_involves_spouse(query)
    scored = []
    for d in corpus:
        title = d["title"].lower()
        title_hits = sum(1 for t in terms if t in title)
        for ch in _doc_chunks(d):
            low = ch.lower()
            matched = sum(1 for t in terms if t in low)
            if not matched:
                continue
            density = sum(min(low.count(t), 8) for t in terms)
            phrase_bonus = sum(20 for p in phrases if p in low)
            money_bonus = 0
            if money_q and dollar_pat.search(ch):
                money_bonus = 30
            # SFC spouse bonus: chunks mentioning both spouse and SFC get a boost
            # (fixes the "can my wife be my caregiver" acceptance test)
            sfc_bonus = 0
            has_spouse = any(w in low for w in ("spouse", "wife", "husband"))
            has_sfc = ("structured family caregiving" in low or
                       ("sfc" in low and "caregiving" in low))
            if q_spouse and has_spouse and has_sfc:
                sfc_bonus = 50
            # score: phrase matches dominate, then term coverage, density, title
            score = (phrase_bonus + money_bonus + sfc_bonus + matched * 10 + density
                     + title_hits * 3)
            scored.append((score, d, ch))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for score, d, ch in scored:
        if len(out) >= n:
            break
        key = (d["url"], ch[:60])
        if key in seen:
            continue
        seen.add(key)
        # For money questions: if doc is short, include full text so LLM sees all figures
        excerpt_text = ch[:2000]
        if money_q and len(d["text"]) < 4000:
            excerpt_text = d["text"][:3000]
        hit = {
            "title": d["title"],
            "url": d["url"],
            "source": d.get("source", "pdf"),
            "verified": d.get("verified", True),
            "excerpt": excerpt_text,
        }
        # v13: carry ingest-time tags (tag_docs.py) into the hit so the
        # ranking can prefer them over re-classification.
        if d.get("tags"):
            hit["tags"] = d["tags"]
        out.append(hit)
    return out


def ollama_models():
    try:
        req = urllib.request.Request(OLLAMA + "/api/tags")
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        seen = []
        for m in data.get("models", []):
            name = m["name"]
            # skip llamacpp hash aliases and exact dupes
            if name.startswith("llamacpp:") or name in seen:
                continue
            seen.append(name)
        return seen
    except Exception:
        return []


_GEMINI_LAST = [0.0]
_GEMINI_MIN_INTERVAL = 4.0
_GEMINI_LOCK = threading.Lock()  # v18 (R10a): was unlocked


def _gemini_space():
    """Keep a minimum gap between Gemini calls (free-tier rate limits)."""
    with _GEMINI_LOCK:
        wait = _GEMINI_MIN_INTERVAL - (time.monotonic() - _GEMINI_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _GEMINI_LAST[0] = time.monotonic()


# Gemini model chain (2026-10-12; v9 reorder at operator direction): the
# Lite goes first as the everyday engine (15 RPM / 500 requests per day)
# and 3.8 Flash (5 RPM / only 20 per day) is the reserve, reached on a
# Lite failure or when the user picks it by name (the normalize step
# starts the chain at the requested model). If the chain fails, the
# caller falls back to the local 9B.
GEMINI_CHAIN = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]


class _GeminiQuota(Exception):
    """The model's daily quota is gone; downgrade at once, never retry."""


def _fmt_hit(h):
    """One excerpt block for the answering prompt, with its v11 program
    label so the model can keep programs (SSI vs Medicaid vs SNAP)
    apart while it reads."""
    prog = h.get("program")
    if not prog:
        prog, _dt = _classify_hit(h)
    return (f"[{h['title']}]({h['url']})\n"
            f"[Program: {prog}]\n{h['excerpt']}")


_QWORD_START = re.compile(
    r"^(how|what|does|do|did|will|would|can|could|is|are|was|were|"
    r"should|if|when|where|who|why)\b", re.IGNORECASE)


_AND_ASK_RX = re.compile(
    r"\s+and\s+(?=(?:what should|what can|what do|what steps|"
    r"what is|what are|how do|how can|how should|can we|should we|"
    r"do we|does it|do i|can i|should i|what happens|what else)\b)",
    re.IGNORECASE)


def _compound_split(q):
    """v15 (regression fix c, the operator's manual test 2026-10-13): TWO
    asks joined by 'and' inside ONE sentence — 'does it affect my
    eligibility and what should we do before the money arrives?'
    — which the '?'/clause splitters never separated, so the
    eligibility core and the advice ask sank or swam together.
    Returns [part1, part2] with the fact preamble kept on both
    parts, or [] when the shape is not a compound ask."""
    text = (q or "").strip().rstrip("?")
    matches = list(_AND_ASK_RX.finditer(text))
    if not matches:
        return []
    m = matches[-1]
    left = text[:m.start()].strip()
    right = re.sub(r"^and\s+", "", text[m.start():].strip(),
                   flags=re.IGNORECASE)
    if len(right.split()) < 4 or len(left.split()) < 4:
        return []
    sents = re.split(r"(?<=[.;])\s+", left)
    ask = sents[-1]
    if not _QWORD_START.match(ask) and not re.search(
            r"\b(does|do|did|can|could|is|are|will|would|should)\b",
            ask, re.IGNORECASE):
        return []
    preamble = " ".join(sents[:-1]).strip()
    part2 = (preamble + " " + right).strip() if preamble else right
    return [left.rstrip("?").strip(), part2.rstrip("?").strip()]


def _split_subquestions(q):
    """Split a bundled question into its sub-questions (v11). Primary
    split on '?'; if that yields fewer than 3 parts, also split long
    comma/semicolon clauses that begin with a question word. Returns
    [] when the question is not bundled."""
    def _ask_of(frag):
        """The question sentence inside a fragment: its last sentence,
        unless that sentence is a short follow-on ('Is that a
        problem?'), in which case the fact sentence before it is part
        of the ask and both are kept."""
        if ". " not in frag:
            return frag
        sents = frag.split(". ")
        last = sents[-1].strip()
        if len(last.split()) < 6 and len(sents) > 1:
            return (sents[-2].strip() + ". " + last).strip()
        return last

    parts = []
    for frag in q.split("?"):
        frag = _ask_of(frag.strip(" \n\t,;"))
        if len(frag.split()) >= 4:
            parts.append(frag)
    if len(parts) < 3:
        alt = []
        for frag in re.split(r"[,;]\s+", q.rstrip("?")):
            frag = _ask_of(frag.strip())
            frag = re.sub(r"^and\s+", "", frag, flags=re.IGNORECASE)
            if len(frag.split()) >= 4 and _QWORD_START.match(frag):
                alt.append(frag)
        if len(alt) > len(parts):
            parts = alt
    if len(parts) < 3:
        # v15: the eligibility+advice compound (one sentence, two
        # asks) decomposes into exactly two parts so the verified
        # core can ship as a Partial when the other part abstains.
        compound = _compound_split(q)
        if compound:
            return compound
    return parts[:6] if len(parts) >= 3 else []


def _retrieve_bundled(question, corp, n_ex, use_rewrite=True,
                      use_rerank=True):
    """v18 (R8): the bundled retrieval, returning BOTH the merged
    excerpt set and the per-part sets. Through v17.3 the per-part
    searches ran here at n=4 and then AGAIN in the unit loop at
    n=6 (a different cache key, so the paid rewrite/rerank legs
    repeated); now one depth (n=6) serves both consumers."""
    parts = _split_subquestions(question)
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_main = _submit(pool, hybrid_search, question,
                           corpus=corp, n=n_ex,
                           use_rewrite=use_rewrite,
                           use_rerank=use_rerank)
        fut_parts = [_submit(pool, hybrid_search, part,
                             corpus=corp, n=6,
                             use_rewrite=use_rewrite,
                             use_rerank=use_rerank)
                     for part in parts]
        main = fut_main.result()
        subs = [f.result() for f in fut_parts]
    guaranteed, seen = [], set()
    for sub in subs:
        for h in sub[:2]:
            key = (h["url"], h["excerpt"][:60])
            if key not in seen:
                seen.add(key)
                guaranteed.append(h)
    per_part = {part: list(sub) for part, sub in zip(parts, subs)}
    if not guaranteed:
        return main, per_part
    rest = [h for h in main
            if (h["url"], h["excerpt"][:60]) not in seen]
    return (guaranteed + rest)[:max(n_ex, len(guaranteed))], per_part


def _merge_guaranteed(base, extra_lists, per_list=2):
    """v27: the _retrieve_bundled merge discipline, generalized.
    `base` hits keep their order and are NEVER displaced; each
    extra source list contributes up to `per_list` hits not
    already present, deduped on (url, excerpt[:60])."""
    out = list(base)
    seen = {(h["url"], h["excerpt"][:60]) for h in out}
    for lst in extra_lists:
        taken = 0
        for h in lst or ():
            if taken >= per_list:
                break
            key = (h["url"], h["excerpt"][:60])
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
            taken += 1
    return out


def _v27_run_cerebras(question, corp, n_ex, use_rewrite,
                      use_rerank, merged, state):
    """v27 (Change 1): run the Cerebras expansion seat ONCE for
    this ask (the ask context's v27_cerebras_ran flag is the
    once-guard) and merge its phrasings' hits into `merged`
    under the guarantee discipline. Returns (merged, phrasings).
    Fail-open throughout: no key, a seat failure, or an empty
    expansion leaves `merged` untouched. When a key is present
    the run is recorded on the ask's pool-note channel (the
    `cerebras` field: {used, phrasings, ms, fail_open}) exactly
    the way other retrieval facts reach the ask record; with no
    key nothing is recorded and retrieval is byte-for-byte the
    pre-v27 behavior."""
    ctx = _ask_ctx()
    if ctx.v27_cerebras_ran:
        state["ran"] = True
        return merged, []
    ctx.v27_cerebras_ran = True
    state["ran"] = True
    key_present = bool(_cerebras_key())
    t0 = time.monotonic()
    phrasings = []
    if key_present:
        try:
            phrasings = _cerebras_expand(question) or []
        except Exception:
            phrasings = []
    info = {"used": bool(phrasings),
            "phrasings": list(phrasings),
            "ms": int((time.monotonic() - t0) * 1000),
            "fail_open": bool(key_present and not phrasings)}
    state["info"] = info
    if key_present:
        _retr_pool_note({"q": question[:80], "engine": "v27",
                         "cerebras": info})
    if not phrasings:
        return merged, []
    lists = []
    for ph in phrasings:
        try:
            lists.append(hybrid_search(
                ph, corpus=corp, n=6, use_rewrite=use_rewrite,
                use_rerank=use_rerank))
        except Exception:
            lists.append([])
    return _merge_guaranteed(merged, lists, per_list=2), phrasings


def _v27_thin_reasons(pools, hits, n_ex):
    """v27 (Change 2): the THIN test on the first merged result,
    evaluated once per retrieve_ask_hits call from the pool
    notes that call's own searches wrote. Thin when any of:
    (a) a v2 fused band was flat (the pool note's `flat`, the
    < 0.045 / spread signal _hybrid_search_v2 already computes);
    (b) fewer hits than requested; (c) no registry-channel hit
    and no pin fired, on the searches that reported those
    signals (a cached pool reports None = unknown and never
    counts toward (c))."""
    reasons = []
    notes = [p for p in pools if isinstance(p, dict)]
    if any(p.get("flat") for p in notes):
        reasons.append("flat_band")
    if len(hits) < n_ex:
        reasons.append("short")
    sig = [p for p in notes if p.get("registry_hit") is not None]
    if sig and not any(p.get("registry_hit") for p in sig) \
            and not any(p.get("pin_fired") for p in sig):
        reasons.append("no_registry_or_pin")
    return reasons


def _v27_wider_pass(question, corp, n_ex, use_rewrite, use_rerank,
                    merged, state):
    """v27 (Change 2): ONE wider pass for this ask, never two
    (the ask context's v27_wider_ran flag is the once-guard).
    Cerebras expansion first when the seat is on and has not
    already run for this ask; otherwise (seat already ran, seat
    off, or the seat failed open) the deterministic widening:
    SYNONYMS + growth expansions searched at increased depth.
    Either way the merge is the guarantee discipline: the
    existing hits are never displaced."""
    ctx = _ask_ctx()
    if ctx.v27_wider_ran:
        return merged
    ctx.v27_wider_ran = True
    if _cerebras_key() and not ctx.v27_cerebras_ran:
        merged, phrasings = _v27_run_cerebras(
            question, corp, n_ex, use_rewrite, use_rerank,
            merged, state)
        if phrasings:
            return merged
    q_low = question.lower()
    exps = []
    for t in keywords(question):
        for w in SYNONYMS.get(t, ()):
            if w not in exps:
                exps.append(w)
    for gt in _growth_expansions(q_low):
        if gt not in exps:
            exps.append(gt)
    if not exps:
        return merged
    widened = question + " " + " ".join(exps)
    try:
        extra = hybrid_search(widened, corpus=corp,
                              n=max(n_ex * 2, n_ex + 6),
                              use_rewrite=use_rewrite,
                              use_rerank=use_rerank)
    except Exception:
        extra = []
    return _merge_guaranteed(merged, [extra], per_list=2)


def retrieve_ask_hits(question, corp, n_ex, use_rewrite=True,
                      use_rerank=True):
    """Retrieval for /ask (v11): for bundled narrative questions,
    retrieve per sub-question and guarantee up to 2 excerpts per part
    before the whole-question ranking fills the remaining slots, so
    no thread of a multi-part question is starved of excerpts (the
    v10 bundled pest-control miss). Single questions unchanged.
    v13: the bundled path now keys off the splitter alone, so a short
    question with 3+ real parts gets per-part retrieval too; the
    narrative check only decided excerpt count, which the caller
    still owns.
    v27: after that first merged result, (Change 1) the Cerebras
    expansion seat's phrasings are retrieved under the same
    guarantee discipline when the seat is on, and (Change 2) a
    THIN first result earns ONE wider pass for the ask (Cerebras
    if it has not run yet, else the deterministic widening)."""
    pools0 = len(_ask_ctx().retr_pools)
    parts = _split_subquestions(question)
    if not parts:
        merged = hybrid_search(question, corpus=corp, n=n_ex,
                               use_rewrite=use_rewrite,
                               use_rerank=use_rerank)
    else:
        merged, _per_part = _retrieve_bundled(
            question, corp, n_ex, use_rewrite=use_rewrite,
            use_rerank=use_rerank)
    first_pools = _retr_pools_since(pools0)
    state = {"ran": False, "info": None}
    # v27 (Change 1): the expansion seat (fail-open; a no-key
    # seat leaves the merged result and the record untouched).
    merged, _ph = _v27_run_cerebras(
        question, corp, n_ex, use_rewrite, use_rerank, merged,
        state)
    # v27 (Change 2): THIN is evaluated once, on the FIRST
    # merged result's own pool notes; one wider pass at most.
    if _v27_thin_reasons(first_pools, merged, n_ex) \
            and not _ask_ctx().v27_wider_ran:
        merged = _v27_wider_pass(
            question, corp, n_ex, use_rewrite, use_rerank,
            merged, state)
    return merged


def _drop_payee_admin(question, hits):
    """v12: representative-payee lesson/FAQ pages state a six-month
    figure for past-due benefits that contradicts the general rule
    (nine months, per SSI Resources/POMS). When the question is about
    backpay/lump sums and never mentions payees, those admin pages
    only mislead the answerer (four graded runs, 2026-10-13), so they
    are removed from the /ask excerpt set. Questions that mention
    payees keep them."""
    q = question.lower()
    if "payee" in q:
        return hits
    if not any(t in q for t in ("backpay", "back pay", "lump sum",
                                "lump-sum", "retroactive", "past-due",
                                "past due")):
        return hits
    return [h for h in hits if "/payee/" not in (h.get("url") or "")]


def _derivation_level1(values):
    """v26.2 (fix 1): ONE level of the derivation whitelist —
    the scope values plus their halves, doubles, and pairwise
    sums and differences (the v23.1 claim check's expansion,
    factored out). All values rounded to cents, matching the
    checkers' own rounding, so set membership below is exact."""
    allowed = set()
    for v in values or ():
        try:
            allowed.add(round(float(v), 2))
        except (TypeError, ValueError):
            pass
    snap = sorted(allowed)
    for v in snap:
        allowed.add(round(v / 2, 2))
        allowed.add(round(v * 2, 2))
    for i, a in enumerate(snap):
        for b in snap[i:]:
            allowed.add(round(a + b, 2))
            allowed.add(round(abs(a - b), 2))
    return allowed


def _derivable(val, level1):
    """v26.2 (fix 1): is val grounded as a derivation of the
    scope whose level-1 set is `level1`? True when val is in
    the level-1 set (in scope, or one whitelist step from it),
    or one further step beyond it: val = y + z, y - z, or
    z - y with y, z both in level1. That second step is the
    two-step chain the battery's post-eligibility math needs
    ($2,600 - $52 = $2,548 and $2,705 - $900 = $1,805, then
    $2,548 - $1,805 = $743) — through v26.1 the check stopped
    at one level and the result failed grounding with every
    input in scope. The full closure is never materialized
    (pairwise expansion explodes combinatorially); the test
    is per candidate value, O(|level1|) set lookups."""
    if any(abs(val - a) <= 0.011 for a in level1):
        return True
    return _derivation_step(val, level1)


def _derivation_step(val, level1):
    """The second whitelist step alone (no membership test):
    val = y + z, y - z, or z - y for some y, z in level1."""
    for y in level1:
        if round(y - val, 2) in level1 \
                or round(val - y, 2) in level1 \
                or round(val + y, 2) in level1:
            return True
    return False


def _check_figures(answer, question, hits, extra_allowed=(),
                   pool_all=False):
    """v11 figure provenance: every dollar figure in the answer must
    trace to an excerpt, the question itself, or a shown equation
    (operand or computed result). Ungrounded figures are listed in a
    server note; the note flags, it does not rewrite.
    v13: extra_allowed carries the verified-registry figures and the
    server-computed CSRA values, which are supported by definition.
    v18 (F5): the four holes close. (1) An equation's operands no
    longer ground themselves or their result: an operand must
    already be grounded (the question, the registry values, or the
    sentence's OWN cited excerpts); only then does the result join
    the grounded set. (2) Provenance is scoped to the excerpts the
    sentence CITES, not pooled across everything retrieved: a
    figure appearing only in an uncited excerpt is ungrounded for
    that sentence. (3) The match tolerance tightens from ±$0.51 to
    ±$0.011. (4) Unprefixed amounts in money contexts ("910.80 per
    month", "910 dollars") are checked too, not only $-figures.
    v25.1: pool_all (the Quick lane's gate call ONLY; Deep never
    passes it, so the F5 scoping above is untouched there) pools
    EVERY excerpt the drafter saw into the allowed set: the Quick
    floor is presence in the retrieved evidence, with per-sentence
    support carried by the citation gate instead. Pool extraction
    mirrors the answer scan (money_re + bare_re) so normalization
    ($, commas, decimals) matches on both sides.
    v26.2: (fix 3) all extraction below runs on date-masked text
    (_mask_dates, the v25.1 precedent this gate never had), so
    "the July 2026 dollar limits" yields no $2,026 figure and
    "(7/2026)" no phantom equation. (fix 2) the question's
    bare-form figures join base, as the pooled excerpts already
    did. (fix 1) a sentence figure also grounds when it is a
    derivation (the whitelist, with one chained step) of
    this sentence's scope plus the figures earlier sentences
    grounded — two-step arithmetic stated in prose grounds end
    to end, while F5/F6 scoping is untouched: the derivation
    test only ever combines values the strict rules already
    grounded."""
    money_re = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
    bare_re = re.compile(
        r"\b(\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars?\b|per month\b|"
        r"a month\b|monthly\b|per year\b|a year\b|annually\b|"
        r"per week\b|a week\b|weekly\b)")

    def _grab(text):
        vals = set()
        for m in money_re.finditer(text or ""):
            try:
                vals.add(round(float(
                    m.group(1).replace(",", "")), 2))
            except ValueError:
                pass
        return vals

    base = set()  # the question + registry values: global scope
    for _v in extra_allowed or ():
        try:
            base.add(round(float(_v), 2))
        except (TypeError, ValueError):
            pass
    base |= _grab(question)
    # v26.2 (fix 2): the question's bare-form figures are
    # premises too ("if i make 1300 a month" — battery B26
    # Quick flagged the asker's own $1,300). Read on masked
    # text (fix 3), mirroring the pooled excerpt read below.
    for m in bare_re.finditer(_mask_dates(question or "")):
        try:
            base.add(round(float(
                m.group(1).replace(",", "")), 2))
        except ValueError:
            pass
    # v30 (checker exemption a-i): a figure copied from
    # the QUESTION is a premise in whatever form the
    # asker stated it — including a bare number with no $
    # and no unit suffix ("my dad gave me 40135 last
    # year"), which fix 2's suffixed forms miss; a draft
    # restating the asker's own number as a dollar figure
    # is not fabricating it (Battery 2's U07-T kill).
    # Bare question numbers join base only at figure
    # scale (>= 100, read on date-masked text so years
    # and counts stay out). Every other figure still must
    # trace to excerpts, and the F5 scoping below is
    # untouched.
    for m in re.finditer(r"\b(\d[\d,]*(?:\.\d{1,2})?)\b",
                         _mask_dates(question or "")):
        try:
            _v = round(float(
                m.group(1).replace(",", "")), 2)
        except ValueError:
            continue
        if _v >= 100:
            base.add(_v)
    if pool_all:
        for _h in hits or ():
            _ex = _mask_dates((_h or {}).get("excerpt", ""))
            base |= _grab(_ex)
            for m in bare_re.finditer(_ex):
                try:
                    base.add(round(float(
                        m.group(1).replace(",", "")), 2))
                except ValueError:
                    pass

    parsed = _parse_citations(answer or "", hits or [])
    ungrounded = []

    def _near(val, scope):
        return any(abs(val - a) <= 0.011 for a in scope)

    def _figure_pass(seed_prior, flag):
        """One pass over the sentences. A sentence's figures
        ground on: base, the results of its explicit equations
        whose operands are in scope (the F5 rule, unchanged),
        its own scope below, and derivations (_derivable) of
        that scope plus the figures grounded so far. Returns
        (flags, grounded). v26.2 (fix 1): the caller runs the
        pass twice — the first pass collects every figure the
        answer grounds ANYWHERE (its shown steps may come
        after the lead sentence that states their result);
        the second pass, seeded with that set, does the
        flagging, so a result stated before its steps still
        grounds on them. Direct (non-derived) grounding stays
        sentence-local exactly as F6 ruled: the seed only ever
        feeds the DERIVATION test, never direct membership."""
        derived = set()
        grounded = set(seed_prior)
        flags = []
        for s in parsed["sentences"]:
            # v26.2 (fix 3): extraction reads date-masked text
            # (masking preserves length; only values are taken
            # from it, never offsets).
            text_s = _mask_dates(s["text"])
            scope = set(base) | derived
            # v26 (F6, D6 ruled 2026-10-15): sentence-local
            # scope for FIGURE grounding in scoped mode.
            # Through v25.1 the scope was the sentence's
            # block-attributed sources, so a citation block at
            # a group's end widened every earlier sentence's
            # figure scope to the block's excerpts — a wrong
            # figure in an early sentence grounded against a
            # later block's excerpt and the unit shipped it
            # `checked` (Phase 0 probe F6). In scoped mode
            # figures now ground against the excerpts the
            # sentence's OWN in-text markers cite (own_sources,
            # kept by _parse_citations). Block attribution
            # itself is unchanged (citation credit,
            # cited_claims, the uncited gate — the c3b7 fix),
            # and pooled mode (pool_all, Quick) keeps the block
            # scope exactly as designed.
            _scope_srcs = s["sources"] if pool_all else (
                s["own_sources"] if "own_sources" in s
                else s["sources"])
            for j in _scope_srcs:
                if 0 <= j < len(hits or []):
                    scope |= _grab(_mask_dates(
                        hits[j].get("excerpt", "")))
            eqs = []
            for m in _EQ_RE.finditer(text_s):
                try:
                    eqs.append((float(m.group(1).replace(",", "")),
                                float(m.group(3).replace(",", "")),
                                float(m.group(4).replace(",", ""))))
                except ValueError:
                    pass
            for m in _PAREN_EQ_RE.finditer(text_s):
                try:
                    eqs.append((float(m.group(2).replace(",", "")),
                                float(m.group(4).replace(",", "")),
                                float(m.group(1).replace(",", ""))))
                except ValueError:
                    pass
            for a, b, c in eqs:
                if _near(a, scope) and _near(b, scope):
                    derived.add(round(c, 2))
            scope |= derived
            # Grounding test: direct membership in this
            # sentence's own level-1 set (scope + one
            # whitelist step — sentence-local, as F6 ruled),
            # or one derivation step over the scope widened
            # by the grounded seed. The seed never grounds a
            # figure by membership alone.
            l1_scope = _derivation_level1(scope)
            l1_all = _derivation_level1(scope | grounded)
            for rx in (money_re, bare_re):
                for m in rx.finditer(text_s):
                    try:
                        val = round(float(
                            m.group(1).replace(",", "")), 2)
                    except ValueError:
                        continue
                    if val == 0:
                        continue
                    if _near(val, l1_scope) \
                            or _derivation_step(val, l1_all):
                        grounded.add(val)
                    elif flag and val not in flags:
                        flags.append(val)
        return flags, grounded

    _collect, answer_grounded = _figure_pass(set(), False)
    ungrounded, _g = _figure_pass(answer_grounded, True)
    if not ungrounded:
        return answer, []
    listed = ", ".join(f"${v:,.2f}" if v % 1 else f"${v:,.0f}"
                       for v in ungrounded[:8])
    note = ("\n\nFigure check (server): the following dollar figures "
            "in the answer above do not appear in the excerpts the "
            "answer cites, the question, or the verified figures: "
            + listed + ". Treat them with caution.")
    return answer + note, ungrounded


# --- v15: quantity checker (item 5) -------------------------------------------
# The figure checker proves WHERE a number came from; this
# proves the relationships BETWEEN numbers: durations against
# their registry periods, and period conversions (weekly <->
# monthly <-> annual) the draft itself states. Deterministic,
# run beside _check_figures, feeding the same objection
# machinery in _run_unit.
_MONTH_NAMES = ["january", "february", "march", "april", "may",
                "june", "july", "august", "september", "october",
                "november", "december"]
_MONTH_RX = ("(january|february|march|april|may|june|july|august|"
             "september|october|november|december)")
_MONEY_RX = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")


def _money_near(text, pos):
    """The dollar amount attached to a unit phrase at pos: the
    closest amount ENDING just before it ('$665 per week'), else
    the first amount starting just after ('per week: $665')."""
    best = None
    for m in _MONEY_RX.finditer(text, max(0, pos - 40), pos + 30):
        if m.end() <= pos + 4:
            best = m
        elif best is None and m.start() - pos <= 25:
            best = m
    return float(best.group(1).replace(",", "")) if best else None


def _check_quantities(answer, question="", mechanisms=None):
    """Deterministic quantity failures in a draft:
    (a) a matched mechanism's registry period (its 'period'
        field) stated as a confusable duration anywhere in
        the answer;
    (b) period conversions the draft states in BOTH units
        (weekly/monthly, monthly/annual, weekly/annual) that
        do not reconcile (factors 52/12, 12, 52; tolerance
        ~2% or $15);
    (c) a backpay exclusion end month that disagrees with the
        receipt month named in the question + the period.
    Returns [{mechanism, label}] shaped like mechanism
    failures.
    v18: `mechanisms` may be passed by the caller — a bundled
    unit passes ITS OWN matched mechanisms (through v17.3 the
    checker re-matched against the FULL question per part, so
    one part's mechanism failed another part's draft; pass 2
    item F3). R4: a confusable now fails only in a sentence
    that ASSERTS the period as the rule (excluded/exclusion/
    resource language, no disclaimer markers), so an answer
    quoting the registry's own warning text no longer fails
    itself."""
    fails = []
    low = (answer or "").lower()
    qlow = (question or "").lower()
    mech = mechanisms if mechanisms is not None else (
        _mechanism_matches(question) if qlow else [])
    sentences = _split_sentences(answer or "")
    for m in mech:
        period = m.get("period") or {}
        for conf in period.get("confusables", ()):
            for sent in sentences:
                sl = sent.lower()
                if conf not in sl:
                    continue
                asserted = any(w in sl for w in (
                    "excluded", "exclusion", "resource",
                    "resources", "counts", "counted"))
                disclaimed = any(w in sl for w in (
                    "not the", "is not", "must not", "n't",
                    "describes", "rather than", "payee"))
                if asserted and not disclaimed:
                    fails.append({
                        "mechanism": m["id"],
                        "label": (f"states a {conf} period where "
                                  f"the rule is {period['count']} "
                                  f"months: {period['note']}")})
                    break
    convs = [(r"\bper week\b|\bweekly\b|\ba week\b", "per week",
              r"\bper month\b|\bmonthly\b|\ba month\b", "per month",
              52.0 / 12.0),
             (r"\bper month\b|\bmonthly\b|\ba month\b", "per month",
              r"\bper year\b|\bannual\w*\b|\ba year\b", "per year",
              12.0),
             (r"\bper week\b|\bweekly\b|\ba week\b", "per week",
              r"\bper year\b|\bannual\w*\b|\ba year\b", "per year",
              52.0)]
    # v29: a conversion reconciles only WITHIN one sentence
    # (or one line / table row). The v28 CSHCS draft stated
    # each table row as two sentences (the annual figure,
    # then the monthly figure); the old cross-sentence
    # pairing took one row's ANNUAL figure as the "per
    # month" value (_money_near reaches back across the
    # boundary) and the next sentence's monthly figure as
    # the year value, failing a correct draft four times.
    # A draft that truly mis-states a conversion states
    # both units together in one sentence; that is still
    # caught, with the same factors and tolerance.
    segments = []
    _seg_start = 0
    for _sm in re.finditer(r"\n|(?<=[.!?])\s+", low):
        segments.append((_seg_start, _sm.start()))
        _seg_start = _sm.end()
    segments.append((_seg_start, len(low)))
    for rx_a, name_a, rx_b, name_b, factor in convs:
        for s0, s1 in segments:
            seg = low[s0:s1]
            for ma in re.finditer(rx_a, seg):
                va = _money_near(seg, ma.start())
                mb = re.search(rx_b, seg[ma.end():])
                if not mb:
                    continue
                vb = _money_near(seg, ma.end() + mb.start())
                if not va or not vb:
                    continue
                expect = va * factor
                if abs(vb - expect) > max(15.0, 0.02 * expect):
                    fails.append({
                        "mechanism": "quantity-conversion",
                        "label": (f"period conversion does not "
                                  f"reconcile: ${va:,.2f} {name_a} is "
                                  f"${expect:,.2f} {name_b}, not "
                                  f"${vb:,.2f}")})
    periods = [m for m in mech if m.get("period")]
    if periods:
        rm = re.search(_MONTH_RX, qlow)
        um = re.search(r"\buntil\s+" + _MONTH_RX, low)
        if rm and um:
            period = periods[0]["period"]
            ridx = _MONTH_NAMES.index(rm.group(1))
            expect_name = _MONTH_NAMES[(ridx + period["count"]) % 12]
            if um.group(1) != expect_name:
                fails.append({
                    "mechanism": periods[0]["id"],
                    "label": (f"exclusion end month is wrong: "
                              f"received in {rm.group(1)}, so the "
                              f"{period['count']}-month exclusion "
                              f"runs through {expect_name}, not "
                              f"{um.group(1)}")})
    return fails


# --- v15: source-conflict claims (item 6) --------------------------------------
# When the registry pins a figure and a retrieved source prints
# a NEAR-MISS variant of it (the $4,066.50 CMS vs $4,067 Indiana
# case), the answer must SURFACE the disagreement with
# provenance instead of silently picking one. Scope (the operator's
# decision): registry figure slots only. A conflict becomes a
# judgeable claim; a draft that omits either value fails it in
# code, whatever a judge says.
def _conflict_scan(hits):
    """Returns [{entry, registry_value, corpus_value, source,
    url}] for registry figures with a near-miss corpus variant
    in the ask's hits (within 2%, not equal — far enough that
    the figure checker would have let both through as one
    value's provenance, close enough to be the same fact
    stated two ways)."""
    out = []
    _today21 = _today_iso()
    for entry in VERIFIED_FIGURES:
        if not _entry_in_force(entry, _today21):
            continue  # v21 (WS2): conflicts compare against
            # the value in force TODAY, not a dated sibling
        terms = entry.get("conflict_terms") or (
            entry["title"].lower(),)
        corpus_vals = {}
        for h in hits or ():
            blob = ((h.get("title") or "") + " "
                    + (h.get("excerpt") or "")).lower()
            if not any(t in blob for t in terms):
                continue
            for tok in _MONEY_RX.findall(h.get("excerpt") or ""):
                try:
                    corpus_vals[float(tok.replace(",", ""))] = h
                except ValueError:
                    pass
        if not corpus_vals:
            continue
        for reg_val in entry.get("figures", ()):
            for cval, h in corpus_vals.items():
                if cval == reg_val:
                    continue
                if abs(cval - reg_val) <= 0.02 * max(reg_val, 1):
                    out.append({"entry": entry["id"],
                                "registry_value": reg_val,
                                "corpus_value": cval,
                                "source": h.get("title") or "",
                                "url": h.get("url") or ""})
    # v18 (F16): the conflict scan generalizes beyond dollars.
    # An entry may name discrete stale-value watches
    # ("conflict_numbers": kind + pattern + the old values to
    # watch for), so conflicts like the ABLE onset age (the
    # manual's 26 vs current law's 46, with no $ token
    # anywhere — undetectable by the dollar scan above) are
    # surfaced the same way.
    for entry in VERIFIED_FIGURES:
        if not _entry_in_force(entry, _today21):
            continue
        for spec in entry.get("conflict_numbers", ()):
            terms = spec.get("terms") or (entry["title"].lower(),)
            for h in hits or ():
                blob = ((h.get("title") or "") + " "
                        + (h.get("excerpt") or "")).lower()
                if not any(t in blob for t in terms):
                    continue
                for m in re.finditer(spec["pattern"], blob):
                    try:
                        cval = float(m.group(1))
                    except (TypeError, ValueError):
                        continue
                    if cval in spec.get("expect", ()) \
                            and cval != spec["value"]:
                        out.append({"entry": entry["id"],
                                    "kind": spec["kind"],
                                    "registry_value": float(
                                        spec["value"]),
                                    "corpus_value": cval,
                                    "source": h.get("title") or "",
                                    "url": h.get("url") or ""})
    return out


# v30 (checker exemption a-ii): sentence markers that NAME
# a source disagreement (see _conflict_claims).
_CONFLICT_NAMED_MARKERS = (
    "disagree", "conflict", "differ", "not consistent",
    "inconsistent", "two values", "both values",
    "another page", "one page", "some sources",
    "sources give", "pages give", "varies", "vary by",
    "depending on the source")


def _conflict_claims(conflicts, answer, engaged_ids=None):
    """Conflict records as claim texts, plus the code override
    map (claim text -> forced final) for drafts that do not
    present BOTH values. v18 (F16, pass 2 item F1): conflict
    claims are resolved IN CODE ONLY — _run_unit keeps them
    out of the judge-facing claim set entirely, so the forced
    finals no longer ride the Flash final rung either. The map
    is consumed as conflict_fail by _run_unit.
    v26.2 (fix 4): a conflict becomes a claim only when it is
    ENGAGED — the question matched the entry (engaged_ids,
    passed by the caller from the ask's registry matches), or
    the draft presents one of the two values in a sentence
    carrying one of the entry's conflict terms or title words
    (the draft is writing about that figure). Through v26.1
    the scan armed on ANY registry entry found in the hits,
    so an ask that never used the figure inherited a forced
    conflict (battery B19: the $812/$811.50 shelter pair
    forced on a patient-liability ask whose question matched
    no registry entry and whose draft presented neither
    value). An unengaged conflict is dropped entirely."""
    claims, forced = [], {}
    low = (answer or "").lower()
    sents = [s.lower() for s in _split_sentences(answer or "")]
    by_id = {e["id"]: e for e in VERIFIED_FIGURES}

    def _forms(v, kind):
        if kind == "age":
            return (f"age {int(v)}", f"age of {int(v)}",
                    f"before age {int(v)}")
        return (f"${v:,.2f}", f"${v:,.0f}", f"{v:,.2f}",
                f"{v:,.0f}")

    def _eforms(v, kind):
        # v26.2 (fix 4): engagement probes also count the
        # comma-less bare form ("2705 dollars") — a shape
        # drafters actually write, which _forms' $-and-comma
        # forms miss, so an on-topic draft would read as
        # never presenting the value and its conflict would
        # drop. Engagement only: the both-values test keeps
        # _forms exactly as before.
        forms = list(_forms(v, kind))
        if kind != "age":
            try:
                fv = float(v)
                forms.append(f"{fv:.0f}" if fv == int(fv)
                             else f"{fv}")
            except (TypeError, ValueError):
                pass
        return tuple(forms)

    for cf in conflicts:
        kind = cf.get("kind")
        if kind == "age":
            rv, cv = int(cf["registry_value"]), int(
                cf["corpus_value"])
        else:
            rv, cv = cf["registry_value"], cf["corpus_value"]

        def _has(v, _kind=kind):
            return any(f in low for f in _forms(v, _kind))

        both = _has(rv) and _has(cv)
        engaged = engaged_ids is not None \
            and cf.get("entry") in engaged_ids
        if not engaged and (any(f in low for f in _eforms(rv, kind))
                            or any(f in low
                                   for f in _eforms(cv, kind))):
            entry = by_id.get(cf.get("entry")) or {}
            terms = [t.lower() for t in
                     (entry.get("conflict_terms") or ())]
            terms += [w for w in re.findall(
                r"[a-z]{4,}", (entry.get("title") or "").lower())
                if w not in terms]
            for s in sents:
                if not any(t in s for t in terms):
                    continue
                if any(f in s for f in _eforms(rv, kind)) \
                        or any(f in s for f in _eforms(cv, kind)):
                    engaged = True
                    break
        if not engaged:
            continue
        if kind == "age":
            text = (f"The answer surfaces the source disagreement "
                    f"on {cf['entry']}: sources give both age {rv} "
                    f"and age {cv} (corpus variant from "
                    f"{cf['source']}), instead of presenting one "
                    f"value as settled.")
        else:
            text = (f"The answer surfaces the source disagreement on "
                    f"{cf['entry']}: sources give both "
                    f"${rv:,.2f} and "
                    f"${cv:,.2f} (corpus variant from "
                    f"{cf['source']}), instead of presenting one "
                    f"figure as settled.")
        claims.append({"text": text, "load_bearing": True,
                       "sources": [], "origin": "conflict"})
        if not both:
            # v30 (checker exemption a-ii): a draft that
            # NAMES the disagreement has surfaced the
            # conflict in prose: a disagreement-marker
            # sentence anywhere in the draft, while the
            # draft presents one of THIS pair's two values.
            # Forcing "contradicted" for not restating both
            # figures killed drafts that held the keyed
            # content (Battery 2's U03-F). The pair's own
            # value is required: the shelter pair in the
            # v262 F4 fixture is presented nowhere in that
            # draft, so its forced flag stands even though
            # another pair of the same entry is discussed.
            # The claim still records the conflict; only
            # the forced kill lifts.
            named = any(
                any(mk in s
                    for mk in _CONFLICT_NAMED_MARKERS)
                for s in sents) and (
                any(f in low for f in _eforms(rv, kind))
                or any(f in low for f in _eforms(cv, kind)))
            if not named:
                forced[text] = "contradicted"
    return claims, forced


# --- v13: verified-figures registry --------------------------------------
# Hand-verified figures and rules for the topics where graded runs kept
# picking the wrong number from competing excerpts (v12 battery: Q5
# backpay, P1 SNAP limit, uncle CSRA, Q7 MMMNA, P5 difficulty of care).
# A matched entry is (a) injected into the prompt as an authoritative
# block that outranks the excerpts and (b) fed to the figure checker as
# allowed values. Matching is data-driven: EVERY group in an entry must
# hit, and a group hits when any of its word tokens or substring phrases
# appears in the question. Groups are deliberately narrow so a plain
# question never pulls an entry it does not need.
VERIFIED_FIGURES = [
    {
        "id": "backpay_exclusion",
        "title": "Retroactive benefits exclusion (nine months)",
        "groups": [
            {"tokens": {"backpay"},
             "phrases": ("back pay", "lump sum", "lump-sum",
                         "retroactive benefit", "retroactive ssi",
                         "retroactive social security",
                         "retroactive payment", "past-due",
                         "past due",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — money the agency owed from
                         # earlier months, paid all at once.
                         "owed me money",
                         "owed me money from earlier months",
                         "money from earlier months",
                         "social security owed me",
                         "paid it all at once")},
        ],
        "statement": (
            "Retroactive (past-due) SSI or Social Security benefits, "
            "including SSDI backpay, are excluded from countable "
            "resources for up to NINE MONTHS after the month they are "
            "received. The six-month figure found in representative-"
            "payee lesson material describes payee account handling; "
            "it is NOT the general exclusion and must not be given as "
            "the answer. For SNAP, a nonrecurring lump-sum payment is "
            "excluded from income and counts as a resource only in "
            "the month it is received."),
        "source": ("SSA SSI Resources page ('retroactive SSI or "
                   "Social Security benefits for up to nine months') "
                   "and POMS; 7 CFR 273.9(c)(8) for SNAP lump sums"),
        "figures": (),
    },
    {
        "id": "able_onset_age",
        "title": "ABLE account onset age (before 46, not 26)",
        "groups": [
            {"tokens": {"able"},
             "phrases": ("able account", "able program",
                         "529a", "investable")},
            {"tokens": set(),
             "phrases": ("onset age", "onset of disability",
                         "age of onset", "became disabled",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — the age the disability
                         # started, asked without the word
                         # "onset".
                         "oldest age",
                         "disability could have started",
                         "age my disability could have started",
                         "disability started",
                         "when my disability started",
                         "disability began")},
        ],
        "statement": (
            "ABLE account eligibility requires the disability's "
            "onset BEFORE age 46 (the ABLE Age Adjustment Act; "
            "effective January 1, 2026). The older 'before 26' "
            "figure in older Indiana manual material is stale and "
            "must not be presented as current. The 2026 annual "
            "contribution cap is $20,000 (the $19,000 figure was "
            "the 2025 limit); the first $100,000 in an "
            "ABLE account is disregarded for SSI; transportation is "
            "a qualified disability expense; Indiana's INvestABLE "
            "plan is exempt for Indiana Medicaid (DFR Policy Manual "
            "section 2615.10.35) and contributions earn a 20% state "
            "tax credit up to $1,000 ($500 if married filing "
            "separately)."),
        "source": ("IRS ABLE guidance (age 46, 2026) and DFR Policy "
                   "Manual section 2615.10.35 (INvestABLE)"),
        # v22 registry fix: the contribution-limit figures
        # ($20,000 / $19,000) MOVED to the dated pair
        # able_contribution_limit (+ _legacy) appended below:
        # this entry's ANDed onset-vocabulary group kept the
        # registry from ever engaging on a pure contribution
        # question (the 2026-10-14 live v21 failure). The
        # $100,000 SSI disregard and the tax credit stay.
        # v29: the credit cap corrected to the verified
        # general cap $1,000 (20% of contributions; $500 is
        # only the married-filing-separately cap, per
        # indiana_able_tax_credit / HB 1303 fiscal note).
        "figures": (100000.0, 1000.0, 500.0),
        # v18 (F16): the discrete stale-value watch list for
        # the generalized conflict scan below.
        "conflict_numbers": (
            {"kind": "age", "value": 46,
             "pattern": r"before (?:the )?age (?:of )?(\d{2})\b",
             "expect": (26,),
             "terms": ("able", "investable", "529a")},
        ),
    },
    {
        "id": "snap_resource_limit_in",
        "title": "Indiana SNAP resource limit ($5,000 under BBCE)",
        "groups": [
            {"tokens": {"snap", "ebt"},
             "phrases": ("food stamps", "food assistance",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — the card and the grocery help
                         # itself, never the program's initials.
                         "grocery help", "help buying groceries",
                         "food help", "help with groceries")},
            {"tokens": {"resource", "resources", "asset", "assets",
                        "limit", "lump",
                        # v22.1: where laypeople keep the money.
                        "bank", "money", "savings", "saving"},
             "phrases": ("resource limit", "asset limit")},
        ],
        "statement": (
            "The Indiana SNAP resource limit is $5,000 for households "
            "under BBCE (broad-based categorical eligibility). The "
            "$3,000 figure is the federal general SNAP resource limit; "
            "it is NOT the Indiana limit and must not be presented as "
            "the limit for an Indiana SNAP household."),
        "source": "FNS SNAP state page for Indiana (BBCE)",
        "figures": (5000.0, 3000.0),
    },
    {
        "id": "csra",
        "title": "Community Spouse Resource Allowance (CSRA)",
        "groups": [
            {"tokens": set(),
             "phrases": ("community spouse", "csra",
                         "spousal resource",
                         "community spouse resource",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — askers say "my wife" / "my
                         # husband", never "community spouse"
                         # (the 2026-10-14 live CSRA failure).
                         "my wife", "my husband", "my spouse",
                         "wife can keep", "husband can keep",
                         "spouse can keep", "spouse resource",
                         "spouse resources")},
            # v22.1: singular/plural pairs — the matcher is
            # exact-token, so "resource" never matched the
            # token "resources" (and vice versa).
            {"tokens": {"savings", "saving", "assets", "asset",
                        "resources", "resource", "countable",
                        # v26.2 (fix 8): the vague-worry shape
                        # ("will my wife be okay financially")
                        # names no money noun at all, and the
                        # battery's B06 matched no entry because
                        # of it. Financial-worry terms only —
                        # facility words stay out, so a question
                        # merely about a spouse in a facility
                        # cannot pull this block.
                        "financially"},
             "phrases": ("be okay", "okay financially",
                         "financially okay", "afford",
                         "protected", "lose everything",
                         "left with nothing", "taken care of")},
        ],
        "statement": (
            "The community spouse resource allowance is ONE HALF of "
            "the couple's combined countable assets measured at the "
            "snapshot date, clamped to a minimum of $32,532 and a "
            "maximum of $162,660 (2026). Do NOT subtract the "
            "applicant's $2,000 allowance, or anything else, before "
            "halving."),
        "source": "CMS 2026 spousal impoverishment standards",
        "figures": (32532.0, 162660.0),
    },
    {
        "id": "mmmna",
        "title": "Community spouse monthly income allowance (MMMNA)",
        "groups": [
            {"tokens": set(),
             "phrases": ("community spouse", "mmmna",
                         "monthly maintenance needs",
                         "spouse of a medicaid applicant",
                         "spousal income",
                         "how much income can the spouse",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — the spouse named plainly.
                         "my wife", "my husband", "my spouse",
                         "husband's check", "wife's check",
                         "spouse's check", "husband's income",
                         "wife's income", "spouse's income")},
            {"tokens": {"income", "check", "checks",
                        # v26.2 (fix 8): same vague-worry
                        # widening as the CSRA entry — B06's
                        # worry is about the spouse's income
                        # and resources together, so both
                        # spousal entries take the same terms.
                        "financially"},
             "phrases": ("keep each month",
                         "maintenance needs allowance",
                         "maximum monthly maintenance",
                         # v22.1: the monthly benefit check is
                         # how askers name the income.
                         "monthly check",
                         "his monthly check", "her monthly check",
                         "keep to live on",
                         # v26.2 (fix 8): the worry phrases.
                         "be okay", "okay financially",
                         "financially okay", "afford",
                         "protected", "lose everything",
                         "left with nothing", "taken care of")},
        ],
        "statement": (
            "The community spouse may keep between $2,705.00 and "
            "$4,066.50 per month of the institutionalized spouse's "
            "income as the monthly maintenance needs allowance (2026); "
            "where the amount lands in that range depends on the "
            "couple's housing costs. Indiana's ILTCP page prints the "
            "maximum rounded as $4,067; the CMS figure is $4,066.50. "
            "The housing (shelter) allowance component is $811.50 "
            "(Indiana's own policy manual, IHCPPM 3010.20.10, "
            "prints the shelter standard as $812)."),
        "source": ("CMS 2026 spousal impoverishment standards; "
                   "Indiana ILTCP (prints the max as $4,067)"),
        "figures": (2705.0, 4066.50, 4067.0, 811.50, 812.0),
        # v15 (item 6): the seeded conflict slot. The registry
        # carries BOTH published maxima, so the near-miss scan
        # arms whenever a corpus excerpt states either one in
        # MMMNA context; the answer must show both with sources.
        "conflict_terms": ("mmmna", "monthly maintenance needs",
                           "maintenance needs allowance"),
    },
    {
        "id": "home_equity_limit",
        "title": "Medicaid home equity limit (2026)",
        "groups": [
            {"tokens": set(), "phrases": ("home equity",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — askers ask what the house is
                         # WORTH, never its "equity".
                         "house is worth", "my house is worth",
                         "our house is worth", "home is worth",
                         "how much my house is worth",
                         "value of my house", "value of our house",
                         "my home's value", "worth of my house")},
        ],
        "statement": (
            "Indiana's Medicaid home equity limit is $752,000 "
            "(effective January 1, 2026): an applicant whose equity "
            "interest in the home exceeds $752,000 is ineligible "
            "for long-term care services. Indiana sits at the "
            "federal floor of the range states may elect within "
            "($752,000 to $1,130,000); it has not chosen a higher "
            "figure. Exceptions apply (a spouse, minor or disabled "
            "child, or a sibling with an equity interest lawfully "
            "living in the home)."),
        "source": ("IHCPPM 3005.10.05 (Indiana's $752,000 limit, "
                   "eff. 1/1/2026); CMS 2026 home equity range"),
        "figures": (752000.0, 1130000.0),
    },
    {
        "id": "asset_limit_2000",
        "title": "The $2,000 individual asset limit",
        "groups": [
            {"tokens": set(),
             "phrases": ("$2,000", "asset limit", "resource limit",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — money in the bank + Medicaid,
                         # kept Medicaid-specific so the SNAP
                         # bank question cannot cross-match.
                         "money can i have in the bank and still "
                         "get medicaid",
                         "have in the bank and still get medicaid",
                         "money in the bank and still get medicaid",
                         "in the bank and still qualify for medicaid",
                         "bank account and still get medicaid",
                         "how much savings can i have and still "
                         "get medicaid")},
        ],
        "statement": (
            "The individual resource (asset) limit for SSI and most "
            "Indiana Medicaid categories is $2,000 ($3,000 for a "
            "couple), and resources are counted as of the first "
            "moment of the month."),
        "source": ("SSA SSI resource limits; POMS SI 01110.600 "
                   "(first-of-the-month counting)"),
        "figures": (2000.0, 3000.0),
    },
    {
        "id": "waiver_income_cap",
        "title": "HCBS waiver income cap ($2,982/mo)",
        "groups": [
            {"tokens": {"waiver"},
             "phrases": ("medicaid waiver",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — askers describe the help at
                         # home, never the word "waiver".
                         "help at home", "care at home",
                         "home care", "in-home care", "in-home help",
                         "help in my home", "care in my home")},
            {"tokens": {"earn", "earns", "make", "makes",
                        "earning", "earnings"},
             "phrases": ("income limit", "income cap",
                         "how much money can i make",
                         "money can i make each month",
                         "make each month and still qualify",
                         "earn each month and still qualify")},
        ],
        "statement": (
            "The Indiana HCBS waiver income cap is $2,982 per month "
            "(2026: 300% of the SSI federal benefit rate). Only the "
            "applicant's own income counts toward it; a spouse's "
            "income, including a spouse's caregiver pay, is not the "
            "applicant's income. Indiana had not posted a 2027 cap "
            "as of October 2026, so do not present the 2026 figure "
            "as the 2027 cap."),
        "source": "Indiana HCBS waiver standards (2026)",
        "figures": (2982.0,),
    },
    {
        "id": "ssa_2027_figures",
        "title": "SSA 2027 amounts (COLA 3.6%)",
        "groups": [
            {"tokens": {"ssi", "sga", "ssdi", "twp"},
             "phrases": ("social security", "cola",
                         "cost-of-living", "cost of living",
                         "substantial gainful activity",
                         "trial work")},
            {"tokens": set(),
             "phrases": ("2027", "next year", "january")},
        ],
        "statement": (
            "For 2027, Social Security benefits rise 3.6% under "
            "the cost-of-living adjustment (effective with the "
            "December 2026 benefits paid in January 2027). The "
            "2027 SSI federal benefit rate is $1,029 per month "
            "for an individual and $1,544 for a couple. The 2027 "
            "substantial gainful activity (SGA) limit is $1,770 "
            "per month for non-blind individuals and $2,950 for "
            "blind individuals. The 2027 trial work period "
            "threshold is $1,270 per month. SSI resource limits "
            "are unchanged at $2,000 for an individual and "
            "$3,000 for a couple."),
        "source": ("SSA press release and 2027 COLA fact sheet "
                   "(October 14, 2026)"),
        "figures": (1029.0, 1544.0, 1770.0, 2950.0, 1270.0,
                    2000.0, 3000.0),
    },
    {
        "id": "difficulty_of_care",
        "title": "Difficulty-of-care exclusion (caregiver pay)",
        "groups": [
            {"tokens": {"caregiver", "caregiving"},
             "phrases": ("caregiver pay", "paid caregiver",
                         "difficulty of care",
                         # v22.1: lay phrasing (registry trigger
                         # audit) — the arrangement described,
                         # never the word "caregiver".
                         "gets paid to take care",
                         "paid to take care",
                         "take care of me at home",
                         "care for me at home",
                         "paid to care for",
                         "wife gets paid", "husband gets paid",
                         "spouse gets paid")},
            {"tokens": {"hip", "magi"},
             "phrases": ("healthy indiana", "household income")},
        ],
        "statement": (
            "Medicaid waiver payments to a caregiver who lives with "
            "the person they care for, including Structured Family "
            "Caregiving pay, are difficulty-of-care payments excluded "
            "from income under IRS Notice 2014-7. For HIP and other "
            "MAGI-based coverage the pay is excludable: it does not "
            "count as household income."),
        "source": "IRS Notice 2014-7 and IRS difficulty-of-care Q&A",
        "figures": (),
    },
]


# --- v21 (WS1b + WS2): registry topics + effective dating --------------
# The v13-v19 entries above are hand-verified prose; v21 adds
# two fields to every entry WITHOUT rewriting their statements:
#   "topic": {"phrases": ((search phrase, pin term), ...),
#             "doc_hint": {"title": substr, "url": substr}}
#       the retrieval pointer (WS1b): when a question matches
#       the entry, hybrid_search pins the source chunk that
#       carries the figure into the candidate pool (rg channel),
#       chosen doc-hint first, then pin-term, then top hit.
#   "valid_from" / "valid_to": ISO dates or None (WS2). An
#       entry serves only inside its window; entries sharing a
#       "family" key are date-exclusive alternatives of one
#       figure. "history" carries superseded values with their
#       own windows: a question ABOUT a past period still
#       matches the entry (its statement is date-aware prose)
#       and _registry_values admits the historical figure.
#   "successor": a note about the next period's value that is
#       NOT verified (never a figure, never served as one).
# The metadata lives in this augmentation block so the
# verified statements above stay byte-identical to v19/v20;
# the dates are backfilled from each entry's own source line
# and the verified record (see v21/STATUS.md for the per-entry
# grounding). An open window (None) means "no bound in the
# record", never an invented date.
_V21_REGISTRY_META = {
    "backpay_exclusion": {
        "valid_from": None, "valid_to": None,
        "topic": {
            "phrases": (
                ("retroactive SSI Social Security benefits "
                 "excluded nine months", "nine months"),
                ("ssi resources retroactive benefits excluded",
                 "retroactive")),
            "doc_hint": {"title": "SSI Resources",
                         "url": "text-resources-ussi"}}},
    "able_onset_age": {
        "valid_from": "2026-01-01", "valid_to": None,
        # v22 registry fix: the 2025 $19,000 history moved
        # with the contribution figures to the dated pair
        # able_contribution_limit (+ _legacy) below.
        "topic": {
            "phrases": (
                ("able account contribution limit investable",
                 "able"),
                ("able accounts annual contribution", "able")),
            "doc_hint": {"title": "able", "url": "irs.gov"}}},
    "snap_resource_limit_in": {
        "valid_from": None, "valid_to": None,
        "topic": {
            "phrases": (
                ("snap food assistance resource limit indiana",
                 "resource"),
                ("indiana snap households limited resources "
                 "5000", "snap"),
                ("indiana snap broad-based categorical "
                 "eligibility resource limit", "resource")),
            "doc_hint": {
                "title": "SNAP (Food Assistance)",
                "url": "snap-food-assistance"}}},
    "csra": {
        "valid_from": "2026-01-01", "valid_to": "2026-12-31",
        "topic": {
            "phrases": (
                ("spousal impoverishment standards community "
                 "spouse", "spousal impoverishment"),
                ("spousal impoverishment protection law "
                 "assets half", "spousal impoverishment"),
                ("community spouse resource allowance 2026",
                 "162,660")),
            "doc_hint": {"title": "3010.20.10",
                         "url": "3000.pdf"}}},
    "mmmna": {
        # The entry's stated minimum ($2,705.00) is the
        # July-December 2026 figure (CMS spousal standards
        # revise the minimum each July 1); the pre-July 2026
        # minimum is not in the verified record, so the
        # window starts 2026-07-01 rather than inventing one.
        "valid_from": "2026-07-01", "valid_to": "2026-12-31",
        "topic": {
            "phrases": (
                ("spousal impoverishment standards monthly "
                 "maintenance needs allowance",
                 "monthly maintenance needs allowance"),
                ("spousal impoverishment maintenance standard "
                 "shelter allowance", "spousal impoverishment"),),
            "doc_hint": {"title": "3010.20.10",
                         "url": "3000.pdf"}}},
    "home_equity_limit": {
        "valid_from": "2026-01-01", "valid_to": None,
        "topic": {
            "phrases": (
                ("home equity limitation medicaid 3005.10.05",
                 "home equity"),),
            "doc_hint": {"title": "3005.10.05",
                         "url": "3000.pdf"}}},
    "asset_limit_2000": {
        "valid_from": None, "valid_to": None,
        "topic": {
            "phrases": (
                ("supplemental security income resource limits "
                 "2000 individual", "resource limits"),),
            "doc_hint": {"title": "SSI Resources",
                         "url": "ssi"}}},
    "waiver_income_cap": {
        "valid_from": "2026-01-01", "valid_to": "2026-12-31",
        "successor": (
            "The 2027 cap had NOT been posted by Indiana as of "
            "2026-10-14. 300% of the 2027 SSI federal benefit "
            "rate ($1,029) implies about $3,087, but that value "
            "is UNCONFIRMED: it is not a registry figure and "
            "must never be served as verified."),
        "topic": {
            "phrases": (
                ("hcbs waiver income limit 2982 special income "
                 "level", "special income level"),
                ("indiana medicaid waiver income limit",
                 "income limit")),
            "doc_hint": {"title": "Eligibility Guide",
                         "url": "in.gov"}}},
    "ssa_2027_figures": {
        "valid_from": "2027-01-01", "valid_to": "2027-12-31",
        "topic": {
            "phrases": (
                ("social security 2027 cost of living "
                 "adjustment fact sheet", "2027"),
                ("substantial gainful activity 2027 trial work "
                 "period", "trial work")),
            "doc_hint": {"title": "2027",
                         "url": "ssa.gov/news"}}},
    "difficulty_of_care": {
        "valid_from": None, "valid_to": None,
        "topic": {
            "phrases": (
                ("difficulty of care medicaid waiver payments "
                 "excludable income", "difficulty of care"),),
            "doc_hint": {"title": "difficulty of care",
                         "url": "irs.gov"}}},
}
for _e21 in VERIFIED_FIGURES:
    _m21 = _V21_REGISTRY_META.get(_e21["id"])
    if _m21:
        _e21.update(_m21)

# v21 (WS2): the transfer penalty divisor joins the registry
# as a DATED PAIR (family "transfer_penalty_divisor"). It was
# NOT a v13-v20 registry entry (the figure lived only in the
# corpus, IHCPPM 3006.00.00, which is exactly why the
# 2026-10-14 divisor ask had no registry help). Both values
# are in the verified record: $8,027 for applications on or
# after July 1, 2026; $7,651 before.
_DIVISOR_GROUPS = [
    {"tokens": set(),
     "phrases": ("transfer penalty", "penalty divisor",
                 "transfer divisor", "penalty period",
                 "asset transfer",
                 # v22.1: lay phrasing (registry trigger
                 # audit) — askers describe giving property to
                 # their kids and being penalized, never the
                 # manual's "transfer penalty divisor".
                 "give property to my kids",
                 "gave property to my kids",
                 "give away property", "gave away property",
                 "giving away property", "property to my kids",
                 "how long will i be penalized",
                 "how long would i be penalized",
                 "penalty for giving away property")},
]
VERIFIED_FIGURES += [
    {
        "id": "transfer_penalty_divisor_current",
        "family": "transfer_penalty_divisor",
        "title": ("Indiana Medicaid transfer penalty divisor "
                  "($8,027, applications from July 2026)"),
        "groups": _DIVISOR_GROUPS,
        "statement": (
            "Indiana's Medicaid transfer penalty divisor is "
            "$8,027 per month for applications filed on or "
            "after July 1, 2026: the penalty period for an "
            "uncompensated asset transfer is the transfer "
            "amount divided by $8,027 (the average monthly "
            "cost of private nursing facility care, IHCPPM "
            "3006.00.00). For applications filed before July "
            "1, 2026, the divisor was $7,651."),
        "source": ("IHCPPM 3006.00.00 (average monthly cost "
                   "of private nursing facility care)"),
        "figures": (8027.0,),
        "valid_from": "2026-07-01", "valid_to": None,
        "topic": {
            "phrases": (
                ("average monthly cost private nursing "
                 "facility care transfer penalty divisor",
                 "divisor"),
                ("transfer penalty divisor 3006.00.00",
                 "3006.00.00")),
            "doc_hint": {"title": "3006.00.00",
                         "url": "3000.pdf"}},
    },
    {
        "id": "transfer_penalty_divisor_legacy",
        "family": "transfer_penalty_divisor",
        "title": ("Indiana Medicaid transfer penalty divisor "
                  "($7,651, applications before July 2026)"),
        "groups": _DIVISOR_GROUPS,
        "statement": (
            "For Indiana Medicaid applications filed before "
            "July 1, 2026, the transfer penalty divisor was "
            "$7,651 per month (the average monthly cost of "
            "private nursing facility care, IHCPPM "
            "3006.00.00). For applications filed on or after "
            "July 1, 2026, the divisor is $8,027."),
        "source": ("IHCPPM 3006.00.00 (average monthly cost "
                   "of private nursing facility care)"),
        "figures": (7651.0,),
        "valid_from": None, "valid_to": "2026-06-30",
        "topic": {
            "phrases": (
                ("average monthly cost private nursing "
                 "facility care transfer penalty divisor",
                 "divisor"),
                ("transfer penalty divisor 3006.00.00",
                 "3006.00.00")),
            "doc_hint": {"title": "3006.00.00",
                         "url": "3000.pdf"}},
    },
]

# v22 registry fix: the ABLE annual contribution limit joins
# as a DATED PAIR (family "able_contribution_limit"), the
# same WS2 pattern as the divisor pair. It used to live
# inside able_onset_age (figures + a 2025 history row), whose
# second trigger group demands onset vocabulary ("onset
# age", "became disabled", ...): the groups are ANDed, so a
# pure contribution question NEVER matched any registry entry
# and the dated machinery never engaged (live v21 failure,
# 2026-10-14: "What was the maximum able account
# contribution for 2025?" answered "I don't know the 2025
# figure" under verified_single with $19,000 admissible but
# unreachable). A pair, not one entry + history, because
# _registry_values admits an entry's base figures for every
# date: only the family/date machinery keeps $20,000 out of
# a 2025 question's allowed set. Both values are in the
# verified record and in the corpus carriers (SSA POMS SI
# 01130.740 prints the year table; the IRS newsroom page
# states the 2025 sentence verbatim).
_ABLE_CONTRIBUTION_GROUPS = [
    {"tokens": {"able"},
     "phrases": ("able account", "able program",
                 "529a", "investable")},
    {"tokens": {"contribution", "contributions", "contribute",
                "contributing", "limit", "maximum", "annual"},
     "phrases": ("contribution limit", "annual contribution",
                 "maximum contribution", "contribution cap",
                 "how much can i contribute",
                 "how much can you contribute",
                 "how much can be contributed")},
]
VERIFIED_FIGURES += [
    {
        "id": "able_contribution_limit",
        "family": "able_contribution_limit",
        "title": ("ABLE account annual contribution limit "
                  "($20,000 for 2026)"),
        "groups": _ABLE_CONTRIBUTION_GROUPS,
        "statement": (
            "The annual contribution limit for an ABLE "
            "account is $20,000 for 2026. For 2025 the limit "
            "was $19,000. (The first $100,000 in the account "
            "is separately disregarded for SSI; that is a "
            "balance disregard, not the contribution limit.)"),
        "source": ("SSA POMS SI 01130.740 (annual "
                   "contribution limit by calendar year: 2025 "
                   "$19,000 / 2026 $20,000); IRS newsroom "
                   "('The contribution limit for 2025 is "
                   "$19,000')"),
        "figures": (20000.0,),
        "valid_from": "2026-01-01", "valid_to": None,
        "topic": {
            "phrases": (
                ("able account annual contribution limit "
                 "by calendar year", "20,000"),
                ("able accounts annual contribution limit",
                 "able")),
            "doc_hint": {"title": "01130.740",
                         "url": "0501130740"}},
    },
    {
        "id": "able_contribution_limit_legacy",
        "family": "able_contribution_limit",
        "title": ("ABLE account annual contribution limit "
                  "($19,000 for 2025)"),
        "groups": _ABLE_CONTRIBUTION_GROUPS,
        "statement": (
            "For 2025, the annual contribution limit for an "
            "ABLE account was $19,000. The limit rose to "
            "$20,000 for 2026."),
        "source": ("SSA POMS SI 01130.740 (annual "
                   "contribution limit by calendar year: 2025 "
                   "$19,000 / 2026 $20,000); IRS newsroom "
                   "('The contribution limit for 2025 is "
                   "$19,000')"),
        "figures": (19000.0,),
        "valid_from": "2025-01-01", "valid_to": "2025-12-31",
        "topic": {
            "phrases": (
                ("able savings account contribution limit "
                 "2025", "19,000"),
                ("able account annual contribution limit "
                 "by calendar year", "19,000")),
            "doc_hint": {
                "title": "able savings account",
                "url": ("newsroom/people-paying-"
                        "disability-related")}},
    },
]


# v27 (Change 4, REGISTRY_PREP Class R item 8): the DD appeal
# clocks, verified directly against the DDARS HCBS Waivers
# Provider Manual text (Section 7's time-limits list) during
# the registry prep. The clocks are day counts, not dollars,
# so the entry carries figures=() (the backpay_exclusion
# pattern): serving them as checker "figures" would wrongly
# admit $33 / $30 / $15 / $18 as grounded dollar amounts.
VERIFIED_FIGURES += [
    {
        "id": "dd_appeal_clocks",
        "title": "DD waiver appeal deadlines (33 / 30 / 15 / 18 calendar days)",
        "groups": [
            {"tokens": {"ddars", "cih"},
             "phrases": ("family supports", "fs waiver",
                         "developmental disability",
                         "developmental disabilities",
                         "dd waiver", "waiver service authorization",
                         "objective-based allocation",
                         "reserved capacity", "priority criteria")},
            {"tokens": {"appeal", "appeals", "deadline"},
             "phrases": ("how long do i have to appeal",
                         "appeal deadline", "time to appeal",
                         "appeal a denial", "appeal the decision",
                         "appeal my allocation", "appeal a decision")},
        ],
        "statement": (
            "DD waiver appeal deadlines run from the decision / "
            "service authorization date, in calendar days: waiver "
            "service authorization appeals must be received within "
            "33 calendar days; CIH objective-based allocation "
            "(OBA) appeals within 30 calendar days; DD eligibility "
            "appeals and level-of-care (LOC) appeals within 15 "
            "calendar days; and an appeal of a denial for entrance "
            "to the waiver via priority (reserved-capacity) "
            "criteria within 18 calendar days."),
        "source": ("DDARS HCBS Waivers Provider Manual, "
                   "Section 7 (Appeal Process), time limits for "
                   "requesting appeals"),
        "figures": (),
    },
]

# v28 (decision f6): tax credits join the registry as
# verified figures, NOT corpus text — the figures change
# every tax year and stale corpus text is exactly how the
# section 3006 divisor went wrong. Every figure below is
# traced to a source saved in v28/sources/ (IRS EITC tables
# page for TY2025; Rev. Proc. 2025-32 + IRS newsroom
# IR-2025-103 for TY2026; IRS Child Tax Credit page for
# TY2025; Indiana LSA fiscal note LS 6935 for HB 1303 +
# Indiana DOR Schedule IN-ABLE for the Indiana ABLE
# credit). No front-door rows: registry entries only.
_EITC_GROUPS = [
    {"tokens": {"eitc"},
     "phrases": ("earned income tax credit",
                 "earned income credit",
                 "earned income tax credits")},
]
VERIFIED_FIGURES += [
    {
        "id": "eitc_amounts_2026",
        "family": "eitc_amounts",
        "title": ("Earned Income Tax Credit maximums "
                  "(tax year 2026)"),
        "groups": _EITC_GROUPS,
        "statement": (
            "For tax year 2026 the federal Earned Income "
            "Tax Credit (EITC) maximums are $8,231 (three "
            "or more qualifying children), $7,316 (two), "
            "$4,427 (one), and $664 (none). The credit "
            "reaches zero at AGI of $70,244 / $65,899 / "
            "$58,863 / $26,820 for married filing jointly "
            "(three or more / two / one / none) and "
            "$62,974 / $58,629 / $51,593 / $19,540 for "
            "other filing statuses. Investment income over "
            "$12,200 disqualifies the credit entirely. "
            "For tax year 2025 the maximums were $8,046 / "
            "$7,152 / $4,328 / $649."),
        "source": ("IRS Revenue Procedure 2025-32, "
                   "section .06 (TY2026 EITC table); IRS "
                   "newsroom IR-2025-103 (TY2026 maximum "
                   "$8,231, up from $8,046 for TY2025)"),
        "figures": (8231.0, 7316.0, 4427.0, 664.0, 12200.0,
                    70244.0, 65899.0, 58863.0, 26820.0,
                    62974.0, 58629.0, 51593.0, 19540.0),
        "valid_from": "2026-01-01", "valid_to": "2026-12-31",
    },
    {
        "id": "eitc_amounts_2025",
        "family": "eitc_amounts",
        "title": ("Earned Income Tax Credit maximums "
                  "(tax year 2025)"),
        "groups": _EITC_GROUPS,
        "statement": (
            "For tax year 2025 the federal Earned Income "
            "Tax Credit (EITC) maximums were $8,046 "
            "(three or more qualifying children), $7,152 "
            "(two), $4,328 (one), and $649 (none). AGI "
            "had to be under $61,555 / $57,310 / $50,434 "
            "/ $19,104 filing single, head of household, "
            "or married filing separately (three or more "
            "/ two / one / none), or $68,675 / $64,430 / "
            "$57,554 / $26,214 married filing jointly, "
            "and investment income could not exceed "
            "$11,950. For tax year 2026 the maximums are "
            "$8,231 / $7,316 / $4,427 / $664."),
        "source": ("IRS Earned Income Tax Credit tables "
                   "page, tax year 2025 (AGI limits, "
                   "investment income limit $11,950, "
                   "maximum credit amounts)"),
        "figures": (8046.0, 7152.0, 4328.0, 649.0, 11950.0,
                    61555.0, 57310.0, 50434.0, 19104.0,
                    68675.0, 64430.0, 57554.0, 26214.0),
        "valid_from": "2025-01-01", "valid_to": "2025-12-31",
    },
    {
        "id": "child_tax_credit_amounts",
        "title": ("Child Tax Credit / Additional Child Tax "
                  "Credit ($2,200 / $1,700)"),
        "groups": [
            {"tokens": {"ctc"},
             "phrases": ("child tax credit",
                         "additional child tax credit")},
        ],
        "statement": (
            "The federal Child Tax Credit is worth up to "
            "$2,200 per qualifying child (under 17 at "
            "year-end) for tax year 2025. The refundable "
            "Additional Child Tax Credit (ACTC) is up to "
            "$1,700 per qualifying child and requires at "
            "least $2,500 of earned income; the section "
            "24(d) refundable amount stays $1,700 for tax "
            "year 2026. The full credit runs up to AGI of "
            "$200,000 ($400,000 married filing jointly), "
            "with a partial credit above that, and the "
            "Credit for Other Dependents is up to $500 "
            "per dependent. The pre-2025 Child Tax Credit "
            "figure of $2,000 is stale and must not be "
            "given as the current amount."),
        "source": ("IRS Child Tax Credit page (TY2025: "
                   "$2,200 per qualifying child, ACTC up "
                   "to $1,700, $2,500 earned-income floor, "
                   "$200,000 / $400,000 full-credit AGI "
                   "limits, $500 Credit for Other "
                   "Dependents); IRS Revenue Procedure "
                   "2025-32, section .05(2) (TY2026 "
                   "section 24(d) amount $1,700)"),
        "figures": (2200.0, 1700.0, 500.0, 2500.0,
                    200000.0, 400000.0),
        "valid_from": "2025-01-01", "valid_to": None,
        "topic": {"phrases": (("child tax credit", 3.0),
                              ("additional child tax credit",
                               2.5),
                              ("credit for other dependents",
                               2.0),
                              ("qualifying child credit",
                               1.5)),
                  "doc_hint": {"title": "child tax credit",
                               "url": "irs.gov"}},
    },
    {
        "id": "indiana_able_tax_credit",
        "title": ("Indiana income tax credit for ABLE "
                  "contributions (Schedule IN-ABLE)"),
        "groups": [
            {"tokens": {"able"},
             "phrases": ("able account", "able accounts",
                         "investable", "529a", "able 529a")},
            {"tokens": set(),
             "phrases": ("tax credit", "state tax credit",
                         "indiana tax credit",
                         "credit for contributing",
                         "credit for contributions",
                         "tax break for contributing")},
        ],
        "statement": (
            "Indiana's income tax credit for ABLE "
            "contributions is claimed on Schedule "
            "IN-ABLE: 20% of the contributions made "
            "during the tax year to INvestABLE, the "
            "state's ABLE program, up to $1,000 per "
            "return ($500 if married filing "
            "separately), and never more than the "
            "filer's Indiana income tax after other "
            "credits. The credit is nonrefundable, "
            "with no carryover or carryback, and a "
            "later nonqualified withdrawal can "
            "require repaying part of it. (The $500 "
            "cap sometimes quoted is only the "
            "married-filing-separately cap.)"),
        "source": ("Indiana HB 1303 (2022), Legislative "
                   "Services Agency fiscal note LS 6935 "
                   "(20% of contributions; $1,000 cap, "
                   "$500 married filing separately; "
                   "effective January 1, 2023); Indiana "
                   "DOR Schedule IN-ABLE (State Form "
                   "57379)"),
        "figures": (1000.0, 500.0),
        "valid_from": "2023-01-01", "valid_to": None,
    },
]


def _groups_hit(groups, q_low, q_tokens):
    """True when EVERY trigger group hits: a group hits when any of its
    word tokens or substring phrases appears in the question. Shared by
    the figures registry and the v14 mechanism registry so the two can
    never drift apart."""
    for group in groups:
        if q_tokens & group.get("tokens", set()):
            continue
        if any(p in q_low for p in group.get("phrases", ())):
            continue
        return False
    return True


_MONTH_NUM = {name: i + 1 for i, name in enumerate((
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november",
    "december"))}


def _today_iso():
    return datetime.date.today().isoformat()


def _question_date(question):
    """v21 (WS2): the date a question is ABOUT, as an ISO
    string, or None when the question carries no date signal
    (callers then use today). An explicit month + year pins
    the 15th of that month; a bare year pins June 1 of it (so
    a year-only question sees the value in force mid-year);
    "next year" / "last year" resolve against today. A year
    outside 2019-2030 is not a date signal (section numbers
    and statute years are not question dates)."""
    ql = (question or "").lower()
    m = re.search(
        r"\b(january|february|march|april|may|june|july|"
        r"august|september|october|november|december)"
        r"(?:\s+of)?\s+(20\d\d)\b", ql)
    if m and 2019 <= int(m.group(2)) <= 2030:
        return (f"{m.group(2)}-{_MONTH_NUM[m.group(1)]:02d}"
                f"-15")
    today = datetime.date.today()
    if "next year" in ql:
        return f"{today.year + 1}-06-01"
    if "last year" in ql:
        return f"{today.year - 1}-06-01"
    m = re.search(r"\b(20\d\d)\b", ql)
    if m and 2019 <= int(m.group(1)) <= 2030:
        return f"{m.group(1)}-06-01"
    return None


def _date_in_window(d, valid_from, valid_to):
    """ISO string comparison: d inside [valid_from, valid_to],
    either bound None = open."""
    if valid_from and d < valid_from:
        return False
    if valid_to and d > valid_to:
        return False
    return True


def _entry_in_force(entry, on_date):
    """The entry's own window contains on_date."""
    return _date_in_window(on_date, entry.get("valid_from"),
                           entry.get("valid_to"))


def _entry_matches_date(entry, on_date):
    """In force on on_date, OR one of its history windows
    contains on_date (the entry's statement speaks to that
    period explicitly, so it may serve with the historical
    figure admitted by _registry_values)."""
    if on_date is None or _entry_in_force(entry, on_date):
        return True
    return any(
        _date_in_window(on_date, h.get("valid_from"),
                        h.get("valid_to"))
        for h in entry.get("history", ()))


def _registry_matches(question, on_date=None):
    """Registry entries whose trigger groups all hit on the
    question AND that are in force for the date the question
    is about (v21 WS2: explicit question date, else today).
    Entries sharing a "family" are date-exclusive by
    construction; if two somehow match (overlapping windows
    after a future edit), the in-force one wins, then the
    latest valid_from, so a family never serves two values."""
    if on_date is None:
        on_date = _question_date(question) or _today_iso()
    q_low = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z]+", q_low))
    matched = [e for e in VERIFIED_FIGURES
               if _groups_hit(e["groups"], q_low, q_tokens)
               and _entry_matches_date(e, on_date)]
    fams = {}
    for e in matched:
        fam = e.get("family")
        if fam:
            fams.setdefault(fam, []).append(e)
    for fam, es in fams.items():
        if len(es) < 2:
            continue
        es.sort(key=lambda e: (
            _entry_in_force(e, on_date),
            e.get("valid_from") or ""), reverse=True)
        for e in es[1:]:
            matched.remove(e)
    return matched


def _registry_block(entries):
    """The pinned prompt block for matched registry entries."""
    if not entries:
        return ""
    lines = ["VERIFIED FIGURES (checked by the server against the "
             "official sources named; where an excerpt below conflicts "
             "with this block, this block governs):"]
    for e in entries:
        lines.append(f"- {e['title']}: {e['statement']} "
                     f"[Source: {e['source']}]")
        if e.get("successor"):
            lines.append(f"  Note: {e['successor']}")
    return "\n".join(lines)


def _registry_values(entries, on_date=None):
    """Flat list of an entry set's verified dollar figures.
    v21 (WS2): history values whose window contains the
    question's date join the in-force figures (a question
    about 2025 may state the 2025 ABLE cap); a history value
    never joins for a date outside its window."""
    if on_date is None:
        on_date = _today_iso()
    vals = []
    for e in entries:
        vals.extend(e.get("figures", ()))
        for h in e.get("history", ()):
            if _date_in_window(on_date, h.get("valid_from"),
                               h.get("valid_to")):
                vals.append(h["value"])
    return vals


# --- v13: deterministic CSRA ---------------------------------------------
_CSRA_MIN_2026 = 32532.0
_CSRA_MAX_2026 = 162660.0
_ASSETS_RX = re.compile(
    r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:in\s+)?(?:joint\s+|combined\s+|"
    r"countable\s+)?(?:savings|assets|resources|bank accounts?)"
    r"|(?:savings|assets|resources)\s+(?:of|total(?:ing)?|are)\s+"
    r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE)


def compute_csra(question):
    """Compute the CSRA in Python: half the couple's combined countable
    assets, clamped to the 2026 CMS range. The assets figure must be
    anchored to a savings/assets phrase in the question (so a house
    price or an income figure is never halved by mistake). Returns
    {assets, half, csra, clamped} or None when nothing is anchored."""
    for m in _ASSETS_RX.finditer(question or ""):
        raw = m.group(1) or m.group(2)
        try:
            assets = float(raw.replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if assets < 1000:
            continue
        half = assets / 2.0
        csra = min(max(half, _CSRA_MIN_2026), _CSRA_MAX_2026)
        clamped = ("min" if half < _CSRA_MIN_2026 else
                   "max" if half > _CSRA_MAX_2026 else "")
        return {"assets": assets, "half": half, "csra": csra,
                "clamped": clamped}
    return None


def _csra_block(comp):
    """Authoritative prompt text for a server-computed CSRA."""
    txt = (f"SERVER-COMPUTED CSRA: combined countable assets "
           f"{_fmt_money(comp['assets'])}; one half is "
           f"{_fmt_money(comp['half'])}")
    if comp["clamped"] == "min":
        txt += (f", which is below the 2026 minimum, so the community "
                f"spouse resource allowance is "
                f"{_fmt_money(comp['csra'])}")
    elif comp["clamped"] == "max":
        txt += (f", which is above the 2026 maximum, so the community "
                f"spouse resource allowance is "
                f"{_fmt_money(comp['csra'])}")
    else:
        txt += (f", so the community spouse resource allowance is "
                f"{_fmt_money(comp['csra'])}")
    return txt + ". Use this figure; do not recompute it differently."


# --- v14: mechanism registry ----------------------------------------------
# The v13 figures registry grown into mechanisms: a question shape, the
# governing answer mechanism, and the elements a complete answer MUST
# contain, checked in code the same way v13 checks figures. An answer
# that omits the mechanism (or uses a forbidden substitute) fails the
# gate no matter how fluent it is. Detection reuses the registry's
# trigger-group format and _groups_hit, so the layers cannot drift.
# Growth rule (plan section 2): every graded failure and every IDK is
# a candidate entry, written up here with its required elements.
MECHANISMS = [
    {
        "id": "qit_over_cap",
        "title": "Qualified Income Trust (Miller trust)",
        "groups": [
            {"tokens": set(),
             "phrases": ("over the income limit", "over the limit",
                         "above the income limit",
                         "exceeds the income limit",
                         "exceeds the limit", "income cap",
                         "over the cap", "miller trust",
                         "qualified income trust",
                         "over the medicaid income limit",
                         "over the medicaid limit",
                         "above the medicaid income limit",
                         "exceeds the medicaid income limit",
                         "is over the income limit",
                         "income is too high", "too much income",
                         # v22.2: plain asker phrasing (audit
                         # MECHANISMS_TRIGGER_AUDIT.md)
                         "more than medicaid allows",
                         "make too much", "makes too much",
                         "making too much", "earn too much",
                         "earns too much", "too much to qualify",
                         "puts him over", "puts her over",
                         "puts me over", "because of his income",
                         "because of her income",
                         "because of my income")},
            {"tokens": {"medicaid", "waiver"},
             "phrases": ("nursing home", "long-term care",
                         "long term care")},
        ],
        "required": [
            {"label": "names the Qualified Income Trust (Miller trust)",
             "any": ("qualified income trust", "miller trust",
                     "income trust", "qit")},
            {"label": "says the excess income is deposited into the "
                      "trust (monthly)",
             "any": ("deposit", "goes into the trust",
                     "placed in the trust", "paid into the trust",
                     "put into the trust", "into the trust each month",
                     "excess income")},
        ],
        "forbidden": (),
        "prompt": (
            "For income over the Medicaid cap, the governing mechanism "
            "is a Qualified Income Trust (Miller trust): the excess "
            "income is deposited to the trust each month, and that is "
            "what makes the person eligible. Do NOT substitute spousal "
            "impoverishment or a cost-sharing program for the trust; "
            "mention them, if at all, only as separate topics."),
        "sufficiency_terms": ("qualified income trust", "miller trust",
                              "income trust"),
        "retry_query": "qualified income trust medicaid eligibility",
        "missing": ("no excerpt covers a Qualified Income Trust "
                    "(Miller trust), the mechanism that lets someone "
                    "over the Medicaid income cap qualify"),
        # v16 item 4: canonical claim formulations, verified
        # against the corpus sources (the in.gov Miller Trust
        # page: over the Special Income Level means ineligible
        # UNLESS the individual has a valid Miller trust; the
        # Medicaid Estate Recovery page: funds remaining in a
        # QIT at death are recoverable). They pin WHAT is
        # disregarded, under WHAT condition, and what is NOT
        # claimed, so judges stop contradicting the disregard
        # claim in its correct scope and drafts cannot widen it.
        "canonical_claims": (
            "Income paid into a valid Qualified Income Trust "
            "(Miller trust) is not counted toward the Medicaid "
            "income limit for the eligibility determination; "
            "depositing the excess income into the trust each "
            "month is what makes a person over the Special "
            "Income Level eligible.",
            "The trust must be a valid Miller trust under "
            "Indiana's rules (Medicaid Eligibility Policy "
            "Manual section 3010.20.15; the state publishes "
            "the required template and instructions). An "
            "informal or self-declared arrangement is not a "
            "Qualified Income Trust.",
            "The disregard covers only the income deposited "
            "into the trust, for the eligibility determination. "
            "It is not a blanket disregard: at the beneficiary's "
            "death, any money remaining in the trust must be paid "
            "to Indiana, up to the total Medicaid paid on the "
            "beneficiary's behalf. That repayment is a REQUIRED "
            "TERM OF THE TRUST ITSELF (a Miller trust payback), "
            "which is a different mechanism from Medicaid "
            "estate recovery — the state's separate claim "
            "against a deceased person's estate. The trust does "
            "not change how the person's other income or "
            "resources are counted.",
        ),
    },
    {
        "id": "backpay_nine_months",
        "title": "Retroactive benefits: nine-month exclusion",
        "groups": [
            {"tokens": {"backpay"},
             "phrases": ("back pay", "lump sum", "lump-sum",
                         "retroactive benefit", "retroactive ssi",
                         "retroactive social security",
                         "retroactive payment", "past-due",
                         "past due",
                         # v22.2: plain asker phrasing, mirroring
                         # the v22.1 figures-entry widening so the
                         # two layers stay consistent
                         "owed me money",
                         "owed me money from earlier months",
                         "money from earlier months",
                         "social security owed me",
                         "paid it all at once")},
        ],
        "required": [
            {"label": "states the nine-month exclusion for retroactive "
                      "benefits",
             "any": ("nine months", "nine-month", "9 months",
                     "9-month")},
        ],
        "forbidden": ("excluded for six months", "excluded for 6 months",
                      "excluded for six-month"),
        # v15 (item 5): the period gets CODE arithmetic, not just
        # string checks: any six-month phrasing anywhere in a
        # backpay answer fails as a confusable period, and the
        # exclusion end month is recomputed from the receipt
        # date when the question supplies one.
        "period": {"count": 9, "unit": "month",
                   "confusables": ("six months", "6 months",
                                   "six-month", "6-month"),
                   "note": ("The backpay exclusion period is NINE "
                            "months after the month of receipt; "
                            "six months appears only in "
                            "payee/administrative material and is "
                            "never the exclusion.")},
        "prompt": (
            "Retroactive benefits are excluded from resources for "
            "NINE months; the six-month figure in representative-payee "
            "material is not the exclusion and must not be given as "
            "the answer."),
        "sufficiency_terms": ("nine months", "nine-month", "9 months"),
        "retry_query": "retroactive SSI Social Security benefits "
                       "excluded nine months",
        "missing": ("no excerpt states the nine-month exclusion for "
                    "retroactive SSI/Social Security benefits"),
    },
    {
        "id": "snap_lump_sum",
        "title": "SNAP lump sum: not income, resource in the month "
                 "received, Indiana $5,000 limit",
        "groups": [
            {"tokens": {"snap"},
             "phrases": ("food stamps", "food assistance")},
            {"tokens": {"lump", "resource", "resources", "limit"},
             "phrases": ("lump sum", "lump-sum", "back payment",
                         "back-payment", "resource limit",
                         # v22.2: plain asker phrasing
                         "one big check", "big check",
                         "one big payment", "big payment",
                         "single payment", "one-time payment",
                         "one time payment", "money all at once",
                         "paid in one payment", "lump-sum payment",
                         "lump payment")},
        ],
        "required": [
            {"label": "says the lump sum is not counted as SNAP income",
             "any": ("not income", "excluded from income",
                     "does not count as income",
                     "not counted as income", "excluded as income")},
            {"label": "states the Indiana SNAP resource limit of "
                      "$5,000",
             "any": ("$5,000", "5,000")},
        ],
        "forbidden": (),
        "prompt": (
            "A nonrecurring lump sum / back payment is excluded from "
            "SNAP income and counts as a resource only in the month "
            "received; the Indiana SNAP resource limit under BBCE is "
            "$5,000 (the $3,000 federal figure is not the Indiana "
            "limit)."),
        "sufficiency_terms": ("$5,000", "broad-based categorical",
                              "bbce"),
        "retry_query": "Indiana SNAP resource limit broad-based "
                       "categorical eligibility 5000",
        "missing": ("no excerpt states the Indiana SNAP resource "
                    "limit ($5,000 under BBCE)"),
    },
    {
        "id": "csra_half_clamp",
        "title": "CSRA: half of combined countable assets, clamped",
        "groups": [
            {"tokens": set(),
             "phrases": ("community spouse", "csra",
                         "spousal resource",
                         "community spouse resource",
                         # v22.2: plain asker phrasing, mirroring
                         # the v22.1 figures csra widening
                         "my wife", "my husband", "my spouse",
                         "wife can keep", "husband can keep",
                         "spouse can keep", "spouse resource",
                         "spouse resources")},
            {"tokens": {"savings", "assets", "resources", "countable",
                        # v22.2: singular forms (the matcher is
                        # exact-token; v22.1 stemming fix parity)
                        "resource", "asset", "saving"},
             "phrases": ()},
        ],
        "required": [
            {"label": "states the one-half share of combined countable "
                      "assets",
             "any": ("half", "one-half", "one half", "50%")},
            {"label": "the server-computed CSRA figure",
             "computed": "csra"},
        ],
        "forbidden": (),
        "prompt": (
            "The community spouse resource allowance is one half of "
            "the couple's combined countable assets at the snapshot, "
            "clamped to [$32,532, $162,660]; use the server-computed "
            "figure when one is provided."),
        "sufficiency_terms": ("community spouse resource", "csra",
                              "spousal impoverishment"),
        "retry_query": "community spouse resource allowance half "
                       "combined countable assets",
        "missing": ("no excerpt covers the community spouse resource "
                    "allowance (CSRA)"),
    },
    {
        "id": "difficulty_of_care_exclusion",
        "title": "Difficulty-of-care exclusion (Notice 2014-7)",
        "groups": [
            {"tokens": {"caregiver", "caregiving"},
             "phrases": ("caregiver pay", "paid caregiver",
                         "difficulty of care",
                         # v22.2: plain asker phrasing, mirroring
                         # the v22.1 figures-entry widening
                         "gets paid to take care",
                         "paid to take care",
                         "take care of me at home",
                         "care for me at home", "paid to care for",
                         "wife gets paid", "husband gets paid",
                         "spouse gets paid")},
            {"tokens": {"hip", "magi"},
             "phrases": ("healthy indiana", "household income")},
        ],
        "required": [
            {"label": "names the difficulty-of-care exclusion "
                      "(IRS Notice 2014-7)",
             "any": ("difficulty of care", "notice 2014-7", "2014-7")},
            {"label": "concludes the caregiver pay is excluded / does "
                      "not count as household income",
             "any": ("excluded", "does not count", "not count",
                     "excludable", "not income", "not counted")},
        ],
        "forbidden": (),
        "prompt": (
            "Caregiver pay under a Medicaid waiver to a caregiver who "
            "lives with the care recipient is a difficulty-of-care "
            "payment excluded from income under IRS Notice 2014-7; "
            "for HIP/MAGI it does not count as household income."),
        "sufficiency_terms": ("difficulty of care", "2014-7",
                              "notice 2014"),
        "retry_query": "difficulty of care payments excluded income "
                       "notice 2014-7 medicaid waiver caregiver",
        "missing": ("no excerpt covers the difficulty-of-care "
                    "exclusion (IRS Notice 2014-7) for caregiver pay"),
    },
    # --- v27 (Change 4, REGISTRY_PREP Class R): the entries below
    # were verified against the source texts named in each entry
    # (v27/sources/ + KEY_VERIFICATION.md + the DDARS manual text)
    # before they were written; no Class U item is included.
    {
        "id": "nemt_booking",
        "title": "NEMT rides: Verida broker, book 3 calendar days ahead",
        "groups": [
            {"tokens": {"nemt", "verida", "southeastrans"},
             "phrases": ("non-emergency transportation",
                         "nonemergency transportation",
                         "medical transportation", "ride to my doctor",
                         "rides to my appointments",
                         "get to my doctor appointments",
                         "get to the doctor", "transportation to my appointments",
                         "help getting to my appointments",
                         "book a ride", "schedule a ride")},
            {"tokens": {"ride", "rides", "transportation", "transport",
                        "trip", "trips", "book", "booking", "schedule"},
             "phrases": ("doctor appointments", "medical appointments",
                         "to my appointments", "a ride")},
        ],
        "required": [
            {"label": "names Verida as the fee-for-service NEMT broker",
             "any": ("verida",)},
            {"label": "states booking at least 3 calendar days ahead",
             "any": ("3 calendar days", "three calendar days",
                     "3 days in advance", "three days in advance",
                     "at least 3 days", "at least three days")},
            {"label": "gives the reservation line 1-855-325-7586",
             "any": ("1-855-325-7586", "855-325-7586")},
        ],
        "forbidden": ("2 business days", "two business days"),
        "prompt": (
            "Non-emergency medical transportation (NEMT) is an "
            "Indiana Medicaid benefit. Fee-for-service / Traditional "
            "Medicaid rides are brokered by Verida, Inc. (the same "
            "company formerly named Southeastrans, renamed per IHCP "
            "banner BR202252): book at least 3 CALENDAR days ahead "
            "(up to 30 days ahead) on the reservation line "
            "1-855-325-7586; urgent trips are available 24/7. The "
            "older '2 business days' figure is superseded and must "
            "not be given. Managed-care members book through their "
            "health plan instead. The FFS benefit starts at 20 "
            "one-way trips per 12-month period, with more available "
            "when the doctor requests them and they are approved."),
        "sufficiency_terms": ("verida", "non-emergency transportation",
                              "nemt"),
        "retry_query": "Verida non-emergency transportation Indiana "
                       "Medicaid book ride 3 calendar days",
        "missing": ("no excerpt covers Verida NEMT booking for "
                    "Indiana Medicaid (3 calendar days ahead, "
                    "1-855-325-7586)"),
    },
    {
        "id": "aba_epsdt",
        "title": "ABA therapy: an EPSDT / state-plan benefit, not a waiver service",
        "groups": [
            {"tokens": {"aba"},
             "phrases": ("applied behavior analysis",
                         "aba therapy", "autism therapy")},
            {"tokens": {"cover", "covers", "covered", "coverage",
                        "qualify", "eligible", "autism", "child",
                        "daughter", "son"},
             "phrases": ("does medicaid cover", "is aba covered",
                         "for my daughter", "for my son",
                         "for my child", "with autism")},
        ],
        "required": [
            {"label": "frames ABA as an EPSDT / state-plan benefit, "
                      "not a waiver service",
             "any": ("epsdt", "early and periodic screening",
                     "state plan", "state-plan")},
            {"label": "states the under-21 limit (21+ ended Oct 1, 2026)",
             "any": ("under 21", "under the age of 21",
                     "younger than 21", "age 21 and older",
                     "21 and older", "20 and younger",
                     "through age 20")},
        ],
        "forbidden": ("waiver service under the family supports",
                      "waiver service under the cih"),
        "prompt": (
            "Applied behavior analysis (ABA) therapy for autism is "
            "covered by Indiana Medicaid as a STATE-PLAN / EPSDT "
            "benefit, NOT as a waiver service (it is on no FS/CIH "
            "waiver service list; the DDARS manual routes under-21s "
            "to ABA under EPSDT). EPSDT-only from April 1, 2026 "
            "(IHCP BT202627): members under 21 only, and ABA for "
            "members 21 and older ended October 1, 2026. Prior "
            "authorization is required (BT201606, criteria at "
            "405 IAC 5-3). Comprehensive ABA has a 4,000-hour "
            "lifetime allocation based on medical necessity, then "
            "up to 15 hours per week of targeted ABA."),
        "sufficiency_terms": ("epsdt", "applied behavior analysis",
                              "aba"),
        "retry_query": "ABA therapy EPSDT Indiana Medicaid under 21 "
                       "prior authorization 4000 hours",
        "missing": ("no excerpt covers ABA therapy as an EPSDT / "
                    "state-plan benefit for members under 21"),
        "canonical_claims": (
            "ABA therapy is not an FS or CIH waiver service; it is "
            "an Indiana Medicaid state-plan benefit delivered "
            "through EPSDT for members under 21.",
            "Comprehensive ABA carries a 4,000-hour lifetime "
            "allocation based on medical necessity; after that "
            "allocation, targeted ABA is available at up to 15 "
            "hours per week.",
        ),
    },
    {
        "id": "parental_income_disregard_waiver",
        "title": "Parental income disregarded for a child in an HCBS waiver category",
        "groups": [
            {"tokens": {"parental", "parents"},
             "phrases": ("my income", "parents' income",
                         "parent's income", "income count against",
                         "count against my child",
                         "count against my son",
                         "count against my daughter")},
            {"tokens": {"waiver"},
             "phrases": ("family supports", "fs waiver", "cih",
                         "community integration and habilitation",
                         "hcbs waiver", "medicaid waiver",
                         "waiver category")},
            {"tokens": {"child", "minor"},
             "phrases": ("my child", "my son", "my daughter",
                         "under 18", "child under 18")},
        ],
        "required": [
            {"label": "states parental income is disregarded for a "
                      "child under 18 in a waiver category",
             "any": ("disregarded", "not factored", "not counted",
                     "does not count", "is not counted")},
            {"label": "states the child is budgeted on the child's "
                      "own income against 300% of the SSI maximum",
             "any": ("300%", "300 percent", "three hundred percent")},
        ],
        "forbidden": (),
        "prompt": (
            "For a child under 18 in an HCBS waiver category (FS, "
            "CIH, H&W, TBI), parental income is DISREGARDED: the "
            "child is budgeted on the child's own income against "
            "300% of the SSI maximum. Do NOT apply SSI parental "
            "deeming or ABD parental deeming to a waiver-category "
            "application."),
        "sufficiency_terms": ("parental income", "disregarded",
                              "300%"),
        "retry_query": "parental income disregarded child under 18 "
                       "HCBS waiver category 300% SSI",
        "missing": ("no excerpt states the parental income "
                    "disregard for a child in an HCBS waiver "
                    "category"),
    },
    {
        "id": "parental_deeming_abd_child",
        "title": "Parental deeming for an ABD-category child living at home (IHCPPM 3455.05.20)",
        "groups": [
            {"tokens": {"parental", "parents", "deeming", "deemed"},
             "phrases": ("my income", "parents' income",
                         "parent's income", "count against my child",
                         "count against my son",
                         "count against my daughter",
                         "deemed to my child", "deeming")},
            {"tokens": {"abd"},
             "phrases": ("aged blind disabled", "aged, blind",
                         "ma a", "ma b", "ma d", "abd category",
                         "abd categories", "non-magi",
                         "disability category")},
            {"tokens": {"child", "minor"},
             "phrases": ("my child", "my son", "my daughter",
                         "living at home", "lives at home",
                         "living with", "at home")},
        ],
        "required": [
            {"label": "states parental income is deemed to an "
                      "ABD-category child living with a parent",
             "any": ("deemed", "deeming")},
            {"label": "states the living-with-a-parent / under-18 "
                      "conditions (or a stated exception)",
             "any": ("living with", "lives with", "at home",
                     "under age 18", "under 18", "waiver",
                     "institutionalized", "student")},
        ],
        "forbidden": (),
        "prompt": (
            "For ABD-category Medicaid (MA A, MA B, MA D), IHCPPM "
            "section 3455.05.20 applies parental deeming: income "
            "is deemed from a non-recipient biological or adoptive "
            "parent when the child is under 18, lives with the "
            "parent(s), and is NOT receiving HCBS waiver services. "
            "No deeming applies when the child is institutionalized "
            "(from the month after admission), and a student "
            "between 18 and 21 is not deemed a parent's income. "
            "The section's allocations (stepparent, siblings) come "
            "off the parent's income before the excess over the "
            "Chapter 3000 standard is deemed to the child. (This "
            "entry carries no general-disregard figure: the "
            "fetched section text names it inconsistently, so no "
            "figure is served until that is resolved.)"),
        "sufficiency_terms": ("3455.05.20", "parental deemed income",
                              "parental deeming"),
        "retry_query": "IHCPPM 3455.05.20 parental deemed income "
                       "ABD child living with parent",
        "missing": ("no excerpt covers IHCPPM 3455.05.20 parental "
                    "deeming for an ABD-category child at home"),
    },
    {
        "id": "magi_childrens_categories_parent_income",
        "title": "MAGI children's categories (Hoosier Healthwise / CHIP): parents' income counts as household MAGI",
        "groups": [
            {"tokens": {"parental", "parents", "magi"},
             "phrases": ("my income", "parents' income",
                         "parent's income", "household income",
                         "count against my child",
                         "count against my son",
                         "count against my daughter")},
            {"tokens": {"chip", "magi"},
             "phrases": ("hoosier healthwise", "children's health "
                         "insurance", "childrens health insurance",
                         "ma 4", "ma 8", "masi")},
            {"tokens": {"child", "minor"},
             "phrases": ("my child", "my son", "my daughter",
                         "children's category", "childrens category")},
        ],
        "required": [
            {"label": "states the parents' income counts as "
                      "household MAGI for these categories",
             "any": ("household magi", "magi", "household income")},
            {"label": "names a MAGI children's category or program "
                      "(Hoosier Healthwise / CHIP / MA 4 / MA 8 / MASI)",
             "any": ("hoosier healthwise", "chip", "ma 4", "ma 8",
                     "masi")},
        ],
        "forbidden": (),
        "prompt": (
            "For the MAGI children's categories (Hoosier "
            "Healthwise / CHIP: Indiana categories MA 4, MA 8 and "
            "MASI), eligibility is based on total HOUSEHOLD MAGI "
            "under 42 CFR 435.603, so the parents' income in the "
            "home counts as household income. This is not SSI "
            "deeming and not the waiver-category disregard: the "
            "mechanism is household MAGI composition."),
        "sufficiency_terms": ("magi", "hoosier healthwise", "435.603"),
        "retry_query": "Hoosier Healthwise CHIP household MAGI "
                       "parents income 42 CFR 435.603 children",
        "missing": ("no excerpt covers household MAGI counting "
                    "parents' income for Hoosier Healthwise / CHIP "
                    "children's categories"),
    },
    {
        "id": "ssi_parental_deeming",
        "title": "SSI parental deeming: a minor living at home; stops at 18",
        "groups": [
            {"tokens": {"ssi", "deeming", "deemed"},
             "phrases": ("supplemental security income", "deeming",
                         "deemed income", "deemed resources")},
            {"tokens": {"parental", "parents", "parent"},
             "phrases": ("my income", "parents' income",
                         "parent's income", "parental income",
                         "parental resources")},
            {"tokens": {"child", "minor"},
             "phrases": ("my child", "my son", "my daughter",
                         "living at home", "lives at home",
                         "minor child")},
        ],
        "required": [
            {"label": "states SSI deems parental income (and "
                      "resources) to a minor living at home",
             "any": ("deemed", "deeming", "deems")},
            {"label": "states deeming stops at 18",
             "any": ("stops at 18", "ends at 18", "through the "
                     "month the child attains 18", "attains 18",
                     "turns 18", "at age 18", "until 18")},
        ],
        "forbidden": (),
        "prompt": (
            "For SSI, parental income and resources are deemed to "
            "a child under 18 who lives in the same household as "
            "the parent(s) (or a parent's spouse): the child is "
            "presumed to share in the parents' income. Deeming "
            "continues through the month the child attains 18 and "
            "then stops. Parental resources above the parent's own "
            "resource limit (individual if one parent, couple if "
            "two) are deemed whether or not available to the child."),
        "sufficiency_terms": ("deeming", "parent-to-child", "si 013"),
        "retry_query": "SSI parental deeming child under 18 living "
                       "at home stops at 18 POMS SI 013",
        "missing": ("no excerpt covers SSI parental deeming to a "
                    "minor living at home (stops at 18)"),
    },
    {
        "id": "supported_decision_making",
        "title": "Supported decision-making agreements (IC 29-3-14)",
        "groups": [
            {"tokens": {"sdm"},
             "phrases": ("supported decision-making",
                         "supported decision making",
                         "supported decision making agreement",
                         "decision-making agreement",
                         "guardianship alternative",
                         "alternatives to guardianship",
                         "less restrictive alternative",
                         "instead of guardianship",
                         "avoid guardianship")},
            {"tokens": {"adult", "guardianship", "guardian"},
             "phrases": ("my adult child", "adult child",
                         "make decisions", "make her own decisions",
                         "make his own decisions",
                         "help making decisions",
                         "help with decisions")},
        ],
        "required": [
            {"label": "names supported decision-making (agreements) "
                      "under IC 29-3-14",
             "any": ("supported decision", "ic 29-3-14", "29-3-14")},
            {"label": "states the adult keeps decision-making "
                      "authority (an agreement is not evidence of "
                      "incapacity)",
             "any": ("keeps decision", "retains decision",
                     "decision-making authority",
                     "decision making authority",
                     "not evidence of incapacity",
                     "not be considered evidence of incapacity",
                     "adult's own decision", "adult keeps")},
        ],
        "forbidden": (),
        "prompt": (
            "Indiana has supported decision-making agreements "
            "under IC 29-3-14 (added by SEA 380 / P.L.68-2019, "
            "effective July 1, 2019): an adult voluntarily names "
            "one or more supporters who help the adult make, "
            "communicate and effectuate life decisions, and the "
            "ADULT keeps decision-making authority. A court may "
            "not consider an adult's execution of an SDM agreement "
            "as evidence of incapacity, and an agreement does not "
            "stop the adult acting independently. An agreement may "
            "not supplant a guardian's authority unless the "
            "guardian consents in writing. SDM is a less "
            "restrictive alternative to guardianship, not a "
            "replacement ordered by a court."),
        "sufficiency_terms": ("supported decision", "29-3-14",
                              "sdm agreement"),
        "retry_query": "Indiana supported decision making agreement "
                       "IC 29-3-14 adult keeps decision authority",
        "missing": ("no excerpt covers Indiana supported "
                    "decision-making agreements (IC 29-3-14)"),
    },
]



def _mechanism_matches(question, pool=None):
    """Mechanism entries whose trigger groups all hit on the question
    (or on any text, e.g. one part of a bundled question)."""
    q_low = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z]+", q_low))
    return [m for m in (pool or MECHANISMS)
            if _groups_hit(m["groups"], q_low, q_tokens)]


def _mechanism_block(mechanisms):
    """Prompt block naming the required mechanisms for this ask."""
    if not mechanisms:
        return ""
    lines = ["REQUIRED MECHANISMS (checked by the server; an answer "
             "that omits the governing mechanism, or substitutes a "
             "different program or rule for it, fails verification):"]
    for m in mechanisms:
        lines.append(f"- {m['title']}: {m['prompt']}")
        # v16 item 4: canonical claim formulations ride in the
        # same block (drafters state the mechanism in these
        # terms; judges enforce them: a matching claim is
        # supported when the excerpts support the mechanism,
        # and a claim that widens a canonical form, by dropping
        # a condition or extending the disregard beyond it,
        # is contradicted).
        for cc in m.get("canonical_claims") or ():
            lines.append(f"  Canonical: {cc}")
    return "\n".join(lines)


def _mech_required_failures(answer, mechanisms, csra_comp=None):
    """Code check: every required element of every matched mechanism
    must be present in the answer, and no forbidden substitute may
    appear. Returns a list of {id, label} failures."""
    a_low = (answer or "").lower()
    failures = []
    for m in mechanisms or ():
        for el in m["required"]:
            if el.get("computed") == "csra":
                # Required only when the question supplied an amount
                # and the server computed a figure; a rule-only CSRA
                # question has no computed figure to state.
                if not csra_comp:
                    continue
                ok = (_fmt_money(csra_comp["csra"]) in (answer or "")
                      or f"{csra_comp['csra']:,.0f}" in (answer or "")
                      or f"{csra_comp['csra']:.0f}" in (answer or ""))
                if not ok:
                    failures.append({"id": m["id"], "label": el["label"]})
                continue
            if not any(s in a_low for s in el["any"]):
                failures.append({"id": m["id"], "label": el["label"]})
        for bad in m.get("forbidden", ()):
            if bad in a_low:
                failures.append(
                    {"id": m["id"],
                     "label": f"uses the forbidden substitute '{bad}'"})
    return failures


def _hit_text(h):
    return ((h.get("title") or "") + " " + (h.get("excerpt") or "")).lower()


def _sufficiency_missing(mechanisms, hits):
    """Mechanisms whose governing material is absent from the excerpt
    set: none of the mechanism's sufficiency terms appears in any
    hit's title or excerpt."""
    missing = []
    for m in mechanisms or ():
        if not any(any(t in _hit_text(h) for t in m["sufficiency_terms"])
                   for h in hits or ()):
            missing.append(m)
    return missing


def _sufficiency_retry(corp, hits, missing):
    """Gate retry (plan section 1): direct keyword searches for the
    missing mechanisms' governing material; chunks that carry a
    sufficiency term are merged ahead of the current set."""
    extra, seen = [], {(h["url"], h["excerpt"][:60]) for h in hits}
    for mech in missing:
        try:
            found = search(mech["retry_query"], corpus=corp, n=6)
        except Exception:
            found = []
        for h in found:
            key = (h["url"], h["excerpt"][:60])
            if key in seen:
                continue
            if any(t in _hit_text(h) for t in mech["sufficiency_terms"]):
                seen.add(key)
                extra.append(h)
    return extra + list(hits)


# --- v13: decomposition + difficulty routing ------------------------------

def decompose_question(question):
    """The bundled sub-questions inside a question (the v11 rule-based
    splitter); [] when the question is not bundled."""
    return _split_subquestions(question or "")


def is_hard_question(question):
    """Rule-based difficulty classifier (v13): the topics whose figures
    are contested between competing sources, plus bundled questions.
    Returns a short reason string, or '' when the question is routine."""
    q_low = (question or "").lower()
    toks = set(re.findall(r"[a-z]+", q_low))
    if toks & {"backpay"} or any(p in q_low for p in
                                 ("back pay", "lump sum", "lump-sum",
                                  "past-due", "past due")):
        return "backpay/lump-sum figures"
    if "community spouse" in q_low or "csra" in q_low \
            or "spousal" in q_low:
        return "spousal/CSRA figures"
    if ("snap" in toks or "food stamps" in q_low) and \
            toks & {"resource", "resources", "asset", "assets", "limit",
                    "lump"}:
        return "SNAP limit figures"
    # v14: over-cap income questions route like the other contested
    # topics (manual test #1: the Miller trust question was answered
    # unrouted on the 9B and missed the governing mechanism entirely).
    if any(p in q_low for p in ("over the income limit", "over the limit",
                                "over the medicaid income limit",
                                "over the medicaid limit",
                                "is over the income limit",
                                "income cap", "over the cap",
                                "miller trust", "qualified income trust")) \
            and (toks & {"medicaid", "waiver"} or "nursing home" in q_low
                 or "long-term care" in q_low or "long term care" in q_low):
        return "income-cap figures"
    if decompose_question(question):
        return "bundled multi-part question"
    return ""


def ollama_unload(model):
    """Ask Ollama to unload a resident model (keep_alive 0) so a routed
    larger model does not fight it for the 8GB VRAM (the v12 bake-off
    500s). Best effort: returns False on any failure, and generation
    still works, just under memory pressure."""
    try:
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            OLLAMA + "/api/generate", data=payload,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception:
        return False


def _resident_models():
    """Names of the models Ollama currently holds in memory (/api/ps).
    Empty list when Ollama cannot be reached."""
    try:
        req = urllib.request.Request(OLLAMA + "/api/ps")
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return [m.get("name") or m.get("model") or ""
                for m in data.get("models", [])]
    except Exception:
        return []


def _unload_others(keep):
    """v14.1: unload every resident CHAT model except `keep`, so a
    local 14B judge or redraft call gets the 8GB VRAM to itself
    (v14 unloaded only the 9B, and only on the local-draft path).
    The embedding model stays resident: retrieval needs it and it
    is small. Best effort throughout, exactly like ollama_unload."""
    for name in _resident_models():
        if name and name != keep and "embed" not in name.lower():
            ollama_unload(name)


# v15.1: _warm_local_judge (v14.1) is REMOVED. It speculatively
# loaded the local 14B with a 30-minute keep-alive whenever a
# fallback judge seat MIGHT be needed, which put the 14B in VRAM
# on remote-path asks. Local judge calls load the model cold,
# on demand — in v23.1 only for an explicit local ask — and
# every local judge call carries keep_alive 0 (see
# _ollama_chat_raw), so the model unloads again immediately
# after the call.


def _gemini_normalize(model):
    """Map a requested model string ('gemini-3.8-flash (API)', 'gemini',
    ...) to a chain entry; unknown or generic requests start at the head."""
    m = (model or "").lower()
    for entry in GEMINI_CHAIN:
        if entry in m:
            return entry
    return GEMINI_CHAIN[0]


def _gemini_call(api_model, payload, deadline=None):
    """One model, bounded polite attempts (delays 0/3/8s, 45s timeout;
    v12 speed pass). Returns the
    answer text. Raises _GeminiQuota on daily-quota exhaustion and
    RuntimeError when this model is unusable right now. v15 (S3):
    the transport is the keep-alive pool; spacing, retries, and
    error semantics are unchanged. v23.1 (WS-A): a deadline clamps
    each attempt's timeout to the ask budget remaining and ends
    the retry loop at the budget edge instead of sleeping past
    it."""
    host = "generativelanguage.googleapis.com"
    path = (f"/v1beta/models/{api_model}:generateContent"
            f"?key={GEMINI_API_KEY}")
    last_err = None
    for delay in (0, 3, 8):
        if deadline is not None:
            _rem = deadline - time.monotonic()
            if _rem <= 0:
                raise RuntimeError(
                    f"{api_model} judge leg budget spent")
            if delay and _rem <= delay + 2:
                raise RuntimeError(
                    f"{api_model} judge leg budget spent "
                    f"(no retry inside {_rem:.1f}s left)")
        if delay:
            time.sleep(delay)
        _gemini_space()
        _timeout = 45
        if deadline is not None:
            _timeout = max(1.0, min(
                45.0, deadline - time.monotonic()))
        try:
            status, raw = _https_post(
                host, path, payload,
                {"Content-Type": "application/json"},
                timeout=_timeout)
        except Exception as e:  # network blips: also retryable
            last_err = e
            continue
        if status != 200:
            body = raw.decode("utf-8", "replace")
            if status == 429 and ("RESOURCE_EXHAUSTED" in body
                                  or "quota" in body.lower()):
                raise _GeminiQuota(f"{api_model} daily quota exhausted")
            if status == 404:
                raise RuntimeError(f"{api_model} not available (HTTP 404)")
            if status in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"{api_model} HTTP {status}")
                continue
            raise RuntimeError(f"{api_model} HTTP {status}: {body[:200]}")
        data = json.loads(raw)
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            raise RuntimeError(
                f"Gemini API unexpected response: {str(data)[:200]}")
        if not content:
            raise RuntimeError("Gemini returned an empty response")
        return content
    raise RuntimeError(f"{api_model} unavailable after retries: {last_err}")


def _numbered_context(hits):
    """v15 (item 3, citations at birth): the drafter-facing excerpt
    context, each excerpt numbered in the SAME order the judges use,
    so a drafter can cite [n] and the server can map the citation
    back to the excerpt deterministically."""
    return "\n\n".join(f"Excerpt {i + 1}:\n{_fmt_hit(h)}"
                       for i, h in enumerate(hits or ()))


def gemini_ask(model, question, hits, instructions="", deadline=None):
    """Ask the Gemini API, best model first, downgrading down GEMINI_CHAIN
    only when a model is unavailable. Returns (answer, prompt, served_model,
    downgraded). Raises if every Gemini model fails; the caller then falls
    back to the local model. v23.2 (BUG-11 F1): `deadline` (the ask's one
    deadline) clamps every attempt via _gemini_call; through v23.1 this
    drafting path called _gemini_call with NO deadline at all."""
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key not configured (gemini_key.txt missing)")
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    payload = json.dumps({
        "contents": [
            {"parts": [{"text": sys_prompt + "\n\n" + user_msg}]}
        ],
        "generationConfig": {"temperature": 0.3},
    }).encode()
    start = _gemini_normalize(model)
    chain = GEMINI_CHAIN[GEMINI_CHAIN.index(start):]
    errors = []
    for api_model in chain:
        try:
            content = _gemini_call(api_model, payload,
                                   deadline=deadline)
            return (content, sys_prompt + "\n\n" + user_msg,
                    api_model, api_model != start)
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("All Gemini models unavailable: " + "; ".join(errors))


def ollama_ask(model, question, hits, instructions="", timeout=300):
    # v23.2 (BUG-11 F1): `timeout` is the caller's deadline-
    # clamped bound. Through v23.1 the socket timeout was a
    # hardcoded 300s with no deadline parameter at all, so a
    # local fallback draft starting near the budget edge
    # could run a full 300s past it (the pest-control ask's
    # local fallback leg).
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    full_prompt = sys_prompt + "\n\n" + user_msg
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "think": False,
        # v15.1: unload immediately after the call. A local
        # generation is a genuine fallback or an explicit pick;
        # it never leaves the model resident behind it.
        "keep_alive": 0,
        # 8-10 excerpt prompts run ~4.5k tokens; Ollama's default context
        # (~4k) silently squeezes generation and answers stop mid-sentence
        # (seen on the discovery survey, 2026-10-12). Give real headroom.
        "options": {"num_ctx": 8192, "num_predict": 1792,
                    "temperature": 0.3},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(
        req, timeout=max(1.0, float(timeout))).read())
    msg = data.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        # thinking models sometimes put the answer in the thinking field
        content = (msg.get("thinking") or "").strip()
    if not content:
        raise RuntimeError("model returned an empty response")
    return content, full_prompt


# --- v14: gap log -----------------------------------------------------------
# Every IDK, abstained part, and failed gate appends one JSONL record:
# {ts, question, badge, gate, missing, part}. The file lives next to the
# corpus (override with FSSA_GAP_LOG) and is the input to the monthly
# rescrape watchlist and to mechanism-registry growth (plan section 7).
GAP_LOG_PATH = os.environ.get("FSSA_GAP_LOG", "gap_log.jsonl")
_GAP_LOCK = threading.Lock()


def _gap_class_guess(gate, missing, question):
    """v21 (WS3a): the growth loop's first-pass classification
    guess, stamped on every gap row so the staging runner can
    route without re-deriving it. Deliberately coarse, with an
    honest vocabulary: a question about a FUTURE year is
    "newer_edition" (the governing material is a newer edition
    or not yet published); a gate that means "the corpus did
    not hold it" (sufficiency, branch-out) is
    "missing_document"; everything else is "unknown" — the
    runner refines "unknown" rows with its corpus-coverage
    check (the retrieval-miss class lives there, because only
    the check can prove the document was present all along)."""
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b",
                                        (question or "").lower())]
    if years and max(years) > datetime.date.today().year:
        return "newer_edition"
    if gate in ("branch_out", "sufficiency"):
        return "missing_document"
    return "unknown"


def log_gap(question, badge, gate, missing, part=None):
    # v21 (WS3a): every row also carries the ask id (the join
    # key back to ask_records.jsonl) and the classification
    # guess, so a gap row IS a complete growth-loop queue
    # candidate on its own.
    try:
        _ask_id = _ask_ctx().ask_id
    except Exception:
        _ask_id = None
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "question": (question or "")[:300], "badge": badge,
           "gate": gate, "missing": list(missing or ())[:8],
           "part": part, "ask_id": _ask_id,
           "class_guess": _gap_class_guess(gate, missing,
                                           question)}
    try:
        with _GAP_LOCK:
            with open(GAP_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break an ask
    return rec


def _read_gaps(limit=200):
    try:
        with open(GAP_LOG_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- provider transports ---------------------------------------------------
# v23.1: the old third-judge / NIM-layer prose that stood here
# described machinery deleted across v20-v23.1 (the NIM judge
# seats, the panel, the relief bench). What remains in this
# section is the shared OpenAI-compatible transport (_nim_text)
# and the per-provider key loading every hosted caller uses.
_NIM_KEYS = {}


def _nim_key_for(key_env, key_file):
    """A hosted provider's API key, loaded once per key file: its
    env var first, else its key file in the server dir (the
    Gemini-key pattern). None when absent; the seat stays dormant."""
    cache_key = key_file
    if cache_key not in _NIM_KEYS:
        val = os.environ.get(key_env) or None
        if not val:
            try:
                with open(key_file, encoding="utf-8") as f:
                    val = f.read().strip() or None
            except FileNotFoundError:
                pass
        _NIM_KEYS[cache_key] = val
    return _NIM_KEYS[cache_key]


# --- v16: measured seats (item 1) + branch-out (item 7) --------------
# v23 (WS2/WS3.1): the seat machinery now serves ONE job, the
# judge seat-2 verdict seat (see SEATS below). A SEAT is an
# ordered candidate list of (model, key slot), a latency
# budget, and a failover order. Runtime seat health benches a
# candidate on repeated timeouts or a rolling median over
# budget; the job fails over to its next candidate. Seats are
# never re-shopped per ask.
SEAT_SLOTS = {
    # v23 (WS2): only the three pseudo-slots remain. Slot 0 is
    # the OpenRouter pseudo-slot: candidates pinned to it are
    # served by _or_text (usage reporting, deny routing, cost
    # ledgering), and _slot_has_key gates the slot on
    # _or_available(), so a missing key or a tripped daily cap
    # benches every OpenRouter candidate at once. Slot 4 is
    # xAI (an ordinary paid provider), slot 5 is Groq (free
    # tier, daily-guarded). The NIM key slots (1-3) and the
    # rationed Flash rung (slot 6) are DELETED: no NIM model
    # and no Flash rung sits on any timed path in v23.
    0: ("OPENROUTER_API_KEY", "openrouter_key.txt"),
    4: ("XAI_API_KEY", "xai_key.txt"),
    5: ("GROQ_API_KEY", "groq_key.txt"),
}
# --- v20: the xAI + Groq provider block ------------------------------
# Seat chains are config-driven constants (here and in SEATS
# below), so any seat can be reverted by editing one list.
# the operator's free-first directive (2026-10-14): free + fast
# providers are the primaries; ALL prepaid providers sit
# behind them as fallovers. The one documented exception is
# judge seat B (see SEATS).
#
# xAI: an ORDINARY PAID provider. The data-sharing program
# that once granted $150/mo in free credits was SUNSET at
# the end of May 2025 (official xAI email to participants;
# confirmed 2026-10-14) and the reported $25 signup promo
# never materialized, so NO grant exists: by default xAI
# spend reserves and settles through the OpenRouter
# reservation gate and counts against the daily ledger
# ($10.00/day in v21) and the per-ask ceiling ($1.00 in v21)
# exactly like OpenRouter
# spend. XAI_FREE_CREDITS stays in the code only so a future
# grant program could flip xAI accounting to the
# visibility-only credit ledger (xai_spend.json,
# xai_credit_today in /health) without code surgery; its
# default False is a permanent fact, not a pending toggle.
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_KEY_FILE = "xai_key.txt"
XAI_KEY_ENV = "XAI_API_KEY"
XAI_FREE_CREDITS = False
# xAI model ids verified against the LIVE api.x.ai catalog
# on 2026-10-14 (connector pull): grok-4.7 is the flagship;
# the fast tier is grok-4.20-0309-non-reasoning (there is
# NO grok-4.1-fast on the direct API).
XAI_HARD_MODEL = "grok-4.7"
XAI_FAST_MODEL = "grok-4.20-0309-non-reasoning"
XAI_DRAFTER_TIMEOUT = 120  # hosted flagship; the OR drafter bound
# USD per 1M tokens (input, output), list prices.
_XAI_PRICES = {XAI_HARD_MODEL: (2.00, 6.00)}
_XAI_PRICE_DEFAULT = (2.00, 6.00)
#
# Groq: the free tier is the drafting BACKUP rail in v23.1
# (GPT-OSS-120B for hard questions, GPT-OSS-20B for simple
# drafting + utilities). Model ids verified TWICE on
# 2026-10-14: against Groq's official models page
# (https://console.groq.com/docs/models — the two GPT-OSS
# models are PRODUCTION) and against the LIVE account
# catalog pulled with the operator's own key (exactly 11
# models; the chat ids below match exactly; there is NO
# Llama chat model on his catalog, only llama-prompt-guard
# classifiers). The v20-v23 Groq Qwen judge seat is DELETED
# in v23.1 with the rest of the panel: one Groq account's
# 8,000 tokens/min organization limit backing both drafting
# and judging was BUG-4's root cause.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_KEY_FILE = "groq_key.txt"
GROQ_KEY_ENV = "GROQ_API_KEY"
GROQ_HARD_MODEL = "openai/gpt-oss-120b"
GROQ_SIMPLE_MODEL = "openai/gpt-oss-20b"
GROQ_UTILITY_MODEL = "openai/gpt-oss-20b"
GROQ_DRAFTER_TIMEOUT = 60  # Groq's whole pitch is speed
# USD per 1M tokens (input, output), list prices from the
# same official models page. Free-tier calls are recorded
# at these prices for visibility (provider_tier=free) and
# never touch the real-money ledger.
_GROQ_PRICES = {GROQ_HARD_MODEL: (0.15, 0.60),
                GROQ_SIMPLE_MODEL: (0.075, 0.30)}
_GROQ_PRICE_DEFAULT = (0.15, 0.60)
# The Groq daily guard (spec item 4): a persistent per-day
# request counter on the Indiana ledger day (the spend
# ledger's convention). At GROQ_DAILY_SOFT_CAP the Groq
# entries shed (chains skip them and the fallover proceeds)
# until the next ledger day; sheds are logged in the ask
# record. The cap sits under Groq's free-plan 1,000
# requests/day for the GPT-OSS models so the server sheds
# on its own schedule instead of eating a 429 mid-ask.
GROQ_DAILY_SOFT_CAP = int(
    os.environ.get("FSSA_GROQ_DAILY_SOFT_CAP", "900"))
_GROQ_USAGE_FILE = "groq_usage.json"
_GROQ_LOCK = threading.Lock()
_GROQ_STATE = {"date": "", "requests": 0, "list_usd": 0.0,
               "broken": False, "loaded": False}
# --- v27 (Change 1): the Cerebras expansion seat -------------------
# A pre-retrieval expansion seat beside the other provider seats:
# one fast Cerebras chat call rewrites the asker's question into
# 3-5 phrasings in manual/program vocabulary, and those phrasings
# are retrieved under the bundled merge discipline (see
# retrieve_ask_hits). Quota-only provider (no dollar cost is
# booked); the key loads via _nim_key_for exactly like the other
# seats (env var first, else the gitignored key file in the
# server dir). Missing key = seat OFF, and retrieval is
# byte-for-byte the pre-v27 behavior. The seat is FAIL-OPEN by
# construction: any failure returns [] and no exception escapes
# _cerebras_expand, ever.
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
CEREBRAS_KEY_ENV = "CEREBRAS_API_KEY"
CEREBRAS_KEY_FILE = "cerebras_key.txt"
CEREBRAS_DEFAULT_MODEL = "gpt-oss-120b"
CEREBRAS_TIMEOUT_S = 4.0  # STRICT total budget (connect + read)
CEREBRAS_MAX_TOKENS = 1024  # >= 512: gpt-oss-120b is a reasoning
# model; a small cap spends the budget in the reasoning field and
# returns empty content (verified in the connector record).
# Cloudflare rejects the default Python user agent with 403 /
# Error 1010 (verified production fact, 2026-10-16); the request
# MUST send a non-default UA.
CEREBRAS_USER_AGENT = "curl/8.5.0"
_CEREBRAS_EXPAND_CACHE = {}


def _cerebras_key():
    return _nim_key_for(CEREBRAS_KEY_ENV, CEREBRAS_KEY_FILE)


def _cerebras_model():
    """The seat's model: the retrieval profile's key when one is
    present there (a "cerebras_model" string, or a "model" inside
    a "cerebras" object), else the verified catalog default."""
    profile = (_V2.get("profile") or {}) if isinstance(
        globals().get("_V2"), dict) else {}
    m = profile.get("cerebras_model")
    if not m and isinstance(profile.get("cerebras"), dict):
        m = profile["cerebras"].get("model")
    return m or CEREBRAS_DEFAULT_MODEL


def _cerebras_post(payload, timeout):
    """One POST to the Cerebras chat endpoint. Returns the reply
    text; raises on any transport/HTTP/shape failure (the caller
    is fail-open). A single attempt on the caller's timeout: the
    seat's strict budget leaves no room for the shared
    transport's retry, so this seat does not ride _nim_text."""
    key = _cerebras_key()
    if not key:
        raise RuntimeError("cerebras API key not configured "
                           "(cerebras_key.txt missing)")
    req = urllib.request.Request(
        CEREBRAS_BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "User-Agent": CEREBRAS_USER_AGENT},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    if not content or not content.strip():
        raise RuntimeError("cerebras returned an empty response")
    return content


def _cerebras_parse_phrasings(text):
    """Parse the seat's plain list output into 3-5 phrasings.
    The multi-line extension of _clean_llm_list_output's
    discipline: preamble lines (a lead-in ending in ':' with no
    question content, 'here are ...' lines) are skipped, list
    furniture (bullets, numbering, quotes) is stripped, and a
    single comma-separated line splits on its commas."""
    out = []
    for raw in (text or "").split("\n"):
        line = raw.strip().strip('"').strip()
        line = re.sub(r"^(?:[-*\u2022]|\d+[.)])\s+", "", line)
        if not line:
            continue
        low = line.lower()
        if low.startswith(("here are", "here is", "sure,")):
            continue
        if line.endswith(":") and "?" not in line \
                and len(line.split()) <= 8:
            continue  # a lead-in line, not a phrasing
        parts = [line]
        if "," in line and "?" not in line and len(line) > 60:
            parts = [p.strip() for p in line.split(",")]
        for p in parts:
            if len(p.split()) >= 3 and p not in out:
                out.append(p)
        if len(out) >= 5:
            break
    return out[:5]


def _cerebras_expand(question):
    """3-5 rewrites of `question` in manual/program vocabulary
    (the words a policy manual would use), or [] on ANY failure:
    no key, timeout, HTTP error, empty or unparseable output.
    FAIL-OPEN: no exception escapes, ever. Results are cached
    per (question, generation), the v2 result cache's
    discipline, so repeats never pay for the seat twice."""
    try:
        if not _cerebras_key():
            return []
        gen = _V2.get("generation") if isinstance(
            globals().get("_V2"), dict) else None
        cache_key = (question, gen)
        cached = _cache_get(_CEREBRAS_EXPAND_CACHE, cache_key)
        if cached is not None:
            return list(cached)
        prompt = (
            "Rewrite the question below as 3 to 5 separate search "
            "phrasings, one per line, each phrased in the "
            "vocabulary an Indiana Medicaid / FSSA policy manual "
            "or program document would use for the same question "
            "(program names, manual terms, service names). "
            "Output ONLY the phrasings, one per line, no "
            "numbering, no explanations.\n\n"
            f"Question: {question}")
        payload = {
            "model": _cerebras_model(),
            "messages": [
                {"role": "system",
                 "content": "You are a precise retrieval utility. "
                            "Reply with only the requested "
                            "output, no explanations."},
                {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": CEREBRAS_MAX_TOKENS,
        }
        text = _cerebras_post(payload, timeout=CEREBRAS_TIMEOUT_S)
        phrasings = _cerebras_parse_phrasings(text)
        if len(phrasings) < 3:
            # Unparseable as a 3-5 phrasing list: treat as the
            # empty result (fail-open), and do not cache it.
            return []
        _cache_put(_CEREBRAS_EXPAND_CACHE, cache_key, phrasings)
        return list(phrasings)
    except Exception:
        return []


# The xAI grant-mode credit ledger (visibility only; only
# accrues when XAI_FREE_CREDITS is True — see above).
_XAI_CREDIT_FILE = "xai_spend.json"
_XAI_CREDIT_LOCK = threading.Lock()
_XAI_CREDIT_STATE = {"date": "", "credit": 0.0,
                     "broken": False, "loaded": False}
# v23.1 (WS-A/WS-B): the judge backup. When the primary judge
# (Gemini 3.5 Flash Lite, see _primary_judgment) cannot serve,
# the round falls back ONCE per ask to DeepSeek Chat V3.1 on
# OpenRouter — the bake-off's accuracy winner (62/65 verdicts;
# BAKEOFF.md), seated as backup rather than primary on
# speed/cost grounds (Lite: free, 4.7s median). The V4 models
# are OUT entirely (operator decision 2026-10-15: V4-Pro and
# V4-Flash were the bake-off's two worst judges; V4-Pro zeroed
# two claim sets when hidden reasoning ate its whole token
# budget). No V4 slug appears anywhere in this build.
JUDGE_BACKUP_MODEL = "deepseek/deepseek-chat-v3.1"
JUDGE_LEG_TIMEOUT_S = 60  # one judge leg's own bound; the
# ask deadline clamps it further via _judge_leg_timeout.


def _leg_timeout(base):
    """v20 (acceptance discipline): clamp one chain leg's
    timeout to the current unit's remaining budget, so a slow
    leg sheds at the budget edge instead of blowing the
    unit's wall (simple 150s / hard 285s). Returns 0.0 when
    the budget is spent — the caller must not start the leg.
    Outside a unit (no deadline published) the base timeout
    stands, exactly as in v19."""
    dl = _ask_ctx().unit_deadline
    if dl is None:
        return float(base)
    remaining = dl - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(float(base), remaining)


# --- v23.2 (BUG-11): ONE clock per ask + the shared draft cap ----
# F1: the ask clock is anchored ONCE, at ask start (do_POST's
# prologue, via _ask_clock_begin), and the class deadline is
# fixed the moment the ask's class is known (_ask_clock_class).
# Every later stage — the drafting ladder, the post-IDK rescue
# re-run, the branch re-run, claim extraction/checking, and
# finalization — consults THESE anchors. Nothing re-arms a
# budget from its own start ever again: through v23.1,
# _run_unit computed deadline = min(ask cap, now + budget)
# at every entry, so the rescue re-run of the CSRA ask got a
# fresh 285s window after the ladder's ~228s, the ask ran
# 416.9s, and budget_cut stayed False because no single
# invocation ever crossed its own private deadline.
# F2: draft_calls lives on the same context, so the ladder,
# the lead micro-repair, the rescue re-run, and the branch
# re-run all draw from the one counter.

def _ask_clock_begin(t0=None):
    """Anchor the ask's clock at ask start: t0 + the 600s
    outer cap. Idempotent within an ask (the first anchor
    wins). Returns the ask (outer) deadline."""
    ctx = _ask_ctx()
    if ctx.ask_t0 is None:
        ctx.ask_t0 = time.monotonic() if t0 is None else t0
        ctx.ask_deadline = ctx.ask_t0 + ASK_TIME_CAP_S
    return ctx.ask_deadline


def _ask_clock_class(hard):
    """Fix the ask's class deadline (150s simple / 285s hard
    from ASK START, not from any unit's start) once the class
    is known. Returns the class deadline."""
    ctx = _ask_ctx()
    _ask_clock_begin()
    if ctx.class_deadline is None:
        ctx.class_deadline = ctx.ask_t0 + (
            ASK_BUDGET_HARD_S if hard else ASK_BUDGET_SIMPLE_S)
    return ctx.class_deadline


def _ask_deadline():
    """The ask's ONE effective deadline: the earlier of the
    class deadline and the outer ask cap. None when no clock
    has been anchored (outside an ask)."""
    ctx = _ask_ctx()
    cands = [d for d in (ctx.class_deadline, ctx.ask_deadline)
             if d is not None]
    return min(cands) if cands else None


def _budget_remaining(deadline=None):
    """Seconds left on the ask clock (or on `deadline` when
    given). +inf when no deadline exists."""
    dl = _ask_deadline() if deadline is None else deadline
    if dl is None:
        return float("inf")
    return dl - time.monotonic()


def _call_may_start(deadline=None):
    """F1's start gate: a new provider call may start only
    when the remaining budget can cover a bounded attempt —
    at least MIN_CALL_START_S left (the call's own timeout is
    separately clamped to what remains, so a call in flight
    ends AT the deadline and is abandoned there)."""
    return _budget_remaining(deadline) >= MIN_CALL_START_S


def _note_budget_cut(phase=None):
    """Record the ask-level budget cut truthfully: the FIRST
    cut wins (its phase is where the budget actually ran
    out), later marks never rewrite it."""
    ctx = _ask_ctx()
    with ctx.lock:
        if not ctx.budget_cut:
            ctx.budget_cut = True
            if phase is None:
                _open = ctx.phase_open
                phase = _open[0] if _open else None
            ctx.cut_phase = phase


def _claim_draft_call():
    """F2: claim one drafting call from the ask's shared
    counter (ladder rungs, lead micro-repairs, rescue and
    branch re-runs all claim here). Returns False at the
    absolute cap — the caller must not start the call; the
    best judged draft on hand ships instead."""
    ctx = _ask_ctx()
    with ctx.lock:
        if ctx.draft_calls >= DRAFT_CALL_CAP:
            ctx.draft_capped = True
            return False
        ctx.draft_calls += 1
        return True


def _claim_quick_draft_call():
    """v26 (F9, D5 = PER-PART): claim one drafting call
    from the CURRENT Quick unit's own budget
    (QUICK_UNIT_DRAFT_CAP: the initial draft + the lane's
    one repair). Through v25.1 Quick claims drew on the
    shared per-ask counter above, so in a multi-part Quick
    ask the early parts' repairs could starve a later part
    of its FIRST draft — the shared cap is BUG-11's
    invariant for the Deep ladder and stays exactly as it
    was; only Quick's accounting moves. The claim still
    counts on the ask-wide draft_calls total (the ask
    record stays an honest count of drafting calls made);
    the CAP check is the per-unit one. _run_unit_quick
    resets the per-unit counter at entry."""
    ctx = _ask_ctx()
    with ctx.lock:
        if ctx.quick_unit_calls >= QUICK_UNIT_DRAFT_CAP:
            ctx.draft_capped = True
            return False
        ctx.quick_unit_calls += 1
        ctx.draft_calls += 1
        return True


def _roster_health_block():
    """GET /health's roster block (v23.1): the ask path's
    configured servers, stated plainly so a health read shows
    who drafts, who judges, and what backs each up. Config
    only — per-ask service is recorded on the ask record."""
    return {
        "drafting": {
            "primary": XAI_HARD_MODEL + " (xAI)",
            "backup": GROQ_HARD_MODEL + " (Groq)",
            "last_resort": "local " + LOCAL_MODEL},
        "judge": {
            "primary": GEMINI_CHAIN[0] + " (Gemini)",
            "backup": JUDGE_BACKUP_MODEL + " (OpenRouter, "
                      "at most once per ask)"},
        "checks": "deterministic figure + citation checks "
                  "under the judge",
        "budgets_s": {"simple": 150, "hard": 285,
                      "ask_cap": ASK_TIME_CAP_S}}


# --- v18 (F10): the per-ask context ----------------------------------
# Through v17.3, per-ask cost, seat events, and retrieval timings
# lived in thread-locals while the pipeline runs its judging,
# drafting, and retrieval in thread pools: everything a worker
# thread recorded was stranded and lost (per-ask cost_usd omitted
# both OpenRouter judge seats; the seat events that would have
# diagnosed the Lite + DeepSeek + DeepSeek panel never survived).
# The context is now an explicit object carried by a ContextVar;
# pool submissions go through _submit, which propagates the
# current context into the worker. The object also carries
# which model served each job this ask, the current unit's
# deadline, and (v23.1) whether the ask's single backup-judge
# use has been claimed.

class _AskCancelled(Exception):
    """v24 (§5.2): cooperative cancellation. Raised at the
    pipeline's clock seams when the job's cancel flag is set;
    do_POST converts it into the cancelled result + record."""


class _AskContext:
    __slots__ = ("cost", "events", "retr",
                 "served_models", "draft_family",
                 "lock", "unit_deadline", "retr_pools", "ask_id",
                 "phase_ms", "phase_open",
                 "judge_backup_used",
                 # v23.2 (BUG-11 F1/F2): the ONE ask clock
                 # (monotonic anchors; None until _ask_clock_
                 # begin runs), the shared per-ask drafting
                 # call counter, and the ask-level budget-cut
                 # record (first cut wins, so a cut in a unit
                 # whose re-run is later discarded still lands
                 # truthfully on the ask record).
                 "ask_t0", "ask_deadline", "class_deadline",
                 "draft_calls", "draft_capped",
                 # v26 (F9): the running Quick unit's own
                 # draft-claim count (reset per unit).
                 "quick_unit_calls",
                 "budget_cut", "cut_phase",
                 # v24: the progress tracker's shared state
                 # (per-unit section states for the job
                 # snapshot, §6.4) and the job record whose
                 # "cancel" flag the Stop button sets (§5.2).
                 "tracker", "cancel_rec",
                 # v27 (Changes 1+2): per-ask seat/wider-pass
                 # state (the seat runs at most once per ask,
                 # the wider pass at most once per ask).
                 "v27_cerebras_ran", "v27_wider_ran")

    def __init__(self):
        self.cost = 0.0
        self.events = []
        # v23 (WS1): per-phase wall time for the ask record
        # (see _set_phase) + the currently open phase. The
        # values are SECONDS (floats, millisecond precision)
        # despite the field's historical _ms name (BUG-7).
        self.phase_ms = {}
        self.phase_open = None
        # v23.1: the DeepSeek V3.1 backup judge may serve at
        # most once per ask; the checking round claims this
        # flag atomically under the context lock.
        self.judge_backup_used = False
        # v21: the ask's id (set by _events_reset per ask;
        # gap-log rows carry it so a queue candidate joins
        # back to its ask record).
        self.ask_id = None
        self.retr = {}
        # v20.2 (observability): one fused-pool snapshot per
        # ask-mode hybrid_search call (see _retr_pool_note);
        # persisted on the ask record so a rerank-window miss
        # is diagnosable after the fact.
        self.retr_pools = []
        self.served_models = {}      # job -> model that served last
        self.draft_family = None
        self.lock = threading.Lock()
        # v20: the current unit's monotonic deadline, set by
        # _run_unit per unit (units run sequentially inside an
        # ask); chain legs clamp their timeouts to what is
        # left of it via _leg_timeout. None outside a unit.
        # v23.2: _run_unit publishes the ask's ONE effective
        # deadline here (min of the class deadline and the ask
        # cap), never a per-invocation re-arm.
        self.unit_deadline = None
        # v23.2 (BUG-11 F1/F2): one ask clock + shared draft
        # counter + ask-level cut record (see __slots__ note).
        self.ask_t0 = None
        self.ask_deadline = None
        self.class_deadline = None
        self.draft_calls = 0
        self.draft_capped = False
        self.quick_unit_calls = 0
        self.budget_cut = False
        self.cut_phase = None
        # v24: see __slots__ note.
        self.tracker = None
        self.cancel_rec = None
        # v27 (Changes 1+2): see __slots__ note.
        self.v27_cerebras_ran = False
        self.v27_wider_ran = False


_ASK_CTX_VAR = contextvars.ContextVar("fssa_ask_ctx", default=None)


def _ask_ctx():
    """The current ask's context, created lazily (also outside
    /ask, e.g. /search, where the recorders still work)."""
    ctx = _ASK_CTX_VAR.get()
    if ctx is None:
        ctx = _AskContext()
        _ASK_CTX_VAR.set(ctx)
    return ctx


def _submit(pool, fn, *args, **kwargs):
    """ThreadPoolExecutor.submit that carries the current ask
    context into the worker thread (F10)."""
    return pool.submit(contextvars.copy_context().run,
                       fn, *args, **kwargs)


# --- v24 (§6): the progress tracker ---------------------------------
# Per-unit section states for the job snapshot. The tracker lives
# on the ask context (shared across the ask's threads exactly the
# way phase_ms is); do_POST creates one unit tracker per answering
# unit, _run_unit/_check_round update the CURRENT unit's tracker
# (carried in a ContextVar set at _run_unit entry), and do_POST
# finalizes the optional sections from the unit records (the
# decision points: a redraft that never ran is not-needed the
# moment its unit closes, a rescue the record marks skipped is
# skipped with the record's own reason). The page never guesses:
# a state the pipeline did not stamp stays "waiting".
_TRK_SECTIONS = (
    ("retrieving", "Retrieving"),
    ("drafting", "Drafting"),
    ("redraft2", "Redraft 2"),
    ("redraft3", "Redraft 3"),
    ("extracting", "Extracting claims"),
    ("checking", "Checking"),
    ("lead_repair", "Lead repair"),
    ("final_checks", "Final checks"),
    ("rescue", "Rescue"),
    ("web_branch", "Web branch"))
_TRK_VAR = contextvars.ContextVar("fssa_trk_unit", default=None)


def _trk_new_unit(index, part):
    return {"index": index, "part": part, "status": "waiting",
            "t0": None, "elapsed": None, "pass": 1,
            "caption": "Pass 1",
            "sections": {name: {"state": "waiting",
                                "seconds": None, "detail": "",
                                "t0": None}
                         for name, _label in _TRK_SECTIONS}}


def _trk_set(unit, name, state, detail=None):
    """Stamp one section of one unit tracker. Seconds are
    derived from the section's own active stamp, never counted."""
    if unit is None:
        return
    sec = unit["sections"].get(name)
    if sec is None:
        return
    now = time.monotonic()
    if state == "active" and sec["state"] != "active":
        sec["t0"] = now
    if state in ("done", "not-needed", "skipped", "failed"):
        if sec.get("t0") is not None:
            sec["seconds"] = round(now - sec["t0"], 1)
        sec["t0"] = None
    sec["state"] = state
    if detail is not None:
        sec["detail"] = detail


def _trk_current():
    return _TRK_VAR.get()


def _trk_phase(name, rnd):
    """Drive section states from the pipeline's own phase
    events (§6.4: states derive from phase facts, never from
    the client's guesses). Called from _set_phase."""
    unit = _TRK_VAR.get()
    if unit is None:
        return
    if unit.get("quick"):
        # v25: a Quick unit stamps its own sections (its
        # Deep-only rows flip to not-needed at unit start in
        # _run_unit_quick). The phase hook must not stamp
        # sections as if the judged pipeline ran.
        return
    if name == "drafting":
        if rnd and rnd >= 3:
            _trk_set(unit, "redraft2", "done")
            _trk_set(unit, "redraft3", "active")
        elif rnd and rnd == 2:
            _trk_set(unit, "drafting", "done")
            _trk_set(unit, "redraft2", "active")
        else:
            _trk_set(unit, "drafting", "active")
    elif name == "extracting":
        for sec_name in ("drafting", "redraft2", "redraft3"):
            if unit["sections"][sec_name]["state"] == "active":
                _trk_set(unit, sec_name, "done")
        if unit["sections"]["lead_repair"]["state"] == "active":
            _trk_set(unit, "lead_repair", "done")
        _trk_set(unit, "extracting", "active")
    elif name == "checking":
        _trk_set(unit, "extracting", "done")
        if unit["sections"]["lead_repair"]["state"] == "active":
            _trk_set(unit, "lead_repair", "done")
        _trk_set(unit, "checking", "active",
                 detail=f"round {rnd}" if rnd else None)
    elif name == "final checks":
        _trk_set(unit, "checking", "done")
        _trk_set(unit, "extracting", "done")
        _trk_set(unit, "final_checks", "active")


def _sections_snapshot(job):
    """The /ask/result snapshot's sections block (§6.4): the
    per-unit trackers as the client renders them, plus the
    ask-wide draft-call count. Returns None when the job has no
    tracker (a job that predates the ask's unit build)."""
    ctx = job.get("_ask_ctx")
    trk = getattr(ctx, "tracker", None) if ctx is not None else None
    if not trk:
        return None
    now = time.monotonic()
    units = []
    for u in trk["units"]:
        secs = []
        for name, label in _TRK_SECTIONS:
            sec = u["sections"][name]
            st = sec["state"]
            seconds = sec["seconds"]
            if st == "active" and sec.get("t0") is not None:
                seconds = round(now - sec["t0"], 1)
            secs.append({"id": name, "label": label, "state": st,
                         "seconds": seconds,
                         "detail": sec["detail"]})
        el = u["elapsed"]
        if el is None and u["t0"] is not None:
            el = round(now - u["t0"], 1)
        units.append({"index": u["index"], "part": u["part"],
                      "status": u["status"], "elapsed_s": el,
                      "caption": u["caption"], "sections": secs})
    return {"units": units,
            "draft_calls": getattr(ctx, "draft_calls", 0),
            "draft_cap": DRAFT_CALL_CAP,
            "parts": trk.get("parts", len(units))}


def _progress_summary(units):
    """v24 (§6.5): the post-run progress facts for the /ask
    response, derived from the unit RECORDS (never guessed):
    the unusual-path pills and one summary line per part for
    the "How this was checked" disclosure."""
    pills = []
    parts = []
    _rungs_max = 0
    for u in units or ():
        _rungs_max = max(_rungs_max, u.get("rungs") or 0)
        _rs = u.get("rescue")
        _wb = u.get("web")
        _rstate = None
        if isinstance(_rs, dict) and _rs.get("skipped"):
            _rstate = "skipped \u00b7 " + (
                "budget spent" if "time budget" in _rs["skipped"]
                else "draft limit reached")
            _p = "Rescue skipped \u00b7 " + (
                "budget spent" if "time budget" in _rs["skipped"]
                else "draft limit reached")
            if _p not in pills:
                pills.append(_p)
        elif _rs:
            _rstate = "used"
            if "Rescue used" not in pills:
                pills.append("Rescue used")
        _wstate = None
        if isinstance(_wb, dict) and _wb.get("skipped"):
            _wstate = "skipped"
            _p = "Web branch skipped \u00b7 " + (
                "budget spent" if "time budget" in _wb["skipped"]
                else "draft limit reached")
            if _p not in pills:
                pills.append(_p)
        elif (_wb and _wb.get("branched")) or u.get("web_used"):
            _wstate = "used"
            if "Web branch used" not in pills:
                pills.append("Web branch used")
        parts.append({
            "part": u.get("part"), "badge": u.get("badge"),
            "rungs": u.get("rungs") or 0,
            "lead_repairs": u.get("lead_repairs") or 0,
            "rescue": _rstate, "web": _wstate,
            "budget_cut": bool(u.get("budget_cut")),
            "draft_capped": bool(u.get("draft_capped"))})
    if _rungs_max >= 2:
        pills.insert(0, "Redraft 2 used")
    if _rungs_max >= 3:
        pills.insert(1, "Redraft 3 used")
    return {"pills": pills, "parts": parts}


def _cancel_requested():
    """v24 (§5.2): has the Stop button fired for this ask? The
    flag lives on the job record; the ask context holds the
    reference (set in do_POST). Sync asks have no job and can
    never be cancelled."""
    ctx = _ask_ctx()
    rec = ctx.cancel_rec
    return bool(rec is not None and rec.get("cancel"))


def _check_cancel():
    if _cancel_requested():
        raise _AskCancelled()


def _model_family(model):
    """v18 (§C, pass 2 A9): model identity for the diversity
    guard is the FAMILY (vendor/base lineage), not the slug:
    DeepSeek V3.1 and V4, or Nemotron Super and Ultra, are one
    voice and may never hold two seats on the same panel."""
    m = (model or "").lower()
    if "grok" in m:
        return "xai"  # v20: the Grok line is its own family;
        # the drafting-family exclusion depends on it never
        # colliding with a judge family.
    if "gpt-oss" in m or m.startswith("openai/"):
        return "openai"
    if "deepseek" in m:
        return "deepseek"
    if "qwen" in m:
        return "qwen"
    if "nemotron" in m:
        return "nemotron"
    if "gemini" in m:
        return "google"
    if "kimi" in m or "moonshot" in m:
        return "moonshot"
    if "glm" in m or "z-ai" in m:
        return "zhipu"
    if "llama" in m:
        return "meta"
    if "mistral" in m or "mixtral" in m:
        return "mistral"
    return m.split("/")[-1].split("-")[0] or "unknown"


# Per-ask event recorders (v18: backed by the ask context, not
# thread-locals): which seats actually served, and every local
# LLM load with its trigger (item 5's local_loads record).

def _events():
    return _ask_ctx().events


def _events_reset():
    # A fresh ask gets a fresh context (cost, events, retrieval
    # timings, panel state all reset together).
    ctx = _AskContext()
    # v21: every ask gets an id at birth; the ask record and
    # its gap-log rows all carry it (growth-loop join key).
    ctx.ask_id = uuid.uuid4().hex[:12]
    _ASK_CTX_VAR.set(ctx)


def _note_seat(job, model, failover):
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.events.append({"kind": "seat", "job": job,
                           "model": model,
                           "failover": bool(failover)})
        ctx.served_models[job] = model


def _note_local_load(trigger):
    _ask_ctx().events.append({"kind": "local_load",
                              "trigger": trigger})


def _events_since(idx):
    return list(_ask_ctx().events[idx:])


# Retrieval sub-timings (item 8): accumulated per ask across
# its retrieval calls; the handler snapshots them into the
# ask's timing.retrieve_split. Stages: rewrite (the two LLM
# query rewrites), embed (query embedding), vector (similarity
# search), lexical (keyword searches), fusion (RRF + boosts +
# bridge searches + ranking), rerank (the LLM rerank call).

def _retr_reset():
    _ask_ctx().retr = {}


def _retr_note(stage, seconds):
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.retr[stage] = ctx.retr.get(stage, 0.0) + seconds


def _retr_snapshot():
    split = _ask_ctx().retr or {}
    return {k: int(v * 1000) for k, v in split.items()}


def _retr_pool_note(entry):
    """v20.2 (observability): stash one ask-mode
    hybrid_search pool snapshot on the ask context. Capped
    at 6 calls per ask (the main retrieval plus bundled
    per-part calls fit; later hunt/repair retrievals are
    dropped rather than bloating the record). Never raises:
    a logging failure must never fail an ask."""
    try:
        ctx = _ask_ctx()
        with ctx.lock:
            if len(ctx.retr_pools) < 6:
                ctx.retr_pools.append(entry)
    except Exception:
        pass


def _retr_pools_since(idx):
    return list(_ask_ctx().retr_pools[idx:])


def _note_seat_fail(job, model, slot, latency_s, timed_out, error):
    """v20.2 (observability): one FAILED judge-seat leg,
    with the reason. Through v20.1 a failed candidate left
    no trace in the ask record — _seat_report kept only
    aggregate health counters and the exception string was
    discarded — so a seat could die silently (the
    2026-10-14 seat-B death: "unavailable", reason
    unrecoverable). The error is stored as
    "ExceptionClass: message", truncated to 200 chars."""
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.events.append({
            "kind": "seat_fail", "job": job, "model": model,
            "slot": slot, "latency_s": round(latency_s, 2),
            "timed_out": bool(timed_out),
            "error": str(error)[:200]})


def _note_seat_down(job, reason):
    """v20.2 (observability): the seat as a whole gave up
    (every candidate failed, or the unit budget shed the
    leg before it started). Pairs with the seat_fail
    events that precede it in the same record."""
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.events.append({"kind": "seat_down", "job": job,
                           "error": str(reason)[:200]})


def _note_rescue(terms, injected, trigger, query=""):
    """v21 (WS1a): the exact-term rescue pass fired. The pool
    snapshots carry the injected chunks (rs/rg ranks); this
    event carries the trigger and the terms that drove it,
    so a rescued ask is diagnosable from the record alone."""
    try:
        ctx = _ask_ctx()
        with ctx.lock:
            ctx.events.append({
                "kind": "rescue", "trigger": trigger,
                "terms": list(terms)[:6],
                "injected": int(injected),
                "q": (query or "")[:80]})
    except Exception:
        pass


# --- v16 item 7: Tavily branch-out ------------------------------------
# Fires ONLY at a unit's honest IDK disposition: basic searches
# (1 credit each) restricted to the official whitelist, top
# pages fetched and held as TEMPORARY excerpts for that ask.
# Budgets: per ask at most BRANCH_MAX_SEARCHES searches and
# BRANCH_MAX_FETCHES page fetches; a monthly credit counter in
# the server dir disables the branch at BRANCH_MONTHLY_STOP
# rather than discovering the free pool's end mid-ask.
TAVILY_KEY_FILE = "tavily_key.txt"
TAVILY_KEY_ENV = "TAVILY_API_KEY"
BRANCH_OUT_ENABLED = True
BRANCH_WHITELIST = ("in.gov", "medicaid.gov", "cms.gov",
                    "ssa.gov", "irs.gov")
BRANCH_MAX_SEARCHES = 3
BRANCH_MAX_FETCHES = 5
BRANCH_MONTHLY_STOP = 800
_TAVILY_STATE_FILE = "tavily_state.json"
_TAVILY_LOCK = threading.RLock()


def _tavily_key():
    return _nim_key_for(TAVILY_KEY_ENV, TAVILY_KEY_FILE)


def _tavily_state():
    """(Caller holds _TAVILY_LOCK.) The monthly counter,
    read with the spend ledger's house rule (v26, F2 —
    the mirror of _or_load_locked): a MISSING state
    file is a legitimate fresh zero; a file that exists
    but cannot be parsed marks the counter BROKEN and
    the web branch fails closed until the file is
    repaired. Through v25.1 any read error silently
    reset the month to zero, and the 800-credit stop
    stopped binding."""
    month = time.strftime("%Y-%m")
    try:
        with open(_TAVILY_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return {"month": month, "credits": 0}
    except Exception:
        return {"month": month, "credits": 0, "broken": True}
    if not isinstance(st, dict) or st.get("month") != month:
        return {"month": month, "credits": 0}
    try:
        credits = int(st.get("credits") or 0)
    except (TypeError, ValueError):
        return {"month": month, "credits": 0, "broken": True}
    return {"month": month, "credits": credits}


def _tavily_credits():
    with _TAVILY_LOCK:
        return _tavily_state()


def _tavily_claim():
    """v26 (F2): check-and-increment ONE credit under
    the lock, BEFORE a search is attempted, so two
    concurrent branch attempts can no longer both pass
    the stop check on the same credits. A credit
    claimed for a search that then fails in transport
    stays counted — the conservative direction, since
    Tavily may have served it. Returns False when the
    counter is broken or the monthly stop is already
    reached (the caller then issues no search)."""
    with _TAVILY_LOCK:
        st = _tavily_state()
        if st.get("broken") \
                or st["credits"] >= BRANCH_MONTHLY_STOP:
            return False
        st["credits"] += 1
        try:
            # v18: atomic write, like the spend ledger (a crash
            # mid-write must not zero the monthly counter).
            _atomic_write_json(_TAVILY_STATE_FILE,
                               {"month": st["month"],
                                "credits": st["credits"]})
        except Exception:
            pass
        return True


def _tavily_post(path, payload, timeout=45):
    key = _tavily_key()
    if not key:
        raise RuntimeError("tavily key not configured")
    payload = dict(payload)
    payload["api_key"] = key
    body = json.dumps(payload).encode()
    status, raw = _https_post(
        "api.tavily.com", path, body,
        {"Content-Type": "application/json"}, timeout=timeout)
    if status in (429, 402):
        raise RuntimeError(f"tavily unavailable (HTTP {status})")
    if status != 200:
        raise RuntimeError(f"tavily HTTP {status}")
    return json.loads(raw)


def _branch_queries(question, claims):
    """Search queries for a branch-out: the question itself,
    then the question narrowed by the top failed claims' key
    terms (the evidence hunt's query shape). At most
    BRANCH_MAX_SEARCHES, deduped."""
    qs = [question]
    for c in claims or ():
        if len(qs) >= BRANCH_MAX_SEARCHES:
            break
        words = [w for w in re.findall(r"[a-z0-9$]{4,}",
                                       (c.get("text") or "").lower())
                 if w not in STOP][:6]
        if words:
            q = question + " " + " ".join(words)
            if q not in qs:
                qs.append(q)
    return qs[:BRANCH_MAX_SEARCHES]


def _html_to_text(html):
    """Branch-out page text (v18, R6): script/style/noscript
    blocks are removed BEFORE the blanket tag strip. Through
    v17.3 the tag strip alone left script and navigation text
    in the page body, where it could masquerade as content."""
    t = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1>",
               " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california",
    "colorado", "connecticut", "delaware", "florida", "georgia",
    "hawaii", "idaho", "illinois", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "west virginia", "wisconsin", "wyoming")


def _names_other_state(text):
    """The first non-Indiana US state named in the text, or
    None. Washington is excluded deliberately: federal pages
    say Washington, D.C. constantly (v18, R6)."""
    low = (text or "").lower()
    if "indiana" in low:
        return None
    for st in _US_STATE_NAMES:
        if st in low:
            return st
    return None


def _branch_out_hits(question, claims):
    """Run the branch-out for one unit. Returns (web_hits,
    record): web_hits are temporary excerpt dicts marked
    source='web' with the fetch date; record is the metadata
    block (queries, urls, credits) or an 'unavailable' note.
    Raises nothing: any failure returns ([], record) and the
    caller keeps the honest IDK it already had."""
    rec = {"queries": [], "urls": [], "credits": 0,
           "fetched": time.strftime("%Y-%m-%d")}
    if not BRANCH_OUT_ENABLED:
        rec["unavailable"] = "disabled by config"
        return [], rec
    if not _tavily_key():
        rec["unavailable"] = "no tavily key"
        return [], rec
    _tst = _tavily_credits()
    if _tst.get("broken"):
        rec["unavailable"] = ("monthly counter state "
                              "unreadable; web branch "
                              "fails closed (v26 F2)")
        return [], rec
    if _tst["credits"] >= BRANCH_MONTHLY_STOP:
        rec["unavailable"] = "monthly credit budget reached"
        return [], rec
    queries = _branch_queries(question, claims)
    urls, seen, titles = [], set(), {}
    try:
        for q in queries:
            if not _tavily_claim():
                raise RuntimeError(
                    "monthly credit budget reached")
            d = _tavily_post("/search", {
                "query": q, "search_depth": "basic",
                "include_domains": list(BRANCH_WHITELIST),
                "max_results": 5})
            rec["credits"] += 1
            rec["queries"].append(q)
            for r in d.get("results", []):
                u = r.get("url") or ""
                host = urlparse(u).netloc.lower()
                if u and u not in seen and any(
                        host == w or host.endswith("." + w)
                        for w in BRANCH_WHITELIST):
                    seen.add(u)
                    urls.append(u)
                    if r.get("title"):
                        titles[u] = str(r["title"])[:120]
            if len(urls) >= BRANCH_MAX_FETCHES:
                break
    except Exception as e:
        rec["unavailable"] = str(e)[:150]
        return [], rec
    urls = urls[:BRANCH_MAX_FETCHES]
    if not urls:
        rec["unavailable"] = "no whitelist results"
        return [], rec
    texts = {}
    try:
        if _tavily_claim():
            d = _tavily_post("/extract", {"urls": urls,
                                          "extract_depth": "basic"})
            rec["credits"] += 1
            for r in d.get("results", []):
                if r.get("url") and r.get("raw_content"):
                    texts[r["url"]] = r["raw_content"]
    except Exception:
        pass  # direct fetch below covers what extract missed
    web_hits = []
    for u in urls:
        text = texts.get(u)
        if not text:
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(
                            u, headers={"User-Agent":
                                        "FSSA-QA branch-out"}),
                        timeout=20) as r:
                    final_url = r.geturl() or u
                    html = r.read().decode("utf-8", "ignore")
                # v18 (R6): the whitelist is re-checked against
                # the POST-REDIRECT url; a redirect off the
                # whitelist discards the page entirely.
                fhost = urlparse(final_url).netloc.lower()
                if not any(fhost == w or fhost.endswith("." + w)
                           for w in BRANCH_WHITELIST):
                    continue
                text = _html_to_text(html)
            except Exception:
                continue
        # v18 (R6): a federal page (medicaid.gov / cms.gov)
        # about ANOTHER state's program is not evidence for
        # an Indiana question; drop it when it names a
        # different state and never mentions Indiana.
        _host = urlparse(u).netloc.lower()
        if _host.endswith(("medicaid.gov", "cms.gov")) and \
                _names_other_state(
                    (titles.get(u) or "") + " " + (text or "")[:800]):
            continue
        chunks = _chunks_plain(text)
        if not chunks:
            continue
        qtoks = set(re.findall(r"[a-z]{4,}", question.lower()))
        scored = sorted(
            chunks,
            key=lambda c: -len(qtoks & set(
                re.findall(r"[a-z]{4,}", c.lower()))))
        excerpt = "\n".join(scored[:2])[:2200]
        web_hits.append({
            "title": titles.get(u) or u.rsplit("/", 1)[-1] or u,
            "url": u, "excerpt": excerpt,
            "program": "Official web source",
            "dtype": "web page (live fetch)",
            "source": "web", "web": True,
            "fetched": rec["fetched"], "verified": False})
        rec["urls"].append(u)
    return web_hits, rec


# --- v17: OpenRouter primary seats ----------------------------------
# The paid swap (operator go, 2026-10-13): OpenRouter takes the
# primary drafting seats and the retrieval utilities; the v16
# NIM seats remain as the fallback chain (see _generate and
# _utility_text), judges and the divergence oracle are
# untouched. Model slugs and backend labels verified against
# OpenRouter's live catalog at build time: openai/gpt-oss-120b
# is hosted by Cerebras (its fastest backend, fp16) and Groq;
# openai/gpt-oss-20b by Groq (no Cerebras endpoint). Routing
# pins the fastest backend first with fallbacks allowed to the
# model's other backends, and data_collection "deny" restricts
# the pool to backends that do not collect prompts for
# training. Every call asks for usage reporting so the
# response carries its exact cost (verified live: usage.cost
# in USD alongside the token counts).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_FILE = "openrouter_key.txt"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_HARD_MODEL = "openai/gpt-oss-120b"
OPENROUTER_SIMPLE_MODEL = "openai/gpt-oss-20b"
OPENROUTER_UTILITY_MODEL = "openai/gpt-oss-20b"
# v20 (judge seat A's paid fallover): Gemini 3.5 Flash-Lite
# via OpenRouter — the same model as GEMINI_CHAIN[0], so the
# primary judge's fallover keeps its model and its family.
# Slug and price read off the live OpenRouter catalog on
# 2026-10-14 ($0.30 in / $2.50 out per 1M).
OPENROUTER_DRAFTER_TIMEOUT = 120  # Cerebras drafts land in
# seconds; the bound only caps a degraded backend before the
# NIM seat takes over (the NIM path keeps DRAFTER_TIMEOUT).
OPENROUTER_DAILY_CAP_USD = float(
    os.environ.get("OPENROUTER_DAILY_CAP_USD", "10.00"))
_OR_ROUTES = {
    OPENROUTER_HARD_MODEL: {
        "order": ["Cerebras", "Groq"], "allow_fallbacks": True,
        "data_collection": "deny"},
    OPENROUTER_SIMPLE_MODEL: {
        "order": ["Groq"], "allow_fallbacks": True,
        "data_collection": "deny"},
    # v23.1: the backup judge's OpenRouter leg. Same posture
    # as every judge route before it: fallbacks allowed
    # across the model's backends, data collection denied, so
    # only backends that do not collect prompts may serve.
    JUDGE_BACKUP_MODEL: {
        "allow_fallbacks": True, "data_collection": "deny"},
}

# --- v18: the reservation gate + spend ledger (the §B hard stop) -------
# the operator's mechanism (v18 PLAN §B): the daily OpenRouter cap is a
# true hard ceiling ($2.00/day through v20.2; $10.00/day in v21
# at the operator's direction), enforced by RESERVATIONS at a
# provable maximum at the single choke point (_nim_text with
# or_route=True — every OpenRouter call in the server passes
# through it; tests/test_hard_stop.py audits that no paid path
# bypasses it). Before a call is admitted, the gate computes its
# maximum possible cost (input bounded by the actual prompt's
# byte length — every token is at least one byte — plus the
# call's max_tokens at the model's output price, the price row
# being the most expensive backend the route may fall back to)
# and, under a lock, checks settled + outstanding reservations +
# this maximum against the ceiling. Over the line, the call
# never starts and the seat behaves as unavailable. On a clean
# response the reservation settles to the billed cost; on ANY
# other outcome (timeout, transport failure, 5xx, a response
# with no usage.cost) it settles at the full reserved maximum,
# per HTTP attempt, so a retry can never double-spend invisibly
# (F11-1). The ledger persists in openrouter_spend.json, written
# atomically (temp file + os.replace); a file that exists but
# cannot be parsed fails the gate CLOSED, never resets to $0
# (F11-2); reservations left unsettled by a crash are reloaded
# at full value and count against the ceiling for the rest of
# the day. Settled spend reconciles against the key's
# usage_daily, taking the maximum (F11-3). The ledger's day is
# America/Indiana/Indianapolis (pass 2 item F6), computed
# arithmetically so it never depends on platform tzdata.
# Behind the code sit two backstops the code cannot override:
# the key's $10 OpenRouter limit and the prepaid balance.
# The other spend/quota surfaces ride the same audit: Gemini
# (daily quota, _GeminiQuota downgrade + the Flash budget) and
# Tavily (the monthly credit counter with its stop) each have
# their stop behavior at their own choke points.
_OR_SPEND_FILE = "openrouter_spend.json"
_OR_LOCK = threading.Lock()
_OR_STATE = {"date": "", "spend": 0.0, "reservations": {},
             "broken": False, "loaded": False}
# USD per 1M tokens (input, output). Each row is a conservative
# maximum across the backends the model's route may fall back
# to (allow_fallbacks), NOT the cheapest endpoint's price.
_OR_PRICES = {
    "openai/gpt-oss-120b": (0.10, 0.50),
    "openai/gpt-oss-20b": (0.05, 0.30),
}
# v23.1: the backup judge's row, priced conservatively at
# the strong-cheap class maximum (the reservation gate prices
# from this row; settled cost comes from the API's own usage
# report — the bake-off's nine V3.1 passes settled at
# $0.0118 total, far under this row).
_OR_PRICES[JUDGE_BACKUP_MODEL] = (0.60, 2.40)
_OR_PRICE_DEFAULT = (2.00, 6.00)


def _ledger_today():
    """Today's date (YYYY-MM-DD) in America/Indiana/Indianapolis,
    under US Eastern DST rules (second Sunday of March 07:00 UTC
    to first Sunday of November 06:00 UTC)."""
    now = time.time()
    year = time.gmtime(now).tm_year

    def _nth_sunday(y, month, n):
        first_weekday = calendar.weekday(y, month, 1)  # Mon=0
        return 1 + (6 - first_weekday) % 7 + 7 * (n - 1)

    dst_start = calendar.timegm(
        (year, 3, _nth_sunday(year, 3, 2), 7, 0, 0, 0, 0, 0))
    dst_end = calendar.timegm(
        (year, 11, _nth_sunday(year, 11, 1), 6, 0, 0, 0, 0, 0))
    offset = -4 * 3600 if dst_start <= now < dst_end else -5 * 3600
    return time.strftime("%Y-%m-%d", time.gmtime(now + offset))


def _atomic_write_json(path, obj):
    """Write JSON atomically: temp file in the same directory,
    fsync, os.replace. A crash can leave a stale temp file but
    never a truncated target (F11-2)."""
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _or_load_locked():
    """(Caller holds _OR_LOCK.) Bring the in-memory ledger
    current with disk for the ledger day. Unsettled reservations
    persist in the file, so a restart counts them at full value.
    A file from another day is a fresh day; a file that exists
    but cannot be parsed marks the ledger BROKEN and the gate
    fails closed."""
    today = _ledger_today()
    if _OR_STATE["loaded"] and _OR_STATE["date"] == today:
        return
    spend, reservations, broken = 0.0, {}, False
    try:
        with open(_OR_SPEND_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            spend = float(st.get("spend") or 0.0)
            raw_res = st.get("reservations") or {}
            reservations = {str(k): float(v)
                            for k, v in raw_res.items()}
    except FileNotFoundError:
        pass  # fresh install: zero is the truth
    except Exception:
        broken = True
    _OR_STATE.update({"date": today, "spend": spend,
                      "reservations": reservations,
                      "broken": broken, "loaded": True})
    if broken:
        print("[openrouter] spend ledger unreadable; the gate "
              "fails CLOSED (OpenRouter seats unavailable) "
              "until the ledger is repaired", flush=True)


def _or_persist_locked():
    """(Caller holds _OR_LOCK.)"""
    if _OR_STATE["broken"]:
        return
    try:
        _atomic_write_json(_OR_SPEND_FILE, {
            "date": _OR_STATE["date"],
            "spend": _OR_STATE["spend"],
            "reservations": _OR_STATE["reservations"]})
    except Exception as e:
        print(f"[openrouter] ledger write failed: {e}",
              flush=True)


def _or_reconciled_floor():
    """The provider-side settled figure for the ledger day, from
    the cached /auth/key state's usage_daily (F11-3): the
    maximum of the dated rows for the ledger day and the UTC
    day (the provider's day frame). 0.0 when unknown; never
    fetches.

    Only a list of dated rows contributes. A scalar (or any
    other non-list shape) returns 0.0: live observation
    (2026-10-14) showed the provider returning a cumulative
    scalar under this field, equal to the key's total usage,
    and an undated figure cannot be attributed to the ledger
    day; counting it would fence off every OpenRouter seat
    once lifetime usage passed the daily ceiling. The
    reservation ledger is the daily source of truth; dated
    rows still reconcile as a floor."""
    data = _OR_KEY_STATE.get("data") or {}
    ud = data.get("usage_daily")
    days = {_ledger_today(), time.strftime("%Y-%m-%d",
                                           time.gmtime())}
    try:
        if isinstance(ud, list):
            vals = [float(row.get("usage") or 0.0)
                    for row in ud
                    if isinstance(row, dict)
                    and row.get("date") in days
                    and row.get("usage") is not None]
            return max(vals) if vals else 0.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _or_committed_locked():
    """(Caller holds _OR_LOCK.) What counts against the
    ceiling: settled spend reconciled against the provider
    figure (the maximum of the two), plus every outstanding
    reservation at full value."""
    settled = max(_OR_STATE["spend"], _or_reconciled_floor())
    return settled + sum(_OR_STATE["reservations"].values())


def _or_spend_today():
    """Today's settled OpenRouter spend in USD (the ledger's
    own figure, persisted across restarts; a new day starts
    at zero)."""
    with _OR_LOCK:
        _or_load_locked()
        return _OR_STATE["spend"]




def _max_cost_from(prices, default, model, system, user,
                   max_tokens):
    """A call's provable maximum cost at a price table:
    input tokens bounded by the prompts' byte length (a
    token is at least one byte, so bytes are a true upper
    bound — pass 2 item F5), output bounded by max_tokens."""
    pin, pout = prices.get(model, default)
    in_bound = len((system or "").encode("utf-8")) \
        + len((user or "").encode("utf-8"))
    return (in_bound * pin + int(max_tokens) * pout) / 1_000_000.0


def _or_max_cost(model, system, user, max_tokens):
    """One OpenRouter call's provable maximum cost (see
    _max_cost_from)."""
    return _max_cost_from(_OR_PRICES, _OR_PRICE_DEFAULT,
                          model, system, user, max_tokens)


def _usage_cost(prices, default, model, usage):
    """A completed call's cost from its reported token
    usage, or None when the response carried no usable
    usage block (callers then settle at the reserved
    maximum, the F11-1 rule)."""
    if not isinstance(usage, dict):
        return None
    try:
        pin, pout = prices.get(model, default)
        pt = float(usage.get("prompt_tokens") or 0)
        ct = float(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if pt <= 0 and ct <= 0:
        return None
    return (pt * pin + ct * pout) / 1_000_000.0


def _or_reserve(max_cost):
    """Reserve max_cost against today's ceiling, under the
    lock. Returns a reservation id, or None when admitting the
    call could cross the ceiling (the call then never starts)
    or the ledger is broken."""
    with _OR_LOCK:
        _or_load_locked()
        if _OR_STATE["broken"]:
            return None
        if _or_committed_locked() + float(max_cost) \
                > OPENROUTER_DAILY_CAP_USD + 1e-9:
            return None
        rid = uuid.uuid4().hex[:12]
        _OR_STATE["reservations"][rid] = round(float(max_cost), 6)
        _or_persist_locked()
        return rid


def _or_settle(rid, actual):
    """Settle one reservation. The ledger gains the ACTUAL
    billed cost when the provider reported one, and the full
    reserved maximum otherwise (timeout, transport failure,
    5xx, or a response carrying no usage.cost). The ask
    context gains the same amount as this ask's cost (F10)."""
    if rid is None:
        return
    with _OR_LOCK:
        _or_load_locked()
        reserved = _OR_STATE["reservations"].pop(rid, None)
        if reserved is None:
            return
        amount = reserved if actual is None else float(actual)
        before = _OR_STATE["spend"]
        _OR_STATE["spend"] = before + amount
        _or_persist_locked()
        crossed = before < OPENROUTER_DAILY_CAP_USD \
            <= _OR_STATE["spend"]
        total = _OR_STATE["spend"]
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.cost += amount
    if crossed:
        print(f"[openrouter] daily spend cap reached "
              f"(${total:.4f} >= ${OPENROUTER_DAILY_CAP_USD:.2f}); "
              f"OpenRouter seats unavailable until tomorrow, "
              f"the NIM fallback chain takes over", flush=True)




def _or_reset_ask():
    _ask_ctx().cost = 0.0


def _or_ask_cost():
    """This ask's OpenRouter spend in USD (0.0 on the free
    paths), for the answer metadata's cost_usd. v18 (F10): read
    from the ask context, so judge-seat spend recorded on pool
    worker threads is included."""
    return round(_ask_ctx().cost, 6)


def _or_key():
    return _nim_key_for(OPENROUTER_KEY_ENV, OPENROUTER_KEY_FILE)


def _or_cap_tripped():
    with _OR_LOCK:
        _or_load_locked()
        if _OR_STATE["broken"]:
            return True
        return _or_committed_locked() >= OPENROUTER_DAILY_CAP_USD


def _or_available():
    """The OpenRouter seats may run: a key is configured and
    the day's committed spend (settled + reservations) is
    under the ceiling. Anything else and the seats are treated
    as unavailable (spec: cap trip = seat unavailable),
    exactly like a dormant NIM seat. The binding gate is the
    reservation itself, taken in _nim_text per attempt."""
    return _or_key() is not None and not _or_cap_tripped()


# The key's own state from OpenRouter's GET /auth/key: its
# dashboard-side limit, what is left of it, and its recorded
# usage. Read at startup and cached for /health (refreshed
# when older than five minutes). Never includes the key.
_OR_KEY_STATE = {"fetched_at": 0.0, "data": None, "error": None}
_OR_KEY_STATE_LOCK = threading.Lock()


def _or_key_state(force=False):
    if not force and _OR_KEY_STATE["data"] is not None \
            and time.monotonic() - _OR_KEY_STATE["fetched_at"] < 300:
        return _OR_KEY_STATE
    key = _or_key()
    if not key:
        with _OR_KEY_STATE_LOCK:
            _OR_KEY_STATE.update(
                {"fetched_at": time.monotonic(), "data": None,
                 "error": "no key configured"})
        return _OR_KEY_STATE
    try:
        status, raw = _https_get(
            "openrouter.ai", "/api/v1/auth/key",
            {"Authorization": "Bearer " + key}, timeout=15)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        data = (json.loads(raw) or {}).get("data") or {}
        keep = {"limit": data.get("limit"),
                "limit_remaining": data.get("limit_remaining"),
                "usage": data.get("usage"),
                "usage_daily": data.get("usage_daily"),
                "is_free_tier": data.get("is_free_tier")}
        with _OR_KEY_STATE_LOCK:
            _OR_KEY_STATE.update(
                {"fetched_at": time.monotonic(), "data": keep,
                 "error": None})
    except Exception as e:
        with _OR_KEY_STATE_LOCK:
            _OR_KEY_STATE.update(
                {"fetched_at": time.monotonic(),
                 "error": str(e)[:150]})
    return _OR_KEY_STATE


def _or_health_block():
    """GET /health's openrouter block: the code-side guardrail
    (today's spend vs the daily cap) next to the key's own
    dashboard-side limit state."""
    ks = _or_key_state()
    data = ks.get("data") or {}
    return {"configured": _or_key() is not None,
            "hard_model": OPENROUTER_HARD_MODEL,
            "simple_model": OPENROUTER_SIMPLE_MODEL,
            "key_limit": data.get("limit"),
            "key_limit_remaining": data.get("limit_remaining"),
            "key_usage": data.get("usage"),
            "key_state_error": ks.get("error"),
            "spend_today_usd": round(_or_spend_today(), 6),
            "daily_cap_usd": OPENROUTER_DAILY_CAP_USD,
            "cap_tripped": _or_cap_tripped()}


def _or_text(model, system, user, temperature=0, max_tokens=2048,
             timeout=60, deadline=None):
    """One OpenRouter chat call with usage reporting and the
    model's pinned backend routing. Raises when the seat
    cannot run (no key, cap tripped, provider failure) so the
    caller falls back to the v16 NIM assignment."""
    if not _or_available():
        raise RuntimeError(
            "openrouter unavailable (no key or daily cap reached)")
    extra = {"usage": {"include": True}}
    route = _OR_ROUTES.get(model)
    if route:
        extra["provider"] = route
    return _nim_text(OPENROUTER_BASE_URL, OPENROUTER_KEY_ENV,
                     OPENROUTER_KEY_FILE, model, system, user,
                     temperature=temperature, max_tokens=max_tokens,
                     timeout=timeout, provider="openrouter",
                     extra=extra, or_route=True, deadline=deadline)


# --- v20: the Groq daily guard ---------------------------------------
# A persistent per-day counter of Groq requests (groq_usage.json,
# Indiana ledger day, atomic writes — the spend ledger's pattern).
# Past GROQ_DAILY_SOFT_CAP, Groq entries are skipped in every
# chain (the fallover proceeds) until the next ledger day. The
# counter also accumulates the day's list-price value of Groq
# calls for visibility: free-tier calls are recorded at list
# price, provider_tier=free, and never touch the real-money
# ledger.

def _groq_load_locked():
    """(Caller holds _GROQ_LOCK.)"""
    today = _ledger_today()
    if _GROQ_STATE["loaded"] and _GROQ_STATE["date"] == today:
        return
    requests, list_usd, broken = 0, 0.0, False
    try:
        with open(_GROQ_USAGE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            requests = int(st.get("requests") or 0)
            list_usd = float(st.get("list_usd") or 0.0)
    except FileNotFoundError:
        pass
    except Exception:
        broken = True
    _GROQ_STATE.update({"date": today, "requests": requests,
                        "list_usd": list_usd, "broken": broken,
                        "loaded": True})
    if broken:
        # A guard that cannot read its counter sheds Groq
        # (fail-safe toward the fallovers), never waves it
        # through past an unknown count.
        print("[groq] usage counter unreadable; Groq sheds "
              "to its fallovers until the counter is repaired",
              flush=True)


def _groq_persist_locked():
    """(Caller holds _GROQ_LOCK.)"""
    if _GROQ_STATE["broken"]:
        return
    try:
        _atomic_write_json(_GROQ_USAGE_FILE, {
            "date": _GROQ_STATE["date"],
            "requests": _GROQ_STATE["requests"],
            "list_usd": _GROQ_STATE["list_usd"]})
    except Exception as e:
        print(f"[groq] usage counter write failed: {e}",
              flush=True)


def _groq_requests_today():
    with _GROQ_LOCK:
        _groq_load_locked()
        return _GROQ_STATE["requests"]


def _groq_guard_tripped():
    """The day's Groq request count is at the soft cap (or
    the counter is unreadable): Groq sheds until tomorrow."""
    with _GROQ_LOCK:
        _groq_load_locked()
        if _GROQ_STATE["broken"]:
            return True
        return _GROQ_STATE["requests"] >= GROQ_DAILY_SOFT_CAP


def _groq_count(n):
    """Count n Groq requests against the day (v26 F13: the
    transport counts ATTEMPTS — a 5xx-retried logical call
    is two requests against the org quota, and through
    v25.1 the counter saw only one)."""
    with _GROQ_LOCK:
        _groq_load_locked()
        _GROQ_STATE["requests"] += int(n)
        _groq_persist_locked()
        return _GROQ_STATE["requests"]


def _groq_count_one():
    """Count one Groq request (kept for callers that make
    exactly one attempt)."""
    return _groq_count(1)


def _groq_note_cost(cost):
    """Add one completed Groq call's list-price value to the
    day's visibility total (provider_tier=free)."""
    if not cost or cost <= 0:
        return
    with _GROQ_LOCK:
        _groq_load_locked()
        _GROQ_STATE["list_usd"] += float(cost)
        _groq_persist_locked()


def _groq_list_usd_today():
    with _GROQ_LOCK:
        _groq_load_locked()
        return _GROQ_STATE["list_usd"]


def _groq_key():
    return _nim_key_for(GROQ_KEY_ENV, GROQ_KEY_FILE)


def _groq_available():
    """Groq may run: a key is configured and the daily guard
    has not shed it. Mirrors _or_available's shape so seat
    gating treats the pseudo-slot identically."""
    return _groq_key() is not None and not _groq_guard_tripped()


def _note_groq_shed(where):
    """Log a Groq shed in the ask record (once per ask): a
    chain skipped its Groq entry because the daily guard is
    past the soft cap."""
    ctx = _ask_ctx()
    with ctx.lock:
        if any(e.get("kind") == "groq_shed" for e in ctx.events):
            return
        ctx.events.append({
            "kind": "groq_shed", "where": where,
            "requests_today": _groq_requests_today(),
            "soft_cap": GROQ_DAILY_SOFT_CAP})


def _groq_health_block():
    """GET /health's groq block: key state, the day's request
    count against the soft cap, and the list-price value of
    the day's free-tier calls."""
    return {"configured": _groq_key() is not None,
            "hard_model": GROQ_HARD_MODEL,
            "simple_model": GROQ_SIMPLE_MODEL,
            "requests_today": _groq_requests_today(),
            "daily_soft_cap": GROQ_DAILY_SOFT_CAP,
            "guard_tripped": _groq_guard_tripped(),
            "list_usd_today": round(_groq_list_usd_today(), 6),
            "provider_tier": "free"}


# --- v23 (WS2): the Flash mid-ladder ration is DELETED ------
# The rationed Gemini 3.8 Flash rung (slot 6) leaves judging
# entirely in the judge switch: Flash now serves ONLY as the
# dispute arbitrator and the final-rung ruler, under its own
# FLASH_DAILY_CAP, exactly as those paths have always run.



# --- v20: the xAI credit ledger (grant mode only) --------------------
# Accrues ONLY when XAI_FREE_CREDITS is True (a future grant
# program). In the default paid mode xAI spend goes through
# the reservation gate into the real-money ledger and this
# file is never written; xai_credit_today reads 0.0.

def _xai_credit_load_locked():
    """(Caller holds _XAI_CREDIT_LOCK.)"""
    today = _ledger_today()
    if _XAI_CREDIT_STATE["loaded"] \
            and _XAI_CREDIT_STATE["date"] == today:
        return
    credit, broken = 0.0, False
    try:
        with open(_XAI_CREDIT_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            credit = float(st.get("credit") or 0.0)
    except FileNotFoundError:
        pass
    except Exception:
        broken = True
    _XAI_CREDIT_STATE.update({"date": today, "credit": credit,
                              "broken": broken, "loaded": True})


def _xai_credit_persist_locked():
    """(Caller holds _XAI_CREDIT_LOCK.)"""
    if _XAI_CREDIT_STATE["broken"]:
        return
    try:
        _atomic_write_json(_XAI_CREDIT_FILE, {
            "date": _XAI_CREDIT_STATE["date"],
            "credit": _XAI_CREDIT_STATE["credit"]})
    except Exception as e:
        print(f"[xai] credit ledger write failed: {e}",
              flush=True)


def _xai_credit_note(cost):
    if not cost or cost <= 0:
        return
    with _XAI_CREDIT_LOCK:
        _xai_credit_load_locked()
        _XAI_CREDIT_STATE["credit"] += float(cost)
        _xai_credit_persist_locked()


def _xai_credit_today():
    with _XAI_CREDIT_LOCK:
        _xai_credit_load_locked()
        return _XAI_CREDIT_STATE["credit"]


def _xai_key():
    return _nim_key_for(XAI_KEY_ENV, XAI_KEY_FILE)


def _xai_available():
    """xAI may run: a key is configured and — in the default
    paid mode — the shared real-money ceiling is untripped,
    exactly the OpenRouter seats' rule (xAI spend reserves
    against the same ledger). In grant mode the key alone
    gates the seat."""
    if _xai_key() is None:
        return False
    if XAI_FREE_CREDITS:
        return True
    return not _or_cap_tripped()


def _xai_health_block():
    """GET /health's xai block: key state and the accounting
    mode. In the default paid mode, today's xAI spend is
    inside the openrouter block's ledger figure (one shared
    real-money ledger); credit_today_usd accrues only in
    grant mode."""
    return {"configured": _xai_key() is not None,
            "hard_model": XAI_HARD_MODEL,
            "fast_model": XAI_FAST_MODEL,
            "free_credits_mode": bool(XAI_FREE_CREDITS),
            "accounting": ("grant-credit (visibility only)"
                           if XAI_FREE_CREDITS else
                           "real-money (shared ledger caps)"),
            "credit_today_usd": round(_xai_credit_today(), 6)}


# --- v20: the xAI + Groq transports -----------------------------------

def _xai_text(model, system, user, temperature=0, max_tokens=2048,
              timeout=60, deadline=None):
    """One xAI chat call. Default (paid) mode: the call
    reserves its provable maximum against the shared
    real-money ledger BEFORE it starts — a refusal means the
    daily ceiling (OPENROUTER_DAILY_CAP_USD, $10.00 since
    v21; corrected v26/F16 — older comments said $2.00)
    would be crossed and the seat behaves as unavailable,
    exactly like an OpenRouter call — and
    settles to the billed cost computed from the response's
    token usage, or at the full reserved maximum when the
    call fails or reports no usage (F11-1). Grant mode
    (XAI_FREE_CREDITS): no reservation; the call's list-price
    value lands in the credit ledger for visibility and
    never touches the real-money caps."""
    if not _xai_available():
        raise RuntimeError(
            "xai unavailable (no key or daily ceiling reached)")
    reservation = None
    if not XAI_FREE_CREDITS:
        reservation = _or_reserve(_max_cost_from(
            _XAI_PRICES, _XAI_PRICE_DEFAULT, model, system,
            user, max_tokens))
        if reservation is None:
            raise RuntimeError(
                "xai daily ceiling reached (reservation refused)")
    usage = {}
    _attempts = []
    try:
        content = _nim_text(
            XAI_BASE_URL, XAI_KEY_ENV, XAI_KEY_FILE, model,
            system, user, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            provider="xai", usage_out=usage, deadline=deadline,
            attempts_out=_attempts)
    except Exception:
        # Settle at the full reserved maximum (F11-1); a
        # grant-mode call records nothing on failure.
        _or_settle(reservation, None)
        raise
    actual = _usage_cost(_XAI_PRICES, _XAI_PRICE_DEFAULT,
                         model, usage)
    if XAI_FREE_CREDITS:
        _xai_credit_note(actual if actual is not None
                         else _max_cost_from(
                             _XAI_PRICES, _XAI_PRICE_DEFAULT,
                             model, system, user, max_tokens))
    elif len(_attempts) > 1:
        # v26 (F13 extension, plan §7): ONE reservation
        # covers this logical call, but the transport
        # retried — the first attempt may have consumed
        # tokens the successful attempt's usage never
        # reports, so settling at `actual` could undercount
        # the ledger by up to one max-cost attempt. Settle
        # at the full reserved maximum instead (the same
        # conservative direction as a total failure). The
        # alternative the plan allowed — accept and document
        # the bound — was not taken: the attempt count is
        # observable for free, so the ledger takes no
        # undercount it can avoid.
        _or_settle(reservation, None)
    else:
        _or_settle(reservation, actual)
    return content


def _groq_text(model, system, user, temperature=0, max_tokens=2048,
               timeout=60, deadline=None):
    """One Groq chat call on the free tier, under the daily
    guard: past the soft cap the call never starts and the
    caller falls over. The request is counted at attempt
    time; a completed call's list-price value is recorded
    for visibility (provider_tier=free) and never touches
    the real-money ledger."""
    if _groq_key() is None:
        raise RuntimeError("groq API key not configured "
                           "(groq_key.txt missing)")
    if _groq_guard_tripped():
        _note_groq_shed("transport")
        raise RuntimeError(
            "groq daily soft cap reached (guard shed)")
    usage = {}
    # v26 (F13): count ATTEMPTS, not logical calls — the
    # transport may retry once on 5xx and the org quota
    # counts both. The count lands in a finally so a failed
    # call's attempts count too (the quota saw them).
    _attempts = []
    try:
        content = _nim_text(
            GROQ_BASE_URL, GROQ_KEY_ENV, GROQ_KEY_FILE, model,
            system, user, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            provider="groq", usage_out=usage, deadline=deadline,
            attempts_out=_attempts)
    finally:
        if _attempts:
            _groq_count(len(_attempts))
    cost = _usage_cost(_GROQ_PRICES, _GROQ_PRICE_DEFAULT,
                       model, usage)
    if cost is None:
        cost = _max_cost_from(_GROQ_PRICES, _GROQ_PRICE_DEFAULT,
                              model, system, user, max_tokens)
    _groq_note_cost(cost)
    return content


def _nim_text(base_url, key_env, key_file, model, system, user,
              temperature=0, max_tokens=2048, timeout=60,
              provider="nim", extra=None, or_route=False,
              usage_out=None, deadline=None, attempts_out=None):
    """One chat call to a hosted OpenAI-compatible provider (the
    NIM layer's seats), over the keep-alive pool (v15 S3).
    Returns the reply text; raises RuntimeError when the seat
    cannot run (no key, 429, persistent 5xx, bad reply) so the
    caller degrades instead of failing. Politeness: ONE retry
    on 5xx, NO retry on 429. v17: `extra` merges additional
    payload fields (OpenRouter's usage include + provider
    routing); `or_route` records the response's reported cost
    into the OpenRouter spend ledger the moment it parses, so
    even a served-but-unusable reply counts against the daily
    cap. v18: or_route calls run under the reservation gate
    (see the ledger block): every HTTP attempt reserves its
    provable maximum first and settles it on every outcome —
    to the billed cost when the response reports one, to the
    full maximum otherwise."""
    key = _nim_key_for(key_env, key_file)
    if not key:
        raise RuntimeError(f"{provider} API key not configured "
                           f"({key_file} missing)")
    payload_obj = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra:
        payload_obj.update(extra)
    payload = json.dumps(payload_obj).encode()
    u = urlparse(base_url)
    host, path = u.netloc, u.path.rstrip("/") + "/chat/completions"
    last_err = None
    for attempt in (0, 1):
        # v21 (WS4a): the leg deadline. Through v20.2 the retry
        # below ran on ANY exception with the FULL timeout
        # again, so a 30s judge leg whose first attempt read-
        # timed-out at 30s retried into a second 30s and the
        # seat_fail landed at ~60s (v20.2 retest: 60.19s /
        # 60.16s) — the leg budget never bit on this transport.
        # Now the per-attempt socket timeout is clamped to the
        # time left before `deadline`, and no attempt (the
        # retry included) starts once the deadline has passed.
        _attempt_timeout = timeout
        if deadline is not None:
            _remaining = deadline - time.monotonic()
            if _remaining <= 0:
                raise RuntimeError(
                    f"{provider} leg deadline spent before "
                    f"attempt {attempt + 1} (timeout budget "
                    "exhausted; no further attempt started)")
            _attempt_timeout = min(timeout, _remaining)
        reservation = None
        if or_route:
            # v18 (§B hard stop): every OpenRouter HTTP attempt
            # takes its own reservation at the call's provable
            # maximum BEFORE it starts. A refusal means the
            # daily ceiling (OPENROUTER_DAILY_CAP_USD, $10.00
            # since v21; corrected v26/F16) would be crossed:
            # the call never starts and the seat behaves as
            # unavailable, exactly like a v17 cap trip.
            reservation = _or_reserve(
                _or_max_cost(model, system, user, max_tokens))
            if reservation is None:
                raise RuntimeError(
                    "openrouter daily ceiling reached "
                    "(reservation refused)")
        try:
            # v26 (F13): an HTTP attempt is being made; the
            # caller that accounts per attempt (Groq's quota
            # counter, the xAI settlement) reads the count.
            if attempts_out is not None:
                attempts_out.append(1)
            status, raw = _https_post(
                host, path, payload,
                {"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
                timeout=_attempt_timeout)
        except Exception as e:
            # The request was sent and never parsed (timeout,
            # transport failure): settle at the full reserved
            # maximum (F11-1); a retry reserves anew.
            _or_settle(reservation, None)
            last_err = e
            if attempt == 0:
                if deadline is not None \
                        and deadline - time.monotonic() <= 0:
                    raise RuntimeError(
                        f"{provider} unavailable: {e} (leg "
                        "deadline spent; retry suppressed)")
                continue
            raise RuntimeError(f"{provider} unavailable: {e}")
        if status == 429:
            _or_settle(reservation, None)
            raise RuntimeError(f"{provider} rate limited (HTTP 429)")
        if status in (500, 502, 503, 504):
            _or_settle(reservation, None)
            last_err = RuntimeError(f"{provider} HTTP {status}")
            if attempt == 0:
                continue
            raise last_err
        if status != 200:
            _or_settle(reservation, None)
            raise RuntimeError(f"{provider} HTTP {status}")
        try:
            data = json.loads(raw)
        except ValueError:
            _or_settle(reservation, None)
            raise RuntimeError(f"{provider} returned an unexpected "
                               "response")
        if or_route:
            # The response parsed: settle to the billed cost,
            # or at the full reserved maximum when the response
            # carries no usage.cost (pass 2 item F5). A
            # served-but-unusable reply still settles here, so
            # it counts against the ceiling.
            _reported = (data.get("usage") or {}).get("cost")
            try:
                _actual = float(_reported) \
                    if _reported is not None else None
            except (TypeError, ValueError):
                _actual = None
            _or_settle(reservation, _actual)
            reservation = None
        if usage_out is not None \
                and isinstance(data.get("usage"), dict):
            # v20: token-usage capture for the xAI/Groq cost
            # accounting (their APIs report tokens, not USD).
            usage_out.update(data["usage"])
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError,
                ValueError):
            raise RuntimeError(f"{provider} returned an unexpected "
                               "response")
        if not content:
            raise RuntimeError(f"{provider} returned an empty response")
        return content
    raise RuntimeError(f"{provider} unavailable after retry: {last_err}")






# --- v17.1: GPT-OSS citation dialect normalization ------------------
# GPT-OSS drafts cite in a dialect the citation parser does
# not read (v17 acceptance diagnosis): load-bearing sentences
# carrying these markers counted as uncited at the
# citations-at-birth gate, drew objections, and the redrafts
# repeated the dialect until the ladder exhausted. Two forms,
# harvested verbatim from the v17 pest-control job records —
# these are the only dialect forms the samples show:
#   [Excerpt N] with the word and the number separated by a
#       narrow no-break space (U+202F) inside square brackets
#       (attempt 1's shipped answer and its prompt's quoted
#       draft: 6 instances, adjacent markers chaining, e.g.
#       "[Excerpt\u202f10][Excerpt\u202f12]"), in the answer
#       always trailed by a zero-width space (U+200B).
#   【N】 fullwidth brackets (U+3010/U+3011) around the
#       number (attempt 2's judging feedback quoting draft
#       sentences: 8 instances).
# Both rewrite to the canonical [N]. The rewrite runs where
# remote drafts are born (the hosted ask wrappers: xai_ask /
# groq_ask / or_ask) so every consumer downstream — the
# citation gate, the judge, the capped assembly — sees one
# dialect. (v23.1 BUG-6 scrub: this comment named nim_ask,
# deleted with the NIM layer.) Canonical [n] markers match
# neither pattern and pass through byte-identical; unbracketed
# "Excerpt N" prose (the prompt's own excerpt labels) is not
# a citation and is never matched (the bracket is required).
_CITE_DIALECT_EXCERPT = re.compile(r"\[Excerpt\s+(\d+)\]\u200b?")
_CITE_DIALECT_FULLWIDTH = re.compile(r"【(\d+)】")


def _normalize_citations(text):
    """Rewrite the harvested GPT-OSS citation dialect forms
    (see the block above) into canonical [n] markers. The
    trailing zero-width space is consumed only when it
    directly follows a dialect marker (it is part of that
    marker's observed typography, and left in place it would
    sit between the canonical marker and the sentence-final
    punctuation the citation tail regex reads)."""
    text = _CITE_DIALECT_EXCERPT.sub(r"[\1]", text)
    return _CITE_DIALECT_FULLWIDTH.sub(r"[\1]", text)


def or_ask(model, question, hits, instructions="", timeout=None,
           deadline=None):
    """One OpenRouter draft generation (v17): nim_ask's exact
    prompt shape and debris strip on the paid primary seat.
    Returns (answer, prompt). Raises when the seat cannot run;
    _generate then fails over down the drafting ladder."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    content = _or_text(model, sys_prompt, user_msg,
                       temperature=0.3, max_tokens=2048,
                       timeout=timeout or OPENROUTER_DRAFTER_TIMEOUT,
                       deadline=deadline)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    # v17.1: normalize the GPT-OSS citation dialect (the v17
    # pest-control failure) before the pipeline reads the draft.
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("openrouter drafter returned an "
                           "empty response")
    return content, sys_prompt + "\n\n" + user_msg


def xai_ask(model, question, hits, instructions="", timeout=None,
            deadline=None):
    """One xAI draft generation (v20): or_ask's exact prompt
    shape and post-processing on the prepaid xAI seat (the
    hard chain's paid layer, behind Groq and the NIM bench).
    Returns (answer, prompt). Raises when the seat cannot
    run; _generate then fails over to the OpenRouter seat."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    content = _xai_text(model, sys_prompt, user_msg,
                        temperature=0.3, max_tokens=2048,
                        timeout=timeout or XAI_DRAFTER_TIMEOUT,
                        deadline=deadline)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("xai drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


def groq_ask(model, question, hits, instructions="", timeout=None,
             deadline=None):
    """One Groq draft generation (v20): or_ask's exact prompt
    shape and post-processing on the free-tier primary seat
    (hard: GPT-OSS-120B; simple: GPT-OSS-20B). Returns
    (answer, prompt). Raises when the seat cannot run (no
    key, guard shed, provider failure); _generate then fails
    over down the chain. GPT-OSS drafts get the same
    citation-dialect normalization as the OpenRouter path."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    content = _groq_text(model, sys_prompt, user_msg,
                         temperature=0.3, max_tokens=2048,
                         timeout=timeout or GROQ_DRAFTER_TIMEOUT,
                         deadline=deadline)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("groq drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


# --- v14: judging -------------------------------------------------------------
# Judges answer one question per claim: does the cited material
# actually say this? Verdicts: supported / contradicted /
# not_addressed. The PRIMARY judge (Gemini Lite; the local 14B when
# Gemini is down) extracts the claim list and gives its verdicts in
# the same call (plan section 4: extraction folded into the judgment
# call). The SECOND judge verdicts the SAME numbered list and may
# flag claims the extractor missed. Code, not a model, resolves the
# verdicts into pass/fail per claim.


def _judge_num_ctx(system, user, num_predict):
    """v14.1: size a judge call's context window to its prompt
    instead of always taking the answer-generation 8192: estimated
    prompt tokens (chars / 3.5, the usual rough ratio) plus the
    output budget plus margin, bucketed, floored at 2048 and never
    above the old 8192."""
    est = int((len(system or "") + len(user or "")) / 3.5) \
        + num_predict + 256
    for bucket in (2048, 3072, 4096, 6144, 8192):
        if est <= bucket:
            return bucket
    return 8192


def _ollama_chat_raw(model, system, user, timeout=300, num_predict=1536):
    """One bare Ollama chat call for judge work (temperature 0)."""
    # v26 (F8): the v14.1 special case that unloaded every
    # other resident model before a routed-14B judge call is
    # deleted with the route; no model gets that treatment
    # now (the 14B is not installed in any case).
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        # v15.1: keep_alive 0 — a fallback judge call unloads
        # the model again as soon as it returns.
        "stream": False, "think": False, "keep_alive": 0,
        "options": {"num_ctx": _judge_num_ctx(system, user,
                                              num_predict),
                    "num_predict": num_predict,
                    "temperature": 0},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    msg = data.get("message", {})
    text = (msg.get("content") or msg.get("thinking") or "").strip()
    if not text:
        raise RuntimeError("judge model returned an empty response")
    return text


def _gemini_text(api_model, system, user, deadline=None):
    """One Gemini call for judge work (temperature 0), through
    the same spacing + retry discipline as answers. deadline
    (v23.1 WS-A) clamps every attempt to the ask budget left
    and stops the retry loop at the edge."""
    payload = json.dumps({
        "contents": [{"parts": [{"text": system + "\n\n" + user}]}],
        "generationConfig": {"temperature": 0},
    }).encode()
    return _gemini_call(api_model, payload, deadline=deadline)


def _parse_judge_json(text):
    """Pull the first JSON object out of a judge reply (judges wrap
    JSON in prose or fences despite instructions). None on failure."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _norm_verdict(v):
    s = re.sub(r"[\s\-]+", "_", str(v or "").strip().lower())
    if s in ("supported", "support", "pass", "true"):
        return "supported"
    if s in ("contradicted", "contradiction", "refuted", "false"):
        return "contradicted"
    return "not_addressed"


_JUDGE_SYSTEM = (
    "You are a strict fact-checking judge for a benefits Q&A system. "
    "You do not grade style, tone, or completeness beyond the claims. "
    "For each claim you answer exactly one question: do the document "
    "excerpts, the verified-figures block, or the required-mechanism "
    "notes actually say this? A claim that goes beyond what the "
    "material states is not_addressed; a claim the material "
    "contradicts is contradicted. Reply with ONLY a JSON object, no "
    "prose, no code fences.")


# v15 (regression fix, the operator's manual test 2026-10-13): claims ABOUT
# the documents themselves ("the documents provide explicit advice
# on steps to take") are framing, not facts about the world. An
# extractor marked one load-bearing and its not_addressed verdict
# sank an otherwise answerable question to a full IDK. Meta claims
# are classified in code, never trusted to the extracting judge:
# they are forced non-load-bearing and excluded from the pass
# computation — they can be dropped or rewritten, never sink an
# answer.
_META_CLAIM_RX = re.compile(
    r"^\s*(the\s+)?(documents?|excerpts?|sources?|records?|material|"
    r"guidance)\s+(provide|provides|provided|state|states|say|says|"
    r"show|shows|cover|covers|indicate|indicates|describe|describes|"
    r"include|includes|contain|contains|offer|offers|give|gives|"
    r"address|addresses|discuss|discusses)\b", re.IGNORECASE)


def _is_meta_claim(text):
    return bool(_META_CLAIM_RX.match(text or ""))


def _judge_excerpts(hits, cap=3000):
    lines = []
    for i, h in enumerate(hits or ()):
        lines.append(f"[{i + 1}] {h.get('title', '')} "
                     f"({h.get('url', '')})\n"
                     f"{(h.get('excerpt') or '')[:cap]}")
    return "\n\n".join(lines)


def _primary_judgment(engine, question, hits, answer, mech_block,
                      deadline=None, local_model=None):
    """Extract the draft's claims AND verdict them in one call.
    engine is 'lite' (the judge: Gemini 3.5 Flash Lite),
    'backup' (Lite down: DeepSeek Chat V3.1 on OpenRouter — the
    caller enforces the once-per-ask rule), or 'local' (a
    local-only ask). deadline is the ask's WS-A budget edge:
    every leg's timeout clamps to what is left of it, and a
    spent budget fails the leg without starting it. Returns
    {available, claims: [{text, load_bearing, verdict, note}],
    model, error}. Only substantive factual claims are
    extracted: rules, figures, conclusions. Connective text,
    citations, restatements of the question, and the confidence
    line are not claims."""
    user = (
        f"Question: {question}\n\nDocument excerpts:\n"
        f"{_judge_excerpts(hits)}\n\n{mech_block}\n\n"
        f"Draft answer:\n{answer}\n\n"
        "Extract the draft's substantive factual claims (at most 14; "
        "merge duplicates; mark load_bearing=true for claims the "
        "answer's conclusion rests on). Claims about the documents "
        "or excerpts themselves (what they provide, cover, or say) "
        "are framing, never facts: always mark them "
        "load_bearing=false. For EACH claim give your "
        "verdict: supported, contradicted, or not_addressed, judging "
        "ONLY against the excerpts and blocks above, plus a one-line "
        "note. In sources, list the numbers of the excerpts (from "
        "the numbered list above) the claim rests on; use an empty "
        "list when it rests only on the verified-figures or "
        "mechanism blocks. Reply with ONLY this JSON:\n"
        '{"claims": [{"text": "...", "load_bearing": true, '
        '"verdict": "supported", "note": "...", '
        '"sources": [1]}]}')
    # v24 (§8): a seat pick names the local model explicitly;
    # without one the local engine defaults to the installed
    # 9B (v26 F8: the v23.2 default here was the retired
    # routed 14B).
    model = {"lite": GEMINI_CHAIN[0],
             "backup": JUDGE_BACKUP_MODEL}.get(
                 engine, local_model or LOCAL_MODEL)
    try:
        if engine == "lite":
            if _judge_leg_timeout(deadline, 45) <= 0:
                return {"available": False, "claims": [],
                        "model": model,
                        "error": "judge leg budget spent"}
            text = _gemini_text(GEMINI_CHAIN[0], _JUDGE_SYSTEM,
                                user, deadline=deadline)
        elif engine == "backup":
            _bt = _judge_leg_timeout(deadline, JUDGE_LEG_TIMEOUT_S)
            if _bt <= 0:
                return {"available": False, "claims": [],
                        "model": model,
                        "error": "judge leg budget spent"}
            text = _or_text(JUDGE_BACKUP_MODEL, _JUDGE_SYSTEM,
                            user, temperature=0, max_tokens=4000,
                            timeout=_bt, deadline=deadline)
        else:
            _lt = _judge_leg_timeout(deadline, 300)
            if _lt <= 0:
                return {"available": False, "claims": [],
                        "model": model,
                        "error": "judge leg budget spent"}
            text = _ollama_chat_raw(model, _JUDGE_SYSTEM,
                                    user, timeout=_lt)
        obj = _parse_judge_json(text)
        if not obj or not isinstance(obj.get("claims"), list):
            return {"available": False, "claims": [], "model": model,
                    "error": "judge reply was not usable JSON"}
        claims = []
        for c in obj["claims"][:14]:
            t = str(c.get("text") or "").strip()
            if not t:
                continue
            # v14.1: the excerpts this claim rests on (1-based in
            # the judge prompt, stored 0-based), used to scope the
            # second judge's and the arbitrator's evidence.
            srcs = []
            for s in c.get("sources") or []:
                try:
                    j = int(s) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= j < len(hits or ()) and j not in srcs:
                    srcs.append(j)
                if len(srcs) >= 4:
                    break
            claims.append({"text": t,
                           "load_bearing": bool(c.get("load_bearing"))
                           and not _is_meta_claim(t),
                           "verdict": _norm_verdict(c.get("verdict")),
                           "note": str(c.get("note") or "")[:200],
                           "sources": srcs})
        if not claims:
            return {"available": False, "claims": [], "model": model,
                    "error": "judge extracted no claims"}
        return {"available": True, "claims": claims,
                "model": model, "error": None}
    except Exception as e:
        return {"available": False, "claims": [], "model": model,
                "error": str(e)[:200]}


def _claim_evidence(hits, claim):
    """v14.1: the excerpt text ONE claim rests on: its mapped
    sources, or the full excerpt set when it has no mapping.
    v15 (item 1): refutation candidates retrieved for the claim
    ride along under their own heading, so every verdict-stage
    judge rules against the union of supporting and possibly
    contradicting material."""
    src = [j for j in (claim.get("sources") or [])
           if 0 <= j < len(hits or ())]
    use = src if src else list(range(len(hits or ())))
    text = "\n\n".join(
        f"[{j + 1}] {hits[j].get('title', '')} "
        f"({hits[j].get('url', '')})\n"
        f"{(hits[j].get('excerpt') or '')[:3000]}" for j in use)
    refs = claim.get("refutations") or ()
    if refs:
        text += ("\n\nPossibly contradicting material (retrieved "
                 "to test this claim; weigh it like the excerpts "
                 "above):\n" + "\n\n".join(
                     f"[R{k + 1}] {h.get('title', '')} "
                     f"({h.get('url', '')})\n"
                     f"{(h.get('excerpt') or '')[:3000]}"
                     for k, h in enumerate(refs)))
    hunt = claim.get("hunt") or ()
    if hunt:
        text += ("\n\nAdditional material retrieved for this "
                 "claim by targeted search (the original excerpts "
                 "did not settle it; weigh this like the excerpts "
                 "above):\n" + "\n\n".join(
                     f"[H{k + 1}] {h.get('title', '')} "
                     f"({h.get('url', '')})\n"
                     f"{(h.get('excerpt') or '')[:3000]}"
                     for k, h in enumerate(hunt)))
    return text


def _scoped_evidence(hits, claims, labels=None):
    """v14.1: per-claim evidence for judge prompts: each claim is
    followed by ONLY the excerpts the primary judgment mapped it
    to (see _claim_evidence). Returns None when there are no
    hits or NO claim carries a mapping, so callers keep the
    legacy full-set prompt. `labels` gives the number each claim
    is cited by (defaults to 1..n in list order)."""
    if not hits or not any(c.get("sources") for c in claims):
        return None
    labels = labels or [i + 1 for i in range(len(claims))]
    return "\n\n".join(
        f"Claim {lab}: {c['text']}\n{_claim_evidence(hits, c)}"
        for c, lab in zip(claims, labels))


def _second_verdicts(engine, question, hits, claims, mech_block,
                     deadline=None, local_model=None):
    """Verdicts for a numbered claim list from ONE judge engine
    ('lite', 'backup', or 'local'), for the claims the primary
    pass did not cover. The judge may also flag substantive
    claims in the draft the extractor missed (returned as extra
    claims, which then go through the same deterministic checks
    and finals as any other claim). v14.1: when the primary
    judgment mapped claims to their excerpts, the prompt carries
    per-claim scoped evidence instead of the whole set. deadline
    clamps every leg to the ask budget (v23.1 WS-A)."""
    verdict_instr = (
        "Give a verdict for EVERY numbered claim: supported, "
        "contradicted, or not_addressed, judging ONLY against the "
        "excerpts and blocks above, with a one-line note each. If "
        "the draft answer (which these claims were extracted from) "
        "makes another substantive factual claim that is NOT in the "
        "list, add it under \"missed\" with your verdict. Reply with "
        "ONLY this JSON:\n"
        '{"verdicts": [{"i": 1, "verdict": "supported", "note": "..."}], '
        '"missed": [{"text": "...", "load_bearing": false, '
        '"verdict": "not_addressed", "note": "..."}]}')
    scoped = _scoped_evidence(hits, claims)
    if scoped is not None:
        user = (
            f"Question: {question}\n\n{mech_block}\n\n"
            f"Claims to judge, each followed by the excerpts it "
            f"rests on:\n{scoped}\n\n" + verdict_instr)
    else:
        numbered = "\n".join(
            f"{i + 1}. {c['text']}" for i, c in enumerate(claims))
        user = (
            f"Question: {question}\n\nDocument excerpts:\n"
            f"{_judge_excerpts(hits)}\n\n{mech_block}\n\n"
            f"Claims to judge:\n{numbered}\n\n" + verdict_instr)
    try:
        if engine == "lite":
            if _judge_leg_timeout(deadline, 45) <= 0:
                raise RuntimeError("judge leg budget spent")
            text = _gemini_text(GEMINI_CHAIN[0], _JUDGE_SYSTEM,
                                user, deadline=deadline)
        elif engine == "backup":
            _bt = _judge_leg_timeout(deadline, JUDGE_LEG_TIMEOUT_S)
            if _bt <= 0:
                raise RuntimeError("judge leg budget spent")
            text = _or_text(JUDGE_BACKUP_MODEL, _JUDGE_SYSTEM,
                            user, temperature=0, max_tokens=4000,
                            timeout=_bt, deadline=deadline)
        else:
            _lt = _judge_leg_timeout(deadline, 300)
            if _lt <= 0:
                raise RuntimeError("judge leg budget spent")
            _note_local_load("local-verdicts")
            text = _ollama_chat_raw(local_model or LOCAL_MODEL,
                                    _JUDGE_SYSTEM,
                                    user, timeout=_lt,
                                    num_predict=1024)
        obj = _parse_judge_json(text)
        if not obj or not isinstance(obj.get("verdicts"), list):
            return {"available": False, "verdicts": {}, "missed": [],
                    "error": "judge reply was not usable JSON"}
        verdicts = {}
        for v in obj["verdicts"]:
            try:
                idx = int(v.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(claims):
                verdicts[idx] = {
                    "verdict": _norm_verdict(v.get("verdict")),
                    "note": str(v.get("note") or "")[:200]}
        missed = []
        for c in obj.get("missed") or []:
            t = str(c.get("text") or "").strip()
            if t:
                missed.append({"text": t,
                               "load_bearing": bool(c.get("load_bearing")),
                               "verdict": _norm_verdict(c.get("verdict")),
                               "note": str(c.get("note") or "")[:200]})
        return {"available": True, "verdicts": verdicts,
                "missed": missed[:4], "error": None}
    except Exception as e:
        return {"available": False, "verdicts": {}, "missed": [],
                "error": str(e)[:200]}


# --- v15: citations at birth (item 3) -----------------------------------------
# The drafter contract (appended to every drafter's instructions
# in _run_unit): every factual sentence ends with the number(s)
# of the excerpt(s) it rests on, [n] style, or [registry] for
# facts from the verified-figures / mechanism blocks. The parser
# below reads a draft back deterministically: a cited sentence
# becomes a claim whose evidence is exactly those excerpts; a
# factual sentence with NO citation is uncited, and an uncited
# LOAD-BEARING sentence is unverified by construction (it can
# never be argued into supported; it goes to the redraft
# objections verbatim and blocks shipping while it stands).
CITATION_CONTRACT = (
    "CITATIONS (checked by the server): end EVERY factual "
    "sentence with the number(s) of the excerpt(s) it rests on, "
    "in square brackets, like [2] or [2][5]. Facts taken from "
    "the VERIFIED FIGURES or REQUIRED MECHANISMS blocks above "
    "end with [registry] instead. A factual sentence with no "
    "citation is treated as unverified and will be sent back or "
    "cut. Headings, the confidence line, and purely connective "
    "sentences need no citation.")


def _split_sentences(text):
    """Sentence splitter for the citation parse: splits on
    sentence-final punctuation, but not on decimal points
    ($4,066.50) or common abbreviations (e.g., i.e., U.S.).
    v18 (F20a): line breaks and bullet markers are sentence
    boundaries too — through v17.3 an unpunctuated bullet list
    read as ONE merged sentence, so its per-bullet citations
    were invisible to the parse."""
    text = (text or "").strip()
    if not text:
        return []
    segments = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:[-•*]|\d+[.)])\s+", "", line)
        if line:
            segments.append(line)
    out = []
    for seg in segments:
        seg = re.sub(r"(\d)\.(\d)", r"\1<DEC>\2", seg)
        for ab in ("e.g", "i.e", "U.S", "Mr", "Mrs", "Dr", "vs", "No"):
            seg = seg.replace(ab + ".", ab + "<DOT>")
        for p in re.split(r"(?<=[.!?])\s+", seg):
            p = p.replace("<DEC>", ".").replace("<DOT>", ".").strip()
            if p:
                out.append(p)
    return out


_CITE_TAIL = re.compile(
    r"((?:\[\d+\]|\[registry\])[\s,]*)+[.!?]?\s*$", re.IGNORECASE)
_CITE_NUM = re.compile(r"\[(\d+)\]")
_LINK_RX = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")
_FACTUAL_RX = re.compile(
    r"(\$\s?\d|\b\d+\s?(?:months?|years?|weeks?|days?|percent|%)\b|"
    r"\b(?:must|shall|counts?|excluded|exclusion|eligible|limit|"
    r"qualif\w+|requires?|prohibit\w+|allows?|not counted|"
    r"does not count|is not|are not|will|can|cannot|can't|won't|"
    r"will not|is the|are the)\b|"
    # v26 (F7, D6 ruled 2026-10-15): the coverage-verb
    # family. Through v25.1 a plain statement of what a
    # program covers carried NO factual signal — "The
    # Health and Wellness Waiver provides pest control"
    # matched nothing in this pattern, so the uncited gate
    # never saw it and Quick shipped it verbatim
    # (Phase 0 probe F7). The list stays conservative:
    # program-fact verbs only (coverage, provision,
    # inclusion, payment-for), no general vocabulary.
    r"\b(?:covers?|covered|provides?|provided|includes?|"
    r"included)\b|\bpays?\s+for\b)", re.IGNORECASE)


# v30 (checker exemption a-iii): a META sentence talks
# about the answer's own sources or scope — sources
# disagreeing, excerpts not covering a point — rather
# than stating a program fact. It needs a subject marker
# (the sources / pages / excerpts / this answer) AND a
# predicate marker (disagreement or coverage framing) in
# the same sentence; a program fact that merely mentions
# a source ("the manual states the limit is $2,000")
# matches no predicate and stays fully gated.
_META_SUBJECT_RX = re.compile(
    r"\b(sources?|pages?|documents?|excerpts?|manual|"
    r"materials|this answer|the answer above|"
    r"retrieved (?:text|passages|excerpts))\b", re.I)
_META_PRED_RX = re.compile(
    r"\b(disagree\w*|differ\w*|conflict\w*|inconsistent|"
    r"not consistent|not cover\w*|do not cover|"
    r"does not cover|outside the scope|beyond the scope|"
    r"not addressed)\b", re.I)


def _is_meta_sentence(text):
    """True for scope/limits/conflict meta-sentences
    (exemption a-iii); deliberately narrow."""
    t = text or ""
    return bool(_META_SUBJECT_RX.search(t)
                and _META_PRED_RX.search(t))


def _parse_citations(answer, hits):
    """Read a draft's citations back (item 3). Returns
    {sentences: [{text, sources, cited, factual}], cited_claims:
    [{text, load_bearing, sources}], uncited_lb: [sentence texts]}.
    A sentence is cited when it ends with [n]/[registry] markers
    or carries a markdown link to one of the hits' URLs.
    Load-bearing heuristic for cited/uncited classification: the
    sentence states a figure, a duration, or a rule (the
    _FACTUAL_RX signals) — connective prose is neither cited
    nor factual and passes through untouched.
    v25.1: BLOCK attribution. Drafters place a citation block
    at the end of a sentence GROUP ("... (2026). [1][registry]"),
    and the sentence splitter strands those markers as their
    own segment or glues them onto the next sentence — through
    v25 every sentence in the group then read as uncited (the
    c3b7a02ec2c5 live Quick failure). A citation block now
    credits every sentence since the previous citation block,
    which is the span its markers were written to cover. A
    sentence after the LAST citation block gains nothing.
    v26 (F6, D6 ruled 2026-10-15): each sentence ALSO keeps
    its OWN-marker sources (own_sources: the markers written
    in the sentence itself, before block attribution).
    Citation credit, cited_claims and the uncited gate keep
    block attribution; _check_figures' scoped mode grounds
    figures against own_sources only, because block scope
    let a wrong figure in an early sentence ground against
    a later block's excerpt (Phase 0 probe F6)."""
    urls = {}
    for j, h in enumerate(hits or ()):
        if h.get("url"):
            # v18 (F20b): a URL maps to ALL chunks retrieved
            # from it (search keeps several per document);
            # through v17.3 the last chunk silently won.
            urls.setdefault(h["url"], []).append(j)
    rows = []
    for s in _split_sentences(answer):
        if s.lower().startswith("confidence:"):
            rows.append({"text": s, "sources": [],
                         "own_sources": [], "cited": True,
                         "factual": False, "_conf": True})
            continue
        srcs = []
        m = _CITE_TAIL.search(s)
        if m:
            for n in _CITE_NUM.findall(m.group(0)):
                j = int(n) - 1
                if 0 <= j < len(hits or ()) and j not in srcs:
                    srcs.append(j)
        # v18 (F20b): markers are read ANYWHERE in the
        # sentence, not only in the tail run.
        for n in _CITE_NUM.findall(s):
            j = int(n) - 1
            if 0 <= j < len(hits or ()) and j not in srcs:
                srcs.append(j)
        for url in _LINK_RX.findall(s):
            for j in urls.get(url, ()):
                if j not in srcs:
                    srcs.append(j)
        factual = bool(_FACTUAL_RX.search(s))
        cited = bool(srcs) or "[registry]" in s.lower()
        rows.append({"text": s, "sources": srcs,
                     "own_sources": list(srcs), "cited": cited,
                     "factual": factual, "_conf": False})
    # v25.1 block attribution: walk the rows; each row that
    # carries a citation flushes the pending run — the run's
    # rows gain that row's sources and its cited flag. The
    # confidence row is transparent: it neither receives
    # credit nor breaks a run.
    _run = []
    for _row in rows:
        if _row["_conf"]:
            continue
        _run.append(_row)
        if _row["cited"]:
            for _r in _run:
                for _j in _row["sources"]:
                    if _j not in _r["sources"]:
                        _r["sources"].append(_j)
                _r["cited"] = True
            _run = []
    sentences, cited_claims, uncited_lb = [], [], []
    for _row in rows:
        sentences.append({"text": _row["text"],
                          "sources": _row["sources"],
                          "own_sources":
                              _row.get("own_sources") or [],
                          "cited": _row["cited"],
                          "factual": _row["factual"],
                          "meta": _is_meta_sentence(
                              _row["text"])})
        if _row["factual"] and _row["cited"]:
            cited_claims.append({"text": _row["text"],
                                 "load_bearing": True,
                                 "sources": _row["sources"]})
        elif _row["factual"] and not _row["cited"] \
                and not _is_meta_sentence(_row["text"]):
            # v30 (exemption a-iii): meta-sentences are
            # exempt from the citation demand — they state
            # no program fact to cite. Every other factual
            # sentence is gated exactly as before.
            uncited_lb.append(_row["text"])
    return {"sentences": sentences, "cited_claims": cited_claims,
            "uncited_lb": uncited_lb}


# --- v17.3: strip, don't sink (operator ruling, 2026-10-14) --------------------
# In the v17.2 acceptance re-run every extracted claim passed
# every judge, yet the answer failed closed to IDK because ONE
# factual sentence in the final draft carried no citation: a
# parenthetical aside. The operator's ruling: after a draft's repair
# rungs are exhausted, a factual sentence that still fails
# citations at birth AND maps to NO extracted claim (an aside,
# not a claim-bearing sentence) is REMOVED from the draft
# instead of failing the unit; the stripped draft then
# re-enters the normal final disposition. Claim-bearing
# sentences are NEVER stripped (a sentence tied to an
# extracted claim that cannot be cited still fails closed
# exactly as before), the mechanism lead sentence is never
# stripped (load-bearing by construction, item 4), stripping
# never happens when it would leave no cited factual content,
# and every cut is recorded in the unit record's
# stripped_sentences so the honesty surface shows it.

_LIMITING_RX = re.compile(
    r"\b(?:only|unless|except|however|if|not|never|"
    r"no longer)\b", re.IGNORECASE)


def _sentence_maps_to_claim(sentence, claims):
    """Does this draft sentence carry one of the extracted
    claims? Tested by normalized-text containment, either way,
    in full or by the 40-character prefix (the same containment
    tests _assemble_capped_partial uses to tie sentences to
    claims). Token overlap is deliberately NOT a signal here:
    it measures topical relatedness, and the aside this ruling
    targets shares most of its vocabulary with the claim it
    parenthetically supports, so an overlap test would class
    the exact target sentence as claim-bearing and the strip
    could never fire."""
    ns = _norm_claim_text(sentence)
    if not ns:
        return False
    for c in claims or ():
        nc = _norm_claim_text(c.get("text") or "")
        if not nc:
            continue
        if ns in nc or nc in ns:
            return True
        if ns[:40] and nc[:40] and (ns[:40] in nc
                                    or nc[:40] in ns):
            return True
        # v18 (F7): a claim carrying a verbatim draft span
        # (citation-born claims do: the span IS the sentence)
        # also maps by span identity.
        span = _norm_claim_text(c.get("span") or "")
        if span and (ns in span or span in ns):
            return True
    return False


def _remove_sentence(text, sentence):
    """Remove ONE occurrence of a sentence from a draft,
    swallowing the whitespace that followed it (or, at the
    end of the text, the whitespace that preceded it) so the
    surviving text is touched nowhere else. Returns None when
    the sentence is not found verbatim."""
    i = text.find(sentence)
    if i < 0:
        return None
    j = i + len(sentence)
    k = j
    while k < len(text) and text[k] in " \t\r\n":
        k += 1
    if k > j:
        return text[:i] + text[k:]
    h = i
    while h > 0 and text[h - 1] in " \t\r\n":
        h -= 1
    return text[:h] + text[j:]


def _strip_uncited_asides(draft, parsed, claims, lead_sentence=None,
                          flagged=None):
    """The v17.3 strip. Candidates are the draft's uncited
    load-bearing sentences (parsed['uncited_lb']). A candidate
    is strippable only when it maps to no extracted claim and
    is not the mechanism lead sentence. Returns (new_draft,
    [removed sentence texts]) when at least one sentence was
    removed and the result verifies; (None, []) when nothing
    may be stripped (no candidates, no judged claims, every
    candidate claim-bearing or the lead, no cited factual
    content to leave behind, or the removal does not verify).
    Verification: the stripped draft's sentence list must be
    exactly the original list minus the removed sentences,
    byte-identical and in order; on any mismatch the strip is
    abandoned and the draft fails closed as before.
    v23.2 (BUG-11 F3): `flagged` is the set of sentences the
    deterministic checks or the judge actually flagged for
    THIS draft (the citation parser's uncited list, sentences
    carrying an ungrounded figure, sentences mapping to a
    claim whose final is not "supported"). When the caller
    supplies it, a candidate outside the flagged set is
    NEVER stripped: the strip may remove only what a check
    flagged, never unflagged content."""
    uncited = list(parsed.get("uncited_lb") or ())
    if not uncited or not claims:
        return None, []
    if not parsed.get("cited_claims"):
        return None, []
    eligible, seen = [], set()
    for s in uncited:
        if s in seen:
            continue
        seen.add(s)
        if flagged is not None and s not in flagged:
            continue  # v23.2 F3: unflagged sentences never strip
        if lead_sentence is not None and s == lead_sentence:
            continue
        # v18 (F7): a sentence carrying limiting language or a
        # figure/duration is never an aside, whatever it maps
        # to — stripping it could only widen the answer's
        # claims (the strip is for pure parentheticals).
        if _LIMITING_RX.search(s) or _has_figure(s) \
                or _has_duration(s):
            continue
        if _sentence_maps_to_claim(s, claims):
            continue
        eligible.append(s)
    if not eligible:
        return None, []
    new_draft = draft
    for s in eligible:
        new_draft = _remove_sentence(new_draft, s)
        if new_draft is None:
            return None, []
    expect = _split_sentences(draft)
    for s in eligible:
        if s not in expect:
            return None, []
        expect.remove(s)
    if _split_sentences(new_draft) != expect:
        return None, []
    return new_draft, eligible


def _norm_claim_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:300]


# --- v22.3 (fix 1): raw-form duplicate claims -----------------------
# The 2026-10-15 CSRA retest (ask 07d907a1fc52) shipped a fully
# correct draft to IDK: the primary extractor restated the
# draft's three figure bullets as normalized claims (all judged
# supported), while _parse_citations ALSO carried the same three
# bullets as extra claims in their raw form (bold labels, **
# emphasis, [1][registry] markers, a trailing clause the
# extractor dropped). _norm_claim_text equality could not see
# them as the same claims, so the raw copies sat as separate
# load-bearing claims, were never judged (every second-family
# leg died or was shed), and their not_addressed finals failed
# the round. The matcher below folds the two forms onto one
# token/figure space and merges ONLY on a conservative rule:
# identical figure multisets, the smaller side's content
# tokens >= 85% contained in the larger, overall token
# Jaccard >= 0.45, and at least 4 content tokens on the smaller
# side. When in doubt both claims are kept: a false merge
# (one verdict silently covering a distinct claim) is worse
# than a duplicate.
_CLAIM_FOLD = (
    (" ", " "), (" ", " "), ("‑", "-"),
    ("‘", "'"), ("’", "'"), ("“", '"'),
    ("”", '"'), ("–", "-"), ("—", "-"),
    ("½", " 1/2 "))
_CLAIM_NUM_RX = re.compile(r"\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+)?")
_CLAIM_WORD_RX = re.compile(r"[a-z0-9]+")
_CLAIM_LABEL_RX = re.compile(
    r"^([a-z][a-z0-9 /&'()-]{0,40}?):\s+")
_CLAIM_STOP = frozenset(
    "the a an of to in is are was were be been being and or "
    "but if then so as at on for with by from that this it its "
    "she he they we you your her his their our may can could "
    "will would shall should do does did have has had per than "
    "into over under about between who whom whose what when "
    "where which while during before after up down out off "
    "again further once here there all any both each few more "
    "most other some such only own same too very just".split())


def _claim_fold(text):
    t = text or ""
    for a, b in _CLAIM_FOLD:
        t = t.replace(a, b)
    return t.lower()


def _claim_numbers(text):
    """Every numeric value a claim states (money, counts,
    fractions), as a sorted list of floats. Citation markers
    are stripped first: a [1] is furniture, not a figure."""
    t = re.sub(r"\[\d+\]|\[registry\]", " ",
               _claim_fold(text))
    out = []
    for m in _CLAIM_NUM_RX.finditer(t):
        raw = m.group(0).replace(",", "").replace(" ", "")
        try:
            if "/" in raw:
                a, b = raw.split("/", 1)
                out.append(round(float(a) / float(b), 4))
            else:
                out.append(round(float(raw), 4))
        except ValueError:
            pass
    return sorted(out)


def _claim_tokens(text):
    """The claim's word tokens on the folded space: markdown
    links and citation markers dropped, emphasis treated as
    spacing, and a short digit-free lead-in label ('Minimum
    protection:') stripped — decoration is not content (the
    F4 normalizer's rule, extended to the raw bullet form)."""
    t = _claim_fold(text)
    t = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[\d+\]|\[registry\]", " ", t)
    for ch in "*_`#":
        t = t.replace(ch, " ")
    t = re.sub(r"\s+", " ", t).strip()
    m = _CLAIM_LABEL_RX.match(t)
    if m:
        label = m.group(1).strip()
        if len(label.split()) <= 5 and not any(
                ch.isdigit() for ch in label):
            t = t[m.end():]
    return _CLAIM_WORD_RX.findall(t.replace("/", " "))


def _claim_content(text):
    return {w for w in _claim_tokens(text)
            if w not in _CLAIM_STOP and not w.isdigit()}


def _claim_dupe_score(a_text, b_text):
    """Containment score (0..1) when the two texts state the
    SAME claim in normalized vs raw form, else None. The
    figure multisets must be identical (a claim about $32,532
    never merges with one about $33,000, and a two-figure
    conflict sentence never merges with a one-figure claim),
    and the smaller side needs >= 4 content tokens so very
    short claims only ever merge on exact normalized text."""
    if _claim_numbers(a_text) != _claim_numbers(b_text):
        return None
    ca, cb = _claim_content(a_text), _claim_content(b_text)
    if min(len(ca), len(cb)) < 4:
        return None
    containment = len(ca & cb) / min(len(ca), len(cb))
    if containment < 0.85:
        return None
    ta, tb = set(_claim_tokens(a_text)), set(_claim_tokens(b_text))
    jacc = len(ta & tb) / max(1, len(ta | tb))
    if jacc < 0.45:
        return None
    return containment


def _judge_leg_timeout(deadline, base):
    """One judge leg's timeout under the ask's deadline (v23.1
    WS-A): the leg's own bound, clamped to the budget left.
    0.0 when the budget is spent — the caller must not start
    the leg. None deadline (outside an ask) = the base bound."""
    if deadline is None:
        return float(base)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(float(base), remaining)


def _empty_checks_block():
    """The checks record's zero shape (rounds that never ran,
    early-IDK records): deterministic figure + citation
    outcomes, who judged, and the engaged registry entries
    with their validity windows (registry freshness is part
    of the record, per the v23.1 plan addendum)."""
    return {"figures": {"checked": 0, "failed": []},
            "citations": {"failed": []},
            "judge": {"served_by": None, "model": None,
                      "backup_used": False,
                      # v23.2: why the backup was not attempted
                      # when it was not (e.g. "budget spent");
                      # None when it ran or Lite served.
                      "backup_skip": None,
                      "verdicts": {"supported": 0,
                                   "contradicted": 0,
                                   "not_addressed": 0}},
            "registry": [], "registry_windows": {}}


def _set_check_line(text):
    """v23.1 (WS-F): the stepper's ONE fact-check line — who is
    checking the draft right now (or who served). Replaces the
    panel era's per-seat widgets (BUG-3: seat states the page
    rendered but nothing reliably wrote)."""
    rec = getattr(_STAGE_TLS, "job", None)
    if rec is not None:
        ph = rec.setdefault("phase", {"name": "retrieving",
                                      "round": None,
                                      "check": None})
        ph["check"] = text


# The per-claim deterministic figure check (v23.1 WS-B,
# addendum-hardened). Money figures only — counts, years, and
# durations are the quantity checker's and the judge's ground;
# the answer-level _check_figures provides the strict
# per-sentence scoping over the shipped text, and this check
# scopes each CLAIM to its own cited excerpts.
_CLAIM_MONEY_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_CLAIM_BARE_RE = re.compile(
    r"\b(\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars?\b|per month\b|"
    r"a month\b|monthly\b|per year\b|a year\b|annually\b|"
    r"per week\b|a week\b|weekly\b)")


def _claim_money(text):
    vals = set()
    for rx in (_CLAIM_MONEY_RE, _CLAIM_BARE_RE):
        for m in rx.finditer(text or ""):
            try:
                vals.add(round(float(
                    m.group(1).replace(",", "")), 2))
            except ValueError:
                pass
    return vals


def _det_claim_check(claim, hits, question, mech_block, admitted):
    """Deterministic checks for ONE claim. Returns
    (figure_failures, cited_ok).

    Figure scope: the claim's OWN cited excerpts (its sources
    indices), the verified-figures / mechanism block text
    (which carries the registry block), the question, and the
    registry values admitted for the question's date. A figure
    also passes when it is a derivation of scoped figures —
    half, double, sum, or difference (the addendum's
    derivation whitelist; a CSRA allowance is legitimately
    half the couple's assets), with one further chained step
    since v26.2 (fix 1: two-step post-eligibility math
    failed on its second step through v26.1). A claim with no
    mapped sources cannot be scoped tighter, so it scopes to
    every excerpt; the answer-level _check_figures still
    enforces the strict per-sentence rule on the shipped text.

    cited_ok: the claim maps to at least one cited excerpt, or
    rests on the registry ([registry] in its text), or is a
    meta claim about the documents, or (v26.2, fix 2) its
    figures all come from the question itself — a restatement
    of the asker's own stated facts is cited by the question.
    Figure extraction reads date-masked claim text (fix 3)."""
    text = claim.get("text") or ""
    figs = _claim_money(_mask_dates(text))
    srcs = [j for j in (claim.get("sources") or [])
            if isinstance(j, int)]
    if srcs:
        scope_texts = [hits[j].get("excerpt", "")
                       for j in srcs if 0 <= j < len(hits or ())]
    else:
        scope_texts = [h.get("excerpt", "") for h in (hits or ())]
    scope = set(admitted or ())
    for t in scope_texts:
        scope |= _claim_money(t)
    if mech_block:
        scope |= _claim_money(mech_block)
    scope |= _claim_money(question)
    allowed1 = _derivation_level1(scope)
    bad = [v for v in sorted(figs)
           if not _derivable(v, allowed1)]
    _q_figs = _claim_money(question)
    _premise = bool(figs) and all(
        any(abs(v - q) <= 0.011 for q in _q_figs) for v in figs)
    cited_ok = bool(srcs) or "[registry]" in text.lower() \
        or bool(claim.get("meta")) or _premise
    return bad, cited_ok


def _extract_claims_only(engine, question, hits, answer, mech_block,
                         deadline=None, local_model=None):
    """v24 (§8): the extraction half of the fused judge pass,
    for asks whose extract and judge seats differ — one fused
    call cannot serve two picks. Same claim rules as
    _primary_judgment, but no verdicts: the judge seat rules
    the extracted claims through _second_verdicts. engine is
    'lite' (the hosted chain head) or 'local' (the picked
    model). Returns the _primary_judgment shape minus
    verdicts."""
    user = (
        f"Question: {question}\n\nDocument excerpts:\n"
        f"{_judge_excerpts(hits)}\n\n{mech_block}\n\n"
        f"Draft answer:\n{answer}\n\n"
        "Extract the draft's substantive factual claims (at most 14; "
        "merge duplicates; mark load_bearing=true for claims the "
        "answer's conclusion rests on). Claims about the documents "
        "or excerpts themselves (what they provide, cover, or say) "
        "are framing, never facts: always mark them "
        "load_bearing=false. Do NOT verdict the claims. In sources, "
        "list the numbers of the excerpts (from the numbered list "
        "above) the claim rests on; use an empty list when it rests "
        "only on the verified-figures or mechanism blocks. Reply "
        "with ONLY this JSON:\n"
        '{"claims": [{"text": "...", "load_bearing": true, '
        '"sources": [1]}]}')
    model = GEMINI_CHAIN[0] if engine == "lite" \
        else (local_model or LOCAL_MODEL)
    try:
        if engine == "lite":
            if _judge_leg_timeout(deadline, 45) <= 0:
                return {"available": False, "claims": [],
                        "model": model,
                        "error": "judge leg budget spent"}
            text = _gemini_text(GEMINI_CHAIN[0], _JUDGE_SYSTEM,
                                user, deadline=deadline)
        else:
            _lt = _judge_leg_timeout(deadline, 300)
            if _lt <= 0:
                return {"available": False, "claims": [],
                        "model": model,
                        "error": "judge leg budget spent"}
            _note_local_load("local-extract")
            text = _ollama_chat_raw(model, _JUDGE_SYSTEM,
                                    user, timeout=_lt)
        obj = _parse_judge_json(text)
        if not obj or not isinstance(obj.get("claims"), list):
            return {"available": False, "claims": [], "model": model,
                    "error": "extraction reply was not usable JSON"}
        claims = []
        for c in obj["claims"][:14]:
            t = str(c.get("text") or "").strip()
            if not t:
                continue
            srcs = []
            for sv in c.get("sources") or []:
                try:
                    j = int(sv) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= j < len(hits or ()) and j not in srcs:
                    srcs.append(j)
                if len(srcs) >= 4:
                    break
            claims.append({
                "text": t,
                "load_bearing": bool(c.get("load_bearing"))
                and not _is_meta_claim(t),
                "sources": srcs})
        if not claims:
            return {"available": False, "claims": [], "model": model,
                    "error": "extraction found no claims"}
        return {"available": True, "claims": claims,
                "model": model, "error": None}
    except Exception as e:
        return {"available": False, "claims": [], "model": model,
                "error": str(e)[:200]}


_JUDGE_LABELS = {"lite": "Gemini Lite",
                 "backup": "DeepSeek V3.1 backup",
                 "local": "local model"}


def _check_round(question, hits, answer, hard, mech_block="",
                 corp=None, mechanisms=None, extra_claims=None,
                 local_only=False, use_cache=True, deadline=None,
                 judge_model=None, extract_model=None):
    """One checking round over a draft (v23.1 WS-B). ONE judge
    pass — Gemini 3.5 Flash Lite extracts the claims and
    verdicts them in a single call — with deterministic figure
    and citation checks UNDER the judge: a claim whose figures
    do not trace to its own cited excerpts (or the verified
    figures, or a simple derivation of them) is contradicted
    no matter what the judge said, and a claim with no citation
    and no judge verdict is not_addressed. When Lite cannot
    serve, the round falls back ONCE PER ASK to the DeepSeek
    V3.1 backup (the claim on the ask context is atomic); when
    no judge can serve, the round runs on the deterministic
    checks alone (mode "det") over whatever citation-derived
    claims exist. use_cache is accepted for signature
    compatibility with the retired panel round and ignored:
    the verdict cache died with the two-family rule that fed
    it. Returns {mode, claims, pass, errors, checks,
    judge_complete, refute_ms, flash_used} — refute_ms and
    flash_used are always 0/False now (record continuity for
    consumers written against the panel round's shape).
    mode: "judge" (Lite served), "judge_backup" (V3.1 served),
    "local" (local-only ask), "det" (no judge served), or
    "none" (no judge AND no claims to check)."""
    errors = []
    checks = _empty_checks_block()
    # v24 (§8): per-seat picks. judge_model / extract_model are
    # None for the hosted chain or a local model name (the
    # Models panel's allowlist, or the legacy local mapping).
    # The legacy local_only flag with no seat picks: both
    # halves of the fused pass run on the local engine's
    # default model — v26 (F8) names it explicitly as the
    # installed 9B (through v25.1 it was the retired routed
    # 14B, a model no longer installed).
    if judge_model is None and extract_model is None and local_only:
        judge_model = extract_model = LOCAL_MODEL
    checks["seats"] = {"extract": extract_model or "hosted",
                       "judge": judge_model or "hosted"}
    ctx = _ask_ctx()
    _set_phase("extracting")
    _set_check_line("Fact check: starting")
    # The engaged registry entries + their validity windows
    # ride the record (freshness), and their admitted values
    # scope the deterministic figure check.
    try:
        _matches = _registry_matches(question)
    except Exception:
        _matches = []
    checks["registry"] = [m.get("id") for m in _matches
                          if m.get("id")]
    checks["registry_windows"] = {
        m["id"]: [m.get("valid_from"), m.get("valid_to")]
        for m in _matches if m.get("id")
        and (m.get("valid_from") or m.get("valid_to"))}
    try:
        admitted = _registry_values(
            _matches, on_date=_question_date(question))
    except Exception:
        admitted = []

    def _spent():
        # v23.2 (BUG-11 F1): the round consults the ask's ONE
        # deadline — its argument when the caller threaded
        # one, else the ask clock — and a stage is spent when
        # too little budget remains to start another provider
        # call (MIN_CALL_START_S), not merely at the edge.
        _dl = deadline if deadline is not None \
            else _ask_deadline()
        return _dl is not None \
            and (_dl - time.monotonic()) < MIN_CALL_START_S

    def _ceiling():
        # v26 (F1): the per-ask cost ceiling is a leg-start
        # seam too. A leg that would start with the ask
        # already at the ceiling does not start, and the
        # round falls back to its deterministic disposition
        # exactly as it does for a budget-spent leg. (The
        # ceiling is consulted only at start seams: a leg
        # already running settles at its actual cost, so one
        # leg can still carry an ask past the line.)
        return _or_ask_cost() >= PER_ASK_CEILING_USD

    def _no_start():
        return _spent() or _ceiling()

    def _leg_skip():
        # Why a judge leg did not start, for the record.
        if _ceiling() and not _spent():
            return "per-ask cost ceiling reached"
        return "budget spent"

    def _leg_skip_error():
        if _leg_skip() == "budget spent":
            return ("judge leg budget spent "
                    "(round started past the deadline)")
        return ("judge leg not started "
                "(per-ask cost ceiling reached)")

    def _family_blocked(model):
        # v26 (F5, D3 = ENFORCE): §C's draft-family
        # exclusion, enforced at the verdict legs. The
        # drafting seat writes ctx.draft_family when a
        # draft serves; through v25.1 nothing read it and
        # the exclusion was dead code, so a seat assignment
        # could put the drafting family on its own verdict
        # and the verdict counted. A blocked leg never
        # starts; the round degrades exactly as it does for
        # a failed judge transport, never an assumed pass.
        return bool(model) and bool(ctx.draft_family) \
            and _model_family(model) == ctx.draft_family

    def _refused_leg(model):
        return {"available": False, "claims": [],
                "model": model,
                "error": "verdict leg refused: the drafting "
                         "family (" + str(ctx.draft_family)
                         + ") never judges its own draft"}

    def _claim_backup():
        """Atomically claim this ask's single backup-judge use."""
        with ctx.lock:
            if ctx.judge_backup_used:
                return False
            ctx.judge_backup_used = True
            return True

    # --- Stage 1: extraction + verdicts, one judge pass. ----
    engine = None
    served_model = None
    backup_used = False
    engines_used = []
    primary = {"available": False, "claims": [],
               "model": None, "error": "not run"}
    if extract_model is not None and extract_model == judge_model:
        _note_local_load("local-ask-judge")
        _set_check_line("Fact check: local model")
        if _family_blocked(judge_model):
            primary = _refused_leg(judge_model)
        elif not _no_start():
            primary = _primary_judgment(
                "local", question, hits, answer, mech_block,
                deadline=deadline, local_model=judge_model)
        elif _spent():
            _note_budget_cut("extracting")
        if primary["available"]:
            engine = "local"
            served_model = primary.get("model") or LOCAL_MODEL
            engines_used.append("local")
        else:
            _perr = str(primary.get("error"))
            if _perr == "not run" and _no_start():
                _perr = _leg_skip_error()
            errors.append("local judge: " + _perr)
    elif extract_model is None and judge_model is None:
        _set_check_line("Fact check: Gemini Lite")
        if _family_blocked(GEMINI_CHAIN[0]):
            primary = _refused_leg(GEMINI_CHAIN[0])
        elif not _no_start():
            primary = _primary_judgment("lite", question, hits,
                                        answer, mech_block,
                                        deadline=deadline)
        elif _spent():
            _note_budget_cut("extracting")
        if primary["available"]:
            engine = "lite"
            served_model = primary.get("model") or GEMINI_CHAIN[0]
            engines_used.append("lite")
        else:
            if primary.get("error"):
                # v23.2 (ABLE ask 87f5d6c5db39): a round that
                # started SPENT keeps the placeholder error
                # "not run" — recorded verbatim in v23.1 as
                # "lite judge: not run", which reads as a Lite
                # failure when in fact no leg was ever tried.
                # Name the real reason.
                _perr = str(primary["error"])
                if _perr == "not run" and _no_start():
                    _perr = _leg_skip_error()
                errors.append("lite judge: " + _perr)
            # v23.2 (ABLE ask): the v23.1 condition
            # `_claim_backup() and not _spent()` evaluated the
            # CLAIM first — a spent round burned the ask's
            # single backup use without attempting it, and
            # nothing recorded why the backup never engaged
            # (backup_used false, no error, no skip reason).
            # Now: spent rounds record the skip explicitly and
            # never claim the backup; a live round claims it
            # only when the attempt will actually run.
            if _no_start():
                if _spent():
                    _note_budget_cut("extracting")
                checks["judge"]["backup_skip"] = _leg_skip()
                errors.append("backup judge: not attempted ("
                              + _leg_skip() + ")")
            elif _family_blocked(JUDGE_BACKUP_MODEL):
                checks["judge"]["backup_skip"] = \
                    "drafting family"
                errors.append("backup judge: not attempted "
                              "(drafting family)")
            elif _claim_backup():
                backup_used = True
                _set_check_line(
                    "Fact check: backup judge (DeepSeek V3.1)")
                primary = _primary_judgment(
                    "backup", question, hits, answer, mech_block,
                    deadline=deadline)
                if primary["available"]:
                    engine = "backup"
                    served_model = JUDGE_BACKUP_MODEL
                    engines_used.append("backup")
                else:
                    errors.append("backup judge: "
                                  + str(primary.get("error")))
    else:
        # v24 (§8): the seats differ, so the fused one-call
        # pass cannot serve both picks. Extraction runs on the
        # extract seat; verdicts run on the judge seat through
        # the verdict transport; the two merge into the same
        # `primary` shape the rest of the round consumes.
        # Hosted halves keep their chains (a hosted verdict
        # gets the once-per-ask V3.1 backup behind Lite).
        _x_eng = "local" if extract_model else "lite"
        _j_eng = "local" if judge_model else "lite"
        _set_check_line("Fact check: seats ("
                        + (extract_model or "hosted")
                        + " / " + (judge_model or "hosted") + ")")
        if not _no_start():
            _ext = _extract_claims_only(
                _x_eng, question, hits, answer, mech_block,
                deadline=deadline, local_model=extract_model)
        else:
            if _spent():
                _note_budget_cut("extracting")
            _ext = {"available": False, "claims": [],
                    "model": extract_model or GEMINI_CHAIN[0],
                    "error": _leg_skip_error()}
        checks["extract"] = {"seat": _x_eng,
                             "model": _ext.get("model")}
        if not _ext["available"]:
            errors.append("extract: " + str(_ext.get("error")))
            primary = {"available": False, "claims": [],
                       "model": None, "error": _ext.get("error")}
        else:
            _fake = [{"text": c["text"]} for c in _ext["claims"]]
            _vres = {"available": False, "verdicts": {},
                     "missed": [], "error": "not run"}
            _j_model = judge_model if _j_eng == "local" \
                else (GEMINI_CHAIN[0] if _j_eng == "lite"
                      else JUDGE_BACKUP_MODEL)
            if _family_blocked(_j_model):
                _vres = {"available": False, "verdicts": {},
                         "missed": [],
                         "error": "verdict leg refused: the "
                                  "drafting family ("
                                  + str(ctx.draft_family)
                                  + ") never judges its own "
                                  "draft"}
            elif not _no_start():
                _vres = _second_verdicts(
                    _j_eng, question, hits, _fake, mech_block,
                    deadline=deadline, local_model=judge_model)
            if not _vres["available"] and _j_eng == "lite" \
                    and not _no_start() \
                    and not _family_blocked(JUDGE_BACKUP_MODEL) \
                    and _claim_backup():
                backup_used = True
                _j_eng = "backup"
                _set_check_line(
                    "Fact check: backup judge (DeepSeek V3.1)")
                _vres = _second_verdicts(
                    "backup", question, hits, _fake, mech_block,
                    deadline=deadline)
            _merged = []
            for _i, _c in enumerate(_ext["claims"]):
                _v = _vres["verdicts"].get(_i) or {}
                _merged.append({
                    "text": _c["text"],
                    "load_bearing": _c["load_bearing"],
                    "verdict": _v.get("verdict"),
                    "note": _v.get("note", ""),
                    "sources": _c.get("sources")})
            if _vres["available"]:
                engine = _j_eng
                served_model = judge_model or {
                    "lite": GEMINI_CHAIN[0],
                    "backup": JUDGE_BACKUP_MODEL}[_j_eng]
                engines_used.append(_j_eng)
                primary = {"available": True, "claims": _merged,
                           "model": served_model, "error": None}
            else:
                # The verdict seat could not serve: the
                # extracted claims ride the deterministic
                # checks alone (the det path), exactly as
                # citation-derived claims do when no judge
                # serves.
                errors.append("verdicts: "
                              + str(_vres.get("error")))
                primary = {"available": True, "claims": _merged,
                           "model": None,
                           "error": _vres.get("error")}
    if engine is None and not extra_claims \
            and not primary.get("claims"):
        # No judge could serve and there are no citation-derived
        # claims to check deterministically: nothing can be
        # verified this round.
        _set_check_line("Fact check: unavailable")
        return {"mode": "none", "claims": [], "pass": False,
                "errors": errors, "checks": checks,
                "judge_complete": False, "refute_ms": 0,
                "flash_used": False}
    if served_model:
        ctx.served_models["judge"] = served_model

    # --- Claim assembly (ported from the panel round): the
    # primary's claims, the v22.3 raw-form dedupe merge for
    # citation-derived extras, then registry-carrying claims
    # first (v22.3 fix 2) so a round that degrades mid-way has
    # already covered the server's own verified figures.
    def _new_claim(text, load_bearing, origin, sources):
        return {"text": text, "load_bearing": bool(load_bearing),
                "lite": None, "backup": None, "local": None,
                "judge": None, "judge_model": None,
                "final": None, "notes": [], "origin": origin,
                "meta": _is_meta_claim(text),
                "sources": list(sources or []),
                "refutations": [],
                "det_figure_fail": [], "det_cited": True,
                "coverage": [], "models": {}}

    claims = []
    if primary.get("claims"):
        claims = [_new_claim(c["text"], c["load_bearing"],
                             "extracted", c.get("sources"))
                  for c in primary["claims"]]
        for i, c in enumerate(primary["claims"]):
            if engine is not None and c.get("verdict") is not None:
                claims[i][engine] = c["verdict"]
                claims[i]["judge"] = c["verdict"]
                claims[i]["judge_model"] = served_model
            if c.get("note"):
                claims[i]["notes"].append(c["note"])
    seen_norm = {_norm_claim_text(c["text"]) for c in claims}
    for ec in extra_claims or ():
        if _norm_claim_text(ec["text"]) in seen_norm:
            continue
        # v22.3 (fix 1): a raw-form duplicate of a claim
        # already on the list (the same draft sentence in its
        # citation-carrying form) merges into that claim
        # instead of sitting beside it as a second claim with
        # no verdict path.
        _survivor, _best = None, 0.0
        for _c in claims:
            if _c.get("meta"):
                continue
            _s = _claim_dupe_score(_c["text"], ec["text"])
            if _s is not None and _s > _best:
                _survivor, _best = _c, _s
        if _survivor is not None:
            for _j in ec.get("sources") or ():
                if _j not in _survivor["sources"]:
                    _survivor["sources"].append(_j)
            if ec.get("load_bearing"):
                _survivor["load_bearing"] = True
            _survivor["notes"].append(
                "v22.3 dedupe: raw-form duplicate merged "
                "into this claim")
            continue
        seen_norm.add(_norm_claim_text(ec["text"]))
        claims.append(_new_claim(ec["text"],
                                 ec.get("load_bearing"),
                                 ec.get("origin", "extra"),
                                 ec.get("sources")))

    def _registry_priority(c):
        if "[registry]" in (c.get("text") or "").lower():
            return 0
        for _f in _claim_numbers(c.get("text")):
            if any(abs(_f - _v) <= 0.011 for _v in admitted):
                return 0
        return 1

    claims = sorted(claims, key=_registry_priority)

    # --- Stage 2: the deterministic checks, under the judge.
    _set_phase("checking")
    for c in claims:
        bad, cited_ok = _det_claim_check(
            c, hits, question, mech_block, admitted)
        c["det_figure_fail"] = bad
        c["det_cited"] = cited_ok
        if _claim_money(c["text"]):
            checks["figures"]["checked"] += 1
        if bad:
            checks["figures"]["failed"].append(
                {"claim": c["text"][:160], "figures": bad})

    # --- Stage 3: verdicts for the claims the primary pass
    # did not cover (citation-derived extras, and any claims a
    # verdict call flags as missed). Batched: at most two
    # verdict calls per round — the anti-413 rule learned from
    # the panel era's Groq failures. When the serving engine
    # fails mid-round, the ask's one backup use (if unclaimed)
    # covers the verdict stage instead.
    targets = [c for c in claims if c["judge"] is None
               and not c.get("meta")]
    if engine is not None and targets and not _no_start():
        verd_engine = engine
        batches = [targets[i:i + 14]
                   for i in range(0, len(targets), 14)][:2]
        for batch in batches:
            if _no_start():
                break
            res = _second_verdicts(verd_engine, question, hits,
                                   batch, mech_block,
                                   deadline=deadline,
                                   local_model=judge_model)
            if not res["available"] and verd_engine == "lite" \
                    and not local_only and not _ceiling() \
                    and not _family_blocked(JUDGE_BACKUP_MODEL) \
                    and _claim_backup():
                backup_used = True
                if "backup" not in engines_used:
                    engines_used.append("backup")
                _set_check_line(
                    "Fact check: backup judge (DeepSeek V3.1)")
                verd_engine = "backup"
                res = _second_verdicts(
                    "backup", question, hits, batch, mech_block,
                    deadline=deadline)
            if not res["available"]:
                errors.append("verdicts: "
                              + str(res.get("error")))
                break
            vmodel = {"lite": GEMINI_CHAIN[0],
                      "backup": JUDGE_BACKUP_MODEL}.get(
                          verd_engine) or served_model
            for sub_idx, v in res["verdicts"].items():
                c = batch[sub_idx]
                c[verd_engine] = v["verdict"]
                c["judge"] = v["verdict"]
                c["judge_model"] = vmodel
                if v["note"]:
                    c["notes"].append(v["note"])
            known = {_norm_claim_text(c["text"]) for c in claims}
            for m in res["missed"]:
                if _norm_claim_text(m["text"]) in known:
                    continue
                known.add(_norm_claim_text(m["text"]))
                nc = _new_claim(m["text"], m["load_bearing"],
                                "missed", [])
                nc[verd_engine] = m["verdict"]
                nc["judge"] = m["verdict"]
                nc["judge_model"] = vmodel
                if m["note"]:
                    nc["notes"].append(m["note"])
                bad, cited_ok = _det_claim_check(
                    nc, hits, question, mech_block, admitted)
                nc["det_figure_fail"] = bad
                nc["det_cited"] = cited_ok
                if bad:
                    checks["figures"]["failed"].append(
                        {"claim": nc["text"][:160],
                         "figures": bad})
                claims.append(nc)

    # --- Stage 4: finals. Deterministic failures are final;
    # the judge rules the rest; a claim nobody ruled on ships
    # only when the deterministic checks pass it.
    for c in claims:
        if c["det_figure_fail"]:
            c["final"] = "contradicted"
            c["notes"].append(
                "deterministic figure check: figure(s) "
                + ", ".join(f"${v:,.2f}" if v % 1
                            else f"${v:,.0f}"
                            for v in c["det_figure_fail"])
                + " do not trace to this claim's cited "
                  "excerpts, the verified figures, or a simple "
                  "derivation of them")
        elif c["judge"] is not None:
            c["final"] = c["judge"]
        elif not c["det_cited"] and not c.get("meta"):
            c["final"] = "not_addressed"
            c["notes"].append(
                "deterministic citation check: no cited "
                "excerpt or registry source, and no judge "
                "verdict")
            checks["citations"]["failed"].append(c["text"][:160])
        else:
            c["final"] = "supported"
        if c["judge_model"]:
            c["models"] = {"judge": c["judge_model"]}
        if c["final"] == "supported" \
                and c["judge"] == "supported":
            c["coverage"] = [_model_family(c["judge_model"])]
        elif c["final"] == "supported":
            c["coverage"] = ["deterministic"]
        if c["judge"] is not None:
            checks["judge"]["verdicts"][c["judge"]] = \
                checks["judge"]["verdicts"].get(c["judge"], 0) + 1
    substantive = [c for c in claims if not c.get("meta")]
    judge_complete = bool(engine) and all(
        c["judge"] is not None for c in substantive)
    passed = bool(substantive) and all(
        c["final"] == "supported" for c in substantive)
    mode = {"lite": "judge", "backup": "judge_backup",
            "local": "local"}.get(engine, "det")
    label = " + ".join(_JUDGE_LABELS[e] for e in engines_used) \
        if engines_used else None
    checks["judge"]["served_by"] = label
    checks["judge"]["model"] = served_model
    checks["judge"]["backup_used"] = backup_used
    _set_check_line("Fact check: " + (label or
                                      "deterministic checks only"))
    return {"mode": mode, "claims": claims, "pass": passed,
            "errors": errors, "checks": checks,
            "judge_complete": judge_complete, "refute_ms": 0,
            "flash_used": False}


def _checks_annotation(jr):
    """The answer page's verification line (v23.1): which
    checks ran and who judged, from the round's checks record.
    Replaces _panel_annotation (the panel is gone)."""
    chk = (jr or {}).get("checks") or {}
    j = chk.get("judge") or {}
    # v24 (§7 item 9): the page's shorter plain forms. Who
    # served still rides the record (checks.judge.served_by);
    # the note says what ran.
    if (jr or {}).get("mode") == "local":
        return "Checked by figures, citations, and the local judge."
    if j.get("served_by"):
        return "Checked by figures, citations, and the " \
            + j["served_by"] + " judge."
    return "Checked by figures and citations only."


# --- v14: the escalation ladder ----------------------------------------------
# Per answering unit (the whole question, or one part of a bundled
# question): draft, then up to TWO redrafts with the checking
# round's objections attached (operator decision, 2026-10-13),
# then the supported-core assembly (Partial) or IDK. Code
# gates (figures, mechanisms, arithmetic) run alongside the
# judge: a draft ships only when every gate passes. (v23.1:
# the panel era's final Flash ruling is deleted with the
# panel; the ladder's end state is assembly-or-IDK.)


def _generate(engine, model, question, hits, instructions):
    """One draft generation with v13's fallback honesty. Returns
    (answer, prompt, served_label, downgraded, fallback)."""
    if engine == "gemini":
        # v23.2 (F1): the Gemini drafting path consults the
        # ask's one deadline like every other drafting leg.
        answer, prompt, gserved, dg = gemini_ask(
            model, question, hits, instructions=instructions,
            deadline=_ask_ctx().unit_deadline)
        label = gserved + " (API)"
        if dg:
            label += " (downgraded: best Gemini unavailable)"
        return answer, prompt, label, dg, False
    if engine in ("nim", "nimfast"):
        # v23.1 (WS-A roster): the drafting ladder is TWO
        # hosted tiers + the caller's local fallback. Tier 1:
        # xAI Grok 4.7 drafts EVERYTHING (hard and simple) —
        # the v23 first live test showed the free Groq primary
        # 429/413-ing under its own organization token cap
        # while the paid Grok draft was the leg that worked.
        # Tier 2 (free): Groq GPT-OSS 120B. Tier 3 (local
        # qwen3.5:9b / the routed local model) is the caller's
        # genuine-unavailability fallback in _try_generate,
        # reached only when this raises. The v23 OpenRouter
        # drafting tier is DELETED (OpenRouter now serves only
        # the backup judge). (The engine names "nim" /
        # "nimfast" are v15-era labels for this hosted ladder,
        # kept so callers and records stay comparable; no NIM
        # model is in it.) The model argument is the nominal
        # pick and is ignored: the ladder owns model choice.
        # Seat-style events record every call, and every leg's
        # timeout clamps to the unit's remaining budget via
        # _leg_timeout (WS-A).
        job = "hard_draft" if engine == "nim" else "simple_draft"
        _xt = _leg_timeout(XAI_DRAFTER_TIMEOUT)
        if _xai_available() and _xt > 0:
            _xt0 = time.monotonic()
            try:
                answer, prompt = xai_ask(
                    XAI_HARD_MODEL, question, hits,
                    instructions=instructions, timeout=_xt,
                    deadline=_xt0 + _xt)
                _note_seat(job, XAI_HARD_MODEL, False)
                return (answer, prompt,
                        XAI_HARD_MODEL + " (xAI API)",
                        False, False)
            except Exception as e:
                # The primary's failure lands in the ask
                # record, not only the server log.
                _xlat = time.monotonic() - _xt0
                _note_seat_fail(
                    job, XAI_HARD_MODEL, 4, _xlat,
                    _xlat >= XAI_DRAFTER_TIMEOUT * 0.9
                    or "timed out" in str(e)
                    or "timeout" in str(e).lower(),
                    f"{type(e).__name__}: {e}")
                print(f"[xai] {job} primary failed: "
                      f"{str(e)[:150]}; the chain falls over",
                      flush=True)
        _gt = _leg_timeout(GROQ_DRAFTER_TIMEOUT)
        if _groq_available() and _gt > 0:
            _gt0 = time.monotonic()
            try:
                answer, prompt = groq_ask(
                    GROQ_HARD_MODEL,
                    question, hits, instructions=instructions,
                    timeout=_gt, deadline=_gt0 + _gt)
                _note_seat(job, GROQ_HARD_MODEL, True)
                return (answer, prompt,
                        GROQ_HARD_MODEL + " (Groq API)",
                        False, False)
            except Exception as e:
                _glat = time.monotonic() - _gt0
                _note_seat_fail(
                    job, GROQ_HARD_MODEL, 5, _glat,
                    _glat >= GROQ_DRAFTER_TIMEOUT * 0.9
                    or "timed out" in str(e)
                    or "timeout" in str(e).lower(),
                    f"{type(e).__name__}: {e}")
                print(f"[groq] {job} backup failed: "
                      f"{str(e)[:150]}; the chain falls over",
                      flush=True)
        elif _groq_key() is not None and _groq_guard_tripped():
            _note_groq_shed(job)
        _note_seat_down(
            job, "every drafting tier down")
        raise RuntimeError(
            f"{job}: every drafting tier down")
    # v23.2 (F1): the local drafting branch consults the ask
    # clock too — its timeout clamps to the unit's remaining
    # budget via _leg_timeout, and it does not start when the
    # budget cannot cover a bounded attempt.
    _ot = _leg_timeout(300)
    if _ot < MIN_CALL_START_S:
        _note_budget_cut()
        raise RuntimeError(
            "local drafting leg budget spent (ask deadline)")
    try:
        answer, prompt = ollama_ask(model, question, hits,
                                    instructions=instructions,
                                    timeout=_ot)
        return answer, prompt, model, False, False
    except Exception:
        if model == LOCAL_MODEL:
            raise
        _note_local_load("local-model-fallback")
        _ot2 = _leg_timeout(300)
        if _ot2 < MIN_CALL_START_S:
            _note_budget_cut()
            raise RuntimeError(
                "local drafting fallback budget spent "
                "(ask deadline)")
        answer, prompt = ollama_ask(LOCAL_MODEL, question, hits,
                                    instructions=instructions,
                                    timeout=_ot2)
        return (answer, prompt,
                LOCAL_MODEL + f" (fallback: routed {model} unavailable)",
                False, True)


# --- v22.1: draft shape guard --------------------------------------
# The local fallback drafter receives the redraft instructions
# verbatim, CORRECTIONS REQUIRED block included, but never the
# previous draft itself (the draft user message is question +
# excerpts only). A weak local model can answer the MEMO instead
# of the question: meta text about corrections and citations
# that then sits in draft_text while the gates burn the unit's
# remaining budget judging a non-answer (2026-10-14 CSRA live
# diagnosis: the local fallback drafted a corrections-and-
# citations memo about MMMNA for a CSRA question, and the unit
# IDK'd at the time gate). The guard is deliberately narrow: a
# draft is memo-shaped only when it carries NO citation marker
# and at least two meta markers, or when its first line is a
# corrections heading. A real answer is never rejected for
# merely being uncited (the citation gate owns that failure).
_MEMO_DRAFT_MARKERS = (
    "correction", "previous draft", "this draft", "the draft",
    "citation", "citations", "uncited", "revised draft",
    "here are the changes")


def _is_memo_shaped_draft(text):
    """True when a draft is meta text ABOUT an answer (a
    corrections / citations memo) rather than an answer."""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    first_line = low.split("\n", 1)[0]
    if "correction" in first_line:
        return True
    if re.search(r"\[\d+\]|\[registry\]", t):
        return False
    markers = sum(1 for m in _MEMO_DRAFT_MARKERS if m in low)
    return markers >= 2


def _build_objections(jr, mech_failures, ungrounded, uncited=(),
                      quantities=(), conflicts=()):
    lines = ["CORRECTIONS REQUIRED: a previous draft of this answer "
             "failed verification. Fix every item below. Ground each "
             "claim in the excerpts or the verified figures, correct "
             "it, or remove it; if the documents do not cover a point, "
             "say so plainly instead of asserting it."]
    for f in mech_failures or ():
        lines.append(f"- Missing or wrong mechanism element: {f['label']}")
    for v in ungrounded or ():
        lines.append(f"- The figure {_fmt_money(v)} appears in none of "
                     "the excerpts or verified figures; do not use it.")
    for c in (jr or {}).get("claims", ()):
        if c["final"] != "supported" and not c.get("meta"):
            note = ("; ".join(c["notes"][:2])) or "no source supports it"
            lines.append(f"- Claim judged {c['final']}: \"{c['text']}\" "
                         f"({note})")
            for h in c.get("refutations") or ():
                lines.append(
                    "  Possibly contradicting source: "
                    + (h.get("title") or "") + " — "
                    + (h.get("excerpt") or "")[:300])
    # v15 additions: uncited load-bearing sentences (item 3),
    # quantity failures (item 5), divergence-required points
    # (item 4), source conflicts to surface (item 6).
    for s in uncited or ():
        lines.append("- This factual sentence carries no citation "
                     "and is unverified by construction: \"" + s +
                     "\" — cite the excerpt it rests on with [n], "
                     "correct it, or remove it.")
    for f in quantities or ():
        lines.append(f"- Quantity check failed: {f['label']} "
                     "Correct the arithmetic.")
    for cf in conflicts or ():
        # v18 (F16): conflict objections format non-dollar
        # kinds (the ABLE age conflict) with their units.
        if cf.get("kind") == "age":
            lines.append(
                f"- The sources disagree on {cf['entry']}: "
                f"age {cf['registry_value']:,.0f} (verified "
                f"registry) vs age {cf['corpus_value']:,.0f} "
                f"({cf['source'] or 'a retrieved source'}). "
                "State BOTH values with where each comes "
                "from; do not present one as settled.")
        else:
            lines.append(f"- The sources disagree on {cf['entry']}: "
                         f"${cf['registry_value']:,.2f} (verified "
                         f"registry) vs ${cf['corpus_value']:,.2f} "
                         f"({cf['source'] or 'a retrieved source'}). "
                         "State BOTH figures with where each comes "
                         "from; do not present one as settled.")
    return "\n".join(lines)


def _assemble_capped_partial(draft, jr, question, hits,
                             allowed_extra):
    """v15 (the 600s cap): build a Partial from the last judged
    draft, then re-run the figure checker on the assembly.
    v18 (F4, pass 2 item A8): the assembly is an ALLOW-LIST,
    reversing the v15 construction. A sentence ships only when
    its text IS a claim of this round (exact claim-text
    identity under the shared normalizer, not paraphrase
    mapping or token overlap) whose final is "supported".
    Through v17.3 it was a deny-list: any sentence not
    resembling a FAILED claim shipped, including sentences no
    judge had ever seen, under a note claiming only
    panel-verified claims were shown. The arithmetic gate runs
    here too (A7: the capped path never ran it). Returns
    (text, min_coverage) — the minimum family coverage over
    the kept claims, which sets partial vs partial_single —
    or None when nothing verified survives (the caller then
    IDKs; the cap never ships unverified material). Identity
    is judged on citation-stripped normalized text: a draft
    sentence carries its [n] markers, the judge's claim text
    does not, and decoration is not content."""
    def _f4key(t):
        t = _norm_claim_text(t)
        t = re.sub(r"\[\d+\]", "", t)
        t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
        t = re.sub(r"\s+", " ", t).strip()
        t = re.sub(r"\s+([.!?])$", r"\1", t)
        return t.rstrip(".")

    supported = {}
    for c in (jr or {}).get("claims", ()):
        if c["final"] == "supported" and not c.get("meta"):
            supported[_f4key(c["text"])] = c
    parsed = _parse_citations(draft, hits)
    cited = {_f4key(cc["text"]) for cc in parsed["cited_claims"]}
    pairs = []
    for s in _split_sentences(draft):
        if s.lower().startswith("confidence:"):
            continue
        key = _f4key(s)
        if key in cited and key in supported:
            pairs.append((s, supported[key]))
    for _pass in range(3):
        if not pairs:
            return None
        text = " ".join(s for s, _c in pairs)
        _a, ung = _check_figures(text, question, hits,
                                 extra_allowed=allowed_extra)
        arith = _arithmetic_errors(text)
        if not ung and not arith:
            # v26.2 (fix 6): the partial floor. A one-sentence
            # assembly ships only when its claim is
            # load-bearing (battery B16 Deep shipped a single
            # orphaned non-load-bearing sentence as a partial);
            # and when the judged draft carried figures and the
            # question seeks one, an assembly with NO figure in
            # it does not ship (battery B20 Deep: a partial
            # confirming a maximum exists, its amount stripped).
            # Both floors return None — the caller's existing
            # fail-closed IDK — and never loosen anything.
            if len(pairs) == 1 \
                    and not (pairs[0][1].get("load_bearing")):
                return None
            if _claim_money(draft) and not _claim_money(text) \
                    and _FIGURE_SEEKING_RX.search(question or ""):
                return None
            min_cov = min((len(c.get("coverage") or [])
                           for _s, c in pairs), default=0)
            return text, min_cov
        bad_toks = {str(v).replace(",", "") for v in ung}

        def _bad(s):
            if bad_toks and set(
                    re.findall(r"\d[\d,]*\.?\d*", s)) & bad_toks:
                return True
            return bool(arith and _arithmetic_errors(s))

        pairs = [(s, c) for s, c in pairs if not _bad(s)]
    return None


# --- v26.2 (fix 5): IDK-shaped shipped text -------------------
# A draft whose operative lead states the documents do not
# answer the question passes every gate (a refusal contains
# nothing to contradict) and through v26.1 shipped under
# checked / quick badges — the battery's badge audit found 8
# such records. The ship exits in _run_unit and
# _run_unit_quick test their own text with this detector and
# fall to the IDK disposition when it fires, so the badge
# never claims a completed lane for a refusal. (The ask-level
# web branch keys off an IDK disposition, so these asks get
# the branch's chance exactly as designed.)
_IDK_SHAPE_RES = (
    re.compile(r"i don'?t know based on the available "
               r"documents"),
    re.compile(r"i (could not|cannot|can't) verify"),
    re.compile(r"the (available )?documents (do not|don't) "
               r"(say|state|specify|give|provide)"),
    re.compile(r"none of the excerpts (give|state|say|"
               r"provide|describe)"),
    re.compile(r"no specific rule listed"),
)


def _idk_shaped(text):
    """True when one of the text's first two substantive
    sentences (skipping "Confidence:" lines and markdown
    headings) is a not-knowing statement in one of the
    shapes above."""
    sents = []
    for s in _split_sentences(text or ""):
        st = s.strip()
        if st.lower().startswith("confidence:"):
            continue
        if st.startswith("#"):
            continue
        sents.append(st)
    for s in sents[:2]:
        sl = s.lower().replace("’", "'")
        if any(rx.search(sl) for rx in _IDK_SHAPE_RES):
            return True
    return False


# v26.2 (fix 6): the figure-seeking question shape for the
# capped-partial central-figure floor.
_FIGURE_SEEKING_RX = re.compile(
    r"how much|how many|what is the|maximum|limit|cap|"
    r"amount|cost", re.IGNORECASE)


def _lead_citation(ctx):
    """The citation a mechanism-led answer's lead sentence must
    carry: the first excerpt that literally states the matched
    mechanism's material, else [registry] (the mechanism block
    the drafter was given)."""
    terms = []
    for m in ctx["mechanisms"]:
        terms += [t.lower() for t in (m.get("sufficiency_terms")
                                      or ())]
    for i, h in enumerate(ctx["hits"]):
        low = ((h.get("title") or "") + " "
               + (h.get("excerpt") or "")).lower()
        if any(t in low for t in terms):
            return f"[{i + 1}]"
    return "[registry]"


def _repair_lead(ctx, draft, lead, eng, mdl, instr):
    """Item 4 micro-repair: the drafter rewrites ONLY the lead
    sentence, with the exact citation it must carry named in
    the instruction. Returns the new lead sentence, or None
    when the repair did not produce a cited single sentence.
    Code never writes or attaches the citation itself; it only
    checks that the drafter's rewrite carries one."""
    cite = _lead_citation(ctx)
    repair_instr = (
        instr + "\n\nMICRO-REPAIR: rewrite ONLY this one "
        "sentence, the answer's lead:\n\"" + lead + "\"\n"
        "The rewritten sentence must end with this citation: "
        + cite + " (the source the sentence's mechanism rests "
        "on). Keep the sentence's meaning and every figure in "
        "it; change nothing else. Reply with ONLY the rewritten "
        "sentence: no preamble, no explanation, no other text.")
    try:
        text, _p, _s, _dg, _fb = _generate(
            eng, mdl, ctx["unit_q"], ctx["hits"], repair_instr)
    except Exception:
        return None
    text = (text or "").strip().strip('"')
    text = text.split("\n")[0].strip()
    if not text or len(text) > 500:
        return None
    if not re.search(r"\[\d+\]|\[registry\]", text):
        return None
    return text


# --- v30 (tier-outage retry): a drafting outage window
# (both hosted tiers down at once) cost five Battery 2
# asks honest IDKs inside one window even though the
# providers recovered on their own minutes later. When a
# draft attempt fails with EXACTLY the all-tiers-down
# error, wait briefly and retry that attempt ONCE before
# the unit gives up; any other failure propagates
# immediately, and the retry never starts when the ask's
# remaining budget cannot cover the wait plus a bounded
# attempt (the wait itself is charged to the ask clock,
# and the hard backstop is unchanged).
TIER_OUTAGE_RETRY_WAIT_S = 20.0


def _is_tier_outage(exc):
    """Exactly the all-tiers-down condition: _generate's
    hosted ladder raises RuntimeError(f"{job}: every
    drafting tier down") and nothing else uses the text."""
    return isinstance(exc, RuntimeError) and str(
        exc).rstrip().endswith("every drafting tier down")


def _generate_with_outage_retry(engine, model, question,
                                hits, instructions,
                                deadline=None):
    """_generate with the one-shot outage retry above.
    Non-outage errors, and a second outage, propagate to
    the caller's existing failure handling unchanged."""
    try:
        return _generate(engine, model, question, hits,
                         instructions)
    except Exception as e:
        if not _is_tier_outage(e) \
                or TIER_OUTAGE_RETRY_WAIT_S <= 0:
            raise
        if _budget_remaining(deadline) \
                <= TIER_OUTAGE_RETRY_WAIT_S + 30:
            raise
        print(f"[v30] drafting tiers down ({e}); one "
              f"retry after "
              f"{TIER_OUTAGE_RETRY_WAIT_S:.0f}s",
              flush=True)
        time.sleep(TIER_OUTAGE_RETRY_WAIT_S)
        return _generate(engine, model, question, hits,
                         instructions)


def _run_unit(ctx):
    """Run the ladder for one answering unit. ctx keys: question
    (full question, for figure provenance), unit_q (what this unit
    answers), hits, engine ('local'|'gemini'), draft_model,
    instructions (base prompt additions), mechanisms, allowed_extra
    (figure-checker allowed values), csra_comp, hard (bool).
    v15 ctx additions: corp (the corpus handle),
    deadline (monotonic cap), diverge (item 4 eligibility),
    explicit_model (an API caller's own model pick, never
    overridden by the hosted drafting ladder).
    Returns the unit record: {answer (None when IDK), badge, served,
    fallback, downgraded, judging, claims, figure_check, rungs,
    judge_ms, refute_ms, cap_hit, missing, gate, prompt}."""
    reg_block = _registry_block(ctx.get("registry_entries") or ())
    mech_only = _mechanism_block(ctx["mechanisms"])
    # Judges see the registry + mechanism blocks. Drafters get the
    # mechanism block, plus the registry block unless the caller's
    # instructions already carry it (the single-question path reuses
    # the v13 instruction blob, which does).
    judge_block = "\n\n".join(x for x in (reg_block, mech_only) if x)
    draft_extra = mech_only
    if not ctx.get("reg_in_instructions") and reg_block:
        draft_extra = "\n\n".join(x for x in (reg_block, mech_only)
                                  if x)
    hard = bool(ctx.get("hard"))
    deadline = ctx.get("deadline")
    # v19 (§G4): a per-class judging budget inside the ask's
    # absolute cap. A simple unit gets 150s of ladder time and
    # a hard unit 420s (v20: 285s); the min() keeps the ask-level 600s cap
    # bounding the whole ask (bundled parts share it). At the
    # budget the ladder stops and the unit composes from the
    # verdicts in hand (the capped-partial path) instead of
    # starting another round.
    # v20 (the operator's acceptance criterion, 2026-10-14): the hard
    # budget drops to 285s — an ask unit must return in under
    # 5 minutes and accurate; 285 leaves margin under the
    # 300s wall, and the ask-level 600s cap stays as the
    # outer safety net. The deadline is published on the ask
    # context so chain legs clamp to it (_leg_timeout).
    # v23.2 (BUG-11 F1 — THE re-arming fix): the class budget
    # is a deadline anchored at ASK START, shared by every
    # unit and re-run of the ask. Through v23.1 this block
    # computed deadline = min(ask cap, NOW + budget) at every
    # _run_unit entry, so each invocation — the ladder, then
    # the post-IDK rescue re-run, then the branch re-run —
    # received a FRESH 150/285s window bounded only by the
    # 600s outer cap; the CSRA ask burned ~228s in unit 1 and
    # ~189s in the rescue unit, no invocation crossed its own
    # private deadline, and budget_cut recorded False at
    # 416.9s. Now: when the ask clock is anchored (do_POST
    # anchors it), the unit's deadline IS the ask's one
    # deadline (class deadline, outer-capped), further
    # bounded by any tighter ctx deadline the caller passed.
    # Standalone callers (offline suites, no anchored clock)
    # anchor a unit-local clock at entry, preserving the
    # pre-v23.2 standalone semantics exactly.
    budget_s = ASK_BUDGET_HARD_S if hard else ASK_BUDGET_SIMPLE_S
    _actx0 = _ask_ctx()
    if _actx0.ask_t0 is not None:
        _ask_clock_class(hard)
        _cands = [d for d in (_actx0.class_deadline,
                              _actx0.ask_deadline, deadline,
                              ctx.get("class_deadline"))
                  if d is not None]
        deadline = min(_cands) if _cands else None
    elif deadline is not None:
        deadline = min(deadline,
                         time.monotonic() + budget_s)
    if deadline is not None:
        _actx0.unit_deadline = deadline
    else:
        budget_s = ASK_TIME_CAP_S
    # v18 (F13): the checking round sees the FULL
    # question for a bundled part, with the part scope stated;
    # through v17.3 a part was drafted and judged as if it were
    # the whole question, so anaphora and shared conditions in
    # the other parts were invisible. Drafting keeps unit_q;
    # the full-question context also rides the unit's drafting
    # instructions (set by the caller for parts).
    judge_q = ctx["unit_q"]
    if ctx.get("full_question") \
            and ctx["full_question"] != ctx["unit_q"]:
        judge_q = (ctx["full_question"]
                   + "\n\n(This unit answers only this part: "
                   + ctx["unit_q"] + ")")
    ev0 = len(_events())
    # v24 (§6): bind this unit's progress tracker for the
    # phase hooks, and stamp the unit live. On a re-run
    # (rescue / web branch) the sections reset for the new
    # pass; the tracker keeps the pass count + caption the
    # caller set.
    _trk = ctx.get("trk")
    _TRK_VAR.set(_trk)
    if _trk is not None:
        _trk["status"] = "active"
        if _trk["t0"] is None:
            _trk["t0"] = time.monotonic()
        if _trk["pass"] > 1:
            for _sec in _trk["sections"].values():
                _sec.update({"state": "waiting", "seconds": None,
                             "detail": "", "t0": None})
        _trk_set(_trk, "retrieving", "done")
    # v20.2: this unit's retrieval-pool snapshots (any
    # ask-mode hybrid_search that runs inside the unit).
    rp0 = len(_ask_ctx().retr_pools)
    lead_repairs = 0
    stripped_sentences = []
    conflicts = _conflict_scan(ctx["hits"])
    draft = None
    jr = None
    mech_failures, ungrounded = [], []
    quantities = []
    uncited_lb = []
    judge_ms = 0
    refute_ms = 0
    rungs = 0
    objections = ""
    gen_error = None
    cap_hit = False
    cut_phase = None
    # v23.2 (F2/F3): the ladder stopped on the shared draft
    # cap, and the assembly guard's firing record.
    draft_capped = False
    assembly_guard = None
    cost_capped = False
    conflict_fail = False
    fail_fast = False  # v26.2 (fix 7): the ladder stopped on
    # a repeated deterministic failure signature
    _prev_sig = None
    idk_shaped_hit = False  # v26.2 (fix 5): a ship exit's
    # text was IDK-shaped and the exit was refused
    check_error = None  # v26 (F10): a checker exception's text
    judged_draft = None       # last draft that went through judging
    # v23: divergence-required points are gone with the
    # oracle; the ship tests keep no missing-points term.
    divergence = None  # v23: the oracle is deleted; always None

    def _draft_choice(rung):
        """(engine, model) for one generation attempt. The
        hosted ladder (engines "nim"/"nimfast", v15-era names)
        owns its model choice in _generate; the model named
        here is the ladder's primary (v23.1: xAI Grok 4.7),
        for the record. Gemini units are unchanged."""
        if ctx.get("draft_seat_model"):
            # v24 (§8): a Models-panel drafting pick serves
            # EVERY rung (the v14 routed-redraft rule below
            # governs the routed default, not a user's pick).
            return "local", ctx["draft_seat_model"]
        if ctx["engine"] == "gemini" or ctx.get("explicit_model"):
            if rung > 0 and ctx["engine"] == "local":
                # v26 (F8): the v14 redraft rule escalated
                # local redrafts to the routed 14B; the
                # route is retired, so a redraft stays on
                # the ask's own model.
                return "local", ctx["draft_model"]
            return ctx["engine"], ctx["draft_model"]
        if rung == 0 and not hard:
            return "nimfast", XAI_HARD_MODEL
        return "nim", XAI_HARD_MODEL

    def _try_generate(engine, model, instr):
        """Generate with the fallback chains. v23.1: _generate's
        hosted ladder already walked its tiers (xAI Grok 4.7,
        then the Groq backup), so reaching the local branch
        means the remote drafting path is genuinely
        unavailable (item 5's one sanctioned drafting load);
        the load is recorded."""
        try:
            # v30: the hosted attempt carries the outage
            # retry; the local fallback below still runs
            # only when the retry also fails.
            return _generate_with_outage_retry(
                engine, model, ctx["unit_q"], ctx["hits"],
                instr, deadline=deadline)
        except Exception:
            if engine in ("nim", "nimfast"):
                _note_local_load("fallback-draft")
                if engine == "nim" or hard:
                    # v26 (F8): the hard-ask local fallback
                    # drafted on the routed 14B through
                    # v25.1; the route is retired and the
                    # fallback is the installed 9B, with the
                    # same VRAM courtesy for it.
                    _unload_others(LOCAL_MODEL)
                    return _generate("local", LOCAL_MODEL,
                                     ctx["unit_q"], ctx["hits"],
                                     instr)
                return _generate("local", LOCAL_MODEL,
                                 ctx["unit_q"], ctx["hits"], instr)
            raise

    def _quantities_for(text):
        """This unit's quantity failures: the unit's OWN
        mechanisms (v18, pass 2 item F3: through v17.3 the
        checker re-matched mechanisms against the full question
        for every part) plus the arithmetic gate (F6) folded
        into the same objection channel."""
        qs = _check_quantities(text, ctx["question"],
                               mechanisms=ctx["mechanisms"])
        for note in _arithmetic_errors(text):
            qs.append({"mechanism": "arithmetic",
                       "label": note.lstrip("- ")})
        return qs

    def _mark_cap():
        """v23.1 (WS-A): record a budget cut + the phase the
        unit was in when the budget ran out. v23.2: the cut is
        ALSO noted on the ask context (first cut wins), so the
        ask record stays truthful even when the cutting unit
        is a re-run whose result is later discarded."""
        nonlocal cap_hit, cut_phase
        cap_hit = True
        _open = _ask_ctx().phase_open
        cut_phase = _open[0] if _open else None
        _note_budget_cut(cut_phase)

    for rung in range(3):  # draft + two redrafts (operator decision)
        _check_cancel()  # v24 (§5.2): the Stop seam
        if deadline and time.monotonic() > deadline:
            _mark_cap()
            break
        if _or_ask_cost() >= PER_ASK_CEILING_USD:
            # v18 (§B remainder): past the per-ask cost line the
            # ladder stops buying redrafts; the best judged
            # draft goes to the capped-partial path below.
            # v26 (F1): the ceiling is consulted at EVERY rung
            # top, rung 0 included — through v25.1 the
            # rung > 0 condition made it a redraft brake only,
            # so a first draft (or a re-run's first draft on an
            # ask whose sibling units already spent) started
            # past the line unchecked.
            cost_capped = True
            break
        rungs = rung + 1
        _set_stage("drafting" if rung == 0
                   else f"redrafting (round {rung + 1})")
        _set_phase("drafting", round=rung + 1)
        instr = ctx["instructions"]
        if draft_extra:
            instr = (instr + "\n\n" + draft_extra).strip()
        instr = (instr + "\n\n" + CITATION_CONTRACT).strip()
        if objections:
            instr = (instr + "\n\n" + objections).strip()
        # v23.2 (F1): no new drafting call starts when the
        # remaining budget cannot cover a bounded attempt.
        if deadline and not _call_may_start(deadline):
            _mark_cap()
            break
        # v23.2 (F2): every ladder rung draws from the ask's
        # ONE draft-call counter (shared with lead repairs
        # and every re-run). At the cap the ladder stops and
        # the best judged draft on hand finalizes below.
        if not _claim_draft_call():
            draft_capped = True
            break
        eng, mdl = _draft_choice(rung)
        try:
            res = _try_generate(eng, mdl, instr)
        except Exception as e:
            gen_error = str(e)[:200]
            draft = None
            break
        draft, prompt, served, dg, fb = res
        if _is_memo_shaped_draft(draft):
            # v22.1 shape guard: a memo about corrections and
            # citations is not a draft. Reject it as a generation
            # failure (the unit IDKs on the generation gate with
            # the reason recorded) instead of spending the
            # unit's remaining budget judging a non-answer. A
            # prior rung's judged draft, when one exists, stays
            # in judged_draft for the record.
            gen_error = ("drafter returned a memo-shaped "
                         "non-answer (meta text about corrections "
                         "and citations); rejected by the draft "
                         "shape guard")
            draft = None
            break
        if served:
            # §C: the drafting model's family is excluded from
            # every verdict path for the rest of this ask (a
            # model never judges its own draft).
            _srv = served.split(" (")[0].strip()
            _actx = _ask_ctx()
            _actx.draft_family = _model_family(_srv)
            _actx.served_models["draft"] = _srv
        ctx["_served"], ctx["_dg"], ctx["_fb"] = served, dg, fb
        ctx["_prompt"] = prompt
        try:
            _a, ungrounded = _check_figures(
                draft, ctx["question"], ctx["hits"],
                extra_allowed=ctx["allowed_extra"])
            mech_failures = _mech_required_failures(
                draft, ctx["mechanisms"], ctx.get("csra_comp"))
            quantities = _quantities_for(draft)
            parsed = _parse_citations(draft, ctx["hits"])
            uncited_lb = parsed["uncited_lb"]
            # v16 item 4: a mechanism-led answer's LEAD factual
            # sentence is load-bearing by construction (it states
            # the mechanism the whole answer hangs on). Track it for
            # the micro-repair and the named-citation objection.
            lead_sentence = None
            if ctx["mechanisms"]:
                for s in parsed["sentences"]:
                    if s["factual"]:
                        lead_sentence = s["text"]
                        break
            lead_uncited = bool(lead_sentence
                                and lead_sentence in uncited_lb)
            c_claims, c_forced = _conflict_claims(
                conflicts, draft,
                engaged_ids={e.get("id") for e in
                             (ctx.get("registry_entries") or ())})
            # v18 (F16): conflict claims are resolved IN CODE ONLY
            # and never enter the judge-facing claim set (pass 2
            # item F1): a draft that does not surface both values
            # fails this gate whatever any judge says about it.
            conflict_fail = bool(c_forced)
            extra_claims = [dict(cc, span=cc["text"])
                            for cc in parsed["cited_claims"]]
        except Exception as e:
            # v26 (F10): a checker that RAISES fails closed
            # like a checker that fails — the unit IDKs with
            # the exception recorded, instead of the exception
            # escaping to do_POST's generic handler (a bare
            # 500, and no ask record was written). The catch
            # is scoped to the checker invocations. Any prior
            # judged draft is discarded too: the gate
            # variables are mid-update for a draft that was
            # never judged, so no disposition may read them
            # against the older draft.
            check_error = f"{type(e).__name__}: {e}"[:200]
            judged_draft = None
            break
        if served and served.startswith(LOCAL_MODEL):
            # A resident 9B draft is done; free its VRAM before
            # judging in case a local fallback seat is needed.
            # v26 (F8): through v25.1 this kept the routed
            # 14B and unloaded everything else (the 9B
            # included, by indirection); it now unloads the
            # 9B drafter itself, which is the stated intent.
            ollama_unload(LOCAL_MODEL)
        _set_stage(f"checking round {rung + 1}")
        _set_phase("checking", round=rung + 1)
        jt0 = time.monotonic()
        jr = _check_round(judge_q, ctx["hits"], draft,
                          hard, judge_block, corp=ctx.get("corp"),
                          mechanisms=ctx["mechanisms"],
                          extra_claims=extra_claims,
                          local_only=bool(ctx.get("local_mode")),
                          judge_model=ctx.get("judge_model"),
                          extract_model=ctx.get("extract_model"),
                          use_cache=not ctx.get("fresh"),
                          deadline=deadline)
        judge_ms += int((time.monotonic() - jt0) * 1000)
        refute_ms += jr.get("refute_ms", 0)
        judged_draft = draft
        if deadline and time.monotonic() > deadline:
            # v23.1 (WS-A): the checking round consumed the
            # rest of the budget. The cap is marked; the
            # normal flow below still ships a passing draft,
            # and the next rung's top check stops the ladder
            # otherwise.
            _mark_cap()
        if lead_uncited:
            # Item 4 micro-repair: ONE per rung. The drafter
            # rewrites only the lead, with the exact citation it
            # must carry named; the repaired draft re-enters
            # normal judging and every other gate re-runs on
            # it. Code never attaches the citation itself and
            # the gate is not loosened.
            # v23.2 (BUG-11 F1+F2): a lead repair IS a drafting
            # call — through v23.1 it called _generate directly,
            # uncounted by the rung loop and ungated by the
            # budget, which is how the CSRA ask's 3 rungs became
            # 6 draft calls in the first unit alone (3 rungs +
            # 3 repairs). It now claims from the shared counter
            # and starts only when the budget can cover it.
            if deadline and not _call_may_start(deadline):
                _mark_cap()
                new_lead = None
            elif _or_ask_cost() >= PER_ASK_CEILING_USD:
                # v26 (F1): a lead repair is a paid leg start;
                # it does not start at the ceiling either.
                cost_capped = True
                new_lead = None
            elif not _claim_draft_call():
                draft_capped = True
                new_lead = None
            else:
                lead_repairs += 1
                _trk_set(_trk_current(), "lead_repair", "active")
                new_lead = _repair_lead(ctx, draft, lead_sentence,
                                        eng, mdl, instr)
            if new_lead and new_lead != lead_sentence:
                draft2 = draft.replace(lead_sentence, new_lead, 1)
                parsed2 = _parse_citations(draft2, ctx["hits"])
                if new_lead not in parsed2["uncited_lb"]:
                    _set_stage(f"checking round {rung + 1} "
                               "(lead repair)")
                    jt1 = time.monotonic()
                    jr2 = _check_round(
                        judge_q, ctx["hits"], draft2, hard,
                        judge_block, corp=ctx.get("corp"),
                        mechanisms=ctx["mechanisms"],
                        extra_claims=extra_claims,
                        local_only=bool(ctx.get("local_mode")),
                          judge_model=ctx.get("judge_model"),
                          extract_model=ctx.get("extract_model"),
                        use_cache=not ctx.get("fresh"),
                        deadline=deadline)
                    judge_ms += int((time.monotonic() - jt1) * 1000)
                    refute_ms += jr2.get("refute_ms", 0)
                    draft, jr = draft2, jr2
                    parsed = parsed2
                    uncited_lb = parsed2["uncited_lb"]
                    judged_draft = draft
                    _cc2, _cf2 = _conflict_claims(
                        conflicts, draft2,
                        engaged_ids={e.get("id") for e in
                                     (ctx.get("registry_entries")
                                      or ())})
                    conflict_fail = bool(_cf2)
                    _a, ungrounded = _check_figures(
                        draft, ctx["question"], ctx["hits"],
                        extra_allowed=ctx["allowed_extra"])
                    mech_failures = _mech_required_failures(
                        draft, ctx["mechanisms"],
                        ctx.get("csra_comp"))
                    quantities = _quantities_for(draft)
                    lead_uncited = bool(
                        lead_sentence and lead_sentence in uncited_lb)
        if jr["mode"] == "none":
            break  # nothing could be checked: redrafting blind
            # helps nothing
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not conflict_fail:
            break  # every gate passed: ship this draft
        objections = _build_objections(
            jr, mech_failures, ungrounded, uncited_lb, quantities,
            conflicts if conflict_fail else ())
        if lead_uncited:
            # Item 4 (b): the redraft objection for a failed lead
            # names the exact sentence AND the exact citation it
            # must carry.
            objections += (
                "\n- The answer's LEAD sentence is load-bearing "
                "by construction (it states the governing "
                "mechanism) and carries no citation: \""
                + (lead_sentence or "") + "\" — the lead must "
                "end with the citation " + _lead_citation(ctx)
                + ". Rewrite the lead with that citation "
                "attached; do not drop the mechanism.")
        draft = None if rung < 2 else draft
        # v26.2 (fix 7): fail-fast. The rung's deterministic
        # FIGURE-failure signature — the ungrounded figures
        # plus the forced-conflict flag — is compared with
        # the previous rung's. An identical second failure
        # means the drafter demonstrably is not fixing what
        # the objections listed; the ladder stops here and
        # the normal final disposition (strip / capped
        # partial / IDK) reads the same state it reads
        # today. Judge-only failures (empty signature) never
        # trip it. The signature is deliberately figure-
        # scoped: uncited-sentence and mechanism-lead
        # failures are what redrafts and lead repairs exist
        # for, and the BUG-11 replay's recorded CSRA shape
        # burns to the draft cap on exactly those terms —
        # that accounting is a permanent regression gate
        # and must not change (found at the v26.2 gate run;
        # the battery evidence for this fix is figure
        # failures: B05 Deep 286.6s, B06 Deep 151.0s, both
        # re-failing identical ungrounded figures rung
        # after rung). The per-ask cost ceiling is
        # unchanged (D1); this only stops spending inside
        # it on a proven no-progress loop.
        _sig = (frozenset(round(float(v), 2) for v in ungrounded),
                bool(conflict_fail))
        if _prev_sig is not None and _sig == _prev_sig \
                and any(_sig):
            fail_fast = True
            break
        _prev_sig = _sig
    # v17.3: strip, don't sink. The ladder's repair rungs are
    # exhausted; if the last judged draft still carries uncited
    # load-bearing sentences, cut the ones that map to no
    # extracted claim (asides) and let the stripped draft
    # re-enter the normal final disposition below (ship test,
    # capped assembly) with every deterministic gate re-run on
    # the stripped text. The cuts are recorded in
    # stripped_sentences (the v23.1 checks record's strip log).
    # Claim-bearing uncited sentences are untouched: they fail
    # closed below exactly as before.
    # v23.2 (F1): the strip's re-check is a provider stage
    # too — when the budget cannot cover it, the cut is marked
    # here and the strip (whose outer condition excludes
    # cap_hit) does not run.
    if uncited_lb and judged_draft is not None and jr is not None \
            and not cap_hit and deadline \
            and not _call_may_start(deadline):
        _mark_cap()
    if uncited_lb and judged_draft is not None and jr is not None \
            and jr["mode"] != "none" and jr["claims"] \
            and not cap_hit and not gen_error and not cost_capped:
        # v18 (F7, pass 2 item A5): no strip after a cap hit, a
        # generation error, or the cost ceiling — in those
        # states the strip could resurrect a draft whose judging
        # never completed (and the re-judge below would spend
        # past the line that stopped the ladder).
        lead_now = None
        if ctx["mechanisms"]:
            for s in parsed["sentences"]:
                if s["factual"]:
                    lead_now = s["text"]
                    break
        # v23.2 (F3): the flagged set for THIS draft — the
        # citation parser's uncited sentences, sentences
        # mapping to a claim the checks/judge did not support,
        # and sentences carrying an ungrounded figure. The
        # strip may remove only flagged sentences.
        _flagged = set(uncited_lb)
        for _c in jr["claims"]:
            if _c.get("final") != "supported":
                for _s in parsed["sentences"]:
                    if _sentence_maps_to_claim(_s["text"], [_c]):
                        _flagged.add(_s["text"])
        if ungrounded:
            _ung = {round(float(v), 2) for v in ungrounded}
            for _s in parsed["sentences"]:
                if _claim_money(_s["text"]) & _ung:
                    _flagged.add(_s["text"])
        stripped_draft, stripped_sentences = _strip_uncited_asides(
            judged_draft, parsed, jr["claims"], lead_now,
            flagged=_flagged)
        if stripped_draft is not None:
            draft = stripped_draft
            judged_draft = stripped_draft
            try:
                parsed = _parse_citations(stripped_draft,
                                          ctx["hits"])
                uncited_lb = parsed["uncited_lb"]
                _a, ungrounded = _check_figures(
                    stripped_draft, ctx["question"], ctx["hits"],
                    extra_allowed=ctx["allowed_extra"])
                mech_failures = _mech_required_failures(
                    stripped_draft, ctx["mechanisms"],
                    ctx.get("csra_comp"))
                quantities = _quantities_for(stripped_draft)
            except Exception as e:
                # v26 (F10): the strip's re-check is a checker
                # invocation too — same fail-closed rule as
                # the per-rung wrap above.
                check_error = f"{type(e).__name__}: {e}"[:200]
                judged_draft = None
                stripped_draft = None
        if stripped_draft is not None:
            # v18 (F7): the stripped draft is RE-CHECKED before
            # it can ship. Through v17.3 only the deterministic
            # gates re-ran and the ship test below read the
            # PRE-strip round's pass, so a strip could carry a
            # check-failed draft into a verified ship.
            _set_stage("checking (stripped draft)")
            _jt2 = time.monotonic()
            try:
                jr = _check_round(
                    judge_q, ctx["hits"], stripped_draft, hard,
                    judge_block, corp=ctx.get("corp"),
                    mechanisms=ctx["mechanisms"],
                    extra_claims=[dict(cc, span=cc["text"])
                                  for cc in parsed["cited_claims"]],
                    local_only=bool(ctx.get("local_mode")),
                              judge_model=ctx.get("judge_model"),
                              extract_model=ctx.get("extract_model"),
                    use_cache=not ctx.get("fresh"), deadline=deadline)
                _cc3, _cf3 = _conflict_claims(
                    conflicts, stripped_draft,
                    engaged_ids={e.get("id") for e in
                                 (ctx.get("registry_entries")
                                  or ())})
                conflict_fail = bool(_cf3)
            except Exception as e:
                # v26 (F10): same fail-closed rule — a checker
                # exception in the strip's re-judge IDKs the
                # unit with the exception recorded.
                check_error = f"{type(e).__name__}: {e}"[:200]
                judged_draft = None
            judge_ms += int((time.monotonic() - _jt2) * 1000)
            if check_error is None:
                refute_ms += jr.get("refute_ms", 0)
                judged_draft = stripped_draft
                if deadline and time.monotonic() > deadline:
                    _mark_cap()
    # v23 (WS1): the ladder is done; what remains (capped
    # assembly, IDK assembly) is the final-checks phase on the
    # page's stepper.
    _set_phase("final checks")
    shipped = False
    if draft is not None and jr is not None and jr["mode"] != "none":
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not conflict_fail:
            shipped = True
        elif deadline and time.monotonic() > deadline:
            _mark_cap()
    if shipped and _idk_shaped(draft):
        # v26.2 (fix 5): badge honesty — a passing draft whose
        # lead states the documents do not answer the question
        # does not ship under a checked badge; the unit falls
        # through to its IDK disposition below.
        shipped = False
        idk_shaped_hit = True
    # v24 (§6.3): the ladder has closed — the optional
    # sections' decision points are now known for THIS pass.
    # A redraft that never ran is not-needed from this moment;
    # lead repair likewise. (Rescue / web branch finalize in
    # do_POST, where their outcomes live.)
    if _trk is not None:
        _trk_set(_trk, "final_checks", "done")
        _trk_set(_trk, "redraft2",
                 "done" if rungs >= 2 else "not-needed")
        _trk_set(_trk, "redraft3",
                 "done" if rungs >= 3 else "not-needed")
        _trk_set(_trk, "lead_repair",
                 "done" if lead_repairs else "not-needed")
    base = {"served": ctx.get("_served"), "fallback": ctx.get("_fb", False),
            "downgraded": ctx.get("_dg", False),
            "prompt": ctx.get("_prompt", ""), "rungs": rungs,
            "judge_ms": judge_ms, "refute_ms": refute_ms,
            "cap_hit": cap_hit,
            # v23.1 (WS-A/BUG-10): the budget cut + the phase
            # it landed in ride the unit record.
            "budget_cut": cap_hit,
            "cut_phase": cut_phase,
            "cost_capped": cost_capped,
            "fail_fast": fail_fast,
            # v23.2 (F2/F3): the ask's shared draft-call count
            # at this unit's end, whether this unit stopped on
            # the draft cap, and the assembly guard's record.
            "draft_calls": _ask_ctx().draft_calls,
            "draft_capped": draft_capped,
            "assembly_guard": assembly_guard,
            "draft_text": judged_draft,
            "figure_check": {"ungrounded": list(ungrounded),
                             "regenerated": rungs > 1},
            "divergence": divergence,
            "events": _events_since(ev0),
            # v20.2: this unit's retrieval-pool snapshots.
            "retr_pools": _retr_pools_since(rp0),
            "lead_repairs": lead_repairs,
            "stripped_sentences": stripped_sentences}
    if shipped:
        answer, _fixes = _check_arithmetic(draft)
        # v23.1 (WS-C): the badge names WHAT checked the
        # answer. "checked" = the deterministic checks passed
        # AND the judge pass covered every substantive claim.
        # "checked_det" = the deterministic checks passed but
        # the judge was unavailable or covered only part of
        # the claim list (the round's judge_complete flag).
        badge = "checked" if jr.get("judge_complete") \
            else "checked_det"
        return dict(base, answer=answer, badge=badge,
                    judging={"mode": jr["mode"],
                             "errors": jr["errors"],
                             "annotation": _checks_annotation(jr),
                             "checks": jr.get("checks")},
                    claims=jr["claims"], missing=[], gate=None)
    if judged_draft is not None \
            and jr is not None and jr["mode"] != "none" \
            and jr["claims"]:
        # v23.1 (WS-C): the supported-core assembly is the
        # general end-of-ladder disposition, not only the
        # cap/cost path: when the ladder ends without a full
        # pass — budget cut, cost ceiling, or claims the
        # checks could not clear — ship ONLY the sentences
        # whose claims passed, as a Partial (one badge name;
        # the panel era's partial_single / partial_reduced
        # split is gone). When nothing supported survives,
        # the assembly returns None and the unit IDKs below:
        # fail-closed is preserved.
        assembled = _assemble_capped_partial(
            judged_draft, jr, ctx["question"], ctx["hits"],
            ctx["allowed_extra"])
        if assembled:
            _atext, _min_cov = assembled
            # v23.2 (BUG-11 F3 — SHIP WHAT PASSED): the
            # assembly guard. "The draft that passed checks"
            # = a judged draft whose check round passed
            # (jr["pass"]: every substantive claim supported)
            # with clean deterministic blocks (no figure
            # failures, no citation failures in jr["checks"])
            # — the CSRA ask's exact state (figures 5/5,
            # citations clean, judge 8 supported / 0
            # contradicted), which v23.1 nevertheless shipped
            # as a ONE-sentence fragment: the assembly's
            # exact-key allow-list matched only 1 of the
            # draft's 24 sentences. MATERIAL LOSS (the guard's
            # concrete definition): the assembled text carries
            # fewer sentences than the passing round's
            # supported-claim count, OR fewer than three
            # quarters of the passing draft's sentences (the
            # round passed in full, so the draft's sentences
            # are supported content; losing a quarter of them
            # to an allow-list mismatch is material — the
            # ABLE ask's 5-of-10 shape sits exactly here). On
            # material loss the
            # checked draft ships VERBATIM (badge from the
            # round's judge coverage, as in the shipped exit)
            # and the guard's firing is logged on the record.
            _chk = jr.get("checks") or {}
            # v26 (F4): the guard's test is the FULL ship
            # test (the rung loop's six terms), not part of
            # it. Through v25.1 a draft failing ONLY the
            # mechanism, quantity, conflict, or uncited gate
            # rode this exit whole under a checked badge
            # whenever the assembly was lossy — those
            # failures live in unit variables, never in
            # jr["checks"]. The answer-level figure term
            # (ungrounded) is the ship test's own figure
            # gate and belongs here for the same reason: the
            # round's claim-level figure block can be clean
            # while the shipped text carries an ungrounded
            # figure (the F6 probe's exact shape). With all
            # the terms, such drafts fall through to the
            # partial assembly below, missing disclosure
            # and all.
            _passed_checks = bool(jr.get("pass")) \
                and not ungrounded and not (
                (_chk.get("figures") or {}).get("failed")) \
                and not ((_chk.get("citations") or {}).get(
                    "failed")) \
                and not mech_failures and not quantities \
                and not conflict_fail and not uncited_lb
            if _passed_checks:
                _supp_n = len([c for c in jr["claims"]
                               if not c.get("meta")
                               and c["final"] == "supported"])
                _draft_sents = [
                    s for s in _split_sentences(judged_draft)
                    if not s.lower().startswith("confidence:")]
                _ship_sents = [
                    s for s in _split_sentences(_atext)
                    if not s.lower().startswith("confidence:")]
                if (len(_ship_sents) < _supp_n
                        or len(_ship_sents) * 4
                        < len(_draft_sents) * 3) \
                        and not _idk_shaped(judged_draft):
                    assembly_guard = {
                        "fired": True,
                        "supported_claims": _supp_n,
                        "draft_sentences": len(_draft_sents),
                        "assembled_sentences": len(_ship_sents),
                        "action": "shipped the checked draft "
                                  "verbatim instead of the "
                                  "materially-lossy assembly"}
                    _g_answer, _g_fixes = _check_arithmetic(
                        judged_draft)
                    _g_badge = "checked" \
                        if jr.get("judge_complete") \
                        else "checked_det"
                    return dict(
                        base, answer=_g_answer, badge=_g_badge,
                        assembly_guard=assembly_guard,
                        judging={"mode": jr["mode"],
                                 "errors": jr["errors"],
                                 "annotation":
                                     _checks_annotation(jr),
                                 "checks": jr.get("checks")},
                        claims=jr["claims"], missing=[],
                        gate=None)
            if cost_capped and not cap_hit:
                _why = ("the ask reached its per-ask cost "
                        f"ceiling (${PER_ASK_CEILING_USD:.2f}); "
                        "only claims that passed the checks "
                        "are shown")
            elif cap_hit:
                _why = ("the ask reached its time budget "
                        f"({budget_s} seconds); only claims "
                        "that passed the checks are shown")
            elif draft_capped:
                _why = ("the ask reached its draft-call cap "
                        f"({DRAFT_CALL_CAP} drafting calls); "
                        "only claims that passed the checks "
                        "are shown")
            else:
                _why = ("verification left some claims "
                        "unverified; only claims that passed "
                        "the checks are shown")
            cut = [c["text"] for c in jr["claims"]
                   if c["final"] != "supported" and not c.get("meta")]
            if _idk_shaped(_atext):
                # v26.2 (fix 5): the assembly's own lead is a
                # not-knowing statement; it does not ship
                # under the partial badge either. Fall through
                # to the IDK disposition below.
                idk_shaped_hit = True
            else:
                return dict(base, answer=_atext, badge="partial",
                            judging={"mode": jr["mode"],
                                     "errors": jr["errors"],
                                     "annotation":
                                         _checks_annotation(jr),
                                     "checks": jr.get("checks")},
                            claims=jr["claims"],
                            missing=[_why,
                                     *[f"not verified: \"{t}\""
                                       for t in cut[:4]]],
                            gate=None)
    # v23.2 (F3, second placement — the ABLE-ask shape): the
    # judged draft PASSED its check round (every substantive
    # claim supported, deterministic blocks clean) but the
    # supported-core assembly returned NOTHING, so the unit
    # would IDK a draft that passed checks. Ship the checked
    # draft verbatim instead (checked when the judge covered
    # every claim, checked_det when it did not — e.g. a det
    # round after both judge legs were budget-spent, the
    # ABLE ask 87f5d6c5db39's exact state) and log the guard.
    if judged_draft is not None and jr is not None \
            and jr["mode"] != "none" and jr["claims"]:
        _chk2 = jr.get("checks") or {}
        # v26 (F4): the same full ship test as the first
        # guard — see the note there.
        if jr.get("pass") and not ungrounded and not (
                (_chk2.get("figures") or {}).get("failed")) \
                and not ((_chk2.get("citations") or {}).get(
                    "failed")) \
                and not mech_failures and not quantities \
                and not conflict_fail and not uncited_lb \
                and not _idk_shaped(judged_draft):
            assembly_guard = {
                "fired": True,
                "supported_claims": len([
                    c for c in jr["claims"] if not c.get("meta")
                    and c["final"] == "supported"]),
                "draft_sentences": len([
                    s for s in _split_sentences(judged_draft)
                    if not s.lower().startswith("confidence:")]),
                "assembled_sentences": 0,
                "action": "shipped the checked draft verbatim "
                          "instead of an IDK (the assembly "
                          "returned no supported core)"}
            _g2_answer, _g2_fixes = _check_arithmetic(
                judged_draft)
            _g2_badge = "checked" if jr.get("judge_complete") \
                else "checked_det"
            return dict(
                base, answer=_g2_answer, badge=_g2_badge,
                assembly_guard=assembly_guard,
                judging={"mode": jr["mode"],
                         "errors": jr["errors"],
                         "annotation": _checks_annotation(jr),
                         "checks": jr.get("checks")},
                claims=jr["claims"], missing=[], gate=None)
    # IDK: assemble what is missing from the failed gates.
    missing, gate = [], "checking"
    if cap_hit:
        gate = "time"
        missing.append("the ask reached its time budget "
                       f"({budget_s} seconds) before a "
                       "verified answer could be assembled")
    if gen_error:
        missing.append("the answering model could not run "
                       f"({gen_error})")
    if check_error:
        missing.append("the answer's checks could not run "
                       f"({check_error})")
    if cost_capped:
        missing.append("the ask reached its per-ask cost ceiling "
                       f"(${PER_ASK_CEILING_USD:.2f}) before a "
                       "verified answer could be assembled")
    if draft_capped:
        missing.append("the ask reached its draft-call cap "
                       f"({DRAFT_CALL_CAP} drafting calls) "
                       "before a verified answer could be "
                       "assembled")
    if jr is not None and jr["mode"] == "none":
        missing.append("no judge could run and no claims "
                       "could be checked "
                       "(" + "; ".join(jr["errors"]) + ")")
    for f in mech_failures:
        missing.append("the answer did not establish: " + f["label"])
    for f in quantities:
        missing.append("quantity check failed: " + f["label"])
    if uncited_lb:
        missing.append("factual sentences carried no citation: "
                       + "; ".join(f"\"{s[:120]}\"" for s in
                                   uncited_lb[:3]))
    if ungrounded:
        missing.append("the draft used figures found in none of the "
                       "excerpts: " + ", ".join(
                           _fmt_money(v) for v in ungrounded[:4]))
    if idk_shaped_hit or (judged_draft is not None
                          and _idk_shaped(judged_draft)):
        # v26.2 (fix 5): the reason this unit IDKs — the
        # draft's own lead said the documents do not answer.
        missing.append("the draft stated that the documents "
                       "do not answer the question, so the "
                       "answer is an IDK rather than a verified "
                       "badge")
    if jr is not None:
        for c in jr["claims"]:
            if c["final"] != "supported" and not c.get("meta"):
                missing.append(f"unverified claim: \"{c['text']}\" "
                               f"(judged {c['final']})")
                if c["final"] == "not_addressed":
                    gate = "grounding"
    if cap_hit:
        gate = "time"
    # v26 (F10): a checker exception rides the judging
    # errors, so the ask record's per-unit judge_errors
    # carry it (the record does not copy unit missing).
    _idk_errors = list((jr or {}).get("errors", []))
    if check_error:
        _idk_errors.insert(0, "checker exception: "
                           + check_error)
    return dict(base, answer=None, badge="idk",
                judging={"mode": (jr or {}).get("mode", "none"),
                         "errors": _idk_errors,
                         "annotation": _checks_annotation(jr)
                         if jr else "",
                         "checks": (jr or {}).get("checks")},
                claims=(jr or {}).get("claims", []),
                missing=missing[:8], gate=gate)


# --- v25.1: Quick self-audit contract + repair ----------------
# The Quick lane has no judge, so its ONE draft call carries a
# self-audit contract: cite every factual sentence as written,
# then audit the draft against the excerpts (every figure
# findable, every factual sentence cited) and CUT what cannot
# be supported. The deterministic final checks remain the floor
# beneath it; when the floor still fails, ONE repair draft —
# the failures listed verbatim — gets the lane's second and
# last draft call.
QUICK_SELF_AUDIT_CONTRACT = (
    "SELF-AUDIT (this lane has no judge; the server checks "
    "your answer with code): before you finish, audit your "
    "own draft against the excerpts above. Every factual "
    "sentence must end with its citation, as required above. "
    "Every dollar figure in your answer must appear in an "
    "excerpt you cite for that sentence, in the question, or "
    "in the VERIFIED FIGURES block. Cut any sentence you "
    "cannot support this way — do not soften it, remove it. "
    "Check any arithmetic you state. Output ONLY the audited "
    "answer, with no notes about the audit.")


def _quick_failure_list(ungrounded, mech_failures, quantities,
                        uncited_lb, conflict_fail):
    """A Quick draft's deterministic failures, phrased for the
    repair prompt (and kept on the unit record)."""
    out = []
    for s in uncited_lb[:6]:
        out.append("factual sentence with no citation: \""
                   + s[:160] + "\"")
    for v in ungrounded[:6]:
        out.append("figure not found in the excerpts: "
                   + _fmt_money(v))
    for f in quantities[:4]:
        out.append("quantity/arithmetic check failed: "
                   + f["label"])
    for f in mech_failures[:4]:
        out.append("the answer did not establish: " + f["label"])
    if conflict_fail:
        out.append("a source conflict's two values were not "
                   "both surfaced")
    return out


def _quick_repair_block(failures):
    lines = ["REPAIR PASS: your previous draft failed the "
             "server's deterministic checks. Write the "
             "complete answer again, fixing exactly these "
             "problems and keeping everything that was "
             "supported:"]
    lines += ["- " + f for f in failures]
    return "\n".join(lines)


def _quick_strip_asides(draft, parsed, lead_sentence=None):
    """v25: the v17.3 strip in code-only form, for the Quick
    lane. Quick runs no claim extraction, so there are no
    extracted claims to map sentences against; eligibility
    rests on the strip's own code guards alone. A candidate
    (an uncited factual sentence) is strippable only when it
    is not the mechanism lead sentence and carries no
    limiting language, no figure, and no duration — the same
    guards _strip_uncited_asides applies before its claim
    test. Anything else uncited is claim-bearing in code
    terms and stays in the text, where the caller fails
    closed on it. The removal verifies exactly as the v17.3
    strip does (the result's sentence list is the original
    minus the removed, byte-identical and in order), and the
    strip never fires when no cited factual content exists to
    leave behind. Returns (new_draft, [removed]) or
    (None, [])."""
    uncited = list(parsed.get("uncited_lb") or ())
    if not uncited or not parsed.get("cited_claims"):
        return None, []
    eligible, seen = [], set()
    for s in uncited:
        if s in seen:
            continue
        seen.add(s)
        if lead_sentence is not None and s == lead_sentence:
            continue
        if _LIMITING_RX.search(s) or _has_figure(s) \
                or _has_duration(s):
            continue
        eligible.append(s)
    if not eligible:
        return None, []
    new_draft = draft
    for s in eligible:
        new_draft = _remove_sentence(new_draft, s)
        if new_draft is None:
            return None, []
    expect = _split_sentences(draft)
    for s in eligible:
        if s not in expect:
            return None, []
        expect.remove(s)
    if _split_sentences(new_draft) != expect:
        return None, []
    return new_draft, eligible


def _run_unit_quick(ctx):
    """v25 (Quick lane): ONE draft call through the Drafting
    seat, then the deterministic final checks ONLY — no claim
    extraction, no judge pass, no redrafts, no lead repair,
    no rescue, no web branch. The ship test is _run_unit's
    own deterministic test with the judge term removed: no
    ungrounded figures, no mechanism failures, no quantity
    failures, no uncited factual sentences, no conflict
    failure. The draft call is _generate itself (the seat's
    own chain; v26 F9: claimed against THIS unit's own
    2-call budget, not the ask's shared Deep cap — the
    claim still counts on the ask-wide total); there is no
    _try_generate local rescue — if the seat raises, the
    unit IDKs. Any material failure IDKs the unit, so a
    Quick answer the code checks cannot clear is never
    shipped unchecked. Same clock discipline as _run_unit:
    the ask's ONE anchored deadline governs.
    v25.1: the draft carries the SELF-AUDIT contract (cite
    as written; audit against the excerpts; cut what cannot
    be supported), the figure gate pools every excerpt the
    drafter saw, and a draft that still fails the floor gets
    ONE repair call (same seat, the failures listed) before
    the unit IDKs — the lane's draft cap is 2.
    Returns the same unit-record shape as _run_unit."""
    reg_block = _registry_block(ctx.get("registry_entries") or ())
    mech_only = _mechanism_block(ctx["mechanisms"])
    draft_extra = mech_only
    if not ctx.get("reg_in_instructions") and reg_block:
        draft_extra = "\n\n".join(x for x in (reg_block, mech_only)
                                  if x)
    hard = bool(ctx.get("hard"))
    deadline = ctx.get("deadline")
    budget_s = ASK_BUDGET_HARD_S if hard else ASK_BUDGET_SIMPLE_S
    _actx0 = _ask_ctx()
    # v26 (F9): this unit's own draft budget starts here.
    _actx0.quick_unit_calls = 0
    if _actx0.ask_t0 is not None:
        _ask_clock_class(hard)
        _cands = [d for d in (_actx0.class_deadline,
                              _actx0.ask_deadline, deadline,
                              ctx.get("class_deadline"))
                  if d is not None]
        deadline = min(_cands) if _cands else None
    elif deadline is not None:
        deadline = min(deadline, time.monotonic() + budget_s)
    if deadline is not None:
        _actx0.unit_deadline = deadline
    ev0 = len(_events())
    rp0 = len(_ask_ctx().retr_pools)
    _trk = ctx.get("trk")
    _TRK_VAR.set(_trk)
    if _trk is not None:
        _trk["status"] = "active"
        if _trk["t0"] is None:
            _trk["t0"] = time.monotonic()
        _trk_set(_trk, "retrieving", "done")
        # v25: the Deep-only sections flip to not-needed at
        # unit start — they never run in this lane, and the
        # guarded phase hook (_trk_phase) never stamps them.
        for _sec in ("extracting", "checking", "redraft2",
                     "redraft3", "lead_repair", "rescue",
                     "web_branch"):
            _trk_set(_trk, _sec, "not-needed")
    conflicts = _conflict_scan(ctx["hits"])
    cap_hit = False
    cut_phase = None
    draft_capped = False
    cost_capped = False
    gen_error = None
    draft = None
    served = None
    prompt = ""
    attempted = False

    def _mark_cap():
        nonlocal cap_hit, cut_phase
        cap_hit = True
        _open = _ask_ctx().phase_open
        cut_phase = _open[0] if _open else None
        _note_budget_cut(cut_phase)

    _check_cancel()  # v24 (§5.2): the Stop seam
    if deadline and time.monotonic() > deadline:
        _mark_cap()
    elif deadline and not _call_may_start(deadline):
        _mark_cap()
    elif _or_ask_cost() >= PER_ASK_CEILING_USD:
        # v26 (F1): the per-ask ceiling is a start seam in
        # this lane too — at the ceiling the first draft
        # does not start and the unit IDKs on the ceiling.
        cost_capped = True
    elif not _claim_quick_draft_call():
        draft_capped = True
    if draft is None and not (cap_hit or draft_capped
                              or cost_capped):
        instr = ctx["instructions"]
        if draft_extra:
            instr = (instr + "\n\n" + draft_extra).strip()
        instr = (instr + "\n\n" + CITATION_CONTRACT).strip()
        # v25.1: the Quick draft also carries the self-audit
        # contract (the lane has no judge to catch what the
        # drafter can catch itself against the excerpts).
        instr = (instr + "\n\n"
                 + QUICK_SELF_AUDIT_CONTRACT).strip()
        _set_stage("drafting")
        _set_phase("drafting", round=1)
        if _trk is not None:
            _trk_set(_trk, "drafting", "active")
        # The Drafting seat for the one call — _run_unit's
        # rung-0 choice, seat pick first.
        if ctx.get("draft_seat_model"):
            _eng, _mdl = "local", ctx["draft_seat_model"]
        elif ctx["engine"] == "gemini" \
                or ctx.get("explicit_model"):
            _eng, _mdl = ctx["engine"], ctx["draft_model"]
        elif not hard:
            _eng, _mdl = "nimfast", XAI_HARD_MODEL
        else:
            _eng, _mdl = "nim", XAI_HARD_MODEL
        attempted = True
        try:
            draft, prompt, served, _dg, _fb = \
                _generate_with_outage_retry(
                    _eng, _mdl, ctx["unit_q"], ctx["hits"],
                    instr, deadline=deadline)
            ctx["_dg"], ctx["_fb"] = _dg, _fb
        except Exception as e:
            gen_error = str(e)[:200]
            draft = None
        if draft is not None and _is_memo_shaped_draft(draft):
            gen_error = ("drafter returned a memo-shaped "
                         "non-answer (meta text about corrections "
                         "and citations); rejected by the draft "
                         "shape guard")
            draft = None
        if served:
            _srv = served.split(" (")[0].strip()
            _actx0.draft_family = _model_family(_srv)
            _actx0.served_models["draft"] = _srv
        if _trk is not None:
            _trk_set(_trk, "drafting", "done")
    # --- deterministic final checks (the Quick ship test) ---
    _set_phase("final checks")
    if _trk is not None:
        _trk_set(_trk, "final_checks", "active")
    ungrounded, mech_failures, quantities = [], [], []
    uncited_lb, stripped = [], []
    conflict_fail = False
    gate_error = None  # v26 (F10): a checker exception's text
    idk_shaped_hit = False  # v26.2 (fix 5)

    def _det_gates(text):
        _p = _parse_citations(text, ctx["hits"])
        _a, _ung = _check_figures(
            text, ctx["question"], ctx["hits"],
            extra_allowed=ctx["allowed_extra"], pool_all=True)
        _mf = _mech_required_failures(
            text, ctx["mechanisms"], ctx.get("csra_comp"))
        _qs = _check_quantities(
            text, ctx["question"], mechanisms=ctx["mechanisms"])
        for _note in _arithmetic_errors(text):
            _qs.append({"mechanism": "arithmetic",
                        "label": _note.lstrip("- ")})
        _cc, _cf = _conflict_claims(
            conflicts, text,
            engaged_ids={e.get("id") for e in
                         (ctx.get("registry_entries") or ())})
        return _p, _ung, _mf, _qs, bool(_cf)

    def _gates_with_strip(text):
        _p, _ung, _mf, _qs, _cf = _det_gates(text)
        _unc = _p["uncited_lb"]
        _str = []
        if _unc:
            _lead = None
            if ctx["mechanisms"]:
                for _s in _p["sentences"]:
                    if _s["factual"]:
                        _lead = _s["text"]
                        break
            _sd, _str = _quick_strip_asides(text, _p, _lead)
            if _sd is not None:
                text = _sd
                _p, _ung, _mf, _qs, _cf = _det_gates(text)
                _unc = _p["uncited_lb"]
        return text, _p, _ung, _mf, _qs, _cf, _unc, _str

    if draft is not None:
        try:
            draft, parsed, ungrounded, mech_failures, \
                quantities, conflict_fail, uncited_lb, \
                stripped = _gates_with_strip(draft)
        except Exception as e:
            # v26 (F10): a checker that RAISES fails closed
            # like a checker that fails — the unit IDKs with
            # the exception recorded, instead of the exception
            # escaping to do_POST's generic handler (a bare
            # 500, and no ask record was written). The catch
            # is scoped to the checker invocations (the
            # _det_gates / _gates_with_strip calls).
            gate_error = f"{type(e).__name__}: {e}"[:200]
            draft = None
    # --- v25.1: ONE repair draft when the floor still fails.
    # Same seat, the failures listed verbatim; the repaired
    # draft faces the same gates. The lane's draft cap is 2
    # (initial + repair, claimed per-part since v26 F9),
    # and the anchored deadline still decides whether the
    # repair may start at all.
    quick_repair = False
    quick_repair_failures = []
    quick_repair_error = None
    if draft is not None and (ungrounded or mech_failures
                              or quantities or uncited_lb
                              or conflict_fail) \
            and not cap_hit and not draft_capped:
        _fails = _quick_failure_list(
            ungrounded, mech_failures, quantities, uncited_lb,
            conflict_fail)
        _may = True
        if deadline and time.monotonic() > deadline:
            _mark_cap()
            _may = False
        elif deadline and not _call_may_start(deadline):
            _mark_cap()
            _may = False
        elif _or_ask_cost() >= PER_ASK_CEILING_USD:
            # v26 (F1): the repair is a paid leg start; it
            # does not start at the ceiling either.
            cost_capped = True
            _may = False
        elif not _claim_quick_draft_call():
            draft_capped = True
            _may = False
        if _may:
            quick_repair = True
            quick_repair_failures = _fails
            _set_stage("drafting")
            _set_phase("drafting", round=2)
            if _trk is not None:
                _trk_set(_trk, "drafting", "active")
            _rinstr = (instr + "\n\n"
                       + _quick_repair_block(_fails)).strip()
            _rdraft = None
            try:
                _rdraft, _rprompt, _rserved, _rdg, _rfb = \
                    _generate_with_outage_retry(
                        _eng, _mdl, ctx["unit_q"],
                        ctx["hits"], _rinstr,
                        deadline=deadline)
            except Exception as e:
                quick_repair_error = str(e)[:200]
            if _rdraft is not None \
                    and _is_memo_shaped_draft(_rdraft):
                quick_repair_error = (
                    "the repair draft was memo-shaped and "
                    "was rejected by the draft shape guard")
                _rdraft = None
            if _rdraft is not None:
                draft = _rdraft
                prompt = _rprompt or prompt
                ctx["_dg"], ctx["_fb"] = _rdg, _rfb
                if _rserved:
                    _srv = _rserved.split(" (")[0].strip()
                    _actx0.draft_family = _model_family(_srv)
                    _actx0.served_models["draft"] = _srv
                    served = _rserved
                try:
                    draft, parsed, ungrounded, mech_failures, \
                        quantities, conflict_fail, uncited_lb, \
                        stripped = _gates_with_strip(draft)
                except Exception as e:
                    # v26 (F10): same fail-closed rule at the
                    # repair's re-gate.
                    gate_error = f"{type(e).__name__}: {e}"[:200]
                    draft = None
            if _trk is not None:
                _trk_set(_trk, "drafting", "done")
            _set_phase("final checks")
            if _trk is not None:
                _trk_set(_trk, "final_checks", "active")
    if _trk is not None:
        _trk_set(_trk, "final_checks", "done")
    _fig_checked = 0
    if draft is not None:
        try:
            _fig_checked = len(_claim_money(draft))
        except Exception:
            _fig_checked = 0
    checks = _empty_checks_block()
    checks["figures"]["checked"] = _fig_checked
    checks["figures"]["failed"] = [_fmt_money(v)
                                   for v in ungrounded]
    checks["citations"]["failed"] = list(uncited_lb)
    checks["registry"] = [e["id"] for e in
                          (ctx.get("registry_entries") or ())]
    judging = {"mode": "quick",
               "errors": [gate_error] if gate_error else [],
               "annotation": "Quick mode: deterministic checks "
                             "only (figures, citations, "
                             "quantities, arithmetic, mechanism "
                             "coverage, conflicts). No judge "
                             "pass ran."
                             + (" One repair draft ran."
                                if quick_repair else ""),
               "checks": checks}
    base = {"served": served, "fallback": ctx.get("_fb", False),
            "downgraded": ctx.get("_dg", False),
            "prompt": prompt, "rungs": 1 if attempted else 0,
            "judge_ms": 0, "refute_ms": 0,
            "cap_hit": cap_hit, "budget_cut": cap_hit,
            "cut_phase": cut_phase, "cost_capped": cost_capped,
            "draft_calls": _ask_ctx().draft_calls,
            "draft_capped": draft_capped,
            "assembly_guard": None,
            "draft_text": draft,
            "figure_check": {"ungrounded": list(ungrounded),
                             "regenerated": False},
            "divergence": None,
            "events": _events_since(ev0),
            "retr_pools": _retr_pools_since(rp0),
            "lead_repairs": 0,
            "quick_self_audit": True,
            "quick_repair": quick_repair,
            "quick_repair_failures": quick_repair_failures,
            "quick_repair_error": quick_repair_error,
            "stripped_sentences": stripped}
    _check_cancel()  # the Stop seam, again before shipping
    _ship = draft is not None and not ungrounded \
        and not mech_failures and not quantities \
        and not uncited_lb and not conflict_fail
    if _ship and _idk_shaped(draft):
        # v26.2 (fix 5): badge honesty, as in _run_unit — an
        # IDK-shaped text does not ship under the quick badge;
        # the unit IDKs below with the reason recorded.
        _ship = False
        idk_shaped_hit = True
    if _ship:
        answer, _fixes = _check_arithmetic(draft)
        return dict(base, answer=answer, badge="quick",
                    judging=judging, claims=[], missing=[],
                    gate=None)
    # IDK: assemble what is missing from the failed gates,
    # in _run_unit's own phrasing.
    missing, gate = [], "checking"
    if cap_hit:
        gate = "time"
        missing.append("the ask reached its time budget "
                       f"({budget_s} seconds) before a "
                       "checked answer could be assembled")
    if gen_error:
        missing.append("the answering model could not run "
                       f"({gen_error})")
    if gate_error:
        missing.append("the answer's checks could not run "
                       f"({gate_error})")
    if cost_capped:
        missing.append("the ask reached its per-ask cost "
                       f"ceiling (${PER_ASK_CEILING_USD:.2f}) "
                       "before a checked answer could be "
                       "assembled")
    if draft_capped:
        # v26 (F9): the cap that binds a Quick unit is its
        # own per-part budget, so the note names that.
        missing.append("this part reached its draft-call "
                       f"cap ({QUICK_UNIT_DRAFT_CAP} drafting "
                       "calls) before a checked answer could "
                       "be assembled")
    for f in mech_failures:
        missing.append("the answer did not establish: "
                       + f["label"])
    for f in quantities:
        missing.append("quantity check failed: " + f["label"])
    if uncited_lb:
        missing.append("factual sentences carried no citation: "
                       + "; ".join(f"\"{s[:120]}\""
                                   for s in uncited_lb[:3]))
    if ungrounded:
        missing.append("the draft used figures found in none of "
                       "the excerpts: " + ", ".join(
                           _fmt_money(v) for v in ungrounded[:4]))
    if idk_shaped_hit:
        missing.append("the draft stated that the documents "
                       "do not answer the question, so the "
                       "answer is an IDK rather than a verified "
                       "badge")
    if conflict_fail:
        missing.append("the draft did not surface both values "
                       "of a source conflict")
    return dict(base, answer=None, badge="idk",
                judging=judging, claims=[],
                missing=missing[:8], gate=gate)


def _idk_answer(missing, hits, lead):
    """The full IDK payload (operator decision): a plain refusal, what
    is missing, and the closest excerpts, inline."""
    lines = [lead, "",
             "I don't know based on the available documents.", ""]
    if missing:
        lines.append("What is missing:")
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")
    if hits:
        lines.append("Closest documents found:")
        for h in hits[:5]:
            lines.append(f"- [{h['title']}]({h['url']})")
    return "\n".join(lines)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FSSA SARA</title>
<link rel="icon" href="/icon.png">
<style>
:root{
  --bg:#020617; --surface-1:#0F172A; --surface-2:#1A1E2F; --surface-3:#1E293B;
  --hairline:#1E293B; --hairline-strong:#334155;
  --ink:#F8FAFC; --ink-secondary:#CBD5E1; --ink-tertiary:#94A3B8; --ink-disabled:#64748B;
  --accent:#2563EB; --accent-hover:#3B82F6; --success:#22C55E; --error:#EF4444; --warning:#F59E0B;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#F8FAFC; --surface-1:#FFFFFF; --surface-2:#F1F5F9; --surface-3:#E2E8F0;
    --hairline:#E2E8F0; --hairline-strong:#CBD5E1;
    --ink:#0F172A; --ink-secondary:#334155; --ink-tertiary:#64748B; --ink-disabled:#94A3B8;
    --accent:#2563EB; --accent-hover:#1D4ED8; --success:#16A34A; --error:#DC2626; --warning:#D97706;
  }
}
*{box-sizing:border-box; margin:0; padding:0;}
body{
  font-family:Inter,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink-secondary);
  line-height:1.6; min-height:100dvh; display:flex; flex-direction:column; align-items:center;
  padding:24px 16px;
}
.wrap{width:100%; max-width:720px;}
header{display:flex; align-items:center; gap:12px; margin-bottom:20px;}
header img{width:40px; height:40px; border-radius:10px;}
header h1{font-size:24px; font-weight:600; letter-spacing:-0.02em; color:var(--ink); line-height:1.2;}
header p{font-size:14px; color:var(--ink-tertiary); line-height:1.3;}
.card{background:var(--surface-1); border:1px solid var(--hairline); border-radius:12px; padding:20px;}
.askrow{display:flex; gap:12px; align-items:flex-start;}
.askrow textarea{flex:1;}
textarea{
  width:100%; background:var(--surface-2); border:1px solid var(--hairline-strong);
  border-radius:6px; color:var(--ink); font-size:16px; padding:12px; font-family:inherit;
  min-height:84px; resize:vertical;
}
select{
  background:var(--surface-2); border:1px solid var(--hairline-strong);
  border-radius:6px; color:var(--ink); font-size:15px; padding:10px; font-family:inherit; min-height:44px;
}
textarea:focus, select:focus{outline:2px solid var(--accent); border-color:var(--accent);}
button{
  background:var(--accent); color:#fff; border:none; border-radius:6px; padding:12px 24px;
  font-size:16px; font-weight:500; cursor:pointer; font-family:inherit; min-height:44px;
}
button:hover{background:var(--accent-hover);}
button:disabled{background:var(--ink-disabled); cursor:default;}
button.secondary{background:var(--surface-2); color:var(--ink); border:1px solid var(--hairline-strong); white-space:nowrap;}
button.secondary:hover{background:var(--surface-3);}
#askBtn{width:100%; height:52px; font-size:17px; margin-top:12px;}
.moderow{display:flex; align-items:center; gap:8px; margin-top:12px;}
.modebtn{flex:0 0 auto; min-height:44px; padding:6px 22px; font-size:15px;
  background:var(--surface-3); color:var(--ink-secondary);}
.modebtn.on{background:var(--accent); color:#fff; font-weight:600;}
.modehint{font-size:13px; color:var(--ink-tertiary);}
.freshrow{margin-top:10px; font-size:14px; color:var(--ink-secondary);}
.freshrow label{display:inline-flex; align-items:center; gap:8px; cursor:pointer; min-height:32px;}
.freshrow input{width:18px; height:18px;}
#panel{display:none; margin-top:16px; border-top:1px solid var(--hairline); padding-top:14px;}
#panel .why{font-size:13px; color:var(--ink-tertiary); margin-bottom:12px;}
.seatrow{display:flex; align-items:center; gap:12px; margin-bottom:10px;}
.seatrow label{flex:0 0 128px; font-size:14px; color:var(--ink-secondary);}
.seatrow select{flex:1;}
.seatrow.static span{font-size:14px; color:var(--ink-tertiary);}
#seatWarn{display:none; margin-top:6px; font-size:13px; color:var(--warning);}
#runCard{display:none; margin-top:16px;}
.timerRow{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:10px;}
#timerText{font-size:20px; color:var(--ink); font-variant-numeric:tabular-nums; font-weight:600;}
#stopBtn{padding:8px 18px; font-size:14px; min-height:40px;}
#passCaption{font-size:13px; color:var(--ink-tertiary); margin-bottom:8px;}
.partline{font-size:13px; color:var(--ink-secondary); padding:2px 0;}
.partline.dim{color:var(--ink-tertiary);}
.secrow{display:flex; align-items:center; gap:8px; font-size:13px; min-height:20px; padding:1px 0;}
.secrow .glyph{flex:0 0 18px; text-align:center; font-size:12px;}
.secrow .slabel{color:var(--ink-secondary);}
.secrow.active .slabel{color:var(--ink); font-weight:600;}
.secrow .sdetail{margin-left:auto; color:var(--ink-tertiary); font-size:12px; font-variant-numeric:tabular-nums; text-align:right;}
.st-waiting .glyph{color:var(--hairline-strong);}
.st-active .glyph{color:var(--accent); animation:pulse 1.6s ease-in-out infinite;}
.st-done .glyph{color:var(--success);}
.st-notneeded .glyph{color:var(--ink-tertiary);}
.st-notneeded .slabel{color:var(--ink-tertiary);}
.st-skipped .glyph{color:var(--warning);}
.st-skipped .slabel{color:var(--ink-tertiary);}
.st-failed .glyph{color:var(--error);}
@keyframes pulse{0%,100%{opacity:1;} 50%{opacity:0.35;}}
@media (prefers-reduced-motion: reduce){ .st-active .glyph{animation:none;} }
.ifneeded{margin-top:8px; border-top:1px dashed var(--hairline); padding-top:8px;}
.ifneeded .glabel{font-size:12px; color:var(--ink-tertiary); margin-bottom:4px;}
#draftCalls{margin-top:8px; font-size:12px; color:var(--ink-tertiary);}
#answer{margin-top:16px; display:none;}
#answer .card h2{font-size:16px; font-weight:600; color:var(--ink); margin-bottom:12px;}
#answerText{white-space:pre-wrap; font-size:16px; max-width:65ch;}
#sources{margin-top:16px;}
#sources h3{font-size:12px; font-weight:500; letter-spacing:0.05em; text-transform:uppercase;
  color:var(--ink-tertiary); margin-bottom:8px;}
#sources a{display:block; font-size:14px; color:var(--accent); text-decoration:none; margin-bottom:4px;}
#sources a:hover{text-decoration:underline;}
.badge{display:inline-block; font-size:12px; padding:2px 8px; border-radius:4px; margin-left:8px;
  vertical-align:middle;}
.badge.ok{background:var(--surface-3); color:var(--success);}
.badge.no{background:var(--surface-3); color:var(--warning);}
.badge.err{background:var(--surface-3); color:var(--error);}
#answeredIn{font-size:13px; color:var(--ink-tertiary); margin-left:10px;}
#idkNote{display:none; margin-top:8px; font-size:14px; color:var(--ink-secondary);}
#verifyNote{margin-top:12px; font-size:13px; color:var(--ink-tertiary);}
#pills{margin-top:10px;}
.pill{display:inline-block; font-size:12px; padding:2px 10px; border-radius:999px;
  background:var(--surface-3); color:var(--ink-secondary); margin:0 6px 6px 0;}
details#howChecked{margin-top:12px; font-size:13px; color:var(--ink-tertiary);}
details#howChecked summary{cursor:pointer; min-height:32px; display:flex; align-items:center;}
details#howChecked .hline{padding:2px 0 2px 12px; white-space:pre-wrap;}
#goneLine{display:none; margin-top:16px; font-size:14px; color:var(--ink-tertiary);}
footer{margin-top:24px; font-size:12px; color:var(--ink-tertiary); max-width:65ch;}
.sr-only{position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap;}
@media (max-width: 767px){
  body{padding:16px;}
  header img{width:32px; height:32px;}
  header h1{font-size:20px;}
  .askrow{flex-direction:column;}
  .askrow textarea{font-size:17px;}
  #modelsBtn{width:100%;}
  .seatrow label{flex-basis:104px;}
}
@media (max-width: 479px){
  header p{display:none;}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img src="/icon.png" alt="FSSA SARA icon">
    <div>
      <h1>FSSA SARA</h1>
      <p>Search And Retrieval Assistant</p>
    </div>
  </header>
  <div class="card">
    <div class="askrow">
      <textarea id="q" aria-label="Your question" placeholder="Ask about Medicaid, waivers, SNAP…"></textarea>
      <button id="modelsBtn" class="secondary" onclick="togglePanel()">Models</button>
    </div>
    <div id="panel">
      <div class="why">Pick a model per task. Hosted is the standard pipeline.</div>
      <div class="seatrow"><label for="seatDraft">Drafting</label><select id="seatDraft"></select></div>
      <div class="seatrow"><label for="seatExtract">Extracting claims</label><select id="seatExtract"></select></div>
      <div class="seatrow"><label for="seatJudge">Judging</label><select id="seatJudge"></select></div>
      <div class="seatrow static"><label>Final checks</label><span>automatic (no model)</span></div>
      <div class="seatrow"><label for="searchHelp">Search help</label>
        <select id="searchHelp"><option value="hosted" selected>Hosted</option><option value="off">Off</option></select></div>
      <div class="seatrow"><label for="webBranch">Web branch</label>
        <select id="webBranch"><option value="allow" selected>Allow</option><option value="never">Never</option></select></div>
      <div id="seatWarn"></div>
    </div>
    <div class="moderow" role="group" aria-label="Answer mode">
      <button id="modeQuick" class="modebtn" onclick="setMode('quick')" aria-pressed="true">Quick</button>
      <button id="modeDeep" class="modebtn" onclick="setMode('deep')" aria-pressed="false">Deep</button>
      <span class="modehint" id="modeHint"></span>
    </div>
    <button id="askBtn" onclick="ask()">Ask</button>
    <div class="freshrow"><label><input type="checkbox" id="fresh"> Fresh answer</label></div>
    <div id="goneLine">That run is no longer available.</div>
    <div class="card" id="runCard" style="margin-top:16px; padding:16px;">
      <div class="timerRow">
        <span id="timerText" role="timer" aria-live="off">Elapsed 0:00</span>
        <button id="stopBtn" class="secondary" onclick="stopRun()">Stop</button>
      </div>
      <div id="passCaption"></div>
      <div id="partLines"></div>
      <div id="sectionList" aria-live="polite" aria-atomic="false"></div>
      <div class="ifneeded" id="ifNeeded" style="display:none;">
        <div class="glabel">If needed</div>
        <div id="chipList"></div>
      </div>
      <div id="draftCalls"></div>
      <div class="sr-only" id="liveLine" aria-live="polite"></div>
    </div>
  </div>
  <div id="answer">
    <div class="card">
      <h2>Answer<span id="answerBadge"></span><span id="answeredIn"></span></h2>
      <div id="answerText"></div>
      <div id="idkNote">No answer in the sources could be verified.</div>
      <div id="pills"></div>
      <div id="verifyNote"></div>
      <details id="howChecked"><summary>How this was checked</summary><div id="howLines"></div></details>
      <div id="sources"><h3>Sources</h3><div id="sourceList"></div></div>
    </div>
  </div>
  <footer>Corpus updated 2026-10-12 · 5,234 documents (Indiana + federal). Check anything affecting benefits or eligibility with your caseworker or FSSA.</footer>
</div>
<script>
const $ = function(id){ return document.getElementById(id); };
// v24 (§6): the progress display's sections — five main-path
// rows in true time order, plus the "If needed" group. States
// come from the server's sections snapshot only; the page
// renders what the snapshot says and never guesses a state.
const MAIN_SECTIONS = ['retrieving','drafting','extracting','checking','final_checks'];
const OPT_SECTIONS = ['redraft2','redraft3','lead_repair','rescue','web_branch'];
const GLYPHS = {waiting:'○', active:'●', done:'✓', 'not-needed':'⊘', skipped:'▲', failed:'✕'};
let startStamp = null, timerInt = null, anchored = false;
let currentJob = null, lastSnapshot = null, prevStates = {};
let seatBlock = null;  // {seat, model} while a pick is unavailable

function fmtT(sec){
  if(sec === null || sec === undefined || isNaN(sec)) return '';
  sec = Math.max(0, Math.round(sec));
  return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
}
function fmtSecs(sec){
  if(sec === null || sec === undefined) return '';
  return sec >= 60 ? fmtT(sec) : (Math.round(sec) + 's');
}
function tickTimer(){
  if(!startStamp) return;
  $('timerText').textContent = 'Elapsed ' + fmtT((Date.now() - startStamp) / 1000);
}
function startTimer(){
  startStamp = Date.now(); anchored = false;
  tickTimer();
  if(timerInt) clearInterval(timerInt);
  timerInt = setInterval(tickTimer, 500);
}
function stopTimer(){ if(timerInt) clearInterval(timerInt); timerInt = null; }

// ---------- Models panel (§8) ----------
function seatPicks(){
  return {draft: $('seatDraft').value || 'hosted',
          extract: $('seatExtract').value || 'hosted',
          judge: $('seatJudge').value || 'hosted'};
}
function togglePanel(){
  const p = $('panel');
  p.style.display = (p.style.display === 'block') ? 'none' : 'block';
}
function modelsSummary(){
  const picks = seatPicks();
  const n = ['draft','extract','judge'].filter(k => picks[k] !== 'hosted').length;
  $('modelsBtn').textContent = n ? ('Models · ' + n + ' local') : 'Models';
}
async function loadModels(){
  try{
    const r = await fetch('/models');
    const d = await r.json();
    const fill = function(sel, opts){
      sel.innerHTML = '';
      (opts || []).forEach(function(o){
        const op = document.createElement('option');
        op.value = o.value; op.textContent = o.label;
        sel.appendChild(op);
      });
    };
    if(d.seats){
      fill($('seatDraft'), d.seats.draft);
      fill($('seatExtract'), d.seats.extract);
      fill($('seatJudge'), d.seats.judge);
    }
    window._installedModels = d.installed || [];
  }catch(e){ /* panel stays hosted-only; the server still guards */ }
  ['seatDraft','seatExtract','seatJudge'].forEach(function(id){
    $(id).addEventListener('change', onSeatChange);
  });
  modelsSummary();
}
async function onSeatChange(ev){
  modelsSummary();
  const sel = ev.target;
  const model = sel.value;
  seatBlock = null;
  $('seatWarn').style.display = 'none';
  if(model === 'hosted') return;
  // §8.5: validate + pre-warm at pick time. A model that does
  // not answer blocks Ask, naming the seat and the model.
  const seatName = sel.id === 'seatDraft' ? 'Drafting'
    : sel.id === 'seatExtract' ? 'Extracting claims' : 'Judging';
  sel.disabled = true;
  try{
    const r = await fetch('/models/warm', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({model: model})
    });
    const d = await r.json();
    if(!d.ok) throw new Error('unavailable');
  }catch(e){
    seatBlock = {seat: seatName, model: model};
    const w = $('seatWarn');
    w.textContent = seatName + ': ' + model + ' is not available right now. Pick another model for that seat.';
    w.style.display = 'block';
  }
  sel.disabled = false;
}

// ---------- Progress display (§6) ----------
function blankSnapshot(){
  const secs = {};
  MAIN_SECTIONS.concat(OPT_SECTIONS).forEach(function(id){
    secs[id] = {id:id, label:id, state:'waiting', seconds:null, detail:''};
  });
  secs.retrieving.state = 'active';
  const labels = {retrieving:'Retrieving', drafting:'Drafting', redraft2:'Redraft 2',
    redraft3:'Redraft 3', extracting:'Extracting claims', checking:'Checking',
    lead_repair:'Lead repair', final_checks:'Final checks', rescue:'Rescue', web_branch:'Web branch'};
  Object.keys(secs).forEach(function(k){ secs[k].label = labels[k]; });
  return {units:[{index:0, part:null, status:'active', elapsed_s:null,
                   caption:'Pass 1', sections:MAIN_SECTIONS.concat(OPT_SECTIONS).map(k => secs[k])}],
          draft_calls:0, draft_cap:4, parts:1};
}
function sectionMap(unit){
  const m = {};
  (unit.sections || []).forEach(function(s){ m[s.id] = s; });
  return m;
}
function announceFlips(snap){
  // Screen-reader announcements on state flips only (§6.2).
  const notes = [];
  (snap.units || []).forEach(function(u){
    (u.sections || []).forEach(function(s){
      const key = u.index + ':' + s.id;
      const prev = prevStates[key];
      if(prev && prev !== s.state){
        if(s.state === 'active') notes.push(s.label + ' started');
        else if(s.state === 'not-needed') notes.push(s.label + ' not needed');
        else if(s.state === 'done') notes.push(s.label + ' done');
        else if(s.state === 'skipped') notes.push(s.label + ' skipped');
      }
      prevStates[key] = s.state;
    });
  });
  if(notes.length) $('liveLine').textContent = notes[notes.length - 1];
}
function renderRun(jp){
  const snap = jp.sections || lastSnapshot || blankSnapshot();
  lastSnapshot = snap;
  announceFlips(snap);
  const units = snap.units || [];
  let active = units.filter(u => u.status === 'active')[0]
    || units[units.length - 1] || blankSnapshot().units[0];
  // Caption: the pass, plus the part when bundled (§6.1).
  let cap = active.caption || '';
  if((snap.parts || units.length) > 1){
    cap = 'Part ' + (active.index + 1) + ' of ' + (snap.parts || units.length)
        + (cap ? ' · ' + cap : '');
  }
  $('passCaption').textContent = cap;
  // The other parts as one-liners (§6.1).
  const pl = $('partLines');
  pl.innerHTML = '';
  if(units.length > 1){
    units.forEach(function(u){
      if(u === active) return;
      const div = document.createElement('div');
      if(u.status === 'done'){
        div.className = 'partline';
        div.textContent = 'Part ' + (u.index + 1) + ' · done · ' + fmtT(u.elapsed_s);
      } else if(u.status === 'active'){
        div.className = 'partline';
        div.textContent = 'Part ' + (u.index + 1) + ' · running';
      } else {
        div.className = 'partline dim';
        div.textContent = 'Part ' + (u.index + 1) + ' · waiting';
      }
      pl.appendChild(div);
    });
  }
  // Main-path rows.
  const m = sectionMap(active);
  const list = $('sectionList');
  list.innerHTML = '';
  MAIN_SECTIONS.forEach(function(id){
    const s = m[id]; if(!s) return;
    list.appendChild(sectionRow(s, jp));
  });
  // If-needed chips.
  const chips = $('chipList');
  chips.innerHTML = '';
  let anyOpt = false;
  OPT_SECTIONS.forEach(function(id){
    const s = m[id]; if(!s) return;
    anyOpt = true;
    chips.appendChild(sectionRow(s, jp));
  });
  $('ifNeeded').style.display = anyOpt ? 'block' : 'none';
  if(typeof snap.draft_calls === 'number'){
    $('draftCalls').textContent = 'Draft calls: ' + snap.draft_calls + ' of ' + (snap.draft_cap || 4);
  }
}
function sectionRow(s, jp){
  const div = document.createElement('div');
  div.className = 'secrow st-' + s.state;
  const g = document.createElement('span');
  g.className = 'glyph';
  g.textContent = GLYPHS[s.state] || '○';
  const lab = document.createElement('span');
  lab.className = 'slabel';
  lab.textContent = s.label;
  const det = document.createElement('span');
  det.className = 'sdetail';
  let txt = '';
  if(s.state === 'done') txt = fmtSecs(s.seconds);
  else if(s.state === 'not-needed') txt = 'Not needed';
  else if(s.state === 'skipped') txt = 'Skipped' + (s.detail ? ' · ' + s.detail : '');
  else if(s.state === 'active'){
    txt = s.detail || '';
    // The fact-check line rides the Checking row (§6.2).
    if(s.id === 'checking' && jp && jp.phase && jp.phase.check) txt = jp.phase.check;
    if(s.id === 'extracting' && jp && jp.phase && jp.phase.check) txt = jp.phase.check;
  }
  det.textContent = txt;
  div.appendChild(g); div.appendChild(lab); div.appendChild(det);
  return div;
}

// ---------- Run lifecycle (§5) ----------
async function stopRun(){
  if(!currentJob) return;
  const b = $('stopBtn');
  b.disabled = true; b.textContent = 'Stopping…';
  try{ await fetch('/ask/cancel/' + currentJob, {method:'POST'}); }
  catch(e){ /* the poll will surface the outcome either way */ }
}
// ---------- Quick / Deep mode (§v25) ----------
// Quick is the default. The pick persists in localStorage
// on this browser/profile; a fresh profile starts Quick.
let askMode = 'quick';
function setMode(m){
  askMode = (m === 'deep') ? 'deep' : 'quick';
  try{ localStorage.setItem('sara_mode', askMode); }catch(e){}
  paintMode();
}
function paintMode(){
  const q = askMode === 'quick';
  $('modeQuick').classList.toggle('on', q);
  $('modeQuick').setAttribute('aria-pressed', q ? 'true' : 'false');
  $('modeDeep').classList.toggle('on', !q);
  $('modeDeep').setAttribute('aria-pressed', !q ? 'true' : 'false');
  $('modeHint').textContent = q
    ? 'One draft, code checks only. Fastest.'
    : 'Full judging pipeline. Slowest, most verified.';
}
function initMode(){
  let stored = null;
  try{ stored = localStorage.getItem('sara_mode'); }catch(e){}
  askMode = (stored === 'deep') ? 'deep' : 'quick';
  paintMode();
}
function buildPayload(q){
  const payload = {question:q, model:'qwen3.5:9b', mode: askMode,
                   fresh: $('fresh').checked};
  const picks = seatPicks();
  if(picks.draft !== 'hosted' || picks.extract !== 'hosted' || picks.judge !== 'hosted'){
    payload.seat_models = picks;
  }
  if($('searchHelp').value !== 'hosted') payload.search_help = $('searchHelp').value;
  if($('webBranch').value !== 'allow') payload.web_branch = $('webBranch').value;
  return payload;
}
async function ask(){
  // A second submit while an ask is running is ignored (v18).
  if(window._asking) return;
  const q = $('q').value.trim();
  if(!q) return;
  if(seatBlock){
    $('panel').style.display = 'block';
    $('seatWarn').style.display = 'block';
    return;
  }
  window._asking = true;
  $('askBtn').disabled = true;
  $('answer').style.display = 'none';
  $('goneLine').style.display = 'none';
  $('runCard').style.display = 'block';
  $('stopBtn').disabled = false;
  $('stopBtn').textContent = 'Stop';
  prevStates = {}; lastSnapshot = null;
  renderRun({sections: blankSnapshot()});
  startTimer();
  const payload = buildPayload(q);
  let d = null, jobId = null;
  try{
    try{
      const r0 = await fetch('/ask?async=1', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const j0 = await r0.json();
      if(!j0.job_id) throw new Error('no job id');
      jobId = j0.job_id; currentJob = jobId;
      sessionStorage.setItem('sara_job', jobId);
      d = await pollJob(jobId);
    }catch(jobErr){
      // Once a job exists, NEVER re-POST synchronously (v18:
      // that re-ran and re-billed the whole pipeline).
      if(jobId || d) throw jobErr;
      const r = await fetch('/ask', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      d = await r.json();
      if(d && d.error) throw new Error(d.error);
      finishRun(d, null);
      return;
    }
  }catch(e){
    showError(e.message);
    return;
  }
}
async function pollJob(jobId){
  for(let i = 0; i < 400; i++){
    await new Promise(res => setTimeout(res, 2000));
    let jp = null;
    try{
      const rp = await fetch('/ask/result/' + jobId);
      jp = await rp.json();
    }catch(pollErr){ continue; }  // a failed poll is tolerated (v18)
    if(jp.error && !jp.status){ showGone(); return null; }
    if(!anchored && typeof jp.elapsed_s === 'number'){
      // Anchor the timer to the SERVER's start (§5.1): the
      // displayed elapsed is the server's truth, and a
      // reloaded page re-anchors the same way.
      startStamp = Date.now() - jp.elapsed_s * 1000;
      anchored = true; tickTimer();
    }
    if(jp.status === 'done'){ finishRun(jp.result, jp); return jp.result; }
    if(jp.status === 'cancelled'){ finishCancelled(jp.result); return jp.result; }
    if(jp.status === 'error'){ throw new Error(jp.error || 'job failed'); }
    renderRun(jp);
  }
  throw new Error('timed out waiting for the answer');
}
function endRunChrome(){
  stopTimer();
  window._asking = false;
  currentJob = null;
  sessionStorage.removeItem('sara_job');
  $('askBtn').disabled = false;
  $('runCard').style.display = 'none';
}
function finishRun(d, jp){
  endRunChrome();
  if(!d){ showGone(); return; }
  const wall = (typeof d.wall_s === 'number') ? d.wall_s
    : (startStamp ? (Date.now() - startStamp) / 1000 : null);
  $('answeredIn').textContent = wall !== null ? ('· Answered in ' + fmtT(wall)) : '';
  $('answerText').textContent = d.answer || ('Error: ' + (d.error || 'unknown'));
  // §7.5 + v25: the badge strings (six Deep + Quick).
  const badgeMap = {
    quick: ['no', 'Quick answer'],
    checked: ['ok', 'Checked'],
    verified_web: ['ok', 'Checked · web sources'],
    checked_det: ['no', 'Checked · figures and citations'],
    verified_web_reduced: ['no', 'Checked · web · figures and citations'],
    partial: ['no', 'Partial · partly verified'],
    idk: ['err', 'No verified answer'],
    code: ['ok', 'Code answer · computed from cited figures']
  };
  const bm = badgeMap[d.badge];
  $('answerBadge').innerHTML = bm
    ? '<span class="badge ' + bm[0] + '">' + bm[1] + '</span>' : '';
  $('idkNote').style.display = (d.badge === 'idk') ? 'block' : 'none';
  const vn = $('verifyNote');
  vn.textContent = (d.judging && d.judging.annotation) ? d.judging.annotation
    : (d.judging ? ('Verification: ' + d.judging.mode) : '');
  // §6.5: unusual-path pills.
  const pills = $('pills');
  pills.innerHTML = '';
  ((d.progress && d.progress.pills) || []).forEach(function(t){
    const sp = document.createElement('span');
    sp.className = 'pill'; sp.textContent = t;
    pills.appendChild(sp);
  });
  // §6.5: the "How this was checked" disclosure.
  const hl = $('howLines');
  hl.innerHTML = '';
  const snap = (jp && jp.sections) || lastSnapshot;
  if(snap && snap.units && snap.units.length){
    snap.units.forEach(function(u){
      const div = document.createElement('div');
      div.className = 'hline';
      const states = (u.sections || []).map(function(s){
        return s.label + ' ' + (GLYPHS[s.state] || s.state);
      }).join(' · ');
      div.textContent = (snap.units.length > 1
        ? ('Part ' + (u.index + 1) + ' — ') : '') + states;
      hl.appendChild(div);
    });
    if(typeof snap.draft_calls === 'number'){
      const div = document.createElement('div');
      div.className = 'hline';
      div.textContent = 'Draft calls: ' + snap.draft_calls + ' of ' + (snap.draft_cap || 4);
      hl.appendChild(div);
    }
  } else if(d.progress && d.progress.parts){
    d.progress.parts.forEach(function(pt, i){
      const div = document.createElement('div');
      div.className = 'hline';
      div.textContent = (d.progress.parts.length > 1 ? ('Part ' + (i + 1) + ' — ') : '')
        + 'badge: ' + (pt.badge || '—') + ' · ' + pt.rungs + ' ladder rung(s)'
        + (pt.rescue ? ' · rescue ' + pt.rescue : '')
        + (pt.web ? ' · web branch ' + pt.web : '')
        + (pt.budget_cut ? ' · budget cut' : '');
      hl.appendChild(div);
    });
  }
  $('howChecked').style.display = hl.children.length ? 'block' : 'none';
  // Sources (§7 item 10 strings).
  const sl = $('sourceList');
  sl.innerHTML = '';
  (d.hits || []).forEach(function(h){
    const a = document.createElement('a');
    a.href = h.url; a.target = '_blank'; a.rel = 'noopener';
    a.textContent = h.title;
    const b = document.createElement('span');
    if(h.web){
      b.className = 'badge ' + (h.verified ? 'ok' : 'no');
      b.textContent = 'live page · ' + (h.fetched || '');
    } else {
      b.className = 'badge ' + (h.verified ? 'ok' : 'no');
      b.textContent = h.verified ? 'verified' : 'not checked';
    }
    a.appendChild(b);
    sl.appendChild(a);
  });
  $('answer').style.display = 'block';
}
function finishCancelled(d){
  endRunChrome();
  const wall = (d && typeof d.wall_s === 'number') ? d.wall_s
    : (startStamp ? (Date.now() - startStamp) / 1000 : 0);
  $('answerBadge').innerHTML = '';
  $('answeredIn').textContent = '· Stopped after ' + fmtT(wall);
  $('answerText').textContent = 'Stopped. No answer was sent.';
  $('idkNote').style.display = 'none';
  $('verifyNote').textContent = '';
  $('pills').innerHTML = '';
  $('howChecked').style.display = 'none';
  $('sourceList').innerHTML = '';
  $('answer').style.display = 'block';
}
function showError(msg){
  endRunChrome();
  $('answerBadge').innerHTML = '<span class="badge err">Error</span>';
  $('answeredIn').textContent = startStamp
    ? ('· Ran for ' + fmtT((Date.now() - startStamp) / 1000)) : '';
  $('answerText').textContent = 'Error: ' + msg;
  $('idkNote').style.display = 'none';
  $('verifyNote').textContent = '';
  $('pills').innerHTML = '';
  $('howChecked').style.display = 'none';
  $('sourceList').innerHTML = '';
  $('answer').style.display = 'block';
}
function showGone(){
  endRunChrome();
  $('goneLine').style.display = 'block';
}
// §5.3: reload / resume — a stored job id re-polls on load.
window.addEventListener('DOMContentLoaded', async function(){
  initMode();
  loadModels();
  const jid = sessionStorage.getItem('sara_job');
  if(!jid) return;
  try{
    const r = await fetch('/ask/result/' + jid);
    const jp = await r.json();
    if(jp.status === 'running'){
      window._asking = true;
      $('askBtn').disabled = true;
      $('runCard').style.display = 'block';
      currentJob = jid;
      startTimer();
      if(typeof jp.elapsed_s === 'number'){
        startStamp = Date.now() - jp.elapsed_s * 1000;
        anchored = true; tickTimer();
      }
      renderRun(jp);
      pollJob(jid).catch(function(e){ showError(e.message); });
    } else if(jp.status === 'done'){
      startStamp = Date.now(); finishRun(jp.result, jp);
    } else if(jp.status === 'cancelled'){
      startStamp = Date.now(); finishCancelled(jp.result);
    } else {
      showGone();
    }
  }catch(e){ showGone(); }
});
$('q').addEventListener('keydown', function(e){
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); ask(); }
});
</script>
</body>
</html>
"""


# --- v18 (§E): ask-record persistence --------------------------------
# Every completed ask (and every async job's lifecycle) appends
# one JSON line to the ask log: the question, the badge, the
# cost, the panel record, and PER UNIT the judged draft text
# plus every claim's full verdict record (per-seat verdicts,
# serving models, family coverage) and the unit's hits. This is
# what the judge-only replay harness (replay_harness.py)
# consumes, and what a future incident reconstruction reads
# instead of brackets. Append-only, lock-guarded, never raises:
# a logging failure must never fail an ask.
ASK_LOG_PATH = os.environ.get("FSSA_ASK_LOG", "ask_records.jsonl")
_ASK_LOG_LOCK = threading.Lock()


def _record_ask(record):
    try:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _ASK_LOG_LOCK:
            with open(ASK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# --- v27.1: the front door ----------------------------------------
# A code front door in front of the AI lanes (design:
# v27/DESIGN.md front-door section; classification + row
# provenance: v27/FRONT_DOOR_DATA.md). Facts live ONLY in
# front_door_data.json beside this file; the code below is five
# general engines plus a conservative router. A missing or
# invalid data file disables the door and the lanes behave
# exactly as v27. The door claims a question ONLY when a row's
# trigger phrases all match, the row is inside its effective
# window and before its review-by date, every input the engine
# needs is present or a row constant, and every dollar amount
# present can be assigned to an input unambiguously. Bundled
# (multi-part) and discovery questions never claim. Everything
# else falls through to Quick/Deep untouched. Every decision
# is appended to front_door_log.jsonl (the expansion queue).
# Claimed answers ship under the badge "code" with their
# figures, arithmetic, and source line in the answer text;
# they cost $0 and never enter the AI answer cache.
#
# v27.2 (BUG-1): the router no longer claims in file order.
# It collects every candidate whose triggers match AND whose
# engine can answer, then applies a specificity guard:
# candidates are scored by (matched phrase count, matched
# phrase characters, longest matched first-group phrase);
# rows flagged "general" in the data (fs_annual_cap) rank
# below every specific row and are blocked outright when
# the question names a service or topic another row's
# first trigger group covers; an exact specificity tie
# between specific rows falls through rather than guess.
# Trigger phrases were widened for all 21 rows in the data
# (natural phrasings, article and word-order variants,
# plurals, plain-words synonyms); the conjunctive
# all-groups structure is unchanged.

_FRONT_DOOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "front_door_data.json")
_FRONT_DOOR_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "front_door_log.jsonl")
_FRONT_DOOR_LOCK = threading.Lock()
_FRONT_DOOR_ROWS = []
_FRONT_DOOR_STATE = {"loaded": False, "rows": 0, "error": None}


def _front_door_load():
    global _FRONT_DOOR_ROWS
    try:
        with open(_FRONT_DOOR_PATH, encoding="utf-8") as f:
            doc = json.load(f)
        rows = doc["rows"]
        for r in rows:
            for k in ("id", "engine", "values", "source",
                      "triggers"):
                if k not in r:
                    raise ValueError("row missing " + k)
        _FRONT_DOOR_ROWS = rows
        _FRONT_DOOR_STATE.update(
            {"loaded": True, "rows": len(rows), "error": None})
    except Exception as e:
        _FRONT_DOOR_ROWS = []
        _FRONT_DOOR_STATE.update(
            {"loaded": False, "rows": 0, "error": str(e)[:200]})


_front_door_load()


def _front_door_health():
    return dict(_FRONT_DOOR_STATE)


def _fd_log(entry):
    try:
        entry = dict(entry, ts=time.time())
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _FRONT_DOOR_LOCK:
            with open(_FRONT_DOOR_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def _fd_money(v):
    v = float(v)
    if v == int(v):
        return "$" + format(int(v), ",")
    return "$" + format(v, ",.2f")


def _fd_norm(q):
    return " " + re.sub(r"\s+", " ", (q or "").lower()) + " "


def _fd_triggers_match(row, qn):
    for phrase in row.get("exclude", ()):
        if phrase in qn:
            return False
    for group in row["triggers"]["all"]:
        if not any(p in qn for p in group):
            return False
    return True


# v27.2 specificity guard (BUG-1 in v27.1/BUGS.md). A row
# flagged "general" in the data (fs_annual_cap) answers
# program-general questions; it may NOT claim a question
# that names a specific service or topic another row
# covers. The detectors are the other rows' FIRST
# trigger-group phrases (the group that names each row's
# own topic), minus program words too broad to name a
# service and phrases too short to mean anything alone.
_FD_GENERAL_STOPWORDS = frozenset(("waiver", "able", "hip"))


def _fd_match_specificity(row, qn):
    """Specificity of a matched row for this question:
    (matched phrase count, matched phrase chars, longest
    matched first-group phrase). More matched topic
    language is a more specific claim on the question."""
    count = 0
    chars = 0
    first_best = 0
    for gi, group in enumerate(row["triggers"]["all"]):
        hits = [p for p in group if p in qn]
        count += len(hits)
        chars += sum(len(p) for p in hits)
        if gi == 0 and hits:
            first_best = max(len(p) for p in hits)
    return (count, chars, first_best)


def _fd_names_covered_topic(qn, general_row):
    """True when the question names a service/topic that
    some specific row's first trigger group covers: the
    general row must not claim it. A phrase that merely
    names the general row's own program (it contains, or
    is contained in, one of the general row's first-group
    phrases) is not a service name and never blocks."""
    gen_phrases = []
    for row in _FRONT_DOOR_ROWS:
        if row.get("general"):
            gen_phrases.extend(row["triggers"]["all"][0])
    for row in _FRONT_DOOR_ROWS:
        if row is general_row or row.get("general"):
            continue
        groups = row["triggers"]["all"]
        if not groups:
            continue
        for phrase in groups[0]:
            if len(phrase) < 4 or phrase in _FD_GENERAL_STOPWORDS:
                continue
            if any(gp in phrase or phrase in gp
                   for gp in gen_phrases):
                continue
            if phrase in qn:
                return True
    return False


def _fd_row_active(row, today):
    if row.get("review_by") and today > row["review_by"]:
        return False
    eff = row.get("effective") or {}
    if eff.get("from") and today < eff["from"]:
        return False
    if eff.get("to") and today > eff["to"]:
        return False
    return True


_FD_AMT_RE = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
# v30 (amount parse): typo'd and plain voices state money
# without the $ ("we have 340000 in savings"). A bare 5-6
# digit integer is accepted as an amount ONLY when the
# question carries money context (the markers below) and
# the digits are not already part of a $-matched amount.
# 5-6 digits keeps years, ages, ZIPs, and counts out; the
# engines stay conservative on top (an amount that cannot
# be assigned still falls through).
_FD_BARE_AMT_RE = re.compile(r"(?<![\d$,.])(\d{5,6})(?![\d,.])")
_FD_MONEY_CONTEXT = ("$", "dollar", "dollers", "asset",
                     "savings", "saved", "bank", "money",
                     "countable", "worth", "estate", "account")
# v30 fix round (SP-5): a bare 3-6 digit number is also an
# amount when its OWN clause carries a money marker (the
# markers below) — "his monthly income is 2600 and mine is
# 900" states both incomes without a $. A 4-digit number in
# the year range 1900-2099 NEVER parses this way (a bare
# year is a year, not money), and a % following digit run
# keeps percentages out.
_FD_BARE_AMT_RE2 = re.compile(
    r"(?<![\d$,.])(\d{3,6})(?!\d|,\d|\.\d|%)")
_FD_CLAUSE_MONEY = _FD_MONEY_CONTEXT + (
    "income", "monthly", "a month", "per month", "earnings",
    "pension", "social security", "paycheck")
_FD_SPOUSE_WORDS = ("wife", "husband", "spouse")
_FD_PATIENT_WORDS = ("my income", "i get", "i receive", "i have",
                     "patient", "nursing home", "my check",
                     "my benefit")
# v30 (spousal-scenario assignment, Workstream C note):
# when the community spouse asks, the PATIENT's income is
# stated in the third person ("he gets $2,600 a month")
# and the asker's own in the first person ("i get $900").
# The base lists read first person as the patient (the
# applicant-asks framing), so that shape assigned nothing
# and the liability engine could never prove (U03-P /
# R-B04). The third-person phrases below mark a patient
# amount; a first-person amount flips to the spouse ONLY
# when a third-person patient amount exists AND the
# question establishes the third person as the one
# entering care (the care signals) — without the signal
# the framing is ambiguous and the assignment stays
# conservative (None, fall through).
_FD_PATIENT_THIRD = ("he gets", "she gets", "he receives",
                     "she receives", "his income",
                     "her income", "his monthly income",
                     "her monthly income", "his check",
                     "her check", "his benefit", "her benefit",
                     "his pension", "her pension",
                     "his social security",
                     "her social security")
_FD_FIRST_PERSON = ("my income", "i get", "i receive",
                    "i have", "my check", "my benefit",
                    "mine is")
_FD_THIRD_CARE_SIGNALS = (
    "husband is going into", "husband is entering",
    "husband's going into", "hubby's going into",
    "hubby is going into", "wife is going into",
    "wife is entering", "wife's going into",
    "he is going into", "she is going into",
    "he's going into", "she's going into",
    "husband is in a nursing home",
    "wife is in a nursing home")
# v30 fix round (SP-6): when the asker names THEMSELF the
# community spouse, the framing inverts the base lists:
# the third person (the husband/wife in the nursing home)
# is the PATIENT, and first-person amounts are the SPOUSE's
# own income. Without this, "my husband in the nursing
# home gets $2,600" assigned the patient amount to the
# spouse on the bare word "husband" and swapped the
# derivation (allocation $105 for $1,805).
_FD_COMMUNITY_ASKER = ("i'm the community spouse",
                       "i am the community spouse")
_FD_SHELTER_WORDS = ("rent", "mortgage", "shelter", "housing",
                     "utilities", "utility")


def _fd_amounts(question):
    """Dollar amounts with their clause context:
    [(value, clause_text_lower)]. Clauses are the spans
    between sentence punctuation, found by offset."""
    ql = (question or "").lower()
    spans = []
    start = 0
    for sm in re.finditer(r"[.,;]", ql):
        spans.append((start, sm.start()))
        start = sm.end()
    spans.append((start, len(ql)))
    found = []  # (offset, value)

    def _clause(off):
        for a, b in spans:
            if a <= off < b:
                return ql[a:b]
        return ""

    for m in _FD_AMT_RE.finditer(ql):
        try:
            val = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        found.append((m.start(), m.end(), val))
    if any(w in ql for w in _FD_MONEY_CONTEXT):
        # v30: bare amounts under money context. A bare
        # token overlapping a $-matched span is the same
        # amount, never a second one.
        for m in _FD_BARE_AMT_RE.finditer(ql):
            if any(a <= m.start() and m.end() <= b
                   for a, b, _v in found):
                continue
            try:
                val = float(m.group(1))
            except Exception:
                continue
            found.append((m.start(), m.end(), val))
    # v30 fix round: clause-marked bare amounts (SP-5).
    # Admitted amounts carry a narrow WINDOW as their
    # clause (24 chars back, 10 forward) instead of the
    # punctuation clause: two incomes stated in one clause
    # ("his monthly income is 2600 and mine is 900") share
    # the punctuation clause, and the liability assignment
    # reads each amount's role off its own window.
    window_spans = set()
    for m in _FD_BARE_AMT_RE2.finditer(ql):
        if any(a <= m.start() and m.end() <= b
               for a, b, _v in found):
            continue
        try:
            val = float(m.group(1))
        except Exception:
            continue
        if len(m.group(1)) == 4 and 1900 <= val <= 2099:
            continue  # a bare year is never money
        if not any(w in _clause(m.start())
                   for w in _FD_CLAUSE_MONEY):
            continue
        found.append((m.start(), m.end(), val))
        window_spans.add((m.start(), m.end()))
    found.sort()
    out = []
    for off, end, val in found:
        if (off, end) in window_spans:
            out.append((val, ql[max(0, off - 24):end + 10]))
        else:
            out.append((val, _clause(off)))
    return out


def _fd_assign_liability(amounts, question=""):
    """Assign amounts to (patient, spouse, shelter). Returns a
    dict with any of the keys, or None when an amount cannot be
    assigned unambiguously (the conservative fall-through)."""
    # v30: the spousal-scenario flip (see _FD_PATIENT_THIRD).
    qn = _fd_norm(question)
    has_third = any(any(w in c for w in _FD_PATIENT_THIRD)
                    for _v, c in amounts)
    flip = has_third and any(
        s in qn for s in _FD_THIRD_CARE_SIGNALS)
    community_asker = any(
        s in qn for s in _FD_COMMUNITY_ASKER)

    def _role(clause):
        if any(w in clause for w in _FD_SHELTER_WORDS):
            return "shelter"
        if community_asker:
            # The asker IS the community spouse: their own
            # (first-person) amounts are the spouse income;
            # every third-person amount is the patient's.
            if any(w in clause for w in _FD_FIRST_PERSON):
                return "spouse"
            if any(w in clause for w in _FD_SPOUSE_WORDS) \
                    or any(w in clause
                           for w in _FD_PATIENT_THIRD) \
                    or any(w in clause
                           for w in _FD_PATIENT_WORDS):
                return "patient"
            return None
        if any(w in clause for w in _FD_SPOUSE_WORDS):
            return "spouse"
        if any(w in clause for w in _FD_PATIENT_THIRD):
            return "patient"
        if any(w in clause for w in _FD_PATIENT_WORDS):
            if flip and any(w in clause
                            for w in _FD_FIRST_PERSON):
                return "spouse"
            return "patient"
        if flip and any(w in clause
                        for w in _FD_FIRST_PERSON):
            # v30: a first-person marker that is not itself
            # a base patient word ("mine is $900") reaches
            # the flip only through this branch — outside
            # the flip it stays unassigned, as before.
            return "spouse"
        return None

    patient = spouse = shelter = None
    for val, clause in amounts:
        role = _role(clause)
        if role is None:
            return None
        if role == "shelter":
            if shelter is not None:
                return None
            shelter = val
        elif role == "spouse":
            if spouse is not None:
                return None
            spouse = val
        else:
            if patient is not None:
                return None
            patient = val
    out = {}
    if patient is not None:
        out["patient_income"] = patient
    if spouse is not None:
        out["spouse_income"] = spouse
    if shelter is not None:
        out["shelter_costs"] = shelter
    return out


def _fd_source_line(row):
    eff = row.get("effective") or {}
    win = ""
    if eff.get("from") or eff.get("to"):
        win = (" Figures effective "
               + (eff.get("from") or "...") + " to "
               + (eff.get("to") or "open") + ".")
    return ("Source: " + row["source"] + "." + win
            + " Verified " + str(row.get("verified_on"))
            + "; next review by " + str(row.get("review_by"))
            + ". Answered by code from this data row; no AI "
              "draft was used.")


def _fd_engine_split_and_clamp(row, question, amounts):
    v = row["values"]
    lines = []
    inputs = {}
    if amounts:
        if len(amounts) != 1:
            return None
        assets = amounts[0][0]
        inputs["assets"] = assets
        half = assets * float(v["fraction"])
        csra = max(float(v["min"]), min(float(v["max"]), half))
        lines.append(
            "The community spouse resource allowance (CSRA) is "
            + _fd_money(csra) + ".")
        lines.append(
            "How it is figured: one half of the couple's "
            "combined countable assets of " + _fd_money(assets)
            + " is " + _fd_money(half) + ", and the allowance is "
            "that half clamped to a minimum of "
            + _fd_money(v["min"]) + " and a maximum of "
            + _fd_money(v["max"]) + " (2026).")
        if csra != half:
            lines.append(
                "The clamp is what sets the answer here: the raw "
                "half falls outside the allowed range.")
    else:
        ex = float(v["example_assets"])
        ex_csra = max(float(v["min"]),
                      min(float(v["max"]), ex * float(v["fraction"])))
        lines.append(
            "The community spouse resource allowance (CSRA) is "
            "one half of the couple's combined countable assets, "
            "clamped to a minimum of " + _fd_money(v["min"])
            + " and a maximum of " + _fd_money(v["max"])
            + " (2026).")
        lines.append(
            "Example: on " + _fd_money(ex) + " of countable "
            "assets, the allowance is " + _fd_money(ex_csra)
            + ". The exact figure needs the couple's countable "
            "assets at the snapshot date; nothing is subtracted "
            "before halving.")
    return "\n\n".join(lines), inputs


def _fd_engine_liability(row, question, amounts):
    v = row["values"]
    qn = _fd_norm(question)
    assigned = _fd_assign_liability(amounts, question)
    if assigned is None:
        return None
    inputs = dict(assigned)
    pna = float(v["pna"])
    mn_min = float(v["mmmna_min"])
    mn_max = float(v["mmmna_max"])
    shelter_std = float(v["shelter_standard"])
    wants_range = (not amounts) and any(
        t in qn for t in ("mmmna", "monthly maintenance",
                         "allocation"))
    if wants_range:
        lines = [
            "The monthly maintenance needs allowance (MMMNA) "
            "lets the community spouse keep between "
            + _fd_money(mn_min) + " and " + _fd_money(mn_max)
            + " per month of the institutionalized spouse's "
            "income (2026).",
            "Where it lands in that range depends on the "
            "spouse's own income and the couple's housing "
            "costs: the target is the minimum of "
            + _fd_money(mn_min) + " plus any shelter costs "
            "above the " + _fd_money(shelter_std)
            + " shelter standard, capped at "
            + _fd_money(mn_max)
            + ", and the allocation is the part of that target "
            "the spouse's own income does not already cover. "
            "With both incomes (and any shelter costs) the "
            "door computes the exact allocation and the "
            "patient liability.",
        ]
        return "\n\n".join(lines), inputs
    if "spouse_income" in inputs and "patient_income" not in inputs:
        spouse_inc = inputs["spouse_income"]
        target = mn_min + max(
            0.0, inputs.get("shelter_costs", shelter_std)
            - shelter_std)
        target = max(mn_min, min(mn_max, target))
        alloc = max(0.0, target - spouse_inc)
        lines = [
            "The spousal allocation is " + _fd_money(alloc)
            + " per month.",
            "How it is figured: the MMMNA target is "
            + _fd_money(target) + " (the 2026 minimum of "
            + _fd_money(mn_min)
            + (" plus excess shelter costs" if inputs.get(
                "shelter_costs") else "")
            + ", capped at " + _fd_money(mn_max)
            + "), minus the spouse's own income of "
            + _fd_money(spouse_inc) + ". The patient liability "
            "also needs the institutionalized spouse's income.",
        ]
        return "\n\n".join(lines), inputs
    if "patient_income" not in inputs:
        return None
    lines = []
    patient = inputs["patient_income"]
    alloc = None
    if "spouse_income" in inputs:
        target = mn_min + max(
            0.0, inputs.get("shelter_costs", shelter_std)
            - shelter_std)
        target = max(mn_min, min(mn_max, target))
        alloc = max(0.0, target - inputs["spouse_income"])
    liability = patient - pna - (alloc or 0.0)
    if liability < 0:
        liability = 0.0
    lines.append(
        "The patient liability is " + _fd_money(liability)
        + " per month.")
    steps = ("How it is figured: income of "
             + _fd_money(patient) + ", minus the personal "
             "needs allowance of " + _fd_money(pna))
    if alloc is not None:
        steps += (", minus the spousal allocation of "
                  + _fd_money(alloc) + " (the MMMNA target of "
                  + _fd_money(target) + ": the 2026 minimum "
                  + _fd_money(mn_min)
                  + " plus any shelter costs above "
                  + _fd_money(shelter_std) + ", capped at "
                  + _fd_money(mn_max) + ", less the spouse's own "
                  "income of "
                  + _fd_money(inputs["spouse_income"]) + ")")
    steps += "."
    lines.append(steps)
    return "\n\n".join(lines), inputs


_FD_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November",
     "December"])}


def _fd_application_date(question):
    ql = (question or "").lower()
    m = re.search(
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+(\d{4})", ql)
    if m:
        return "%04d-%02d-01" % (int(m.group(2)),
                                 _FD_MONTHS[m.group(1)])
    m = re.search(r"(?:applied|applying|filed|filing|application)"
                  r"[^0-9]{0,24}(\d{4})", ql)
    if m:
        return m.group(1) + "-01-01"
    return datetime.date.today().isoformat()


def _fd_engine_penalty(row, question, amounts):
    v = row["values"]
    app_date = _fd_application_date(question)
    divisor = None
    for d in sorted(v["divisors"],
                    key=lambda d: d.get("from") or "",
                    reverse=True):
        if d.get("from") is None or app_date >= d["from"]:
            divisor = float(d["divisor"])
            break
    if divisor is None:
        return None
    inputs = {"application_date": app_date}
    if amounts:
        if len(amounts) != 1:
            return None
        amount = amounts[0][0]
        inputs["amount"] = amount
        months = amount / divisor
        lines = [
            "The transfer penalty period is "
            + format(months, ".1f") + " months.",
            "How it is figured: the uncompensated transfer of "
            + _fd_money(amount) + " divided by the penalty "
            "divisor of " + _fd_money(divisor) + " per month "
            "(the divisor for applications on the date in the "
            "question, " + app_date + "). Only transfers inside "
            "the " + str(v["lookback_months"]) + "-month "
            "lookback are penalized.",
        ]
        return "\n\n".join(lines), inputs
    if "divisor" in _fd_norm(question):
        lines = [
            "The transfer penalty divisor is "
            + _fd_money(divisor) + " per month for an "
            "application dated " + app_date + ".",
            "The penalty period for an uncompensated transfer "
            "is the transfer amount divided by this divisor. "
            "Only transfers inside the "
            + str(v["lookback_months"]) + "-month lookback are "
            "penalized.",
        ]
        return "\n\n".join(lines), inputs
    return None


def _fd_engine_year_indexed(row, question, amounts):
    v = row["values"]
    table = v["table"]
    m = re.search(r"\b(20\d{2})\b", question or "")
    year = m.group(1) if m else str(datetime.date.today().year)
    if year not in table:
        return None
    entry = table[year]
    labels = v.get("field_labels", {})
    lines = [row["title"] + " for " + year + ":"]
    for k, val in entry.items():
        label = labels.get(k, k)
        if k.endswith("_pct"):
            lines.append("- " + label + ": " + str(val) + "%")
        elif isinstance(val, (int, float)):
            lines.append("- " + label + ": " + _fd_money(val))
        else:
            lines.append("- " + label + ": " + str(val))
    return "\n".join(lines), {"year": year}


def _fd_engine_lookup(row, question, amounts):
    lines = [row["title"] + ":"]
    for fact in row["values"]["facts"]:
        lines.append("- " + fact["label"] + ": " + fact["value"])
    return "\n".join(lines), {}


def _fd_engine_coverage(row, question, amounts):
    v = row["values"]
    lines = []
    if v.get("covered"):
        lines.append("Yes. " + row["title"] + " is covered "
                     "under the " + v["program"] + ".")
    else:
        lines.append("No. " + row["title"] + " is not covered "
                     "under the " + v["program"] + ".")
    if v.get("cap"):
        cap = v["cap"]
        if cap.get("plain"):
            lines.append("Cap: " + str(cap["amount"]) + " "
                         + cap["period"] + ".")
        else:
            lines.append("Cap: " + _fd_money(cap["amount"])
                         + " " + cap["period"] + ".")
    if v.get("conditions"):
        lines.append(v["conditions"])
    return "\n\n".join(lines), {}


_FD_ENGINES = {
    "split_and_clamp": _fd_engine_split_and_clamp,
    "liability": _fd_engine_liability,
    "penalty": _fd_engine_penalty,
    "year_indexed": _fd_engine_year_indexed,
    "lookup": _fd_engine_lookup,
    "coverage": _fd_engine_coverage,
}

# v30 (engine-class priority): a row whose engine COMPUTES
# the asker's answer outranks a row that renders stored
# facts, which outranks a broad coverage row — regardless
# of trigger-word overlap. Priority only reorders
# candidates whose engines already proved they can answer
# (a formula row that cannot parse the question never
# enters the list), so it cannot manufacture a claim; it
# decides contests the specificity tuple used to settle
# by wording luck (the 2026-10-16 live find: the
# paid-relative COVERAGE row claiming a transfer-penalty
# question the penalty FORMULA row had already proven).
_FD_ENGINE_CLASS = {"penalty": 3, "liability": 3,
                    "split_and_clamp": 3,
                    "year_indexed": 2, "lookup": 2,
                    "coverage": 1}

# v30 (win margin): when the top two specific candidates
# are in the same engine class and their specificity is
# within this many matched-phrase chars (with the same
# matched-phrase count), the claim is a coin flip dressed
# as a win — the door falls through and logs door_margin
# instead. Calibrated against the claim battery's
# near-tie fixtures, not in production.
_FD_MARGIN_CHARS = 8


def _fd_within_margin(spec_a, spec_b):
    """The v30 near-tie test on two _fd_match_specificity
    tuples: same matched-phrase count and matched-phrase
    chars differing by less than the margin."""
    return spec_a[0] == spec_b[0] \
        and abs(spec_a[1] - spec_b[1]) < _FD_MARGIN_CHARS


# v30 fix round (F2): the specificity band for engine-class
# priority. Class-primary ordering let a thin lookup match
# (fs_serves_ages: 2 phrases, 23 chars) override a coverage
# row's blowout match on its own topic (paid_relative_cap:
# 8 phrases, 65 chars) — class priority assumed candidacy
# implies answerability, which is thin for lookup/coverage
# engines. Revised rule: a higher class decides the contest
# only when its specificity is WITHIN THE BAND of the best
# specificity on the table (phrase count within 1, phrase
# chars within 24). Outside the band the strongest match
# wins and class never gets asked. Inside the band the
# class ladder still decides, tier by tier.
_FD_BAND_COUNT = 1
_FD_BAND_CHARS = 24


def _fd_within_band(spec, best):
    return spec[0] >= best[0] - _FD_BAND_COUNT \
        and spec[1] >= best[1] - _FD_BAND_CHARS


def _fd_trigger_groups_match(row, qn):
    """Every trigger group satisfied, ignoring excludes:
    the candidacy half of _fd_triggers_match. A row that
    group-matches but is vetoed by an exclude was IN the
    contest; the fall-through log names it (fix round:
    the NT logging gap)."""
    for group in row["triggers"]["all"]:
        if not any(p in qn for p in group):
            return False
    return True

_FD_TOPIC_TERMS = (
    ("P1_spousal_ltc", ("nursing home", "spouse", "wife",
                        "husband", "csra", "mmmna", "liability",
                        "long-term care", "long term care")),
    ("P2_P3_waivers", ("waiver", "family supports", "cih",
                       "health and wellness", "respite",
                       "structured family", "ddars")),
    ("P8_snap", ("snap", "food stamps", "food assistance",
                 "ebt")),
    ("P13_msp", ("qmb", "slmb", "medicare savings")),
    ("P12_medicare", ("medicare", "part b", "hospice")),
    ("P16_able", ("able account", "able")),
    ("P5_hip", ("hip", "healthy indiana")),
    ("P18_nemt", ("ride", "verida", "nemt", "transportation")),
    ("P11_appeals", ("appeal", "hearing", "notice")),
    ("P14_ssi_ssdi", ("ssi", "ssdi", "social security")),
    ("P1_eligibility", ("medicaid", "asset", "income limit",
                        "qualify", "eligible")),
)


def _fd_topic(question):
    qn = _fd_norm(question)
    for tag, terms in _FD_TOPIC_TERMS:
        if any(t in qn for t in terms):
            return tag
    return "other"


# --- v30 (verdict layer): for four arithmetic rows, the
# render appends the computed verdict on the ASKER'S OWN
# numbers (under/over, remainder). Every threshold comes
# from the row's own data (facts / cap), never a literal
# here. A verdict is computed only when the asker's
# numbers parse unambiguously (exactly one amount in the
# needed role); missing or ambiguous numbers render the
# rule alone. Never guesses.
def _fd_fact_figure(row, label_part):
    """First number in the named fact's value, from the
    row's own data."""
    for f in (row.get("values") or {}).get("facts") or ():
        if label_part in (f.get("label") or "").lower():
            m = re.search(r"\$?\s?(\d[\d,]*(?:\.\d{1,2})?)",
                          f.get("value") or "")
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except Exception:
                    return None
    return None


def _fd_role_amount(amounts, words):
    """The single amount whose clause carries one of the
    role words; None unless exactly one matches."""
    hits = [v for v, c in amounts
            if any(w in c for w in words)]
    return hits[0] if len(hits) == 1 else None


def _fd_verdict_qmb(row, question, amounts):
    lines = []
    inc_lim = _fd_fact_figure(row, "income limit")
    inc = _fd_role_amount(
        amounts, ("income", "social security", "make",
                  "makes", "earn", "get", "gets", "receive",
                  "receives"))
    if inc is not None and inc_lim:
        d = inc_lim - inc
        if d > 0:
            lines.append(
                "On your numbers: " + _fd_money(inc)
                + " a month is under the "
                + _fd_money(inc_lim) + " QMB income limit "
                "by " + _fd_money(d) + ".")
        elif d < 0:
            lines.append(
                "On your numbers: " + _fd_money(inc)
                + " a month is over the "
                + _fd_money(inc_lim) + " QMB income limit "
                "by " + _fd_money(-d) + ".")
        else:
            lines.append(
                "On your numbers: " + _fd_money(inc)
                + " a month is right at the "
                + _fd_money(inc_lim) + " QMB income limit.")
    ast_lim = _fd_fact_figure(row, "asset limit")
    ast = _fd_role_amount(
        amounts, ("asset", "savings", "saved", "bank",
                  "countable"))
    if ast is not None and ast_lim:
        d = ast_lim - ast
        if d > 0:
            lines.append(
                "Your countable assets of " + _fd_money(ast)
                + " are under the " + _fd_money(ast_lim)
                + " QMB asset limit by " + _fd_money(d)
                + ".")
        elif d < 0:
            lines.append(
                "Your countable assets of " + _fd_money(ast)
                + " are over the " + _fd_money(ast_lim)
                + " QMB asset limit by " + _fd_money(-d)
                + ".")
    return "\n".join(lines) or None


def _fd_verdict_hip(row, question, amounts):
    need_h = _fd_fact_figure(row, "work activity")
    need_e = _fd_fact_figure(row, "earnings alternative")
    if not need_h or not need_e:
        return None
    ql = (question or "").lower()
    hrs_mo = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hour)"
                  r"\s*(?:a|per|an|/)\s*(week|month|wk|mo)",
                  ql)
    if m:
        hv = float(m.group(1))
        hrs_mo = hv * 52.0 / 12.0 \
            if m.group(2) in ("week", "wk") else hv
    wage = _fd_role_amount(amounts, ("hour",))
    earn = _fd_role_amount(amounts, ("earn", "make",
                                     "paid"))
    if earn is not None and not any(
            w in c for v, c in amounts if v == earn
            for w in ("month",)):
        earn = None
    if earn is None and wage is not None \
            and hrs_mo is not None:
        earn = hrs_mo * wage
    if hrs_mo is None and earn is None:
        return None
    hrs_txt = ("about " + (str(int(round(hrs_mo)))
                           if hrs_mo == int(hrs_mo)
                           else f"{hrs_mo:.0f}")
               + " hours a month") if hrs_mo is not None \
        else None
    meets_h = hrs_mo is not None and hrs_mo >= need_h
    meets_e = earn is not None and earn >= need_e
    if meets_h:
        return ("On your numbers: " + hrs_txt + " meets "
                "the " + f"{need_h:.0f}" + "-hour work "
                "requirement.")
    if meets_e:
        out = ("On your numbers: about " + _fd_money(earn)
               + " a month in earnings meets the "
               "requirement through the "
               + _fd_money(need_e) + " earnings alternative")
        if hrs_txt:
            out += (" (hours alone, " + hrs_txt + ", are "
                    "under " + f"{need_h:.0f}" + ")"
                    )
        return out + "."
    if hrs_mo is not None and earn is not None:
        return ("On your numbers: " + hrs_txt + " is "
                "under the " + f"{need_h:.0f}" + "-hour "
                "requirement, and about " + _fd_money(earn)
                + " a month in earnings is under the "
                + _fd_money(need_e) + " earnings "
                "alternative.")
    if hrs_mo is not None:
        return ("On your numbers: " + hrs_txt + " is "
                "under the " + f"{need_h:.0f}" + "-hour "
                "requirement. The other way to meet it is "
                + _fd_money(need_e) + " a month in "
                "earnings.")
    return ("On your numbers: about " + _fd_money(earn)
            + " a month in earnings is under the "
            + _fd_money(need_e) + " earnings alternative. "
            "The other way to meet it is "
            + f"{need_h:.0f}" + " hours a month of work "
            "activity.")


def _fd_verdict_cap(row, question, amounts):
    cap_v = (row.get("values") or {}).get("cap") or {}
    try:
        cap = float(cap_v["amount"])
    except Exception:
        return None
    period = cap_v.get("period") or "per year"
    ql = (question or "").lower()
    raw_spends, raw_quotes = [], []
    for m in _FD_AMT_RE.finditer(ql):
        try:
            val = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        before = ql[max(0, m.start() - 40):m.start()]
        after = ql[m.end():m.end() + 25]
        if any(w in before
               for w in ("spent", "used", "paid")):
            raw_spends.append(val)
        elif any(w in before
                 for w in ("quot", "estimate", "charge")) \
                or " cost" in after:
            raw_quotes.append(val)
    # v30 fix round (V-PEST-1): a question that RESTATES
    # the cap ("used $3,200 of its $4,000 pest control
    # allowance") puts the cap amount itself inside the
    # spend window — the cap is not a spend. Cap-equal
    # amounts are set aside first; they count as a spend
    # only when nothing else does (a full-cap spend).
    spends = [v for v in raw_spends if v != cap]
    quotes = [v for v in raw_quotes if v != cap]
    if not spends:
        spends = raw_spends
    if len(spends) != 1:
        return None
    spent = spends[0]
    left = cap - spent
    cap_txt = _fd_money(cap) + " cap (" + period + ")"
    if left < 0:
        return ("On your numbers: " + _fd_money(spent)
                + " used is " + _fd_money(-left)
                + " over the " + cap_txt + ".")
    out = ("On your numbers: " + _fd_money(spent)
           + " used of the " + cap_txt + " leaves "
           + _fd_money(left) + ".")
    if len(quotes) == 1:
        q = quotes[0]
        if q > left:
            out += (" Of the " + _fd_money(q) + " quoted, "
                    + _fd_money(left) + " fits under the "
                    "cap and " + _fd_money(q - left)
                    + " falls outside it.")
        else:
            out += (" The " + _fd_money(q) + " quoted fits "
                    "under what is left of the cap.")
    return out


_FD_VERDICTS = {
    "qmb_limits": _fd_verdict_qmb,
    "hip_work_requirement": _fd_verdict_hip,
    "fs_annual_cap": _fd_verdict_cap,
    "pest_control_hw": _fd_verdict_cap,
}


def _fd_verdict(row, question, amounts):
    """The winner's verdict paragraph, or None. Never
    raises (a verdict bug must not cost the claim)."""
    fn = _FD_VERDICTS.get(row["id"])
    if fn is None:
        return None
    try:
        return fn(row, question, amounts)
    except Exception:
        return None


def _front_door_try(question):
    """Returns {answer, row_id, engine, inputs} when the door
    claims the question, else None. Logs every decision.
    Never raises. FSSA_FRONT_DOOR=0 is the kill switch: the
    door claims nothing and the lanes see v27 behavior (the
    legacy gate suites run under it)."""
    try:
        if os.environ.get("FSSA_FRONT_DOOR") == "0":
            return None
        if not _FRONT_DOOR_ROWS:
            return None
        if is_discovery_question(question):
            _fd_log({"decision": "fell_through",
                     "topic": "discovery",
                     "q": (question or "")[:160]})
            return None
        try:
            if decompose_question(question):
                _fd_log({"decision": "fell_through",
                         "topic": "bundled",
                         "q": (question or "")[:160]})
                return None
        except Exception:
            pass
        qn = _fd_norm(question)
        amounts = _fd_amounts(question)
        today = datetime.date.today().isoformat()
        # v27.2: collect every candidate (triggers match AND
        # the engine proves it can answer), then pick by the
        # specificity guard instead of file order. Specific
        # rows outrank general rows; a general row is blocked
        # outright when the question names a covered
        # service/topic; an exact specificity tie between
        # specific rows is ambiguity and falls through.
        specific = []
        general = []
        blocked = []
        for row in _FRONT_DOOR_ROWS:
            if not _fd_row_active(row, today):
                continue
            if not _fd_triggers_match(row, qn):
                # v30 fix round: a row whose trigger groups
                # all match but whose exclude vetoed it was
                # in this contest; remember it for the
                # fall-through log.
                if _fd_trigger_groups_match(row, qn):
                    blocked.append(row["id"])
                continue
            engine = _FD_ENGINES.get(row["engine"])
            if engine is None:
                continue
            got = engine(row, question, amounts)
            if got is None:
                continue
            entry = (_fd_match_specificity(row, qn), row, got)
            if row.get("general"):
                general.append(entry)
            else:
                specific.append(entry)
        winner = None
        margin_pair = None
        if specific:
            # v30 fix round (F2): class priority inside a
            # specificity band. Walk the class ladder from
            # the top: the first class with a candidate
            # whose specificity is within the band of the
            # best specificity on the table decides, and
            # its strongest in-band candidate wins. A class
            # whose best is a blowout loss (outside the
            # band) never gets to decide, so the strongest
            # match overall wins instead. The win margin
            # still applies INSIDE the deciding class: a
            # top two within the margin — an exact tie
            # included — falls through with a door_margin
            # log. (Supersedes the v30 class-primary sort:
            # see _FD_BAND_COUNT / _FD_BAND_CHARS.)
            best_spec = max(e[0] for e in specific)
            classes = sorted(
                {_FD_ENGINE_CLASS.get(e[1]["engine"], 0)
                 for e in specific}, reverse=True)
            for cls in classes:
                pool = [e for e in specific
                        if _FD_ENGINE_CLASS.get(
                            e[1]["engine"], 0) == cls
                        and _fd_within_band(e[0], best_spec)]
                if not pool:
                    continue
                pool.sort(key=lambda e: e[0], reverse=True)
                winner = pool[0]
                if len(pool) > 1 and _fd_within_margin(
                        pool[0][0], pool[1][0]):
                    margin_pair = (pool[0], pool[1])
                    winner = None
                break
        if margin_pair is not None:
            _fd_log({"decision": "door_margin",
                     "rows": [margin_pair[0][1]["id"],
                              margin_pair[1][1]["id"]],
                     "specs": [list(margin_pair[0][0]),
                               list(margin_pair[1][0])],
                     "topic": _fd_topic(question),
                     "q": (question or "")[:160]})
            return None
        if winner is None and general:
            if not _fd_names_covered_topic(qn, general[0][1]):
                winner = general[0]
        if winner is not None:
            _spec, row, got = winner
            text, inputs = got
            # v30 (verdict layer): four arithmetic rows
            # append the computed verdict on the asker's
            # own numbers; no verdict when they are
            # missing or ambiguous.
            _verdict = _fd_verdict(row, question, amounts)
            if _verdict:
                text = text + "\n\n" + _verdict
                inputs = dict(inputs or {})
                inputs["verdict"] = True
            answer = text + "\n\n" + _fd_source_line(row)
            _fd_log({"decision": "claimed", "row": row["id"],
                     "engine": row["engine"], "inputs": inputs,
                     "q": (question or "")[:160]})
            return {"answer": answer, "row_id": row["id"],
                    "engine": row["engine"], "inputs": inputs}
        _fd_log({"decision": "fell_through",
                 "topic": _fd_topic(question),
                 "q": (question or "")[:160]})
        if blocked:
            # v30 fix round: every fall-through that had a
            # contender removed by an exclude is logged as
            # a margin decision naming the vetoed row(s) —
            # the audit trail the near-tie fixtures read.
            _fd_log({"decision": "door_margin",
                     "rows": blocked,
                     "reason": "excluded",
                     "topic": _fd_topic(question),
                     "q": (question or "")[:160]})
        return None
    except Exception:
        return None


# --- v18 (F18): identical in-flight ask dedupe -----------------------
# A second submission of the same ask (same normalized key) while
# the first is still running waits for the first's result and
# replays it, instead of running (and paying for) a second
# pipeline: the Miller triple-submission shape. The owner
# publishes its result into the entry before releasing.
_INFLIGHT = {}
_INFLIGHT_LOCK = threading.Lock()


def _inflight_acquire(key):
    """Returns (entry, is_owner)."""
    with _INFLIGHT_LOCK:
        ent = _INFLIGHT.get(key)
        if ent is not None:
            return ent, False
        ent = {"event": threading.Event(), "result": None}
        _INFLIGHT[key] = ent
        return ent, True


def _inflight_release(key):
    with _INFLIGHT_LOCK:
        ent = _INFLIGHT.pop(key, None)
    if ent is not None:
        ent["event"].set()


def _run_job(rec, raw_body, headers):
    """v15 (S5): run one async ask job. The synchronous do_POST
    logic executes on a capture shim (the async flag stripped
    first), so a completed job's payload is EXACTLY the
    synchronous /ask response; the stage thread-local points at
    this job's record so the pipeline's stage updates land
    here. v23.1 (WS-A/BUG-10): the pipeline runs on a daemon
    child thread under a hard join of ASK_TIME_CAP_S + 60s —
    the 600s cap is enforced HERE, inside the job, not only by
    the deadlines threaded through the pipeline. A job that
    outlives the join is abandoned: the record finalizes as an
    error naming the budget backstop, and the child (a daemon)
    is fenced off from overwriting the record afterwards."""
    try:
        payload = json.loads(raw_body)
        if isinstance(payload, dict):
            payload.pop("async", None)
            raw_body = json.dumps(payload).encode()
    except Exception:
        pass
    shim = type("_JobShim", (), {})()
    # v18 fix (2026-10-14, live-found): the re-serialized body
    # above can be LONGER than the original request body (the
    # async strip re-dumps with default separators), while the
    # shim's do_POST re-reads exactly the original
    # Content-Length bytes: the body arrived truncated, the
    # JSON parse failed, and v18's new except handler masked
    # the real error behind an unbound _inf_entry. The shim
    # therefore carries a corrected Content-Length for the
    # body it actually holds (headers are read only for
    # Content-Length in do_POST).
    _shim_headers = dict(headers.items())
    _shim_headers["Content-Length"] = str(len(raw_body))
    shim.headers = _shim_headers
    shim.rfile = io.BytesIO(raw_body)
    shim.path = "/ask"
    shim.wfile = io.BytesIO()
    captured = {}

    def _cap(obj, code=200):
        captured["obj"] = obj
        captured["code"] = code

    shim._json = _cap
    shim.send_response = lambda *a, **k: None
    shim.send_header = lambda *a, **k: None
    shim.end_headers = lambda *a, **k: None
    shim.log_message = lambda *a: None

    def _work():
        _STAGE_TLS.job = rec
        try:
            Handler.do_POST(shim)
            if not rec.get("_abandoned"):
                rec["result"] = captured.get("obj")
                code = captured.get("code", 200)
                rec["status"] = "done" if code < 400 else "error"
                # v24 (§5.2): a stopped ask is its own
                # terminal status, not a done and not an error.
                if isinstance(rec["result"], dict) \
                        and rec["result"].get("cancelled"):
                    rec["status"] = "cancelled"
                if rec["status"] == "error":
                    rec["error"] = str(captured.get("obj"))[:300]
        except Exception as e:
            if not rec.get("_abandoned"):
                rec["status"] = "error"
                rec["error"] = str(e)[:300]
        rec["stage"] = "done"
        try:
            _set_phase("done")
        except Exception:
            pass

    _t = threading.Thread(target=_work, daemon=True)
    _t.start()
    _t.join(ASK_TIME_CAP_S + JOB_BACKSTOP_SLACK_S)
    if _t.is_alive():
        rec["_abandoned"] = True
        rec["status"] = "error"
        rec["error"] = ("the ask exceeded the "
                        f"{ASK_TIME_CAP_S}s cap and was stopped "
                        "by the job-thread budget backstop")
        rec["stage"] = "done"
    # v18 (§E): the job layer's own timestamps persist too (pass
    # 1 item P1's correction: total_ms is honest; what was
    # missing is exactly this job record).
    _record_ask({"kind": "job", "job_id": rec.get("id"),
                 "created": rec.get("ts"),
                 "finished": time.time(), "status": rec["status"]})


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/health":
            self._json({"ok": True, "docs_in": len(CORPUS_IN),
                        "docs_federal": len(CORPUS_FED),
                        "docs": len(CORPUS_IN),
                        "embeddings_loaded": len(EMBEDDINGS),
                        "hybrid_active": len(EMBEDDINGS) > 0,
                        # v20.2: the boot-time consistency
                        # verdict (see _run_consistency_check).
                        "consistency": CONSISTENCY,
                        # v22: retrieval engine state (requested
                        # vs effective, FTS5 capability, the
                        # generation being served, vector state
                        # and the embedding-drift canary).
                        "retrieval": _v2_health(),
                        # v23.1: the ask path's roster (who
                        # drafts, who judges, what backs each
                        # up) + the branch-out budget state.
                        "roster": _roster_health_block(),
                        "tavily": {
                            "branch_out_enabled": bool(
                                BRANCH_OUT_ENABLED
                                and _tavily_key()),
                            "credits_month":
                                _tavily_credits()["credits"],
                            "credits_stop": BRANCH_MONTHLY_STOP},
                        # v17: the paid seats' guardrail state
                        # (today's spend vs the code-side cap,
                        # the key's own dashboard-side limit).
                        "openrouter": _or_health_block(),
                        # v20: the new providers' state — xAI's
                        # accounting mode + credit-ledger
                        # figure, and Groq's daily guard.
                        "xai": _xai_health_block(),
                        "groq": _groq_health_block(),
                        # v27.1: front-door state (rows loaded;
                        # a missing data file reads loaded=false
                        # and the lanes are untouched).
                        "front_door": _front_door_health()})
        elif u.path == "/gaps":
            # v14: the gap log (IDKs, abstained parts, failed gates)
            # for the monthly rescrape watchlist and registry growth.
            self._json({"gaps": _read_gaps()})
        elif u.path.startswith("/ask/result/"):
            # v15 (S5): poll an async ask job: live stage while it
            # runs, the exact /ask payload when it completes.
            job = _job_get(u.path.rsplit("/", 1)[-1])
            if job is None:
                self._json({"error": "unknown job"}, 404)
            else:
                out = {"job_id": job["id"], "status": job["status"],
                       "stage": job["stage"],
                       # v23 (WS1): the structured phase the
                       # page's stepper renders, plus elapsed
                       # seconds (elapsed only; never an ETA).
                       "phase": job.get("phase") or
                                {"name": job["stage"], "round": None,
                                 "check": None},
                       "elapsed_s": round(time.time()
                                          - job["ts"], 1)}
                # v24 (§6.4): the per-section progress states,
                # derived from the ask context's tracker.
                _secs = None
                try:
                    _secs = _sections_snapshot(job)
                except Exception:
                    _secs = None
                if _secs is not None:
                    out["sections"] = _secs
                if job["status"] in ("done", "cancelled"):
                    out["result"] = job["result"]
                elif job["status"] == "error":
                    out["error"] = job["error"]
                self._json(out)
        elif u.path == "/models":
            # v24 (§8.3): the Models panel's option structure —
            # per seat: the hosted entry plus (allowlist n
            # installed). The hosted entries stay (the panel's
            # judging seat is Gemini-faced); what changed from
            # v23.2 is the Ollama side: the qwen3.5:9b-only
            # filter became the §8.2 allowlist. The legacy
            # "models" list keeps its v23.2 shape for any
            # older client.
            installed = ollama_models()
            local_opts = [m for m in LOCAL_SEAT_ALLOWLIST
                          if m in installed]
            seats = {}
            for _seat in ("draft", "extract", "judge"):
                seats[_seat] = (
                    [{"value": "hosted",
                      "label": "Hosted (standard)"}]
                    + [{"value": m, "label": "Local · " + m}
                       for m in local_opts])
            models = [m for m in installed if m == "qwen3.5:9b"]
            if GEMINI_API_KEY:
                models = ["gemini-3.5-flash-lite (API)",
                          "gemini-3.8-flash (API)"] + models
            self._json({"models": models, "seats": seats,
                        "allowlist": list(LOCAL_SEAT_ALLOWLIST),
                        "installed": installed})
        elif u.path == "/search":
            qs = parse_qs(u.query)
            q = qs.get("q", [""])[0]
            # v24 (§10): the corpus control is gone — every
            # search runs both corpora. The request field stays
            # ACCEPTED (and ignored) for one version so old
            # clients do not break.
            corp = get_corpus("both")
            # v23: plain search rides the same v2 engine as the
            # ask path. If the generation store is down the box
            # degrades to the keyword scan; the ASK path never
            # does (it fails closed in the pipeline).
            try:
                self._json({"query": q, "hits": hybrid_search(
                    q, corpus=corp, boosts=False)})
            except Exception:
                self._json({"query": q, "hits": search(q, corpus=corp)})
        elif u.path == "/icon.png":
            try:
                with open("icon.png", "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/ask/cancel/"):
            # v24 (§5.2): the Stop button. Sets the job's
            # cancel flag; the pipeline unwinds at its next
            # clock seam and the job ends "cancelled".
            job = _job_get(u.path.rsplit("/", 1)[-1])
            if job is None:
                self._json({"error": "unknown job"}, 404)
            else:
                job["cancel"] = True
                self._json({"ok": True, "job_id": job["id"],
                            "status": job["status"]})
            return
        if u.path == "/models/warm":
            # v24 (§8.5): picking a local model in the panel
            # fires this — a minimal Ollama call that loads the
            # model NOW (while the user is still typing), so
            # the load cost does not land inside the ask clock.
            # Allowlist only; best effort; the reply says
            # whether the model answered.
            try:
                _wl = int(self.headers.get("Content-Length", 0))
                _wb = json.loads(self.rfile.read(_wl) or b"{}")
            except Exception:
                _wb = {}
            _wm = str(_wb.get("model") or "")
            if _wm not in LOCAL_SEAT_ALLOWLIST:
                self._json({"ok": False,
                            "error": "model not in the seat "
                                     "allowlist"}, 400)
                return
            _wok = False
            try:
                _wp = json.dumps(
                    {"model": _wm, "prompt": " ",
                     "stream": False, "keep_alive": "10m",
                     "options": {"num_predict": 1}}).encode()
                _wr = urllib.request.Request(
                    OLLAMA + "/api/generate", data=_wp,
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(_wr, timeout=120).read()
                _wok = True
            except Exception:
                _wok = False
            self._json({"ok": _wok, "model": _wm})
            return
        if u.path != "/ask":
            self.send_response(404)
            self.end_headers()
            return
        # v18 fix (2026-10-14, live-found): the dedupe handles
        # are bound BEFORE the try, so the except handler can
        # never mask an early failure (e.g. an unparseable
        # body) behind an unbound-variable error.
        _inf_entry = None  # v18 (F18): dedupe ownership
        _inf_key = None
        try:
            length = int(self.headers.get("Content-Length", 0))
            # DoS protection: reject bodies over 100KB
            MAX_BODY = 100 * 1024
            if length > MAX_BODY:
                self._json({"error": f"request too large (max {MAX_BODY} bytes)"}, 413)
                return
            raw = self.rfile.read(length)
            req = json.loads(raw)
            # v26 (N2; GPT's finding 2 + Claude's note 3 via
            # plan §7): request-shape validation BEFORE the
            # async branch. Through v25.1 a non-object body
            # failed at .get() and a non-string field at
            # .strip(), both landing in the catch-all as a
            # bare 500 with no record; and an unknown mode on
            # the async path was accepted with a 202 (the job
            # then failed opaquely). Shape errors are client
            # errors: 400, before any job exists. (The sync
            # flow re-validates mode below; harmless.)
            if not isinstance(req, dict):
                self._json({"error": "request body must be "
                            "a JSON object"}, 400)
                return
            for _fld in ("question", "model"):
                if _fld in req \
                        and not isinstance(req[_fld], str):
                    self._json({"error": _fld + " must be "
                                "a string"}, 400)
                    return
            if "mode" in req \
                    and req["mode"] not in ("quick", "deep"):
                self._json({"error": "unknown mode: "
                            + str(req["mode"])[:40]}, 400)
                return
            _inf_entry = None  # v18 (F18): dedupe ownership
            _inf_key = None
            # v15 (S5): the async job path (POST /ask?async=1 or
            # "async": true in the body). The job runs the SAME
            # synchronous logic on a capture shim; callers poll
            # GET /ask/result/<id>. Sync callers are untouched.
            if parse_qs(u.query).get("async") or req.get("async"):
                rec = _job_create()
                threading.Thread(target=_run_job,
                                 args=(rec, raw, self.headers),
                                 daemon=True).start()
                self._json({"job_id": rec["id"],
                            "status": "running",
                            "stage": rec["stage"]}, 202)
                return
            question = req.get("question", "").strip()
            model = req.get("model", "").strip()
            # v24 (§10): the corpus dropdown is gone — every
            # ask searches BOTH corpora (today's default
            # experience, now the only one). The request field
            # stays accepted (and ignored) for one version.
            corpus_name = "both"
            # v25 (Quick/Deep): the ask's mode. Quick is the
            # DEFAULT — an absent mode is a Quick ask. Deep
            # is the v24 judged pipeline, unchanged. Anything
            # else is a client error: an unknown mode must
            # never silently become either lane.
            ask_mode = req.get("mode")
            if ask_mode is None:
                ask_mode = "quick"
            if ask_mode not in ("quick", "deep"):
                self._json({"error": "unknown mode: "
                            + str(ask_mode)[:40]}, 400)
                return
            # Feature toggles
            use_rewrite = req.get("rewrite", REWRITE_ENABLED)
            use_rerank = req.get("rerank", RERANK_ENABLED)
            corp = get_corpus(corpus_name)
            # v24 (§8): the Models panel's per-seat picks. Each
            # seat is "hosted" or an allowlisted local model;
            # anything else in the payload is ignored — a
            # benefits tool does not run unlisted models as
            # drafters or judges, whatever a client sends.
            seat_models = {"draft": "hosted",
                           "extract": "hosted", "judge": "hosted"}
            # v26 (F5): set below when a colliding judge pick
            # is substituted; stamped on the ask record.
            seat_substitution = None
            _sm = req.get("seat_models")
            if isinstance(_sm, dict):
                for _sk in seat_models:
                    _sv = _sm.get(_sk)
                    if _sv == "hosted" \
                            or _sv in LOCAL_SEAT_ALLOWLIST:
                        seat_models[_sk] = _sv
            search_help = req.get("search_help")
            if search_help not in ("hosted", "off"):
                search_help = None
            web_branch_pick = req.get("web_branch")
            if web_branch_pick not in ("allow", "never"):
                web_branch_pick = None
            if not question:
                self._json({"error": "empty question"}, 400)
                return
            # v18 (F19): the honest local mode. The webapp's
            # local selection sends local=true, and a local ask
            # runs the WHOLE pipeline on-device: local drafting
            # (the picked local model), local judging seats
            # (threaded into the units as local_only), retrieval
            # utilities OFF (rewrite/rerank ride hosted seats),
            # and no branch-out. Through v17.3 neither webapp
            # selection was fully local: the Gemini primary and
            # the hosted utilities ran regardless of the toggle.
            # The flag joins the answer-cache key below, so
            # local and hosted answers never replay each other.
            local_mode = bool(req.get("local"))
            if local_mode:
                use_rewrite = False
                use_rerank = False
                # v24 (§8.3): the legacy local=true + model
                # payload maps to all three seats on that
                # model, search help off, web branch never —
                # the old mode's exact shape, one version of
                # transition for old clients.
                _lm = model or LOCAL_MODEL
                # v26 (F3, D4 = VALIDATE): the legacy mapping
                # overwrites the validated seat picks with the
                # raw model field — through v25.1 ANY installed
                # model (the coder, either abliterated 8B)
                # became drafter + extractor + judge here,
                # skipping the §8 allowlist the panel seats
                # honor. The path stays; the model must pass
                # the same membership check, or the ask is
                # refused at intake.
                if _lm not in LOCAL_SEAT_ALLOWLIST:
                    self._json({"error": "local model not in "
                                "the seat allowlist: "
                                + str(_lm)[:60]}, 400)
                    return
                seat_models = {"draft": _lm, "extract": _lm,
                               "judge": _lm}
                search_help = "off"
                web_branch_pick = "never"
            if search_help == "off":
                use_rewrite = False
                use_rerank = False
            # v26 (F5, D3 = ENFORCE): the draft-family
            # exclusion at intake. §C's rule (a model never
            # judges its own draft) was dead code through
            # v25.1 — draft_family was written at serve time
            # and read nowhere — so panel picks could seat
            # the drafting family as its own judge and the
            # verdict counted. A colliding judge pick is
            # substituted with the roster's default
            # different-family judge (the hosted chain) and
            # the substitution is stamped on the ask record.
            # The legacy local mapping is exempt HERE by
            # design: a local ask is fully on-device, the
            # allowlist holds one family only, and its judge
            # leg is refused at runtime instead (the round
            # finals on deterministic checks) — the ask
            # stays local and never self-judges either way.
            if not local_mode:
                _dpick = seat_models.get("draft")
                _jpick = seat_models.get("judge")
                if _jpick != "hosted" and _dpick != "hosted" \
                        and _model_family(_jpick) \
                        == _model_family(_dpick):
                    seat_substitution = {
                        "seat": "judge", "from": _jpick,
                        "to": "hosted",
                        "reason": "the judge pick is the "
                        "drafting family ("
                        + _model_family(_jpick) + "); a "
                        "model never judges its own draft"}
                    seat_models["judge"] = "hosted"
            seat_local = {k: (None if v == "hosted" else v)
                          for k, v in seat_models.items()}
            any_seat_local = any(seat_local.values())
            # v24 (§8.4): the Web branch line — Allow is
            # today's hosted behavior; the legacy local mode
            # forced Never, and the panel makes it a pick.
            branch_allowed = (web_branch_pick != "never") \
                and not local_mode
            t_start = time.monotonic()
            # v16: per-ask records (seat events + local loads,
            # retrieval sub-timings, branch-out accounting).
            # v23.1 (BUG-1): the resets install the fresh ask
            # context FIRST. Through v23 the retrieving phase
            # opened on the PREVIOUS ask's context (the two
            # calls below ran before _events_reset), so the
            # reset discarded it and phase_ms never contained
            # "retrieving" — the phase the budget math most
            # needs, since retrieval is inside the cap.
            _events_reset()
            _retr_reset()
            _or_reset_ask()
            _set_stage("retrieving")
            _set_phase("retrieving")
            # v24 (§5.2/§6.4): wire the job's cancel flag and
            # the progress tracker into the ask context, and
            # publish the context on the job record so the
            # /ask/result snapshot reads live section states.
            _actx = _ask_ctx()
            _actx.tracker = {"units": [], "parts": 0}
            _job_rec = getattr(_STAGE_TLS, "job", None)
            if _job_rec is not None:
                _actx.cancel_rec = _job_rec
                _job_rec["_ask_ctx"] = _actx
            # v15: the per-ask wall-clock cap (config
            # ASK_TIME_CAP_S); the ladder and the units loop
            # consult this deadline and never ship unverified.
            deadline = t_start + ASK_TIME_CAP_S
            # v23.2 (BUG-11 F1): anchor the ask's ONE clock
            # HERE, at ask start. The class deadline is fixed
            # later, the moment `hard` is known
            # (_ask_clock_class at the unit-spec build), and
            # every unit / rescue / branch re-run consults
            # these anchors — no stage re-arms its own budget.
            _ask_clock_begin(t_start)
            retr_split = None
            ask_web = {"attempted": False, "queries": [],
                       "urls": [], "credits": 0, "fetched": None}

            def _branch_attempt(claims):
                """Item 7: at an honest IDK disposition, search
                the live official web (whitelist only) and return
                temporary web excerpts, or [] when the branch
                cannot or should not run. Skipped for discovery
                surveys and for explicit model picks (Gemini or a
                named local model): branch-out is a property of
                the default pipeline. Every attempt gap-logs
                under gate 'branch_out' for rescrape absorption.
                The ask's 600s cap is shared: under ~60s left,
                the branch does not start."""
                nonlocal n_gaps
                if ask_mode == "quick":
                    return []  # v25: Quick never branches out
                if discovery or use_gemini:
                    return []
                if not branch_allowed:
                    return []  # v24 (§8.4): Web branch: Never
                if model and model != LOCAL_MODEL:
                    return []
                if time.monotonic() > deadline - 60:
                    return []
                # v23.2 (F1): the branch also consults the
                # class deadline — past it, no branch search
                # (and no branch re-run) starts at all.
                _cd = _ask_ctx().class_deadline
                if _cd is not None \
                        and time.monotonic() > _cd - 60:
                    return []
                web_hits_b, rec = _branch_out_hits(question, claims)
                ask_web["attempted"] = True
                ask_web["queries"] += rec["queries"]
                ask_web["urls"] += rec["urls"]
                ask_web["credits"] += rec["credits"]
                ask_web["fetched"] = rec["fetched"]
                if rec.get("unavailable"):
                    ask_web["unavailable"] = rec["unavailable"]
                log_gap(question, "idk", "branch_out",
                        rec["urls"] or
                        [rec.get("unavailable", "no results")])
                n_gaps += 1
                return web_hits_b

            def _apply_web_badge(unit_rec, unit_hits):
                """Badge translation for branch evidence (item
                7): a checked unit whose supported load-bearing
                claims rest on web excerpts ships verified_web;
                its cited web hits are marked verified for the
                source list. Partial keeps its badge; the web
                sourcing is stated in the record. v23.1: a
                deterministically-checked unit (checked_det)
                ships verified_web_reduced — the web variant of
                the reduced check."""
                web_idx = {i for i, h in enumerate(unit_hits)
                           if h.get("web")}
                if not web_idx:
                    return
                cited_web = False
                for c in unit_rec.get("claims", ()):
                    srcs = set(c.get("sources") or ())
                    if c["final"] == "supported" \
                            and c.get("load_bearing") \
                            and web_idx & srcs:
                        cited_web = True
                        for j in web_idx & srcs:
                            unit_hits[j]["verified"] = True
                if cited_web:
                    unit_rec["web_used"] = True
                    if unit_rec["badge"] == "checked":
                        unit_rec["badge"] = "verified_web"
                    elif unit_rec["badge"] == "checked_det":
                        unit_rec["badge"] = "verified_web_reduced"
            # Determine if using Gemini or local Ollama
            use_gemini = model.startswith("gemini")
            # v13: fresh/nocache (JSON body or query string) skips the
            # answer-cache READ so a manual retest regenerates instead
            # of replaying the cached answer; the new answer still
            # replaces the cache entry below.
            fresh = bool(req.get("fresh") or req.get("nocache")
                         or parse_qs(u.query).get("fresh")
                         or parse_qs(u.query).get("nocache"))
            # v26 (F8, D2 = RETIRE): the v13 routed-model
            # route is deleted. Through v25.1, contested and
            # bundled questions asked of the local default
            # were silently rerouted to qwen3:14b — a model
            # deleted from the PC in the 2026-10-15 cleanup,
            # so the route drafted on a ghost (or failed
            # trying) and freed VRAM for a model that would
            # never serve. A no-model ask now follows the
            # normal seat logic: the local default is the
            # installed 9B, explicit picks name their model,
            # and the default seats draft on the hosted
            # ladder. What the route ALSO carried — the
            # hard-question classification for default local
            # asks (it sets the class deadline) — is kept as
            # a plain question property below (_hard_q); it
            # was never the route's to take with it.
            routed_model = model or LOCAL_MODEL
            route_reason = ""
            _hard_q = (not use_gemini
                       and model in ("", "default", LOCAL_MODEL)
                       and bool(is_hard_question(question)))
            # v25: the mode rides the key (last position) —
            # a Quick answer is never served for a Deep ask
            # or vice versa, and in-flight dedupe (same key)
            # keeps the lanes apart too.
            ask_key = (question, routed_model, corpus_name,
                       use_rewrite, use_rerank, local_mode,
                       seat_models["draft"], seat_models["extract"],
                       seat_models["judge"], search_help or "",
                       web_branch_pick or "", ask_mode)
            # v27.1: the front door. A claimed question is
            # answered by code from front_door_data.json and
            # returns here; anything else has already been
            # logged as a fall-through and continues into the
            # lanes below untouched. Door answers never enter
            # the AI answer cache and cost $0.
            _fd = None
            try:
                _fd = _front_door_try(question)
            except Exception:
                _fd = None
            if _fd is not None:
                _fd_end = time.monotonic()
                _fd_result = {
                    "answer": _fd["answer"], "hits": [],
                    "ts": time.time(),
                    "model": "front-door (code)", "prompt": "",
                    "fallback": False, "downgraded": False,
                    "discovery": False,
                    "timing": {
                        "retrieve_ms": 0,
                        "generate_ms": int(
                            (_fd_end - t_start) * 1000),
                        "total_ms": int(
                            (_fd_end - t_start) * 1000),
                        "rungs": 0},
                    "registry": [], "routed": None,
                    "figure_check": {"ungrounded": [],
                                     "regenerated": False},
                    "parts": 1, "badge": "code",
                    "judging": {
                        "mode": "front_door",
                        "annotation": (
                            "Computed by code from a verified "
                            "data row (front door row "
                            + _fd["row_id"] + "); no AI draft, "
                            "no judge."),
                        "checks": _empty_checks_block(),
                        "errors": []},
                    "verification": [], "web": None,
                    "cost_usd": 0.0, "mechanisms": [],
                    "front_door": {"row": _fd["row_id"],
                                   "engine": _fd["engine"],
                                   "inputs": _fd["inputs"]},
                    "wall_s": round(_fd_end - t_start, 3),
                    "progress": {
                        "pills": ["Front door: code answer"],
                        "parts": []}}
                try:
                    _record_ask({
                        "kind": "ask",
                        "ask_id": _ask_ctx().ask_id,
                        "ts": time.time(),
                        "wall_s": _fd_result["wall_s"],
                        "phase_ms": _phase_ms_snapshot(),
                        "question": question,
                        "corpus": corpus_name, "mode": ask_mode,
                        "local": bool(local_mode),
                        "seat_models": dict(seat_models),
                        "seat_substitution": seat_substitution,
                        "search_help": search_help or "",
                        "web_branch": web_branch_pick or "",
                        "badge": "code", "cost_usd": 0.0,
                        "served": "front-door (code)",
                        "budget_cut": False, "cut_phase": None,
                        "draft_calls": 0, "draft_capped": False,
                        "checks": _empty_checks_block(),
                        "annotation": _fd_result["judging"][
                            "annotation"],
                        "events": [], "retrieval": [],
                        "units": [],
                        "front_door": _fd_result["front_door"]})
                except Exception:
                    pass
                self._json(_fd_result)
                return
            if not fresh:
                cached_answer = _cache_get(_ASK_CACHE, ask_key)
                if cached_answer:
                    # v18 (F17): a replay reports no new spend
                    # and says it is a replay.
                    replay = dict(cached_answer)
                    replay["cost_usd"] = 0.0
                    replay["cached"] = True
                    self._json(replay)
                    return
            # v18 (F18): identical in-flight dedupe (see
            # _inflight_acquire). A duplicate submission waits
            # for the running twin and replays its result.
            if not fresh:
                _inf_entry, _inf_owner = _inflight_acquire(ask_key)
                _inf_key = ask_key
                if not _inf_owner:
                    _inf_entry["event"].wait(ASK_TIME_CAP_S + 180)
                    _twin = _inf_entry.get("result")
                    _inf_entry = None
                    _inf_key = None
                    if _twin is not None:
                        replay = dict(_twin)
                        replay["cost_usd"] = 0.0
                        replay["cached"] = True
                        replay["deduped"] = True
                        self._json(replay)
                        return
                    # The twin died without a result: fall
                    # through and run this ask itself (without
                    # re-acquiring).
            if not model and not use_gemini:
                # No model named: the local default answers
                # (v26 F8: the installed 9B; the routed 14B
                # default is retired).
                model = routed_model
            if not model:
                if _inf_entry is not None:
                    _inflight_release(_inf_key)
                    _inf_entry = None
                self._json({"error": "no Ollama models available"}, 500)
                return
            discovery = is_discovery_question(question)
            # Narrative (long, story-like) questions get more excerpts:
            # their answers turn on one operative sentence inside a long
            # document, and 8 slots lose that lottery too often.
            n_ex = 12 if _is_narrative(question) else 8
            missing_areas = []
            instructions = ""
            # v23 (WS3.2): ask retrieval is the v2 engine,
            # unconditionally — the legacy EMBEDDINGS gate and
            # the keyword-only else branch are deleted with
            # the legacy path. An engine that cannot open
            # fails the ask closed with a 503, never a silent
            # keyword downgrade.
            try:
                if discovery:
                    hits, missing_areas = discovery_hits(
                        question, corpus=corp)
                    if not hits:  # nothing per-area: normal search
                        hits = hybrid_search(
                            question, corpus=corp, n=n_ex,
                            use_rewrite=use_rewrite,
                            use_rerank=use_rerank)
                    instructions = SURVEY_INSTRUCTIONS
                    if missing_areas:
                        instructions += (
                            " Retrieval found no documents for: "
                            + ", ".join(missing_areas)
                            + ". List those categories in the "
                            "'Not covered' line.")
                else:
                    # v11: bundled (multi-part) narrative
                    # questions get per-sub-question retrieval
                    # with guaranteed slots.
                    hits = retrieve_ask_hits(
                        question, corp, n_ex,
                        use_rewrite=use_rewrite,
                        use_rerank=use_rerank)
                    hits = _drop_payee_admin(question, hits)
            except Exception as e:
                if _inf_entry is not None:
                    _inflight_release(_inf_key)
                    _inf_entry = None
                self._json({"error": "retrieval engine "
                                    "unavailable: "
                                    + str(e)[:200]}, 503)
                return
            # v13: verified-figures registry + server-computed CSRA.
            # Matched entries ride in the prompt as an authoritative
            # block, and their figures join the checker's allowed set.
            # (After retrieval so the discovery branch's instruction
            # assignment cannot overwrite the block.)
            registry_entries = _registry_matches(question)
            csra_comp = None
            if any(e["id"] == "csra" for e in registry_entries):
                csra_comp = compute_csra(question)
            reg_block = _registry_block(registry_entries)
            if csra_comp:
                reg_block = (reg_block + "\n"
                             + _csra_block(csra_comp)).strip()
            if reg_block:
                instructions = (instructions + "\n\n"
                                + reg_block).strip()
            # v13: bundled questions get their parts named in the
            # prompt so every part is answered (retrieval already
            # guarantees per-part excerpts via retrieve_ask_hits).
            parts = decompose_question(question)
            if parts:
                numbered = "\n".join(
                    f"{i + 1}. {p}" for i, p in enumerate(parts))
                instructions = (
                    instructions + f"\n\nThis question has "
                    f"{len(parts)} parts. Answer EVERY part, in order, "
                    f"each under its own short heading:\n"
                    f"{numbered}").strip()
            t_retrieved = time.monotonic()
            # --- v14 pipeline: sufficiency gate -> ladder -> badges --
            mechanisms_all = _mechanism_matches(question)
            allowed_extra = _registry_values(
                registry_entries, on_date=_question_date(question))
            if csra_comp:
                allowed_extra += [csra_comp["assets"], csra_comp["half"],
                                  csra_comp["csra"]]
            sufficiency_ms = 0
            n_gaps = 0

            def _respond(answer_text, badge, judging, verification,
                         figure_check, served, fallback, downgraded,
                         full_prompt, rungs, judge_ms, refute_ms=0,
                         cap_hit=False):
                # v26 (F14): one final cancel check at the
                # top of response assembly — a Stop pressed
                # after the last unit seam (during assembly
                # itself) was invisible through v25.1 and the
                # ask reported "done" with its answer. This is
                # _check_cancel's FLAG half only, mirroring
                # its job-vs-context precedence exactly: the
                # helper's deadline half must not run here,
                # because a leg that started inside its margin
                # legitimately finishes past the soft ask
                # deadline, and raising TimeoutError at
                # assembly would 500 those asks (see BUILD.md).
                _job_now = getattr(_STAGE_TLS, "job", None)
                if _job_now is not None:
                    _cancelled_now = bool(_job_now.get("cancel"))
                else:
                    # v26.1: the context holds cancel_rec,
                    # not a cancel attribute; mirror
                    # _cancel_requested()'s flag read.
                    # Sync asks have no job — the context
                    # is the only place their flag can live.
                    _cancelled_now = _cancel_requested()
                if _cancelled_now:
                    raise _AskCancelled()
                t_end = time.monotonic()
                timing = {"retrieve_ms": int((t_retrieved - t_start)
                                             * 1000),
                          "retrieve_split": retr_split
                          if retr_split is not None
                          else _retr_snapshot(),
                          "generate_ms": int((t_end - t_retrieved)
                                             * 1000),
                          "total_ms": int((t_end - t_start) * 1000),
                          "sufficiency_ms": sufficiency_ms,
                          "judge_ms": judge_ms, "rungs": rungs,
                          "refute_ms": refute_ms, "cap_hit": cap_hit}
                print(f"[ask] served={served} route={route_reason or '-'} "
                      f"reg={[e['id'] for e in registry_entries]} "
                      f"badge={badge} judging={judging.get('mode')} "
                      f"rungs={rungs} gaps={n_gaps} "
                      f"retrieve={timing['retrieve_ms']}ms "
                      f"generate={timing['generate_ms']}ms "
                      f"total={timing['total_ms']}ms "
                      f"q={question[:60]!r}", flush=True)
                result = {"answer": answer_text, "hits": hits,
                          "ts": time.time(),
                          "model": served, "prompt": full_prompt,
                          "fallback": fallback, "downgraded": downgraded,
                          "discovery": discovery, "timing": timing,
                          "registry": [e["id"] for e in registry_entries],
                          "routed": route_reason or None,
                          "figure_check": figure_check,
                          "parts": len(parts), "badge": badge,
                          "judging": judging,
                          "verification": verification,
                          "web": ask_web if ask_web["attempted"]
                          else None,
                          "cost_usd": _or_ask_cost(),
                          "mechanisms": [m["id"] for m in mechanisms_all]}
                # v24 (§5.1): the frozen total rides the
                # response — the page's "Answered in m:ss" is
                # the server's wall time, not a client clock.
                result["wall_s"] = round(t_end - t_start, 3)
                # v24 (§6.5): post-run pills + per-part lines.
                try:
                    result["progress"] = _progress_summary(units)
                except Exception:
                    result["progress"] = {"pills": [], "parts": []}
                # v18 (F17): only top-tier answers enter the
                # replay cache. Through v17.3 partials and IDKs
                # cached too, so a transient failure froze into
                # the answer every later re-ask replayed.
                # v23.1: the top tier is checked / verified_web.
                # checked_det does NOT cache — an answer the
                # judge never saw must not freeze into the
                # replay every later re-ask returns.
                # v25: badge "quick" does not cache either,
                # for the same reason (deterministic checks
                # only; the mode is in the key, so the lanes
                # stay separated regardless).
                # v26 (F15): an ABANDONED job's late
                # completion writes NEITHER the answer cache
                # NOR the ask record. Through v25.1 the
                # orphaned thread's finish still cached its
                # answer — a later identical ask replayed a
                # result the job layer had already reported
                # as an error — and still wrote its record.
                _job_abandoned = bool(
                    _job_rec and _job_rec.get("_abandoned"))
                if badge in ("checked", "verified_web") \
                        and not _job_abandoned:
                    _cache_put(_ASK_CACHE, ask_key, result)
                # v18 (§E): persist the ask record (see
                # _record_ask). Per-unit detail comes from the
                # units list when the pipeline built one; the
                # early IDK paths record without it.
                try:
                    _units_now = units
                except NameError:
                    _units_now = []
                _ask_rec = {
                    "kind": "ask", "ask_id": _ask_ctx().ask_id,
                    "ts": time.time(),
                    "wall_s": round(t_end - t_start, 3),
                    # v23 (WS1): per-phase wall time — the
                    # values are SECONDS (millisecond
                    # precision) despite the field name
                    # (BUG-7); the held budget test's
                    # measurement.
                    "phase_ms": _phase_ms_snapshot(),
                    "question": question, "corpus": corpus_name,
                    "mode": ask_mode,  # v25: the lane that ran
                    "local": bool(local_mode or any_seat_local),
                    # v24 (§8): the seat picks + the two panel
                    # lines, as served for this ask.
                    "seat_models": dict(seat_models),
                    "seat_substitution": seat_substitution,
                    "search_help": search_help
                    or ("off" if local_mode else "hosted"),
                    "web_branch": web_branch_pick
                    or ("never" if local_mode else "allow"),
                    "badge": badge,
                    "cost_usd": result["cost_usd"],
                    "served": served,
                    # v23.1 (WS-A/BUG-10): whether the ask was
                    # finalized by a budget cut, and the unit +
                    # phase it landed in.
                    "budget_cut": bool(cap_hit),
                    "cut_phase": next(
                        (u.get("cut_phase") for u in _units_now
                         if u.get("budget_cut")), None)
                    or _ask_ctx().cut_phase,
                    # v23.2 (F2): the ask's shared drafting
                    # call count + cap flag, on the ask record.
                    "draft_calls": _ask_ctx().draft_calls,
                    "draft_capped": _ask_ctx().draft_capped,
                    "checks": (judging or {}).get("checks"),
                    "annotation": (judging or {}).get("annotation"),
                    # v20.2 (observability): the ask's full
                    # event stream (seat successes AND the new
                    # seat_fail/seat_down failure reasons,
                    # local loads, guard sheds) and every
                    # retrieval-pool snapshot, at record level;
                    # the per-unit slices ride below.
                    "events": list(_ask_ctx().events),
                    "retrieval": list(_ask_ctx().retr_pools),
                    "units": [{
                        "part": u.get("part"),
                        "unit_q": u.get("unit_q") or None,
                        "badge": u.get("badge"),
                        "budget_cut": bool(u.get("budget_cut")),
                        "cut_phase": u.get("cut_phase"),
                        "draft_text": (u.get("draft_text")
                                       or "")[:8000],
                        "claims": [{
                            "text": c.get("text"),
                            "final": c.get("final"),
                            "verdicts": {k: c.get(k) for k in
                                         ("lite", "backup",
                                          "local", "judge")},
                            "det_figure_fail": c.get(
                                "det_figure_fail"),
                            "det_cited": c.get("det_cited"),
                            "coverage": c.get("coverage"),
                            "models": c.get("models"),
                            "origin": c.get("origin"),
                            "meta": bool(c.get("meta")),
                            "load_bearing": bool(
                                c.get("load_bearing")),
                            "sources": c.get("sources"),
                        } for c in u.get("claims", ())],
                        "hits": [{
                            "title": h.get("title"),
                            "url": h.get("url"),
                            "excerpt": (h.get("excerpt")
                                        or "")[:3000],
                        } for h in u.get("hits", ())],
                        # v20.2 (observability): this unit's
                        # event slice (seat_fail/seat_down
                        # reasons included), its judge error
                        # strings (previously response-only),
                        # and any retrieval pools it ran.
                        "rescue": u.get("rescue"),
                        # v23.2 (F2/F3): the shared draft-call
                        # count, the draft-cap flag, and the
                        # assembly guard's record, per unit.
                        "draft_calls": u.get("draft_calls"),
                        "draft_capped": bool(
                            u.get("draft_capped")),
                        "assembly_guard": u.get(
                            "assembly_guard"),
                        # v25.1: the Quick lane's self-audit
                        # flag and repair record (None/False
                        # on Deep units, which run neither).
                        "quick_self_audit": u.get(
                            "quick_self_audit"),
                        "quick_repair": bool(
                            u.get("quick_repair")),
                        "quick_repair_failures": u.get(
                            "quick_repair_failures"),
                        "quick_repair_error": u.get(
                            "quick_repair_error"),
                        "events": u.get("events"),
                        "judge_errors": (u.get("judging")
                                          or {}).get("errors"),
                        "retrieval": u.get("retr_pools"),
                    } for u in _units_now],
                }
                # v26 (F15): the abandoned-job gate (above).
                if not _job_abandoned:
                    _record_ask(_ask_rec)
                # v18 (F18): publish to any identical in-flight
                # duplicate waiting on this ask, then release.
                if _inf_entry is not None:
                    _inf_entry["result"] = result
                    _inflight_release(_inf_key)
                self._json(result)

            no_judging = {"mode": "none",
                          "errors": [],
                          "annotation": "",
                          "checks": _empty_checks_block()}
            if not hits:
                # v16 item 7: zero corpus hits is an honest IDK
                # disposition: branch out to the official web
                # before abstaining. Web excerpts become the
                # unit's evidence (marked source='web').
                web_b = _branch_attempt([])
                if web_b:
                    hits = web_b
                else:
                    missing = ["no documents in the selected corpus "
                               "matched the question"]
                    log_gap(question, "idk", "sufficiency", missing)
                    n_gaps += 1
                    _respond(_idk_answer(
                        missing, hits,
                        "No matching documents were found in the selected "
                        "corpus."), "idk", no_judging, [],
                        {"ungrounded": [], "regenerated": False},
                        "none (abstained before generation)",
                        False, False, "", 0, 0)
                    return
            # --- retrieval sufficiency gate (plan section 1): the
            # excerpt set must contain each matched mechanism's
            # governing material. Retry once with the mechanisms'
            # own searches; still missing -> IDK, no generation
            # around the hole.
            if mechanisms_all and not discovery:
                t_suff = time.monotonic()
                missing_mechs = _sufficiency_missing(mechanisms_all,
                                                     hits)
                if missing_mechs:
                    hits = _drop_payee_admin(
                        question,
                        _sufficiency_retry(corp, hits, missing_mechs))
                    missing_mechs = _sufficiency_missing(mechanisms_all,
                                                         hits)
                sufficiency_ms = int((time.monotonic() - t_suff) * 1000)
                if missing_mechs:
                    # v16 item 7: a sufficiency IDK is an honest
                    # IDK disposition: branch out, merge any web
                    # excerpts (corpus first, web appended), and
                    # proceed only if the union covers the
                    # mechanism; otherwise the IDK stands.
                    web_b = _branch_attempt(
                        [{"text": m["missing"]}
                         for m in missing_mechs])
                    if web_b:
                        seen_k = {(h["url"], h["excerpt"][:60])
                                  for h in hits}
                        merged = hits + [
                            h for h in web_b
                            if (h["url"], h["excerpt"][:60])
                            not in seen_k]
                        if not _sufficiency_missing(mechanisms_all,
                                                    merged):
                            hits = merged
                            missing_mechs = []
                if missing_mechs:
                    missing = [m["missing"] for m in missing_mechs]
                    log_gap(question, "idk", "sufficiency", missing)
                    n_gaps += 1
                    _respond(_idk_answer(
                        missing, hits,
                        "The documents retrieved do not cover the "
                        "mechanism this question turns on."),
                        "idk", no_judging, [],
                        {"ungrounded": [], "regenerated": False},
                        "none (abstained before generation)",
                        False, False, "", 0, 0)
                    return
            # v16 item 8: freeze the retrieval split here (initial
            # retrieval + sufficiency retries); later searches
            # (refutation, hunts, per-part retrieval) are judging
            # work, not this ask's retrieval cost.
            retr_split = _retr_snapshot()
            # --- answering units: per part for bundled questions
            # (v13 decomposition; each part verified on its own), a
            # single unit otherwise. Discovery runs as one unit
            # under its survey instructions.
            engine = "gemini" if use_gemini else "local"
            draft_model = model if use_gemini else routed_model
            # v26 (F8): the v13 VRAM eviction that freed the
            # GPU for the routed 14B is deleted with the
            # route — a hard ask no longer unloads resident
            # models for a model that will never serve.
            hard = _hard_q or bool(mechanisms_all)
            # v23.2 (F1): fix the ask's class deadline NOW
            # (150s simple / 285s hard from ask start). The
            # units below carry it in their ctx, and _run_unit
            # reads the same anchor from the ask context.
            class_deadline = _ask_clock_class(hard)
            unit_specs = []
            if parts and not discovery:
                part_reg_ids = []
                part_mech_ids = []
                # v18 (R8): ONE bundled retrieval serves the
                # whole question — the per-part hits come from
                # the same pass that produced `hits` above
                # (single depth, shared), instead of a fresh
                # hybrid_search per part.
                _per_part = {}
                if EMBEDDINGS:
                    _mh, _per_part = _retrieve_bundled(
                        question, corp, n_ex,
                        use_rewrite=use_rewrite,
                        use_rerank=use_rerank)
                for part in parts:
                    p_reg = _registry_matches(part)
                    p_mechs = _mechanism_matches(part)
                    part_reg_ids += [e["id"] for e in p_reg]
                    part_mech_ids += [m["id"] for m in p_mechs]
                    p_hits = []
                    if EMBEDDINGS:
                        p_hits = list(_per_part.get(part) or [])
                    else:
                        p_hits = search(part, corpus=corp, n=6)
                    p_hits = _drop_payee_admin(part, p_hits)
                    if len(p_hits) < 2:
                        seen_k = {(h["url"], h["excerpt"][:60])
                                  for h in p_hits}
                        p_hits += [h for h in hits
                                   if (h["url"], h["excerpt"][:60])
                                   not in seen_k]
                    p_csra = None
                    if any(e["id"] == "csra" for e in p_reg):
                        p_csra = compute_csra(part)
                    p_allowed = _registry_values(
                        p_reg, on_date=_question_date(part)) + [
                        v for v in allowed_extra
                        if v not in _registry_values(
                            p_reg, on_date=_question_date(part))]
                    if p_csra:
                        p_allowed += [p_csra["assets"], p_csra["half"],
                                      p_csra["csra"]]
                    unit_specs.append({
                        "unit_q": part, "part": part, "hits": p_hits,
                        "registry_entries": p_reg, "mechanisms": p_mechs,
                        "csra_comp": p_csra, "allowed_extra": p_allowed,
                        # v18 (F13): the full question rides the
                        # part's drafting instructions as context.
                        "instructions": (
                            "Context: this part belongs to the "
                            f"full question: {question}"),
                        "reg_in_instructions": False})
                # Registry/mechanism entries that match the whole
                # question but no single part (the splitter can split
                # a trigger across parts): their figures stay allowed
                # everywhere (folded into p_allowed above), and the
                # mechanism check runs on the assembled answer below.
                global_mechs = [m for m in mechanisms_all
                                if m["id"] not in part_mech_ids]
            else:
                global_mechs = []
                unit_specs.append({
                    "unit_q": question, "part": None, "hits": hits,
                    "registry_entries": registry_entries,
                    "mechanisms": mechanisms_all,
                    "csra_comp": csra_comp,
                    "allowed_extra": allowed_extra,
                    "instructions": instructions,
                    "reg_in_instructions": True})
            prepared = []
            for spec in unit_specs:
                ctx = {"question": question, "unit_q": spec["unit_q"],
                       "hits": spec["hits"], "engine": engine,
                       "draft_model": draft_model,
                       "instructions": spec["instructions"],
                       "registry_entries": spec["registry_entries"],
                       "mechanisms": spec["mechanisms"],
                       "csra_comp": spec["csra_comp"],
                       "allowed_extra": spec["allowed_extra"],
                       "hard": hard,
                       "reg_in_instructions":
                           spec["reg_in_instructions"],
                       # v15: corpus for refutation/hunt retrieval,
                       # the ask deadline, and the explicit-model
                       # guard. (v23: the divergence-eligibility
                       # key died with the oracle.)
                       "corp": corp, "deadline": deadline,
                       # v23.2 (F1): the ask's class deadline
                       # rides the ctx alongside the outer cap;
                       # _run_unit takes the earlier of the two
                       # (plus the ask-context anchor).
                       "class_deadline": class_deadline,
                       "explicit_model": bool(
                           model and not use_gemini
                           and model != LOCAL_MODEL),
                       # v18: the honest local mode (F19), the
                       # cache-bypass flag (fresh asks never
                       # read the answer cache), and the full
                       # question for part checking (F13).
                       "local_mode": local_mode,
                       # v24 (§8): this ask's seat picks.
                       "draft_seat_model": seat_local["draft"],
                       "judge_model": seat_local["judge"],
                       "extract_model": seat_local["extract"],
                       "fresh": fresh,
                       "full_question": question
                       if spec["part"] else None}
                prepared.append((spec, ctx))

            # v24 (§6.4): one progress tracker per unit,
            # created before any unit runs so the snapshot
            # shows every part from the first frame.
            _trk_units = []
            for _i, (_spec, _ctx) in enumerate(prepared):
                _tu = _trk_new_unit(_i, _spec["part"])
                if ask_mode == "quick":
                    # v25: Quick units stamp their own
                    # sections; the phase hook leaves them
                    # alone (see _trk_phase).
                    _tu["quick"] = True
                _ctx["trk"] = _tu
                _trk_units.append(_tu)
            _actx.tracker = {"units": _trk_units,
                             "parts": len(_trk_units)}

            def _run_prepared(pc):
                _check_cancel()  # v24 (§5.2): the Stop seam
                _spec, _ctx = pc
                # v23.2 (F1): between parts the CLASS deadline
                # binds too, not only the 600s outer cap — a
                # part that would start past its class budget
                # is a time-gated IDK, exactly as at the cap.
                if time.monotonic() > min(
                        deadline, _ctx.get("class_deadline")
                        or deadline):
                    # Cap reached between parts: this part is an
                    # IDK with a time gate, never a rushed answer.
                    return {
                        "answer": None, "badge": "idk", "served": None,
                        "fallback": False, "downgraded": False,
                        "prompt": "", "rungs": 0, "judge_ms": 0,
                        "refute_ms": 0, "cap_hit": True,
                        "cost_capped": False, "draft_text": None,
                        "conflict_fail": False,
                        "figure_check": {"ungrounded": [],
                                         "regenerated": False},
                        "judging": {"mode": "none",
                                    "annotation": "",
                                    "errors": ["ask time cap "
                                               "reached before "
                                               "this part ran"],
                                    "checks": no_judging["checks"]},
                        "budget_cut": True,
                        "cut_phase": "between parts",
                        "claims": [],
                        "missing": ["the ask reached its time "
                                    f"budget ({ASK_TIME_CAP_S} "
                                    "seconds) before this part "
                                    "could be verified"],
                        "gate": "time", "divergence": None}
                return _run_unit(_ctx)

            def _run_prepared_quick(pc):
                """v25: one Quick unit, under the same
                between-parts class-deadline gate as Deep
                (a part that would start past its class
                budget is a time-gated IDK, never a rushed
                answer)."""
                _check_cancel()  # v24 (§5.2): the Stop seam
                _spec, _ctx = pc
                if time.monotonic() > min(
                        deadline, _ctx.get("class_deadline")
                        or deadline):
                    return {
                        "answer": None, "badge": "idk",
                        "served": None,
                        "fallback": False, "downgraded": False,
                        "prompt": "", "rungs": 0, "judge_ms": 0,
                        "refute_ms": 0, "cap_hit": True,
                        "cost_capped": False, "draft_text": None,
                        "conflict_fail": False,
                        "figure_check": {"ungrounded": [],
                                         "regenerated": False},
                        "judging": {"mode": "quick",
                                    "annotation": "",
                                    "errors": ["ask time cap "
                                               "reached before "
                                               "this part ran"],
                                    "checks":
                                        _empty_checks_block()},
                        "budget_cut": True,
                        "cut_phase": "between parts",
                        "claims": [],
                        "missing": ["the ask reached its time "
                                    f"budget ({ASK_TIME_CAP_S} "
                                    "seconds) before this part "
                                    "could be checked"],
                        "gate": "time", "divergence": None,
                        "events": [], "retr_pools": [],
                        "lead_repairs": 0,
                        "stripped_sentences": [],
                        "draft_calls": _ask_ctx().draft_calls,
                        "draft_capped": False,
                        "assembly_guard": None}
                return _run_unit_quick(_ctx)

            # v18 (R8): bundled units run in PARALLEL (max 3
            # workers). v26 (F16) correction: the units do NOT
            # each ride their own _AskContext — ONE shared
            # _AskContext is propagated into every worker by
            # _submit, so cost, draft-call accounting, and
            # events are ask-wide BY DESIGN (F10 made the
            # context itself thread-safe; per-unit state rides
            # each unit's own ctx dict). The shared stage line is
            # last-writer-wins across units (documented nuance),
            # and per-unit events in parallel mode are the units'
            # full ask events. The branch-out re-run below stays
            # serial: it is rare and spends Tavily credits.
            if ask_mode == "quick":
                # v25: Quick units run the same way (parallel
                # when bundled) through the Quick runner.
                if len(prepared) > 1:
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        _futs = [_submit(pool, _run_prepared_quick,
                                         pc) for pc in prepared]
                        raw_units = [f.result() for f in _futs]
                else:
                    raw_units = [_run_prepared_quick(pc)
                                 for pc in prepared]
            elif len(prepared) > 1:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    _futs = [_submit(pool, _run_prepared, pc)
                             for pc in prepared]
                    raw_units = [f.result() for f in _futs]
            else:
                raw_units = [_run_prepared(pc) for pc in prepared]
            units = []
            for (spec, ctx), unit in zip(prepared, raw_units):
                unit_hits = spec["hits"]
                if ask_mode == "quick":
                    # v25: a Quick unit is final when its
                    # runner returns — no rescue, no web
                    # branch, no badge translation. Only the
                    # tracker close + the gap log remain.
                    _tu = ctx.get("trk")
                    if _tu is not None:
                        _tu["status"] = "done"
                        if _tu["t0"] is not None:
                            _tu["elapsed"] = round(
                                time.monotonic() - _tu["t0"], 1)
                        if unit.get("gate") == "time" \
                                and _tu["t0"] is None:
                            _tu["caption"] = (
                                "Skipped \u00b7 time budget spent")
                    unit["part"] = spec["part"]
                    unit["unit_q"] = spec["unit_q"]
                    unit["hits"] = unit_hits
                    units.append(unit)
                    if unit["badge"] == "idk":
                        log_gap(question, "idk",
                                unit["gate"] or "checking",
                                unit["missing"],
                                part=spec["part"])
                        n_gaps += 1
                    continue
                if (unit["badge"] == "idk"
                        or unit["badge"].startswith("partial")) \
                        and unit["gate"] != "time" \
                        and (unit["judging"] or {}).get("mode") \
                        != "none":
                    # v16 item 7: the unit's honest disposition
                    # is IDK (sufficiency, ladder, refutation and
                    # arbitration all ran): branch out and
                    # re-run the unit ONCE on the union evidence
                    # (corpus hits first, web excerpts appended)
                    # through the same pipeline and gates. The
                    # corpus governs any conflict (source-conflict
                    # machinery runs inside the re-run).
                    failed_claims = [
                        c for c in unit.get("claims", ())
                        if c["final"] != "supported"
                        and not c.get("meta")]
                    # v21 (WS1a, trigger ii; calibration
                    # widened the same day at the operator's
                    # direction): a unit that ends IDK — or in
                    # the partial tier, where some claims
                    # demonstrably lacked evidence — gets the
                    # exact-term rescue over the CORPUS before
                    # any web branch-out spends a Tavily
                    # search. When the rescue injects chunks
                    # the unit never saw, the unit ALWAYS
                    # re-runs once on the enlarged evidence
                    # (the branch-out's union shape, corpus
                    # first) through the same gates; the
                    # re-run is adopted only when its badge
                    # strictly outranks the original, so the
                    # rescue can complete a partial but never
                    # downgrade one.
                    _check_cancel()  # v24 (§5.2): the Stop seam
                    _seen_r = {(h["url"], h["excerpt"][:60])
                               for h in spec["hits"]}
                    _rescue_r, _rterms_r = _rescue_search(
                        spec["unit_q"], ctx["corp"],
                        exclude_keys=_seen_r, limit=4)
                    if _rescue_r:
                        _note_rescue(_rterms_r, len(_rescue_r),
                                     "post_idk", spec["unit_q"])
                        _ctx_r = dict(ctx)
                        _ctx_r["hits"] = (list(spec["hits"])
                                          + _rescue_r)
                        # v23.2 (BUG-11 F1+F2): the rescue
                        # re-run lives INSIDE the ask's one
                        # clock and the shared draft cap.
                        # Through v23.1 it started unconditionally
                        # and _run_unit re-armed it a fresh class
                        # budget — the CSRA ask's rescue unit
                        # drafted 5 more times after the ladder's
                        # 6, none of it bounded by the 285s the
                        # ask was supposed to cost. When the
                        # budget or the draft cap is spent, the
                        # re-run does not start; the cut is
                        # recorded on the surviving unit.
                        _skip_r = None
                        if not _call_may_start(class_deadline):
                            _note_budget_cut("rescue")
                            unit["cap_hit"] = True
                            unit["budget_cut"] = True
                            if not unit.get("cut_phase"):
                                unit["cut_phase"] = "rescue"
                            _skip_r = "the ask's time budget " \
                                      "is spent"
                        elif _ask_ctx().draft_calls \
                                >= DRAFT_CALL_CAP:
                            unit["draft_capped"] = True
                            _skip_r = "the ask's draft-call " \
                                      "cap is reached"
                        _unit_r = None
                        if _skip_r is not None:
                            unit["rescue"] = {
                                "terms": _rterms_r[:6],
                                "injected": len(_rescue_r),
                                "urls": [h["url"]
                                         for h in _rescue_r],
                                "skipped": "rescue re-run not "
                                           "started: "
                                           + _skip_r}
                        else:
                            _tu_r = ctx.get("trk")
                            if _tu_r is not None:
                                _tu_r["pass"] += 1
                                _tu_r["caption"] = (
                                    f"Pass {_tu_r['pass']} "
                                    "\u00b7 after rescue")
                                _trk_set(_tu_r, "rescue", "active")
                            _unit_r = _run_unit(_ctx_r)
                        if _unit_r is not None:
                            _unit_r["rescue"] = {
                                "terms": _rterms_r[:6],
                                "injected": len(_rescue_r),
                                "urls": [h["url"]
                                         for h in _rescue_r]}
                            _BADGE_RANK_R = {
                                "idk": 0, "partial": 1,
                                "checked_det": 4,
                                "verified_web_reduced": 4,
                                "checked": 6, "verified_web": 6}
                            if _BADGE_RANK_R.get(
                                    _unit_r["badge"], 0) \
                                    > _BADGE_RANK_R.get(
                                        unit["badge"], 0):
                                unit = _unit_r
                                unit_hits = _ctx_r["hits"]
                            else:
                                unit["rescue"] = \
                                    _unit_r["rescue"]
                    if unit["badge"] == "idk":
                        _check_cancel()  # v24 (§5.2): Stop seam
                        web_b = _branch_attempt(failed_claims)
                    else:
                        web_b = []
                    if web_b and (not _call_may_start(
                            class_deadline)
                            or _ask_ctx().draft_calls
                            >= DRAFT_CALL_CAP):
                        # v23.2 (F1+F2): the branch re-run is
                        # governed by the same one clock + draft
                        # cap; it does not start when either is
                        # spent (the branch search itself was
                        # already gated in _branch_attempt).
                        if not _call_may_start(class_deadline):
                            _note_budget_cut("branch")
                            unit["cap_hit"] = True
                            unit["budget_cut"] = True
                            if not unit.get("cut_phase"):
                                unit["cut_phase"] = "branch"
                        else:
                            unit["draft_capped"] = True
                        unit["web"] = {
                            "branched": True,
                            "urls": [h["url"] for h in web_b],
                            "fetched": web_b[0]["fetched"],
                            "skipped": "branch re-run not "
                                       "started: the ask's "
                                       "budget / draft cap is "
                                       "spent"}
                    elif web_b:
                        ctx2 = dict(ctx)
                        ctx2["hits"] = list(spec["hits"]) + web_b
                        _tu_b = ctx.get("trk")
                        if _tu_b is not None:
                            _tu_b["pass"] += 1
                            _tu_b["caption"] = (
                                f"Pass {_tu_b['pass']} "
                                "\u00b7 web branch")
                            _trk_set(_tu_b, "web_branch", "active")
                        unit2 = _run_unit(ctx2)
                        web_rec = {
                            "branched": True,
                            "urls": [h["url"] for h in web_b],
                            "fetched": web_b[0]["fetched"]}
                        unit2["web"] = web_rec
                        if unit2["badge"] != "idk":
                            unit = unit2
                            unit_hits = ctx2["hits"]
                        else:
                            unit["web"] = web_rec
                _apply_web_badge(unit, unit_hits)
                # v24 (§6.3): this unit's rescue / web-branch
                # decision points are now known — finalize its
                # tracker from the unit record itself.
                _tu = ctx.get("trk")
                if _tu is not None:
                    _tu["status"] = "done"
                    if _tu["t0"] is not None:
                        _tu["elapsed"] = round(
                            time.monotonic() - _tu["t0"], 1)
                    if unit.get("gate") == "time" \
                            and _tu["t0"] is None:
                        _tu["caption"] = ("Skipped \u00b7 time "
                                          "budget spent")
                    _rs = unit.get("rescue")
                    if isinstance(_rs, dict) and _rs.get("skipped"):
                        _trk_set(_tu, "rescue", "skipped",
                                 detail="time budget spent"
                                 if "time budget" in _rs["skipped"]
                                 else "draft limit reached")
                    elif _rs:
                        _trk_set(_tu, "rescue", "done")
                    elif unit["badge"] in (
                            "checked", "verified_web",
                            "checked_det", "verified_web_reduced"):
                        _trk_set(_tu, "rescue", "not-needed")
                    elif unit["badge"] in ("idk", "partial") \
                            and unit.get("gate") != "time" \
                            and (unit.get("judging") or {}).get(
                                "mode") != "none":
                        _trk_set(_tu, "rescue", "done",
                                 detail="no new documents")
                    _wb = unit.get("web")
                    if isinstance(_wb, dict) and _wb.get("skipped"):
                        _trk_set(_tu, "web_branch", "skipped",
                                 detail="time budget spent"
                                 if "time budget" in _wb["skipped"]
                                 else "draft limit reached")
                    elif _wb and _wb.get("branched"):
                        _trk_set(_tu, "web_branch", "done")
                    elif unit["badge"] != "idk":
                        _trk_set(_tu, "web_branch", "not-needed")
                    elif not ask_web.get("attempted"):
                        _trk_set(_tu, "web_branch", "not-needed")
                unit["part"] = spec["part"]
                unit["unit_q"] = spec["unit_q"]
                unit["hits"] = unit_hits
                units.append(unit)
                if unit["badge"] == "idk":
                    log_gap(question, "idk", unit["gate"] or "checking",
                            unit["missing"], part=spec["part"])
                    n_gaps += 1
                # v23.1: claims the deterministic checks killed
                # feed the growth loop too (the panel era fed it
                # refutation-killed claims; the refutation
                # machinery is deleted).
                for c in unit.get("claims", ()):
                    if c.get("det_figure_fail") \
                            and c["final"] != "supported":
                        log_gap(question, unit["badge"], "figure_check",
                                [f"claim failed the deterministic "
                                 f"figure check: \"{c['text']}\""],
                                part=spec["part"])
                        n_gaps += 1
            # --- assembly + badges ----------------------------------
            if len(units) == 1:
                u = units[0]
                answer_text = u["answer"] if u["answer"] is not None \
                    else _idk_answer(
                        u["missing"], hits,
                        "I could not verify an answer to this question "
                        "against the source documents.")
                overall_badge = u["badge"]
            else:
                sections = []
                for i, u in enumerate(units):
                    head = f"{i + 1}. {u['part']}"
                    if u["answer"] is not None:
                        sections.append(head + "\n" + u["answer"])
                    else:
                        sections.append(head + "\n" + _idk_answer(
                            u["missing"], u["hits"],
                            "For this part, the documents do not "
                            "settle it."))
                answer_text = "\n\n".join(sections)
                badges = [u["badge"] for u in units]
                # v18 (§C): the assembly badge is the MINIMUM
                # over units by explicit rank (verified_web sits
                # at the top rank and surfaces only when every
                # unit is top-rank and at least one unit is
                # web-sourced). Any IDK unit caps the assembly
                # at the partial tier. v23.1 (WS-C): the rank
                # table carries the new badge names — checked
                # at the top with verified_web, checked_det at
                # the reduced tier with verified_web_reduced,
                # and ONE partial name (the panel era's
                # partial_single / partial_reduced split is
                # gone, so a capped assembly is just "partial").
                _BADGE_RANK = {"idk": 0, "partial": 1,
                               # v25: a Quick part sits above
                               # partial and below every judged
                               # tier (Deep units never carry
                               # it; the lanes never mix in one
                               # ask).
                               "quick": 2,
                               "checked_det": 4,
                               "verified_web_reduced": 4,
                               "checked": 6, "verified_web": 6}
                _answered = [b for b in badges if b != "idk"]
                if not _answered:
                    overall_badge = "idk"
                else:
                    _worst = min(_answered,
                                 key=lambda b: _BADGE_RANK.get(b, 0))
                    if len(_answered) < len(badges):
                        overall_badge = "partial"
                    elif _BADGE_RANK.get(_worst, 0) >= 6:
                        overall_badge = "verified_web" \
                            if "verified_web" in badges \
                            else "checked"
                    else:
                        overall_badge = _worst
                if global_mechs:
                    g_fail = _mech_required_failures(
                        answer_text, global_mechs, csra_comp)
                    if g_fail:
                        labels = "; ".join(f["label"] for f in g_fail)
                        answer_text += (
                            "\n\nNote: the retrieved documents do not "
                            "fully settle every part of this question "
                            f"(missing: {labels}).")
                        if overall_badge != "idk":
                            overall_badge = "partial"
                        log_gap(question, overall_badge, "mechanism",
                                [f["label"] for f in g_fail])
                        n_gaps += 1
            modes = {u["judging"]["mode"] for u in units}
            # v23.1: aggregate the units' checks records: who
            # judged (the union of serving labels), whether the
            # backup fired anywhere, and the deterministic
            # failure totals across units.
            _served_by = []
            _fig_failed = []
            _cit_failed = []
            _backup_any = False
            _verdict_totals = {"supported": 0, "contradicted": 0,
                               "not_addressed": 0}
            for u in units:
                _uc = (u["judging"].get("checks") or {})
                _uj = _uc.get("judge") or {}
                if _uj.get("served_by") \
                        and _uj["served_by"] not in _served_by:
                    _served_by.append(_uj["served_by"])
                _backup_any = _backup_any \
                    or bool(_uj.get("backup_used"))
                for _k, _v in (_uj.get("verdicts") or {}).items():
                    _verdict_totals[_k] = \
                        _verdict_totals.get(_k, 0) + _v
                _fig_failed.extend(
                    (_uc.get("figures") or {}).get("failed") or [])
                _cit_failed.extend(
                    (_uc.get("citations") or {}).get("failed")
                    or [])
            judging = {"mode": modes.pop() if len(modes) == 1
                       else "mixed",
                       "checks": {
                           "figures": {
                               "checked": sum(
                                   ((u["judging"].get("checks")
                                     or {}).get("figures") or {})
                                   .get("checked", 0)
                                   for u in units),
                               "failed": _fig_failed},
                           "citations": {"failed": _cit_failed},
                           "judge": {
                               "served_by": " + ".join(_served_by)
                               or None,
                               "backup_used": _backup_any,
                               "verdicts": _verdict_totals},
                           "registry": list(dict.fromkeys(
                               e for u in units
                               for e in (((u["judging"].get(
                                   "checks") or {}).get("registry"))
                                   or [])))}}

            _annots = []
            for u in units:
                _a = (u["judging"] or {}).get("annotation")
                if _a and _a not in _annots:
                    _annots.append(_a)
            judging["annotation"] = " · ".join(_annots)
            verification = [{
                "part": u["part"], "badge": u["badge"],
                "claims": len(u["claims"]),
                "failed_claims": [c["text"] for c in u["claims"]
                                  if c["final"] != "supported"
                                  and not c.get("meta")],
                "gate": u["gate"], "rungs": u["rungs"],
                "judging_mode": u["judging"]["mode"],
                "divergence": u.get("divergence"),
                "events": u.get("events"),
                "lead_repairs": u.get("lead_repairs"),
                "stripped_sentences": u.get("stripped_sentences"),
                "web": u.get("web"),
                "web_used": bool(u.get("web_used"))}
                for u in units]
            figure_check = {
                "ungrounded": sorted({v for u in units
                                      for v in
                                      u["figure_check"]["ungrounded"]}),
                "regenerated": any(u["figure_check"]["regenerated"]
                                   for u in units)}
            served_labels = []
            for u in units:
                if u["served"] and u["served"] not in served_labels:
                    served_labels.append(u["served"])
            if len(served_labels) == 1:
                served = served_labels[0]
            elif served_labels:
                served = "mixed: " + ", ".join(served_labels)
            else:
                served = "none (generation failed)"
            _respond(answer_text, overall_badge, judging, verification,
                     figure_check, served,
                     any(u["fallback"] for u in units),
                     any(u["downgraded"] for u in units),
                     units[0]["prompt"] if units else "",
                     max((u["rungs"] for u in units), default=0),
                     sum(u["judge_ms"] for u in units),
                     refute_ms=sum(u.get("refute_ms", 0)
                                   for u in units),
                     # v23.2 (F1): the ask-level cut record
                     # counts too — a cut in a discarded re-run
                     # or a skipped rescue still cut THIS ask.
                     cap_hit=any(u.get("cap_hit") for u in units)
                     or _ask_ctx().budget_cut)
        except _AskCancelled:
            # v24 (§5.2): the Stop button landed at a seam.
            # The record says cancelled; the response carries
            # the wall time so the page freezes its timer at
            # "Stopped after m:ss". No answer ships.
            if _inf_entry is not None:
                _inflight_release(_inf_key)
                _inf_entry = None
            _wall = round(time.monotonic() - t_start, 3)
            try:
                _record_ask({
                    "kind": "ask", "ask_id": _ask_ctx().ask_id,
                    "ts": time.time(), "wall_s": _wall,
                    "question": question, "corpus": corpus_name,
                    "mode": ask_mode,  # v25: the lane that ran
                    "local": bool(local_mode or any_seat_local),
                    "seat_models": dict(seat_models),
                    "seat_substitution": seat_substitution,
                    "badge": "cancelled", "status": "cancelled",
                    "cost_usd": _or_ask_cost(),
                    "phase_ms": _phase_ms_snapshot()})
            except Exception:
                pass
            self._json({"cancelled": True, "wall_s": _wall,
                        "answer": "", "badge": None,
                        "question": question})
        except Exception as e:
            if _inf_entry is not None:
                _inflight_release(_inf_key)
            self._json({"error": str(e)[:300]}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"FSSA Q&A server: {len(CORPUS_IN)} IN docs + {len(CORPUS_FED)} federal docs on port {PORT}", flush=True)
    # v20.2: the embeddings/corpora consistency check (WARN
    # only; see _run_consistency_check). Runs before serving
    # so the boot log and the first /health read agree.
    _run_consistency_check()
    # v17: read the OpenRouter key's state once at startup (in
    # a daemon thread, so a slow provider never delays the
    # listener); /health serves the cached state from there.
    def _or_warm():
        ks = _or_key_state(force=True)
        d = ks.get("data") or {}
        print(f"[openrouter] key state: limit={d.get('limit')} "
              f"limit_remaining={d.get('limit_remaining')} "
              f"usage={d.get('usage')} error={ks.get('error')} "
              f"spend_today=${_or_spend_today():.4f} "
              f"cap=${OPENROUTER_DAILY_CAP_USD:.2f}", flush=True)

    threading.Thread(target=_or_warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
