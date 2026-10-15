# Staging dir — merge interface (human act)

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
