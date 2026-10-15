#!/usr/bin/env python3
"""FSSA v21 vocabulary audit (WS6).

The two v20/v20.2 production failures were vocabulary
failures: the corpus states a rule in ITS words, the
question arrives in the asker's words, and retrieval never
bridges the two. This tool hunts that class systematically
instead of waiting for the next live miss.

ALGORITHM
(1) UNIT EXTRACTION: scan both corpora for answerable
    units — chunks carrying a rule/figure (cues: $ amounts,
    "every <n> <time unit>", "per month/year", "limit",
    section numbers like 3006.00.00), document titles as
    identity units, and every VERIFIED_FIGURES entry. Each
    unit carries its doc, its chunk, its corpus-side key
    terms (rarity-weighted), and its program/topic.
(2) PHRASING GENERATORS:
    G1 template transforms (frequency -> "how often...",
        amount -> "what is the limit...", generic rules
        form, plus the key-term control query);
    G2 mechanical substitutions (a built-in lay-synonym
        map applied both ways, acronym expansion AND
        contraction, number-format flips digit<->word and
        $8,027<->8027, single-edit typo injection on the
        rarest term);
    G4 real questions mined from the harness case file
        (the merged seed + probe cases, whose expected docs
        are grounded) — the ask records' canonical sets;
    G3 LLM cross-examination (a model sees ONLY the chunk
        and writes jargon-free questions): PLUGGABLE,
        --llm nim, DEFAULT OFF. This build makes no live
        calls; G3 runs post-deploy (see STATUS: the v21
        tail step) when a NIM key is present in the env.
(3) DRY-RUN: the server's retrieval in-process per phrasing
    (retrieve_ask_hits; keyword/bridge/registry/rescue
    channels; vector only when an embeddings file exists —
    the run states which sides ran). Record the unit doc's
    rank in the final selection.
(4) CLASSIFY + EMIT (into the output dir, growth-loop
    staging card shape):
    vocabulary_gap (lay phrasings fail, corpus-term
        phrasings pass) -> concept-map queue card;
    total_miss -> harness case proposal;
    typo_only_fail -> fuzzy-rescue note (in the report);
    wrong-program winner on a failing phrasing ->
        scope-collision card.
    Plus a coverage report: units tested, phrasings per
    unit, per-generator failure rates, class counts, and
    the top vocabulary gaps.

Sampling (the verification run's stated coverage): ALL
registry units, ALL units from documents implicated in
past failures, and a seeded random sample of the remaining
cue-chunks up to --max-units. The full-corpus run is a
deploy-stage job (it is O(units x phrasings) retrieval
calls); this run states exactly what it covered.

Usage:
  python3 vocab_audit.py [--max-units 64] [--seed 20261014]
                         [--out DIR] [--llm nim]
"""
import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
V21_DIR = os.path.dirname(HERE)


def load_server(server_dir):
    """Import fssa_search_server with CWD pointed at a temp
    dir whose corpus.json / corpus_federal.json symlink to
    the server dir's versioned corpora (the module loads
    corpora by relative name at import), with growth_terms
    linked alongside, and sys.argv sanitized (the module
    reads argv[1] as its port). Same pattern the ask-path
    offline checks use; self-contained so this tool depends
    on nothing else in tools/."""
    tmp = tempfile.mkdtemp(prefix="fssa-vocab-audit-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json"),
            ("growth_terms.json", "growth_terms.json")):
        srcp = os.path.join(server_dir, target)
        if os.path.exists(srcp):
            os.symlink(srcp, os.path.join(tmp, link))
    old_cwd = os.getcwd()
    old_argv = sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(server_dir, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        sys.argv = [old_argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
    return mod

CUES = [
    re.compile(r"\$\s?\d[\d,]*"),
    re.compile(r"\bevery\s+(one|two|three|four|five|six|"
               r"seven|eight|nine|ten|eleven|twelve|\d+)\s+"
               r"(years?|months?|weeks?|days?)\b", re.I),
    re.compile(r"\bper\s+(month|year|week|day)\b", re.I),
    re.compile(r"\blimit\b", re.I),
    re.compile(r"\b\d{4}\.\d{2}\.\d{2}(\.\d{2})?\b"),
]
SECTION_RX = re.compile(r"\b\d{4}\.\d{2}\.\d{2}(\.\d{2})?\b")

ACRONYMS = {
    "qit": "qualified income trust",
    "csra": "community spouse resource allowance",
    "mmmna": "monthly maintenance needs allowance",
    "sfc": "structured family caregiving",
    "magi": "modified adjusted gross income",
    "snap": "supplemental nutrition assistance program",
    "ssi": "supplemental security income",
    "ssdi": "social security disability insurance",
    "hcbs": "home and community based services",
    "twp": "trial work period",
    "sga": "substantial gainful activity",
}
LAY_PAIRS = [
    ("resource limit", "asset limit"),
    ("eyeglasses", "glasses"),
    ("qualified income trust", "miller trust"),
    ("substantial gainful activity", "work limit"),
    ("trial work period", "work trial period"),
    ("contribution limit", "deposit limit"),
    ("home equity", "house value"),
    ("nursing facility", "nursing home"),
    ("structured family caregiving", "family caregiving program"),
    ("penalty divisor", "divisor"),
]
NUM_WORDS = {"1": "one", "2": "two", "3": "three", "4": "four",
             "5": "five", "6": "six", "7": "seven", "8": "eight",
             "9": "nine", "10": "ten", "11": "eleven",
             "12": "twelve"}
WORD_NUMS = {v: k for k, v in NUM_WORDS.items()}

IMPLICATED = ("Vision Services", "Dental Services",
              "3006.00.00", "2615.75.15", "3010.20.10",
              "Trial Work Period", "IN.0210", "SSI Resources",
              "Resource Guide", "Spousal Impoverishment",
              "Ch 2600", "INvestABLE", "SNAP (Food Assistance)",
              "H&W Waiver Provider Manual", "CHIP Eligibility")


def _norm_q(q):
    return re.sub(r"\s+", " ", (q or "").strip().lower())


class Audit:
    def __init__(self, mod):
        self.mod = mod
        self.blobs = {}
        self._df_cache = {}

    def blob(self, d):
        key = id(d)
        if key not in self.blobs:
            self.blobs[key] = ((d.get("title") or "") + " "
                               + (d.get("text") or "")).lower()
        return self.blobs[key]

    def df(self, term):
        if term not in self._df_cache:
            docs = self.mod.CORPUS_IN + self.mod.CORPUS_FED
            self._df_cache[term] = sum(
                1 for d in docs if term in self.blob(d))
        return self._df_cache[term]

    def key_terms(self, text, title=""):
        words = []
        for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
            if w not in self.mod.STOP and w not in words:
                words.append(w)
        scored = sorted(words, key=lambda w: self.df(w))
        terms = scored[:5]
        for s in SECTION_RX.findall((title or "") + " "
                                     + (text or "")[:400]):
            t = s if isinstance(s, str) else s[0]
            full = SECTION_RX.search(
                (title or "") + " " + (text or "")[:400])
            if full and full.group(0) not in terms:
                terms.insert(0, full.group(0))
            break
        return terms[:6]


def extract_units(mod, audit, max_units, seed):
    units, seen = [], set()

    def add(doc, chunk, source, side):
        key = (doc["url"], (chunk or "")[:60])
        if key in seen:
            return
        seen.add(key)
        prog, _dt = mod._classify_hit(
            {"title": doc.get("title"), "url": doc.get("url")})
        units.append({
            "unit_id": f"{source}-{len(units)}",
            "source": source, "side": side,
            "doc_title": doc.get("title"),
            "doc_url": doc.get("url"), "program": prog,
            "chunk": chunk,
            "key_terms": audit.key_terms(chunk,
                                         doc.get("title"))})

    # Registry units (all of them).
    for e in mod.VERIFIED_FIGURES:
        topic = e.get("topic") or {}
        hint = topic.get("doc_hint") or {}
        ht = (hint.get("title") or "").lower()
        hu = (hint.get("url") or "").lower()
        doc = None
        for d in mod.CORPUS_IN + mod.CORPUS_FED:
            if (ht and ht in (d.get("title") or "").lower()) \
                    or (hu and hu in (d.get("url") or "")
                        .lower()):
                doc = d
                break
        if doc is None:
            continue
        fig = None
        if e.get("figures"):
            fig = f"{e['figures'][0]:,.0f}" \
                if e["figures"][0] >= 100 else None
        chunks = mod._doc_chunks(doc)
        if not chunks:
            continue
        chunk = None
        for ch in chunks:
            if fig and fig in ch:
                chunk = ch
                break
        add(doc, chunk or chunks[0],
            "registry:" + e["id"],
            "in" if doc in mod.CORPUS_IN else "federal")

    # Implicated docs: up to 2 cue-chunks each.
    for d in mod.CORPUS_IN + mod.CORPUS_FED:
        if not any(s in (d.get("title") or "") for s in
                   IMPLICATED):
            continue
        n = 0
        for ch in mod._doc_chunks(d):
            if any(c.search(ch) for c in CUES):
                add(d, ch, "implicated",
                    "in" if d in mod.CORPUS_IN else "federal")
                n += 1
                if n >= 2:
                    break
        if n == 0 and mod._doc_chunks(d):
            add(d, mod._doc_chunks(d)[0], "implicated",
                "in" if d in mod.CORPUS_IN else "federal")

    # Seeded random sample of the remaining cue-chunks.
    rng = random.Random(seed)
    pool = []
    for d in mod.CORPUS_IN + mod.CORPUS_FED:
        for ch in mod._doc_chunks(d):
            if any(c.search(ch) for c in CUES):
                pool.append((d, ch))
    rng.shuffle(pool)
    for d, ch in pool:
        if len(units) >= max_units:
            break
        add(d, ch, "sample",
            "in" if d in mod.CORPUS_IN else "federal")
    return units


def g1_phrasings(mod, unit):
    terms = unit["key_terms"]
    topic = " ".join(t for t in terms
                     if not SECTION_RX.fullmatch(t))[:60]
    topic = topic.strip() or (unit["doc_title"] or "")[:50]
    out = []
    chunk = unit["chunk"] or ""
    if any(c.search(chunk) for c in CUES[:1]) or "$" in chunk:
        out.append((f"What is the {topic} limit?",
                    "G1", "neutral"))
    if CUES[1].search(chunk):
        out.append((f"How often {topic}?", "G1", "neutral"))
    out.append((f"What are the rules for {topic}?",
                "G1", "neutral"))
    out.append((" ".join(terms), "G1", "corpus"))
    seen, ded = set(), []
    for p in out:
        if _norm_q(p[0]) not in seen and len(p[0]) >= 8:
            seen.add(_norm_q(p[0]))
            ded.append(p)
    return ded[:3]


def g2_variants(mod, audit, base, unit):
    out = []
    low = base.lower()
    # Acronym expansion / contraction.
    for acro, full in ACRONYMS.items():
        if re.search(rf"\b{acro}\b", low):
            out.append((base.replace(acro, full).replace(
                acro.upper(), full), "G2-acronym", "corpus"))
        if full in low:
            out.append((base.replace(full, acro),
                        "G2-acronym", "lay"))
    # Lay synonym swaps, both directions.
    for a, b in LAY_PAIRS:
        if a in low:
            out.append((base.replace(a, b), "G2-lay", "lay"))
        if b in low:
            out.append((base.replace(b, a), "G2-lay", "corpus"))
    # Number-format flips.
    m = re.search(r"\$([\d,]+)", base)
    if m:
        out.append((base.replace("$" + m.group(1),
                                 m.group(1).replace(",", "")),
                    "G2-number", "lay"))
    m = re.search(r"\bevery (\d+|one|two|three|four|five|six|"
                  r"seven|eight|nine|ten|eleven|twelve)\b", low)
    if m:
        tok = m.group(1)
        flip = NUM_WORDS.get(tok) or WORD_NUMS.get(tok)
        if flip:
            out.append((base.replace(tok, flip),
                        "G2-number", "neutral"))
    # Single-edit typo on the rarest content term.
    words = [w for w in re.findall(r"[a-z]{5,}", low)
             if w not in mod.STOP]
    if words:
        rare = min(words, key=lambda w: audit.df(w))
        i = len(rare) // 2
        typo = rare[:i] + rare[i + 1:]
        if typo != rare:
            out.append((base.replace(rare, typo),
                        "G2-typo", "typo"))
    seen, ded = set(), []
    for p in out:
        k = _norm_q(p[0])
        if k not in seen and k != _norm_q(base):
            seen.add(k)
            ded.append(p)
    return ded[:3]


def g3_questions(chunk, terms):
    """G3 (pluggable, OFF by default in this build): a model
    sees only the chunk and writes jargon-free questions.
    Runs post-deploy with a NIM key in the env; without one
    it reports unavailable and produces nothing. This build
    never calls it (the verification run passes no --llm)."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        return []
    prompt = (
        "Here is one passage from a benefits policy corpus:\n"
        + (chunk or "")[:1500]
        + "\n\nWrite 3 questions a layperson might ask that "
          "this passage answers. Use everyday words only; do "
          "not use the passage's jargon, acronyms, or section "
          "numbers. One question per line, no numbering.")
    payload = json.dumps({
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4, "max_tokens": 400}).encode()
    req = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"]
    return [(l.strip(), "G3", "lay") for l in
            text.splitlines() if len(l.strip()) > 10][:3]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-units", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--out", default=os.path.join(
        HERE, "vocab_staging"))
    ap.add_argument("--server-dir", default=V21_DIR)
    ap.add_argument("--cases", default=os.path.join(
        HERE, "retrieval_cases.jsonl"))
    ap.add_argument("--llm", default=None,
                    help="pluggable G3 mode ('nim'); default "
                         "off — this build makes no live calls")
    args = ap.parse_args()

    mod = load_server(args.server_dir)
    mod.REWRITE_ENABLED = False
    mod.RERANK_ENABLED = False
    audit = Audit(mod)
    units = extract_units(mod, audit, args.max_units,
                          args.seed)

    # G4: real questions from the case file, attached to the
    # unit whose doc they expect.
    def g4_for(unit):
        out = []
        for line in open(args.cases, encoding="utf-8"):
            if not line.strip():
                continue
            c = json.loads(line)
            et = (c.get("expect_title_contains") or "")
            eu = (c.get("expect_url_contains") or "")
            any_ = c.get("expect_any") or []
            title = unit["doc_title"] or ""
            url = unit["doc_url"] or ""
            if (et and et in title) or (eu and eu in url) \
                    or any(a in title or a in url
                           for a in any_):
                out.append((c["question"], "G4", "lay"))
        return out[:1]

    os.makedirs(args.out, exist_ok=True)
    results = []
    gen_stats = {}
    class_counts = {}
    vocab_gaps = []
    cards, proposals = [], []

    for unit in units:
        corp = mod.CORPUS_IN if unit["side"] == "in" \
            else mod.CORPUS_FED
        phrasings = g1_phrasings(mod, unit)
        base = phrasings[-1][0] if phrasings else ""
        phrasings += g2_variants(mod, audit, base, unit)
        phrasings += g4_for(unit)
        if args.llm == "nim":
            phrasings += [(q, g, s) for q, g, s in
                          g3_questions(unit["chunk"],
                                       unit["key_terms"])]
        rows = []
        for text, gen, side_kind in phrasings:
            mod._events_reset()
            n_ex = 12 if mod._is_narrative(text) else 8
            hits = mod.retrieve_ask_hits(
                text, corp, n_ex, use_rewrite=False,
                use_rerank=False)
            rank = next((i for i, h in enumerate(hits)
                         if h.get("url") == unit["doc_url"]),
                        None)
            chunk_rank = next(
                (i for i, h in enumerate(hits)
                 if (h.get("excerpt") or "")[:60]
                 == (unit["chunk"] or "")[:60]), None)
            winner_prog = None
            if hits:
                winner_prog = mod._classify_hit(hits[0])[0]
            rows.append({"q": text, "gen": gen,
                         "kind": side_kind, "rank": rank,
                         "chunk_rank": chunk_rank,
                         "winner_prog": winner_prog})
            st = gen_stats.setdefault(
                gen, {"run": 0, "fail": 0})
            st["run"] += 1
            if rank is None:
                st["fail"] += 1
        lay = [r for r in rows if r["kind"] == "lay"]
        corpus_rows = [r for r in rows if r["kind"] == "corpus"]
        typo = [r for r in rows if r["kind"] == "typo"]
        non_typo = [r for r in rows if r["kind"] != "typo"]
        if rows and all(r["rank"] is None for r in rows):
            cls = "total_miss"
        elif typo and all(r["rank"] is not None
                          for r in non_typo) and any(
                              r["rank"] is None for r in typo):
            cls = "typo_only_fail"
        elif lay and all(r["rank"] is None for r in lay) \
                and any(r["rank"] is not None
                        for r in corpus_rows):
            cls = "vocabulary_gap"
        elif any(r["rank"] is None
                 and r["winner_prog"] not in (None,
                                              unit["program"])
                 for r in rows):
            cls = "scope_collision"
        else:
            cls = "pass"
        class_counts[cls] = class_counts.get(cls, 0) + 1
        results.append({"unit": {k: v for k, v in unit.items()
                                 if k != "chunk"},
                        "class": cls, "phrasings": rows})
        uid = "vocab-" + hashlib.sha256(
            (unit["doc_url"] + (unit["chunk"] or "")[:60])
            .encode()).hexdigest()[:10]
        if cls == "vocabulary_gap":
            failed_lay = [r["q"] for r in lay
                          if r["rank"] is None]
            concept = failed_lay[0] if failed_lay else ""
            cards.append({
                "id": uid, "status":
                    "staged — pending human merge",
                "class": "vocabulary_gap",
                "question": concept,
                "unit_doc": unit["doc_title"],
                "unit_url": unit["doc_url"],
                "corpus_terms": unit["key_terms"],
                "note": ("Lay phrasings failed while the "
                         "corpus-term phrasing reached the "
                         "document. Concept-map candidate: "
                         "register the lay phrasing's terms "
                         "for this unit's key terms in "
                         "growth_terms.json (human merge).")})
            for r in lay:
                if r["rank"] is None:
                    vocab_gaps.append({
                        "unit": unit["doc_title"],
                        "lay_question": r["q"],
                        "corpus_terms": unit["key_terms"]})
        elif cls == "scope_collision":
            cards.append({
                "id": uid, "status":
                    "staged — pending human merge",
                "class": "scope_collision",
                "question": next(
                    (r["q"] for r in rows
                     if r["rank"] is None), ""),
                "unit_doc": unit["doc_title"],
                "unit_url": unit["doc_url"],
                "unit_program": unit["program"],
                "note": ("A failing phrasing's top hit "
                         "belongs to a different program "
                         "than the unit's document. Review "
                         "for program scoping, not "
                         "vocabulary.")})
        elif cls == "total_miss":
            expect = []
            for t in unit["key_terms"]:
                if SECTION_RX.fullmatch(t):
                    expect.append(t)
            if not expect:
                expect = [(unit["doc_title"] or "")[:40]]
            proposals.append({
                "question": next(
                    (r["q"] for r in rows
                     if r["gen"] == "G4"),
                    rows[0]["q"] if rows else ""),
                "expect_any": expect,
                "note": ("vocab-audit total miss: no "
                         "generated phrasing reached the "
                         "unit document"),
                "unit_doc": unit["doc_title"]})

    for card in cards:
        with open(os.path.join(args.out,
                               card["id"] + ".card.json"),
                  "w", encoding="utf-8") as f:
            json.dump(card, f, indent=1)
        with open(os.path.join(args.out,
                               card["id"] + ".card.md"),
                  "w", encoding="utf-8") as f:
            f.write(f"# {card['id']}: {card['class']}\n\n"
                    f"- Status: {card['status']}\n"
                    f"- Question: {card.get('question')}\n"
                    f"- Unit doc: {card.get('unit_doc')}\n"
                    f"- Corpus terms: "
                    f"{card.get('corpus_terms')}\n\n"
                    f"{card.get('note', '')}\n")
    if proposals:
        with open(os.path.join(args.out,
                               "harness_proposals.jsonl"),
                  "w", encoding="utf-8") as f:
            for p in proposals:
                f.write(json.dumps(p) + "\n")

    total_phr = sum(len(r["phrasings"]) for r in results)
    report = {
        "units_tested": len(results),
        "units_by_source": {
            s: sum(1 for r in results
                   if r["unit"]["source"].startswith(s))
            for s in ("registry", "implicated", "sample")},
        "phrasings_total": total_phr,
        "phrasings_per_unit": round(
            total_phr / max(1, len(results)), 2),
        "per_generator": gen_stats,
        "classes": class_counts,
        "vector_side": ("exercised" if mod.EMBEDDINGS else
                        "NOT exercised (no embeddings "
                        "file on the VM)"),
        "g3": ("ran (--llm nim)" if args.llm == "nim"
               else "off (post-deploy step)"),
        "top_vocabulary_gaps": vocab_gaps[:15],
        "fuzzy_rescue_notes": [
            {"unit": r["unit"]["doc_title"],
             "note": ("typo-only failure: the exact-term "
                      "rescue is exact; a fuzzy (edit-"
                      "distance) term variant is the "
                      "candidate fix, staged as a note, "
                      "not code")}
            for r in results if r["class"] ==
            "typo_only_fail"],
        "results": results}
    with open(os.path.join(args.out, "report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    lines = ["# Vocabulary audit — coverage report", "",
             f"Units tested: {len(results)} "
             f"(registry "
             f"{report['units_by_source']['registry']}, "
             f"implicated "
             f"{report['units_by_source']['implicated']}, "
             f"sample {report['units_by_source']['sample']})",
             f"Phrasings: {total_phr} "
             f"({report['phrasings_per_unit']} per unit)",
             f"Vector side: {report['vector_side']}",
             f"G3: {report['g3']}", "", "## Per-generator", ""]
    for g, st in sorted(gen_stats.items()):
        lines.append(f"- {g}: {st['run']} run, {st['fail']} "
                     f"failed "
                     f"({round(100.0 * st['fail'] / max(1, st['run']), 1)}%)")
    lines += ["", "## Classes", ""]
    for c, n in sorted(class_counts.items()):
        lines.append(f"- {c}: {n}")
    lines += ["", "## Top vocabulary gaps", ""]
    for vg in vocab_gaps[:15]:
        lines.append(f"- {vg['unit'][:60]} :: "
                     f"{vg['lay_question'][:80]} -> "
                     f"{', '.join(vg['corpus_terms'][:4])}")
    with open(os.path.join(args.out, "report.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"units tested: {len(results)} "
          f"(registry {report['units_by_source']['registry']}, "
          f"implicated "
          f"{report['units_by_source']['implicated']}, "
          f"sample {report['units_by_source']['sample']})")
    print(f"phrasings: {total_phr}; per-generator: "
          + "; ".join(f"{g} {st['fail']}/{st['run']} failed"
                      for g, st in sorted(gen_stats.items())))
    print(f"classes: {class_counts}")
    print(f"cards: {len(cards)}; harness proposals: "
          f"{len(proposals)}; vocab gaps: {len(vocab_gaps)}")
    print(f"vector side: {report['vector_side']}; "
          f"G3: {report['g3']}")
    print(f"output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
