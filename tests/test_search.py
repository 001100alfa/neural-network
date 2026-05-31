"""Tests for ranked code search (BM25 + optional semantic re-ranking)."""

from __future__ import annotations

from aio.search import CodeSearcher, _cosine, _norm, tokenize
from aio.tools import ToolContext, default_registry


# -- tokenizer --------------------------------------------------------------

def test_tokenize_splits_camel_and_snake_case():
    toks = tokenize("parseHTTPRequest_v2")
    assert "parsehttprequest_v2" in toks   # whole identifier kept
    assert "parse" in toks and "http" in toks and "request" in toks and "v2" in toks


def test_tokenize_lowercases_plain_words():
    assert tokenize("Retry Backoff") == ["retry", "backoff"]


# -- helpers ----------------------------------------------------------------

def test_cosine_and_norm():
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([0, 0], [1, 1]) == 0.0
    assert _norm([2, 4, 6]) == [0.0, 0.5, 1.0]
    assert _norm([5, 5, 5]) == [0.0, 0.0, 0.0]   # all-equal -> zeros


# -- BM25 lexical ranking ---------------------------------------------------

def _repo(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n"
        "    # verify the password hash and issue a session token\n"
        "    return check_password(user, password)\n"
    )
    (tmp_path / "retry.py").write_text(
        "def with_backoff(fn):\n"
        "    # retry with exponential backoff on rate limit errors\n"
        "    for attempt in range(5):\n"
        "        sleep(2 ** attempt)\n"
    )
    (tmp_path / "notes.md").write_text("# Project\nMiscellaneous documentation.\n")
    return tmp_path


def test_bm25_ranks_relevant_file_first(tmp_path):
    s = CodeSearcher(_repo(tmp_path)).build()
    assert s.n_files == 3
    hits = s.search("exponential backoff rate limit", k=3)
    assert hits, "expected at least one hit"
    assert hits[0]["path"] == "retry.py"
    assert hits[0]["score"] > 0

    hits2 = s.search("verify password session token", k=3)
    assert hits2[0]["path"] == "auth.py"


def test_search_returns_empty_for_no_match(tmp_path):
    s = CodeSearcher(_repo(tmp_path)).build()
    assert s.search("zzzznonexistentterm", k=5) == []


def test_ignores_non_searchable_and_ignored_dirs(tmp_path):
    (tmp_path / "keep.py").write_text("def keeper(): pass\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG binary")
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "vendor.js").write_text("function vendored(){ return keeper; }\n")
    (tmp_path / ".gitignore").write_text("secret.py\n")
    (tmp_path / "secret.py").write_text("def keeper(): return 'hidden'\n")

    s = CodeSearcher(tmp_path).build()
    paths = {c.path for c in s.chunks}
    assert "keep.py" in paths
    assert "image.png" not in paths                    # binary ext skipped
    assert not any("node_modules" in p for p in paths)  # ignored dir
    assert "secret.py" not in paths                    # gitignored


# -- semantic / hybrid ------------------------------------------------------

def _fake_embedder(texts):
    """Deterministic 2-d embedder: axis-0 = 'canine-ish', axis-1 = otherwise.

    Lets a test force a chunk with ZERO lexical overlap to win on meaning.
    """
    vecs = []
    for t in texts:
        low = t.lower()
        canine = any(w in low for w in ("dog", "canine", "puppy", "animal"))
        vecs.append([1.0, 0.0] if canine else [0.0, 1.0])
    return vecs


def test_semantic_surfaces_chunk_with_no_lexical_overlap(tmp_path):
    (tmp_path / "a.txt").write_text("the quick brown fox jumps\n")
    (tmp_path / "b.txt").write_text("a lazy dog sleeps all day\n")

    s = CodeSearcher(tmp_path, embed_fn=_fake_embedder).build()
    assert all(c.embedding is not None for c in s.chunks)

    # "canine animal" shares no words with either file -> BM25 is 0 for both,
    # but the embedder knows b.txt is canine-related, so it must rank first.
    hits = s.search("canine animal", k=2)
    assert hits and hits[0]["path"] == "b.txt"


def test_semantic_flag_ignored_without_embedder(tmp_path):
    s = CodeSearcher(_repo(tmp_path)).build()
    # semantic requested but no embeddings available -> graceful BM25 fallback
    hits = s.search("exponential backoff", k=2, semantic=True)
    assert hits[0]["path"] == "retry.py"


# -- search_code tool -------------------------------------------------------

def test_search_code_tool_ranks_and_caches(tmp_path):
    _repo(tmp_path)
    reg = default_registry()
    tool = reg.get("search_code")
    assert tool is not None and tool.name in {t.name for t in reg}
    ctx = ToolContext(workdir=tmp_path)
    out = tool.run({"query": "retry exponential backoff", "limit": 2}, ctx)
    assert "retry.py" in out and "score" in out
    assert ctx.code_searcher is not None             # cached on the context
    # a second call reuses the cached index (no rebuild requested)
    cached = ctx.code_searcher
    tool.run({"query": "login password"}, ctx)
    assert ctx.code_searcher is cached


# -- structural relevance prior (implementation > tests/docs) ---------------

def test_structural_weight_downweights_tests_and_docs():
    from aio.search import _structural_weight
    impl = "def with_backoff(fn):\n    retry on rate limit\n"
    assert _structural_weight("src/retry.py", impl) > _structural_weight("tests/test_retry.py", impl)
    assert _structural_weight("src/retry.py", impl) > _structural_weight("docs/guide.md", impl)


def test_structural_weight_boosts_definitions():
    from aio.search import _structural_weight
    with_def = "def parse(x):\n    return x\n"
    no_def = "parse the request and handle errors here\n"
    assert _structural_weight("a.py", with_def) > _structural_weight("a.py", no_def)


def test_search_prefers_implementation_over_test_file(tmp_path):
    # both files mention the query words; the implementation must rank first
    (tmp_path / "retry.py").write_text(
        "def with_backoff(fn):\n"
        "    # retry with exponential backoff on rate limit errors\n"
        "    for i in range(5):\n        sleep(2 ** i)\n")
    (tmp_path / "test_retry.py").write_text(
        "def test_with_backoff():\n"
        "    # retry with exponential backoff on rate limit errors works\n"
        "    assert with_backoff(lambda: 1) == 1\n"
        "    # retry retry backoff backoff rate limit rate limit\n")
    s = CodeSearcher(tmp_path).build()
    hits = s.search("retry exponential backoff rate limit", k=2)
    assert hits[0]["path"] == "retry.py"          # implementation wins despite test repeating words


# -- per-line tokenization optimization (must not change results) -----------

def test_per_line_tokens_match_whole_text():
    # the index reuses per-line token lists across overlapping windows; the
    # result must be identical to tokenizing the joined window text.
    lines = ["def parseHTTPRequest(self, x):",
             "    return self.compute_total(x) + y",
             "",
             "class Foo: pass"]
    whole = tokenize("\n".join(lines))
    per_line = []
    for ln in lines:
        per_line.extend(tokenize(ln))
    assert whole == per_line


def test_chunk_accepts_precomputed_tokens():
    from aio.search import _Chunk
    toks = ["a", "b"]
    c = _Chunk("f.py", 1, 1, "a b", tokens=toks)
    assert c.tokens is toks                       # reused, not recomputed
    c2 = _Chunk("f.py", 1, 1, "a b")
    assert c2.tokens == ["a", "b"]                # still tokenizes when not given
