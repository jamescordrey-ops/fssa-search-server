# Offline test suite

102 tests, all offline: no network, no API keys, no spend. They cover
the verifier and citation/figure gates (`test_verifier.py`), the ask
pipeline (`test_pipeline.py`), the spend hard stop
(`test_hard_stop.py`), the job-submission path (`test_jobpath.py`),
and the replay harness (`test_replay.py`).

Run from the repository root:

```sh
python -m pytest tests/
# or
python -m unittest discover -s tests
```

## Layout note

Each test module loads the server from a path constant at the top of
the file:

```python
SERVER = os.path.join(HERE, "..", "fssa_search_server.py")
```

The suite was written when the server lived one directory above the
tests. In this repository layout the server is at
`server/fssa_search_server.py`, so point the constant at
`../server/fssa_search_server.py` (one line per module) before
running, or copy `server/fssa_search_server.py` next to the corpus
files in a flat working directory and run the suite there.

`golden_set.jsonl` is the golden question set: questions with
known-good answers drawn from the corpus, used to grade models in
drafting and judging seats before they are trusted.
