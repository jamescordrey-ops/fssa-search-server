# g0004 delta — targeted registry-carrier enrichment (WS-E, v23.1)

Contents: `enrichment_delta.jsonl` — 17 merged enrichment
rows (250 questions), one per registry-carrier section
targeted by the WS-E pass (the WS3.5 machinery: fleet-format
questions merged with each section's existing rows in the
established merged shape, questions capped at 16, figures
offset-verified; writer "fleet+code"). The rows were
produced and validated entirely on the VM — full record in
`../WSE_RECORD.md`:

- Fleet merge via `tools/enrich_sections.py --merge`
  (2 waves, 17 sections, 0 rejected rows), then the
  deterministic layer via `--deterministic`, run against an
  isolated stage copy of the generation store
  (`wse_stage/`); the shipping store was never touched.
- `tools/enrich_qa.py` over the staged store: **0 figure
  failures, 0 question failures, 0 alias failures** across
  all 6,095 rows (28,089 questions; fleet rows 1,407).
- Full-table diff vs the base store: exactly these 17
  enrich rows and their 17 `section_fts` rows differ;
  zero section texts differ.
- `tools/registry_vocab.py` against the staged store
  (`generations/g0004_staging/`): **figures 14/14,
  mechanisms 5/5, exit 0** (baseline on the same base:
  10/14 + 3/5). The staged audit output is preserved at
  `registry_vocab_g0004.json` in this directory.
- None of the 17 sections belongs to one of the 48
  g0002-repaired docs, so their live (g0003) section texts
  are expected byte-identical to the VM base texts the
  delta was validated against. The build below asserts
  this per section instead of assuming it.

## Deploy procedure (data-only generation update, PC side)

This mirrors how g0003 was actually built at its deploy
(the build_g0003.py pattern in the v23 deploy record:
reuse + embed-fresh), NOT a whole-layer rebuild.

1. Copy the live generation dir `generations/g0003` to
   `generations/g0004` in the PC server dir.
2. For each of the 17 delta rows: assert the section_id
   exists in g0004's store AND every figure in the delta
   row verifies as a substring of the LIVE section text
   at its stored offsets (the enrich_qa rule). On ANY
   failure: stop and report; do not ship a partial delta
   silently.
3. REPLACE the `enrich` rows for the 17 section_ids with
   the rows from `enrichment_delta.jsonl` (questions /
   aliases / figures / scope_tags JSON-encoded; writer
   "fleet+code").
4. Rebuild the `section_fts` synth_q/aliases columns for
   those 17 sections from the enrich table (the
   `enrich_sections.py --refresh-fts` pattern).
5. Vectors: chunk texts are unchanged by this delta, so
   chunk vectors carry over byte-for-byte from g0003.
   Synthq vectors: reuse the stored vector for every
   unchanged (section, question) pair; embed fresh, with
   the finalize embedder (nomic-embed-text via the PC's
   Ollama), every new or changed question in the 17
   sections. Do NOT run stock `tools/finalize_vectors.py`
   against g0004 for this: it rebuilds a generation's
   whole vector layer from the legacy embeddings file and
   can take vector-canonical paths; the recorded machinery
   for an enrichment-only delta is the reuse + embed-fresh
   build used for g0003.
6. Exactness asserts (must pass before any flip): chunk
   vector count == chunk count (77,354 on the live base);
   synthq vector rows == the store's total question count
   after the delta; row norms unit.
7. Canary BEFORE any flip: re-embed 5 unchanged questions
   and compare against their stored synthq vectors — min
   cosine must read **1.000000**, embedding_drift false
   (the g0003 build's gate; the server's own chunk canary
   must also read ok on open).
8. Re-run `tools/registry_vocab.py --gen generations/g0004`
   against the PC-built store: it must read **figures
   14/14 + mechanisms 5/5 (exit 0)** on the live base
   before the flip. The VM staging run reads this on the
   g0000 base; carrier neighbourhoods resolve against the
   store, so the live-base run is the confirming gate,
   not a formality.
9. Only on a clean canary AND a clean audit: flip
   `generations/CURRENT` to g0004 and restart by the
   standard procedure. On ANY snag: leave CURRENT at
   g0003 and ship v23.1 code without the generation
   change. The flip decision is the coordinator's; WS-E
   changed no CURRENT pointer anywhere.
