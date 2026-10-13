"""FSSA Medicaid Q&A server.

Serves:
  GET  /                    -> Q&A web page (house design tokens)
  GET  /search?q=keywords   -> JSON {hits: [{title, url, source, verified, excerpt}]}
  GET  /health              -> {"ok": true, "docs": N}
  GET  /models              -> {"models": [...]} from Ollama
  POST /ask                 -> {"answer": ..., "hits": [...]} (JSON body: {question, model})
  GET  /icon.png            -> app icon

The /ask endpoint searches the local corpus, then asks the local Ollama
model to answer from the retrieved excerpts. No data leaves the PC.

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
"""
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
    try:
        conn.request("POST", path, body=body, headers=headers)
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

def _cache_put(cache, key, val):
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = val

def vector_search(query, n=6, corpus=None):
    """Pure vector similarity search over embedded chunks, respecting corpus filter."""
    if not EMBEDDINGS:
        return []
    qvec = _embed_query(query)
    if not qvec:
        return []
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
    ask; they now run on the NIM fast tier (the same seat that
    drafts simple questions), so the local models stay cold.
    Raises on any failure: the utilities degrade to their empty
    result, exactly as they did when the local call failed.
    (_nim_text and the DRAFTER_* constants are defined later in
    this file; this resolves at call time.)"""
    return _nim_text(
        DRAFTER_BASE_URL, DRAFTER_KEY_ENV, DRAFTER_KEY_FILE,
        DRAFTER_SIMPLE_MODEL,
        "You are a precise retrieval utility. Reply with only "
        "the requested output, no explanations.",
        prompt, temperature=0, max_tokens=max_tokens,
        timeout=timeout, provider=DRAFTER_PROVIDER)


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
    ({"wife", "husband", "spouse", "spouses", "caregiver", "caregiving"},
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


def _check_arithmetic(answer):
    """Verify the explicit dollar equations in a finished answer (v9).

    Small models write the equation and still botch the result
    ('($2,705 - $900 = $1,305)'). Python does not botch it: extract
    '$A op $B = $C' (and the '$C ($A op $B)' form), recompute, and
    append a visible correction when a stated result is off by more
    than rounding. Returns (answer, number_of_corrections)."""
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
    cached = _cache_get(_SEARCH_CACHE, cache_key)
    if cached:
        return cached

    # rewrite query for better keyword matching (optional)
    rewritten = rewrite_query(query) if (use_rewrite and REWRITE_ENABLED) else []
    # NOTE: rewritten terms are searched separately, NOT concatenated into n-grams
    # to avoid synthetic cross-boundary phrase corruption.

    kw_hits = search(query, corpus, n=n*3, extra_terms=rewritten)
    vec_hits = vector_search(query, n=n*3, corpus=corpus)
    # policy-phrasing pass: search the question rewritten in the manuals'
    # own vocabulary, fused as its own list (situational retrieval fix)
    policy_q = rewrite_policy_phrase(query) if (use_rewrite and REWRITE_ENABLED) else ""
    kw2_hits = search(policy_q, corpus, n=n*2) if policy_q else []

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
    # LLM rerank: the candidate window scales with the requested result
    # count (top 12 for a 6-result search, top 24 for a 12-excerpt
    # narrative ask), then rerank down to n.
    candidates = hits[:max(12, n * 2)]
    result = None
    if use_rerank and RERANK_ENABLED and len(candidates) > n:
        ranked = rerank_with_llm(query, candidates)
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
    return result

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
    "SPOUSE INCOME: Under spousal impoverishment rules, income belongs to "
    "the spouse whose name it is in. A non-applicant (community) spouse's "
    "own earnings, including pay for caregiving, are that spouse's income "
    "and are not counted toward the applicant spouse's income limit. Do "
    "not state that a caregiver spouse's pay counts toward the other "
    "spouse's limit unless an excerpt explicitly says so. Where the "
    "money is deposited does not change whose income it is: a spouse's "
    "pay deposited into a joint account remains that spouse's income, "
    "not joint income attributable to the applicant. "
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
    "step unless an excerpt says so). For spousal resource cases, the "
    "community spouse's share is one half of the couple's combined "
    "countable assets at the snapshot date, clamped to the stated "
    "minimum and maximum; do not subtract the applicant's $2,000 "
    "allowance before halving. "
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
    scored = []
    for d in corpus:
        title = d["title"].lower()
        title_hits = sum(1 for t in terms if t in title)
        # Penalize provider-facing pages for member queries
        provider_penalty = 0
        if not has_provider_term and "provider" in title:
            provider_penalty = 15
        for ch in _chunks(d["text"]):
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
            if has_spouse and has_sfc:
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


def _gemini_space():
    """Keep a minimum gap between Gemini calls (free-tier rate limits)."""
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
    # v12: the whole-question search and the per-part searches are
    # independent; run them concurrently (they are HTTP-bound) and
    # assemble in the original order. Uncle-class retrieval dropped
    # from ~44s serial to roughly the slowest single search.
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_main = pool.submit(hybrid_search, question, corpus=corp,
                               n=n_ex, use_rewrite=use_rewrite,
                               use_rerank=use_rerank)
        fut_parts = [pool.submit(hybrid_search, part, corpus=corp, n=4,
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
    if not guaranteed:
        return main
    rest = [h for h in main
            if (h["url"], h["excerpt"][:60]) not in seen]
    return (guaranteed + rest)[:max(n_ex, len(guaranteed))]


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
    server-computed CSRA values, which are supported by definition."""
    allowed = set()
    money_re = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")

    def _grab(text):
        for m in money_re.finditer(text or ""):
            try:
                allowed.add(round(float(
                    m.group(1).replace(",", "")), 2))
            except ValueError:
                pass

    for _v in extra_allowed or ():
        try:
            allowed.add(round(float(_v), 2))
        except (TypeError, ValueError):
            pass
    _grab(question)
    for h in hits or []:
        _grab(h.get("excerpt", ""))
    for rx in (_EQ_RE, _PAREN_EQ_RE):
        for m in rx.finditer(answer or ""):
            for g in m.groups():
                try:
                    allowed.add(round(float(g.replace(",", "")), 2))
                except (ValueError, AttributeError):
                    pass
    ungrounded = []
    for m in money_re.finditer(answer or ""):
        try:
            val = round(float(m.group(1).replace(",", "")), 2)
        except ValueError:
            continue
        if val == 0:
            continue
        if not any(abs(val - a) <= 0.51 for a in allowed):
            if val not in ungrounded:
                ungrounded.append(val)
    if not ungrounded:
        return answer, []
    listed = ", ".join(f"${v:,.2f}" if v % 1 else f"${v:,.0f}"
                       for v in ungrounded[:8])
    note = ("\n\nFigure check (server): the following dollar figures "
            "in the answer above do not appear in the source excerpts "
            "or the verified figures: "
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


def _check_quantities(answer, question=""):
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
    failures."""
    fails = []
    low = (answer or "").lower()
    qlow = (question or "").lower()
    mech = _mechanism_matches(question) if qlow else []
    for m in mech:
        period = m.get("period") or {}
        for conf in period.get("confusables", ()):
            if conf in low:
                fails.append({
                    "mechanism": m["id"],
                    "label": (f"states a {conf} period where the "
                              f"rule is {period['count']} months: "
                              f"{period['note']}")})
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
    return out


def _conflict_claims(conflicts, answer):
    """Conflict records as judgeable claims, plus the code
    override map (claim text -> forced final) for drafts that
    do not present BOTH values."""
    claims, forced = [], {}
    low = (answer or "").lower()
    for cf in conflicts:
        def _has(v):
            forms = {f"${v:,.2f}", f"${v:,.0f}", f"{v:,.2f}",
                     f"{v:,.0f}"}
            return any(f.lower() in low for f in forms)
        both = _has(cf["registry_value"]) and _has(cf["corpus_value"])
        text = (f"The answer surfaces the source disagreement on "
                f"{cf['entry']}: sources give both "
                f"${cf['registry_value']:,.2f} and "
                f"${cf['corpus_value']:,.2f} (corpus variant from "
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
            "The housing (shelter) allowance component is $811.50."),
        "source": ("CMS 2026 spousal impoverishment standards; "
                   "Indiana ILTCP (prints the max as $4,067)"),
        "figures": (2705.0, 4066.50, 4067.0, 811.50),
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
            "For 2026 the Medicaid home equity limit falls between "
            "$752,000 and $1,130,000 depending on the state's "
            "election; home equity above the applicable limit makes "
            "the home a countable resource unless an exception (a "
            "spouse, minor or disabled child, or a sibling with an "
            "equity interest lawfully living in the home) applies."),
        "source": "CMS 2026 home equity limits",
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
            "applicant's income."),
        "source": "Indiana HCBS waiver standards (2026)",
        "figures": (2982.0,),
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
    sys_prompt = SYSTEM_PROMPT + (("\n\n" + instructions) if instructions
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
    sys_prompt = SYSTEM_PROMPT + (("\n\n" + instructions) if instructions
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
_FLASH_STATE = {"date": "", "count": 0}
_FLASH_LOCK = threading.Lock()


def _flash_calls_today():
    with _FLASH_LOCK:
        today = time.strftime("%Y-%m-%d")
        if _FLASH_STATE["date"] != today:
            _FLASH_STATE["date"] = today
            _FLASH_STATE["count"] = 0
        return _FLASH_STATE["count"]


def _flash_spend():
    """Reserve one Flash call; False when the daily budget is gone."""
    with _FLASH_LOCK:
        today = time.strftime("%Y-%m-%d")
        if _FLASH_STATE["date"] != today:
            _FLASH_STATE["date"] = today
            _FLASH_STATE["count"] = 0
        if _FLASH_STATE["count"] >= FLASH_DAILY_CAP:
            return False
        _FLASH_STATE["count"] += 1
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
THIRD_JUDGE_TIMEOUT = 60
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
        "key_file": "nvidia_key.txt",
        "key_env": "NVIDIA_API_KEY",
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


def _nim_text(base_url, key_env, key_file, model, system, user,
              temperature=0, max_tokens=2048, timeout=60,
              provider="nim"):
    """One chat call to a hosted OpenAI-compatible provider (the
    NIM layer's seats), over the keep-alive pool (v15 S3).
    Returns the reply text; raises RuntimeError when the seat
    cannot run (no key, 429, persistent 5xx, bad reply) so the
    caller degrades instead of failing. Politeness: ONE retry
    on 5xx, NO retry on 429."""
    key = _nim_key_for(key_env, key_file)
    if not key:
        raise RuntimeError(f"{provider} API key not configured "
                           f"({key_file} missing)")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    u = urlparse(base_url)
    host, path = u.netloc, u.path.rstrip("/") + "/chat/completions"
    last_err = None
    for attempt in (0, 1):
        try:
            status, raw = _https_post(
                host, path, payload,
                {"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
                timeout=timeout)
        except Exception as e:
            last_err = e
            if attempt == 0:
                continue
            raise RuntimeError(f"{provider} unavailable: {e}")
        if status == 429:
            raise RuntimeError(f"{provider} rate limited (HTTP 429)")
        if status in (500, 502, 503, 504):
            last_err = RuntimeError(f"{provider} HTTP {status}")
            if attempt == 0:
                continue
            raise last_err
        if status != 200:
            raise RuntimeError(f"{provider} HTTP {status}")
        try:
            data = json.loads(raw)
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
    """One verdict call to the standing NIM judge seat (v14.2's
    third seat, promoted in v15). Temperature 0."""
    content = _nim_text(THIRD_JUDGE_BASE_URL, THIRD_JUDGE_KEY_ENV,
                        THIRD_JUDGE_KEY_FILE, THIRD_JUDGE_MODEL,
                        system, user, temperature=0, max_tokens=2048,
                        timeout=THIRD_JUDGE_TIMEOUT,
                        provider=THIRD_JUDGE_PROVIDER)
    _third_count_one()
    return content


def nim_ask(model, question, hits, instructions="", timeout=None):
    """One NIM draft generation (v15 item 2 / S4): the same prompt
    shape as the local and Gemini drafters (SYSTEM_PROMPT +
    instructions, numbered excerpts), temperature 0.3. Returns
    (answer, prompt). Raises when the seat cannot run; callers
    fall back per the v15/v15.1 chains. `timeout` overrides
    DRAFTER_TIMEOUT (the v15.1 emergency seat uses a tighter
    bound: it answers in about a second warm, so a long silence
    means the provider is down, not slow)."""
    context = _numbered_context(hits)
    user_msg = f"Question: {question}\n\nDocument excerpts:\n{context}"
    sys_prompt = SYSTEM_PROMPT + (("\n\n" + instructions) if instructions
                                  else "")
    content = _nim_text(DRAFTER_BASE_URL, DRAFTER_KEY_ENV,
                        DRAFTER_KEY_FILE, model, sys_prompt, user_msg,
                        temperature=0.3, max_tokens=2048,
                        timeout=timeout or DRAFTER_TIMEOUT,
                        provider=DRAFTER_PROVIDER)
    # Hosted open models occasionally trail control-token debris
    # (kimi-k3's catalog smoke ended "<|close|>response"); it is
    # never answer text, so cut anything from the first such
    # marker onward.
    content = re.split(r"<\|[^>]{0,40}>", content)[0].strip()
    if not content:
        raise RuntimeError("NIM drafter returned an empty response")
    return content, sys_prompt + "\n\n" + user_msg


def _panel_info(judges, ms, third_status, third_error=None):
    """The judging-metadata panel record: which judges produced
    verdicts, per-seat wall-clock ms, and the NIM seat's status
    ('ok', 'no-key', 'unavailable', or 'not-run'). v15: also the
    count of claims resolved by the verdict cache (S2) and the
    refutation stage's shape (S1). In v15 the "third" record
    describes the NIM seat, which is now the standing second
    judge (see the NIM layer header)."""
    return {"judges": list(judges),
            "primary_ms": ms.get("primary"),
            "second_ms": ms.get("second"),
            "third_ms": ms.get("third"),
            "third": {"provider": THIRD_JUDGE_PROVIDER,
                      "model": THIRD_JUDGE_MODEL,
                      "status": third_status,
                      "error": third_error},
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


def _judge_excerpts(hits, cap=1000):
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
        f"{(hits[j].get('excerpt') or '')[:1000]}" for j in use)
    refs = claim.get("refutations") or ()
    if refs:
        text += ("\n\nPossibly contradicting material (retrieved "
                 "to test this claim; weigh it like the excerpts "
                 "above):\n" + "\n\n".join(
                     f"[R{k + 1}] {h.get('title', '')} "
                     f"({h.get('url', '')})\n"
                     f"{(h.get('excerpt') or '')[:1000]}"
                     for k, h in enumerate(refs)))
    hunt = claim.get("hunt") or ()
    if hunt:
        text += ("\n\nAdditional material retrieved for this "
                 "claim by targeted search (the original excerpts "
                 "did not settle it; weigh this like the excerpts "
                 "above):\n" + "\n\n".join(
                     f"[H{k + 1}] {h.get('title', '')} "
                     f"({h.get('url', '')})\n"
                     f"{(h.get('excerpt') or '')[:1000]}"
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
        else:
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
    disputed claims closed)."""
    if not indices or not _flash_spend():
        return None
    subset = [claims[i] for i in indices]
    scoped = _scoped_evidence(hits, subset,
                              labels=[i + 1 for i in indices])
    # v14.2: with the third judge seated, arbitration sees its
    # verdict too and the wording stops saying "two judges". The
    # dual-judge prompt text below is byte-identical to v14.1.
    panel_split = any(claims[i].get("third") for i in indices)
    who = "The judges disagree" if panel_split \
        else "Two judges disagree"
    lines = []
    for i in indices:
        c = claims[i]
        line = (
            f"{i + 1}. {c['text']}\n"
            f"   Gemini Lite: {c.get('lite') or '-'} "
            f"({c.get('lite_note', '')})\n"
            f"   Local 14B: {c.get('local') or '-'} "
            f"({c.get('local_note', '')})")
        if c.get("third"):
            line += (f"\n   Third judge ({THIRD_JUDGE_MODEL} via "
                     f"{THIRD_JUDGE_PROVIDER}): {c['third']}")
        if scoped is not None:
            line += f"\n   Evidence:\n{_claim_evidence(hits, c)}"
        lines.append(line)
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
            "the excerpts alone: supported, contradicted, or "
            "not_addressed. Your ruling is final. Reply with ONLY this "
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
    ($4,066.50) or common abbreviations (e.g., i.e., U.S.)."""
    text = (text or "").strip()
    if not text:
        return []
    text = re.sub(r"(\d)\.(\d)", r"\1<DEC>\2", text)
    for ab in ("e.g", "i.e", "U.S", "Mr", "Mrs", "Dr", "vs", "No"):
        text = text.replace(ab + ".", ab + "<DOT>")
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for p in parts:
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
    r"does not count|is not|are not)\b)", re.IGNORECASE)


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
            urls[h["url"]] = j
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
        for url in _LINK_RX.findall(s):
            if url in urls and urls[url] not in srcs:
                srcs.append(urls[url])
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


def _evidence_hash(hits, claim):
    parts = []
    src = [j for j in (claim.get("sources") or [])
           if 0 <= j < len(hits or ())]
    use = src if src else list(range(len(hits or ())))
    for j in use:
        h = hits[j]
        parts.append((h.get("url") or "") + "|" +
                     (h.get("excerpt") or "")[:200])
    for h in claim.get("refutations") or ():
        parts.append("R|" + (h.get("url") or "") + "|" +
                     (h.get("excerpt") or "")[:200])
    return hashlib.sha256(
        "\n".join(parts).encode("utf-8", "replace")).hexdigest()[:24]


def _verdict_cache_get(hits, claim):
    key = (_norm_claim_text(claim.get("text")),
           _evidence_hash(hits, claim))
    with _VERDICT_LOCK:
        return _VERDICT_CACHE.get(key)


def _verdict_cache_put(hits, claim, verdict):
    key = (_norm_claim_text(claim.get("text")),
           _evidence_hash(hits, claim))
    with _VERDICT_LOCK:
        if len(_VERDICT_CACHE) >= _VERDICT_CACHE_MAX:
            _VERDICT_CACHE.pop(next(iter(_VERDICT_CACHE)))
        _VERDICT_CACHE[key] = verdict


def _judge_round(question, hits, answer, hard, mech_block="",
                 corp=None, mechanisms=None, extra_claims=None):
    """One full judging round over a draft. v15 composition: the
    PRIMARY judge extracts claims and verdicts them (Gemini Lite;
    the NIM seat when Lite is down; the demoted local 14B only
    when both remotes are down). The SECOND judge verdicts the
    same list (the NIM seat by default; Lite when the NIM seat
    extracted). v15.1 (James's ruling, 2026-10-13): the local
    14B is NEVER seated on a remote ask — a failed second seat
    degrades to the surviving remote judge alone, and there is
    no local tiebreak. Refutation retrieval (item 1) runs
    CONCURRENTLY with the verdict stage (S1) and joins before
    resolution: claims whose refutation surfaced new material
    are re-verdicted against the union evidence, and a
    re-verdict that does not affirm them sends them to the
    disputed pool. Cached claims (S2) resolve from the verdict
    cache without new judge calls unless the join's refutation
    invalidates them. Disputes go to Flash (load-bearing, or
    any claim on hard questions); whatever Flash does not rule
    on fails closed on the worst verdict. Returns
    {mode, claims, flash_used, pass, errors, panel, refute_ms}
    where each claim carries lite/nim/local/flash/final
    verdicts (plus the v14.2-era 'third' alias of nim). mode is
    'panel' (both remote seats), 'dual', 'arbitrated',
    'gemini-only', 'nim-only', 'local-only', or 'none'."""
    errors = []
    panel_ms = {"primary": None, "second": None, "third": None,
                "cached_claims": 0, "refuted_claims": 0}
    nim_status = "ok" if _third_judge_key() else "no-key"
    nim_error = None

    # --- Stage 1: primary extraction + verdicts. Lite first,
    # then the NIM seat, then (both remotes down) the demoted
    # local 14B. The local GPU warm-up runs alongside ONLY when
    # a local seat is likely to be needed, which v15 knows only
    # after a remote failure — so there is no speculative warm-up
    # on the default path (that is the demotion's latency win).
    pt0 = time.monotonic()
    primary = _primary_judgment("lite", question, hits, answer,
                                mech_block)
    primary_engine = "lite"
    if not primary["available"]:
        errors.append("lite judge: " + str(primary["error"]))
        primary = _primary_judgment("nim", question, hits, answer,
                                    mech_block)
        primary_engine = "nim"
        if not primary["available"]:
            errors.append("nim judge: " + str(primary["error"]))
            nim_status = "unavailable"
            nim_error = str(primary["error"])
            if LOCAL_FALLBACK_JUDGE:
                primary = _primary_judgment("local", question, hits,
                                            answer, mech_block)
                primary_engine = "local"
    panel_ms["primary"] = int((time.monotonic() - pt0) * 1000)
    if not primary["available"]:
        if primary_engine == "local":
            errors.append("local judge: " + str(primary["error"]))
        return {"mode": "none", "claims": [], "flash_used": False,
                "pass": False, "errors": errors, "refute_ms": 0,
                "panel": _panel_info([], panel_ms, nim_status,
                                     nim_error)}
    claims = [{"text": c["text"], "load_bearing": c["load_bearing"],
               "lite": None, "nim": None, "third": None,
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
                       "lite": None, "nim": None, "third": None,
                       "local": None, "flash": None, "final": None,
                       "notes": [], "origin": ec.get("origin", "extra"),
                       "meta": _is_meta_claim(ec["text"]),
                       "sources": list(ec.get("sources") or []),
                       "refutations": []})
    # --- S2: verdict cache. Resolved claims (identical claim +
    # identical scoped evidence) resolve without new judge
    # calls; the join below can still invalidate a cached claim
    # whose refutation returns new material.
    cached_idx = set()
    for i, c in enumerate(claims):
        hit_v = _verdict_cache_get(hits, c)
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
        return _refutation_retrieve(corp, hits,
                                    [claims[i] for i in live_idx
                                     if claims[i]["load_bearing"]],
                                    mechanisms)

    st0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_second = pool.submit(_second_verdicts, second_engine,
                                 question, hits,
                                 [claims[i] for i in live_idx],
                                 mech_block) if second_engine \
            else None
        fut_refute = pool.submit(_run_refutation)
        if fut_second is not None:
            second = fut_second.result()
        refute_ms = fut_refute.result()
    panel_ms["second"] = int((time.monotonic() - st0) * 1000)
    judges = [primary_engine]
    if second["available"]:
        judges.append(second_engine)
        for sub_idx, v in second["verdicts"].items():
            claims[live_idx[sub_idx]][second_engine] = v["verdict"]
            if v["note"]:
                claims[live_idx[sub_idx]]["notes"].append(v["note"])
        for m in second["missed"]:
            claims.append({"text": m["text"],
                           "load_bearing": m["load_bearing"],
                           "lite": None, "nim": None, "third": None,
                           "local": None, "flash": None, "final": None,
                           "notes": [m["note"]] if m["note"] else [],
                           "origin": "missed",
                           "meta": _is_meta_claim(m["text"]),
                           "sources": [], "refutations": []})
            claims[-1][second_engine] = m["verdict"]
            live_idx.append(len(claims) - 1)
    elif second_engine:
        errors.append(second_engine + " judge: "
                      + str(second["error"]))
        if second_engine == "nim":
            nim_status = "unavailable"
            nim_error = str(second["error"])
    if "nim" in judges:
        panel_ms["third"] = panel_ms["second"]
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
    rejudge_engine = second_engine if second["available"] else None
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
    remote = [j for j in judges if j in ("lite", "nim")]
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
            c["final"] = c["cached"]
            continue
        vs = [c[k] for k in ("lite", "nim", "local") if c.get(k)]
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
            rulings = _flash_arbitrate(question, hits, claims,
                                       to_rule)
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
                vs = [claims[i][k] for k in ("lite", "nim", "local")
                      if claims[i].get(k)]
                if claims[i].get("refute_verdict") \
                        and claims[i]["refute_verdict"] != "supported" \
                        and "supported" not in vs:
                    claims[i]["final"] = "contradicted"
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
    if corp is not None:
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
                    ([second_engine] if second["available"] else [])):
                rv = _second_verdicts(eng, question, hits,
                                      [claims[i]], mech_block)
                if rv["available"] and 0 in rv["verdicts"]:
                    claims[i][eng] = rv["verdicts"][0]["verdict"]
                    verdicts.append(rv["verdicts"][0]["verdict"])
                    if rv["verdicts"][0]["note"]:
                        claims[i]["notes"].append(
                            rv["verdicts"][0]["note"])
            if len(verdicts) >= 2 and all(
                    v == "supported" for v in verdicts):
                claims[i]["final"] = "supported"
                claims[i]["notes"].append(
                    "targeted retrieval found the passage; "
                    "re-judged supported")
    # Cache the trustworthy resolutions (S2): finals decided by
    # two agreeing judges, a majority, or a Flash ruling.
    for i, c in enumerate(claims):
        if i in cached_idx or c.get("origin") == "conflict":
            continue
        vs = [c[k] for k in ("lite", "nim", "local") if c.get(k)]
        trustworthy = bool(c.get("flash")) or len(set(vs)) == 1 \
            and len(vs) >= 2
        if trustworthy and c["final"]:
            _verdict_cache_put(hits, c, c["final"])
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
            "panel": _panel_info(judges, panel_ms, nim_status,
                                 nim_error)}

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
        # v15: NIM drafting seats (kimi-k3 / the fast tier). The
        # served label names the NIM model honestly; callers own
        # the fallback chains when this raises.
        answer, prompt = nim_ask(model, question, hits,
                                 instructions=instructions)
        return answer, prompt, model + " (NIM API)", False, False
    try:
        answer, prompt = ollama_ask(model, question, hits,
                                    instructions=instructions)
        return answer, prompt, model, False, False
    except Exception:
        if model == LOCAL_MODEL:
            raise
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
                     "the judges, but the current draft omits it: "
                     "\"" + p + "\" — work it in accurately.")
    for cf in conflicts or ():
        lines.append(f"- The sources disagree on {cf['entry']}: "
                     f"${cf['registry_value']:,.2f} (verified "
                     f"registry) vs ${cf['corpus_value']:,.2f} "
                     f"({cf['source'] or 'a retrieved source'}). "
                     "State BOTH figures with where each comes "
                     "from; do not present one as settled.")
    return "\n".join(lines)


def _points_missing(points, draft):
    """v15 (item 4): which divergence-required points a draft
    fails to cover: every figure in the point must appear, and
    at least half its content words must be present."""
    low = (draft or "").lower()
    low_flat = low.replace(",", "")
    draft_toks = set(re.findall(r"[a-z]{4,}", low))
    missing = []
    for p in points or ():
        nums = re.findall(r"\d[\d,]*\.?\d*", p)
        num_ok = all(n.replace(",", "") in low_flat for n in nums)
        toks = {w for w in re.findall(r"[a-z]{4,}", p.lower())
                if w not in STOP}
        overlap = (len(toks & draft_toks) / len(toks)) if toks else 1.0
        if not (num_ok and overlap >= 0.5):
            missing.append(p)
    return missing


def _assemble_capped_partial(draft, jr, question, hits,
                             allowed_extra):
    """v15 (the 600s cap): build a Partial ONLY from sentences
    of the last judged draft that no failed claim touches, then
    re-run the figure checker on the assembly. Returns the
    text, or None when nothing verified survives (the caller
    then IDKs; the cap never ships unverified material)."""
    failed = [c for c in (jr or {}).get("claims", ())
              if c["final"] != "supported" and not c.get("meta")]
    kept = []
    for s in _split_sentences(draft):
        if s.lower().startswith("confidence:"):
            continue
        stoks = set(re.findall(r"[a-z0-9]{3,}", s.lower()))
        bad = False
        for c in failed:
            ctoks = set(re.findall(r"[a-z0-9]{3,}", c["text"].lower()))
            if ctoks and len(stoks & ctoks) / max(
                    1, min(len(stoks), len(ctoks))) >= 0.6:
                bad = True
                break
            ns, nc = _norm_claim_text(s), _norm_claim_text(c["text"])
            if ns[:40] and (ns[:40] in nc or nc[:40] in ns):
                bad = True
                break
        if not bad:
            kept.append(s)
    for _pass in range(3):
        if not kept:
            return None
        text = " ".join(kept)
        _a, ung = _check_figures(text, question, hits,
                                 extra_allowed=allowed_extra)
        if not ung:
            return text
        bad_toks = {str(v).replace(",", "") for v in ung}
        kept = [s for s in kept
                if not (set(re.findall(r"\d[\d,]*\.?\d*", s))
                        & bad_toks)]
    return None


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
    diverge = bool(ctx.get("diverge")) and ctx["engine"] == "local" \
        and not ctx.get("explicit_model")
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
        """Generate with the v15 fallback chains: a dead NIM seat
        never fails an ask (hard falls to the demoted 14B,
        simple to the 9B, whose own fallback _generate owns).
        v15.1: a failed NIM draft first retries on the NIM
        emergency seat (EMERGENCY_DRAFT_MODEL). One slow seat
        (the Kimi drafter timing out) is not "the remote path
        is down" and must not pull the ladder onto the local
        GPU; the local models generate only when NIM as a
        whole cannot run."""
        try:
            return _generate(engine, model, ctx["unit_q"],
                             ctx["hits"], instr)
        except Exception:
            if engine in ("nim", "nimfast"):
                if model != EMERGENCY_DRAFT_MODEL:
                    try:
                        ans, prm = nim_ask(
                            EMERGENCY_DRAFT_MODEL, ctx["unit_q"],
                            ctx["hits"], instr, timeout=120)
                        return (ans, prm,
                                EMERGENCY_DRAFT_MODEL
                                + " (NIM API)", False, False)
                    except Exception:
                        pass
                if engine == "nim" or hard:
                    _unload_others(ROUTED_MODEL)
                    return _generate("local", ROUTED_MODEL,
                                     ctx["unit_q"], ctx["hits"],
                                     instr)
                return _generate("local", LOCAL_MODEL,
                                 ctx["unit_q"], ctx["hits"], instr)
            raise

    for rung in range(3):  # draft + two redrafts (James's decision)
        if deadline and time.monotonic() > deadline:
            cap_hit = True
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
                # Item 4: the independent second draft (the
                # oracle) generates in parallel with draft A.
                # v15.1: the oracle drafts on NIM
                # (DIVERGENCE_ORACLE_MODEL: strong, hosted, and
                # independent of the Kimi draft A), NOT on the
                # local 14B as in v15.0 — the local models stay
                # cold on the remote path.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fa = pool.submit(_try_generate, eng, mdl, instr)
                    fb = pool.submit(_generate, "nim",
                                     DIVERGENCE_ORACLE_MODEL,
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
        ctx["_served"], ctx["_dg"], ctx["_fb"] = served, dg, fb
        ctx["_prompt"] = prompt
        _a, ungrounded = _check_figures(
            draft, ctx["question"], ctx["hits"],
            extra_allowed=ctx["allowed_extra"])
        mech_failures = _mech_required_failures(
            draft, ctx["mechanisms"], ctx.get("csra_comp"))
        quantities = _check_quantities(draft, ctx["question"])
        parsed = _parse_citations(draft, ctx["hits"])
        uncited_lb = parsed["uncited_lb"]
        c_claims, c_forced = _conflict_claims(conflicts, draft)
        extra_claims = parsed["cited_claims"] + c_claims
        if served and served.startswith(LOCAL_MODEL):
            # A resident 9B draft is done; free its VRAM before
            # judging in case a local fallback seat is needed.
            _unload_others(ROUTED_MODEL)
        _set_stage(f"judging round {rung + 1}")
        jt0 = time.monotonic()
        jr = _judge_round(ctx["unit_q"], ctx["hits"], draft,
                          hard, judge_block, corp=ctx.get("corp"),
                          mechanisms=ctx["mechanisms"],
                          extra_claims=extra_claims)
        judge_ms += int((time.monotonic() - jt0) * 1000)
        refute_ms += jr.get("refute_ms", 0)
        judged_draft = draft
        if c_forced:
            for c in jr["claims"]:
                if c.get("origin") == "conflict" \
                        and c["text"] in c_forced:
                    c["final"] = "contradicted"
            jr["pass"] = bool(jr["claims"]) and all(
                c["final"] == "supported" for c in jr["claims"]
                if not c.get("meta"))
        if rung == 0 and diverge and draft_b_text:
            # Divergence mapping: B-only load-bearing claims the
            # NIM extractor confirms become required points the
            # final draft must cover; contested overlaps are
            # recorded (their A-side claims already went through
            # refutation as load-bearing claims).
            bj = _primary_judgment("nim", ctx["unit_q"],
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
                required_points = points
                divergence = {"b_claims": len(bj["claims"]),
                              "required_points": points,
                              "contested": contested}
        missing_points = _points_missing(required_points, draft)
        if jr["mode"] == "none":
            break  # no judge can run: redrafting blind helps nothing
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not missing_points:
            break  # every gate passed: ship this draft
        objections = _build_objections(
            jr, mech_failures, ungrounded, uncited_lb, quantities,
            missing_points,
            conflicts if any(c.get("origin") == "conflict"
                             and c["final"] != "supported"
                             for c in jr["claims"]) else ())
        draft = None if rung < 2 else draft
    shipped = False
    if draft is not None and jr is not None and jr["mode"] != "none":
        if jr["pass"] and not ungrounded and not mech_failures \
                and not quantities and not uncited_lb \
                and not missing_points:
            shipped = True
        elif deadline and time.monotonic() > deadline:
            cap_hit = True  # no budget left for the final ruling
        else:
            # Final rung (plan section 5): Flash rules once on the
            # last draft's failed claims; its ruling is final.
            failed = [i for i, c in enumerate(jr["claims"])
                      if c["final"] != "supported"
                      and c["flash"] is None and not c.get("meta")]
            if failed and not ungrounded and not mech_failures \
                    and not quantities and not uncited_lb \
                    and not missing_points:
                rulings = _flash_arbitrate(ctx["unit_q"], ctx["hits"],
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
            "figure_check": {"ungrounded": list(ungrounded),
                             "regenerated": rungs > 1},
            "divergence": divergence}
    if shipped:
        answer, _fixes = _check_arithmetic(draft)
        badge = "verified" if jr["mode"] in ("dual", "arbitrated",
                                             "panel") \
            else "verified_single"
        return dict(base, answer=answer, badge=badge,
                    judging={"mode": jr["mode"],
                             "flash_used": jr["flash_used"],
                             "errors": jr["errors"],
                             "panel": jr.get("panel")},
                    claims=jr["claims"], missing=[], gate=None)
    if cap_hit and judged_draft is not None and jr is not None \
            and jr["mode"] != "none":
        # The cap stopped the ladder: ship ONLY the claims the
        # panel already verified, as a Partial.
        assembled = _assemble_capped_partial(
            judged_draft, jr, ctx["question"], ctx["hits"],
            ctx["allowed_extra"])
        if assembled:
            badge = "partial" if jr["mode"] in (
                "dual", "arbitrated", "panel") else "partial_single"
            cut = [c["text"] for c in jr["claims"]
                   if c["final"] != "supported" and not c.get("meta")]
            return dict(base, answer=assembled, badge=badge,
                        judging={"mode": jr["mode"],
                                 "flash_used": jr["flash_used"],
                                 "errors": jr["errors"],
                                 "panel": jr.get("panel")},
                        claims=jr["claims"],
                        missing=["the ask reached its time budget "
                                 f"({ASK_TIME_CAP_S} seconds); only "
                                 "panel-verified claims are shown",
                                 *[f"not verified in time: \"{t}\""
                                   for t in cut[:4]]],
                        gate=None)
    # IDK: assemble what is missing from the failed gates.
    missing, gate = [], "judging"
    if cap_hit:
        gate = "time"
        missing.append("the ask reached its time budget "
                       f"({ASK_TIME_CAP_S} seconds) before a "
                       "verified answer could be assembled")
    if gen_error:
        missing.append("the answering model could not run "
                       f"({gen_error})")
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
    <label for="model">Model</label>
    <select id="model"><option>Loading models...</option></select>
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
async function loadModels(){
  try{
    const r = await fetch('/models');
    const d = await r.json();
    const sel = document.getElementById('model');
    sel.innerHTML = '';
    d.models.forEach(m=>{
      const o = document.createElement('option');
      o.value = m; o.textContent = m;
      if(m === 'qwen3.5:9b') o.selected = true;
      sel.appendChild(o);
    });
    if(!d.models.length) sel.innerHTML = '<option>No Ollama models found</option>';
  }catch(e){
    document.getElementById('model').innerHTML = '<option>Could not reach Ollama</option>';
  }
}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const model = document.getElementById('model').value;
  const corpus = document.getElementById('corpus').value;
  const btn = document.getElementById('ask');
  btn.disabled = true;
  document.getElementById('spin').style.display = 'block';
  document.getElementById('answer').style.display = 'none';
  const spin = document.getElementById('spin');
  try{
    // v15: job-based flow — submit the ask as a job and poll for
    // the result, showing the server's live stage. Falls back to
    // the synchronous request if job submission fails.
    const payload = {question:q, model:model, corpus:corpus, fresh:document.getElementById('fresh').checked};
    let d = null;
    try{
      const r0 = await fetch('/ask?async=1', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const j0 = await r0.json();
      if(!j0.job_id) throw new Error('no job id');
      spin.textContent = 'Working: ' + (j0.stage || 'retrieving') + '...';
      for(let i = 0; i < 400 && !d; i++){
        await new Promise(res => setTimeout(res, 2000));
        const rp = await fetch('/ask/result/' + j0.job_id);
        const jp = await rp.json();
        if(jp.status === 'done'){ d = jp.result; }
        else if(jp.status === 'error'){ throw new Error(jp.error || 'job failed'); }
        else { spin.textContent = 'Working: ' + (jp.stage || 'retrieving') + '...'; }
      }
      if(!d) throw new Error('timed out waiting for the answer');
    }catch(jobErr){
      if(d) throw jobErr;
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
      verified_single: ['no', 'Verified · one judge only'],
      partial: ['no', 'Partial · some parts could not be verified'],
      partial_single: ['no', 'Partial · one judge only'],
      idk: ['err', 'IDK · not verified']
    };
    const bm = badgeMap[d.badge];
    document.getElementById('answerBadge').innerHTML = bm
      ? '<span class="badge ' + bm[0] + '">' + bm[1] + '</span>' : '';
    const vn = document.getElementById('verifyNote');
    if(d.judging){
      let note = 'Verification: ' + d.judging.mode;
      if(d.judging.panel && (d.judging.panel.judges||[]).length)
        note += ' · ' + d.judging.panel.judges.length + ' judge(s)';
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
      b.className = 'badge ' + (h.verified ? 'ok' : 'no');
      b.textContent = h.verified ? 'verified' : 'unverified';
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
loadModels();
</script>
</body>
</html>
"""


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
    shim.headers = headers
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
                        "hybrid_active": len(EMBEDDINGS) > 0})
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
        try:
            length = int(self.headers.get("Content-Length", 0))
            # DoS protection: reject bodies over 100KB
            MAX_BODY = 100 * 1024
            if length > MAX_BODY:
                self._json({"error": f"request too large (max {MAX_BODY} bytes)"}, 413)
                return
            raw = self.rfile.read(length)
            req = json.loads(raw)
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
            t_start = time.monotonic()
            _set_stage("retrieving")
            # v15: the per-ask wall-clock cap (config
            # ASK_TIME_CAP_S); the ladder and the units loop
            # consult this deadline and never ship unverified.
            deadline = t_start + ASK_TIME_CAP_S
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
                       use_rewrite, use_rerank)
            if not fresh:
                cached_answer = _cache_get(_ASK_CACHE, ask_key)
                if cached_answer:
                    self._json(cached_answer)
                    return
            if not model and not use_gemini:
                # No model named: the routed local default answers.
                model = routed_model
            if not model:
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
                          "model": served, "prompt": full_prompt,
                          "fallback": fallback, "downgraded": downgraded,
                          "discovery": discovery, "timing": timing,
                          "registry": [e["id"] for e in registry_entries],
                          "routed": route_reason or None,
                          "figure_check": figure_check,
                          "parts": len(parts), "badge": badge,
                          "judging": judging,
                          "verification": verification,
                          "mechanisms": [m["id"] for m in mechanisms_all]}
                _cache_put(_ASK_CACHE, ask_key, result)
                self._json(result)

            no_judging = {"mode": "none", "flash_used": False,
                          "flash_calls_today": _flash_calls_today(),
                          "panel": _panel_info(
                              [], {"primary": None, "second": None,
                                   "third": None},
                              "no-key" if not _third_judge_key()
                              else "not-run")}
            if not hits:
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
                for part in parts:
                    p_reg = _registry_matches(part)
                    p_mechs = _mechanism_matches(part)
                    part_reg_ids += [e["id"] for e in p_reg]
                    part_mech_ids += [m["id"] for m in p_mechs]
                    p_hits = []
                    if EMBEDDINGS:
                        p_hits = hybrid_search(
                            part, corpus=corp, n=6,
                            use_rewrite=use_rewrite,
                            use_rerank=use_rerank)
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
                        "instructions": "",
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
            units = []
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
                           and model != LOCAL_MODEL)}
                if time.monotonic() > deadline:
                    # Cap reached between parts: this part is an
                    # IDK with a time gate, never a rushed answer.
                    unit = {
                        "answer": None, "badge": "idk", "served": None,
                        "fallback": False, "downgraded": False,
                        "prompt": "", "rungs": 0, "judge_ms": 0,
                        "refute_ms": 0, "cap_hit": True,
                        "figure_check": {"ungrounded": [],
                                         "regenerated": False},
                        "judging": {"mode": "none",
                                    "flash_used": False,
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
                else:
                    unit = _run_unit(ctx)
                unit["part"] = spec["part"]
                unit["hits"] = spec["hits"]
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
                if all(b == "verified" for b in badges):
                    overall_badge = "verified"
                elif all(b in ("verified", "verified_single")
                         for b in badges) and \
                        any(b == "verified_single" for b in badges):
                    overall_badge = "verified_single"
                elif all(b == "idk" for b in badges):
                    overall_badge = "idk"
                elif any(b == "verified" for b in badges):
                    overall_badge = "partial"
                else:
                    overall_badge = "partial_single"
                if global_mechs:
                    g_fail = _mech_required_failures(
                        answer_text, global_mechs, csra_comp)
                    if g_fail:
                        labels = "; ".join(f["label"] for f in g_fail)
                        answer_text += (
                            "\n\nNote: the retrieved documents do not "
                            "fully settle every part of this question "
                            f"(missing: {labels}).")
                        if overall_badge == "verified":
                            overall_badge = "partial"
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
                               "provider": THIRD_JUDGE_PROVIDER,
                               "model": THIRD_JUDGE_MODEL,
                               "status": third_status},
                           "third_calls_today": _third_calls_today(),
                           "cached_claims": sum(
                               (u["judging"].get("panel") or {})
                               .get("cached_claims", 0)
                               for u in units),
                           "refuted_claims": sum(
                               (u["judging"].get("panel") or {})
                               .get("refuted_claims", 0)
                               for u in units)}}
            verification = [{
                "part": u["part"], "badge": u["badge"],
                "claims": len(u["claims"]),
                "failed_claims": [c["text"] for c in u["claims"]
                                  if c["final"] != "supported"
                                  and not c.get("meta")],
                "gate": u["gate"], "rungs": u["rungs"],
                "judging_mode": u["judging"]["mode"],
                "divergence": u.get("divergence")} for u in units]
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
            self._json({"error": str(e)[:300]}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"FSSA Q&A server: {len(CORPUS_IN)} IN docs + {len(CORPUS_FED)} federal docs on port {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
