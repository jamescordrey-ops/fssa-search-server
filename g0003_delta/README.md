# g0003 delta — fleet enrichment for the 48 repaired sections

Contents: `enrichment_delta.jsonl` — 48 merged enrichment
rows (713 questions), one per section created by the
g0002 repair of the 48 previously-binary documents
(g0002_delta/manifest.json, status "repaired"). Until now
those sections carried the deterministic layer only
(writer "template+code"). These rows were written by a
Muse writer fleet in the established batch format
(10-12 specific, content-grounded questions per section,
aliases, verbatim figures, scope tags; writer "fleet+code")
and merged with each section's existing template
questions/aliases/figures in the established merged shape
(questions capped at 16, figures offset-verified).

Validation: the deterministic QA (the enrich_qa.py rules:
every figure's text[start:end] equals its value in the
section text; questions 12-220 chars, no URLs, no
duplicates; fleet-row question band 3-16; aliases <= 80
chars) reads ZERO failures across all 48 rows.

## Deploy procedure (data-only generation update)

1. Copy the live generation dir `generations/g0002` to
   `generations/g0003` in the PC server dir.
2. In g0003's corpus DB, REPLACE the `enrich` rows for the
   48 section_ids with the rows from
   `enrichment_delta.jsonl` (questions/aliases/figures/
   scope_tags JSON-encoded; writer "fleet+code").
3. Rebuild the FTS synth_q/aliases columns for those
   sections (section_fts is fed from enrich at finalize).
4. Run the stock `tools/finalize_vectors.py` (v23 build:
   the duplicate-text assignment fix is folded in) against
   g0003: chunk vectors are reused where texts are
   unchanged; synthq vectors are embedded fresh for the
   changed questions. The exactness assert (every chunk
   assigned a vector) must pass.
5. Embedding canary BEFORE any flip: cosine of re-embedded
   unchanged text against the stored vectors must read
   1.000000 / drift false, as in the g0001/g0002 deploys.
6. Only on a clean canary: flip `generations/CURRENT` to
   g0003 and restart. On ANY snag: leave CURRENT at g0002
   and ship the code without the generation change.
