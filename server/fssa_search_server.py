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
provider, so moving the seat is a one-line change. The seat is
DORMANT without the active provider's key (its key file in the
server dir, or its env var): the seat then simply never runs and
judging is exactly v14.1 dual judging. It ships dormant; the key
arrives with James's provider signup. The panel (which judges
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
NIM as a whole is down. v15.1 narrows it further (James's
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
local 14B's dispute tiebreak is GONE (James's ruling: disputed
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

v17 (2026-10-13): the paid swap (James's go in chat): the
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
guardrails (James's hard requirement): every OpenRouter
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
$2.00/day at the single OpenRouter choke point: every call
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
to v19/v20 (James's direction, 2026-10-14).

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
2027 SSI/SGA/TWP amounts. §G speed build (James's direction,
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

v20 (2026-10-14): the free-first route rewire (James's
directive; see v20/STATUS.md). FREE + FAST providers become
the primaries; every PREPAID provider sits behind them as a
fallover. The final form is James's LAYER MODEL (later on
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
proven Qwen3-235B on OpenRouter. Acceptance (James,
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
and the $0.50 per-ask ceiling) exactly like OpenRouter,
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
"""
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

def _embed_query(text):
    """Get embedding vector for a query via Ollama."""
    try:
        payload = json.dumps({"model": "nomic-embed-text", "prompt": text[:2000]}).encode()
        req = urllib.request.Request(OLLAMA + "/api/embeddings", data=payload,
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return data["embedding"]
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
# v13: contested-figure and bundled questions asked of the local default
# route to the 14B (strongest local reasoner in the v12 bake-off). The 9B
# is unloaded before a routed call so the two never share the 8GB VRAM.
ROUTED_MODEL = "qwen3:14b"
# Feature toggles (can be overridden via /ask body: {"rewrite": false, "rerank": false})
REWRITE_ENABLED = True
RERANK_ENABLED = True
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

# v14.2: the third judge's API key is loaded lazily by
# _third_judge_key() from the provider config at the top of the
# judging section (env var first, then the provider's gitignored
# key file in the server dir, the same pattern as the Gemini key
# above). Absent key = the seat stays dormant, exactly v14.1.

# --- v15: pipeline config -------------------------------------------------
# Per-ask wall-clock ceiling (James's decision, 2026-10-13): when an
# ask hits the cap mid-ladder it stops climbing and ships the best
# state reached (a Partial built ONLY from claims already verified
# by the panel, else an IDK with its gap log entry); timing records
# cap_hit. The cap can never make an unverified answer ship.
ASK_TIME_CAP_S = 600
# v18 (§B remainder): the per-ask OpenRouter cost ceiling. Past
# this line the ladder stops spending on redrafts and ships the
# best capped Partial from the claims already judged (or IDKs);
# it works alongside the time cap and the $2.00/day hard stop.
# Tunable via env; James may retune after the first battery.
PER_ASK_CEILING_USD = float(
    os.environ.get("FSSA_PER_ASK_CEILING_USD", "0.50"))
# The demoted local 14B as LAST-RESORT judge. v15.1 (James's
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


_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBS_MAX = 50


def _job_create():
    job_id = uuid.uuid4().hex[:16]
    rec = {"id": job_id, "status": "running", "stage": "retrieving",
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
    """v15.1: one bare chat call for the retrieval utilities
    (query rewrite, policy-phrase rewrite, rerank). Through v15.0
    these ran on the local 9B, which loaded it into VRAM on every
    ask; they now run on the NIM fast tier, so the local models
    stay cold. Raises on any failure: the utilities degrade to
    their empty result, exactly as they did when the local call
    failed. v16: the call rides the simple_draft SEAT's active
    candidate and key slot (seat machinery defined later in this
    file; resolves at call time). Utility calls deliberately do
    not report to seat health: their short outputs would
    pollute the drafting seat's latency median. v17: the
    OpenRouter utility seat (GPT-OSS-20B) gets the call first —
    the v16 split showed these calls were 80-95% of retrieval
    time; any OpenRouter failure falls through to the NIM seat
    path below, unchanged."""
    # v20 (layer model): Groq's free tier (GPT-OSS-20B)
    # first, then the free NIM seat path, then the paid
    # OpenRouter seat — free before paid, as everywhere.
    if _groq_available():
        try:
            return _groq_text(
                GROQ_UTILITY_MODEL,
                "You are a precise retrieval utility. Reply with "
                "only the requested output, no explanations.",
                prompt, temperature=0, max_tokens=max_tokens,
                timeout=timeout)
        except Exception:
            pass  # the NIM seat path below takes over
    try:
        model, slot, _fo = _seat_pick("simple_draft")
        if model is None:
            model, key_env, key_file = (DRAFTER_SIMPLE_MODEL,
                                        DRAFTER_KEY_ENV,
                                        DRAFTER_KEY_FILE)
        else:
            key_env, key_file = SEAT_SLOTS[slot]
        return _nim_text(
            DRAFTER_BASE_URL, key_env, key_file, model,
            "You are a precise retrieval utility. Reply with only "
            "the requested output, no explanations.",
            prompt, temperature=0, max_tokens=max_tokens,
            timeout=timeout, provider=DRAFTER_PROVIDER)
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


def rewrite_policy_phrase(question):
    """Rewrite a life-situation question as a policy-manual topic phrase.

    Users ask 'does buying a van count'; the manuals are organized as
    'exempt vehicles', 'disaster assistance payments', 'non-recurring
    lump sum payments'. Searching the manual's own phrasing surfaces the
    manual sections that the raw question misses (situational test,
    2026-10-12: Q1/Q2/Q4 retrieval failures)."""
    try:
        prompt = ("Rewrite this question as a short formal policy-manual topic "
                  "phrase, using terms like resources, income, excluded, "
                  "countable, eligibility, transfer of assets, benefits. "
                  "Output ONLY the phrase, no other text.\n\n"
                  f"Question: {question}\nPhrase:")
        text = _utility_text(prompt, max_tokens=128,
                             timeout=30).strip().split("\n")[0]
        text = text.strip().strip('"').strip()
        return text if 3 < len(text) < 120 else ""
    except Exception:
        return ""


# Deterministic topic bridges (2026-10-12 v5, extended v6): fixed
# lay-term -> manual phrasing searches for the chronic retrieval gaps.
# The LLM policy rewrite aims at these same phrases but lands
# inconsistently; these always fire.
# (token triggers, substring triggers, phrases to search as
# (phrase, pin_term) pairs - the guaranteed pin must be a chunk whose
# text contains pin_term when one is set (v11 fix round: a pin is only
# worth guaranteeing if it literally states the operative rule),
# program scope for the pin (None = any), extra requirement: at least
# one of these tokens/substrings must ALSO be present)
_TOPIC_BRIDGES = (
    ({"fema", "disaster", "disasters", "burned", "flood", "flooding",
      "tornado", "hurricane", "earthquake"}, (),
     (("disaster assistance payments excluded", None),), None, ()),
    ({"backpay"}, ("back pay", "lump sum"),
     (("non-recurring lump sum payments excluded resources", None),
      ("retroactive SSI Social Security benefits nine months",
       "retroactive SSI or Social Security benefits"),
      ("nonrecurring lump-sum payment excluded income",
       "nonrecurring lump-sum payment")),
     None, ()),
    ({"vehicle", "van", "car", "automobile", "truck"}, (),
     (("exempt vehicles countable resources", None),), None, ()),
    # v18 (F15): "caregiver"/"caregiving" no longer trigger the
    # spouse bridge on their own (any caregiving question pulled
    # spouse material in); the structured-family-caregiving
    # phrase trigger below still fires it for SFC questions.
    ({"wife", "husband", "spouse", "spouses"},
     ("structured family caregiving",),
     (("spousal impoverishment income in the name of the spouse", None),
      ("community spouse income not counted eligibility", None)),
     None, ()),
    ({"reapplied"}, ("retroactive",),
     (("retroactive coverage eligible third month before application",
       None),),
     "Medicaid", ()),
    # v11: caregiver pay in a MAGI/HIP context is governed by the IRS
    # difficulty-of-care exclusion, whose vocabulary ("difficulty of
    # care", "excludable") shares almost no words with the question.
    ({"caregiver", "caregiving"}, ("caregiver pay",),
     (("difficulty of care payments excluded income",
       "difficulty of care"),
      ("medicaid waiver payments excludable caregiver",
       "faq")),
     None, ({"hip"}, ("household income", "magi"))),
    # v11: "who pays my Part B premium" -> Medicare Savings Programs.
    (set(), ("part b premium", "medicare premium"),
     (("medicare savings programs pay part b premiums",
       "medicare savings"),
      ("qualified medicare beneficiary income limits",
       "qualified medicare beneficiary")),
     "Medicare", ()),
    # v11: ABLE accounts, the legal way to save past the resource limit.
    ({"able"}, ("able account", "stable account"),
     (("able account resources excluded medicaid ssi", "able"),),
     None, ()),
    # v14: over-the-income-cap questions are governed by the Qualified
    # Income Trust (Miller trust). The vocabulary ("qualified income
    # trust") shares almost no words with how the question is asked, so
    # without this pin the QIT material never surfaces and the model
    # improvises around the hole (manual test #1, 2026-10-13).
    (set(), ("over the income limit", "over the limit", "income cap",
             "over the medicaid income limit", "over the medicaid limit",
             "is over the income limit", "miller trust",
             "qualified income trust"),
     (("qualified income trust medicaid eligibility",
       "qualified income trust"),
      ("miller trust income limit medicaid", "miller trust")),
     None, ({"medicaid", "waiver"}, ("nursing home", "long-term care",
                                     "long term care"))),
)

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


def _title_family(title):
    """Normalize a title to its document family: drop state names, dates,
    parentheticals, and site suffixes so near-identical documents (the
    Section 1135 state variants, disaster-relief duplicates) group
    together for diversity capping."""
    t = title.lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\|.*$", " ", t)
    for s in US_STATES:
        t = re.sub(r"\b" + re.escape(s) + r"\b", " ", t)
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _is_table_chunk(text):
    """Detect table-like content: multiple lines with consistent delimiters."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 3:
        return False
    # check for pipe or tab delimiters, or multiple dollar amounts in aligned rows
    pipe_lines = sum(1 for l in lines if l.count("|") >= 2)
    if pipe_lines >= 3:
        return True
    dollar_lines = sum(1 for l in lines if re.search(r"\$[\d,]+", l))
    if dollar_lines >= 4 and len(lines) <= 20:
        return True
    return False


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


def _question_programs(q):
    """Which programs a question is about, from its own wording."""
    ql = q.lower()
    toks = set(re.findall(r"[a-z]+", ql))
    progs = set()
    if "medicaid" in toks or "nursing home" in ql or \
            "community spouse" in ql or "estate recovery" in ql or \
            "lookback" in ql or "look-back" in ql or "waiver" in toks:
        progs.add("Medicaid")
    if toks & {"ssi", "ssdi"} or "supplemental security" in ql or \
            "social security" in ql:
        progs.add("SSI/SSDI")
    if "snap" in toks or "food stamps" in ql or "food assistance" in ql:
        progs.add("SNAP")
    if "medicare" in toks or "part b" in ql or "part d" in ql:
        progs.add("Medicare")
    if "hip" in toks or "healthy indiana" in ql or "magi" in ql or \
            "household income" in ql:
        progs.add("MAGI")
    return progs


def _hit_year(h):
    """Newest plausible year (2020-2027) in a hit's title/URL/excerpt."""
    text = ((h.get("title") or "") + " " + (h.get("url") or "") + " "
            + (h.get("excerpt") or "")[:400])
    years = [int(y) for y in re.findall(r"\b(20[2-9]\d)\b", text)
             if 2020 <= int(y) <= 2027]
    return max(years) if years else None


def _hit_tags(h):
    """(program, doc_type, year) for a hit (v13). Prefers the ingest-time
    tags tag_docs.py stamps on corpus docs (h['tags']); falls back to
    the search-time classifiers, which are the same functions the
    tagger imports, so the two can never drift."""
    tags = h.get("tags") or {}
    prog = tags.get("program") or h.get("program")
    dtype = tags.get("doc_type") or h.get("dtype")
    if not prog or not dtype:
        c_prog, c_dtype = _classify_hit(h)
        prog = prog or c_prog
        dtype = dtype or c_dtype
    year = tags.get("year")
    if year is None:
        year = _hit_year(h)
    return prog, dtype, year


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


def _arithmetic_errors(answer):
    """v18 (F6): the pure check behind _check_arithmetic: the
    correction notes for the answer's explicit dollar equations,
    WITHOUT appending anything. _run_unit gates shipping on
    this (through v17.3 the only call ran after the ship
    decision and merely appended a note to a shipped answer)."""
    notes, seen = [], set()

    def consider(a, op, b, c, raw):
        want = _eval_money(a, op, b)
        if want is None or abs(want - c) <= 0.51 or raw in seen:
            return
        seen.add(raw)
        notes.append(f"- {_fmt_money(a)} {op} {_fmt_money(b)} = "
                     f"{_fmt_money(want)} (the answer above says "
                     f"{_fmt_money(c)})")

    for m in _EQ_RE.finditer(answer or ""):
        try:
            consider(float(m.group(1).replace(",", "")), m.group(2),
                     float(m.group(3).replace(",", "")),
                     float(m.group(4).replace(",", "")), m.group(0))
        except ValueError:
            continue
    for m in _PAREN_EQ_RE.finditer(answer or ""):
        try:
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

def hybrid_search(query, corpus=None, n=6, use_rewrite=True, use_rerank=True,
                  boosts=True):
    """Combine keyword and vector search with proper reciprocal rank fusion."""
    # Check cache
    cache_key = (query, id(corpus) if corpus else "in", n, use_rewrite,
                 use_rerank, boosts)
    # v18: callers receive COPIES (list and hit dicts). The
    # cached objects are shared process-wide; through v17.3 a
    # caller extending the returned list (the bundled-parts
    # path) or writing onto a hit dict (the web badge) polluted
    # the cache entry for every later ask.
    cached = _cache_get(_SEARCH_CACHE, cache_key)
    if cached:
        return [dict(h) for h in cached]

    # v16 item 8: the vector leg is independent of the rewrite,
    # so it runs CONCURRENTLY with the rewrite + keyword legs
    # (they were serial in v15; the sub-timings indicted the
    # serialization, not any single leg).
    _vpool = ThreadPoolExecutor(max_workers=1)
    _vec_fut = _submit(_vpool, vector_search, query, n=n * 3,
                       corpus=corpus)
    # rewrite query for better keyword matching (optional)
    _t_seg = time.monotonic()
    rewritten = rewrite_query(query) if (use_rewrite and REWRITE_ENABLED) else []
    _retr_note("rewrite", time.monotonic() - _t_seg)
    # NOTE: rewritten terms are searched separately, NOT concatenated into n-grams
    # to avoid synthetic cross-boundary phrase corruption.

    _t_seg = time.monotonic()
    kw_hits = search(query, corpus, n=n*3, extra_terms=rewritten)
    _retr_note("lexical", time.monotonic() - _t_seg)
    # policy-phrasing pass: search the question rewritten in the manuals'
    # own vocabulary, fused as its own list (situational retrieval fix)
    _t_seg = time.monotonic()
    policy_q = rewrite_policy_phrase(query) if (use_rewrite and REWRITE_ENABLED) else ""
    _retr_note("rewrite", time.monotonic() - _t_seg)
    _t_seg = time.monotonic()
    kw2_hits = search(policy_q, corpus, n=n*2) if policy_q else []
    _retr_note("lexical", time.monotonic() - _t_seg)
    vec_hits = _vec_fut.result()
    _vpool.shutdown(wait=False)
    _t_fusion = time.monotonic()

    # deterministic topic bridges: fixed phrases for the chronic gaps.
    # /ask-mode only (v7 gate): plain /search keeps the unenhanced
    # hybrid ranking, same as the figure/duration boosts.
    q_low = query.lower()
    q_tokens = set(re.findall(r"[a-z]+", q_low))
    bridge_hits = []
    bridge_tops = []  # (bridge index, hit): pins are allocated per
    # bridge below, so one bridge's phrases cannot eat every slot
    if boosts:
        for bidx, (token_triggers, text_triggers, phrases, prog_scope,
                   req) in enumerate(_TOPIC_BRIDGES):
            if not ((q_tokens & token_triggers) or
                    any(t in q_low for t in text_triggers)):
                continue
            if req:
                req_tokens, req_texts = req
                if not ((q_tokens & req_tokens) or
                        any(t in q_low for t in req_texts)):
                    continue
            for phrase, pin_term in phrases:
                found = search(phrase, corpus, n=n * 4)
                if not found:
                    continue
                bridge_hits += found[:n * 2]
                # Pin choice, most specific first: a chunk that
                # literally contains the pin term (the operative rule
                # text); then a chunk from the bridge's program scope;
                # then the top hit. If no keyword candidate carries
                # the pin term, try the vector index for one (the IRS
                # difficulty-of-care passage ranks poorly by keywords
                # but well by meaning).
                top = None
                if pin_term:
                    pt = pin_term.lower()
                    top = next(
                        (f for f in found
                         if pt in (f["title"] + " " + f["excerpt"]).lower()),
                        None)
                    if top is None:
                        for f in vector_search(phrase, n=6, corpus=corpus):
                            if pt in (f["title"] + " "
                                      + f["excerpt"]).lower():
                                top = f
                                bridge_hits.append(f)
                                break
                if top is None and prog_scope:
                    top = next(
                        (f for f in found
                         if _classify_hit(f)[0] == prog_scope), None)
                if top is None:
                    top = found[0]
                bridge_tops.append((bidx, top))

    # proper reciprocal rank fusion (no artificial bonuses)
    RRF_K = 60
    scores = {}
    kw_keys, vec_keys = set(), set()
    for rank, h in enumerate(kw_hits):
        key = (h["url"], h["excerpt"][:60])
        kw_keys.add(key)
        scores[key] = scores.get(key, 0) + 1.0 / (rank + RRF_K)
    for rank, h in enumerate(vec_hits):
        key = (h["url"], h["excerpt"][:60])
        vec_keys.add(key)
        scores[key] = scores.get(key, 0) + 1.0 / (rank + RRF_K)
    solo_candidates = set()
    for rank, h in enumerate(kw2_hits):
        key = (h["url"], h["excerpt"][:60])
        if rank < 4:
            solo_candidates.add(key)
        scores[key] = scores.get(key, 0) + 1.0 / (rank + RRF_K)
    for rank, h in enumerate(bridge_hits):
        key = (h["url"], h["excerpt"][:60])
        if rank < 4:
            solo_candidates.add(key)
        scores[key] = scores.get(key, 0) + 1.0 / (rank + RRF_K)
    # solo-source bonus (v6): a top chunk found ONLY by the policy or
    # bridge searches (the lists built to find exactly it) is otherwise
    # buried by single-list RRF below the rerank window (the FEMA miss:
    # /search ranks the section #1, /ask never saw it). The bonus lifts
    # it to parity with two-list chunks; the rerank still judges it.
    if boosts:
        for key in solo_candidates:
            if key not in kw_keys and key not in vec_keys:
                scores[key] = scores.get(key, 0) + 0.03

    # merge hits
    merged = {}
    for h in kw_hits + vec_hits + kw2_hits + bridge_hits:
        key = (h["url"], h["excerpt"][:60])
        if key not in merged:
            merged[key] = h
        merged[key]["_fused"] = scores[key]

    hits = list(merged.values())

    # table boost for money questions
    money_q = any(w in query.lower() for w in
                  ("limit", "cost", "amount", "much", "income", "pay", "rate",
                   "dollar", "price", "fee", "premium", "allowance", "keep"))
    for h in hits:
        if money_q and _is_table_chunk(h["excerpt"]):
            h["_fused"] = h.get("_fused", 0) + 1.0

    # figure/duration boosts (2026-10-12 excerpt precision): on money
    # questions, ANY chunk quoting a dollar figure gets a moderate lift,
    # not just table-shaped ones (the 2026 spousal standards live in
    # prose). On how-long questions, chunks stating a duration in months
    # or years get the same lift (the SSI nine-month exclusion is one
    # sentence inside a long resources page). The lift is sized to
    # reorder chunks within the relevant pool, not to override topical
    # relevance, and it applies before the LLM rerank.
    duration_q = any(w in query.lower() for w in
                     ("how long", "months", "years", "period", "deadline",
                      "when does", "expire", "lookback", "look-back"))
    if boosts:  # /search passes boosts=False (v5 guard: plain search
        # ranking should not be skewed by excerpt-oriented boosts)
        # v11 program scoping: when the question is about Medicaid,
        # SNAP, or MAGI coverage and never mentions SSI/SSDI, SSI-law
        # chunks (POMS above all) are demoted: the two programs share
        # vocabulary but not figures or timeframes, and in v10 POMS
        # chunks crowded out the Medicaid sources on three questions.
        q_progs = _question_programs(query)
        scope_away_from_ssi = bool(q_progs & {"Medicaid", "SNAP", "MAGI"}) \
            and "SSI/SSDI" not in q_progs
        # v13 source ranking v2 inputs: is the question Indiana-scoped?
        indiana_scoped = "indiana" in q_low or "hoosier" in q_low
        for h in hits:
            lift = 0.0
            if money_q and _has_figure(h["excerpt"]):
                lift += 0.05
            if duration_q and _has_duration(h["excerpt"]):
                lift += 0.05
            if scope_away_from_ssi and \
                    _classify_hit(h)[0] == "SSI/SSDI":
                lift -= 0.05
            # v11 recency: dated material (bulletins, current-year
            # standards) gets a small lift scaled by its year; undated
            # chunks are neutral. Sized under the figure boosts so it
            # breaks ties rather than overriding relevance.
            yr = _hit_year(h)
            if yr and yr >= 2024:
                lift += min(0.012, 0.003 * (yr - 2023))
            # v13 source ranking v2: prefer the source class that owns
            # the rule. On Indiana-scoped questions, Indiana-state
            # sources beat federal generic pages (P1 failed on the
            # federal $3,000 figure while Indiana $5,000 chunks sat in
            # the pool). Manuals, POMS, regulations and waiver
            # applications beat program-administration pages, and
            # payee/provider-admin pages are demoted (their narrower
            # figures captured Q5 across four graded runs). Modest,
            # same scale as the v11 lifts.
            _p13, dtype13, _y13 = _hit_tags(h)
            if indiana_scoped and _is_indiana_source(h):
                lift += 0.03
            if dtype13 in ("POMS operations manual", "policy manual",
                           "regulation", "waiver application"):
                lift += 0.02
            u13 = (h.get("url") or "").lower()
            t13 = (h.get("title") or "").lower()
            if "/payee/" in u13 or "provider" in t13:
                lift -= 0.03
            if lift:
                h["_fused"] = h.get("_fused", 0) + lift
                # Debug: the total v11/v13 boost-loop lift rides out on
                # the hit as rank_lift (set in the strip step below).
                h["_rank_lift"] = h.get("_rank_lift", 0.0) + lift

    # explainer weight (v5): on SSI/SSDI rule questions, SSA's own
    # explainer pages (the Understanding SSI series, SSI spotlights)
    # state the general rule; program-administration pages (payee,
    # provider operations) state narrower variants that mislead when
    # blended (the backpay six-vs-nine-months blend in retest v4).
    ssi_rule_q = bool(q_tokens & {"ssi", "ssdi", "backpay", "retroactive",
                                  "supplemental", "lump"}) \
        or "lump sum" in q_low or "back pay" in q_low
    if ssi_rule_q:
        for h in hits:
            u = h["url"].lower()
            t = h["title"].lower()
            if ("ssa.gov/ssi/" in u or "understanding supplemental" in t
                    or "ssi spotlight" in t or t.startswith("ssi ")):
                h["_fused"] = h.get("_fused", 0) + 0.04

    # --- ranking tuning (2026-10-12) -------------------------------------
    # Title match: documents titled with the query's own terms are usually
    # the canonical source (e.g. "DFR Policy Manual Ch 2600: Resources" for
    # a resources question). Small bonus on the RRF scale (~0.017-0.033).
    q_terms = keywords(query)
    q_phrases = [" ".join(q_terms[i:i + 2]) for i in range(len(q_terms) - 1)]
    query_has_state = any(s in query.lower() for s in US_STATES)
    for h in hits:
        t = h["title"].lower()
        bonus = 0.004 * sum(1 for term in set(q_terms) if term in t)
        if any(p in t for p in q_phrases):
            bonus += 0.010
        # Generic question (no state named): demote state-specific titles
        # so national/general explainers are not buried under dozens of
        # near-identical state documents (the Section 1135 problem).
        if not query_has_state and any(s in t for s in US_STATES):
            bonus -= 0.006
        h["_fused"] = h.get("_fused", 0) + bonus

    hits.sort(key=lambda h: -h.get("_fused", 0))
    # Diversity: at most 2 hits per title family AND at most 2 chunks
    # from the same document URL, so one document family (1135 state
    # variants, disaster-relief duplicates) or one document's chunks
    # cannot crowd out every other source in the pool or the final list.
    diverse, fam_counts, url_counts = [], {}, {}
    for h in hits:
        fam = _title_family(h["title"])
        if fam_counts.get(fam, 0) >= 2 or url_counts.get(h["url"], 0) >= 2:
            continue
        fam_counts[fam] = fam_counts.get(fam, 0) + 1
        url_counts[h["url"]] = url_counts.get(h["url"], 0) + 1
        diverse.append(h)
    hits = diverse
    # strip internal keys
    for h in hits:
        h.pop("_fused", None)
        h.pop("_vscore", None)
        _rl = h.pop("_rank_lift", 0.0)
        if _rl:
            h["rank_lift"] = round(_rl, 3)
    _retr_note("fusion", time.monotonic() - _t_fusion)
    # LLM rerank: the candidate window scales with the requested result
    # count (top 12 for a 6-result search, top 24 for a 12-excerpt
    # narrative ask), then rerank down to n.
    candidates = hits[:max(12, n * 2)]
    result = None
    if use_rerank and RERANK_ENABLED and len(candidates) > n:
        _t_seg = time.monotonic()
        ranked = rerank_with_llm(query, candidates)
        _retr_note("rerank", time.monotonic() - _t_seg)
        if ranked:
            result = ranked[:n]
    if result is None:
        result = hits[:n]

    # guaranteed bridge slots (v7; pins moved to the FRONT in v8): when
    # a topic bridge fired, its top chunk is the operative rule
    # statement the bridge exists to find. Pin up to 2 of them at the
    # head of the excerpt list (the answering model weighs early
    # excerpts most), moving them up if they are already present,
    # respecting the 2-per-URL cap.
    if boosts and bridge_tops and result:
        # v11 fix round: allocate pins per bridge first (one each, up
        # to 3), then fill any remaining slots with second pins. The
        # old global cap of 2 let the spouse bridge's two phrases eat
        # every slot and starved the difficulty-of-care pin entirely.
        ordered, seen_bridges = [], set()
        for bidx, top in bridge_tops:
            if bidx not in seen_bridges:
                seen_bridges.add(bidx)
                ordered.append(top)
        for bidx, top in bridge_tops:
            if top not in ordered:
                ordered.append(top)
        pins = []
        for top in ordered:
            if len(pins) >= 3:
                break
            key = (top["url"], top["excerpt"][:60])
            existing = next((h for h in result
                             if (h["url"], h["excerpt"][:60]) == key),
                            None)
            if existing is None and \
                    sum(1 for h in result if h["url"] == top["url"]) >= 2:
                continue
            pins.append(existing if existing is not None else
                        {k: v for k, v in top.items()
                         if k not in ("_fused", "_vscore")})
        if pins:
            pin_keys = {(h["url"], h["excerpt"][:60]) for h in pins}
            rest = [h for h in result
                    if (h["url"], h["excerpt"][:60]) not in pin_keys]
            result = (pins + rest)[:n]

    # v11: tag every returned hit with its program and document type;
    # the ask functions label excerpts with these for the model.
    for h in result:
        prog, dtype = _classify_hit(h)
        h["program"] = prog
        h["dtype"] = dtype

    _cache_put(_SEARCH_CACHE, cache_key, result)
    return [dict(h) for h in result]  # v18: copies, see above

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
    whole corpus on EVERY call (and the refutation/hunt/retry
    paths call search repeatedly per ask). Corpus dicts are
    process-lifetime objects, so the cache rides on them."""
    chs = d.get(_DOC_CHUNKS_KEY)
    if chs is None:
        chs = _chunks(d["text"])
        try:
            d[_DOC_CHUNKS_KEY] = chs
        except Exception:
            pass
    return chs


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
    # Provider bias fix: if query has no provider terms, penalize provider-titled docs
    has_provider_term = any(t in ("provider", "providers") for t in terms)
    # v18 (F15): the SFC spouse boost applies only when the
    # QUERY itself involves a spouse; through v17.3 chunk
    # content alone armed it for any query.
    q_spouse = _question_involves_spouse(query)
    scored = []
    for d in corpus:
        title = d["title"].lower()
        title_hits = sum(1 for t in terms if t in title)
        # Penalize provider-facing pages for member queries
        provider_penalty = 0
        if not has_provider_term and "provider" in title:
            provider_penalty = 15
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
            # provider_penalty demotes provider-facing pages for member queries
            score = (phrase_bonus + money_bonus + sfc_bonus + matched * 10 + density
                     + title_hits * 3 - provider_penalty)
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


# Gemini model chain (2026-10-12; v9 reorder at James's direction): the
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
    """v15 (regression fix c, James's manual test 2026-10-13): TWO
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
    still owns."""
    parts = _split_subquestions(question)
    if not parts:
        return hybrid_search(question, corpus=corp, n=n_ex,
                             use_rewrite=use_rewrite,
                             use_rerank=use_rerank)
    merged, _per_part = _retrieve_bundled(
        question, corp, n_ex, use_rewrite=use_rewrite,
        use_rerank=use_rerank)
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


def _check_figures(answer, question, hits, extra_allowed=()):
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
    month", "910 dollars") are checked too, not only $-figures."""
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

    parsed = _parse_citations(answer or "", hits or [])
    derived = set()
    ungrounded = []

    def _near(val, scope):
        return any(abs(val - a) <= 0.011 for a in scope)

    def _flag(val):
        if val and val not in ungrounded:
            ungrounded.append(val)

    for s in parsed["sentences"]:
        text_s = s["text"]
        scope = set(base) | derived
        for j in s["sources"]:
            if 0 <= j < len(hits or []):
                scope |= _grab(hits[j].get("excerpt", ""))
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
        for rx in (money_re, bare_re):
            for m in rx.finditer(text_s):
                try:
                    val = round(float(
                        m.group(1).replace(",", "")), 2)
                except ValueError:
                    continue
                if val == 0:
                    continue
                if not _near(val, scope):
                    _flag(val)
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
    for rx_a, name_a, rx_b, name_b, factor in convs:
        for ma in re.finditer(rx_a, low):
            va = _money_near(low, ma.start())
            mb = re.search(rx_b, low[ma.end():ma.end() + 120])
            if not mb:
                continue
            vb = _money_near(low, ma.end() + mb.start())
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
# provenance instead of silently picking one. Scope (James's
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
    for entry in VERIFIED_FIGURES:
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


def _conflict_claims(conflicts, answer):
    """Conflict records as claim texts, plus the code override
    map (claim text -> forced final) for drafts that do not
    present BOTH values. v18 (F16, pass 2 item F1): conflict
    claims are resolved IN CODE ONLY — _run_unit keeps them
    out of the judge-facing claim set entirely, so the forced
    finals no longer ride the Flash final rung either. The map
    is consumed as conflict_fail by _run_unit."""
    claims, forced = [], {}
    low = (answer or "").lower()
    for cf in conflicts:
        if cf.get("kind") == "age":
            rv, cv = int(cf["registry_value"]), int(
                cf["corpus_value"])

            def _has(v):
                forms = (f"age {int(v)}", f"age of {int(v)}",
                         f"before age {int(v)}")
                return any(f in low for f in forms)

            both = _has(rv) and _has(cv)
            text = (f"The answer surfaces the source disagreement "
                    f"on {cf['entry']}: sources give both age {rv} "
                    f"and age {cv} (corpus variant from "
                    f"{cf['source']}), instead of presenting one "
                    f"value as settled.")
        else:
            rv, cv = cf["registry_value"], cf["corpus_value"]

            def _has(v):
                forms = {f"${v:,.2f}", f"${v:,.0f}", f"{v:,.2f}",
                         f"{v:,.0f}"}
                return any(f.lower() in low for f in forms)

            both = _has(rv) and _has(cv)
            text = (f"The answer surfaces the source disagreement on "
                    f"{cf['entry']}: sources give both "
                    f"${rv:,.2f} and "
                    f"${cv:,.2f} (corpus variant from "
                    f"{cf['source']}), instead of presenting one "
                    f"figure as settled.")
        claims.append({"text": text, "load_bearing": True,
                       "sources": [], "origin": "conflict"})
        if not both:
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
                         "past due")},
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
                         "age of onset", "became disabled")},
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
            "tax credit up to $500."),
        "source": ("IRS ABLE guidance (age 46, 2026) and DFR Policy "
                   "Manual section 2615.10.35 (INvestABLE)"),
        "figures": (20000.0, 100000.0, 500.0),
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
            {"tokens": {"snap"},
             "phrases": ("food stamps", "food assistance")},
            {"tokens": {"resource", "resources", "asset", "assets",
                        "limit", "lump"},
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
                         "community spouse resource")},
            {"tokens": {"savings", "assets", "resources", "countable"},
             "phrases": ()},
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
                         "monthly maintenance needs")},
            {"tokens": {"income"},
             "phrases": ("keep each month",)},
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
            {"tokens": set(), "phrases": ("home equity",)},
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
             "phrases": ("$2,000", "asset limit", "resource limit")},
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
             "phrases": ("medicaid waiver",)},
            {"tokens": set(),
             "phrases": ("income limit", "income cap")},
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
                         "difficulty of care")},
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


def _registry_matches(question):
    """Registry entries whose trigger groups all hit on the question."""
    q_low = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z]+", q_low))
    return [e for e in VERIFIED_FIGURES
            if _groups_hit(e["groups"], q_low, q_tokens)]


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
    return "\n".join(lines)


def _registry_values(entries):
    """Flat list of an entry set's verified dollar figures."""
    vals = []
    for e in entries:
        vals.extend(e.get("figures", ()))
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
                         "income is too high", "too much income")},
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
                         "past due")},
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
                         "back-payment", "resource limit")},
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
                         "community spouse resource")},
            {"tokens": {"savings", "assets", "resources", "countable"},
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
                         "difficulty of care")},
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


def route_local_model(question, requested):
    """Decide the serving local model for an /ask (v13). Only the local
    default is routable: hard questions go to ROUTED_MODEL, everything
    else stays on LOCAL_MODEL. Explicit model picks are returned
    untouched. Returns (model, route_reason)."""
    if requested not in ("", "default", LOCAL_MODEL):
        return requested, ""
    reason = is_hard_question(question)
    if reason:
        return ROUTED_MODEL, reason
    return LOCAL_MODEL, ""


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
# on remote-path asks. The fallback judge seats in _judge_round
# now load the model cold, on demand, only when a remote seat
# has actually failed — and every local judge call carries
# keep_alive 0 (see _ollama_chat_raw), so the model unloads
# again immediately after the call.


def _gemini_normalize(model):
    """Map a requested model string ('gemini-3.8-flash (API)', 'gemini',
    ...) to a chain entry; unknown or generic requests start at the head."""
    m = (model or "").lower()
    for entry in GEMINI_CHAIN:
        if entry in m:
            return entry
    return GEMINI_CHAIN[0]


def _gemini_call(api_model, payload):
    """One model, bounded polite attempts (delays 0/3/8s, 45s timeout;
    v12 speed pass). Returns the
    answer text. Raises _GeminiQuota on daily-quota exhaustion and
    RuntimeError when this model is unusable right now. v15 (S3):
    the transport is the keep-alive pool; spacing, retries, and
    error semantics are unchanged."""
    host = "generativelanguage.googleapis.com"
    path = (f"/v1beta/models/{api_model}:generateContent"
            f"?key={GEMINI_API_KEY}")
    last_err = None
    for delay in (0, 3, 8):
        if delay:
            time.sleep(delay)
        _gemini_space()
        try:
            status, raw = _https_post(
                host, path, payload,
                {"Content-Type": "application/json"}, timeout=45)
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


def gemini_ask(model, question, hits, instructions=""):
    """Ask the Gemini API, best model first, downgrading down GEMINI_CHAIN
    only when a model is unavailable. Returns (answer, prompt, served_model,
    downgraded). Raises if every Gemini model fails; the caller then falls
    back to the local model."""
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
            content = _gemini_call(api_model, payload)
            return (content, sys_prompt + "\n\n" + user_msg,
                    api_model, api_model != start)
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("All Gemini models unavailable: " + "; ".join(errors))


def ollama_ask(model, question, hits, instructions=""):
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
    data = json.loads(urllib.request.urlopen(req, timeout=300).read())
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


def log_gap(question, badge, gate, missing, part=None):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "question": (question or "")[:300], "badge": badge,
           "gate": gate, "missing": list(missing or ())[:8],
           "part": part}
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


# --- v14: Flash arbitration budget ------------------------------------------
# Flash (gemini-3.8-flash) has a 20 requests/day free-tier cap; the
# plan budgets arbitration at ~1-2 calls/day. The counter is a hard
# stop, not a target: at the cap, disputed claims fail closed (cut /
# IDK) rather than shipping unarbitrated.
FLASH_MODEL = "gemini-3.8-flash"
FLASH_DAILY_CAP = 18
_FLASH_STATE_FILE = "flash_state.json"
_FLASH_STATE = {"date": "", "count": 0, "loaded": False}
_FLASH_LOCK = threading.Lock()


def _flash_load_locked():
    """(Caller holds _FLASH_LOCK.) v18 (R10b): the Flash counter
    persists beside the spend ledger, with the ledger's
    Indianapolis day. Through v17.3 it lived in memory only, so
    every restart re-granted up to 18 arbitrations against the
    20/day upstream quota (and the re-grant's 429s fed F1's
    fail-open). A state file that exists but cannot be parsed
    reads as a SPENT budget: fail closed, never re-grant."""
    today = _ledger_today()
    if _FLASH_STATE["loaded"] and _FLASH_STATE["date"] == today:
        return
    count = 0
    try:
        with open(_FLASH_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            count = int(st.get("count") or 0)
    except FileNotFoundError:
        pass
    except Exception:
        count = FLASH_DAILY_CAP
    _FLASH_STATE.update({"date": today, "count": count,
                         "loaded": True})


def _flash_calls_today():
    with _FLASH_LOCK:
        _flash_load_locked()
        return _FLASH_STATE["count"]


def _flash_spend():
    """Reserve one Flash call; False when the daily budget is gone."""
    with _FLASH_LOCK:
        _flash_load_locked()
        if _FLASH_STATE["count"] >= FLASH_DAILY_CAP:
            return False
        _FLASH_STATE["count"] += 1
        try:
            _atomic_write_json(
                _FLASH_STATE_FILE,
                {"date": _FLASH_STATE["date"],
                 "count": _FLASH_STATE["count"]})
        except Exception:
            pass  # the in-memory count still governs this run
        return True


# --- v14.2: third judge seat -> v15: the NIM layer ---------------------------
# v14.2 shipped this as a third judge seat (provider decision,
# James, 2026-10-13: NVIDIA NIM default, Nemotron 3 Super 120B;
# Cerebras began demanding a card, Groq stays a configured
# alternate). v15 (plan item 2, the demotion) promotes the seat
# and grows it into the server's NIM layer:
# - the NIM Nemotron is now the STANDING SECOND JUDGE (verdicts
#   beside the Gemini Lite primary); the metadata keeps the
#   v14.2 "third" seat record shape so consumers do not drift.
# - a DRAFTER block (same provider pattern) drafts hard questions
#   on moonshotai/kimi-k3 and simple questions on a fast NIM
#   model, and generates ladder redrafts, all with the local
#   models as automatic fallbacks.
# All providers here are OpenAI-compatible chat completions with
# Bearer auth, so the client is provider-agnostic and one
# constant per block picks the active provider. Seats are
# DORMANT without their provider's key (gitignored key file in
# the server dir, or env var): a dormant seat simply never runs
# and the pipeline degrades per the v14/v15 rules. Politeness:
# ONE retry on 5xx, NO retry on 429 (a rate-limited seat is
# unavailable for that ask), timeouts sized per seat (the
# judge answers in seconds warm; a cold flagship drafter took
# ~51s for its first reply in the pre-build catalog smoke).
THIRD_JUDGE_PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "key_file": "nvidia_key.txt",
        "key_env": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_file": "groq_key.txt",
        "key_env": "GROQ_API_KEY",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "gpt-oss-120b",
        "key_file": "cerebras_key.txt",
        "key_env": "CEREBRAS_API_KEY",
    },
}
THIRD_JUDGE_PROVIDER = "nvidia"  # <-- the one-line provider flip
_THIRD_CFG = THIRD_JUDGE_PROVIDERS[THIRD_JUDGE_PROVIDER]
THIRD_JUDGE_BASE_URL = _THIRD_CFG["base_url"]
THIRD_JUDGE_MODEL = _THIRD_CFG["model"]
THIRD_JUDGE_KEY_FILE = _THIRD_CFG["key_file"]
THIRD_JUDGE_KEY_ENV = _THIRD_CFG["key_env"]
THIRD_JUDGE_TIMEOUT = 30  # v19 (§G3): was 60; a stalled judge
# provider now costs at most ~30s before the seat fails over.
_THIRD_STATE = {"date": "", "count": 0}
_THIRD_LOCK = threading.Lock()

# v15: the NIM drafting seat (plan item 2 + speedup S4). "model"
# drafts hard questions and generates redrafts; "simple_model"
# drafts simple questions. Ids verified against the live NIM
# catalog at build time (80 models, 2026-10-13): kimi-k3 and
# nemotron-3.5-lightning-30b-a3b both present and answering.
DRAFTER_PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "moonshotai/kimi-k3",
        "simple_model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        # v16: the drafting fallback key is key SLOT 2 (the
        # seats pass their slot's key explicitly; these
        # constants are only the no-seat fallback).
        "key_file": "nvidia_key2.txt",
        "key_env": "NVIDIA_API_KEY2",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "simple_model": "openai/gpt-oss-20b",
        "key_file": "groq_key.txt",
        "key_env": "GROQ_API_KEY",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "gpt-oss-120b",
        "simple_model": "gpt-oss-20b",
        "key_file": "cerebras_key.txt",
        "key_env": "CEREBRAS_API_KEY",
    },
}
DRAFTER_PROVIDER = "nvidia"  # <-- the one-line provider flip
_DRAFT_CFG = DRAFTER_PROVIDERS[DRAFTER_PROVIDER]
DRAFTER_BASE_URL = _DRAFT_CFG["base_url"]
DRAFTER_MODEL = _DRAFT_CFG["model"]
DRAFTER_SIMPLE_MODEL = _DRAFT_CFG["simple_model"]
DRAFTER_KEY_FILE = _DRAFT_CFG["key_file"]
DRAFTER_KEY_ENV = _DRAFT_CFG["key_env"]
DRAFTER_TIMEOUT = 240  # cold flagship drafts are slow (see header)
# v20 (acceptance discipline): NIM drafting legs get a tight
# per-call bound (~60s) so the free-first chain fits the
# per-class unit budgets; DRAFTER_TIMEOUT remains the
# unbounded-retry default for non-seat callers.
NIM_DRAFTER_TIMEOUT = 60
# v15.1 (see V151_AUDIT.md): two NIM seats that keep the local
# models cold. The divergence oracle (item 4) and the emergency
# draft seat both run on the NIM judge-seat model (Nemotron
# Super: strong, fast warm, and independent of the Kimi
# drafter). In v15.0 the oracle drafted on the local 14B, and a
# failed Kimi draft fell straight to local generation; now a
# single slow or dead NIM seat degrades to another NIM seat,
# and the local models generate only when NIM as a whole
# cannot run (or the caller explicitly picked a local model).
DIVERGENCE_ORACLE_MODEL = THIRD_JUDGE_MODEL
EMERGENCY_DRAFT_MODEL = THIRD_JUDGE_MODEL
# v16 supersedes both constants' code paths (kept for the
# record): the divergence oracle drafts on the hard_draft
# seat's runner-up family (see _diverge_model), and the
# emergency-draft role is the drafting seats' measured
# failover inside _generate. Neither constant is referenced
# by v16 code paths.
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


def _third_judge_key():
    """The active judge provider's key (see _nim_key_for)."""
    return _nim_key_for(THIRD_JUDGE_KEY_ENV, THIRD_JUDGE_KEY_FILE)


def _drafter_key():
    """The active drafter provider's key (see _nim_key_for)."""
    return _nim_key_for(DRAFTER_KEY_ENV, DRAFTER_KEY_FILE)


def _third_calls_today():
    with _THIRD_LOCK:
        today = time.strftime("%Y-%m-%d")
        if _THIRD_STATE["date"] != today:
            _THIRD_STATE["date"] = today
            _THIRD_STATE["count"] = 0
        return _THIRD_STATE["count"]


def _third_count_one():
    """Count one completed third-judge call (soft telemetry for the
    free tier's daily token budget; in memory, resets on restart,
    like the Flash counter)."""
    with _THIRD_LOCK:
        today = time.strftime("%Y-%m-%d")
        if _THIRD_STATE["date"] != today:
            _THIRD_STATE["date"] = today
            _THIRD_STATE["count"] = 0
        _THIRD_STATE["count"] += 1


# --- v16: measured seats (item 1) + branch-out (item 7) --------------
# Every NIM job (hard drafting, simple drafting, the two NIM judge
# seats) is a SEAT: an ordered candidate list of (model, key slot)
# assigned by the build-time bake-off (see v16/STATUS.md for the
# table), a latency budget, and a failover order (the bake-off
# runner-up order). Three key slots pin seats to separate key
# files so parallel seats stop sharing one key's rate ceiling.
# Runtime seat health benches a seat on repeated timeouts or a
# rolling median over budget; the job fails over to its next
# measured candidate. Seats are never re-shopped per ask.
SEAT_SLOTS = {
    # v17.1: slot 0 is the OpenRouter pseudo-slot. Candidates
    # pinned to it are served by _or_text (the v17 OpenRouter
    # path: usage reporting, deny routing, cost ledgering)
    # instead of a NIM key. _slot_has_key gates the slot on
    # _or_available(), so a missing key or a tripped daily cap
    # benches every OpenRouter candidate at once and the seat
    # fails over to its NIM candidates, exactly as a keyless
    # NIM slot degrades instead of burning retries.
    0: ("OPENROUTER_API_KEY", "openrouter_key.txt"),
    1: ("NVIDIA_API_KEY", "nvidia_key.txt"),
    2: ("NVIDIA_API_KEY2", "nvidia_key2.txt"),
    3: ("NVIDIA_API_KEY3", "nvidia_key3.txt"),
    # v20: two more pseudo-slots in the same pattern. Slot 4
    # is xAI (an ordinary paid provider; see the v20 provider
    # block below), slot 5 is Groq (free tier, daily-guarded).
    # A missing key file leaves the slot dormant, exactly as
    # the NIM slots have always behaved.
    4: ("XAI_API_KEY", "xai_key.txt"),
    5: ("GROQ_API_KEY", "groq_key.txt"),
}
SEAT_BASE_URL = "https://integrate.api.nvidia.com/v1"
# v20: per-slot base URLs + provider names. Slots 1-3 are
# the NIM slots (SEAT_BASE_URL); slot 0 never reaches the
# generic transport (it is served by _or_text); slots 4/5
# are served by _xai_text / _groq_text, which wrap the same
# OpenAI-compatible client.
SLOT_BASE_URLS = {1: SEAT_BASE_URL, 2: SEAT_BASE_URL,
                  3: SEAT_BASE_URL,
                  4: "https://api.x.ai/v1",
                  5: "https://api.groq.com/openai/v1"}
# Slot 6 is the rationed Gemini 3.8 Flash mid-ladder rung
# (v20 layer model): no key file and no base URL — it is
# served by _gemini_text under its own sub-cap counter.
_SLOT_PROVIDERS = {0: "openrouter", 4: "xai", 5: "groq",
                  6: "gemini"}


def _slot_base_url(slot):
    return SLOT_BASE_URLS.get(slot, SEAT_BASE_URL)


def _slot_provider_name(slot):
    return _SLOT_PROVIDERS.get(slot, "nim")


# --- v20: the xAI + Groq provider block ------------------------------
# Seat chains are config-driven constants (here and in SEATS
# below), so any seat can be reverted by editing one list.
# James's free-first directive (2026-10-14): free + fast
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
# reservation gate and counts against the $2.00/day ledger
# and the $0.50 per-ask ceiling exactly like OpenRouter
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
# Groq: the free tier is the primary rail for hard drafting
# (GPT-OSS-120B), simple drafting + utilities (GPT-OSS-20B),
# and judge seat C (Qwen3.8-27B). Model ids verified TWICE
# on 2026-10-14: against Groq's official models page
# (https://console.groq.com/docs/models — the two GPT-OSS
# models are PRODUCTION; qwen/qwen3.8-27b is a PREVIEW
# model) and against the LIVE account catalog pulled with
# James's own key (exactly 11 models; the three chat ids
# below match exactly; there is NO Llama chat model on his
# catalog, only llama-prompt-guard classifiers, and no
# qwen/qwen3-32b). Preview models can be discontinued at
# short notice: seat C's fallover chain (the proven
# Qwen3-235B on OpenRouter) is the protection, and the
# Stage-2 deploy checklist in v20/STATUS.md re-checks the
# catalog before go-live.
# James is also vaulting his Groq key; when the parent's
# live-catalog pull lands, these constants are the exact
# strings to swap.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_KEY_FILE = "groq_key.txt"
GROQ_KEY_ENV = "GROQ_API_KEY"
GROQ_HARD_MODEL = "openai/gpt-oss-120b"
GROQ_SIMPLE_MODEL = "openai/gpt-oss-20b"
GROQ_UTILITY_MODEL = "openai/gpt-oss-20b"
GROQ_QWEN_MODEL = "qwen/qwen3.8-27b"
GROQ_DRAFTER_TIMEOUT = 60  # Groq's whole pitch is speed
# USD per 1M tokens (input, output), list prices from the
# same official models page. Free-tier calls are recorded
# at these prices for visibility (provider_tier=free) and
# never touch the real-money ledger.
_GROQ_PRICES = {GROQ_HARD_MODEL: (0.15, 0.60),
                GROQ_SIMPLE_MODEL: (0.075, 0.30),
                GROQ_QWEN_MODEL: (0.80, 4.00)}
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
# The xAI grant-mode credit ledger (visibility only; only
# accrues when XAI_FREE_CREDITS is True — see above).
_XAI_CREDIT_FILE = "xai_spend.json"
_XAI_CREDIT_LOCK = threading.Lock()
_XAI_CREDIT_STATE = {"date": "", "credit": 0.0,
                     "broken": False, "loaded": False}
# Candidate lists are in bake-off finishing order (winner first).
# These assignments are written from the bake-off table in
# v16/STATUS.md; if this block and the table ever disagree, the
# table is the record and this block is the bug.
# v20 note: the drafting seats below are now the NIM BENCH
# layer of the drafting chains. The primaries live in
# _generate's explicit chain (hard: Groq GPT-OSS-120B ->
# xAI Grok 4.7 -> OpenRouter GPT-OSS-120B -> this bench ->
# local; simple: Groq GPT-OSS-20B -> OpenRouter -> this
# bench, whose gpt-oss-20b head is the NIM simple primary).
# The judge seats carry their full chains here, as before.
# v18 (§B, pass 2 item B): judge seat 2 is a Qwen-family
# NON-THINKING instruct. The originally planned GPT-OSS-20B was
# rejected in the pass-2 challenge round: on simple questions
# it is the identical model that drafted the answer and ran
# the retrieval utilities, so it would have judged its own
# output, and its price edge is noise under a $2/day ceiling.
# The slug is a single named constant and a PLACEHOLDER pending
# pre-battery verification of the exact OpenRouter slug and
# price (a live read, forbidden during the offline build); the
# seat passes the §E replay-calibration gate before it judges
# live traffic (PLAN §F).
JUDGE_SEAT2_MODEL = "qwen/qwen3-235b-a22b-2507"
JUDGE_SEAT2_MAX_TOKENS = 1500  # non-thinking: no reasoning burn
# Seat 2's failover: a fifth-family OpenRouter open-weight
# model (placeholder slug, same pre-battery verification),
# then the NIM Nemotron Ultra lane.
SEAT2_FAILOVER_MODEL = "meta-llama/llama-4-maverick"

SEATS = {
    "hard_draft": {
        # v20: the bench order follows the Layer-2 chain spec
        # (Kimi K3, then GLM-5.3, lead); Nemotron Ultra and
        # DeepSeek V4.1-Flash remain as deeper free depth.
        "candidates": [("moonshotai/kimi-k3", 2),
                       ("z-ai/glm-5.3", 2),
                       ("nvidia/nemotron-3-ultra-550b-a55b", 2),
                       ("deepseek-ai/deepseek-v4.1-flash", 2)],
        "budget_median": 30.0, "budget_p95": 120.0},
    "simple_draft": {
        "candidates": [("openai/gpt-oss-20b", 2),
                       ("z-ai/glm-5.3-flash", 2),
                       ("nvidia/nemotron-3.5-lightning-30b-a3b", 2)],
        "budget_median": 30.0, "budget_p95": 120.0},
    "judge_nim1": {
        # v17.1 (James's ruling, 2026-10-14): the paid judges
        # lead this seat — OpenRouter DeepSeek V3.1, then
        # OpenRouter Qwen3-32B, with NIM Nemotron Super kept
        # as the free failover (the seat's old runner-ups
        # served in judge_nim2's chain until v17.2 moved
        # that seat to OpenRouter too). Evidence is the
        # coordinator's 10-claim Miller-packet probe, accepted
        # by James in place of a battery run: DeepSeek 10/10
        # verdicts at $0.000573/call (31s), Qwen 10/10 at
        # $0.000297 (23s), Super 8/10 (36s, one empty first
        # attempt). The median budget rises from 20s to 45s
        # because it was written for the old seat: at 20s the
        # bench rule would bench the new primaries on their
        # own measured latency and silently hand the seat
        # back to the failover.
        # v20 (seat B, DeepSeek family; final form under
        # James's layer model, 2026-10-14): NIM DeepSeek
        # V4.1-Flash is the FREE primary (id verified in the
        # live NIM catalog pull of 2026-10-14, where it is
        # deepseek-ai/deepseek-v4.1-flash; it already serves
        # on the hard_draft bench), then NIM Nemotron Super
        # (the rest of the free layer), then the rationed
        # Gemini 3.8 Flash mid-ladder rung (slot 6, Layer 1
        # rules: sub-cap FLASH_MID_DAILY_CAP, never a badge
        # stand-in for this seat's family — the panel records
        # it as-served, a google voice), then the proven
        # V3.1 on OpenRouter as the prepaid fallover. All
        # three judge primaries are free (Lite via Google,
        # V4.1-Flash via NIM, Qwen3.8-27B via Groq). NOTE:
        # V4.1-Flash is UNGRADED as a primary judge — Stage 3
        # grading compares this panel against the v19 panel
        # (V3.1 primary) on the anchors before the free-first
        # panel is trusted, and this candidate order stays
        # config-reversible until then (v20/STATUS.md).
        "candidates": [("deepseek-ai/deepseek-v4.1-flash", 2),
                       ("nvidia/nemotron-3-super-120b-a12b", 1),
                       (FLASH_MODEL, 6),
                       ("deepseek/deepseek-chat-v3.1", 0)],
        "budget_median": 45.0, "budget_p95": None},
    "judge_nim2": {
        # v17.2 (James's ruling, 2026-10-14): this seat goes
        # paid too, mirroring v17.1's judge_nim1 swap. The
        # v17.1 single test isolated this seat as the
        # remaining drag: Ultra benched on timeouts, its
        # DeepSeek-flash call ~90s and GLM calls ~120s on the
        # free tier (judge_ms 363s of that ask's total), while
        # the new OpenRouter seat ran a 12.4s median.
        # OpenRouter Qwen3-32B leads: judge_nim1's primary is
        # DeepSeek, so the two paid seats lead on different
        # model families and the standing panel is Gemini
        # Lite + DeepSeek + Qwen when all primaries serve.
        # OpenRouter DeepSeek V3.1 is second; NIM Nemotron
        # Ultra stays as free failover depth on slot 3. The
        # slot-0 candidates ride the same OpenRouter
        # pseudo-slot as judge_nim1's: a daily-cap trip
        # benches both at once and the seat falls to Ultra.
        # The median budget rises from 20s to 45s, mirroring
        # judge_nim1's v17.1 adjustment: the accepted probe
        # measured 23s (Qwen) and 31s (DeepSeek) per verdict
        # call, so at 20s the bench rule would bench the new
        # primaries on their own measured latency and
        # silently hand the seat back to the failover.
        # v18 (§B): seat 2's primary is now the Qwen-family
        # non-thinking instruct named by JUDGE_SEAT2_MODEL
        # (GPT-OSS-20B was rejected in pass 2: it drafts and
        # runs utilities on simple asks, so it would judge its
        # own output). Qwen3-32B is OUT of both judge chains:
        # it stays a drafting/utility candidate only. Failover
        # is a fifth-family OpenRouter model, then NIM Ultra.
        # v20 (seat C, Alibaba family; final form under
        # James's layer model, 2026-10-14): the Groq free
        # tier leads with Qwen3.8-27B (GROQ_QWEN_MODEL; id
        # verified on Groq's official models page AND in the
        # live account catalog pull, both 2026-10-14 — a
        # preview model, so this chain's depth is the
        # protection), then the rationed Gemini 3.8 Flash
        # mid-ladder rung (slot 6, Layer 1 rules), then the
        # v19 primary — the proven Qwen3-235B instruct on
        # OpenRouter — as the prepaid fallover.
        # v20.1 (fix 1, James's approval 2026-10-14): the
        # v19 tail is RESTORED behind the 235B, for depth
        # and for the Meta-family tail voice: NIM Nemotron
        # Ultra — the v19 seat's own Ultra entry,
        # nvidia/nemotron-3-ultra-550b-a55b on key slot 3 —
        # listed ahead of the paid legs per the layer model,
        # then SEAT2_FAILOVER_MODEL (Llama-4-Maverick,
        # OpenRouter). The first v20 pass dropped the tail
        # because the concrete chain in James's layer
        # directive ended at the 235B; both models remained
        # in the file throughout (SEAT2_FAILOVER_MODEL
        # stayed defined and priced; Ultra still serves the
        # hard_draft bench), so the restore is config-only.
        "candidates": [(GROQ_QWEN_MODEL, 5),
                       (FLASH_MODEL, 6),
                       ("nvidia/nemotron-3-ultra-550b-a55b", 3),
                       (JUDGE_SEAT2_MODEL, 0),
                       (SEAT2_FAILOVER_MODEL, 0)],
        "budget_median": 45.0, "budget_p95": None},
}
SEAT_BENCH_COOLDOWN_S = 600
_SEAT_STATE = {}
_SEAT_LOCK = threading.Lock()


def _seat_state(job, model):
    key = (job, model)
    if key not in _SEAT_STATE:
        _SEAT_STATE[key] = {"lat": [], "consec_timeouts": 0,
                            "benched_until": 0.0, "benches": 0,
                            "calls": 0, "failovers": 0}
    return _SEAT_STATE[key]


def _slot_has_key(slot):
    """Whether a key is configured for a key slot (env var or
    the slot's key file). A seat whose slot has no key is
    dormant, exactly as v15's seats were without their key.
    Slot 0 is the OpenRouter pseudo-slot (v17.1): it counts as
    keyed only while the OpenRouter seats may run at all (key
    present and the daily spend cap untripped)."""
    if slot == 0:
        return _or_available()
    if slot == 4:
        # v20: the xAI pseudo-slot counts as keyed only while
        # xAI may run (key present; in the default paid mode
        # also the real-money ceiling untripped, exactly like
        # the OpenRouter pseudo-slot it shares a ledger with).
        return _xai_available()
    if slot == 5:
        # v20: the Groq pseudo-slot counts as keyed only
        # while the key is present AND the daily guard has
        # not shed Groq for the rest of the ledger day.
        return _groq_available()
    if slot == 6:
        # v20: the Flash mid-ladder rung counts as keyed
        # only while its daily ration lasts (the Gemini
        # client itself enforces spacing, backoff, and the
        # model's hard 20/day cap at call time).
        return _flash_mid_available()
    key_env, key_file = SEAT_SLOTS[slot]
    return _nim_key_for(key_env, key_file) is not None


def _candidate_layer(model, slot):
    """v20 (James's layer model, 2026-10-14): a seat
    candidate's chain layer. Layer 0 = free + fast (Groq,
    slot 5); Layer 2 = free + slow (the NIM slots); Layer 3 =
    paid (OpenRouter slot 0, xAI slot 4). Gemini 3.8 Flash's
    rationed mid-ladder rung (slot 6) is classed Layer 1
    (free + strong + scarce) as a PROVIDER, but its chain
    position is the mid-ladder one the concrete seat chains
    assign it — after the seat's free primaries, ahead of
    every paid fallover — so its ordering value sits between
    the NIM layer and the paid layer. Ordering rule: layer
    wins over latency; the v19 latency ordering applies
    INSIDE a layer only."""
    if slot == 5:
        return 0.0
    if slot in (1, 2, 3):
        return 2.0
    if slot == 6:
        return 2.5
    return 3.0


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


def _seat_pick(job, skip=(), exclude_families=()):
    """The active (model, key slot) for a job: the first
    candidate not benched, not in skip, and on a key slot that
    has a key configured (a keyless seat is skipped over, so a
    missing key file degrades the panel instead of burning
    retries). Returns (model, slot, is_failover);
    (None, None, False) when every candidate is benched,
    skipped, or keyless. v18 (§C): `exclude_families` bars
    whole model FAMILIES (the drafting family; a family already
    holding another verdict seat), and a round's atomically
    planned pick (see _plan_panel) leads when present: the
    planned seat, once tried and failed, is NOT replaced
    mid-round (a same-family substitute is how the Miller
    panel silently became Lite + DeepSeek + DeepSeek)."""
    now = time.monotonic()
    cands = SEATS[job]["candidates"]
    ctx = _ask_ctx()
    excl = set(exclude_families)
    if job in ("judge_nim1", "judge_nim2"):
        for other, pick in ctx.panel_plan.items():
            if other != job:
                excl.add(_model_family(pick[0]))
    plan = ctx.panel_plan.get(job)
    if plan is not None:
        pmodel, pslot = plan
        if pmodel in skip:
            return None, None, False
        if _model_family(pmodel) not in excl \
                and _slot_has_key(pslot):
            with _SEAT_LOCK:
                benched = _seat_state(job, pmodel)[
                    "benched_until"] > now
            if not benched:
                idx = [m for m, _s in cands].index(pmodel)
                return pmodel, pslot, idx > 0
        # planned seat unusable (key gone, benched, excluded):
        # fall through to the guarded scan below
    # v20: the scan walks layer-major order (the configured
    # list order inside a layer): a lower layer's candidate
    # is always tried before any higher layer's, whatever
    # the latency table says.
    for idx in sorted(range(len(cands)),
                      key=lambda i: (_candidate_layer(*cands[i]),
                                     i)):
        model, slot = cands[idx]
        if model in skip or not _slot_has_key(slot):
            continue
        if _model_family(model) in excl:
            continue
        with _SEAT_LOCK:
            st = _seat_state(job, model)
            if st["benched_until"] > now:
                continue
        return model, slot, idx > 0
    return None, None, False


def _seat_exclusions(job):
    """§C: the families that may not serve `job` as a verdict:
    the drafting model's family, plus every family that has
    already served ANOTHER verdict job this ask."""
    ctx = _ask_ctx()
    out = set()
    if ctx.draft_family:
        out.add(ctx.draft_family)
    for other, fams in ctx.verdict_families.items():
        if other != job:
            out |= set(fams)
    return out


def _seat_median(job, model):
    """v19 (§G2): the rolling median latency (seconds) of a
    seat candidate from its recent calls, or None when the
    candidate has never served on this server run."""
    with _SEAT_LOCK:
        lat = list(_seat_state(job, model)["lat"])
    if not lat:
        return None
    return sorted(lat)[len(lat) // 2]


def _plan_panel():
    """§C (pass 1 D4 + pass 2 item B5): plan the round's two
    stage-2 verdict seats ATOMICALLY on the ask context, before
    any verdict call starts. The seats get DISTINCT model
    families; the drafting family and families that already
    served verdicts this ask are excluded from both. Without
    the plan, two concurrent picks could each see the other's
    seat as empty and land on the same family. A seat that
    cannot be filled with a distinct family stays unfilled:
    the badge degrades via per-claim coverage instead of a
    same-family substitute voting twice.
    v19 (§G2): among the eligible candidates the seat leads
    with the FASTEST MEASURED one (rolling median latency);
    an unmeasured candidate is scored at 75% of the seat's
    median budget, so it gets tried without leading blind.
    Ties keep the configured candidate order. Failover inside
    a call still walks the configured order (§C's rule that a
    planned seat is not replaced mid-round is unchanged).
    v20: the plan key is (layer, latency, order) — James's
    layer model wins over latency, and the §G2 latency
    ordering applies inside a layer only, so a measured-fast
    paid candidate can never lead a seat over a free one."""
    ctx = _ask_ctx()
    taken = set()
    if ctx.draft_family:
        taken.add(ctx.draft_family)
    for fams in ctx.verdict_families.values():
        taken |= set(fams)
    for job in ("judge_nim1", "judge_nim2"):
        if job in ctx.panel_plan:
            taken.add(_model_family(ctx.panel_plan[job][0]))
            continue
        best = None
        for order, (model, slot) in enumerate(SEATS[job]["candidates"]):
            fam = _model_family(model)
            if fam in taken or not _slot_has_key(slot):
                continue
            with _SEAT_LOCK:
                if _seat_state(job, model)["benched_until"] \
                        > time.monotonic():
                    continue
            med = _seat_median(job, model)
            score = med if med is not None \
                else SEATS[job]["budget_median"] * 0.75
            key = (_candidate_layer(model, slot), score, order)
            if best is None or key < best[0]:
                best = (key, model, slot, fam)
        if best is not None:
            _key, model, slot, fam = best
            ctx.panel_plan[job] = (model, slot)
            taken.add(fam)


def _seat_report(job, model, latency_s, ok, timed_out=False):
    """Record one seat call. Bench rule (item 1): two
    consecutive timeouts, or a rolling median (last 8 calls,
    at least 4 samples) over the job's median budget, benches
    the seat for SEAT_BENCH_COOLDOWN_S; a successful call after
    cooldown is the probe that re-enters it."""
    with _SEAT_LOCK:
        st = _seat_state(job, model)
        st["calls"] += 1
        st["lat"].append(round(latency_s, 2))
        del st["lat"][:-8]
        if timed_out:
            st["consec_timeouts"] += 1
        elif ok:
            st["consec_timeouts"] = 0
        bench = False
        if st["consec_timeouts"] >= 2:
            bench = True
        elif len(st["lat"]) >= 4:
            med = sorted(st["lat"])[len(st["lat"]) // 2]
            if med > SEATS[job]["budget_median"]:
                bench = True
        if bench and st["benched_until"] <= time.monotonic():
            st["benched_until"] = time.monotonic() \
                + SEAT_BENCH_COOLDOWN_S
            st["benches"] += 1
            st["consec_timeouts"] = 0
            print(f"[seat] BENCH {job} {model} "
                  f"(median over budget or repeated timeouts)",
                  flush=True)


def _seat_health_block():
    """GET /health's seat block + the ask metadata's seat
    record: per job, the assigned candidates with live state,
    and which model is active right now."""
    now = time.monotonic()
    out = {}
    for job, cfg in SEATS.items():
        active, _slot, _fo = _seat_pick(job)
        seats = []
        for model, slot in cfg["candidates"]:
            with _SEAT_LOCK:
                st = _seat_state(job, model)
                lat = list(st["lat"])
                seats.append({
                    "model": model, "key_slot": slot,
                    "calls": st["calls"], "benches": st["benches"],
                    "benched": st["benched_until"] > now,
                    "median_s": (sorted(lat)[len(lat) // 2]
                                 if lat else None)})
        out[job] = {"active": active, "seats": seats}
    return out


# --- v18 (F10): the per-ask context ----------------------------------
# Through v17.3, per-ask cost, seat events, and retrieval timings
# lived in thread-locals while the pipeline runs its judging,
# drafting, and retrieval in thread pools: everything a worker
# thread recorded was stranded and lost (per-ask cost_usd omitted
# both OpenRouter judge seats; the seat events that would have
# diagnosed the Lite + DeepSeek + DeepSeek panel never survived).
# The context is now an explicit object carried by a ContextVar;
# pool submissions go through _submit, which propagates the
# current context into the worker. The object also carries the
# panel-integrity state (§C): which model families have served
# verdicts this ask, which model served each job, the round's
# atomically planned seats, and the drafting model's family
# (excluded from every verdict path, pass 2 item B).

class _AskContext:
    __slots__ = ("cost", "events", "retr", "verdict_families",
                 "served_models", "panel_plan", "draft_family",
                 "lock", "unit_deadline")

    def __init__(self):
        self.cost = 0.0
        self.events = []
        self.retr = {}
        self.verdict_families = {}   # judge job -> {families served}
        self.served_models = {}      # job -> model that served last
        self.panel_plan = {}         # judge job -> (model, slot)
        self.draft_family = None
        self.lock = threading.Lock()
        # v20: the current unit's monotonic deadline, set by
        # _run_unit per unit (units run sequentially inside an
        # ask); chain legs clamp their timeouts to what is
        # left of it via _leg_timeout. None outside a unit.
        self.unit_deadline = None


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
    _ASK_CTX_VAR.set(_AskContext())


def _note_seat(job, model, failover):
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.events.append({"kind": "seat", "job": job,
                           "model": model,
                           "failover": bool(failover)})
        ctx.served_models[job] = model
        if job.startswith("judge"):
            ctx.verdict_families.setdefault(job, set()).add(
                _model_family(model))


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


def _judge_seat_text(job, system, user):
    """One verdict call on a judge seat, with seat health
    and failover to the seat's next measured candidate.
    Raises RuntimeError when every candidate fails (the caller
    degrades per the panel rules). v17.1: a candidate on the
    OpenRouter pseudo-slot (slot 0) is served by _or_text with
    a 4000-token budget (both pinned judges are reasoning
    models; the NIM seats keep their 2048). v18 (§C): the pick
    honors the round's atomic panel plan and the family
    exclusions (the drafting family never judges); seat 2's
    non-thinking instruct gets JUDGE_SEAT2_MAX_TOKENS instead
    of the reasoning budget, since there is no reasoning to
    burn and the cap is what the reservation prices."""
    # v20: if this seat's chain carries a Groq candidate and
    # the daily guard has shed Groq, the shed is logged in
    # the ask record (the pick below silently skips the slot,
    # exactly like a keyless slot).
    if _groq_guard_tripped() and any(
            s == 5 for _m, s in SEATS[job]["candidates"]):
        _note_groq_shed(job)
    tried = []
    while True:
        model, slot, failover = _seat_pick(
            job, skip=tried,
            exclude_families=_seat_exclusions(job))
        if model is None:
            raise RuntimeError(
                f"{job}: all seat candidates unavailable "
                f"(tried {tried})")
        # v20: the leg runs clamped to the unit's remaining
        # budget; at the budget edge the seat sheds instead
        # of starting a call that cannot finish inside it.
        _jt = _leg_timeout(THIRD_JUDGE_TIMEOUT)
        if _jt <= 0:
            raise RuntimeError(
                f"{job}: unit budget spent; seat leg shed")
        t0 = time.monotonic()
        try:
            if slot == 0:
                max_tokens = 4000
                if job == "judge_nim2" \
                        and model == JUDGE_SEAT2_MODEL:
                    max_tokens = JUDGE_SEAT2_MAX_TOKENS
                content = _or_text(
                    model, system, user, temperature=0,
                    max_tokens=max_tokens,
                    timeout=_jt)
            elif slot == 4:
                # v20: xAI candidate (no v20 judge chain seats
                # one by default; the dispatch keeps the slot
                # config-reversible like the others).
                content = _xai_text(
                    model, system, user, temperature=0,
                    max_tokens=4000,
                    timeout=_jt)
            elif slot == 5:
                # v20: Groq candidate (seat C's Qwen primary).
                content = _groq_text(
                    model, system, user, temperature=0,
                    max_tokens=4000,
                    timeout=_jt)
            elif slot == 6:
                # v20: the rationed Gemini 3.8 Flash
                # mid-ladder rung (counted at attempt time;
                # the Gemini client applies its own spacing
                # and timeout discipline). The verdict records
                # as-served: a google voice in this seat, never
                # a stand-in for the seat's own family.
                _flash_mid_note()
                content = _gemini_text(FLASH_MODEL, system, user)
            else:
                key_env, key_file = SEAT_SLOTS[slot]
                content = _nim_text(
                    _slot_base_url(slot), key_env, key_file,
                    model, system, user, temperature=0,
                    max_tokens=2048,
                    timeout=_jt,
                    provider="nim")
            lat = time.monotonic() - t0
            _seat_report(job, model, lat, True)
            if failover:
                with _SEAT_LOCK:
                    _seat_state(job, model)["failovers"] += 1
            _note_seat(job, model, failover)
            return content
        except Exception as e:
            lat = time.monotonic() - t0
            _seat_report(job, model, lat, False,
                         timed_out=lat >= THIRD_JUDGE_TIMEOUT * 0.9
                         or "timed out" in str(e)
                         or "timeout" in str(e).lower())
            tried.append(model)
            if len(tried) >= len(SEATS[job]["candidates"]):
                raise


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


def _tavily_credits():
    with _TAVILY_LOCK:
        month = time.strftime("%Y-%m")
        try:
            with open(_TAVILY_STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("month") != month:
                st = {"month": month, "credits": 0}
        except Exception:
            st = {"month": month, "credits": 0}
        return st


def _tavily_spend(n):
    with _TAVILY_LOCK:
        st = _tavily_credits()
        st["credits"] += n
        try:
            # v18: atomic write, like the spend ledger (a crash
            # mid-write must not zero the monthly counter).
            _atomic_write_json(_TAVILY_STATE_FILE, st)
        except Exception:
            pass
        return st["credits"]


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
    if _tavily_credits()["credits"] >= BRANCH_MONTHLY_STOP:
        rec["unavailable"] = "monthly credit budget reached"
        return [], rec
    queries = _branch_queries(question, claims)
    urls, seen, titles = [], set(), {}
    try:
        for q in queries:
            d = _tavily_post("/search", {
                "query": q, "search_depth": "basic",
                "include_domains": list(BRANCH_WHITELIST),
                "max_results": 5})
            _tavily_spend(1)
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
        d = _tavily_post("/extract", {"urls": urls,
                                      "extract_depth": "basic"})
        _tavily_spend(1)
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
# The paid swap (James's go, 2026-10-13): OpenRouter takes the
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
OR_LITE_MODEL = "google/gemini-3.5-flash-lite"
OPENROUTER_DRAFTER_TIMEOUT = 120  # Cerebras drafts land in
# seconds; the bound only caps a degraded backend before the
# NIM seat takes over (the NIM path keeps DRAFTER_TIMEOUT).
OPENROUTER_DAILY_CAP_USD = float(
    os.environ.get("OPENROUTER_DAILY_CAP_USD", "2.00"))
_OR_ROUTES = {
    OPENROUTER_HARD_MODEL: {
        "order": ["Cerebras", "Groq"], "allow_fallbacks": True,
        "data_collection": "deny"},
    OPENROUTER_SIMPLE_MODEL: {
        "order": ["Groq"], "allow_fallbacks": True,
        "data_collection": "deny"},
    # v17.1: the judge seats' OpenRouter candidates (judge_nim1
    # in v17.1; judge_nim2 joined in v17.2 with the same two
    # models in the opposite order). No
    # backend order is pinned (no per-backend latency
    # measurement exists for judging; the probe ran unpinned),
    # but the deny restriction matches the drafting seats, so
    # only backends that do not collect prompts may serve.
    "deepseek/deepseek-chat-v3.1": {
        "allow_fallbacks": True, "data_collection": "deny"},
    "qwen/qwen3-32b": {
        "allow_fallbacks": True, "data_collection": "deny"},
    # v18: the seat-2 pair (JUDGE_SEAT2_MODEL and its
    # fifth-family failover). Same posture: fallbacks allowed
    # across the model's backends, data collection denied.
    # Slugs/prices verified pre-battery (see the constant).
    JUDGE_SEAT2_MODEL: {
        "allow_fallbacks": True, "data_collection": "deny"},
    SEAT2_FAILOVER_MODEL: {
        "allow_fallbacks": True, "data_collection": "deny"},
}

# --- v18: the reservation gate + spend ledger (the §B hard stop) -------
# James's mechanism (v18 PLAN §B): the daily OpenRouter cap is a
# true hard ceiling of $2.00/day, enforced by RESERVATIONS at a
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
    "deepseek/deepseek-chat-v3.1": (0.25, 0.95),
    "qwen/qwen3-32b": (0.30, 1.20),
}
# v18 seat-2 rows (placeholder prices pending the pre-battery
# verification, set conservatively at the class maximum).
_OR_PRICES[JUDGE_SEAT2_MODEL] = (0.30, 1.20)
_OR_PRICES[SEAT2_FAILOVER_MODEL] = (0.60, 2.40)
# v20: seat A's Lite fallover row (catalog-verified above).
_OR_PRICES[OR_LITE_MODEL] = (0.30, 2.50)
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


def _or_committed():
    """Settled (reconciled) + outstanding reservations: the
    number the gate enforces the ceiling against."""
    with _OR_LOCK:
        _or_load_locked()
        return _or_committed_locked()


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


def _or_note_cost(cost):
    """Compatibility shim for any v17-era caller: record a
    settled cost with no reservation. v18 code settles through
    _or_settle; nothing on the OpenRouter path calls this."""
    if not cost or cost <= 0:
        return
    with _OR_LOCK:
        _or_load_locked()
        _OR_STATE["spend"] += float(cost)
        _or_persist_locked()
    ctx = _ask_ctx()
    with ctx.lock:
        ctx.cost += float(cost)


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
             timeout=60):
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
                     extra=extra, or_route=True)


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


def _groq_count_one():
    """Count one Groq request (counted at attempt time: the
    free plan's daily quota counts requests, not successes)."""
    with _GROQ_LOCK:
        _groq_load_locked()
        _GROQ_STATE["requests"] += 1
        _groq_persist_locked()
        return _GROQ_STATE["requests"]


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
            "qwen_model": GROQ_QWEN_MODEL,
            "requests_today": _groq_requests_today(),
            "daily_soft_cap": GROQ_DAILY_SOFT_CAP,
            "guard_tripped": _groq_guard_tripped(),
            "list_usd_today": round(_groq_list_usd_today(), 6),
            "provider_tier": "free"}


# --- v20: the Gemini 3.8 Flash mid-ladder ration ---------------------
# Layer 1 of James's layer model: Gemini 3.8 Flash may step
# in mid-ladder as a replacement judge (judge seats B and C,
# slot 6) when a free primary fails — but it is free, strong,
# and SCARCE (20 requests/day on the free key), and its
# final-rung/arbitration reserve must survive. A persistent
# per-day counter (Indiana ledger day, the spend ledger's
# pattern) sub-caps mid-ladder use at FLASH_MID_DAILY_CAP
# (default 10): the other half of the day's quota is never
# touched by mid-ladder duty. The rung records as-served
# (a google voice); it never impersonates the seat's family
# for badge purposes.
FLASH_MID_DAILY_CAP = int(
    os.environ.get("FSSA_FLASH_MID_DAILY_CAP", "10"))
_FLASH_MID_FILE = "flash_mid_usage.json"
_FLASH_MID_LOCK = threading.Lock()
_FLASH_MID_STATE = {"date": "", "count": 0, "broken": False,
                    "loaded": False}


def _flash_mid_load_locked():
    """(Caller holds _FLASH_MID_LOCK.)"""
    today = _ledger_today()
    if _FLASH_MID_STATE["loaded"] \
            and _FLASH_MID_STATE["date"] == today:
        return
    count, broken = 0, False
    try:
        with open(_FLASH_MID_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today:
            count = int(st.get("count") or 0)
    except FileNotFoundError:
        pass
    except Exception:
        broken = True
    _FLASH_MID_STATE.update({"date": today, "count": count,
                             "broken": broken, "loaded": True})
    if broken:
        print("[flash-mid] ration counter unreadable; the "
              "mid-ladder rung sheds until it is repaired",
              flush=True)


def _flash_mid_persist_locked():
    """(Caller holds _FLASH_MID_LOCK.)"""
    if _FLASH_MID_STATE["broken"]:
        return
    try:
        _atomic_write_json(_FLASH_MID_FILE, {
            "date": _FLASH_MID_STATE["date"],
            "count": _FLASH_MID_STATE["count"]})
    except Exception as e:
        print(f"[flash-mid] ration counter write failed: {e}",
              flush=True)


def _flash_mid_count_today():
    with _FLASH_MID_LOCK:
        _flash_mid_load_locked()
        return _FLASH_MID_STATE["count"]


def _flash_mid_available():
    """The mid-ladder rung may run: today's ration is not
    spent (an unreadable counter sheds the rung, fail-safe
    toward the reserve)."""
    with _FLASH_MID_LOCK:
        _flash_mid_load_locked()
        if _FLASH_MID_STATE["broken"]:
            return False
        return _FLASH_MID_STATE["count"] < FLASH_MID_DAILY_CAP


def _flash_mid_note():
    """Count one mid-ladder Flash call (at attempt time: the
    ration protects a scarce quota, spent or failed)."""
    with _FLASH_MID_LOCK:
        _flash_mid_load_locked()
        _FLASH_MID_STATE["count"] += 1
        _flash_mid_persist_locked()


def _flash_mid_health_block():
    """GET /health's flash-mid block: today's ration use."""
    return {"model": FLASH_MODEL,
            "used_today": _flash_mid_count_today(),
            "daily_sub_cap": FLASH_MID_DAILY_CAP,
            "final_rung_daily_cap": FLASH_DAILY_CAP,
            "available": _flash_mid_available()}


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
              timeout=60):
    """One xAI chat call. Default (paid) mode: the call
    reserves its provable maximum against the shared
    real-money ledger BEFORE it starts — a refusal means the
    $2.00 ceiling would be crossed and the seat behaves as
    unavailable, exactly like an OpenRouter call — and
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
    try:
        content = _nim_text(
            XAI_BASE_URL, XAI_KEY_ENV, XAI_KEY_FILE, model,
            system, user, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            provider="xai", usage_out=usage)
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
    else:
        _or_settle(reservation, actual)
    return content


def _groq_text(model, system, user, temperature=0, max_tokens=2048,
               timeout=60):
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
    _groq_count_one()
    usage = {}
    content = _nim_text(
        GROQ_BASE_URL, GROQ_KEY_ENV, GROQ_KEY_FILE, model,
        system, user, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout,
        provider="groq", usage_out=usage)
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
              usage_out=None):
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
        reservation = None
        if or_route:
            # v18 (§B hard stop): every OpenRouter HTTP attempt
            # takes its own reservation at the call's provable
            # maximum BEFORE it starts. A refusal means the
            # $2.00 ceiling would be crossed: the call never
            # starts and the seat behaves as unavailable,
            # exactly like a v17 cap trip.
            reservation = _or_reserve(
                _or_max_cost(model, system, user, max_tokens))
            if reservation is None:
                raise RuntimeError(
                    "openrouter daily ceiling reached "
                    "(reservation refused)")
        try:
            status, raw = _https_post(
                host, path, payload,
                {"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
                timeout=timeout)
        except Exception as e:
            # The request was sent and never parsed (timeout,
            # transport failure): settle at the full reserved
            # maximum (F11-1); a retry reserves anew.
            _or_settle(reservation, None)
            last_err = e
            if attempt == 0:
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


def _third_judge_text(system, user):
    """One verdict call to the judge_nim1 seat (v16: seat-based;
    health-tracked with failover to the seat's runner-up
    candidates; v17.1: the seat's lead candidates ride
    OpenRouter, NIM Nemotron Super is its free failover).
    Temperature 0."""
    content = _judge_seat_text("judge_nim1", system, user)
    _third_count_one()
    return content


def _nim2_judge_text(system, user):
    """One verdict call to the judge_nim2 seat (v16 item 3: the
    second standing judge seat, a different model family from
    seat 1 where the bake-off allowed; v17.2: the seat's lead
    candidates ride OpenRouter, NIM Nemotron Ultra is its free
    failover on key slot 3)."""
    return _judge_seat_text("judge_nim2", system, user)


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
# remote drafts are born (nim_ask / or_ask) so every consumer
# downstream — the citation gate, the judges, the capped
# assembly — sees one dialect. Canonical [n] markers match
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


def nim_ask(model, question, hits, instructions="", key_env=None,
            key_file=None, timeout=None):
    """One NIM draft generation (v15 item 2 / S4): the same prompt
    shape as the local and Gemini drafters (SYSTEM_PROMPT +
    instructions, numbered excerpts), temperature 0.3. Returns
    (answer, prompt). Raises when the seat cannot run; callers
    fall back through the drafting seat's candidates first and
    to local models only when the whole seat is down (v16).
    `timeout` overrides DRAFTER_TIMEOUT for bounded retries."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    content = _nim_text(DRAFTER_BASE_URL, key_env or DRAFTER_KEY_ENV,
                        key_file or DRAFTER_KEY_FILE, model,
                        sys_prompt, user_msg,
                        temperature=0.3, max_tokens=2048,
                        timeout=timeout or DRAFTER_TIMEOUT,
                        provider=DRAFTER_PROVIDER)
    # Hosted open models occasionally trail control-token debris
    # (kimi-k3's catalog smoke ended "<|close|>response"); it is
    # never answer text, so cut anything from the first such
    # marker onward.
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    # v17.1: the GPT-OSS models serve these seats too (on NIM
    # as fallback candidates); normalize their citation
    # dialect before the pipeline reads the draft.
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("NIM drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


def or_ask(model, question, hits, instructions="", timeout=None):
    """One OpenRouter draft generation (v17): nim_ask's exact
    prompt shape and debris strip on the paid primary seat.
    Returns (answer, prompt). Raises when the seat cannot run;
    _generate then fails over to the drafting seat's v16 NIM
    candidates."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = _system_prompt_for(question) + (("\n\n" + instructions) if instructions
                                  else "")
    content = _or_text(model, sys_prompt, user_msg,
                       temperature=0.3, max_tokens=2048,
                       timeout=timeout or OPENROUTER_DRAFTER_TIMEOUT)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    # v17.1: normalize the GPT-OSS citation dialect (the v17
    # pest-control failure) before the pipeline reads the draft.
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("openrouter drafter returned an "
                           "empty response")
    return content, sys_prompt + "\n\n" + user_msg


def xai_ask(model, question, hits, instructions="", timeout=None):
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
                        timeout=timeout or XAI_DRAFTER_TIMEOUT)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("xai drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


def groq_ask(model, question, hits, instructions="", timeout=None):
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
                         timeout=timeout or GROQ_DRAFTER_TIMEOUT)
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    content = _normalize_citations(content)
    if not content:
        raise RuntimeError("groq drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


def _panel_info(judges, ms, third_status, third_error=None,
                nim2_status="not-run", nim2_error=None,
                served=None):
    """The judging-metadata panel record: which judges produced
    verdicts, per-seat wall-clock ms, and the NIM seats' status
    ('ok', 'no-key', 'unavailable', or 'not-run'). v16: the
    "third" record describes the judge_nim1 seat and a "nim2"
    record the judge_nim2 seat; the model named
    is the seat's assigned candidate — the ask's seat events
    record the model that actually served. v17.1: judge_nim1's
    primary candidate rides the OpenRouter pseudo-slot, so
    the third record's provider and key_slot are read off the
    seat's active candidate instead of hardcoded to NIM.
    v17.2: judge_nim2's primary rides the pseudo-slot too, so
    the nim2 record is read off its seat's active candidate
    the same way. v18 (F10): when `served` is given (the ask
    context's served-model map), the records name the model
    that actually SERVED each seat, not the seat's assigned
    candidate."""
    nim1_model, _s1, _f1 = _seat_pick("judge_nim1")
    nim2_model, _s2, _f2 = _seat_pick("judge_nim2")
    if served:
        nim1_model = served.get("judge_nim1", nim1_model)
        nim2_model = served.get("judge_nim2", nim2_model)
    return {"judges": list(judges),
            "primary_ms": ms.get("primary"),
            "second_ms": ms.get("second"),
            "third_ms": ms.get("third"),
            "nim2_ms": ms.get("nim2"),
            "third": {"provider": _slot_provider_name(_s1),
                      "model": nim1_model or THIRD_JUDGE_MODEL,
                      "key_slot": _s1,
                      "status": third_status,
                      "error": third_error},
            "nim2": {"provider": _slot_provider_name(_s2),
                     "model": nim2_model,
                     "key_slot": _s2,
                     "status": nim2_status,
                     "error": nim2_error},
            "third_calls_today": _third_calls_today(),
            "cached_claims": ms.get("cached_claims", 0),
            "refuted_claims": ms.get("refuted_claims", 0)}


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
    if model == ROUTED_MODEL:
        # v14.1: the local judge gets the GPU to itself; any other
        # resident chat model (the 9B drafter above all) is unloaded
        # first, on every judge path, not just local-drafted asks.
        _unload_others(model)
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


def _gemini_text(api_model, system, user):
    """One Gemini call for judge/arbitration work (temperature 0),
    through the same spacing + retry discipline as answers."""
    payload = json.dumps({
        "contents": [{"parts": [{"text": system + "\n\n" + user}]}],
        "generationConfig": {"temperature": 0},
    }).encode()
    return _gemini_call(api_model, payload)


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


# v15 (regression fix, James's manual test 2026-10-13): claims ABOUT
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


def _primary_judgment(engine, question, hits, answer, mech_block):
    """Extract the draft's claims AND verdict them in one call.
    engine is 'lite' (Gemini) or 'local' (the 14B). Returns
    {available, claims: [{text, load_bearing, verdict, note}], error}.
    Only substantive factual claims are extracted: rules, figures,
    conclusions. Connective text, citations, restatements of the
    question, and the confidence line are not claims."""
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
    try:
        if engine == "lite":
            text = _gemini_text(GEMINI_CHAIN[0], _JUDGE_SYSTEM, user)
        elif engine == "flashmid":
            # v20.1 (fix 2): seat A's rationed Gemini 3.8
            # Flash mid-ladder rung (slot 6, Layer 1 rules),
            # tried between the free Lite path and the paid
            # Lite-via-OpenRouter attempt. Counted at attempt
            # time against the shared mid-ladder ration,
            # exactly as the judge seats' slot-6 dispatch
            # counts it; the caller gates on
            # _flash_mid_available(), and the rung refuses
            # here too rather than overspend the ration.
            # Served by _gemini_text like every Flash call;
            # the caller records the serving model, so the
            # rung never impersonates the Lite primary.
            if not _flash_mid_available():
                raise RuntimeError(
                    "flash mid-ladder ration spent")
            _flash_mid_note()
            text = _gemini_text(FLASH_MODEL, _JUDGE_SYSTEM, user)
        elif engine == "orlite":
            # v20: seat A's paid fallover — the same Lite
            # model via OpenRouter, tried ahead of the
            # NIM-seat extractor so a Gemini outage degrades
            # the transport before it changes the judge.
            text = _or_text(OR_LITE_MODEL, _JUDGE_SYSTEM, user,
                            temperature=0, max_tokens=4000,
                            timeout=60)
        elif engine == "nim":
            # v15: the NIM seat extracts when Lite is down (the
            # local 14B is the extractor of last resort).
            text = _third_judge_text(_JUDGE_SYSTEM, user)
        else:
            text = _ollama_chat_raw(ROUTED_MODEL, _JUDGE_SYSTEM, user)
        obj = _parse_judge_json(text)
        if not obj or not isinstance(obj.get("claims"), list):
            return {"available": False, "claims": [],
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
            return {"available": False, "claims": [],
                    "error": "judge extracted no claims"}
        return {"available": True, "claims": claims, "error": None}
    except Exception as e:
        return {"available": False, "claims": [],
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


def _second_verdicts(engine, question, hits, claims, mech_block):
    """The second judge verdicts the SAME numbered claim list, and
    may flag substantive claims in the draft the extractor missed
    (returned as extra claims, which then go through disagreement
    resolution like any other claim). v14.1: when the primary
    judgment mapped claims to their excerpts, the prompt carries
    per-claim scoped evidence instead of the whole set."""
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
            text = _gemini_text(GEMINI_CHAIN[0], _JUDGE_SYSTEM, user)
        elif engine == "nim":
            # v15: the NIM seat as second judge (verdicts only in
            # this seat's other role; the missed-claims pass is
            # part of this function's contract for every engine).
            text = _third_judge_text(_JUDGE_SYSTEM, user)
        elif engine == "nim2":
            # v16: the second NIM judge seat (key slot 3).
            text = _nim2_judge_text(_JUDGE_SYSTEM, user)
        else:
            _note_local_load("fallback-judge")
            text = _ollama_chat_raw(ROUTED_MODEL, _JUDGE_SYSTEM, user,
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


def _flash_arbitrate(question, hits, claims, indices):
    """Flash rules on the disputed claims (one call for all of them).
    Returns {index: verdict} or None when Flash is unavailable or
    the daily arbitration budget is spent (callers then fail the
    disputed claims closed). v18 (R5): the prompt shows Flash
    EVERY verdict on record — both seats, the local seat, and
    the refutation check — labeled with the model that actually
    SERVED each seat (through v17.3 it printed only lite/local/
    third, read two note fields that were never assigned, and
    labeled the seat with stale constants: nim2 verdicts and
    refute_verdict were invisible to the arbitrator, whose
    ruling is final). The unscoped path now carries the
    refutation material too, via _claim_evidence."""
    if not indices or not _flash_spend():
        return None
    subset = [claims[i] for i in indices]
    scoped = _scoped_evidence(hits, subset,
                              labels=[i + 1 for i in indices])
    served = dict(_ask_ctx().served_models)
    labels = {"lite": GEMINI_CHAIN[0] + " (primary judge)",
              "nim": (served.get("judge_nim1")
                      or "judge seat 1"),
              "nim2": (served.get("judge_nim2")
                       or "judge seat 2"),
              "local": ROUTED_MODEL + " (local judge)"}
    panel_split = any(
        len({claims[i].get(k) for k in ("lite", "nim", "nim2",
                                        "local")
             if claims[i].get(k)}) > 1 for i in indices)
    who = "The judges disagree" if panel_split \
        else "The panel's verdicts and the refutation check conflict"
    lines = []
    for i in indices:
        c = claims[i]
        parts = [f"{i + 1}. {c['text']}"]
        for eng in ("lite", "nim", "nim2", "local"):
            if c.get(eng):
                parts.append(f"   {labels[eng]}: {c[eng]}")
        if c.get("refute_verdict"):
            parts.append(
                f"   Refutation check: {c['refute_verdict']}")
        if c.get("notes"):
            parts.append(f"   Judge note: {c['notes'][0]}")
        parts.append(
            f"   Evidence:\n{_claim_evidence(hits, c)}")
        lines.append("\n".join(parts))
    if scoped is not None:
        user = (
            f"Question: {question}\n\n"
            f"{who} on these claims. Rule on EACH "
            "claim from the evidence shown with it alone: "
            "supported, contradicted, or not_addressed. Your "
            "ruling is final. Reply with ONLY this JSON:\n"
            '{"rulings": [{"i": 1, "verdict": "supported"}]}\n\n'
            + "\n".join(lines))
    else:
        user = (
            f"Question: {question}\n\nDocument excerpts:\n"
            f"{_judge_excerpts(hits)}\n\n"
            f"{who} on these claims. Rule on EACH claim from "
            "the excerpts and the evidence shown with it alone: "
            "supported, contradicted, or not_addressed. Your "
            "ruling is final. Reply with ONLY this "
            "JSON:\n"
            '{"rulings": [{"i": 1, "verdict": "supported"}]}\n\n'
            + "\n".join(lines))
    try:
        text = _gemini_text(FLASH_MODEL, _JUDGE_SYSTEM, user)
        obj = _parse_judge_json(text)
        if not obj or not isinstance(obj.get("rulings"), list):
            return None
        out = {}
        for r in obj["rulings"]:
            try:
                idx = int(r.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if idx in indices:
                out[idx] = _norm_verdict(r.get("verdict"))
        return out or None
    except Exception:
        return None


def _worse_verdict(a, b):
    if "contradicted" in (a, b):
        return "contradicted"
    if "not_addressed" in (a, b):
        return "not_addressed"
    return "supported"


def _majority_final(verdicts):
    """v14.2: resolve ONE claim from THREE judges' verdicts.
    Majority rules, fail-closed:
    - 2+ contradicted -> contradicted (the claim fails).
    - exactly 1 contradicted and no majority (2+) supporting ->
      contradicted (the claim fails: an unrebutted contradiction
      never ships).
    - 2+ supported -> supported (the claim passes; a lone
      not_addressed or a lone contradicted outvoted 2-to-1 does
      not sink it).
    - otherwise (fewer than 2 supports, no contradiction) ->
      not_addressed (the claim is unverified: redraft fodder).
    - one of EACH verdict (a genuine 3-way split) -> None: the
      caller routes it to Flash arbitration under the usual
      conditions, else fails it closed with the worst verdict.
    """
    s = verdicts.count("supported")
    c = verdicts.count("contradicted")
    if s == 1 and c == 1:
        return None  # 1 supported + 1 contradicted + 1 not_addressed
    if c >= 2 or (c == 1 and s < 2):
        return "contradicted"
    if s >= 2:
        return "supported"
    return "not_addressed"


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
    r"will not|is the|are the)\b)", re.IGNORECASE)


def _parse_citations(answer, hits):
    """Read a draft's citations back (item 3). Returns
    {sentences: [{text, sources, cited, factual}], cited_claims:
    [{text, load_bearing, sources}], uncited_lb: [sentence texts]}.
    A sentence is cited when it ends with [n]/[registry] markers
    or carries a markdown link to one of the hits' URLs.
    Load-bearing heuristic for cited/uncited classification: the
    sentence states a figure, a duration, or a rule (the
    _FACTUAL_RX signals) — connective prose is neither cited
    nor factual and passes through untouched."""
    urls = {}
    for j, h in enumerate(hits or ()):
        if h.get("url"):
            # v18 (F20b): a URL maps to ALL chunks retrieved
            # from it (search keeps several per document);
            # through v17.3 the last chunk silently won.
            urls.setdefault(h["url"], []).append(j)
    sentences, cited_claims, uncited_lb = [], [], []
    for s in _split_sentences(answer):
        if s.lower().startswith("confidence:"):
            sentences.append({"text": s, "sources": [],
                              "cited": True, "factual": False})
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
        sentences.append({"text": s, "sources": srcs,
                          "cited": cited, "factual": factual})
        if factual and cited:
            cited_claims.append({"text": s, "load_bearing": True,
                                 "sources": srcs})
        elif factual and not cited:
            uncited_lb.append(s)
    return {"sentences": sentences, "cited_claims": cited_claims,
            "uncited_lb": uncited_lb}


# --- v17.3: strip, don't sink (James's ruling, 2026-10-14) --------------------
# In the v17.2 acceptance re-run every extracted claim passed
# every judge, yet the answer failed closed to IDK because ONE
# factual sentence in the final draft carried no citation: a
# parenthetical aside. James's ruling: after a draft's repair
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


def _strip_uncited_asides(draft, parsed, claims, lead_sentence=None):
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
    abandoned and the draft fails closed as before."""
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


# --- v15: refutation retrieval (item 1, parallel per S1) ----------------------
def _refutation_queries(claim, mechanisms):
    """Search queries designed to DISPROVE one claim: its own
    figures and key terms restated as a hunt for those numbers
    in other rules, plus the matched mechanisms' forbidden
    substitutes and confusables where the registry names them."""
    text = claim.get("text") or ""
    queries = []
    figs = re.findall(r"\$\s?[\d,]+(?:\.\d+)?", text)
    terms = [w for w in re.findall(r"[a-z]{4,}", text.lower())
             if w not in STOP][:6]
    if figs and terms:
        queries.append(" ".join(terms[:4] + figs[:1]))
    if terms:
        queries.append(" ".join(terms[:6]))
    for m in mechanisms or ():
        for bad in m.get("forbidden", ()):
            queries.append(bad)
        period = m.get("period") or {}
        for conf in period.get("confusables", ()):
            queries.append(conf)
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:3]


def _refutation_retrieve(corp, hits, claims, mechanisms):
    """For each LOAD-BEARING claim, run its refutation queries
    against the corpus and attach any NEW excerpt (not already
    in the ask's set) that bears on the claim as a refutation
    candidate (claim['refutations'], capped at 2). Retrieval is
    keyword search (fast, local); judges rule on the candidates,
    code never auto-fails a claim on one. Returns total ms."""
    t0 = time.monotonic()
    if corp is None:
        return 0
    seen_keys = {(h.get("url"), (h.get("excerpt") or "")[:60])
                 for h in hits or ()}
    for claim in claims:
        if not claim.get("load_bearing"):
            continue
        found = []
        for q in _refutation_queries(claim, mechanisms):
            try:
                res = search(q, corpus=corp, n=4)
            except Exception:
                res = []
            for h in res:
                key = (h.get("url"), (h.get("excerpt") or "")[:60])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                found.append(h)
                if len(found) >= 2:
                    break
            if len(found) >= 2:
                break
        if found:
            claim["refutations"] = found
    return int((time.monotonic() - t0) * 1000)


def _hunt_evidence(corp, hits, claim, mechanisms):
    """v15 (item 1, regression fix b): a claim judged
    not_addressed gets ONE targeted evidence hunt before it is
    abandoned — the failure mode where the right DOCUMENT was
    retrieved but the chunk carrying the specific passage was
    not (James's manual test, 2026-10-13: the Spousal
    Impoverishment document was in the sources without its
    name-on-the-check passage). Queries come from the claim
    itself plus the matched mechanisms' own retry searches.
    Returns up to 2 NEW excerpts (other chunks of a retrieved
    document count as new)."""
    if corp is None:
        return []
    seen_keys = {(h.get("url"), (h.get("excerpt") or "")[:60])
                 for h in hits or ()}
    queries = _refutation_queries(claim, ())
    for m in mechanisms or ():
        if m.get("retry_query"):
            queries.append(m["retry_query"])
    found = []
    for q in queries[:4]:
        try:
            res = search(q, corpus=corp, n=5)
        except Exception:
            res = []
        for h in res:
            key = (h.get("url"), (h.get("excerpt") or "")[:60])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append(h)
            if len(found) >= 2:
                return found
    return found


# --- v15: claim-verdict cache (speedup S2) ------------------------------------
# A resolved verdict is reusable when the CLAIM and the EVIDENCE
# it rests on are both unchanged: keyed by (normalized claim
# text, hash of the claim's scoped evidence). Only trustworthy
# resolutions are written (finals decided by two agreeing
# judges, a majority, or a Flash ruling — never a lone
# fail-closed guess). A cached claim whose parallel refutation
# returns new material loses the cached verdict at the join
# (its evidence changed, so the key no longer describes it).
_VERDICT_CACHE = {}
_VERDICT_CACHE_MAX = 500
_VERDICT_LOCK = threading.Lock()


def _norm_claim_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:300]


def _registry_block_hash():
    """A fingerprint of the verified-figures registry in force,
    so a registry edit invalidates verdicts cached under the
    old facts (F12 defect a)."""
    try:
        raw = json.dumps(
            [[e.get("id"), e.get("statement"), e.get("figures"),
              e.get("conflict_numbers")] for e in VERIFIED_FIGURES],
            sort_keys=True, default=str)
    except Exception:
        raw = ""
    except Exception:
        raw = ""
    return hashlib.sha256(
        raw.encode("utf-8", "replace")).hexdigest()[:12]


_REGISTRY_HASH = _registry_block_hash()


def _verdict_cache_key(question, hits, claim, mech_block=""):
    """v18 (F12, pass 2 item A1): THE cache key, one function
    used for both the get and the put (through v17.3 the two
    sides were computed at different pipeline points over
    different claim states). The key binds the claim text, the
    QUESTION, the mechanism block in force, the registry
    fingerprint, and the scoped evidence. Refutations are
    deliberately NOT in the key: a refutation hit invalidates
    the cached verdict at the join instead."""
    parts = ["Q|" + _norm_claim_text(question),
             "M|" + hashlib.sha256(
                 (mech_block or "").encode("utf-8", "replace")
                 ).hexdigest()[:12],
             "R|" + _REGISTRY_HASH]
    src = [j for j in (claim.get("sources") or [])
           if 0 <= j < len(hits or ())]
    use = src if src else list(range(len(hits or ())))
    for j in use:
        h = hits[j]
        parts.append((h.get("url") or "") + "|" +
                     (h.get("excerpt") or "")[:200])
    return (_norm_claim_text(claim.get("text")),
            hashlib.sha256(
                "\n".join(parts).encode("utf-8", "replace")
            ).hexdigest()[:24])


def _verdict_cache_get(key):
    with _VERDICT_LOCK:
        return _VERDICT_CACHE.get(key)


def _verdict_cache_put(key, value):
    with _VERDICT_LOCK:
        if len(_VERDICT_CACHE) >= _VERDICT_CACHE_MAX:
            _VERDICT_CACHE.pop(next(iter(_VERDICT_CACHE)))
        _VERDICT_CACHE[key] = value


def _panel_state(judges, served):
    """v18 (§C, James's ruling 2026-10-14): the round's panel
    state. FULL only when all three seats served (Lite,
    judge_nim1, judge_nim2), each by its PRIMARY (non-failover)
    candidate, from three distinct families, with no local seat
    on the panel and no seat missing. Anything less (a seat
    served by a failover candidate, a seat missing or
    unavailable, a local fallback seat, or fewer than three
    distinct families among the seats that served) is DEGRADED.
    A top-class badge earned on a degraded panel is renamed
    (verified_reduced / partial_reduced) where badges are
    assigned in _run_unit, so the degradation is visible in the
    badge itself and not only in the panel annotation. The
    lite seat counts as primary-served only when
    GEMINI_CHAIN[0] itself served: its same-model OpenRouter
    transport (the "orlite" fallover) leaves no served record
    and keeps the seat's primary standing, but when the
    rationed Flash mid-ladder rung serves the extraction
    (v20.1 fix 2) the serving model is recorded in
    served["lite"], and the comparison below then degrades
    the panel — the rung is a google voice, never a stand-in
    for the Lite primary."""
    if "local" in (judges or ()):
        return "degraded"
    models = {}
    if "lite" in judges:
        models["lite"] = (served or {}).get("lite") \
            or GEMINI_CHAIN[0]
    if "nim" in judges:
        models["nim"] = (served or {}).get("judge_nim1")
    if "nim2" in judges:
        models["nim2"] = (served or {}).get("judge_nim2")
    if len(models) < 3 or any(m is None for m in models.values()):
        return "degraded"
    primaries = {"lite": GEMINI_CHAIN[0],
                 "nim": SEATS["judge_nim1"]["candidates"][0][0],
                 "nim2": SEATS["judge_nim2"]["candidates"][0][0]}
    if any(models[k] != primaries[k] for k in models):
        return "degraded"
    if len({_model_family(m) for m in models.values()}) < 3:
        return "degraded"
    return "full"


def _judge_round(question, hits, answer, hard, mech_block="",
                 corp=None, mechanisms=None, extra_claims=None,
                 local_only=False, use_cache=True, deadline=None):
    """One full judging round over a draft. v16 composition: the
    PRIMARY judge extracts claims and verdicts them (Gemini Lite;
    the judge_nim1 seat when Lite is down; a local model only
    when both remotes are down). TWO further verdict seats run
    concurrently with refutation retrieval (S1): the second
    seat (judge_nim1 by default; Lite when nim1 extracted) and
    the judge_nim2 seat on its own key, a different family.
    A single failed remote seat composes from the survivors;
    no local seat ever joins a remote-primary round. Claims
    whose refutation surfaced new material are re-verdicted
    against the union evidence, and a re-verdict that does not
    affirm them sends them to the disputed pool. Cached claims
    (S2) resolve from the verdict cache unless the join's
    refutation invalidates them. Disputes go to Flash
    (load-bearing, or any claim on hard questions); v16 REMOVES
    the 14B tiebreak (James's ruling): whatever Flash does not
    rule on fails closed on the worst verdict. Returns
    {mode, claims, flash_used, pass, errors, panel, refute_ms}
    where each claim carries lite/nim/nim2/local/flash/final
    verdicts (plus the v14.2-era 'third' alias of nim). mode is
    'panel' (2+ remote seats), 'dual', 'arbitrated',
    'gemini-only', 'nim-only', 'local-only', or 'none'."""
    errors = []
    panel_ms = {"primary": None, "second": None, "third": None,
                "nim2": None,
                "cached_claims": 0, "refuted_claims": 0,
                "repaired_claims": 0}
    nim_status = "ok" if _third_judge_key() else "no-key"
    nim_error = None
    # v17.2: judge_nim2's primary rides the OpenRouter
    # pseudo-slot; the seat counts as keyed while any of its
    # candidates is pickable, not only while slot 3 has a key.
    nim2_status = "ok" if _seat_pick("judge_nim2")[0] \
        is not None else "no-key"
    nim2_error = None

    # --- Stage 1: primary extraction + verdicts. Lite first,
    # then the NIM seat, then (both remotes down) the demoted
    # local 14B. The local GPU warm-up runs alongside ONLY when
    # a local seat is likely to be needed, which v15 knows only
    # after a remote failure — so there is no speculative warm-up
    # on the default path (that is the demotion's latency win).
    pt0 = time.monotonic()
    if local_only:
        # v18 (F19 widened): a "Run locally" ask is judged by
        # the local model only; no hosted seat is consulted at
        # any stage of the round (in v17.3 the Lite primary and
        # hosted utilities ran regardless of the selection).
        _note_local_load("local-ask-judge")
        primary = _primary_judgment("local", question, hits,
                                    answer, mech_block)
        primary_engine = "local"
    else:
        primary = _primary_judgment("lite", question, hits,
                                    answer, mech_block)
        primary_engine = "lite"
    if not primary["available"] and not local_only:
        errors.append("lite judge: " + str(primary["error"]))
        # v20.1 (fix 2, James's approval 2026-10-14): seat A
        # gains the rationed Gemini 3.8 Flash mid-ladder
        # rung (slot 6, Layer 1 rules) between the free Lite
        # path and the paid Lite-via-OpenRouter attempt —
        # the same shared ration as seats B and C
        # (flash_mid_usage.json, FLASH_MID_DAILY_CAP/day,
        # counted at attempt time). A spent ration skips
        # the rung silently, exactly as _slot_has_key(6)
        # skips a rationed seat candidate. As-served: the
        # extraction verdicts keep the round's "lite" voice
        # slot, but the serving model is recorded in
        # served_models["lite"], and _panel_state counts a
        # Flash-served seat A as DEGRADED — the rung never
        # impersonates the Lite primary for badges.
        if _flash_mid_available():
            _flash_primary = _primary_judgment(
                "flashmid", question, hits, answer, mech_block)
            if _flash_primary["available"]:
                primary = _flash_primary
                primary_engine = "lite"
                _ask_ctx().served_models["lite"] = FLASH_MODEL
            else:
                errors.append("lite (flash rung) judge: "
                              + str(_flash_primary["error"]))
        if not primary["available"]:
            # v20: seat A's paid fallover — Lite via
            # OpenRouter (the same model, the same "lite"
            # voice) is tried before the extractor changes
            # family to the NIM seat.
            _or_primary = _primary_judgment(
                "orlite", question, hits, answer, mech_block)
            if _or_primary["available"]:
                primary = _or_primary
                primary_engine = "lite"
            else:
                errors.append("lite (openrouter) judge: "
                              + str(_or_primary["error"]))
                primary = _primary_judgment(
                    "nim", question, hits, answer, mech_block)
                primary_engine = "nim"
        if not primary["available"]:
            errors.append("nim judge: " + str(primary["error"]))
            nim_status = "unavailable"
            nim_error = str(primary["error"])
            if LOCAL_FALLBACK_JUDGE:
                # Both remote extraction paths are down: the one
                # sanctioned local judging load (item 5); the
                # model loads cold, on demand (v15.1 removed the
                # speculative warm-up).
                _note_local_load("fallback-judge")
                primary = _primary_judgment("local", question, hits,
                                            answer, mech_block)
                primary_engine = "local"
    panel_ms["primary"] = int((time.monotonic() - pt0) * 1000)
    if not primary["available"]:
        if primary_engine == "local":
            errors.append("local judge: " + str(primary["error"]))
        return {"mode": "none", "claims": [], "flash_used": False,
                "pass": False, "errors": errors, "refute_ms": 0,
                # no panel sat at all: degraded by definition
                "panel_state": "degraded",
                "panel": _panel_info([], panel_ms, nim_status,
                                     nim_error)}
    claims = [{"text": c["text"], "load_bearing": c["load_bearing"],
               "lite": None, "nim": None, "nim2": None, "third": None,
               "local": None, "flash": None, "final": None,
               "notes": [], "origin": "extracted",
               "meta": _is_meta_claim(c["text"]),
               "sources": list(c.get("sources") or []),
               "refutations": []}
              for c in primary["claims"]]
    for i, c in enumerate(primary["claims"]):
        claims[i][primary_engine] = c["verdict"]
        if c["note"]:
            claims[i]["notes"].append(c["note"])
    # Extra claims (v15): citation-derived claims the extractor
    # did not list, divergence completeness claims, and conflict
    # claims. They carry no primary verdict; the verdict stage
    # and the arbitrator rule on them like any other claim.
    seen_norm = {_norm_claim_text(c["text"]) for c in claims}
    for ec in extra_claims or ():
        if _norm_claim_text(ec["text"]) in seen_norm:
            continue
        seen_norm.add(_norm_claim_text(ec["text"]))
        claims.append({"text": ec["text"],
                       "load_bearing": bool(ec.get("load_bearing")),
                       "lite": None, "nim": None, "nim2": None, "third": None,
                       "local": None, "flash": None, "final": None,
                       "notes": [], "origin": ec.get("origin", "extra"),
                       "meta": _is_meta_claim(ec["text"]),
                       "sources": list(ec.get("sources") or []),
                       "refutations": []})
    # --- S2: verdict cache. Resolved claims (identical claim +
    # identical scoped evidence) resolve without new judge
    # calls; the join below can still invalidate a cached claim
    # whose refutation returns new material. v18 (F12): the
    # key is computed ONCE per claim here and reused for the
    # put; a claim the fresh primary just contradicted never
    # resolves from the cache; a fresh/nocache ask skips the
    # cache entirely.
    def _claim_vkey(c):
        if "_vkey" not in c:
            c["_vkey"] = _verdict_cache_key(question, hits, c,
                                            mech_block)
        return c["_vkey"]

    cached_idx = set()
    if use_cache:
        for i, c in enumerate(claims):
            _claim_vkey(c)
            if c.get(primary_engine) == "contradicted":
                continue
            hit_v = _verdict_cache_get(c["_vkey"])
            if hit_v:
                c["cached"] = hit_v
                cached_idx.add(i)
    panel_ms["cached_claims"] = len(cached_idx)
    live_idx = [i for i in range(len(claims)) if i not in cached_idx]
    # --- Stage 2 (S1): the second judge's verdicts and the
    # refutation retrieval run CONCURRENTLY; local fallback
    # seats compose per the demotion rules.
    if primary_engine == "lite":
        second_engine = "nim"
    elif primary_engine == "nim":
        second_engine = "lite"
    else:
        second_engine = None  # local extracted: no second remote
    second = {"available": False, "verdicts": {}, "missed": [],
              "error": "not run"}
    refute_ms = 0

    def _run_refutation():
        # v18 (F12 defect c): cached load-bearing claims are
        # refutation-checked too. Through v17.3 only live claims
        # were passed, so a cached claim could never gain
        # refutations and the join's invalidation branch below
        # was dead code.
        return _refutation_retrieve(
            corp, hits,
            [c for c in claims if c["load_bearing"]],
            mechanisms)

    st0 = time.monotonic()
    nim2 = {"available": False, "verdicts": {}, "missed": [],
            "error": "not run"}
    # v16: the judge_nim2 seat verdicts alongside the second
    # seat whenever the primary is remote. A local-primary round
    # (both remotes down) stays local-only, as in v15.
    # v19 (§G5): the fast lane. A SIMPLE question's round runs
    # the primary plus the second seat only; the third seat
    # sits out (two distinct families can still earn a full
    # verified badge under §C coverage). Hard questions keep
    # the full three-seat panel, unchanged.
    run_nim2 = primary_engine != "local" and hard
    # v18 (§C): plan the two stage-2 verdict seats ATOMICALLY
    # before dispatch (distinct families; the drafting family
    # excluded). A local-only round plans nothing: no remote
    # seat runs at all (F19).
    _jr_ctx = _ask_ctx()
    _jr_ctx.panel_plan = {}
    if not local_only:
        _plan_panel()
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_second = _submit(pool, _second_verdicts, second_engine,
                             question, hits,
                             [claims[i] for i in live_idx],
                             mech_block) if second_engine \
            else None
        fut_nim2 = _submit(pool, _second_verdicts, "nim2",
                           question, hits,
                           [claims[i] for i in live_idx],
                           mech_block) if run_nim2 else None
        fut_refute = _submit(pool, _run_refutation)
        if fut_second is not None:
            second = fut_second.result()
        if fut_nim2 is not None:
            nim2 = fut_nim2.result()
        refute_ms = fut_refute.result()
    panel_ms["second"] = int((time.monotonic() - st0) * 1000)
    if run_nim2:
        panel_ms["nim2"] = panel_ms["second"]
    judges = [primary_engine]

    def _absorb(res, engine):
        for sub_idx, v in res["verdicts"].items():
            claims[live_idx[sub_idx]][engine] = v["verdict"]
            if v["note"]:
                claims[live_idx[sub_idx]]["notes"].append(v["note"])
        known = {_norm_claim_text(c["text"]) for c in claims}
        for m in res["missed"]:
            if _norm_claim_text(m["text"]) in known:
                continue
            known.add(_norm_claim_text(m["text"]))
            claims.append({"text": m["text"],
                           "load_bearing": m["load_bearing"],
                           "lite": None, "nim": None,
                           "nim2": None, "third": None,
                           "local": None, "flash": None,
                           "final": None,
                           "notes": [m["note"]] if m["note"] else [],
                           "origin": "missed",
                           "meta": _is_meta_claim(m["text"]),
                           "sources": [], "refutations": []})
            claims[-1][engine] = m["verdict"]
            live_idx.append(len(claims) - 1)

    if second["available"]:
        judges.append(second_engine)
        _absorb(second, second_engine)
    elif second_engine:
        errors.append(second_engine + " judge: "
                      + str(second["error"]))
        if second_engine == "nim":
            nim_status = "unavailable"
            nim_error = str(second["error"])
    if nim2["available"]:
        judges.append("nim2")
        _absorb(nim2, "nim2")
    elif run_nim2:
        errors.append("nim2 judge: " + str(nim2["error"]))
        nim2_status = "unavailable"
        nim2_error = str(nim2["error"])
    if primary_engine == "nim":
        panel_ms["third"] = panel_ms["primary"]
    elif second_engine == "nim" and second["available"]:
        panel_ms["third"] = panel_ms["second"]
    # --- v19 (§G1): completeness repair. A verdict seat's
    # batched reply can parse cleanly yet silently OMIT claims
    # (the seat counts as available; the omitted claims end up
    # covered by one family only). On the 2026-10-14 SFC ask
    # both stage-2 seats omitted the same 3 load-bearing
    # claims, and no path ever re-asked them. Each seat that
    # ran now gets ONE follow-up call naming only the claims
    # it skipped; a claim the seat already verdicted is never
    # re-asked, and the repair is skipped when the round's
    # deadline is nearly spent.
    if not local_only:
        for _eng in dict.fromkeys(
                ([second_engine] if second["available"] else [])
                + (["nim2"] if nim2["available"] else [])):
            _missing = [i for i in live_idx
                        if claims[i].get(_eng) is None
                        and not claims[i].get("meta")]
            if not _missing:
                continue
            if deadline is not None \
                    and deadline - time.monotonic() < 45:
                break
            _rv = _second_verdicts(
                _eng, question, hits,
                [claims[i] for i in _missing], mech_block)
            if _rv["available"]:
                for _sub, _v in _rv["verdicts"].items():
                    _ci = _missing[_sub]
                    claims[_ci][_eng] = _v["verdict"]
                    if _v["note"]:
                        claims[_ci]["notes"].append(
                            "completeness repair: " + _v["note"])
                panel_ms["repaired_claims"] += len(_rv["verdicts"])
    # --- v15.1 (James's ruling, 2026-10-13): NO local second
    # seat. Through v15.0 a failed remote second seat was filled
    # by the local 14B, which put the 14B on the panel of
    # ordinary remote asks. The 14B now judges ONLY in
    # local-only degradation (both remote seats down; stage 1
    # above) or on an explicit local pick. A failed second seat
    # degrades the round to the surviving remote judge alone
    # (mode gemini-only / nim-only, the *_single badges); the
    # seat's error is already recorded above.
    # --- The join (S1): cached claims whose refutation returned
    # NEW material lose their cached verdict (their evidence
    # changed); every claim carrying refutation candidates is
    # re-verdicted against the union evidence by the second
    # seat. A re-verdict that does not affirm the claim sends
    # it to the disputed pool below.
    refuted = [i for i in range(len(claims))
               if claims[i].get("refutations")]
    panel_ms["refuted_claims"] = len(refuted)
    for i in refuted:
        if i in cached_idx:
            cached_idx.discard(i)
            claims[i].pop("cached", None)
            panel_ms["cached_claims"] -= 1
            if i not in live_idx:
                live_idx.append(i)
    rejudge_engine = second_engine if second["available"] else (
        "nim2" if nim2["available"] else None)
    if refuted and rejudge_engine:
        subset = [claims[i] for i in refuted]
        rv = _second_verdicts(rejudge_engine, question, hits,
                              subset, mech_block)
        if rv["available"]:
            for sub_idx, v in rv["verdicts"].items():
                ci = refuted[sub_idx]
                claims[ci]["refute_verdict"] = v["verdict"]
                if v["note"]:
                    claims[ci]["notes"].append(
                        "refutation check: " + v["note"])
    # --- Mode + resolution. Code resolves verdicts, never
    # averages (v14's rule, unchanged).
    remote = [j for j in judges if j in ("lite", "nim", "nim2")]
    if len(remote) >= 2:
        mode = "panel"
    elif len(judges) >= 2:
        mode = "dual"
    elif judges == ["lite"]:
        mode = "gemini-only"
    elif judges == ["nim"]:
        mode = "nim-only"
    else:
        mode = "local-only"
    disputed = []
    for i, c in enumerate(claims):
        if i in cached_idx and c.get("cached"):
            _cv = c["cached"]
            c["final"] = _cv["verdict"] if isinstance(_cv, dict) \
                else _cv
            continue
        vs = [c[k] for k in ("lite", "nim", "nim2", "local") if c.get(k)]
        if c.get("refute_verdict") and c["refute_verdict"] != "supported":
            disputed.append(i)
            continue
        if len(vs) >= 3:
            f = _majority_final(vs)
            if f is None:
                disputed.append(i)
            else:
                c["final"] = f
        elif len(vs) == 2:
            if vs[0] == vs[1]:
                c["final"] = vs[0]
            else:
                disputed.append(i)
        elif len(vs) == 1:
            c["final"] = vs[0]
        else:
            c["final"] = "not_addressed"
    for c in claims:  # keep the arbitration evidence's alias fresh
        c["third"] = c.get("nim")
    flash_used = False
    if disputed:
        _set_stage("arbitrating")
        to_rule = [i for i in disputed
                   if claims[i]["load_bearing"] or hard]
        if to_rule:
            # v18 (F19): no Flash in a local-only round; the
            # disputed claims fail closed below instead.
            rulings = None if local_only else _flash_arbitrate(
                question, hits, claims, to_rule)
            if rulings:
                flash_used = True
                mode = "arbitrated"
                for idx, verdict in rulings.items():
                    claims[idx]["flash"] = verdict
                    claims[idx]["final"] = verdict
        # v15.1 (James's ruling, 2026-10-13): NO local
        # tiebreak. Through v15.0, disputes Flash could not
        # rule on went to the local 14B for a tiebreak verdict
        # (the Miller smoke's [lite, nim, local] panel). That
        # seat is removed: a disputed claim with no Flash
        # ruling FAILS CLOSED in the loop below (worst verdict
        # on record, or not_addressed). The local model is
        # never seated on a remote ask.
        for i in disputed:
            if claims[i]["final"] is None:
                vs = [claims[i][k] for k in ("lite", "nim", "nim2", "local")
                      if claims[i].get(k)]
                if claims[i].get("refute_verdict") \
                        and claims[i]["refute_verdict"] != "supported":
                    # v18 (F1, pass 2 item A3): Flash was asked
                    # (this claim is load-bearing or the question
                    # is hard) and did not rule, so the
                    # refutation verdict STANDS as the final.
                    # Through v17.3 it was honored only when no
                    # seat had said "supported"; whenever the
                    # panel had, the refutation silently dropped
                    # out of the worst-of fold and a refuted
                    # claim shipped Verified.
                    claims[i]["final"] = claims[i]["refute_verdict"]
                elif vs:
                    w = vs[0]
                    for v in vs[1:]:
                        w = _worse_verdict(w, v)
                    claims[i]["final"] = w
                else:
                    claims[i]["final"] = "not_addressed"
    # --- Targeted evidence hunt (item 1, regression fix b):
    # claims still not_addressed get one hunt for the specific
    # passage before they are abandoned. Promotion is strict:
    # at least two judges must re-verdict supported against the
    # enlarged evidence; the hunt never demotes.
    if corp is not None \
            and not (deadline is not None
                     and deadline - time.monotonic() < 90):
        # v18 (F18): the hunt is discretionary work; it is
        # skipped when the ask's deadline is nearly spent.
        hunt_idx = [i for i, c in enumerate(claims)
                    if c["final"] == "not_addressed"
                    and not c.get("meta")][:2]
        for i in hunt_idx:
            new = _hunt_evidence(corp, hits, claims[i], mechanisms)
            if not new:
                continue
            claims[i]["hunt"] = new
            verdicts = []
            for eng in dict.fromkeys(
                    [primary_engine] +
                    ([second_engine] if second["available"] else [])
                    + (["nim2"] if nim2["available"] else [])):
                rv = _second_verdicts(eng, question, hits,
                                      [claims[i]], mech_block)
                if rv["available"] and 0 in rv["verdicts"]:
                    claims[i][eng] = rv["verdicts"][0]["verdict"]
                    _mdl = {"lite": GEMINI_CHAIN[0],
                            "local": ROUTED_MODEL}.get(
                        eng, _ask_ctx().served_models.get(
                            "judge_" + eng))
                    verdicts.append(
                        (rv["verdicts"][0]["verdict"],
                         _model_family(_mdl) if _mdl else eng))
                    if rv["verdicts"][0]["note"]:
                        claims[i]["notes"].append(
                            rv["verdicts"][0]["note"])
            # v18 (pass 2 item E): promotion needs two distinct
            # FAMILIES affirming, not two verdicts that may be
            # one model serving two seats.
            _sup_fams = {fam for v, fam in verdicts
                         if v == "supported"}
            if verdicts and all(v == "supported"
                                for v, _f in verdicts) \
                    and len(_sup_fams) >= 2:
                claims[i]["final"] = "supported"
                claims[i]["notes"].append(
                    "targeted retrieval found the passage; "
                    "re-judged supported")
    # --- v18 (§C): per-claim coverage + serving models. A
    # claim's coverage is the set of DISTINCT MODEL FAMILIES
    # among its SUPPORTING verdicts (pass 2 item B: a seat and
    # its own failover are one family and can never make a
    # claim panel-verified; two seats served by one model are
    # one voice). Badges derive from coverage in _run_unit;
    # §E persists both maps per claim. Cached claims carry the
    # coverage recorded when their verdict was stored.
    _served = dict(_ask_ctx().served_models)
    # v20.1 (fix 2): seat A's model attribution is as-served
    # too — FLASH_MODEL when the mid-ladder rung extracted,
    # else the Lite primary (the rung is google-family either
    # way, so coverage families are unchanged).
    _engine_model = {"lite": _served.get("lite")
                     or GEMINI_CHAIN[0],
                     "local": ROUTED_MODEL, "flash": FLASH_MODEL,
                     "nim": _served.get("judge_nim1"),
                     "nim2": _served.get("judge_nim2")}
    for c in claims:
        if isinstance(c.get("cached"), dict):
            c["coverage"] = list(c["cached"].get("coverage") or [])
            c["models"] = dict(c["cached"].get("models") or {})
            continue
        _models, _fams = {}, set()
        for _eng in ("lite", "nim", "nim2", "local", "flash"):
            if c.get(_eng):
                _mdl = _engine_model.get(_eng)
                if _mdl:
                    _models[_eng] = _mdl
                if c[_eng] == "supported":
                    _fams.add(_model_family(_mdl) if _mdl else _eng)
        c["models"] = _models
        c["coverage"] = sorted(_fams)
    # Cache the trustworthy resolutions (S2 + v18 F12): a
    # verdict is stored only when at least two DISTINCT
    # FAMILIES produced verdicts on the claim, or Flash ruled.
    # Through v17.3 the put counted engine fields, so one model
    # serving two seats wrote a "unanimous" entry. Hunt-promoted
    # verdicts are not stored: they were earned on evidence
    # outside the key's scope.
    if use_cache:
        for i, c in enumerate(claims):
            if i in cached_idx or c.get("origin") == "conflict" \
                    or c.get("hunt") or not c.get("final"):
                continue
            _all_fams = {_model_family(mdl) for mdl in
                         (c.get("models") or {}).values()}
            if c.get("flash") or len(_all_fams) >= 2:
                _verdict_cache_put(
                    _claim_vkey(c),
                    {"verdict": c["final"],
                     "coverage": c.get("coverage") or [],
                     "models": c.get("models") or {}})
    # Keep the v14.2-era 'third' alias pointing at the NIM seat.
    for c in claims:
        c["third"] = c.get("nim")
    # Meta claims (about the documents themselves) never sink
    # an answer: the pass is computed over substantive claims.
    substantive = [c for c in claims if not c.get("meta")]
    passed = bool(substantive) and all(
        c["final"] == "supported" for c in substantive)
    return {"mode": mode, "claims": claims, "flash_used": flash_used,
            "pass": passed, "errors": errors, "refute_ms": refute_ms,
            # §C (James's ruling 2026-10-14): the panel state
            # rides the round so _run_unit can rename a top-class
            # badge earned on a degraded panel.
            "panel_state": _panel_state(judges, _served),
            "panel": _panel_info(judges, panel_ms, nim_status,
                                 nim_error, nim2_status, nim2_error,
                                 served=_served)}

# --- v14: the escalation ladder ----------------------------------------------
# Per answering unit (the whole question, or one part of a bundled
# question): draft, then up to TWO redrafts with the judges'
# objections attached (James's decision, 2026-10-13), then a final
# Flash ruling on the last draft's failed claims, then IDK. Code
# gates (figures, mechanisms, arithmetic) run alongside the judges:
# a draft ships only when every gate passes.


def _generate(engine, model, question, hits, instructions):
    """One draft generation with v13's fallback honesty. Returns
    (answer, prompt, served_label, downgraded, fallback)."""
    if engine == "gemini":
        answer, prompt, gserved, dg = gemini_ask(
            model, question, hits, instructions=instructions)
        label = gserved + " (API)"
        if dg:
            label += " (downgraded: best Gemini unavailable)"
        return answer, prompt, label, dg, False
    if engine in ("nim", "nimfast"):
        # v16: the drafting SEATS own model choice (the model
        # argument is the v15-era nominal pick and is ignored):
        # the seat's active candidate drafts; on failure the job
        # fails over to the seat's next measured candidate
        # before this raises, and only a fully-down seat raises
        # (the caller then applies the genuine-unavailability
        # local fallback). Seat health records every call.
        job = "hard_draft" if engine == "nim" else "simple_draft"
        # v20: the drafting chain in strict layer order
        # (James's layer model, 2026-10-14): Layer 0 Groq's
        # free tier (hard: GPT-OSS-120B; simple: GPT-OSS-20B);
        # Layer 2 the v16 NIM bench loop (free, slow — the
        # hard bench leads with Kimi K3 and GLM-5.3 per the
        # chain spec); Layer 3 the paid seats (hard: Grok 4.7
        # on xAI, then GPT-OSS on OpenRouter; simple:
        # GPT-OSS-20B on OpenRouter). A fully-down chain
        # raises into the caller's genuine-unavailability
        # local fallback (Layer 4), unchanged from v16.
        _gt = _leg_timeout(GROQ_DRAFTER_TIMEOUT)
        if _groq_available() and _gt > 0:
            try:
                answer, prompt = groq_ask(
                    GROQ_HARD_MODEL if engine == "nim"
                    else GROQ_SIMPLE_MODEL,
                    question, hits, instructions=instructions,
                    timeout=_gt)
                _note_seat(job, GROQ_HARD_MODEL
                           if engine == "nim"
                           else GROQ_SIMPLE_MODEL, False)
                return (answer, prompt,
                        (GROQ_HARD_MODEL if engine == "nim"
                         else GROQ_SIMPLE_MODEL) + " (Groq API)",
                        False, False)
            except Exception as e:
                print(f"[groq] {job} primary failed: "
                      f"{str(e)[:150]}; the chain falls over",
                      flush=True)
        elif _groq_key() is not None and _groq_guard_tripped():
            _note_groq_shed(job)
        # Layer 2: the NIM bench. The loop walks the seat's
        # candidates as in v16 (seat health records every
        # call); exhausting the bench no longer raises — the
        # paid layer below gets its turn first.
        tried, last_err = [], None
        while True:
            # v20: a leg starts only while the unit's budget
            # has room for it, and runs clamped to what is
            # left (NIM legs are the slow ones: ~60s bound).
            _lt = _leg_timeout(NIM_DRAFTER_TIMEOUT)
            if _lt <= 0:
                break
            smodel, slot, failover = _seat_pick(job, skip=tried)
            if smodel is None:
                break
            key_env, key_file = SEAT_SLOTS[slot]
            t0 = time.monotonic()
            try:
                answer, prompt = nim_ask(
                    smodel, question, hits,
                    instructions=instructions,
                    key_env=key_env, key_file=key_file,
                    timeout=_lt)
                lat = time.monotonic() - t0
                _seat_report(job, smodel, lat, True)
                _note_seat(job, smodel, failover)
                label = smodel + " (NIM API)"
                if failover:
                    label += " (failover seat)"
                return answer, prompt, label, False, False
            except Exception as e:
                lat = time.monotonic() - t0
                _seat_report(job, smodel, lat, False,
                             timed_out=lat >= NIM_DRAFTER_TIMEOUT * 0.9
                             or "timed out" in str(e)
                             or "timeout" in str(e).lower())
                tried.append(smodel)
                last_err = e
                if len(tried) >= len(SEATS[job]["candidates"]):
                    break
        # Layer 3 (paid): xAI Grok 4.7 first on hard
        # questions (reservation-gated like OpenRouter), then
        # the v17 OpenRouter seat (hard: GPT-OSS-120B;
        # simple: GPT-OSS-20B).
        _xt = _leg_timeout(XAI_DRAFTER_TIMEOUT)
        if engine == "nim" and _xai_available() and _xt > 0:
            try:
                answer, prompt = xai_ask(
                    XAI_HARD_MODEL, question, hits,
                    instructions=instructions, timeout=_xt)
                _note_seat(job, XAI_HARD_MODEL, False)
                return (answer, prompt,
                        XAI_HARD_MODEL + " (xAI API)",
                        False, False)
            except Exception as e:
                print(f"[xai] {job} seat failed: "
                      f"{str(e)[:150]}; the chain falls over",
                      flush=True)
        _ot = _leg_timeout(OPENROUTER_DRAFTER_TIMEOUT)
        if _or_available() and _ot > 0:
            or_model = (OPENROUTER_HARD_MODEL if engine == "nim"
                        else OPENROUTER_SIMPLE_MODEL)
            try:
                answer, prompt = or_ask(
                    or_model, question, hits,
                    instructions=instructions, timeout=_ot)
                _note_seat(job, or_model, False)
                return (answer, prompt,
                        or_model + " (OpenRouter API)", False, False)
            except Exception as e:
                print(f"[openrouter] {job} seat failed: "
                      f"{str(e)[:150]}; the chain falls over",
                      flush=True)
        raise RuntimeError(
            f"{job}: every drafting layer down (tried NIM "
            f"bench {tried}): {last_err}")
    if engine == "nimx":
        # v16: an explicit NIM model on the drafting key slot
        # (the divergence oracle's B draft). No seat machinery:
        # the caller chose this model deliberately.
        key_env, key_file = SEAT_SLOTS[2]
        answer, prompt = nim_ask(model, question, hits,
                                 instructions=instructions,
                                 key_env=key_env, key_file=key_file,
                                 timeout=NIM_DRAFTER_TIMEOUT)
        return answer, prompt, model + " (NIM API)", False, False
    try:
        answer, prompt = ollama_ask(model, question, hits,
                                    instructions=instructions)
        return answer, prompt, model, False, False
    except Exception:
        if model == LOCAL_MODEL:
            raise
        _note_local_load("local-model-fallback")
        answer, prompt = ollama_ask(LOCAL_MODEL, question, hits,
                                    instructions=instructions)
        return (answer, prompt,
                LOCAL_MODEL + f" (fallback: routed {model} unavailable)",
                False, True)


def _build_objections(jr, mech_failures, ungrounded, uncited=(),
                      quantities=(), required_points=(), conflicts=()):
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
    for p in required_points or ():
        lines.append("- This point was established by the "
                     "independent second draft and confirmed by "
                     "a second judge model, but the current "
                     "draft omits it: "
                     "\"" + p + "\" — work it in accurately.")
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


def _points_missing(points, draft, question=""):
    """v15 (item 4): which divergence-required points a draft
    fails to cover: every figure in the point must appear, and
    at least half its content words must be present.
    v18 (F14): a point must also pass a RELEVANCE test before
    it can block shipping — it shares at least two content
    words with the question, or carries a figure. Through
    v17.3 any B-draft tangent, however unrelated to what was
    asked, blocked the A draft's ship."""
    low = (draft or "").lower()
    low_flat = low.replace(",", "")
    draft_toks = set(re.findall(r"[a-z]{4,}", low))
    q_toks = {w for w in re.findall(r"[a-z]{4,}",
                                    (question or "").lower())
              if w not in STOP}
    missing = []
    for p in points or ():
        nums = re.findall(r"\d[\d,]*\.?\d*", p)
        toks = {w for w in re.findall(r"[a-z]{4,}", p.lower())
                if w not in STOP}
        if not nums and len(toks & q_toks) < 2:
            continue  # not relevant to what was asked
        num_ok = all(n.replace(",", "") in low_flat for n in nums)
        overlap = (len(toks & draft_toks) / len(toks)) if toks else 1.0
        if not (num_ok and overlap >= 0.5):
            missing.append(p)
    return missing


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


def _diverge_model():
    """The divergence oracle's model (v16 item 4 + James's
    ruling): a NIM flagship from a DIFFERENT family than the
    hard-draft seat's active pick — normally the seat's
    runner-up candidate. Never a local model. v18 (§C):
    "different family" is literal — through v17.3 the oracle
    merely differed in slug and could share the drafting
    family; the ask's drafting family is excluded too."""
    active, _slot, _fo = _seat_pick("hard_draft")
    fams = {_model_family(active)} if active else set()
    if _ask_ctx().draft_family:
        fams.add(_ask_ctx().draft_family)
    for model, _s in SEATS["hard_draft"]["candidates"]:
        if _model_family(model) not in fams:
            return model
    for model, _s in SEATS["hard_draft"]["candidates"]:
        if model != active:
            return model
    return SEATS["hard_draft"]["candidates"][0][0]


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


def _panel_annotation(jr):
    """§C (pass 2 item D): the annotation naming the panel that
    actually SAT, built from the claims' serving-model records:
    the distinct judge models in seat order, plus a count of
    claims covered by one judge family only when any exist.
    Shown by the webapp under the badge; persisted in §E
    records. Empty when no judge served (mode none)."""
    claims = (jr or {}).get("claims") or []
    sat = []
    for eng in ("lite", "nim", "nim2", "local", "flash"):
        for c in claims:
            mdl = (c.get("models") or {}).get(eng)
            if mdl and mdl not in sat:
                sat.append(mdl)
    if not sat:
        return ""
    text = "Judges: " + " + ".join(sat)
    single = sum(1 for c in claims if not c.get("meta")
                 and len(c.get("coverage") or []) < 2)
    if single:
        text += (f" — {single} claim(s) checked by one judge "
                 "family only")
    return text


def _run_unit(ctx):
    """Run the ladder for one answering unit. ctx keys: question
    (full question, for figure provenance), unit_q (what this unit
    answers), hits, engine ('local'|'gemini'), draft_model,
    instructions (base prompt additions), mechanisms, allowed_extra
    (figure-checker allowed values), csra_comp, hard (bool).
    v15 ctx additions: corp (for refutation/hunt retrieval),
    deadline (monotonic cap), diverge (item 4 eligibility),
    explicit_model (an API caller's own model pick, never
    overridden by NIM drafting).
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
    # v20 (James's acceptance criterion, 2026-10-14): the hard
    # budget drops to 285s — an ask unit must return in under
    # 5 minutes and accurate; 285 leaves margin under the
    # 300s wall, and the ask-level 600s cap stays as the
    # outer safety net. The deadline is published on the ask
    # context so chain legs clamp to it (_leg_timeout).
    budget_s = ASK_TIME_CAP_S
    if deadline is not None:
        budget_s = 285 if hard else 150
        deadline = min(deadline, time.monotonic() + budget_s)
        _ask_ctx().unit_deadline = deadline
    # v18 (F13): judging (and its verdict-cache keys, the
    # arbitration, and the divergence check) sees the FULL
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
    diverge = bool(ctx.get("diverge")) and ctx["engine"] == "local" \
        and not ctx.get("explicit_model") and hard
    ev0 = len(_events())
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
    cost_capped = False
    conflict_fail = False
    judged_draft = None       # last draft that went through judging
    required_points = []      # item 4: confirmed B-only points
    missing_points = []
    divergence = None

    def _draft_choice(rung):
        """(engine, model) for one generation attempt. v15: the
        local path drafts on NIM (hard: kimi-k3; simple: the fast
        tier; redrafts: kimi-k3) unless the caller explicitly
        picked a model. Gemini units are unchanged."""
        if ctx["engine"] == "gemini" or ctx.get("explicit_model"):
            if rung > 0 and ctx["engine"] == "local":
                return "local", ROUTED_MODEL  # v14 redraft rule
            return ctx["engine"], ctx["draft_model"]
        if rung == 0 and not hard:
            return "nimfast", DRAFTER_SIMPLE_MODEL
        return "nim", DRAFTER_MODEL

    def _try_generate(engine, model, instr):
        """Generate with the fallback chains. v16: _generate
        already failed over through every candidate in the NIM
        seat, so reaching the local branch means the remote
        drafting path is genuinely unavailable (item 5's one
        sanctioned drafting load); the load is recorded."""
        try:
            return _generate(engine, model, ctx["unit_q"],
                             ctx["hits"], instr)
        except Exception:
            if engine in ("nim", "nimfast"):
                _note_local_load("fallback-draft")
                if engine == "nim" or hard:
                    _unload_others(ROUTED_MODEL)
                    return _generate("local", ROUTED_MODEL,
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

    for rung in range(3):  # draft + two redrafts (James's decision)
        if deadline and time.monotonic() > deadline:
            cap_hit = True
            break
        if rung > 0 and _or_ask_cost() >= PER_ASK_CEILING_USD:
            # v18 (§B remainder): past the per-ask cost line the
            # ladder stops buying redrafts; the best judged
            # draft goes to the capped-partial path below.
            cost_capped = True
            break
        rungs = rung + 1
        _set_stage("drafting" if rung == 0
                   else f"redrafting (round {rung + 1})")
        instr = ctx["instructions"]
        if draft_extra:
            instr = (instr + "\n\n" + draft_extra).strip()
        instr = (instr + "\n\n" + CITATION_CONTRACT).strip()
        if objections:
            instr = (instr + "\n\n" + objections).strip()
        eng, mdl = _draft_choice(rung)
        draft_b_text = None
        try:
            if rung == 0 and diverge:
                # Item 4 (v16): the independent second draft comes
                # from a NIM flagship of a different family than
                # draft A's seat (the bake-off runner-up seat),
                # never a local model (James's ruling).
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fa = _submit(pool, _try_generate, eng, mdl, instr)
                    fb = _submit(pool, _generate, "nimx",
                                     _diverge_model(),
                                     ctx["unit_q"], ctx["hits"],
                                     instr)
                    res = fa.result()
                    try:
                        draft_b_text = fb.result()[0]
                    except Exception:
                        draft_b_text = None
            else:
                res = _try_generate(eng, mdl, instr)
        except Exception as e:
            gen_error = str(e)[:200]
            draft = None
            break
        draft, prompt, served, dg, fb = res
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
        c_claims, c_forced = _conflict_claims(conflicts, draft)
        # v18 (F16): conflict claims are resolved IN CODE ONLY
        # and never enter the judge-facing claim set (pass 2
        # item F1): a draft that does not surface both values
        # fails this gate whatever any judge says about it.
        conflict_fail = bool(c_forced)
        extra_claims = [dict(cc, span=cc["text"])
                        for cc in parsed["cited_claims"]]
        if served and served.startswith(LOCAL_MODEL):
            # A resident 9B draft is done; free its VRAM before
            # judging in case a local fallback seat is needed.
            _unload_others(ROUTED_MODEL)
        _set_stage(f"judging round {rung + 1}")
        jt0 = time.monotonic()
        jr = _judge_round(judge_q, ctx["hits"], draft,
                          hard, judge_block, corp=ctx.get("corp"),
                          mechanisms=ctx["mechanisms"],
                          extra_claims=extra_claims,
                          local_only=bool(ctx.get("local_mode")),
                          use_cache=not ctx.get("fresh"),
                          deadline=deadline)
        judge_ms += int((time.monotonic() - jt0) * 1000)
        refute_ms += jr.get("refute_ms", 0)
        judged_draft = draft
        if lead_uncited:
            # Item 4 micro-repair: ONE per rung. The drafter
            # rewrites only the lead, with the exact citation it
            # must carry named; the repaired draft re-enters
            # normal judging and every other gate re-runs on
            # it. Code never attaches the citation itself and
            # the gate is not loosened.
            lead_repairs += 1
            new_lead = _repair_lead(ctx, draft, lead_sentence,
                                    eng, mdl, instr)
            if new_lead and new_lead != lead_sentence:
                draft2 = draft.replace(lead_sentence, new_lead, 1)
                parsed2 = _parse_citations(draft2, ctx["hits"])
                if new_lead not in parsed2["uncited_lb"]:
                    _set_stage(f"judging round {rung + 1} "
                               "(lead repair)")
                    jt1 = time.monotonic()
                    jr2 = _judge_round(
                        judge_q, ctx["hits"], draft2, hard,
                        judge_block, corp=ctx.get("corp"),
                        mechanisms=ctx["mechanisms"],
                        extra_claims=extra_claims,
                        local_only=bool(ctx.get("local_mode")),
                        use_cache=not ctx.get("fresh"),
                        deadline=deadline)
                    judge_ms += int((time.monotonic() - jt1) * 1000)
                    refute_ms += jr2.get("refute_ms", 0)
                    draft, jr = draft2, jr2
                    parsed = parsed2
                    uncited_lb = parsed2["uncited_lb"]
                    judged_draft = draft
                    _cc2, _cf2 = _conflict_claims(conflicts, draft2)
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
        if rung == 0 and diverge and draft_b_text:
            # Divergence mapping: B-only load-bearing claims the
            # NIM extractor confirms become required points the
            # final draft must cover; contested overlaps are
            # recorded (their A-side claims already went through
            # refutation as load-bearing claims).
            bj = _primary_judgment("nim", judge_q,
                                   ctx["hits"], draft_b_text,
                                   judge_block)
            if bj["available"]:
                a_toks = [{w for w in re.findall(
                    r"[a-z]{4,}", c["text"].lower()) if w not in STOP}
                    for c in jr["claims"]]
                points, contested = [], []
                for bc in bj["claims"]:
                    if not bc["load_bearing"] \
                            or bc["verdict"] != "supported":
                        continue
                    btoks = {w for w in re.findall(
                        r"[a-z]{4,}", bc["text"].lower())
                        if w not in STOP}
                    best = max((len(btoks & at) / max(1, len(btoks))
                                for at in a_toks), default=0.0)
                    if best < 0.5:
                        points.append(bc["text"])
                for c in jr["claims"]:
                    if c["final"] == "supported":
                        continue
                    ctoks = {w for w in re.findall(
                        r"[a-z]{4,}", c["text"].lower())
                        if w not in STOP}
                    for bc in bj["claims"]:
                        btoks = {w for w in re.findall(
                            r"[a-z]{4,}", bc["text"].lower())
                            if w not in STOP}
                        if ctoks and len(ctoks & btoks) / max(
                                1, len(ctoks)) >= 0.3:
                            contested.append(c["text"])
                            break
                # v18 (F14): a B-only point becomes REQUIRED
                # only when a SECOND model confirms it. Through
                # v17.3 one _primary_judgment call both proposed
                # the points and (in the objection text) claimed
                # the judges had confirmed them; unconfirmed
                # points now never block shipping.
                lj = _primary_judgment("lite", judge_q,
                                       ctx["hits"], draft_b_text,
                                       judge_block)
                if lj["available"]:
                    confirmed = []
                    for p in points:
                        ptoks = {w for w in re.findall(
                            r"[a-z]{4,}", p.lower())
                            if w not in STOP}
                        for lc in lj["claims"]:
                            if lc["verdict"] != "supported":
                                continue
                            ltoks = {w for w in re.findall(
                                r"[a-z]{4,}", lc["text"].lower())
                                if w not in STOP}
                            if ptoks and len(ptoks & ltoks) / max(
                                    1, len(ptoks)) >= 0.5:
                                confirmed.append(p)
                                break
                    points = confirmed
                else:
                    points = []
                required_points = points
                divergence = {"b_claims": len(bj["claims"]),
                              "required_points": points,
                              "contested": contested}
        missing_points = _points_missing(required_points, draft,
                                         ctx["unit_q"])
        if jr["mode"] == "none":
            break  # no judge can run: redrafting blind helps nothing
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not missing_points and not conflict_fail:
            break  # every gate passed: ship this draft
        objections = _build_objections(
            jr, mech_failures, ungrounded, uncited_lb, quantities,
            missing_points,
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
    # v17.3: strip, don't sink. The ladder's repair rungs are
    # exhausted; if the last judged draft still carries uncited
    # load-bearing sentences, cut the ones that map to no
    # extracted claim (asides) and let the stripped draft
    # re-enter the normal final disposition below (ship test,
    # Flash arbitration, capped assembly) with every
    # deterministic gate re-run on the stripped text. The cuts
    # are recorded in stripped_sentences. Claim-bearing
    # uncited sentences are untouched: they fail closed below
    # exactly as before.
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
        stripped_draft, stripped_sentences = _strip_uncited_asides(
            judged_draft, parsed, jr["claims"], lead_now)
        if stripped_draft is not None:
            draft = stripped_draft
            judged_draft = stripped_draft
            parsed = _parse_citations(stripped_draft, ctx["hits"])
            uncited_lb = parsed["uncited_lb"]
            _a, ungrounded = _check_figures(
                stripped_draft, ctx["question"], ctx["hits"],
                extra_allowed=ctx["allowed_extra"])
            mech_failures = _mech_required_failures(
                stripped_draft, ctx["mechanisms"],
                ctx.get("csra_comp"))
            quantities = _quantities_for(stripped_draft)
            missing_points = _points_missing(required_points,
                                             stripped_draft,
                                             ctx["unit_q"])
            # v18 (F7): the stripped draft is RE-JUDGED before
            # it can ship. Through v17.3 only the deterministic
            # gates re-ran and the ship test below read the
            # PRE-strip round's pass, so a strip could carry a
            # panel-failed draft into a verified ship.
            _set_stage("judging (stripped draft)")
            _jt2 = time.monotonic()
            jr = _judge_round(
                judge_q, ctx["hits"], stripped_draft, hard,
                judge_block, corp=ctx.get("corp"),
                mechanisms=ctx["mechanisms"],
                extra_claims=[dict(cc, span=cc["text"])
                              for cc in parsed["cited_claims"]],
                local_only=bool(ctx.get("local_mode")),
                use_cache=not ctx.get("fresh"), deadline=deadline)
            judge_ms += int((time.monotonic() - _jt2) * 1000)
            refute_ms += jr.get("refute_ms", 0)
            judged_draft = stripped_draft
            _cc3, _cf3 = _conflict_claims(conflicts,
                                          stripped_draft)
            conflict_fail = bool(_cf3)
    shipped = False
    if draft is not None and jr is not None and jr["mode"] != "none":
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not missing_points and not conflict_fail:
            shipped = True
        elif deadline and time.monotonic() > deadline:
            cap_hit = True  # no budget left for the final ruling
        else:
            # Final rung (plan section 5): Flash rules once on the
            # last draft's failed claims; its ruling is final.
            # v18 (F3, pass 2 item A4): claims a MAJORITY of
            # seats contradicted are excluded — Flash was being
            # handed settled contradictions framed as splits,
            # and its ruling overwrote the panel's. Conflict
            # claims never reach Flash either (code-resolved).
            failed = [i for i, c in enumerate(jr["claims"])
                      if c["final"] != "supported"
                      and c["flash"] is None and not c.get("meta")
                      and c.get("origin") != "conflict"
                      and sum(1 for k in ("lite", "nim", "nim2",
                                          "local")
                              if c.get(k) == "contradicted") < 2]
            if failed and not ungrounded and not mech_failures \
                    and not quantities and not uncited_lb \
                    and not missing_points and not conflict_fail:
                rulings = _flash_arbitrate(judge_q, ctx["hits"],
                                           jr["claims"], failed)
                if rulings:
                    jr["flash_used"] = True
                    jr["mode"] = "arbitrated"
                    for idx, verdict in rulings.items():
                        jr["claims"][idx]["flash"] = verdict
                        jr["claims"][idx]["final"] = verdict
                    jr["pass"] = all(
                        c["final"] == "supported" for c in jr["claims"]
                        if not c.get("meta"))
                    shipped = jr["pass"]
    base = {"served": ctx.get("_served"), "fallback": ctx.get("_fb", False),
            "downgraded": ctx.get("_dg", False),
            "prompt": ctx.get("_prompt", ""), "rungs": rungs,
            "judge_ms": judge_ms, "refute_ms": refute_ms,
            "cap_hit": cap_hit,
            "cost_capped": cost_capped,
            "draft_text": judged_draft,
            "figure_check": {"ungrounded": list(ungrounded),
                             "regenerated": rungs > 1},
            "divergence": divergence,
            "events": _events_since(ev0),
            "lead_repairs": lead_repairs,
            "stripped_sentences": stripped_sentences}
    if shipped:
        answer, _fixes = _check_arithmetic(draft)
        # v18 (F2/§C): the badge follows PER-CLAIM FAMILY
        # COVERAGE, not the round's mode: "verified" only when
        # every substantive claim carries supporting verdicts
        # from at least two distinct model families. A claim
        # resolved by one family alone (whatever the round's
        # mode string says) degrades the answer to
        # verified_single.
        _subs = [c for c in jr["claims"] if not c.get("meta")]
        badge = "verified" if _subs and all(
            len(c.get("coverage") or []) >= 2 for c in _subs) \
            else "verified_single"
        # v18 (§C, James's ruling 2026-10-14): a top-class badge
        # earned on a DEGRADED panel (see _panel_state) is
        # renamed so the degradation shows in the badge itself.
        # verified_single keeps its name: it is already visibly
        # different and more specific.
        if badge == "verified" \
                and jr.get("panel_state") == "degraded":
            badge = "verified_reduced"
        return dict(base, answer=answer, badge=badge,
                    judging={"mode": jr["mode"],
                             "flash_used": jr["flash_used"],
                             "errors": jr["errors"],
                             "annotation": _panel_annotation(jr),
                             "panel_state": jr.get("panel_state"),
                             "panel": jr.get("panel")},
                    claims=jr["claims"], missing=[], gate=None)
    if (cap_hit or cost_capped) and judged_draft is not None \
            and jr is not None and jr["mode"] != "none":
        # The cap stopped the ladder: ship ONLY the claims the
        # panel already verified, as a Partial.
        assembled = _assemble_capped_partial(
            judged_draft, jr, ctx["question"], ctx["hits"],
            ctx["allowed_extra"])
        if assembled:
            _atext, _min_cov = assembled
            badge = "partial" if _min_cov >= 2 \
                else "partial_single"
            # v18 (§C, James's ruling 2026-10-14): same rename
            # as the verified top class; partial_single keeps
            # its name (already visibly different, more
            # specific).
            if badge == "partial" \
                    and jr.get("panel_state") == "degraded":
                badge = "partial_reduced"
            _why = ("the ask reached its per-ask cost ceiling "
                    f"(${PER_ASK_CEILING_USD:.2f}); only "
                    "panel-verified claims are shown") \
                if cost_capped and not cap_hit else \
                ("the ask reached its time budget "
                 f"({budget_s} seconds); only "
                 "panel-verified claims are shown")
            cut = [c["text"] for c in jr["claims"]
                   if c["final"] != "supported" and not c.get("meta")]
            return dict(base, answer=_atext, badge=badge,
                        judging={"mode": jr["mode"],
                                 "flash_used": jr["flash_used"],
                                 "errors": jr["errors"],
                                 "annotation": _panel_annotation(jr),
                                 "panel_state": jr.get("panel_state"),
                                 "panel": jr.get("panel")},
                        claims=jr["claims"],
                        missing=[_why,
                                 *[f"not verified in time: \"{t}\""
                                   for t in cut[:4]]],
                        gate=None)
    # IDK: assemble what is missing from the failed gates.
    missing, gate = [], "judging"
    if cap_hit:
        gate = "time"
        missing.append("the ask reached its time budget "
                       f"({budget_s} seconds) before a "
                       "verified answer could be assembled")
    if gen_error:
        missing.append("the answering model could not run "
                       f"({gen_error})")
    if cost_capped:
        missing.append("the ask reached its per-ask cost ceiling "
                       f"(${PER_ASK_CEILING_USD:.2f}) before a "
                       "verified answer could be assembled")
    if jr is not None and jr["mode"] == "none":
        missing.append("no verification judge could run "
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
    if jr is not None:
        for c in jr["claims"]:
            if c["final"] != "supported" and not c.get("meta"):
                missing.append(f"unverified claim: \"{c['text']}\" "
                               f"(judged {c['final']})")
                if c["final"] == "not_addressed":
                    gate = "grounding"
                if c["flash"] is not None:
                    gate = "arbitration"
    if cap_hit:
        gate = "time"
    return dict(base, answer=None, badge="idk",
                judging={"mode": (jr or {}).get("mode", "none"),
                         "flash_used": (jr or {}).get("flash_used", False),
                         "errors": (jr or {}).get("errors", []),
                         "annotation": _panel_annotation(jr),
                         "panel_state": (jr or {}).get("panel_state"),
                         "panel": (jr or {}).get("panel")},
                claims=(jr or {}).get("claims", []),
                missing=missing[:8], gate=gate)
def _idk_answer(missing, hits, lead):
    """The full IDK payload (James's decision): a plain refusal, what
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
<title>FSSA Medicaid Q&A</title>
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
  line-height:1.6; min-height:100vh; display:flex; flex-direction:column; align-items:center;
  padding:48px 16px;
}
.wrap{width:100%; max-width:720px;}
header{display:flex; align-items:center; gap:16px; margin-bottom:32px;}
header img{width:48px; height:48px; border-radius:12px;}
header h1{font-size:24px; font-weight:600; letter-spacing:-0.02em; color:var(--ink);}
header p{font-size:14px; color:var(--ink-tertiary);}
.card{background:var(--surface-1); border:1px solid var(--hairline); border-radius:12px; padding:24px;}
label{display:block; font-size:12px; font-weight:500; letter-spacing:0.05em; text-transform:uppercase;
  color:var(--ink-tertiary); margin-bottom:8px;}
select, textarea{
  width:100%; background:var(--surface-2); border:1px solid var(--hairline-strong);
  border-radius:6px; color:var(--ink); font-size:16px; padding:12px; font-family:inherit;
}
select{margin-bottom:16px;}
textarea{min-height:96px; resize:vertical; margin-bottom:16px;}
select:focus, textarea:focus{outline:2px solid var(--accent); border-color:var(--accent);}
button{
  background:var(--accent); color:#fff; border:none; border-radius:6px; padding:12px 24px;
  font-size:16px; font-weight:500; cursor:pointer; font-family:inherit;
}
button:hover{background:var(--accent-hover);}
button:disabled{background:var(--ink-disabled); cursor:default;}
#answer{margin-top:24px; display:none;}
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
#verifyNote{margin-top:12px; font-size:13px; color:var(--ink-tertiary);}
.spinner{display:none; margin-top:16px; color:var(--ink-tertiary); font-size:14px;}
footer{margin-top:48px; font-size:12px; color:var(--ink-disabled); text-align:center; max-width:65ch;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img src="/icon.png" alt="FSSA Q&A icon">
    <div>
      <h1>FSSA Medicaid Q&A</h1>
      <p>Ask questions about Indiana Medicaid, FSSA benefits, and the HCBS waiver. Answers come from 5,234 documents (Indiana FSSA/Medicaid plus federal, including SSA and its POMS operations manual), on your PC.</p>
    </div>
  </header>
  <div class="card">
    <label style="display:inline-block; text-transform:none; letter-spacing:0; font-size:14px; margin:0 0 16px 0;"><input type="checkbox" id="runLocal" onchange="toggleLocal()"> Run fully locally (drafting, judging and utilities all on your PC; no hosted calls, no web branch-out)</label>
    <div id="localPick" style="display:none;">
      <label for="localModel">Local model</label>
      <select id="localModel">
        <option value="qwen3.5:9b" selected>qwen3.5:9b (fast)</option>
        <option value="qwen3:14b">qwen3:14b (stronger, slower)</option>
      </select>
    </div>
    <label for="corpus">Search</label>
    <select id="corpus">
      <option value="in">Indiana (FSSA/Medicaid)</option>
      <option value="federal">Federal</option>
      <option value="both" selected>Both</option>
    </select>
    <label for="q">Your question</label>
    <textarea id="q" placeholder="e.g. What does the HCBS waiver say about structured family caregiving?"></textarea>
    <button id="ask" onclick="ask()">Ask</button>
    <label style="display:inline-block; text-transform:none; letter-spacing:0; font-size:14px; margin:0 0 0 12px;"><input type="checkbox" id="fresh"> Fresh answer (skip cache)</label>
    <button id="copyBtn" onclick="copyPrompt()" style="display:none; margin-left:8px; background:var(--surface-3); color:var(--ink);">Copy full prompt</button>
    <div class="spinner" id="spin">Searching documents and asking the model...</div>
  </div>
  <div id="answer">
    <div class="card">
      <h2>Answer<span id="answerBadge"></span></h2>
      <div id="answerText"></div>
      <div id="verifyNote"></div>
      <div id="sources"><h3>Sources</h3><div id="sourceList"></div></div>
    </div>
  </div>
  <footer>Corpus updated 2026-10-12. Confirm anything affecting benefits or eligibility with your caseworker or FSSA.</footer>
</div>
<script>
function toggleLocal(){
  const on = document.getElementById('runLocal').checked;
  document.getElementById('localPick').style.display = on ? 'block' : 'none';
}
async function ask(){
  // v18 (F18): a second submit while an ask is running is
  // ignored outright — through v17.3 the button state was the
  // only guard, and a re-entrant call doubled the spend.
  if(window._asking) return;
  window._asking = true;
  const q = document.getElementById('q').value.trim();
  if(!q){ window._asking = false; return; }
  // v16: no model dropdown. The default ask names the local
  // default model, which the server treats as "no explicit
  // pick" (hosted NIM drafting + the remote judge panel). The
  // run-locally toggle is the only way to pick a local model.
  const runLocal = document.getElementById('runLocal').checked;
  const model = runLocal
    ? document.getElementById('localModel').value : 'qwen3.5:9b';
  const corpus = document.getElementById('corpus').value;
  const btn = document.getElementById('ask');
  btn.disabled = true;
  document.getElementById('spin').style.display = 'block';
  document.getElementById('answer').style.display = 'none';
  const spin = document.getElementById('spin');
  try{
    // v15: job-based flow — submit the ask as a job and poll for
    // the result, showing the server's live stage. Falls back to
    // the synchronous request ONLY when no job was created.
    // v18 (F19): the local selection is sent as local=true; the
    // server runs the whole pipeline on-device for it.
    const payload = {question:q, model:model, corpus:corpus, fresh:document.getElementById('fresh').checked, local:runLocal};
    if(runLocal) spin.textContent = 'Running locally on ' + model + '...';
    let d = null;
    let jobId = null;
    try{
      const r0 = await fetch('/ask?async=1', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const j0 = await r0.json();
      if(!j0.job_id) throw new Error('no job id');
      jobId = j0.job_id;
      spin.textContent = 'Working: ' + (j0.stage || 'retrieving') + '...';
      for(let i = 0; i < 400 && !d; i++){
        await new Promise(res => setTimeout(res, 2000));
        // v18 (F18): a failed POLL is tolerated and polling
        // continues — the job is still running server-side.
        let jp = null;
        try{
          const rp = await fetch('/ask/result/' + jobId);
          jp = await rp.json();
        }catch(pollErr){ continue; }
        if(jp.status === 'done'){ d = jp.result; }
        else if(jp.status === 'error'){ throw new Error(jp.error || 'job failed'); }
        else { spin.textContent = 'Working: ' + (jp.stage || 'retrieving') + '...'; }
      }
      if(!d) throw new Error('timed out waiting for the answer');
    }catch(jobErr){
      // v18 (F18): once a job exists, NEVER re-POST the question
      // synchronously — that re-ran (and re-billed) the whole
      // pipeline while the job also finished: the Miller
      // triple-submission shape. The error is shown instead.
      if(jobId || d) throw jobErr;
      const r = await fetch('/ask', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      d = await r.json();
    }
    document.getElementById('answerText').textContent = d.answer || ('Error: ' + (d.error||'unknown'));
    const badgeMap = {
      verified: ['ok', 'Verified'],
      verified_web: ['ok', 'Verified · web sources'],
      verified_reduced: ['no', 'Verified (reduced panel)'],
      verified_web_reduced: ['no', 'Verified web (reduced panel)'],
      verified_single: ['no', 'Verified · one judge only'],
      partial: ['no', 'Partial · some parts could not be verified'],
      partial_reduced: ['no', 'Partial (reduced panel)'],
      partial_single: ['no', 'Partial · one judge only'],
      idk: ['err', 'IDK · not verified']
    };
    const bm = badgeMap[d.badge];
    document.getElementById('answerBadge').innerHTML = bm
      ? '<span class="badge ' + bm[0] + '">' + bm[1] + '</span>' : '';
    const vn = document.getElementById('verifyNote');
    if(d.judging){
      // v18 (§C): the annotation names the panel that actually
      // sat ("Judges: ..."), built server-side from the claims'
      // serving models; the mode line is the fallback.
      let note = d.judging.annotation || ('Verification: ' + d.judging.mode);
      if(d.judging.flash_used) note += ' · Flash arbitration used';
      if(d.timing && d.timing.rungs) note += ' · ' + d.timing.rungs + ' ladder rung(s)';
      vn.textContent = note;
    } else { vn.textContent = ''; }
    const sl = document.getElementById('sourceList');
    sl.innerHTML = '';
    (d.hits||[]).forEach(h=>{
      const a = document.createElement('a');
      a.href = h.url; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = h.title;
      const b = document.createElement('span');
      if(h.web){
        // v16 branch-out evidence: live-fetched official page,
        // with the fetch date shown beside it.
        b.className = 'badge ' + (h.verified ? 'ok' : 'no');
        b.textContent = 'web · fetched ' + (h.fetched || '');
      } else {
        b.className = 'badge ' + (h.verified ? 'ok' : 'no');
        b.textContent = h.verified ? 'verified' : 'unverified';
      }
      a.appendChild(b);
      sl.appendChild(a);
    });
    document.getElementById('answer').style.display = 'block';
    if(d.prompt){
      window._lastPrompt = d.prompt;
      document.getElementById('copyBtn').style.display = 'inline-block';
    }
  }catch(e){
    document.getElementById('answerText').textContent = 'Error: ' + e.message;
    document.getElementById('answer').style.display = 'block';
  }
  btn.disabled = false;
  window._asking = false;
  document.getElementById('spin').style.display = 'none';
}
async function copyPrompt(){
  if(!window._lastPrompt) return;
  try{
    await navigator.clipboard.writeText(window._lastPrompt);
    const b = document.getElementById('copyBtn');
    b.textContent = 'Copied!';
    setTimeout(()=>{ b.textContent = 'Copy full prompt'; }, 2000);
  }catch(e){ alert('Copy failed: ' + e.message); }
}
document.getElementById('q').addEventListener('keydown', e=>{
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
    here."""
    _STAGE_TLS.job = rec
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
    try:
        Handler.do_POST(shim)
        rec["result"] = captured.get("obj")
        code = captured.get("code", 200)
        rec["status"] = "done" if code < 400 else "error"
        if rec["status"] == "error":
            rec["error"] = str(captured.get("obj"))[:300]
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = str(e)[:300]
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
                        # v16: seat health (item 1) and the
                        # branch-out budget state (item 7).
                        "seats": _seat_health_block(),
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
                        "gemini_flash_mid": _flash_mid_health_block()})
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
                       "stage": job["stage"]}
                if job["status"] == "done":
                    out["result"] = job["result"]
                elif job["status"] == "error":
                    out["error"] = job["error"]
                self._json(out)
        elif u.path == "/models":
            # Q&A dropdown is deliberately limited to the 9b local model
            # plus Gemini (James's call, 2026-10-12); Ollama may host many
            # more models for other uses.
            models = [m for m in ollama_models() if m == "qwen3.5:9b"]
            # Add Gemini options if API key is configured
            if GEMINI_API_KEY:
                models = ["gemini-3.5-flash-lite (API)",
                          "gemini-3.8-flash (API)"] + models
            self._json({"models": models})
        elif u.path == "/search":
            qs = parse_qs(u.query)
            q = qs.get("q", [""])[0]
            corp = get_corpus(qs.get("corpus", ["in"])[0])
            # use hybrid search when embeddings are available, fallback to keyword
            if EMBEDDINGS:
                self._json({"query": q, "hits": hybrid_search(q, corpus=corp,
                                                              boosts=False)})
            else:
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
            corpus_name = req.get("corpus", "in")
            # Feature toggles
            use_rewrite = req.get("rewrite", REWRITE_ENABLED)
            use_rerank = req.get("rerank", RERANK_ENABLED)
            corp = get_corpus(corpus_name)
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
            t_start = time.monotonic()
            _set_stage("retrieving")
            # v15: the per-ask wall-clock cap (config
            # ASK_TIME_CAP_S); the ladder and the units loop
            # consult this deadline and never ship unverified.
            deadline = t_start + ASK_TIME_CAP_S
            # v16: per-ask records (seat events + local loads,
            # retrieval sub-timings, branch-out accounting).
            _events_reset()
            _retr_reset()
            _or_reset_ask()
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
                if discovery or use_gemini:
                    return []
                if local_mode:
                    return []  # v18 (F19): local asks never branch out
                if model and model != LOCAL_MODEL:
                    return []
                if time.monotonic() > deadline - 60:
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
                7): a verified unit whose supported load-bearing
                claims rest on web excerpts ships verified_web;
                its cited web hits are marked verified for the
                source list. Partial keeps its badge; the web
                sourcing is stated in the record. The web flag is
                orthogonal to the panel state (§C, James's ruling
                2026-10-14): a verified unit from a degraded panel
                ships verified_web_reduced."""
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
                    if unit_rec["badge"] == "verified":
                        unit_rec["badge"] = "verified_web"
                    elif unit_rec["badge"] == "verified_reduced":
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
            # v13 routing: contested-figure and bundled questions asked
            # of the local default go to the 14B. Explicit model picks
            # and the Gemini chain are never rerouted. The cache key
            # carries the routed model, so a 14B answer is never
            # replayed for a 9B ask or vice versa.
            routed_model, route_reason = model, ""
            if not use_gemini:
                routed_model, route_reason = route_local_model(
                    question, model)
            ask_key = (question, routed_model, corpus_name,
                       use_rewrite, use_rerank, local_mode)
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
                # No model named: the routed local default answers.
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
            if discovery and EMBEDDINGS:
                hits, missing_areas = discovery_hits(question, corpus=corp)
                if not hits:  # nothing per-area: fall back to normal search
                    hits = hybrid_search(question, corpus=corp, n=n_ex,
                                         use_rewrite=use_rewrite,
                                         use_rerank=use_rerank)
                instructions = SURVEY_INSTRUCTIONS
                if missing_areas:
                    instructions += (" Retrieval found no documents for: "
                                     + ", ".join(missing_areas)
                                     + ". List those categories in the "
                                       "'Not covered' line.")
            elif EMBEDDINGS:
                # v11: bundled (multi-part) narrative questions get
                # per-sub-question retrieval with guaranteed slots.
                hits = retrieve_ask_hits(question, corp, n_ex,
                                         use_rewrite=use_rewrite,
                                         use_rerank=use_rerank)
                hits = _drop_payee_admin(question, hits)
            else:
                hits = search(question, corpus=corp)
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
            allowed_extra = _registry_values(registry_entries)
            if csra_comp:
                allowed_extra += [csra_comp["assets"], csra_comp["half"],
                                  csra_comp["csra"]]
            sufficiency_ms = 0
            n_gaps = 0

            def _respond(answer_text, badge, judging, verification,
                         figure_check, served, fallback, downgraded,
                         full_prompt, rungs, judge_ms, refute_ms=0,
                         cap_hit=False):
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
                # v18 (F17): only verified-tier answers enter the
                # replay cache. Through v17.3 partials and IDKs
                # cached too, so a transient failure froze into
                # the answer every later re-ask replayed. (The
                # _reduced names are the same verified tier under
                # James's 2026-10-14 badge ruling and cache the
                # same way.)
                if badge in ("verified", "verified_web",
                             "verified_reduced",
                             "verified_web_reduced"):
                    _cache_put(_ASK_CACHE, ask_key, result)
                # v18 (§E): persist the ask record (see
                # _record_ask). Per-unit detail comes from the
                # units list when the pipeline built one; the
                # early IDK paths record without it.
                try:
                    _units_now = units
                except NameError:
                    _units_now = []
                _record_ask({
                    "kind": "ask", "ts": time.time(),
                    "wall_s": round(t_end - t_start, 3),
                    "question": question, "corpus": corpus_name,
                    "local": local_mode, "badge": badge,
                    "cost_usd": result["cost_usd"],
                    "served": served,
                    "panel": (judging or {}).get("panel"),
                    "annotation": (judging or {}).get("annotation"),
                    "units": [{
                        "part": u.get("part"),
                        "unit_q": u.get("unit_q") or None,
                        "badge": u.get("badge"),
                        "draft_text": (u.get("draft_text")
                                       or "")[:8000],
                        "claims": [{
                            "text": c.get("text"),
                            "final": c.get("final"),
                            "verdicts": {k: c.get(k) for k in
                                         ("lite", "nim", "nim2",
                                          "local", "flash",
                                          "refute_verdict")},
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
                    } for u in _units_now],
                })
                # v18 (F18): publish to any identical in-flight
                # duplicate waiting on this ask, then release.
                if _inf_entry is not None:
                    _inf_entry["result"] = result
                    _inflight_release(_inf_key)
                self._json(result)

            no_judging = {"mode": "none", "flash_used": False,
                          "flash_calls_today": _flash_calls_today(),
                          "panel": _panel_info(
                              [], {"primary": None, "second": None,
                                   "third": None},
                              "no-key" if not _third_judge_key()
                              else "not-run")}
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
            if not use_gemini and route_reason:
                # v13: free the 9B's VRAM before the 14B loads
                # (v14.1: every other resident chat model goes too).
                _unload_others(ROUTED_MODEL)
            hard = bool(route_reason) or bool(mechanisms_all)
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
                    p_allowed = _registry_values(p_reg) + [
                        v for v in allowed_extra
                        if v not in _registry_values(p_reg)]
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
                       # the ask deadline, divergence eligibility
                       # (single-unit asks only, never per-part),
                       # and the explicit-model guard.
                       "corp": corp, "deadline": deadline,
                       "diverge": spec["part"] is None
                       and not discovery,
                       "explicit_model": bool(
                           model and not use_gemini
                           and model != LOCAL_MODEL),
                       # v18: the honest local mode (F19), the
                       # cache-bypass flag (F12: fresh asks never
                       # read the verdict cache), and the full
                       # question for part judging (F13).
                       "local_mode": local_mode,
                       "fresh": fresh,
                       "full_question": question
                       if spec["part"] else None}
                prepared.append((spec, ctx))

            def _run_prepared(pc):
                _spec, _ctx = pc
                if time.monotonic() > deadline:
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
                                    "flash_used": False,
                                    "annotation": "",
                                    "errors": ["ask time cap "
                                               "reached before "
                                               "this part ran"],
                                    "panel": no_judging["panel"]},
                        "claims": [],
                        "missing": ["the ask reached its time "
                                    f"budget ({ASK_TIME_CAP_S} "
                                    "seconds) before this part "
                                    "could be verified"],
                        "gate": "time", "divergence": None}
                return _run_unit(_ctx)

            # v18 (R8): bundled units run in PARALLEL (max 3
            # workers). Each unit's per-ask state rides its own
            # _AskContext (F10), so parallel units cannot corrupt
            # each other's accounting; the shared stage line is
            # last-writer-wins across units (documented nuance),
            # and per-unit events in parallel mode are the units'
            # full ask events. The branch-out re-run below stays
            # serial: it is rare and spends Tavily credits.
            if len(prepared) > 1:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    _futs = [_submit(pool, _run_prepared, pc)
                             for pc in prepared]
                    raw_units = [f.result() for f in _futs]
            else:
                raw_units = [_run_prepared(pc) for pc in prepared]
            units = []
            for (spec, ctx), unit in zip(prepared, raw_units):
                unit_hits = spec["hits"]
                if unit["badge"] == "idk" \
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
                    web_b = _branch_attempt(failed_claims)
                    if web_b:
                        ctx2 = dict(ctx)
                        ctx2["hits"] = list(spec["hits"]) + web_b
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
                unit["part"] = spec["part"]
                unit["unit_q"] = spec["unit_q"]
                unit["hits"] = unit_hits
                units.append(unit)
                if unit["badge"] == "idk":
                    log_gap(question, "idk", unit["gate"] or "judging",
                            unit["missing"], part=spec["part"])
                    n_gaps += 1
                # v15 (item 1): claims killed by retrieved
                # contradictions feed the growth loop too.
                for c in unit.get("claims", ()):
                    if c.get("refutations") \
                            and c["final"] != "supported":
                        log_gap(question, unit["badge"], "refutation",
                                [f"claim judged {c['final']} after "
                                 f"refutation retrieval: "
                                 f"\"{c['text']}\""],
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
                # at the verified rank and surfaces only when
                # every unit is top-rank and at least one unit
                # is web-sourced). Any IDK unit caps the
                # assembly at the partial tier, graded by the
                # weakest ANSWERED unit. (Through v17.3 the
                # comparisons were ad-hoc string tests; a unit
                # mix of verified + partial_single, for example,
                # fell through to partial_single by accident of
                # ordering rather than by rule.) The _reduced
                # names (James's ruling 2026-10-14) rank just
                # below their full counterparts and above the
                # matching _single class, so a degraded panel
                # stays visible in the assembled badge too.
                _BADGE_RANK = {"idk": 0, "partial_single": 1,
                               "partial_reduced": 2, "partial": 3,
                               "verified_single": 4,
                               "verified_reduced": 5,
                               "verified_web_reduced": 5,
                               "verified": 6, "verified_web": 6}
                # How a weakest-answered unit's badge reads when
                # an IDK unit caps the assembly at the partial
                # tier: full top classes drop to "partial", the
                # reduced ones to "partial_reduced" (the
                # pre-ruling mapping for the old names is
                # unchanged).
                _CAPPED_BY_WORST = {
                    "partial_single": "partial_single",
                    "partial_reduced": "partial_reduced",
                    "partial": "partial",
                    "verified_single": "partial",
                    "verified_reduced": "partial_reduced",
                    "verified_web_reduced": "partial_reduced",
                    "verified": "partial",
                    "verified_web": "partial"}
                _answered = [b for b in badges if b != "idk"]
                if not _answered:
                    overall_badge = "idk"
                else:
                    _worst = min(_answered,
                                 key=lambda b: _BADGE_RANK.get(b, 0))
                    if len(_answered) < len(badges):
                        overall_badge = _CAPPED_BY_WORST.get(
                            _worst, "partial_single")
                    elif _BADGE_RANK.get(_worst, 0) >= 6:
                        overall_badge = "verified_web" \
                            if "verified_web" in badges \
                            else "verified"
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
                        if overall_badge in ("verified",
                                             "verified_web"):
                            overall_badge = "partial"
                        elif overall_badge in (
                                "verified_reduced",
                                "verified_web_reduced"):
                            overall_badge = "partial_reduced"
                        elif overall_badge == "verified_single":
                            overall_badge = "partial_single"
                        log_gap(question, overall_badge, "mechanism",
                                [f["label"] for f in g_fail])
                        n_gaps += 1
            modes = {u["judging"]["mode"] for u in units}
            # v14.2: aggregate the units' panel records (bundled
            # parts share the same seat configuration): the union
            # of judges that ran, and the third seat's status —
            # 'ok' if it judged any unit, else the dormant/failed
            # status every unit reported.
            panel_judges = []
            third_status = "no-key" if not _third_judge_key() \
                else "not-run"
            for u in units:
                p = u["judging"].get("panel") or {}
                for j in p.get("judges") or ():
                    if j not in panel_judges:
                        panel_judges.append(j)
                st = (p.get("third") or {}).get("status")
                if st == "ok":
                    third_status = "ok"
                elif st == "unavailable" and third_status != "ok":
                    third_status = "unavailable"
            judging = {"mode": modes.pop() if len(modes) == 1
                       else "mixed",
                       "flash_used": any(u["judging"]["flash_used"]
                                         for u in units),
                       "flash_calls_today": _flash_calls_today(),
                       "panel": {
                           "judges": panel_judges,
                           "third": {
                               # v17.1: judge_nim1's primary
                               # rides OpenRouter; name the
                               # provider of its active candidate.
                               # v20: the name comes off the
                               # slot map (xAI/Groq slots label
                               # themselves).
                               "provider": _slot_provider_name(
                                   _seat_pick("judge_nim1")[1]),
                               "model": _seat_pick("judge_nim1")[0]
                               or THIRD_JUDGE_MODEL,
                               "status": third_status},
                           "nim2": {
                               # v17.2: judge_nim2's primary
                               # rides OpenRouter too; name
                               # the provider and key slot of
                               # its active candidate.
                               "provider": _slot_provider_name(
                                   _seat_pick("judge_nim2")[1]),
                               "model": _seat_pick("judge_nim2")[0],
                               "key_slot": _seat_pick(
                                   "judge_nim2")[1],
                               "status": "ok" if "nim2"
                               in panel_judges else "not-run"},
                           "third_calls_today": _third_calls_today(),
                           "cached_claims": sum(
                               (u["judging"].get("panel") or {})
                               .get("cached_claims", 0)
                               for u in units),
                           "refuted_claims": sum(
                               (u["judging"].get("panel") or {})
                               .get("refuted_claims", 0)
                               for u in units)}}
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
                     cap_hit=any(u.get("cap_hit") for u in units))
        except Exception as e:
            if _inf_entry is not None:
                _inflight_release(_inf_key)
            self._json({"error": str(e)[:300]}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"FSSA Q&A server: {len(CORPUS_IN)} IN docs + {len(CORPUS_FED)} federal docs on port {PORT}", flush=True)
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
