# FSSA search server v20 — STATUS

Built 2026-10-14 (Stage 1, VM only). Source: `~/workspace/fssa-qa/v19/`
copied to `~/workspace/fssa-qa/v20/`; only `fssa_search_server.py` was
modified (this STATUS replaces the copied v19 one). **No PC access, no
live API calls, no money spent, no deploy, no restart.** Verification =
`py_compile` (clean) + offline code review + a stubbed-corpus import
smoke in /tmp (results below). The live server still runs v19. Stage 2
(deploy) and Stage 3 (grading) each need the operator's explicit go.

## What v20 is

The free-first route rewire under the operator's **layer model**: strict
free-before-paid layering in every seat/role chain, with the v19
latency ordering applying INSIDE a layer only (layer wins in
`_plan_panel`'s key and in `_seat_pick`'s scan, via `_candidate_layer`).
All v19 verification machinery is intact and unweakened: gates,
badges, the §C diversity guard, panel planning, completeness repair
(G1), the fast lane (G5), ledger caps, caches.

- **Layer 0 — free + fast:** Groq seats; Gemini 3.5 Flash-Lite via the
  free Google path.
- **Layer 1 — free + strong + scarce:** Gemini 3.8 Flash, rationed
  mid-ladder (below).
- **Layer 2 — free + slow:** NIM seats.
- **Layer 3 — paid:** OpenRouter + xAI, under the existing ledger caps.
- **Layer 4 — local last** (cold-load rule), then fail-closed IDK.

### The chains as built

- **Hard drafting:** Groq `openai/gpt-oss-120b` → NIM bench (Kimi K3,
  GLM-5.3, Nemotron Ultra, DeepSeek V4.1-Flash) → xAI `grok-4.7` →
  OpenRouter `openai/gpt-oss-120b` → local.
- **Simple drafting:** Groq `openai/gpt-oss-20b` → NIM bench
  (`openai/gpt-oss-20b`, GLM-5.3-Flash, Nemotron 3.5 Lightning) →
  OpenRouter `openai/gpt-oss-20b` → local.
- **Utilities:** Groq 20B → NIM simple seat → OpenRouter 20B → the
  v19 degrade-to-empty terminal (utilities still never load local
  models, per v15.1).
- **Judge seat A (Google):** Gemini Lite free path (primary extractor,
  unchanged) → the same model via OpenRouter (`OR_LITE_MODEL`,
  `google/gemini-3.5-flash-lite`) → the seat-B chain as extractor →
  local. The Flash mid-ladder rung was NOT added to seat A in the
  first pass: the layer directive named seats B and C for that rung.
  **Resolved by fix 2 in the v20.1 addendum below:** the rung now sits
  between the free Lite path and the OpenRouter Lite attempt.
- **Judge seat B (DeepSeek):** NIM `deepseek-ai/deepseek-v4.1-flash`
  → NIM Nemotron Super → Gemini 3.8 Flash (slot 6, rationed) →
  OpenRouter `deepseek/deepseek-chat-v3.1` → (pipeline local).
- **Judge seat C (Alibaba):** Groq `qwen/qwen3.8-27b` → Gemini 3.8
  Flash (slot 6, rationed) → OpenRouter `qwen/qwen3-235b-a22b-2507`
  → (pipeline local). **Extended by fix 1 in the v20.1 addendum
  below:** the v19 tail (NIM Nemotron Ultra, then Llama-4-Maverick)
  is restored behind the 235B.

### Seat B's history (why the chain looks like this)

The build passed through three forms in one day, all the operator's calls:
(1) V3.1 stays primary as a documented free-first exception (the
proven judge had no free home); (2) the exception was removed,
V4.1-Flash became the free NIM primary; (3) the layer-model revision
put Nemotron Super and the rationed Flash rung between V4.1-Flash and
the paid V3.1 fallover. Form (3) is what shipped here. **V4.1-Flash is
UNGRADED as a primary judge.** Stage 3 grading compares this panel
against the v19 panel (V3.1 primary) on the 12 anchors before the
free-first panel is trusted; the seat order is a config constant and
reverts in one edit.

### Seat C's dropped tail (deliberate, flagged)

the operator's concrete seat-C chain ends at the OpenRouter Qwen3-235B
fallover, so the v19 tail (Llama-4-Maverick on OpenRouter, NIM
Nemotron Ultra) is NOT in seat C's candidate list anymore. Both models
remain in the file (`SEAT2_FAILOVER_MODEL` stays defined and priced;
Ultra still serves the hard_draft bench), and restoring either is a
one-line config change. This was read as deliberate spec, not an
oversight; flagging it here so it can be reversed at deploy if wanted.
**Resolved 2026-10-14: the operator approved restoring the tail; see fix 1
in the v20.1 addendum below.**

## New providers

Both clients ride the existing OpenAI-compatible transport
(`_nim_text`), keyed by the `*_key.txt` server-dir pattern (env var
first, then file; a missing file = provider unavailable, never a
crash — the `_nim_key_for` pattern). New pseudo-slots: **4 = xAI**,
**5 = Groq**, **6 = the rationed Flash rung** (no key file; served by
`_gemini_text`). Panel/provider labels resolve through
`_slot_provider_name` everywhere (no more hardcoded "nim").

- **xAI** — base `https://api.x.ai/v1`, key `xai_key.txt`. Models:
  `grok-4.7` (hard drafting, paid layer), `grok-4.20-0309-non-reasoning`
  defined as `XAI_FAST_MODEL` (unused by default chains).
- **Groq** — base `https://api.groq.com/openai/v1`, key
  `groq_key.txt`. Models: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
  `qwen/qwen3.8-27b` (constants `GROQ_HARD_MODEL`, `GROQ_SIMPLE_MODEL` /
  `GROQ_UTILITY_MODEL`, `GROQ_QWEN_MODEL` — the exact strings to swap
  if a future catalog pull renames anything).

### xAI is an ordinary PAID provider (resolved fact)

The $150/mo data-sharing free-credits program was **sunset at the end
of May 2025** (official xAI email to participants; found by the operator
2026-10-14), and the $25 signup promo never materialized. No grant
exists. By default xAI calls reserve and settle through the OpenRouter
reservation gate (`_or_reserve` / `_or_settle`) and count against the
$2.00/day ledger and the $0.50 per-ask ceiling exactly like OpenRouter
calls; when the cap trips, xAI seats gate off like OpenRouter seats.
The flag **`XAI_FREE_CREDITS = False`** is a permanent fact, kept only
so a future grant program could switch xAI to the visibility-only
credit ledger (`xai_spend.json`, `xai_credit_today` in /health)
without code surgery. Do not flip it on the basis of docs or roundups;
the 2025 sunset is why this file treats stale program pages as
unverified.

### Groq daily guard (spec item 4)

Persistent per-day counter in `groq_usage.json` (Indiana ledger day,
atomic writes, the spend ledger's pattern). Soft cap **900/day**
(`FSSA_GROQ_DAILY_SOFT_CAP`): past it, `_slot_has_key(5)` goes false
and every chain skips Groq to its fallover until the next ledger day;
sheds are logged once per ask in the ask record
(`{"kind": "groq_shed", ...}` event). Requests count at attempt time.
An unreadable counter sheds Groq (fail-safe). Completed calls also
accrue their list-price value in the same file for visibility
(`provider_tier=free`); free-tier calls never touch the real-money
ledger.

### Gemini 3.8 Flash mid-ladder ration (Layer 1)

Slot 6 in judge seats B and C. Persistent per-day counter in
`flash_mid_usage.json`; sub-cap **10/day** (`FSSA_FLASH_MID_DAILY_CAP`)
so mid-ladder duty can never consume the half of Flash's 20/day free
quota reserved for final-rung/arbitration work (the final-rung path
keeps its own `FLASH_DAILY_CAP = 18` code cap). Counted at attempt
time. The rung records **as-served** (a google voice): it never
impersonates the seat's family for badges — `_panel_state` counts a
Flash-served seat as degraded, and the §C plan will not assign Flash
to both seats in one round (verified in the smoke test below).

### Money and prices

- Reservation discipline unchanged for OpenRouter; xAI (paid mode)
  reuses the same gate with its own price table. Failures and
  usage-less responses settle at the full reserved maximum (F11-1).
  xAI/Groq transports report token usage via `_nim_text(usage_out=…)`;
  billed figures are computed from usage at list prices (the providers
  return tokens, not USD — see UNVERIFIED).
- New price rows (USD per 1M in/out): `grok-4.7` **2.00 / 6.00**;
  Groq `openai/gpt-oss-120b` **0.15 / 0.60**; Groq
  `openai/gpt-oss-20b` **0.075 / 0.30**; Groq `qwen/qwen3.8-27b`
  **0.80 / 4.00**; OpenRouter `google/gemini-3.5-flash-lite`
  **0.30 / 2.50** (added to `_OR_PRICES`).
- Groq calls are recorded at list price for visibility, flagged
  `provider_tier=free`, and never enter the real-money ledger. NIM and
  Gemini-free calls remain unpriced, as in v19.
- /health gains three blocks: `xai` (config, accounting mode,
  `credit_today_usd`), `groq` (config, requests today vs soft cap,
  guard state, list USD today), `gemini_flash_mid` (ration use).

## Acceptance discipline (the operator, 2026-10-14) — the Stage 3 yardstick

**Waiting is fine if an ask returns in UNDER 5 MINUTES and accurate.
Accuracy is not the currency: no gate, panel rule, or citation rule
was weakened to buy speed.** Built to that:

- Per-class unit budgets: **simple 150s / hard 285s** (hard was 420s
  in v19), inside the unchanged 600s `ASK_TIME_CAP_S` safety net.
  `_run_unit` publishes the unit deadline on the ask context.
- Per-call timeouts sized to the wall: judge legs 30s
  (`THIRD_JUDGE_TIMEOUT`, all transports); NIM drafting legs ~60s
  (new `NIM_DRAFTER_TIMEOUT`, bench loop + divergence draft); Groq
  drafting 60s; xAI / OpenRouter drafting 120s.
- `_leg_timeout(base)` clamps every chain leg (drafting layers and
  judge seats) to the unit's remaining budget; at the budget edge a
  leg does not start (the seat/layer sheds) instead of blowing the
  wall. Outside a unit, base timeouts stand (v19 behavior).

## Model-ID verification (the catalog gate)

Gate rule (the operator, 2026-10-14): every seat's model id must be verified
against its provider's LIVE catalog before it ships as a default;
anything unverifiable stays a clearly-marked non-default constant.
Status of every v20 default:

| Model | Provider | Verified |
|---|---|---|
| `grok-4.7`, `grok-4.20-0309-non-reasoning` | xAI | LIVE catalog pull, 2026-10-14 (parent, via the operator's key). No `grok-4.1-fast` exists on the direct API. |
| `openai/gpt-oss-120b`, `openai/gpt-oss-20b` | Groq | Official models page (production) AND live account catalog pull, 2026-10-14 (parent, the operator's key; account sees exactly 11 models). |
| `qwen/qwen3.8-27b` | Groq | Same two sources (PREVIEW model on the official page; present in the live account catalog). |
| `deepseek-ai/deepseek-v4.1-flash`, NIM bench ids | NIM | Live NIM catalog pull, 2026-10-14 (parent, the operator's key). Catalog has NO DeepSeek V3.1, NO Qwen, NO Llama-4. |
| `google/gemini-3.5-flash-lite` | OpenRouter | Live OpenRouter catalog pull, 2026-10-14 (462 models; $0.30/$2.50). |
| v19 carryovers (V3.1, Qwen3-235B, Gemini ids) | OR / Google | Verified in their own versions' records; unchanged strings. |

There is **no Llama chat model on the operator's Groq catalog** (only
llama-prompt-guard classifiers) and no Llama seat was added on Groq.

## Verification performed (offline only)

- `python3 -m py_compile fssa_search_server.py` — clean.
- Stubbed import smoke (copy in /tmp, `corpus.json = []`, no key files
  present): layer values sort 0 / 2 / 2.5 / 3 / 3; seat B and C
  candidate lists exactly as specced; with every provider keyless the
  raw seat picks fall to the Flash rung while the atomic panel plan
  assigns it to seat B only and leaves seat C unfilled (diversity
  guard intact, as-served composition); Groq guard counter persists
  and increments; Flash ration counts; `_leg_timeout` returns the base
  outside a unit, clamps to the remaining budget inside one, and
  returns 0.0 (shed) once spent; all three new /health blocks build;
  cost math spot-checked (1M in + 1M out on Groq GPT-OSS-120B =
  $0.75 at list).
- Diff vs v19: ~1,080 changed lines, all in the areas above; the
  pipeline core (retrieval, gates, composition) is untouched.

## Stage-2 deploy checklist (NOT done; needs the operator's go)

1. **Pre-deploy catalog re-check (free models reads only):** pull the
   xAI models list, the Groq models list (the operator's account key), and
   the NIM catalog; diff every id in the chains above against them.
   Any id missing → stop; swap the named constant or revert that seat
   by config before deploying. Groq's `qwen/qwen3.8-27b` is a preview
   model and the most likely drift candidate.
2. Place key files in the PC server dir (gitignored, never in git):
   `xai_key.txt`, `groq_key.txt`. Missing either = that provider
   simply stays dark; the server must still boot and answer.
3. Back up the live v19 server file + corpora/embeddings (the v19
   deploy pattern), then swap in the v20 file. Corpora and embeddings
   are unchanged by v20.
4. Restart via the usual manual route; check /health: `xai.configured`
   and `groq.configured` true, `groq.guard_tripped` false,
   `gemini_flash_mid.used_today` 0, openrouter block sane.
5. Expect three NEW state files in the server dir: `groq_usage.json`,
   `flash_mid_usage.json` (and `xai_spend.json` only if the permanent-
   False flag were ever flipped — it should NOT appear).
6. Stage 3 (separate go): fresh ledger day at start; run the anchor
   battery; grade the free-first panel against the v19 panel (V3.1
   primary) before trusting it; yardstick = under 300s per unit AND
   accuracy (zero IDKs on the 12 anchors is the headline, as before).

## UNVERIFIED / open items

- **Preview-model continuity:** Groq's `qwen/qwen3.8-27b` is a preview
  model; Groq may rename or drop it without notice. Mitigated by seat
  C's fallover depth + the Stage-2 catalog re-check, not eliminated.
- **Ungraded new primaries:** V4.1-Flash (judge), Qwen3.8-27B (judge),
  and the Groq GPT-OSS drafting primaries are ungraded in these roles
  until the Stage 3 battery runs. That is what Stage 3 is for.
- **Computed costs:** xAI/Groq per-call dollar figures are computed
  from reported token usage at list prices (their APIs return tokens,
  not USD). Groq free-tier calls bill $0 in reality; the figures are
  visibility accounting. xAI's actual billed amounts may differ in
  detail (cached-token pricing etc. not modeled).
- **Seat A Flash question:** whether the rationed Flash rung should
  also serve as seat A's extraction fallover (ahead of the OpenRouter
  Lite attempt) is undecided; v20 leaves seat A as previously specced.
  **Resolved 2026-10-14: yes; see fix 2 in the v20.1 addendum below.**
- **Groq free-plan limits** beyond the models page (rate limits per
  model on the operator's plan) were not probed; the 900/day guard sits under
  the published 1,000/day GPT-OSS figure from the route discussion.

## Addendum — v20.1 fixes (2026-10-14, the operator's approval)

Three fixes approved by the operator after the Stage 1 gate report and
applied the same day. Constraints held: VM only, no live API calls,
no PC, no money, no deploy. Verification = `py_compile` (clean) +
offline code review + a stubbed smoke in /tmp (results at the end).
The live server still runs v19; Stages 2 and 3 still need the operator's
explicit go.

### Fix 1 — Seat C's v19 tail restored

`judge_nim2` candidates are now, in configured order:

1. `(GROQ_QWEN_MODEL, 5)` — Groq `qwen/qwen3.8-27b` (primary)
2. `(FLASH_MODEL, 6)` — the rationed Gemini 3.8 Flash rung
3. `("nvidia/nemotron-3-ultra-550b-a55b", 3)` — NIM Nemotron Ultra
4. `(JUDGE_SEAT2_MODEL, 0)` — OpenRouter `qwen/qwen3-235b-a22b-2507`
5. `(SEAT2_FAILOVER_MODEL, 0)` — OpenRouter `meta-llama/llama-4-maverick`

then the pipeline's local fallback, as before. Restored for depth
and for the Meta-family tail voice. Two record-keeping notes:

- The Ultra entry is v19's OWN seat-C entry, verified in the v19
  file: `nvidia/nemotron-3-ultra-550b-a55b` on key slot 3 (the same
  model also serves v20's hard_draft bench on slot 2; both ids are
  in the 2026-10-14 live NIM catalog pull). The fix request named
  `nvidia/llama-3.1-nemotron-ultra-253b-v1`; that id also exists in
  the live catalog but appears nowhere in v19 or v20 and was never
  this seat's entry, so the faithful restore uses the 550B id. If
  the 253B v1 model is ever wanted here instead, it is a one-line
  swap of the same tuple's model string.
- Walking order nuance (standing machinery, unchanged): on
  unplanned picks `_seat_pick` walks layer-major, so the NIM Ultra
  leg (Layer 2) is walked ahead of the Flash rung (ordering value
  2.5) even though the configured list names Flash second — the
  same layer rule that governs every seat, and the reason the rung
  sits behind both NIM legs in seat B's effective order too.

### Fix 2 — Seat A gains the rationed Flash rung

Seat A's extraction fallover in `_judge_round` is now: Gemini Lite
free path → **Gemini 3.8 Flash mid-ladder rung (slot 6 mechanics)**
→ Lite via OpenRouter (`orlite`) → the seat-B chain as extractor
(`nim`) → local. Implementation, all additive:

- A new `"flashmid"` engine in `_primary_judgment`: gates on
  `_flash_mid_available()`, counts the attempt with
  `_flash_mid_note()`, and calls `_gemini_text(FLASH_MODEL, …)` —
  the exact ration mechanics of the seats' slot-6 dispatch (shared
  `flash_mid_usage.json` pool, `FLASH_MID_DAILY_CAP` 10/day across
  ALL mid-ladder duty, seats B/C and seat A together). A spent
  ration skips the rung silently in the caller, as `_slot_has_key(6)`
  skips a rationed seat candidate.
- As-served recording: when the rung serves, the extraction
  verdicts keep the round's `"lite"` voice slot (the round's
  plumbing, mirroring how a failover candidate's verdicts land in
  a seat's field), and the serving model is recorded in
  `served_models["lite"]`. `_panel_state` now consults that record
  for the lite seat, so a Flash-served seat A counts as DEGRADED,
  never as the Lite primary; per-claim `models["lite"]` attribution
  is likewise as-served (the family is google either way, so
  coverage families are unchanged). The same-model OpenRouter
  transport still leaves no served record and keeps the seat's
  primary standing, exactly as the first v20 pass built it.
- No restructuring was needed: the fallover is an engine sequence
  in `_judge_round`, and the rung slots into it directly.

### Fix 3 — Qwen preview-drift protocol (verified; no code change)

`GROQ_QWEN_MODEL` (`qwen/qwen3.8-27b`) is a Groq PREVIEW model and
may be renamed or dropped without notice. The protocol:

1. **Pre-deploy catalog re-check** (already Stage-2 checklist
   item 1): the Groq models list is diffed against every chain id
   before deploy; a missing id stops the deploy.
2. **Single-constant swap:** the id lives only in `GROQ_QWEN_MODEL`;
   a rename is a one-line edit (plus the `_GROQ_PRICES` row key).
3. **Fallthrough resilience — verified in code, no fix needed.**
   A model-not-found surfaces as HTTP 404 from Groq; `_nim_text`
   raises `RuntimeError` on ANY non-200 (404 included; the only
   retry is one retry on 5xx, 429 raises at once), `_groq_text`
   passes it through, and `_judge_seat_text` catches it per
   candidate like every other leg failure: `_seat_report(ok=False)`,
   the model joins the call's tried list, and the walk moves to
   the next candidate. No exception escapes the seat call until
   every pickable candidate has failed, and every caller converts
   that to `available: False` — a degraded round, never a failed
   ask. Two standing rules bound the behavior honestly: (a) with
   an atomic panel plan in force (§C, since v18), a failed planned
   candidate is NOT substituted mid-round — the seat sits out that
   round and the degradation shows in coverage/badges; that rule
   applies to every failure type equally, 404s included. On
   unplanned walks the fallthrough is direct (smoke-demonstrated
   below). (b) Benching is timeout/median-based, so a fast 404
   does not durably bench the dead leg: it is retried once per
   judge call and fails fast (one HTTP round trip) until the id
   is fixed — bounded cost, never silent.
4. **Route-report visibility:** a persistently dead primary shows
   up as seat C never serving its primary in the ask records' seat
   events and in `route_report.py` output, and `_seat_report` calls
   accumulate in the /health seat block — drift is diagnosable
   from records, not discovered by accident.

### Verification performed for this addendum (offline only)

- `python3 -m py_compile fssa_search_server.py` — clean
  (9,714 lines).
- Stubbed smoke (copy in /tmp, `corpus.json = []`, fake Groq key
  in env, transports stubbed; NO live calls):
  - Seat B and seat C candidate lists match the spec exactly
    (seat C per fix 1, including the restored tail).
  - `_judge_seat_text("judge_nim2")` with `_https_post` returning
    404 for api.groq.com fell through the dead Groq leg (and past
    the keyless NIM Ultra leg) to the Flash rung, which served;
    the Flash ration counted the attempt (count 1).
  - `_primary_judgment("flashmid")` extracted claims from the
    stubbed Flash reply and counted the ration (count 2).
  - `_panel_state`: all-primary served map = "full"; the same map
    with `served["lite"] = FLASH_MODEL` = "degraded".
  - A stub `_judge_round` (Lite stubbed down, Flash stubbed
    serving): the rung performed the extraction, the OpenRouter
    Lite fallover was never attempted, and
    `served_models["lite"]` recorded `FLASH_MODEL`.
  - With the ration exhausted, `_primary_judgment("flashmid")`
    returned unavailable ("ration spent") without any Gemini call.

## Stage 2 deploy — DONE (2026-10-14, ~16:55–17:00 EDT)

Deployed under the operator's "Deploy is go" (2026-10-14). Verification was
/health + server-local /search probes only: NO /ask calls, no test
battery, no live model calls, no deploy spend.

- Pre-deploy catalog re-check (parent, all green): Groq ids verified
  fresh against the operator's account catalog (openai/gpt-oss-120b,
  openai/gpt-oss-20b, qwen/qwen3.8-27b); NIM ids against the day's
  live pull; OpenRouter ids against the day's live catalog pull; xAI
  ids verified live earlier the same day.
- Pre-check (server-local, before swap): v19 healthy — docs_in 1291,
  docs_federal 3955, embeddings_loaded 73,014, spend_today $0.748457,
  cap $2.00, cap_tripped false; live MD5 c0b17f305051daf579071116f7975944.
- Backup: live v19 file copied to `fssa_search_server_v19.py.bak` in
  the runtime server directory\ before the swap. Corpora,
  embeddings, and dedupe files untouched (unchanged by v20).
- Key files placed (values never in git; `*_key*.txt` is gitignored):
  groq_key.txt (56 bytes) before the swap; xai_key.txt (84 bytes)
  placed after the operator supplied the value mid-deploy, followed by one
  extra restart via restart_fssa.ps1 so the first full v20 state has
  both providers configured.
- Swap + restart via one-off task DeployV20Once (deleted after the
  run). VM source MD5 6d8f77aa67326b5ca7e074d9e8d67c23; staged-file
  MD5 and post-restart LIVE_MD5 both matched it exactly.
- Post-deploy /health (server-local): ok true; docs_in 1291;
  docs_federal 3955; embeddings_loaded 73,014; hybrid_active true;
  groq.configured TRUE, guard_tripped FALSE, requests_today 0,
  daily_soft_cap 900; gemini_flash_mid.used_today 0 (sub-cap 10,
  final-rung cap 18); xai.configured TRUE (hard grok-4.7, fast
  grok-4.20-0309-non-reasoning, free_credits_mode false, real-money
  accounting); openrouter cap $2.00, cap_tripped false, spend_today
  ~$0.75 (the pre-existing day ledger; the deploy made no model
  calls — small read-to-read movement is the ledger's own usage
  reconciliation).
- Seat state on first boot matches the v20.1 design: hard_draft
  leads moonshotai/kimi-k3 (NIM bench; Groq legs lead once a unit
  plans), judge_nim1 leads deepseek-ai/deepseek-v4.1-flash with
  Nemotron Super, the rationed gemini-3.8-flash rung (slot 6), and
  deepseek-chat-v3.1 behind it; judge_nim2 leads qwen/qwen3.8-27b
  (slot 5) with the Flash rung, NIM Ultra (slot 3), Qwen3-235B, and
  Llama-4-Maverick behind it (restored tail present).
- Retrieval probes (server-local /search, no models): "qualified
  income trust" (IN) → IHCPPM 2615.75.15 (QIT/Miller) #1;
  "transfer of property penalty divisor 8027" (IN) → IHCPPM
  3006.00.00 #1; "substantial gainful activity 2027 trial work
  period" (federal) → SSA Trial Work Period pages #1-2. Same anchors
  as the v19 deploy probes.
- GitHub push via PC clone (one-off task PushV20Once, deleted after):
  commit 226d7145f1b1bacdeccefc7a6a749cf37057918b "v20: free-first
  layer route (Groq/Gemini/NIM before prepaid), seat tails restored,
  285s hard budget", 3c437d0..226d714 main -> main, fssa_search_server.py
  only (1,068 insertions, 85 deletions). Head verification via the
  GitHub connector is the parent's step.
- One-off tasks DeployV20Once and PushV20Once both DELETED (verified
  by schtasks success messages).
- Transfer note: the pc-scp.sh helper's SFTP path failed through the
  tunnel proxy ("proxy: no response"); transfers were done with
  scp -O (legacy protocol) using the identical key, proxy command,
  and known_hosts pinning.
