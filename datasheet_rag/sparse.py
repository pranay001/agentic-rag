"""BM25 sparse vectors computed client-side; Qdrant applies IDF server-side (Modifier.IDF).

The tokenizer keeps datasheet identifiers whole ('ltc3310hv-1#pbf', 'rds(on)', '3.3v') and also indexes
their parts, so both exact part numbers and their components match.
"""
import hashlib
import re
from collections import Counter

from qdrant_client import models

_TOKEN_RE = re.compile(r"[a-z0-9µω°±]+(?:[.\-#/()][a-z0-9µω°±]+\)?)*")
_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were with which".split())
BM25_K1, BM25_B, BM25_AVGDL = 1.2, 0.75, 120.0


def tokenize(text: str) -> list[str]:
    out = []
    for t in _TOKEN_RE.findall(text.lower()):
        if t in _STOP:
            continue
        out.append(t)
        parts = [x for x in re.split(r"[.\-#/()]", t) if x]
        if len(parts) > 1:
            out += [x for x in parts if x not in _STOP]
    return out


def _term_id(t: str) -> int:
    return int.from_bytes(hashlib.blake2b(t.encode(), digest_size=4).digest(), "little")


def sparse_doc(text: str) -> models.SparseVector:
    toks = tokenize(text)
    tf, dl = Counter(_term_id(t) for t in toks), max(len(toks), 1)
    norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / BM25_AVGDL)
    idx = list(tf)
    return models.SparseVector(indices=idx, values=[tf[i] * (BM25_K1 + 1) / (tf[i] + norm) for i in idx])


def sparse_query(text: str) -> models.SparseVector:
    ids = sorted({_term_id(t) for t in tokenize(text)})
    return models.SparseVector(indices=ids, values=[1.0] * len(ids))
