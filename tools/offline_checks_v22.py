#!/usr/bin/env python3
"""v22 offline checks: no network, no model calls, no PC.

 1. FTS5 + fts5vocab present in this Python's sqlite3.
 2. retrieval_profile.json / pins.json valid; 10 pins converted;
    every pin carries trigger/target/reason/case_id keys.
 3. Generation g0000 counts match its manifest; the vector
    reconciliation classes are recorded.
 4. Registry lint summary is in the manifest.
 5. Grep asserts: the -15 provider penalty and the -0.03 title
    demotion are gone from the server source.
 6. Enrichment: every section has a row; a 200-row sample of
    stored figures revalidates as substrings at stored offsets.
 7. Channel unit checks against the live v2 code (harness
    import, models off): T finds the divisor section on the
    title-only term; X finds a section number; R resolves the
    divisor entry to its source doc; the typo expansion outcome
    for 'devisor' is reported as measured; pins serve the Vision
    module for the eyeglasses question; the three anchor
    questions put the expected doc in the top 3.
 8. v23: RETRIEVAL=legacy in the environment changes nothing
    (the flag and the legacy engine are deleted; the separate
    'legacy' phase now asserts the v2 engine serves anyway).
    This replaces the v22 'legacy path intact' check.
"""
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILURES = []


def check(name, ok, detail=""):
    log.info("%s %s %s", "PASS" if ok else "FAIL", name, detail)
    if not ok:
        FAILURES.append(f"{name}: {detail}")


def main_checks():
    # 1. FTS5
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, 'row')")
        fts5 = True
    except Exception as e:
        fts5 = False
        log.info("FTS5 probe error: %s", e)
    check("fts5-present", fts5, f"sqlite {sqlite3.sqlite_version}")

    # 2. profile + pins
    profile = json.loads((ROOT / "retrieval_profile.json").read_text())
    pins = json.loads((ROOT / "pins.json").read_text())
    check("profile-versioned", bool(profile.get("version")),
          str(profile.get("version")))
    pin_list = pins.get("pins", [])
    check("pins-count", len(pin_list) == 10, f"{len(pin_list)} pins")
    check("pins-shape", all(
        set(("trigger_tokens", "target", "reason")) <= set(p)
        and "case_id" in p for p in pin_list))

    # 3. generation vs manifest
    gen_dir = ROOT / "generations" / "g0000"
    manifest = json.loads((gen_dir / "manifest_g0000.json").read_text())
    conn = sqlite3.connect(str(gen_dir / "corpus_g0000.db"))
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("docs", "chunks", "sections")}
    check("generation-counts",
          counts["docs"] == manifest["docs"]
          and counts["chunks"] == manifest["chunks"]
          and counts["sections"] == manifest["sections"],
          str(counts))
    rec = json.loads((gen_dir / "vector_reconciliation.json").read_text())
    live = rec.get("recorded_live_state", {})
    check("reconciliation-recorded",
          rec.get("vectors_state") == "pending"
          and live.get("delta_rows") == -440
          and live.get("missing_urls") == 134
          and live.get("mismatched_urls") == 393
          and live.get("orphan_urls") == 1
          and "finalize" in rec,
          f"recorded delta={live.get('delta_rows')}")

    # 4. registry lint summary
    lint = manifest.get("registry_lint", [])
    stat = {}
    for row in lint:
        stat[row["status"]] = stat.get(row["status"], 0) + 1
    check("registry-lint", len(lint) == 12, f"statuses={stat}")

    # 5. demotions deleted
    src = (ROOT / "fssa_search_server.py").read_text()
    check("penalty-deleted", "provider_penalty" not in src)
    check("demotion-deleted", "lift -= 0.03" not in src)

    # 6. enrichment validation sample
    total_sections = counts["sections"]
    enriched = conn.execute("SELECT COUNT(*) FROM enrich").fetchone()[0]
    check("enrichment-rows", enriched == total_sections,
          f"{enriched}/{total_sections}")
    bad = 0
    rows = conn.execute(
        "SELECT e.figures, s.text FROM enrich e"
        " JOIN sections s ON s.section_id = e.section_id"
        " ORDER BY e.section_id LIMIT 200").fetchall()
    for figs_json, text in rows:
        for fig in json.loads(figs_json or "[]"):
            if text[fig["start"]:fig["end"]] != fig["value"]:
                bad += 1
    check("figures-substring-valid", bad == 0,
          f"{bad} bad of 200-row sample")
    conn.close()
    return 0


def v2_checks():
    sys.path.insert(0, str(HERE))
    import retrieval_regression as rr
    mod = rr.load_server(str(ROOT))
    mod.REWRITE_ENABLED = False
    mod.RERANK_ENABLED = False
    assert mod._v2_open(), "v2 did not open"
    health = mod._v2_health()
    check("v2-health", health["effective"] == "v2"
          and health["fts5"] is True
          and health["generation"] == "g0000", str(health))

    match = mod._v2_match(["divisor"])
    t_secs = mod._v2_fts_sections(match, "in", 5)
    top_title = mod._V2["docs"][
        mod._V2["sections"][t_secs[0]]["doc_id"]]["title"] \
        if t_secs else ""
    check("channel-T-divisor", "3006" in top_title, top_title[:70])

    x_secs = mod._v2_exact_sections(
        "What does IHCPPM 3006.00.00 say about penalties?")
    x_ok = any("3006" in (mod._V2["sections"][s]["title"] or "")
               for s in x_secs)
    check("channel-X-section-number", x_ok, f"{len(x_secs)} sections")

    r_secs = mod._v2_registry_sections(
        "What is Indiana's Medicaid transfer penalty divisor?", "in")
    r_ok = bool(r_secs) and \
        mod._V2["sections"][r_secs[0]]["doc_id"] == 1282
    check("channel-R-divisor-doc", r_ok,
          f"doc={mod._V2['sections'][r_secs[0]]['doc_id'] if r_secs else None}")

    typo = mod._v2_typo_expansions(["devisor", "penalty", "medicaid"])
    check("typo-expansion-measured", True,
          f"'devisor' expansions={typo} "
          f"({'divisor found' if 'divisor' in typo else 'NOT expanded'})")

    hits = mod.hybrid_search(
        "Under Indiana Medicaid fee-for-service, how often are "
        "eyeglasses covered for adults?",
        corpus=mod.CORPUS_IN, n=8, use_rewrite=False, use_rerank=False)
    check("pins-eyeglasses",
          any("PROMOD00051" in (h["title"] or "") for h in hits[:3]),
          "; ".join((h["title"] or "")[:40] for h in hits[:3]))
    for q, want in (
            ("What is Indiana's Medicaid transfer penalty divisor?",
             "3006"),
            ("What is a qualified income trust and when does an "
             "Indiana Medicaid applicant need one?", "2615.75.15")):
        hits = mod.hybrid_search(q, corpus=mod.CORPUS_IN, n=8,
                                 use_rewrite=False, use_rerank=False)
        check(f"v2-anchor-{want}",
              any(want in (h["title"] or "") for h in hits[:3]),
              "; ".join((h["title"] or "")[:40] for h in hits[:3]))
    return 0


def legacy_phase():
    """v23 REPLACEMENT for the v22 'legacy-intact' check: the
    legacy engine and the RETRIEVAL flag are DELETED, so this
    phase (still launched with RETRIEVAL=legacy in the env)
    asserts the flag is dead: the engine opens v2 anyway and
    hybrid_search serves v2 hits. Listed in v23/STATUS.md."""
    sys.path.insert(0, str(HERE))
    import retrieval_regression as rr
    mod = rr.load_server(str(ROOT))
    mod.REWRITE_ENABLED = False
    mod.RERANK_ENABLED = False
    hits = mod.hybrid_search(
        "What is Indiana's Medicaid transfer penalty divisor?",
        corpus=mod.CORPUS_IN, n=8, use_rewrite=False, use_rerank=False)
    ok = mod._v2_health()["effective"] == "v2" and len(hits) > 0
    check("v23-flag-dead-engine-v2", ok,
          f"effective={mod._v2_health()['effective']}, hits={len(hits)}")
    return 0 if not FAILURES else 1


def main():
    if os.environ.get("FSSA_CHECK_PHASE") == "v2":
        v2_checks()
        return 0 if not FAILURES else 1
    if os.environ.get("FSSA_CHECK_PHASE") == "legacy":
        return legacy_phase()
    main_checks()
    for phase, ret in (("v2", "v2"), ("legacy", "legacy")):
        env = dict(os.environ)
        env["FSSA_CHECK_PHASE"] = phase
        env["RETRIEVAL"] = ret
        r = subprocess.run([sys.executable, str(Path(__file__))],
                           env=env, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        if r.returncode != 0:
            FAILURES.append(f"phase {phase} exit {r.returncode}")
    log.info("v22 offline checks: %s",
             "ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES")
    for f in FAILURES:
        log.info("  FAILED: %s", f)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
