#!/usr/bin/env python3
"""Corpus enrichment for the v22 generation (the vocabulary fix).

Layers, per section of the generation:

- code (deterministic, every section): extracted figures, each a
  verbatim substring of the section text with offsets (unvalidated
  figures are never stored); aliases from the curated concept map
  (both directions) plus acronym pairs mined from the text itself;
  scope tags (program, payer).
- template (deterministic, every section): G1-style questions
  derived from the section heading and its figure kinds.
- fleet (Muse's agent fleet, prioritized batches): natural
  lay-phrased questions, 6-10 per section, written against the
  section text. Batches are built by this tool; outputs merge back
  through validation (dedupe, format) and are recorded in the
  checkpoint so the job resumes where it stopped.

Enrichment is retrieval-keys-only: it lands in the enrich table and
in section_fts (synth_q / aliases fields). It is never served as
evidence and never cited.

Modes:
  --deterministic        fill code+template layers for all sections
  --make-batches N       write the next N-sized fleet batch files
                         (priority order, skipping fleet-done)
  --merge DIR            merge fleet outputs (out_*.jsonl) from DIR
  --refresh-fts          rebuild every section_fts synth_q/aliases
                         from the enrich table (deploy-order safety:
                         run after finalize_vectors.py, which
                         rebuilds sections for vector-canonical
                         docs and resets their FTS enrichment)
  --export               write enrichment.jsonl from the enrich table
"""
import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent
GEN_DIR = SERVER_DIR / "generations" / "g0000"
DB = GEN_DIR / "corpus_g0000.db"
BATCH_DIR = HERE / "enrichment_batches"
CHECKPOINT = HERE / "enrichment_checkpoint.json"

CONCEPT_GROUPS = [
    ["csra", "community spouse resource allowance",
     "spousal impoverishment", "spousal impoverishment resource standard"],
    ["miller trust", "qualified income trust", "qit",
     "income trust"],
    ["backpay", "back pay", "lump sum", "non-recurring lump sum",
     "nonrecurring lump-sum payment", "retroactive benefits",
     "past-due benefits"],
    ["snap", "food stamps", "food assistance",
     "supplemental nutrition assistance program"],
    ["mmmna", "minimum monthly maintenance needs allowance",
     "spousal income standard", "maintenance needs allowance"],
    ["medicaid", "medical assistance"],
    ["ssi", "supplemental security income"],
    ["ssdi", "social security disability insurance",
     "disability benefits"],
    ["able", "investable", "529a", "able account"],
    ["sfc", "structured family caregiving"],
    ["hcbs", "home and community based services", "waiver services"],
    ["magi", "modified adjusted gross income"],
    ["ihcp", "indiana health coverage programs"],
    ["fssa", "family and social services administration"],
    ["dfr", "division of family resources"],
    ["ompp", "office of medicaid policy and planning"],
    ["ltc", "long term care", "long-term care"],
    ["nemt", "non-emergency medical transportation",
     "medical transportation"],
    ["estate recovery", "merp", "medicaid estate recovery"],
    ["spend down", "spenddown", "medically needy"],
]

MONEY_RX = re.compile(r"\$[\d,]+(?:\.\d{2})?")
PCT_RX = re.compile(r"\b\d+(?:\.\d+)?%")
FREQ_RX = re.compile(
    r"\b(?:every\s+(?:\d+|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|twelve)\s+(?:days?|weeks?|months?|years?)|"
    r"\d+\s+per\s+(?:day|week|month|year)|"
    r"once\s+(?:a|per|every)\s+(?:day|week|month|year)|"
    r"annually|monthly|weekly)\b", re.IGNORECASE)
ACRONYM_RX = re.compile(r"([A-Z][A-Za-z][A-Za-z ]{2,60}?)\s*"
                        r"\(([A-Z]{2,6})\)")


def figures_of(text):
    out, seen = [], set()
    for m in MONEY_RX.finditer(text):
        key = ("money", m.group(0))
        if key not in seen:
            seen.add(key)
            out.append({"value": m.group(0), "kind": "money",
                        "start": m.start(), "end": m.end()})
    for m in FREQ_RX.finditer(text):
        key = ("frequency", m.group(0).lower())
        if key not in seen:
            seen.add(key)
            out.append({"value": m.group(0), "kind": "frequency",
                        "start": m.start(), "end": m.end()})
    for m in PCT_RX.finditer(text):
        key = ("percent", m.group(0))
        if key not in seen:
            seen.add(key)
            out.append({"value": m.group(0), "kind": "percent",
                        "start": m.start(), "end": m.end()})
    return out[:12]


def aliases_of(title, heading, text):
    low = f"{title}\n{heading}\n{text[:4000]}".lower()
    out, seen = [], set()
    for group in CONCEPT_GROUPS:
        if any(term in low for term in group):
            for term in group:
                if term not in seen:
                    seen.add(term)
                    out.append(term)
    for full, acr in ACRONYM_RX.findall(text[:6000]):
        for term in (full.strip().lower(), acr.lower()):
            if term and term not in seen:
                seen.add(term)
                out.append(term)
    return out[:24]


def scope_tags_of(title, heading, text):
    low = f"{title}\n{heading}\n{text[:4000]}".lower()
    tags = []
    for name, terms in (
            ("Medicaid", ("medicaid",)),
            ("SNAP", ("snap", "food assistance", "food stamps")),
            ("SSI/SSDI", ("supplemental security income",
                          "social security disability")),
            ("MAGI/HIP", ("magi", "healthy indiana plan")),
            ("Medicare", ("medicare",)),
            ("Long-term care", ("long term care", "nursing facilit",
                                "institutional")),
            ("Waiver", ("waiver", "hcbs"))):
        if any(t in low for t in terms):
            tags.append(name)
    if "fee-for-service" in low or "fee for service" in low:
        tags.append("payer:FFS")
    if "managed care" in low:
        tags.append("payer:managed care")
    if "managed health services" in low:
        tags.append("payer:MHS")
    return tags


def topic_of(title, heading):
    topic = heading or title
    if "://" in topic:
        # Scraped publication docs whose title is a raw URL: use
        # the slug words as the topic instead of the URL itself.
        slug = topic.rstrip("/").rsplit("/", 1)[-1]
        slug = re.sub(r"\.(pdf|epub|html?)$", "", slug)
        slug = re.sub(r"^[\d\-]+", "", slug)
        topic = re.sub(r"[-_]+", " ", slug).strip() or "this document"
    topic = re.sub(r"^\d{4}\.\d{2}\.\d{2}(\.\d{2})?\s*", "", topic)
    topic = re.sub(r"^IHCPPM\s*", "", topic)
    topic = re.sub(r"^IHCP Provider Reference Module:\s*", "", topic)
    # POMS titles end in an effective date ("... - 03/25/2025");
    # the date is not a topic (it became the whole topic when the
    # long-title tail-split in template_questions took the last
    # segment: "What are the rules for 03/25/2025?").
    topic = re.sub(r"\s*-\s*\d{2}/\d{2}/\d{4}\s*$", "", topic)
    return topic.strip(" :") or title


def template_questions(title, heading, text, figures):
    topic = topic_of(title, heading)
    if len(topic) > 90:
        # Long manual titles (POMS especially): keep the tail,
        # which carries the subject, so questions stay bounded.
        topic = topic.split(" - ")[-1] if " - " in topic \
            else topic[:90]
    if len(topic) > 110:
        topic = topic[:110].rsplit(" ", 1)[0]
    low_topic = topic.lower()
    qs = [f"What are the rules for {low_topic}?",
          f"What does {low_topic} cover?"]
    kinds = {f["kind"] for f in figures}
    if "money" in kinds:
        qs.append(f"What is the dollar amount or limit for "
                  f"{low_topic}?")
        qs.append(f"How much money is allowed under {low_topic}?")
    if "frequency" in kinds:
        qs.append(f"How often does {low_topic} apply or get covered?")
    if "percent" in kinds:
        qs.append(f"What percentage applies under {low_topic}?")
    low = text.lower()
    if any(w in low for w in ("eligible", "eligibility", "qualif",
                              "must meet", "required to")):
        qs.append(f"Who qualifies under {low_topic}?")
    if any(w in low for w in ("apply", "application", "enroll")):
        qs.append(f"How do I apply or sign up for {low_topic}?")
    out, seen = [], set()
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def load_sections(conn):
    return conn.execute(
        "SELECT s.section_id, s.doc_id, s.heading, s.title, s.text,"
        " d.url, d.source_file, d.doc_type FROM sections s"
        " JOIN docs d ON d.doc_id = s.doc_id ORDER BY s.row"
    ).fetchall()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_checkpoint():
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    return {"total_sections": 0, "deterministic_done": False,
            "fleet_done": [], "updated": None}


def write_checkpoint(cp):
    cp["updated"] = now()
    CHECKPOINT.write_text(json.dumps(cp, indent=1), encoding="utf-8")


TEMPLATE_RX = re.compile(
    r"^(What are the rules for |What does .+ cover\?|"
    r"What is the dollar amount or limit for |"
    r"How much money is allowed under |"
    r"How often does .+ apply or get covered\?|"
    r"What percentage applies under |Who qualifies under |"
    r"How do I apply or sign up for )")


def deterministic(conn):
    rows = load_sections(conn)
    for sid, _did, heading, title, text, _u, _sf, _dt in rows:
        figs = figures_of(text)
        aliases = aliases_of(title, heading, text)
        tags = scope_tags_of(title, heading, text)
        qs = template_questions(title, heading, text, figs)
        existing = conn.execute(
            "SELECT questions, writer FROM enrich WHERE section_id=?",
            (sid,)).fetchone()
        if existing and "fleet" in (existing[1] or ""):
            # Keep fleet questions; refresh the code layers around
            # them. IDEMPOTENT: previously generated template-form
            # questions are stripped before the current set is
            # added, so re-running this pass never accumulates
            # variants (a fleet original phrased exactly like a
            # template form is dropped too; the regenerated
            # template covers the same ground).
            fleet_qs = [q for q in json.loads(existing[0] or "[]")
                        if not TEMPLATE_RX.match(q)]
            merged = fleet_qs + [q for q in qs if q not in fleet_qs]
            merged = merged[:16]
            conn.execute(
                "INSERT OR REPLACE INTO enrich VALUES (?,?,?,?,?,?,?,?)",
                (sid, json.dumps(merged), json.dumps(aliases),
                 json.dumps(figs), json.dumps(tags),
                 existing[1], 1, now()))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO enrich VALUES (?,?,?,?,?,?,?,?)",
                (sid, json.dumps(qs), json.dumps(aliases),
                 json.dumps(figs), json.dumps(tags),
                 "template+code", 1, now()))
        refresh_fts_row(conn, sid)
    conn.commit()
    cp = read_checkpoint()
    cp["total_sections"] = len(rows)
    cp["deterministic_done"] = True
    write_checkpoint(cp)
    log.info("deterministic enrichment written for %d sections",
             len(rows))
    return 0


def refresh_fts_row(conn, sid):
    row = conn.execute(
        "SELECT questions, aliases FROM enrich WHERE section_id=?",
        (sid,)).fetchone()
    srow = conn.execute(
        "SELECT row FROM sections WHERE section_id=?",
        (sid,)).fetchone()
    if not row or not srow:
        return
    questions = json.loads(row[0] or "[]")
    aliases = json.loads(row[1] or "[]")
    conn.execute(
        "UPDATE section_fts SET synth_q=?, aliases=? WHERE rowid=?",
        (" ".join(questions), " ".join(aliases), srow[0]))


def refresh_fts(conn):
    n = 0
    for (sid,) in conn.execute("SELECT section_id FROM enrich"):
        refresh_fts_row(conn, sid)
        n += 1
    conn.commit()
    log.info("section_fts enrichment refreshed for %d sections", n)
    return 0


PRIORITY_HINTS = (
    "3006.00.00", "2615.75.15", "3010.20.10", "3005.10.05",
    "PROMOD00051", "vision", "spousal", "investable", "snap",
    "eligibility guide",
)


NAV_RX = re.compile(
    r"mission and vision|facebook|newsletter|mission values|"
    r"grants office|program information -|survey|hub:", re.IGNORECASE)


def priority_key(row):
    """Content-first ordering for the fleet: sections carrying
    figures in rule documents first, navigation/portal text last."""
    sid, _did, heading, title, text, url, source_file, doc_type = row
    low = (title + " " + url + " " + heading).lower()
    if NAV_RX.search(title) or len(text.strip()) < 300:
        return (6, _did)
    figs = figures_of(text)
    kinds = {f["kind"] for f in figs}
    rule_doc = doc_type in ("policy manual", "provider module",
                            "POMS operations manual",
                            "statute/regulation")
    hinted = any(h in low for h in PRIORITY_HINTS)
    if hinted and figs:
        tier = 0
    elif rule_doc and ("money" in kinds or "frequency" in kinds):
        tier = 1
    elif rule_doc:
        tier = 2
    elif source_file == "in" and figs:
        tier = 3
    elif source_file == "in":
        tier = 4
    elif figs:
        tier = 5
    else:
        tier = 6
    return (tier, _did)


def make_batches(conn, size):
    BATCH_DIR.mkdir(exist_ok=True)
    cp = read_checkpoint()
    done = set(cp.get("fleet_done", []))
    rows = sorted(load_sections(conn), key=priority_key)
    todo = [r for r in rows if r[0] not in done]
    existing = sorted(BATCH_DIR.glob("batch_*.jsonl"))
    nxt = len(existing)
    made = 0
    for i in range(0, len(todo), size):
        chunk = todo[i:i + size]
        path = BATCH_DIR / f"batch_{nxt + made:03d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for sid, _did, heading, title, text, _u, _sf, _dt in chunk:
                enrich = conn.execute(
                    "SELECT questions FROM enrich WHERE section_id=?",
                    (sid,)).fetchone()
                f.write(json.dumps({
                    "section_id": sid, "title": title,
                    "heading": heading, "text": text[:1400],
                    "draft_questions": json.loads(
                        enrich[0] if enrich else "[]")}) + "\n")
        made += 1
    log.info("wrote %d batch files (%d sections pending fleet)",
             made, len(todo))
    return 0


def merge(conn, out_dir):
    cp = read_checkpoint()
    done = set(cp.get("fleet_done", []))
    merged, rejected = 0, 0
    for path in sorted(Path(out_dir).glob("out_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                rejected += 1
                continue
            sid = rec.get("section_id")
            row = conn.execute(
                "SELECT questions, aliases, figures, scope_tags"
                " FROM enrich WHERE section_id=?", (sid,)).fetchone()
            if row is None:
                rejected += 1
                continue
            qs, seen = [], set()
            for q in rec.get("questions", []):
                q = " ".join(str(q).split()).strip()
                if 12 <= len(q) <= 220 and "http" not in q \
                        and q.lower() not in seen:
                    seen.add(q.lower())
                    qs.append(q)
            if len(qs) < 3:
                rejected += 1
                continue
            aliases = json.loads(row[1] or "[]")
            for a in rec.get("aliases_extra", []):
                a = str(a).strip().lower()
                if a and len(a) <= 80 and a not in aliases:
                    aliases.append(a)
            conn.execute(
                "INSERT OR REPLACE INTO enrich VALUES (?,?,?,?,?,?,?,?)",
                (sid, json.dumps(qs), json.dumps(aliases[:24]),
                 row[2], row[3], "fleet+code", 1, now()))
            refresh_fts_row(conn, sid)
            done.add(sid)
            merged += 1
    conn.commit()
    cp["fleet_done"] = sorted(done)
    write_checkpoint(cp)
    log.info("fleet sections merged: %d (rejected rows: %d); "
             "fleet total now %d", merged, rejected, len(done))
    return 0


def export(conn):
    out = SERVER_DIR / "enrichment.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for row in conn.execute(
                "SELECT section_id, questions, aliases, figures,"
                " scope_tags, writer FROM enrich"):
            f.write(json.dumps({
                "section_id": row[0],
                "questions": json.loads(row[1] or "[]"),
                "aliases": json.loads(row[2] or "[]"),
                "figures": json.loads(row[3] or "[]"),
                "scope_tags": json.loads(row[4] or "[]"),
                "writer": row[5]}) + "\n")
            n += 1
    log.info("exported %d enrichment rows to %s", n, out)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deterministic", action="store_true")
    ap.add_argument("--make-batches", type=int, default=0,
                    metavar="SIZE")
    ap.add_argument("--merge", default="")
    ap.add_argument("--refresh-fts", action="store_true")
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(str(DB))
    try:
        if args.deterministic:
            return deterministic(conn)
        if args.make_batches:
            return make_batches(conn, args.make_batches)
        if args.merge:
            return merge(conn, args.merge)
        if args.refresh_fts:
            return refresh_fts(conn)
        if args.export:
            return export(conn)
        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
