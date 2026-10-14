# FSSA Search Server v20.2 — STATUS

Built 2026-10-14 (VM only; no PC access, no live API calls, no spend).
Source copied from `~/workspace/fssa-qa/v20/` (the deployed v20.1 tree,
live MD5 6d8f77aa) into `~/workspace/fssa-qa/v20.2/`, then modified.
v20's own STATUS remains in the v20 directory; this file documents
ONLY the v20.2 delta. The file deploys as-is in the deploy stage
that follows: `fssa_search_server.py` (10,059 lines; v20.1 was
9,714).

Reason for the version: (1) the first live v20 ask (the eyeglasses
question, 2026-10-14) IDK'd because the IHCP Vision Services module
held the exact answer and never reached the drafter — retrieval
fixes; (2) that diagnosis also showed what the records could NOT
say (why seat B died, whether the module entered the rerank
window) — observability; (3) the Copilot fair point on boot-time
consistency; (4) the comment scrub that the GitHub repo format
pass inventoried but could not apply without breaking byte
identity with the deployed file.

## Item 1 — retrieval fixes (eyeglasses root cause)

Diagnosis recap (from the ask record pulled under the screenshot
rule): the Vision Services module (PROMOD00051) ranked #3 on the
bare terms, #1-2 on "vision services", and ABSENT from the top 6
on the natural question; of the 8 excerpts served, zero contained
"eyeglass". Three headwinds, all addressed:

- **1a. Duration/frequency triggers (line 1264).** `"how often"`
  and `"how frequently"` added to the duration-boost trigger
  tuple (joining "how long", "months", "years", "period",
  "deadline", "when does", "expire", "lookback", "look-back").
  The eyeglasses question fired no duration lift in v20.
- **1b. Vision topic bridge (line 836, in `_TOPIC_BRIDGES`).**
  Tokens {eyeglasses, eyeglass, glasses, spectacles}; text
  triggers ("vision services", "eye exam", "eye exams");
  phrases `(("eyeglasses one pair frequency",
  "one pair of eyeglasses"), ("vision services eyeglasses
  coverage", "eyeglasses"))`; scope None, no requirement gate.
  Bare "vision"/"optical" triggers were deliberately avoided
  (FSSA mission/vision org-page noise). NOTE the first phrase
  was re-aimed during testing: the obvious
  "eyeglasses frequency limits" / pin-term "frequency limits"
  pins the FSSA Resource Guide instead (its chunk contains the
  string "frequency limits" about other services — verified in
  the offline smoke). "one pair of eyeglasses" occurs only in
  the module's frequency text, and its phrase search ranks the
  module's frequency chunk #0.
- **1c. Provider Reference Module demotion exemption
  (line 1320).** The v13 fused-ranking demotion (−0.03 for
  titles containing "provider") now skips titles containing
  "provider reference module". The IHCP modules are primary
  sources; everything else keeps the demotion. Known
  remainder, NOT changed (out of the item's scope): the
  keyword leg's own −15 provider_penalty in `search()` still
  applies to module chunks inside raw keyword ranking; bridge
  pins are the designed counterweight and the pool record
  (item 2) now shows the effect per call.

**Offline A/B (keyword-only path, identical corpus + call,
no embeddings on the VM):** v20 file: Vision module ABSENT
from the top 8 on "How often are eyeglasses covered for
adults?" v20.2 file: module at ranks 0 AND 1, rank 0 being
the pinned frequency chunk ("One pair of eyeglasses per
12-month period — for members 21 years of age or older, one
pair every five years"). Live confirmation (with embeddings
+ rerank) comes with the deploy stage's retest.

## Item 2 — observability (ask records)

### (i) Per-seat failure reasons
`_judge_seat_text` (the single choke point for judge-seat
legs) now records, on the ask context's event stream:

- `{"kind": "seat_fail", "job", "model", "slot",
  "latency_s", "timed_out", "error"}` for EVERY failed leg
  (helpers `_note_seat_fail` line 4597, `_note_seat_down`
  line 4615; wired into the except block, which previously
  computed the timeout heuristic and discarded the exception
  string). `error` is `"ExceptionClass: message"` truncated
  to 200 chars; `timed_out` keeps the exact v20 heuristic
  (latency ≥ 0.9 × leg timeout, or "timed out"/"timeout" in
  the message).
- `{"kind": "seat_down", "job", "error"}` at all three
  terminal points: all candidates failed (with the last
  error), no candidate available (with the tried list), and
  the unit-budget shed.

Seat A engines that do not pass through `_judge_seat_text`
(Lite direct, Lite via OpenRouter, the Flash rung) already
surface error strings via `_primary_judgment` into the judge
round's `errors` list — which v20.1 kept only in the HTTP
response. The record now persists them (see judge_errors
below), so both surfaces are covered.

### (ii) Retrieval candidate pool
`hybrid_search` snapshots the fused pool on every ask-mode
(boosts=True) call, BEFORE the internal `_fused` key is
stripped: pre-strip snapshot line 1382, assembly + note
lines ~1460-1500, recorder `_retr_pool_note` line 4577.
Snapshots ride the ask context (`_AskContext.retr_pools`,
slot added line 4446; cap 6 calls per ask, 28 entries per
pool) and are persisted on the record (below). A cache hit
records a marked stub (`"cached": true` + the served set).
/search calls (boosts=False) record nothing.

Pool entry fields (exact): `t` title[:70], `u` url, `s`
fused score (post-lift, 4dp), `kw`/`vec`/`kw2`/`br` =
0-based rank in the keyword / vector / rewrite-keyword /
bridge list or null, `w` = sat in the top-16+ rerank
window (the diversity-filtered candidate list,
`hits[:max(12, 2n)]`), `sel` = final position in the
served result or null. Entries cover the whole window
plus any bridge pin selected from outside it.

### Exact new ask-record fields
Top level on the `"kind": "ask"` record (construction
line ~9492):
- `"events"`: the ask's full event list (existing event
  kinds — seat successes, local_load, groq_shed, etc. —
  plus the new `seat_fail` / `seat_down`).
- `"retrieval"`: the list of pool snapshots, each
  `{"q": query[:80], "n": n, "corp": "in"|"federal"|"both",
  "pool": [entries]}` or the cached stub
  `{"q", "n", "cached": true, "pool": [{"t","u","sel"}]}`.

Per unit (inside `"units[]"`, line ~9527):
- `"events"`: the unit's event slice (was already on the
  unit object; now persisted).
- `"judge_errors"`: the unit judging `errors` list
  (previously response-only).
- `"retrieval"`: pool snapshots from hybrid_search calls
  that ran inside the unit (`_run_unit` base dict,
  line 8546; usually empty — the main retrieval happens
  before units and lands at record level).

## Item 3 — boot-time consistency check

`_run_consistency_check()` (line 1970), invoked in
`__main__` before serving (line 10045); result exposed at
`/health` as `"consistency": {"ok": bool, "detail": {...}}`
(line 9079). WARN only: on mismatch it prints a loud
`[consistency] WARNING:` line with the full detail JSON;
it can never refuse or delay boot beyond the check itself
(0.6s cold over both full corpora, measured).

DESIGN FINDING (read before trusting the first live
reading): embeddings are NOT built with the server's
retrieval chunker. The generation pipeline
(gen_embeddings_stream.py, and the incremental twin) uses
its own chunker: 600-char windows, 100 overlap,
section-aware over the same `_split_sections`, docs under
200 chars skipped, first doc per URL. A manifest built
from `_doc_chunks` yields 18,945 chunks for corpora whose
embeddings file holds 73,014 rows — a false alarm by
construction. The check therefore replicates the
GENERATION chunker (`_emb_window` / `_emb_chunk_count`,
line 1957; `_split_sections` parity verified line-by-line
against the generator). Against the v19 corpora the
replica yields **73,454 manifest rows vs the live file's
73,014 (delta −440, 0.6%)** — generation-vintage noise
(embed failures are skipped per-chunk at build time; the
append-only build file carries earlier-vintage chunks;
some docs were embedded by the incremental generator's
plain-window variant). The incident signature this check
exists for is thousand-scale (the v19 dedupe resurrection
was +2,244).

Detail fields (exact): `embeddings_rows`, `manifest_rows`,
`delta_rows`, `dedupe_skip_entries_applicable`,
`manifest_rows_after_dedupe_skips`, `docs_in`,
`docs_federal`, `matrix_rows`, `orphan_urls`,
`missing_urls`, `mismatched_urls` (per-URL vs the raw
manifest), `examples` (first 3 of each). `ok` = totals
match AND no orphan/missing/mismatched URLs AND the
matrix row count equals the chunk list length.
dedupe_skip.json is read when present (its applicable
entries are counted and reported) but the verdict
compares against the RAW manifest: on the live build the
skip list's indices address an older build file, so
adjusting by it would double-count a removal already
applied (raw delta −440 vs adjusted delta ≈ −1,800
against live). EXPECTATION FOR THE DEPLOY STAGE: the
first live /health will very likely read `ok: false`
with delta ≈ −440 and a few hundred per-URL mismatches —
that is the documented healthy noise floor, not an
incident; a delta in the thousands, or orphan/missing
counts in the hundreds, is.

## Item 4 — code scrub

- Server file: all **55 lines** carrying the operator's first name (54
  possessive attributions + possessive-adjacent forms)
  rewritten to neutral wording ("operator ruling",
  "operator decision", "the operator's layer model",
  "standing"-style phrasing), preserving meaning and
  dates. Applied via an explicit ordered replacement map
  (26 phrase pairs, longest-first, sentence-level special
  cases first), then verified: a grep for the name returns 0, and a
  full diff against v20 confirms every scrub change sits
  in a comment or docstring — no code token, prompt, or
  string literal touched. Post-scrub sweep for the wider
  identifier set (family names, town, account and email
  identifiers, Tailscale IP, street address, agency name): 0 hits.
- `apply_dedupe.py` (in this dir): the hardcoded
  `BASE = Path(r"C:\Users\<operator>\odysseus\data\fssa-server")`
  is now a parameter — `FSSA_SERVER_DIR` env var, else
  first CLI argument, else the script's own directory
  (it runs from a copy inside the server dir). Compiles;
  the rest of the file is byte-identical to v20's copy.
- `growth_loop.py`: **not present in the v20.2 dir**
  (it never lived in the version dirs; copies exist at
  `~/workspace/fssa-qa/v15/growth_loop.py` — stale — and
  at the GitHub repo root). Nothing to scrub here; the
  repo copy's 4 comment mentions are the repo stage's
  business, flagged here so it is not lost.

## Item 5 — PyMuPDF errand (parse_pdfs.py environment)

Finding: **the interpreter/environment that ran
parse_pdfs.py cannot be determined from VM evidence, and
no PyMuPDF version can be stated.** Evidence, all
VM-side:
- `~/workspace/fssa-qa/parse_pdfs.py` is a VM script:
  shebang `#!/usr/bin/env python3`, `import pymupdf`,
  hardcoded VM paths (`~/workspace/fssa-qa/corpus.json`,
  `pdf_urls.json`; both files exist; script mtime
  2026-10-12, the original corpus-build era). It is NOT
  a PC script (the format pass's search of PC Pythons
  3.12/3.13 was therefore moot for this file).
- Its sibling corpus scripts demonstrably ran on this VM
  under CPython 3.12 (`__pycache__/*.cpython-312.pyc`
  for crawl_ssa, gen_embeddings_stream,
  parse_federal_crawl, etc.), and the project venv
  `~/workspace/fssa-qa/.venv` is CPython 3.12.3.
- BUT parse_pdfs.py left no `.pyc`, and today **no**
  interpreter on the VM can import pymupdf: system
  python3 (3.12.3), the `.venv` (site-packages: curl_cffi
  stack only), `~/.local`, and the distro paths were all
  checked. The environment that ran it (an install since
  removed, or a transient env) no longer exists in a
  verifiable form.
- Consequence for requirements.txt: keep the format
  pass's `pymupdf>=1.24` floor marked UNVERIFIED, or drop
  the pin to a comment; do not state a verified version.
  If PDF parsing is ever needed again, install fresh
  into the project venv and record the version then.

## Verification (all offline; py_compile + stub checks)

- `python3 -m py_compile` on `fssa_search_server.py` and
  `apply_dedupe.py`: OK. Final diff vs v20 reviewed hunk
  by hunk: only items 1-4 changes; nothing else moved.
- A 24-check stub harness (keyword-only path, transports
  monkeypatched, synthetic embeddings) — ALL PASS:
  - Vision bridge: module pinned rank 0 on the natural
    question, pinned excerpt = the frequency chunk; pool
    snapshot stores it with sel=0, br=0, w=true; served
    count == n; /search-mode calls record nothing.
  - Seat failures: helpers record kind/job/model/slot/
    latency/timed_out + 200-char truncation; end-to-end
    through `_judge_seat_text` with an always-raising
    transport: one `seat_fail` per attempted leg with
    `"RuntimeError: HTTP 429: slow down"` persisted, then
    the terminal `seat_down`, then the raise.
  - Consistency: no-embeddings boot warns (never raises);
    an exact synthetic embeddings set reads `ok: true`;
    one injected orphan row flips it to `ok: false` with
    `orphan_urls: 1`. Server replica reproduces 73,454
    on the real v19 corpora (IN 22,660 + federal) in 0.6s.
- NOT verified here (deploy stage, by design): live
  hybrid ranking with embeddings + the LLM rerank (the
  pool snapshot code runs on that path too — same code,
  exercised here only on the keyword path), the
  consistency reading against the real PC embeddings
  file, and any behavior under real provider traffic.

## Deploy notes for the next stage

- Same swap procedure as v20.1 (backup, MD5 check,
  restart, /health). Expected /health deltas: the new
  `consistency` block (see item 3's expectation note —
  do not treat the documented −440-class reading as a
  deploy failure), docs/embeddings counts unchanged.
- First retest: the eyeglasses question. Its ask record
  should now show the module pinned (pool entry sel=0,
  br rank present) and, if any judge seat fails, the
  seat_fail/seat_down reasons in the record's `events`.
- apply_dedupe.py's copy on the PC still has the old
  hardcoded BASE; replace it with this dir's version
  when convenient (it is only run by hand during
  embeddings work).
