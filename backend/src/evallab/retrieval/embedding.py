"""Chunking y embedding deterministas y versionados (M9, 10.1).

`hash-embed@1.0.0` es un embedding léxico por feature hashing (unigramas y bigramas de tokens
normalizados, sin tildes ni stopwords básicas del español, SHA-256 → índice y signo, norma L2,
seis decimales). No es un modelo semántico: se elige porque es reproducible bit a bit en
cualquier máquina, no necesita red ni pesos externos y basta para un corpus sintético pequeño.
Cambiar cualquier parte (tokenización, stopwords, dimensión, redondeo) exige otra versión.

`paragraph@1.0.0` parte cada documento en párrafos (líneas en blanco) y numera los chunks
`<doc_id>#c<n>` desde 1.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

EMBEDDER = "hash-embed"
EMBEDDER_VERSION = "1.0.0"
DIMENSION = 256
DECIMALS = 6
CHUNKER = "paragraph"
CHUNKER_VERSION = "1.0.0"
STOPWORDS = frozenset(
    (
        "a",
        "al",
        "como",
        "con",
        "de",
        "del",
        "el",
        "en",
        "es",
        "la",
        "las",
        "lo",
        "los",
        "mas",
        "o",
        "para",
        "por",
        "que",
        "se",
        "su",
        "sus",
        "un",
        "una",
        "y",
    )
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [t for t in _TOKEN.findall(plain) if t not in STOPWORDS]


def features(text: str) -> list[str]:
    words = tokens(text)
    return words + [f"{a} {b}" for a, b in itertools.pairwise(words)]


def embed(text: str, dimension: int = DIMENSION) -> list[float]:
    vector = [0.0] * dimension
    for feature in features(text):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [round(v / norm, DECIMALS) for v in vector]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    position: int
    text: str

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def chunk_document(doc_id: str, text: str) -> list[Chunk]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [
        Chunk(chunk_id=f"{doc_id}#c{index}", doc_id=doc_id, position=index, text=paragraph)
        for index, paragraph in enumerate(paragraphs, start=1)
    ]


def vector_literal(vector: Sequence[float]) -> str:
    """Formato de entrada de pgvector: `[v1,v2,...]` con la precisión fijada."""
    return "[" + ",".join(f"{v:.{DECIMALS}f}" for v in vector) + "]"
