"""Convert embeddings.json to embeddings.npz (v12 binary format).

Usage: python make_npz.py [embeddings.json path]

Writes embeddings.npz next to the input: a float32 matrix `vecs`
(N x dim) plus `meta`, the UTF-8 JSON bytes of the per-chunk
{url, title, text} list in the same row order. The server prefers
the npz at load (seconds instead of minutes of JSON parsing) and
scores searches with one matmul over the matrix.

The input is parsed as a stream of chunk objects: a whole-file
json.load of the multi-GB embeddings.json raises MemoryError on a
16 GB machine (seen on the PC, 2026-10-13).
"""
import json
import os
import sys

import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else "embeddings.json"
dst = os.path.join(os.path.dirname(os.path.abspath(src)), "embeddings.npz")

dec = json.JSONDecoder()
vecs = []
meta = []

with open(src, encoding="utf-8") as f:
    buf = f.read(1 << 20)
    # Position at the first chunk object inside "chunks": [ { ...
    anchor = buf.index('"chunks"')
    i = buf.index("[", anchor) + 1
    while True:
        # Skip separators between chunk objects.
        while i < len(buf) and buf[i] in " \t\r\n,":
            i += 1
        if i >= len(buf):
            more = f.read(1 << 22)
            if not more:
                raise ValueError("EOF before chunks array closed")
            buf = buf[i:] + more
            i = 0
            continue
        if buf[i] == "]":
            break
        try:
            ch, end = dec.raw_decode(buf, i)
        except json.JSONDecodeError:
            more = f.read(1 << 22)
            if not more:
                raise
            buf = buf[i:] + more
            i = 0
            continue
        vecs.append(np.asarray(ch["vec"], dtype=np.float32))
        meta.append({"url": ch["url"], "title": ch["title"],
                     "text": ch["text"]})
        i = end
        if i > (1 << 21):  # keep the buffer bounded
            buf = buf[i:]
            i = 0
        if len(vecs) % 20000 == 0:
            print(f"  parsed {len(vecs)} chunks", flush=True)

print(f"parsed {len(vecs)} chunks from {src}")
mat = np.stack(vecs)
meta_bytes = json.dumps(meta).encode("utf-8")
np.savez_compressed(dst, vecs=mat,
                    meta=np.frombuffer(meta_bytes, dtype=np.uint8))
print(f"wrote {dst}: vecs {mat.shape} float32, "
      f"file {os.path.getsize(dst) / 1e6:.1f} MB")

with np.load(dst) as z:
    back_meta = json.loads(bytes(z["meta"]).decode("utf-8"))
    back_vecs = z["vecs"]
assert len(back_meta) == len(meta) and back_vecs.shape == mat.shape
for i in (0, len(meta) - 1):
    assert back_meta[i]["url"] == meta[i]["url"]
    assert np.allclose(back_vecs[i], mat[i])
print("round-trip check OK")
