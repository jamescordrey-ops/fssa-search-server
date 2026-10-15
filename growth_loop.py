#!/usr/bin/env python3
"""FSSA v21 growth loop: the staging pass (WS3b + WS5).

Successor to v15/growth_loop.py. The v15 tool clustered gap
rows and drafted NEW MECHANISM candidates with a NIM seat
into review_queue.json. v21 changes the job: the registries
are no longer the growth surface — the CORPUS and the
RETRIEVAL layer are. This runner reads gap candidates (the
server's gap_log.jsonl rows, v21 shape: question, badge,
gate, missing, ask_id, class_guess), classifies each one,
and stages review cards. It NEVER touches the live corpus,
the embeddings, or the server; merging is a separate
human-approved act (see "Merge interface" below, also
written into every staging dir as README.md).

Classes:
  retrieval_miss    The governing document IS in the corpus
                    (proven by this runner's own coverage
                    check) but retrieval never surfaced it:
                    the eyeglasses/divisor pattern. Becomes
                    a REGRESSION CASE proposal for
                    tools/retrieval_cases.jsonl, plus a
                    lay-term suggestion. Never a fetch.
  missing_document  The corpus does not hold the material.
                    When a source URL is known (branch-out
                    rows carry URLs in `missing`) and its
                    domain is allowlisted, the runner fetches
                    + chunks it (never in --dry-run). With
                    no allowlisted URL it stages a link-only
                    card.
  newer_edition     The material is a newer edition of
                    something (or not yet published): the
                    row's class_guess says so, or the
                    question names a future year. With a
                    known allowlisted URL it fetches and
                    stages a diff summary against the corpus
                    doc it supersedes; without one it stages
                    an awaiting-publication card.
  superseded_figure A figure in the corpus/registry was
                    superseded (the row's class_guess or a
                    figure-shaped missing item says so).
                    Card only: figures change through the
                    registry's human edit, never a fetch.
  unknown           None of the checks fired. Card only,
                    honest about it.

Fetch allowlist (the corpus's own source domains):
in.gov, fssa.in.gov, ssa.gov, cms.gov, fns.usda.gov,
usda.gov, federalregister.gov, law.cornell.edu. Out-of-
allowlist candidates are recorded as link-only cards and
NEVER fetched.

Watchlist (WS5): --watchlist also stages standing cards for
the not-yet-published 2027 items (CMS spousal impoverishment
standards, the 2027 Part B premium, the Indiana 2027 waiver
income cap), each with its expected source domains, so when
they publish, a normal staging pass picks them up as
newer-edition / superseded-figure cards. SSA's 2027 COLA
material is NOT on the watchlist: it published 2026-10-14
and is already in the corpus (SSA press release + 2027 fact
sheet + the FNS SNAP FY27 memo, incorporated in v19).

Merge interface (what a human merge consumes):
  staging/<id>.card.json + <id>.card.md   one per gap/watch
  staging/fetched/<id>.json              fetched docs, in
        corpus shape {title, url, source, verified, text}
        — merge = add to the corpus build, re-run the
        embeddings generation, redeploy.
  staging/regression_proposals.jsonl     {question,
        expect_any, note, ask_id} — merge = append to
        tools/retrieval_cases.jsonl, run the harness.
  staging/terms_proposals.json           concept -> corpus
        terms — merge = fold into growth_terms.json next to
        the server file (the server reads it at boot), or
        into SYNONYMS for permanent vocabulary.
Nothing merges itself. Every card carries status
"staged — pending human merge".

Usage:
  python3 growth_loop.py --gaps gap_log.jsonl --dry-run
  python3 growth_loop.py --gaps gap_log.jsonl --watchlist
Defaults: gaps = fixtures/gap_fixtures.jsonl (the offline
demonstration set), corpus = the v21 dir's versioned
corpora, staging = ./staging next to this script.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
V21_DIR = os.path.dirname(HERE)

ALLOWLIST = ("in.gov", "fssa.in.gov", "ssa.gov", "cms.gov",
             "fns.usda.gov", "usda.gov", "federalregister.gov",
             "law.cornell.edu")

WATCHLIST = [
    {"name": "CMS 2027 spousal impoverishment standards",
     "domains": ["cms.gov"],
     "status": "awaiting publication",
     "note": ("MMMNA minimum/maximum and CSRA minimum/maximum "
              "for 2027. CMS publishes these each fall; not "
              "posted as of 2026-10-14. On publication this "
              "becomes a superseded-figure card for the csra "
              "and mmmna registry entries (which are dated "
              "through 2026-12-31).")},
    {"name": "2027 Medicare Part B premium",
     "domains": ["cms.gov"],
     "status": "awaiting publication",
     "note": ("Expected late November 2026 (the usual CMS "
              "announcement window). Not posted as of "
              "2026-10-14.")},
    {"name": "Indiana 2027 HCBS waiver income cap",
     "domains": ["in.gov", "fssa.in.gov"],
     "status": "awaiting publication",
     "note": ("Indiana had not posted a 2027 cap as of "
              "2026-10-14. 300% of the 2027 SSI FBR ($1,029) "
              "implies about $3,087, UNCONFIRMED; the waiver "
              "registry entry carries that as a successor "
              "note, never as a figure. On publication this "
              "becomes a superseded-figure card for "
              "waiver_income_cap.")},
]

STOP = set(
    "a an the and or of to in on for with as at by from is are "
    "was were be been it its this that i you he she we they "
    "what when where why how do does did can could should "
    "would will has have had not no if then than so under "
    "over often frequently during before after between within "
    "without more most very all any each other about into "
    "through above below there here out up down off per via "
    "only also just such own same".split())

GENERIC = {"indiana", "medicaid", "medicare", "state",
           "federal", "program", "question", "service",
           "services"}

_URL_RX = re.compile(r"https?://[^\s\"'\]]+")


def _host(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def _allowlisted(url):
    host = _host(url)
    return any(host == d or host.endswith("." + d)
               for d in ALLOWLIST)


def _content_words(text):
    return [w for w in re.findall(r"[a-z]{3,}", (text or "")
                                  .lower()) if w not in STOP]


def _windows(text, size=1500, overlap=300):
    """The runner's plain chunker (coverage checks and chunk
    counts only; a fetched doc is re-chunked by the server's
    own pipeline at merge time)."""
    paras = [p.strip() for p in (text or "").split("\n")
             if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 > size and cur:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p if len(cur) > overlap else p
        else:
            cur = (cur + "\n" + p).strip()
    if cur.strip():
        chunks.append(cur)
    return chunks or [(text or "")[:size]]


def _term_in(term, low):
    """Term match with the light stemming the manuals need:
    'covered' also matches covers/coverage ('cover'), and a
    plural matches its singular. Without it the IHCP Vision
    module (which writes 'covers' and 'aged 21 and older',
    never 'covered for adults') loses the coverage check to
    any page that happens to use the question's exact forms."""
    if term in low:
        return True
    if term.endswith("ed") and term[:-2] in low:
        return True
    if term.endswith("s") and term[:-1] in low:
        return True
    return False


class CorpusIndex:
    """Doc-level coverage index over the local corpora."""

    def __init__(self, paths, growth_terms_path=None):
        self.docs = []
        self.urls = set()
        for path in paths:
            with open(path, encoding="utf-8") as f:
                for d in json.load(f):
                    self.docs.append(d)
                    if d.get("url"):
                        self.urls.add(d["url"])
        # The server's lay-term registrations (WS3c) inform
        # the coverage check too: a registered concept's
        # corpus terms are evidence the doc was findable.
        self.growth_terms = {}
        gt_path = growth_terms_path or os.path.join(
            V21_DIR, "growth_terms.json")
        try:
            with open(gt_path, encoding="utf-8") as f:
                raw = json.load(f)
            self.growth_terms = {
                str(k).lower(): [str(x).lower() for x in v]
                for k, v in raw.items()
                if isinstance(v, list)}
        except Exception:
            pass

    def best_coverage(self, question):
        """(doc, matched_terms, chunk_count) for the doc whose
        chunk best co-covers the question's distinctive terms,
        or (None, [], 0). Scoring mirrors the server's rescue
        pass: distinct terms first, then term rarity (document
        frequency over the corpus), then source authority
        (Indiana-state and manual-class docs outrank generic
        program pages — without the authority term, a
        medicaid.gov screening page out-covered the IHCP
        Vision module on the eyeglasses fixture)."""
        import math
        terms = []
        for w in _content_words(question):
            if w not in GENERIC and w not in terms:
                terms.append(w)
        ql = (question or "").lower()
        for concept, mapped in self.growth_terms.items():
            if concept in ql:
                for t in mapped:
                    if t not in terms:
                        terms.append(t)
        if not terms:
            return None, [], 0
        df = {t: 0 for t in terms}
        for d in self.docs:
            blob = ((d.get("title") or "") + " "
                    + (d.get("text") or "")).lower()
            for t in terms:
                if _term_in(t, blob):
                    df[t] += 1
        rarity = {t: math.log(1.0 + len(self.docs)
                              / (1.0 + df[t])) for t in terms}
        best = (None, [], 0, None)
        for d in self.docs:
            title_low = (d.get("title") or "").lower()
            url_low = (d.get("url") or "").lower()
            auth = (2.0 if "in.gov" in url_low else 0.0) + (
                2.0 if any(k in title_low for k in
                           ("policy manual",
                            "provider reference module",
                            "ihcppm")) else 0.0)
            chunks = _windows(d.get("text") or "")
            for ch in chunks:
                low = title_low + " " + ch.lower()
                matched = [t for t in terms
                           if _term_in(t, low)]
                if not matched:
                    continue
                score = (len(set(matched)),
                         round(sum(rarity[t]
                                   for t in set(matched)), 3),
                         auth)
                if best[3] is None or score > best[3]:
                    best = (d, matched, len(chunks), score)
        # A doc "covers" the gap when one chunk carries at
        # least 3 distinctive terms, or at least 2 including a
        # term that names the topic (longest matched term).
        if len(best[1]) >= 3 or (len(best[1]) >= 2
                                 and max(len(t) for t
                                         in best[1]) >= 7):
            return best[0], best[1], best[2]
        return None, [], 0


def classify(gap, index):
    """(cls, evidence) for one gap row. Precedence:
    explicit newer-edition/superseded signals first, then the
    corpus-coverage proof (retrieval_miss), then the gate's
    own meaning, then unknown."""
    q = gap.get("question") or ""
    guess = (gap.get("class_guess") or "").lower()
    missing = gap.get("missing") or []
    urls = []
    for mtxt in missing:
        urls += _URL_RX.findall(mtxt or "")
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", q)]
    if guess == "newer_edition" or (
            years and max(years) > datetime.date.today().year):
        return "newer_edition", {"source_urls": urls}
    if guess == "superseded_figure" or any(
            "superseded" in (mtxt or "").lower()
            or "stale" in (mtxt or "").lower()
            for mtxt in missing):
        return "superseded_figure", {"source_urls": urls}
    doc, matched, nchunks = index.best_coverage(q)
    if doc is not None:
        return "retrieval_miss", {
            "doc_title": doc.get("title"),
            "doc_url": doc.get("url"),
            "matched_terms": matched,
            "doc_chunks": nchunks,
            "source_urls": urls}
    if gap.get("gate") in ("branch_out", "sufficiency") \
            or guess == "missing_document":
        return "missing_document", {"source_urls": urls}
    return "unknown", {"source_urls": urls}


def lay_term_suggestions(gap, evidence):
    """For a retrieval miss: the question's own phrases as
    concepts, mapped to the terms the matched document
    actually uses (section numbers in its title/text and its
    title's vocabulary) — the growth_terms.json shape."""
    if not evidence.get("doc_title"):
        return []
    title = evidence["doc_title"]
    terms = []
    for num in re.findall(r"\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?",
                          title):
        if num not in terms:
            terms.append(num)
    for code in re.findall(r"\(([A-Z]{2,}\d+)\)", title):
        terms.append(code.lower())
    for w in _content_words(title):
        if w not in terms and w not in ("ihcp", "ihcppm"):
            terms.append(w)
    # Concepts are the question terms the matched chunk
    # actually carries (the distinctive end of the question),
    # longest first — not the question's generic vocabulary.
    q_words = set(_content_words(gap.get("question") or ""))
    concepts = [t for t in evidence.get("matched_terms", [])
                if t not in GENERIC and len(t) >= 4
                and t in q_words]
    concepts.sort(key=len, reverse=True)
    return [{"concept": c, "corpus_terms": terms[:6]}
            for c in concepts[:3]]


def fetch_and_chunk(url):
    """Fetch one allowlisted URL and chunk it. Non-dry mode
    only; the caller has already checked the allowlist."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "fssa-growth-loop/21"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>",
                  " ", raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    title = ""
    mt = re.search(r"(?is)<title>(.*?)</title>", raw)
    if mt:
        title = re.sub(r"\s+", " ", mt.group(1)).strip()
    return {"title": title or url, "url": url,
            "source": "growth-loop fetch", "verified": False,
            "text": text}, len(_windows(text))


def card_md(card):
    lines = [f"# {card['id']}: {card['class']}",
             "",
             f"- Status: {card['status']}",
             f"- Triggering question: {card['question']}",
             f"- Ask id: {card.get('ask_id')}",
             f"- Gap gate: {card.get('gate')}",
             f"- Class: {card['class']}",
             f"- Source URL: {card.get('source_url')}",
             f"- Fetch date: {card.get('fetch_date')}",
             f"- Chunk count: {card.get('chunk_count')}",
             f"- Diff summary: {card.get('diff_summary')}",
             ""]
    if card.get("matched_doc"):
        lines += [f"Matched corpus doc: {card['matched_doc']}",
                  f"Matched terms: "
                  f"{', '.join(card.get('matched_terms', []))}",
                  ""]
    if card.get("lay_term_suggestions"):
        lines.append("Lay-term suggestions:")
        for s in card["lay_term_suggestions"]:
            lines.append(f"- {s['concept']} -> "
                         f"{', '.join(s['corpus_terms'])}")
        lines.append("")
    lines.append(card.get("note", ""))
    return "\n".join(lines) + "\n"


MERGE_README = """# Staging dir — merge interface (human act)

Every card here is status "staged — pending human merge".
Nothing in this directory is live. To merge, a human:

1. Fetched docs (fetched/*.json, corpus shape): add to the
   corpus build, re-run embeddings generation, redeploy.
2. regression_proposals.jsonl: append its rows to
   tools/retrieval_cases.jsonl, run the retrieval harness
   (it must pass before the merge ships).
3. terms_proposals.json: fold the concept -> corpus-terms
   mappings into growth_terms.json next to the server file
   (read at boot), or into SYNONYMS for permanent vocabulary.
4. superseded_figure / newer_edition cards: edit the
   registry entry's figures + valid_from/valid_to by hand,
   from the card's source; never from the card alone.

The runner never writes outside this staging dir.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gaps", default=os.path.join(
        HERE, "fixtures", "gap_fixtures.jsonl"),
        help="path to a gap_log.jsonl copy (v21 row shape)")
    ap.add_argument("--corpus", nargs="*", default=[
        os.path.join(V21_DIR, "corpus_v19.json"),
        os.path.join(V21_DIR, "corpus_federal_v19.json")])
    ap.add_argument("--staging", default=os.path.join(
        HERE, "staging"))
    ap.add_argument("--dry-run", action="store_true",
                    help="offline: never fetch; allowlisted "
                         "fetch targets stage as fetch-pending")
    ap.add_argument("--watchlist", action="store_true",
                    help="also stage the WS5 watchlist cards")
    args = ap.parse_args()

    gaps = []
    with open(args.gaps, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                gaps.append(json.loads(line))
    index = CorpusIndex(args.corpus)
    os.makedirs(os.path.join(args.staging, "fetched"),
                exist_ok=True)

    # Dedup state: corpus URLs + prior staged cards' URLs.
    seen_urls = set(index.urls)
    prior_ids = set()
    for name in os.listdir(args.staging):
        if name.endswith(".card.json"):
            prior_ids.add(name[:-len(".card.json")])
            try:
                with open(os.path.join(args.staging, name),
                          encoding="utf-8") as f:
                    u = json.load(f).get("source_url")
                if u:
                    seen_urls.add(u)
            except Exception:
                pass

    today = datetime.date.today().isoformat()
    cards, proposals, terms_out = [], [], {}

    def stage(card):
        cid = card["id"]
        if cid in prior_ids:
            print(f"skip (already staged): {cid}")
            return
        with open(os.path.join(args.staging,
                               cid + ".card.json"), "w",
                  encoding="utf-8") as f:
            json.dump(card, f, indent=1)
        with open(os.path.join(args.staging,
                               cid + ".card.md"), "w",
                  encoding="utf-8") as f:
            f.write(card_md(card))
        cards.append(card)
        print(f"staged {cid}: {card['class']} — "
              f"{card['question'][:70]}")

    for gap in gaps:
        cls, ev = classify(gap, index)
        q = gap.get("question") or ""
        slug = hashlib.sha256(
            (q + (gap.get("ask_id") or "")).encode()
        ).hexdigest()[:10]
        card = {
            "id": f"gap-{slug}",
            "status": "staged — pending human merge",
            "class": cls,
            "question": q,
            "ask_id": gap.get("ask_id"),
            "gate": gap.get("gate"),
            "badge": gap.get("badge"),
            "missing": gap.get("missing"),
            "class_guess_from_server": gap.get("class_guess"),
            "source_url": None, "fetch_date": None,
            "chunk_count": None, "diff_summary": None,
            "note": ""}
        urls = [u for u in ev.get("source_urls", ())
                if u not in seen_urls]
        if cls == "retrieval_miss":
            card["matched_doc"] = ev["doc_title"]
            card["matched_terms"] = ev["matched_terms"]
            card["chunk_count"] = ev["doc_chunks"]
            card["note"] = (
                "The governing document is already in the "
                "corpus; retrieval missed it. This is a "
                "regression-case proposal, not a fetch.")
            card["lay_term_suggestions"] = \
                lay_term_suggestions(gap, ev)
            expect = []
            for pat in re.findall(
                    r"\d{4}\.\d{2}\.\d{2}(?:\.\d{2})?",
                    ev["doc_title"] or ""):
                expect.append(pat)
            if not expect:
                expect = [(ev["doc_title"] or "")[:40]]
            proposals.append({
                "question": q, "expect_any": expect,
                "note": ("growth-loop proposal from ask "
                         f"{gap.get('ask_id')}: retrieval "
                         "miss; doc was in corpus all along"),
                "ask_id": gap.get("ask_id")})
            for s in card["lay_term_suggestions"]:
                terms_out.setdefault(s["concept"],
                                     s["corpus_terms"])
        elif cls in ("missing_document", "newer_edition"):
            target = next((u for u in urls
                           if _allowlisted(u)), None)
            link_only = next((u for u in urls), None)
            if target:
                card["source_url"] = target
                seen_urls.add(target)
                if args.dry_run:
                    card["note"] = (
                        "DRY RUN: allowlisted fetch target "
                        "identified; nothing fetched. A live "
                        "run fetches, chunks, and stages the "
                        "doc under fetched/.")
                    card["diff_summary"] = (
                        "not computed (dry run)" if cls ==
                        "newer_edition" else None)
                else:
                    doc, nch = fetch_and_chunk(target)
                    with open(os.path.join(
                            args.staging, "fetched",
                            card["id"] + ".json"), "w",
                            encoding="utf-8") as f:
                        json.dump(doc, f, indent=1)
                    card["fetch_date"] = today
                    card["chunk_count"] = nch
                    card["note"] = (
                        "Fetched from the allowlist and "
                        "staged under fetched/ in corpus "
                        "shape. Merge stays human.")
                    if cls == "newer_edition":
                        card["diff_summary"] = (
                            "fetched text staged; edition diff "
                            "is computed at merge review "
                            "against the superseded corpus doc")
            elif link_only:
                card["source_url"] = link_only
                card["note"] = (
                    "Link-only card: the source domain is "
                    "not on the fetch allowlist, so nothing "
                    "was fetched. A human may add the domain "
                    "or fetch it manually.")
            else:
                card["note"] = (
                    "No source URL in the gap row; a human "
                    "supplies the source before any fetch. "
                    + ("Standing target: see the watchlist."
                       if cls == "newer_edition" else ""))
        elif cls == "superseded_figure":
            card["note"] = (
                "Figure changes go through the registry's "
                "human edit (figures + valid_from/valid_to); "
                "the runner never fetches a figure into force.")
        else:
            card["note"] = (
                "No classification check fired. The runner "
                "leaves this for human triage rather than "
                "guessing.")
        stage(card)

    if args.watchlist:
        for w in WATCHLIST:
            wid = "watch-" + hashlib.sha256(
                w["name"].encode()).hexdigest()[:10]
            stage({
                "id": wid,
                "status": "staged — pending human merge",
                "class": "watchlist",
                "question": w["name"],
                "ask_id": None, "gate": None, "badge": None,
                "missing": None, "class_guess_from_server":
                    None,
                "source_url": None, "fetch_date": None,
                "chunk_count": None, "diff_summary": None,
                "expected_domains": w["domains"],
                "publication_status": w["status"],
                "note": w["note"]})

    if proposals:
        with open(os.path.join(args.staging,
                               "regression_proposals.jsonl"),
                  "w", encoding="utf-8") as f:
            for p in proposals:
                f.write(json.dumps(p) + "\n")
    if terms_out:
        with open(os.path.join(args.staging,
                               "terms_proposals.json"), "w",
                  encoding="utf-8") as f:
            json.dump(terms_out, f, indent=1)
    with open(os.path.join(args.staging, "README.md"), "w",
              encoding="utf-8") as f:
        f.write(MERGE_README)

    print(f"\ngaps read: {len(gaps)}; cards staged: "
          f"{len(cards)}; regression proposals: "
          f"{len(proposals)}; term proposals: {len(terms_out)}")
    print(f"mode: {'dry-run (offline, nothing fetched)' if args.dry_run else 'live (allowlist fetches permitted)'}")
    print(f"staging dir: {args.staging} — nothing here is "
          "live; merging is a human act.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
