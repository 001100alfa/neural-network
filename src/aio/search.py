"""Ranked code search — BM25 lexical retrieval with optional semantic re-ranking.

The naive symbol index (:mod:`aio.codeindex`) only answers "where is X
*defined*". This adds real *retrieval*: ask a natural-language or keyword query
and get the most relevant chunks across the repo, ranked by Okapi BM25 — the
same scoring family used by Lucene/Elasticsearch — over identifier-aware tokens
(camelCase / snake_case / dotted names are split into subwords). Pure stdlib,
offline, deterministic.

When an embedding function is supplied (``embed_fn``), each chunk and the query
are embedded and cosine similarity is blended with the normalized BM25 score
into a hybrid ranking — genuine semantic search. The embedder is pluggable; a
network-backed one over an OpenAI-compatible ``/v1/embeddings`` endpoint is
provided by :func:`openai_embedder`. BM25 always works without it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

from .projectmap import IGNORE_DIRS, _gitignore_patterns, _ignored

MAX_FILES = 2000
MAX_FILE_BYTES = 400_000
CHUNK_LINES = 40
CHUNK_OVERLAP = 10

# Extensions worth indexing for code search (source + docs/config).
SEARCHABLE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".php", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".html", ".css", ".scss", ".vue", ".sql", ".md", ".rst",
    ".txt", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")

EmbedFn = Callable[[list[str]], list[list[float]]]


def tokenize(text: str) -> list[str]:
    """Identifier-aware tokenizer: lower-cases and splits camelCase/snake_case.

    ``parseHTTPRequest`` -> ``parse http request`` (plus the whole token), so a
    query for "parse request" matches code that never writes those words apart.
    """
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text):
        low = tok.lower()
        out.append(low)
        if "_" in tok or not tok.islower():
            for part in tok.split("_"):
                for sub in _CAMEL_RE.findall(part):
                    s = sub.lower()
                    if s and s != low:
                        out.append(s)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _structural_weight(path: str, text: str) -> float:
    """A zero-dependency relevance prior: favour implementation over tests/docs.

    Pure BM25 over-ranks test and doc files that merely *repeat* query words.
    This nudges results toward where behaviour actually lives, without any
    embeddings: down-weight tests/docs/fixtures, up-weight chunks that define
    a symbol (def/class/func/...). Applied as a multiplier on the lexical score.
    """
    w = 1.0
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".spec.js")) \
            or "/tests/" in low or "/test/" in low or low.endswith((".md", ".rst", ".txt")):
        w *= 0.55                                   # tests/docs: relevant but secondary
    if "/examples/" in low or "/fixtures/" in low or "/vendor/" in low:
        w *= 0.7
    # chunks that DEFINE something are usually what "how does X work" wants
    import re as _re
    if _re.search(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|func|function|fn|interface|type|struct)\b",
                  text, _re.M):
        w *= 1.3
    return w


class _Chunk:
    __slots__ = ("path", "start", "end", "text", "tokens", "embedding", "weight")

    def __init__(self, path: str, start: int, end: int, text: str) -> None:
        self.path = path
        self.start = start          # 1-based first line
        self.end = end              # 1-based last line
        self.text = text
        self.tokens = tokenize(text)
        self.embedding: list[float] | None = None
        self.weight = _structural_weight(path, text)


class CodeSearcher:
    """Builds a BM25 index over chunked repo files; optionally semantic.

    ``embed_fn(list_of_texts) -> list_of_vectors`` enables hybrid ranking.
    """

    def __init__(self, workdir: Path, embed_fn: EmbedFn | None = None,
                 k1: float = 1.5, b: float = 0.75) -> None:
        self.workdir = Path(workdir)
        self.embed_fn = embed_fn
        self.k1 = k1
        self.b = b
        self.chunks: list[_Chunk] = []
        self.df: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.avgdl = 0.0
        self.n_files = 0

    # -- build ------------------------------------------------------------
    def build(self) -> "CodeSearcher":
        patterns = _gitignore_patterns(self.workdir)
        for f in self._iter_files(patterns):
            try:
                text = f.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", "replace")
            except OSError:
                continue
            rel = str(f.relative_to(self.workdir))
            self._add_file(rel, text)
            self.n_files += 1
        self._finalize()
        if self.embed_fn is not None and self.chunks:
            self._embed_chunks()
        return self

    def _iter_files(self, patterns):
        count = 0
        for f in sorted(self.workdir.rglob("*")):
            if count >= MAX_FILES:
                break
            if not f.is_file() or f.suffix.lower() not in SEARCHABLE_EXTS:
                continue
            rel_parts = f.relative_to(self.workdir).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            rel = str(f.relative_to(self.workdir))
            if _ignored(rel, f.name, patterns):
                continue
            count += 1
            yield f

    def _add_file(self, rel: str, text: str) -> None:
        lines = text.splitlines()
        if not lines:
            return
        step = max(1, CHUNK_LINES - CHUNK_OVERLAP)
        for start in range(0, len(lines), step):
            window = lines[start:start + CHUNK_LINES]
            if not any(ln.strip() for ln in window):
                continue
            chunk = _Chunk(rel, start + 1, start + len(window), "\n".join(window))
            if not chunk.tokens:
                continue
            self.chunks.append(chunk)
            for term in set(chunk.tokens):
                self.df[term] = self.df.get(term, 0) + 1
            if start + CHUNK_LINES >= len(lines):
                break

    def _finalize(self) -> None:
        n = len(self.chunks)
        if not n:
            return
        self.avgdl = sum(len(c.tokens) for c in self.chunks) / n
        for term, df in self.df.items():
            # BM25 idf with +1 so common terms never go negative
            self.idf[term] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _embed_chunks(self) -> None:  # pragma: no cover - exercised via fake in tests
        assert self.embed_fn is not None
        vectors = self.embed_fn([c.text for c in self.chunks])
        for chunk, vec in zip(self.chunks, vectors):
            chunk.embedding = vec

    # -- query ------------------------------------------------------------
    def _bm25(self, query_terms: list[str], chunk: _Chunk) -> float:
        if not chunk.tokens:
            return 0.0
        freqs: dict[str, int] = {}
        for t in chunk.tokens:
            freqs[t] = freqs.get(t, 0) + 1
        dl = len(chunk.tokens)
        score = 0.0
        for term in query_terms:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            idf = self.idf.get(term, 0.0)
            denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            score += idf * (tf * (self.k1 + 1)) / denom
        return score

    def search(self, query: str, k: int = 8, semantic: bool | None = None) -> list[dict]:
        """Return up to ``k`` ranked chunks for ``query``.

        ``semantic`` defaults to True when an embedder is available. When on,
        the final score blends normalized BM25 with cosine similarity.
        """
        if not self.chunks:
            return []
        query_terms = tokenize(query)
        bm = [self._bm25(query_terms, c) for c in self.chunks]
        use_semantic = (semantic is None and self.embed_fn is not None) or bool(semantic)
        use_semantic = use_semantic and any(c.embedding is not None for c in self.chunks)

        if use_semantic and self.embed_fn is not None:
            qvec = self.embed_fn([query])[0]
            sims = [
                _cosine(qvec, c.embedding) if c.embedding is not None else 0.0
                for c in self.chunks
            ]
            base = [0.6 * s + 0.4 * b for s, b in zip(_norm(sims), _norm(bm))]
        else:
            base = bm
        # apply the structural relevance prior (implementation > tests/docs)
        scores = [v * c.weight for v, c in zip(base, self.chunks)]

        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        out: list[dict] = []
        for i in ranked:
            if scores[i] <= 0.0:
                continue
            c = self.chunks[i]
            out.append({
                "path": c.path,
                "start_line": c.start,
                "end_line": c.end,
                "score": round(float(scores[i]), 4),
                "snippet": c.text,
            })
            if len(out) >= k:
                break
        return out


def _norm(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]; all-equal -> all zeros."""
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.0 for _ in values]
    span = hi - lo
    return [(v - lo) / span for v in values]


def openai_embedder(api_key: str, base_url: str = "https://api.openai.com/v1",
                    model: str = "text-embedding-3-small",
                    batch: int = 64) -> EmbedFn:  # pragma: no cover - network
    """Build an :data:`EmbedFn` over an OpenAI-compatible ``/v1/embeddings`` API."""
    import json
    import urllib.request

    url = base_url.rstrip("/") + "/embeddings"

    def embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            payload = {"model": model, "input": texts[i:i + batch]}
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vectors.extend(item["embedding"] for item in data["data"])
        return vectors

    return embed
