#!/usr/bin/env python3
"""v21 retrieval regression harness (WS1c).

Offline, build-gate semantics: runs the server's ask-path
retrieval with NO model calls (query rewrite and LLM rerank
disabled, so the keyword / bridge / registry-pin / rescue
sides are what get exercised) and checks that each case's
expected document lands in the FINAL excerpt selection the
ask path would draft from: retrieve_ask_hits (bundled
questions get per-part retrieval, as in production) followed
by the handler's mechanism sufficiency retry (a matched
mechanism whose governing terms are absent retries with its
own searches and merges the finds ahead of the set — pure
retrieval, no models).

The vector side is exercised only when an embeddings file is
present next to the corpora in the server dir; the v21 VM dir
carries none, so vector_search returns [] by design and the
report says so plainly. When run in a dir WITH embeddings,
the query-embedding call would need a local Ollama; the
harness never starts one.

Corpus handling: the server module loads corpus.json /
corpus_federal.json from its CWD at import. The harness
points a temp dir's symlinks at the versioned corpora in the
server dir and imports the module from there, so the exact
production code and data are what run.

Usage:
  python3 retrieval_regression.py [--cases FILE]
                                  [--server-dir DIR]
Cases file: JSONL, one case per line, in either of two
formats: {"question", "expect_any": [title/URL substrings],
"note"} (the v21 seeds), or the probe-set format {"question",
"expect_title_contains", "expect_url_contains", "corpus":
"indiana"|"federal", "class", "note"} staged with the
probe-cases grounding. A case's corpus field picks the
retrieval corpus (default "both"); narrative-length questions
get n=12 excerpts, matching the ask path. Exit 0 only when
every case passes. The result line names the channels
(kw/vec/kw2/br/rg/rs ranks in the pool snapshot) that carried
the expected document.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SERVER_DIR = os.path.dirname(HERE)


def load_server(server_dir):
    """Import fssa_search_server with CWD pointed at a temp
    dir whose corpus.json / corpus_federal.json symlink to
    the server dir's versioned corpora."""
    tmp = tempfile.mkdtemp(prefix="fssa-retrieval-harness-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json")):
        src = os.path.join(server_dir, target)
        if not os.path.exists(src):
            raise SystemExit(f"missing corpus file: {src}")
        os.symlink(src, os.path.join(tmp, link))
    # growth_terms.json: the server reads it from CWD too;
    # link the real seed so the harness exercises WS3c as
    # deployed.
    gt = os.path.join(server_dir, "growth_terms.json")
    if os.path.exists(gt):
        os.symlink(gt, os.path.join(tmp, "growth_terms.json"))
    old_cwd = os.getcwd()
    old_argv = sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(server_dir, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        # The server module reads sys.argv[1] as its port at
        # import; keep the harness's own CLI out of its way.
        sys.argv = [old_argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=os.path.join(
        HERE, "retrieval_cases.jsonl"))
    ap.add_argument("--server-dir", default=DEFAULT_SERVER_DIR)
    args = ap.parse_args()

    cases = []
    with open(args.cases, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if not cases:
        print("no cases loaded", flush=True)
        return 2

    mod = load_server(args.server_dir)
    # No models: rewrite + rerank off, both at the call and at
    # the module switches the ask path reads.
    mod.REWRITE_ENABLED = False
    mod.RERANK_ENABLED = False
    vector_live = bool(mod.EMBEDDINGS)

    print(f"server dir: {args.server_dir}")
    print(f"corpora: IN {len(mod.CORPUS_IN)} docs, "
          f"federal {len(mod.CORPUS_FED)} docs")
    print("sides exercised: keyword, bridge, registry-pin "
          "(rg), exact-term rescue (rs)"
          + (", vector" if vector_live else
             "; vector NOT exercised (no embeddings file in "
             "the server dir, by design)"))
    print()

    failures = 0
    for i, case in enumerate(cases, 1):
        q = case["question"]
        side = (case.get("corpus") or "both").lower()
        if side in ("indiana", "in"):
            corp = mod.CORPUS_IN
        elif side == "federal":
            corp = mod.CORPUS_FED
        else:
            corp = mod.get_corpus("both")
        n_ex = 12 if mod._is_narrative(q) else 8
        mod._events_reset()
        ctx = mod._ask_ctx()
        hits = mod.retrieve_ask_hits(q, corp, n_ex,
                                     use_rewrite=False,
                                     use_rerank=False)
        # The handler's sufficiency gate (pure retrieval):
        # a matched mechanism whose governing material is
        # absent retries with its own searches before any
        # drafting happens.
        _mechs = mod._mechanism_matches(q)
        if _mechs:
            _missing = mod._sufficiency_missing(_mechs, hits)
            if _missing:
                hits = mod._sufficiency_retry(corp, hits,
                                              _missing)

        def _matches(h):
            title = (h.get("title") or "").lower()
            url = (h.get("url") or "").lower()
            if case.get("expect_any"):
                blob = title + " " + url
                return any(e.lower() in blob
                           for e in case["expect_any"])
            et = (case.get("expect_title_contains")
                  or "").lower()
            eu = (case.get("expect_url_contains") or "").lower()
            return (not et or et in title) and \
                (not eu or eu in url)

        found_at = None
        for j, h in enumerate(hits):
            if _matches(h):
                found_at = j
                break
        channels = set()
        doc_channels = {}
        for pool_rec in ctx.retr_pools:
            for entry in pool_rec.get("pool", ()):
                for ch in ("kw", "vec", "kw2", "br", "rg",
                           "rs"):
                    if entry.get(ch) is not None:
                        channels.add(ch)
                # The channels that carried the EXPECTED doc
                # (matched on the pool entry's title/URL).
                probe_h = {"title": entry.get("t"),
                           "url": entry.get("u")}
                if _matches(probe_h):
                    for ch in ("kw", "vec", "kw2", "br", "rg",
                               "rs"):
                        if entry.get(ch) is not None:
                            doc_channels[ch] = entry.get(ch)
        rescue_events = [e for e in ctx.events
                         if e.get("kind") == "rescue"]
        status = "PASS" if found_at is not None else "FAIL"
        if found_at is None:
            failures += 1
        expect_desc = case.get("expect_any") or (
            f"title~'{case.get('expect_title_contains')}' "
            f"url~'{case.get('expect_url_contains')}'")
        print(f"[{status}] case {i} "
              f"[{case.get('class', 'seed')}/{side}]: {q[:95]}")
        print(f"       expected {expect_desc}"
              + (f" -> selected at #{found_at}" if found_at
                 is not None else " -> NOT in final selection"))
        print(f"       channels in pool: "
              f"{', '.join(sorted(channels)) or 'none'}"
              + (f"; rescue fired: "
                 f"{rescue_events[0]['trigger']} "
                 f"({rescue_events[0]['injected']} injected)"
                 if rescue_events else "")
              + (f"; expected doc carried by: "
                 f"{doc_channels}" if doc_channels else
                 "; expected doc carried by: (not in pool "
                 "snapshot)"))
        if found_at is None:
            print("       final selection was:")
            for j, h in enumerate(hits):
                print(f"         #{j} {(h.get('title') or '')[:80]}")
        print()

    print(f"summary: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
