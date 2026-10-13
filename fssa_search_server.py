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
"""
import json
import os
import re
import sys
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

def rewrite_query(question):
    """Use the single local model to rewrite a question into better search terms."""
    try:
        prompt = (f"Rewrite this question as 5-8 keywords for document search. "
                  f"Output ONLY the keywords, comma-separated, no other text.\n\n"
                  f"Question: {question}\nKeywords:")
        payload = json.dumps({
            "model": LOCAL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0},
        }).encode()
        req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        text = _clean_llm_list_output(data["message"]["content"])
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
        payload = json.dumps({
            "model": LOCAL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0},
        }).encode()
        req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        text = data["message"]["content"].strip().split("\n")[0]
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
    """Use the single local model to rerank search hits by relevance to the query."""
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
        payload = json.dumps({
            "model": LOCAL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False,
            "options": {"temperature": 0},
        }).encode()
        req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        text = _clean_llm_list_output(data["message"]["content"])
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


def _registry_matches(question):
    """Registry entries whose trigger groups all hit on the question."""
    q_low = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z]+", q_low))
    matched = []
    for entry in VERIFIED_FIGURES:
        fires = True
        for group in entry["groups"]:
            if q_tokens & group.get("tokens", set()):
                continue
            if any(p in q_low for p in group.get("phrases", ())):
                continue
            fires = False
            break
        if fires:
            matched.append(entry)
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
    RuntimeError when this model is unusable right now."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{api_model}:generateContent?key={GEMINI_API_KEY}")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    last_err = None
    for delay in (0, 3, 8):
        if delay:
            time.sleep(delay)
        _gemini_space()
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=45).read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 429 and ("RESOURCE_EXHAUSTED" in body
                                  or "quota" in body.lower()):
                raise _GeminiQuota(f"{api_model} daily quota exhausted")
            if e.code == 404:
                raise RuntimeError(f"{api_model} not available (HTTP 404)")
            if e.code in (429, 500, 502, 503, 504):
                last_err = e
                continue
            raise RuntimeError(f"{api_model} HTTP {e.code}: {body[:200]}")
        except Exception as e:  # network blips: also retryable
            last_err = e
            continue
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            raise RuntimeError(
                f"Gemini API unexpected response: {str(data)[:200]}")
        if not content:
            raise RuntimeError("Gemini returned an empty response")
        return content
    raise RuntimeError(f"{api_model} unavailable after retries: {last_err}")


def gemini_ask(model, question, hits, instructions=""):
    """Ask the Gemini API, best model first, downgrading down GEMINI_CHAIN
    only when a model is unavailable. Returns (answer, prompt, served_model,
    downgraded). Raises if every Gemini model fails; the caller then falls
    back to the local model."""
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key not configured (gemini_key.txt missing)")
    context = "\n\n".join(_fmt_hit(h) for h in hits)
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
    context = "\n\n".join(_fmt_hit(h) for h in hits)
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
        "keep_alive": "30m",
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
      <h2>Answer</h2>
      <div id="answerText"></div>
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
  try{
    const r = await fetch('/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q, model:model, corpus:corpus, fresh:document.getElementById('fresh').checked})
    });
    const d = await r.json();
    document.getElementById('answerText').textContent = d.answer || ('Error: ' + (d.error||'unknown'));
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
            req = json.loads(self.rfile.read(length))
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
            if not hits:
                self._json({"answer": "No matching documents found in the selected corpus.",
                            "hits": []})
                return
            t_retrieved = time.monotonic()
            served, fallback, downgraded = model, False, False
            # v13: the local model that actually generates (None while
            # a Gemini answer stands); the figure-check regeneration
            # re-asks this same engine.
            answer_model = None
            if use_gemini:
                try:
                    # NOTE: no excerpt cap for the API path. A 10-cap
                    # (tried briefly in v12) cut the spousal chunk out
                    # of bundled questions and Lite answered "not in
                    # the documents"; excerpt count is already bounded
                    # by the retrieval design.
                    answer, full_prompt, gserved, downgraded = gemini_ask(
                        model, question, hits,
                        instructions=instructions)
                    served = gserved + " (API)"
                    if downgraded:
                        served += " (downgraded: best Gemini unavailable)"
                except Exception:
                    # Never dead-end on a Gemini outage: answer with the
                    # local model and say so in the response metadata.
                    answer, full_prompt = ollama_ask(
                        LOCAL_MODEL, question, hits,
                        instructions=instructions)
                    served = LOCAL_MODEL + " (fallback: Gemini unavailable)"
                    fallback = True
                    answer_model = LOCAL_MODEL
            else:
                # Use local 9b model if user didn't specify (avoids VRAM thrashing)
                if model in ("", "default"):
                    model = LOCAL_MODEL
                gen_model = routed_model if route_reason else model
                if route_reason:
                    # v13: free the 9B's VRAM before the 14B loads;
                    # holding both on the 8GB card is what produced
                    # the v12 bake-off HTTP 500s.
                    ollama_unload(LOCAL_MODEL)
                try:
                    answer, full_prompt = ollama_ask(
                        gen_model, question, hits,
                        instructions=instructions)
                    served = gen_model
                    answer_model = gen_model
                except Exception:
                    if not route_reason:
                        raise
                    # The routed 14B failed: the 9B answers, and the
                    # metadata says so.
                    answer, full_prompt = ollama_ask(
                        LOCAL_MODEL, question, hits,
                        instructions=instructions)
                    served = (LOCAL_MODEL + " (fallback: routed "
                              + ROUTED_MODEL + " unavailable)")
                    fallback = True
                    answer_model = LOCAL_MODEL
            # v11: figure provenance first (which dollar figures in
            # the answer trace to the excerpts), then the v9 equation
            # verification; both notes ride along in the answer text.
            # v13: the registry figures and the computed CSRA values
            # count as supported, and an unsupported figure now forces
            # ONE regeneration naming the problem figure (never two).
            allowed_extra = _registry_values(registry_entries)
            if csra_comp:
                allowed_extra += [csra_comp["assets"], csra_comp["half"],
                                  csra_comp["csra"]]
            answer, ungrounded = _check_figures(
                answer, question, hits, extra_allowed=allowed_extra)
            figure_check = {"ungrounded": list(ungrounded),
                            "regenerated": False}
            if ungrounded:
                bad = ", ".join(_fmt_money(v) for v in ungrounded[:4])
                fix = ((instructions + "\n\n") if instructions else "") \
                    + ("CORRECTION: a previous draft of this answer "
                       "used these dollar figures, which appear in "
                       "NONE of the excerpts or the verified figures "
                       "above: " + bad + ". Do not use them. Answer "
                       "the question again using only figures from "
                       "the excerpts or the verified figures.")
                try:
                    if answer_model is None:
                        a2, p2, gs2, dg2 = gemini_ask(
                            model, question, hits, instructions=fix)
                        served2 = gs2 + " (API)"
                        if dg2:
                            served2 += (" (downgraded: best Gemini "
                                        "unavailable)")
                    else:
                        a2, p2 = ollama_ask(
                            answer_model, question, hits,
                            instructions=fix)
                        served2 = served
                    # The regenerated answer replaces the draft (and
                    # its caution note); it is re-checked, and if it
                    # still invents a figure the note stays. No second
                    # regeneration, ever.
                    answer, full_prompt, served = a2, p2, served2
                    figure_check["regenerated"] = True
                    answer, ungrounded = _check_figures(
                        answer, question, hits,
                        extra_allowed=allowed_extra)
                    figure_check["ungrounded"] = list(ungrounded)
                except Exception:
                    pass  # keep the first answer and its caution note
            answer, _arith_fixes = _check_arithmetic(answer)
            t_end = time.monotonic()
            timing = {"retrieve_ms": int((t_retrieved - t_start) * 1000),
                      "generate_ms": int((t_end - t_retrieved) * 1000),
                      "total_ms": int((t_end - t_start) * 1000)}
            print(f"[ask] served={served} route={route_reason or '-'} "
                  f"reg={[e['id'] for e in registry_entries]} "
                  f"retrieve={timing['retrieve_ms']}ms "
                  f"generate={timing['generate_ms']}ms "
                  f"total={timing['total_ms']}ms q={question[:60]!r}",
                  flush=True)
            result = {"answer": answer, "hits": hits, "model": served,
                      "prompt": full_prompt, "fallback": fallback,
                      "downgraded": downgraded, "discovery": discovery,
                      "timing": timing,
                      "registry": [e["id"] for e in registry_entries],
                      "routed": route_reason or None,
                      "figure_check": figure_check,
                      "parts": len(parts)}
            _cache_put(_ASK_CACHE, ask_key, result)
            self._json(result)
        except Exception as e:
            self._json({"error": str(e)[:300]}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"FSSA Q&A server: {len(CORPUS_IN)} IN docs + {len(CORPUS_FED)} federal docs on port {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
