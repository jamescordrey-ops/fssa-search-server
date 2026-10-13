"""Ingest-time tagging for new corpus documents (v12).

Usage: python3 tag_docs.py <in.json> <out.json>

Reads a corpus JSON (list of docs with title/url/text), stamps each
doc with a "tags" object {program, doc_type, year} computed by the
server's OWN classification functions (imported from
fssa_search_server.py), so ingest-time tags can never drift from the
search-time tags the ranking uses. Prints the tag distribution.

Run from the repo directory so the server module finds its corpus
files; its embeddings load is skipped gracefully when absent.
"""
import importlib.util
import json
import os
import sys
from collections import Counter

spec = importlib.util.spec_from_file_location(
    "fssa_search_server",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "fssa_search_server.py"))
srv = importlib.util.module_from_spec(spec)
_argv = sys.argv
sys.argv = ["fssa_search_server"]  # the server reads argv[1] as a port
try:
    spec.loader.exec_module(srv)
finally:
    sys.argv = _argv

src, dst = sys.argv[1], sys.argv[2]
docs = json.load(open(src, encoding="utf-8"))
counts = Counter()
for d in docs:
    hit = {"title": d.get("title", ""), "url": d.get("url", ""),
           "excerpt": (d.get("text") or "")[:2000]}
    program, doc_type = srv._classify_hit(hit)
    year = srv._hit_year(hit)
    d["tags"] = {"program": program, "doc_type": doc_type,
                 "year": year}
    counts[(program, doc_type)] += 1

json.dump(docs, open(dst, "w", encoding="utf-8"))
print(f"tagged {len(docs)} docs -> {dst}")
for (program, doc_type), n in counts.most_common():
    print(f"  {program:28s} {doc_type:22s} {n}")
