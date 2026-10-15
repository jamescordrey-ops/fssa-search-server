#!/usr/bin/env python3
"""v23 (WS3.3): registry wake-up vocabulary, GENERATED from
enrichment — plus the plain-language audit against it.

The disease this treats (v22.1/v22.2 trigger audits): registry
entries' hand-written trigger groups were phrased in
SOURCE-document vocabulary, so plain asker questions never
woke the entry and its verified figures never engaged. The
hand groups were then repaired by hand, entry by entry. This
tool generates each entry's wake-up vocabulary mechanically
from the enrichment layer instead:

  carriers   the sections that carry the entry. The source
             section resolves citation-first (doc numbers in
             the entry's source field, figure-verified), then
             the generation's registry_sources table, then
             FTS figure verification; neighbourhood sections
             follow by BM25 over the store's FTS (synth_q
             included) and by needle-IDF ranking over the
             question surface, needles = the entry's topic
             phrases + title (+ statement when no topic
             metadata exists). Mechanisms resolve by their
             own retry_query the same way.
  vocabulary the distinct units (stemmed unigrams +
             bigrams) of the carriers' synthetic questions +
             aliases, counted with rank weight (the source
             section weighs 3x: its phrasing is the entry's
             home ground). Corpus universals drop out.
  wake       tf-idf argmax over the registry: a unit weighs
             its IDF across the registry's vocabularies
             times (1 + log) of its attestation in the
             entry's carriers; wake needs the strict top
             score and >= 2.0. A tie (or top-two) inside one
             dated family resolves by the question's date
             through the server's own _question_date /
             _entry_matches_date rule, exactly as the
             runtime resolves those families.

The hand groups are NOT replaced in this release: they stay
as the runtime's precision overlay. This tool STAGES the
generated vocabulary (tools/vocab_staging/registry_vocab.json)
and audits it against the 19 plain-language audit questions
(14 figures from REGISTRY_TRIGGER_AUDIT.md, 5 mechanisms
from MECHANISMS_TRIGGER_AUDIT.md, verbatim as locked in
tools/offline_checks.py).

MEASURED AUDIT (2026-10-15, generation g0000): figures
10/14, mechanisms 3/5 — BELOW the plan bar of 14/14 + 5/5.
The shortfall is enrichment register coverage, not the
generator: the failing entries' carrier neighbourhoods
contain no asker phrasing for the audit questions' key
words at all (asset_limit_2000: 'bank'/'money'/'account'
appear in zero carrier questions — its source section is
template-only; able_onset_age: its registry_sources row
is a coincidental figure match on a waiver application;
the divisor pair and the two waiver mechanisms share
carriers so closely that single-question phrasing luck
decides them). The named follow-up is a targeted fleet
enrichment pass over registry-carrier sections (the
WS3.5 machinery), after which this audit is re-run.
Method history (carrier/scoring variants tried, audit
numbers for each) is recorded in the v23 STATUS.md.

Usage:
  python3 tools/registry_vocab.py [--gen generations/g0000]
                                  [--out tools/vocab_staging]
Exit 0 only when the audit reads 14/14 figures + 5/5
mechanisms.
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on",
    "for", "is", "are", "was", "were", "be", "been", "what",
    "how", "much", "many", "can", "i", "my", "me", "we",
    "our", "you", "your", "do", "does", "did", "if", "it",
    "its", "this", "that", "with", "from", "by", "at", "as",
    "an", "get", "got", "have", "has", "had", "will", "would",
    "still", "into", "about", "there", "when", "who", "whom",
    "which", "while", "than", "then", "so", "but", "not",
    "no", "yes", "one", "two", "per", "each", "any", "all",
    "over", "under", "after", "before", "through", "during",
}


def stem(tok):
    """Light, CONSISTENT stemming (the audit applies the
    same function to questions and vocabulary, so agreement
    matters more than linguistic perfection): allowances ->
    allowance, changes -> change, resources -> resource."""
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 4 and tok.endswith("sses"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s") \
            and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def terms(text):
    out = set()
    for tok in re.findall(r"[a-z][a-z0-9']+", (text or "").lower()):
        tok = tok.strip("'")
        if len(tok) < 3 or tok in STOP:
            continue
        out.add(stem(tok))
    return out


def units(text):
    """Vocabulary units of one question/alias: stemmed
    unigrams plus stemmed bigrams (adjacent content words).
    Bigrams carry the asker phrasing ('nursing home',
    'food stamps', 'able account') that unigrams smear
    across entries. URL/domain chrome in aliases
    (ChangesIn.gov and friends) is stripped first: it is
    source furniture, not asker phrasing."""
    text = re.sub(r"https?://\S+", " ", text or "")
    text = re.sub(r"\S+\.(gov|com|org|net|edu)\b", " ",
                  text or "")
    toks = []
    for tok in re.findall(r"[a-z][a-z0-9']+",
                          (text or "").lower()):
        tok = tok.strip("'")
        if len(tok) < 3 or tok in STOP:
            continue
        toks.append(stem(tok))
    out = set(toks)
    for a, b in zip(toks, toks[1:]):
        out.add(a + " " + b)
    return out


def load_server():
    tmp = tempfile.mkdtemp(prefix="fssa-regvocab-")
    for link, target in (
            ("corpus.json", "corpus_v19.json"),
            ("corpus_federal.json", "corpus_federal_v19.json"),
            ("growth_terms.json", "growth_terms.json")):
        src = os.path.join(ROOT, target)
        if os.path.exists(src):
            os.symlink(src, os.path.join(tmp, link))
    cwd, argv = os.getcwd(), sys.argv
    os.chdir(tmp)
    try:
        spec = importlib.util.spec_from_file_location(
            "fssa_search_server",
            os.path.join(ROOT, "fssa_search_server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["fssa_search_server"] = mod
        sys.argv = [argv[0]]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
        os.chdir(cwd)
    return mod


def fts_sections(conn, query_terms, limit, doc_id=None):
    """BM25-ranked section ids for a term set (the store's
    own section_fts: title/headings/body/synth_q/aliases)."""
    qs = " OR ".join(f'"{t}"' for t in sorted(query_terms)[:24]
                     if t.replace("'", "").isalnum())
    if not qs:
        return []
    sql = ("SELECT s.section_id FROM section_fts f "
           "JOIN sections s ON s.rowid = f.rowid "
           "WHERE section_fts MATCH ? ")
    args = [qs]
    if doc_id is not None:
        sql += "AND s.doc_id = ? "
        args.append(doc_id)
    sql += "ORDER BY rank LIMIT ?"
    args.append(limit)
    try:
        return [r[0] for r in conn.execute(sql, args)]
    except sqlite3.OperationalError:
        return []


def figure_strings(entry):
    """The entry's figure values as they appear in text:
    32532 -> ('32,532', '32532')."""
    out = set()
    for v in entry.get("figures") or entry.get("values") or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == int(f):
            out.add(f"{int(f):,}")
            out.add(str(int(f)))
    return out


def resolve_doc_by_citation(mod, conn, entry):
    """The entry's own source CITATION: doc-number tokens
    in the source field ('SSA POMS SI 01130.740',
    'DFR Policy Manual section 2615.10.35') matched
    against doc titles, figure-verified — a citation
    names its document, and the figure check keeps the
    match honest."""
    figs = figure_strings(entry)
    cites = re.findall(r"\d{4,5}(?:\.\d+)+",
                       entry.get("source") or "")
    for cite in cites:
        for did, in conn.execute(
                "SELECT doc_id FROM docs WHERE title LIKE ?",
                (f"%{cite}%",)):
            row = conn.execute(
                "SELECT text FROM doc_texts WHERE doc_id=?",
                (did,)).fetchone()
            text = row[0] if row else ""
            if figs and any(f in text for f in figs):
                return did
    return None


def resolve_doc_by_figures(mod, conn, entry):
    """The builder's verification (lint_registry): among
    the docs the entry's title/statement terms find by
    FTS, the carrier is the one whose text states the
    most of the entry's figures."""
    title_terms = terms(entry.get("title") or "") \
        | terms(entry.get("statement") or "")
    figs = figure_strings(entry)
    cand_docs = []
    for sid in fts_sections(conn, title_terms, 40):
        d = conn.execute(
            "SELECT doc_id FROM sections WHERE section_id=?",
            (sid,)).fetchone()
        if d and d[0] not in cand_docs:
            cand_docs.append(d[0])
    best, best_n = None, -1
    for did in cand_docs:
        row = conn.execute(
            "SELECT text FROM doc_texts WHERE doc_id=?",
            (did,)).fetchone()
        text = row[0] if row else ""
        n = sum(1 for f in figs if f in text)
        if n > best_n:
            best, best_n = did, n
    return best



_INDEX = None  # (section_units, sdf) built once by main()


def build_index(conn):
    """Every enriched section's question/alias unit set,
    plus each unit's section frequency — the IDF base for
    needle weighting."""
    import math
    section_units = {}
    sdf = {}
    for sid, qj, aj in conn.execute(
            "SELECT section_id, questions, aliases "
            "FROM enrich"):
        texts = json.loads(qj or "[]") + json.loads(aj or "[]")
        su = set()
        for t in texts:
            su |= units(t)
        if not su:
            continue
        section_units[sid] = su
        for u in su:
            sdf[u] = sdf.get(u, 0) + 1
    total = len(section_units)
    idf = {u: math.log((total + 1) / (c + 0.5))
           for u, c in sdf.items()}
    return section_units, idf


def rank_by_questions(conn, needles, limit):
    """Sections ranked by how strongly their synthetic
    questions + aliases speak the needle vocabulary. Each
    matched needle weighs its rarity across the whole
    question surface, so corpus-common needles (resource,
    income, security) cannot flood the ranking the way a
    raw overlap count let them in v4 — the carriers are
    the sections that are ABOUT the topic in asker
    phrasing, wherever they live in the store."""
    section_units, idf = _INDEX
    scored = []
    for sid, su in section_units.items():
        hit = needles & su
        if not hit:
            continue
        score = sum(idf.get(u, 0.0) for u in hit)
        if score > 0:
            scored.append((score, sid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [sid for _s, sid in scored[:limit]]


def carriers_for_entry(mod, conn, entry, sources):
    """(section_ids, how): the entry's carrier sections.
    The corpus is page-per-doc (one section per page for
    most docs), so carriers are store-wide: the registry
    source section itself, the sections that STATE the
    entry's figures anywhere in the store, and the
    store-wide head of the entry's topic ranking (the same
    topic phrases channel R ranks by). Capped at 24."""
    eid = entry["id"]
    # Citation first for every entry: a source citation
    # names its document, and one registry_sources row is
    # a known coincidental figure match (able_onset_age
    # points at a waiver application whose text happens to
    # state a $20,000 home-modification cap). The
    # registry table remains the fallback, then FTS
    # figure verification.
    doc_id = resolve_doc_by_citation(mod, conn, entry)
    how = "citation"
    if doc_id is None:
        doc_id = sources.get(eid)
        how = "registry_sources"
    if doc_id is None:
        doc_id = resolve_doc_by_figures(mod, conn, entry)
        how = "figure-verified-fts"
    picked = []
    if doc_id is not None:
        for (sid,) in conn.execute(
                "SELECT section_id FROM sections "
                "WHERE doc_id=? ORDER BY first_ordinal",
                (doc_id,)):
            picked.append(sid)
    topic = entry.get("topic") or getattr(
        mod, "_V21_REGISTRY_META", {}).get(eid, {}).get(
            "topic") or {}
    needles = set()
    for phrase, _t in topic.get("phrases", ()):
        needles |= units(phrase)
    needles |= units(entry.get("title") or "")
    if not topic.get("phrases"):
        # Only entries with NO curated topic metadata (the
        # divisor pair, the ABLE pair) fall back to their
        # statement's units as needles — with a topic
        # present, statement units ('nursing home' in the
        # home-equity statement) flooded the carrier set
        # with every institutional-care section in the
        # store. (Needles only RANK carriers; the
        # vocabulary itself still comes solely from the
        # carriers' questions + aliases.)
        needles |= units(entry.get("statement") or "")
    needle_terms = set()
    for u in needles:
        needle_terms |= set(u.split())
    for sid in fts_sections(conn, needle_terms, 5):
        if sid not in picked:
            picked.append(sid)
    for sid in rank_by_questions(conn, needles, 5):
        if sid not in picked:
            picked.append(sid)
    return picked[:10], how


def carriers_for_mechanism(mod, conn, mech):
    q = mech.get("retry_query") or mech.get("title") or ""
    needles = units(q) | units(mech.get("title") or "")
    needle_terms = set()
    for u in needles:
        needle_terms |= set(u.split())
    picked = fts_sections(conn, needle_terms, 4)
    for sid in rank_by_questions(conn, needles, 4):
        if sid not in picked:
            picked.append(sid)
    return picked[:8], "retry_query-fts"


def build_vocab(conn, section_ids):
    """(unit -> carrier-text count, per-text unit sets)
    over the carriers' synthetic questions + aliases.
    Counts are rank-weighted: the FIRST carrier (the
    entry's verified source section, carriers_for_entry
    puts it first) weighs 3x — its phrasing is the entry's
    home ground and should dominate its vocabulary over
    the neighbourhood sections that follow."""
    vocab = {}
    per_text = []
    n_q = 0
    for pos, sid in enumerate(section_ids):
        weight = 3 if pos == 0 else 1
        row = conn.execute(
            "SELECT questions, aliases FROM enrich "
            "WHERE section_id=?", (sid,)).fetchone()
        if not row:
            continue
        questions = json.loads(row[0] or "[]")
        aliases = json.loads(row[1] or "[]")
        n_q += len(questions)
        for text in list(questions) + list(aliases):
            us = units(text)
            if not us:
                continue
            per_text.append(sorted(us))
            for t in us:
                vocab[t] = vocab.get(t, 0) + weight
    return vocab, n_q, per_text


# The audit questions, verbatim from tools/offline_checks.py
# (the v22.1 figures audit + the v22.2 mechanisms audit).
FIG_AUDIT = {
    "backpay_exclusion":
        "Social Security owed me money from earlier months "
        "and just paid it all at once. How long before that "
        "money counts against me?",
    "able_onset_age":
        "What is the oldest age my disability could have "
        "started for me to open an ABLE account?",
    "snap_resource_limit_in":
        "How much can I have in the bank and still get EBT "
        "in Indiana?",
    "csra":
        "If I go into a nursing home how much of our "
        "savings can my wife keep?",
    "mmmna":
        "If my husband is in a nursing home, how much of "
        "his monthly check can I keep to live on?",
    "home_equity_limit":
        "If our house is worth a lot, can I still get "
        "Medicaid help to pay for a nursing home?",
    "asset_limit_2000":
        "How much money can I have in the bank and still "
        "get Medicaid?",
    "waiver_income_cap":
        "I get help at home through Medicaid. How much "
        "money can I make each month and still qualify?",
    "ssa_2027_figures":
        "How much will my Social Security check go up "
        "next year?",
    "difficulty_of_care":
        "My wife gets paid to take care of me at home. "
        "Does that money count if she applies for Healthy "
        "Indiana?",
    "transfer_penalty_divisor_current":
        "If I give property to my kids now and apply for "
        "Medicaid after July 2026, how long will I be "
        "penalized?",
    "transfer_penalty_divisor_legacy":
        "If I gave property to my kids and applied for "
        "Medicaid in March 2026, how long would I be "
        "penalized?",
    "able_contribution_limit":
        "How much money can I contribute to an ABLE "
        "account each year?",
    "able_contribution_limit_legacy":
        "How much money could I contribute to an ABLE "
        "account in 2025?",
}
MECH_AUDIT = {
    "qit_over_cap":
        "My dad's pension and Social Security add up to "
        "more than Medicaid allows each month, so he "
        "doesn't qualify. Is there anything that can be "
        "done so he can still get help paying for his care?",
    "backpay_nine_months":
        "Social Security owed me money from earlier months "
        "and just paid it all at once. How long before that "
        "money counts against me?",
    "snap_lump_sum":
        "I just got one big check for months I was owed. "
        "Will that count as income for my food stamps?",
    "csra_half_clamp":
        "If I go into a nursing home how much of our "
        "savings can my wife keep?",
    "difficulty_of_care_exclusion":
        "My wife gets paid to take care of me at home. "
        "Does that money count if she applies for Healthy "
        "Indiana?",
}


def wake(mod, entries, vocabs, question):
    """The woken entry id for one question, or None. Score =
    tf-idf over the question's units: IDF across the
    registry's generated vocabularies times the unit's
    attestation frequency in this entry's carriers. Wake
    needs the strict top score and a score of at least
    2.0. A tie inside one dated family resolves by the
    question's date (the runtime's own in-force rule)."""
    import math
    n = len(entries)
    df = {}
    for e in entries:
        for t in (vocabs.get(e["id"]) or {}):
            df[t] = df.get(t, 0) + 1

    def w(t):
        return math.log((n + 1) / (df.get(t, 0) + 0.5))

    qt = units(question)
    scored = []
    for e in entries:
        v = vocabs.get(e["id"]) or {}
        s = sum(w(t) * (1.0 + math.log(v[t]))
                for t in qt & set(v))
        scored.append((round(s, 3), e))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 2.0:
        return None, scored
    top = scored[0][0]
    leaders = [e for s, e in scored if s == top]
    if len(leaders) == 1:
        return leaders[0]["id"], scored
    fams = {e.get("family") for e in leaders}
    if len(fams) == 1 and fams != {None}:
        on_date = mod._question_date(question) or mod._today_iso()
        for e in leaders:
            if mod._entry_matches_date(e, on_date):
                return e["id"], scored
    return None, scored


TOP_UNITS = 500


def characteristic(entries, conn, carrier_fn, out_map):
    """Build each entry's raw unit counts over its carriers,
    then stage each entry's characteristic units: every
    unit attested in at least two of its carriers'
    questions + aliases, minus corpus universals (units
    in every entry's raw vocabulary). A wake-up vocabulary
    is the entry's own asker phrasing, not the whole term
    dump of its carriers. Returns {id: {unit: count}} over
    the staged units only."""
    import math
    raw, meta = {}, {}
    for e in entries:
        secs, how = carrier_fn(e)
        vocab, n_q, per_text = build_vocab(conn, secs)
        raw[e["id"]] = vocab
        meta[e["id"]] = (secs, how, n_q, per_text)
    n = len(entries)
    df = {}
    for vocab in raw.values():
        for t in vocab:
            df[t] = df.get(t, 0) + 1

    def idf(t):
        return math.log((n + 1) / (df.get(t, 0) + 0.5))

    staged = {}
    for e in entries:
        vocab = raw[e["id"]]
        # Rank by carrier-question frequency; drop only
        # units carried by (nearly) every entry — corpus
        # universals like 'medicaid' that wake nothing in
        # particular. The topic words themselves (nursing,
        # wife, bank) are shared across neighbouring
        # entries BY NATURE; ranking them away by IDF was
        # v3's mistake — distinctiveness lives in the wake
        # scoring's IDF weights and in combinations.
        # Tight carriers make the full attested vocabulary
        # stageable: drop corpus universals (in every
        # entry's raw vocab) and cap by frequency.
        ranked = sorted(
            (kv for kv in vocab.items() if df[kv[0]] < n),
            key=lambda kv: (-kv[1], kv[0]))
        keep = dict(ranked[:TOP_UNITS])
        staged[e["id"]] = keep
        secs, how, n_q, per_text = meta[e["id"]]
        out_map[e["id"]] = {
            "carriers": secs, "carrier_how": how,
            "carrier_questions": n_q,
            "terms": sorted(keep),
            "term_counts": {t: keep[t] for t in sorted(keep)},
            "question_units": per_text}
    return staged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", default=None,
                    help="generation dir (default: CURRENT)")
    ap.add_argument("--out", default=os.path.join(
        HERE, "vocab_staging"))
    args = ap.parse_args()
    mod = load_server()
    gen_dir = args.gen
    if gen_dir is None:
        cur = open(os.path.join(ROOT, "generations", "CURRENT"),
                   encoding="utf-8").read().strip()
        gen_dir = os.path.join(ROOT, "generations", cur)
    db = next(f for f in os.listdir(gen_dir)
              if f.startswith("corpus_") and f.endswith(".db"))
    conn = sqlite3.connect(
        f"file:{os.path.join(gen_dir, db)}?mode=ro", uri=True)
    sources = {eid: did for eid, did in conn.execute(
        "SELECT entry_id, doc_id FROM registry_sources")}
    global _INDEX
    _INDEX = build_index(conn)

    out = {"generation": os.path.basename(gen_dir),
           "figures": {}, "mechanisms": {}}
    fig_vocabs = characteristic(mod.VERIFIED_FIGURES, conn,
                                lambda e: carriers_for_entry(
                                    mod, conn, e, sources),
                                out["figures"])
    mech_vocabs = characteristic(mod.MECHANISMS, conn,
                                 lambda m:
                                 carriers_for_mechanism(
                                     mod, conn, m),
                                 out["mechanisms"])

    os.makedirs(args.out, exist_ok=True)
    staged = os.path.join(args.out, "registry_vocab.json")
    with open(staged, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"staged: {staged}")

    fig_entries = list(mod.VERIFIED_FIGURES)
    mech_entries = list(mod.MECHANISMS)
    fp = mp = 0
    print("\nFIGURES audit (generated vocabulary):")
    for eid, q in FIG_AUDIT.items():
        got, scored = wake(
            mod, fig_entries, fig_vocabs, q)
        ok = got == eid
        fp += ok
        top = "; ".join(f"{e['id']}={s}" for s, e in scored[:3])
        print(f"  {'PASS' if ok else 'FAIL'} {eid}: "
              f"woke={got} [{top}]")
    print("\nMECHANISMS audit (generated vocabulary):")
    for eid, q in MECH_AUDIT.items():
        got, scored = wake(
            mod, mech_entries, mech_vocabs, q)
        ok = got == eid
        mp += ok
        top = "; ".join(f"{e['id']}={s}" for s, e in scored[:3])
        print(f"  {'PASS' if ok else 'FAIL'} {eid}: "
              f"woke={got} [{top}]")
    print(f"\nregistry vocab audit: figures {fp}/14, "
          f"mechanisms {mp}/5")
    return 0 if (fp == 14 and mp == 5) else 1


if __name__ == "__main__":
    sys.exit(main())
