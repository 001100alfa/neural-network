"""Tests for the SQLite-backed SessionStore."""

from __future__ import annotations

import json
import threading

from aio.store import SCHEMA_VERSION, SessionStore


def _payload(sid, title, msgs, updated, provider="anthropic", model="m"):
    return {"id": sid, "title": title, "provider": provider, "model": model,
            "updated": updated, "messages": [{"role": "user", "content": c} for c in msgs]}


def test_save_list_get_delete(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    s.save(_payload("a", "First", ["hello world"], updated=100))
    s.save(_payload("b", "Second", ["the quick brown fox"], updated=200))

    listed = s.list()
    assert [r["id"] for r in listed] == ["b", "a"]          # ordered by updated DESC
    assert listed[0]["title"] == "Second" and listed[0]["count"] == 1
    assert set(listed[0]) == {"id", "title", "provider", "model", "updated", "count"}

    got = s.get("b")
    assert got["title"] == "Second" and got["messages"][0]["content"] == "the quick brown fox"
    assert s.get("missing") is None

    assert s.delete("a") is True
    assert s.delete("a") is False                           # already gone
    assert [r["id"] for r in s.list()] == ["b"]


def test_upsert_preserves_created_and_replaces_body(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    s.save(_payload("x", "v1", ["original text"], updated=100))
    created1 = s._conn.execute("SELECT created FROM sessions WHERE id='x'").fetchone()["created"]
    s.save(_payload("x", "v2", ["replaced text"], updated=500))
    row = s._conn.execute("SELECT created, updated, title FROM sessions WHERE id='x'").fetchone()
    assert row["created"] == created1          # created is preserved across updates
    assert row["updated"] == 500 and row["title"] == "v2"
    assert s.get("x")["messages"][0]["content"] == "replaced text"
    # old content no longer searchable; new content is
    assert s.search("original") == []
    assert len(s.search("replaced")) == 1


def test_search_fts(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    assert s.fts_enabled  # this environment has FTS5
    s.save(_payload("s1", "Animals chat", ["the quick brown fox", "jumps over the lazy dog"], 100))
    s.save(_payload("s2", "Greeting", ["hello world"], 200))

    res = s.search("brown")
    assert len(res) == 1 and res[0]["id"] == "s1" and "brown" in res[0]["snippet"]
    assert res[0]["matches"] >= 1
    # title-only match is found too
    assert any(r["title"] == "Greeting" for r in s.search("greeting"))
    # blank query returns nothing
    assert s.search("") == []
    assert s.search("   ") == []


def test_search_like_fallback(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    s._fts = False  # force the no-FTS5 path
    s.save(_payload("s1", "Animals", ["the quick brown fox"], 100))
    s.save(_payload("s2", "Greeting", ["hello world"], 200))
    res = s.search("BROWN")  # case-insensitive
    assert len(res) == 1 and res[0]["id"] == "s1"
    assert any(r["id"] == "s2" for r in s.search("greeting"))


def test_search_special_characters_dont_crash(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    s.save(_payload("s1", "Code", ['def f(x): return x["a"] or y'], 100))
    # quotes / parens would break a naive FTS MATCH; must be handled safely
    for q in ['x["a"]', "f(x)", '"quoted"', "a OR b"]:
        s.search(q)  # no exception


def test_migrate_legacy_json(tmp_path):
    # lay down legacy *.json session files
    (tmp_path / "old1.json").write_text(json.dumps(
        {"id": "old1", "title": "Legacy one", "updated": 10,
         "messages": [{"role": "user", "content": "migrate me"}]}))
    (tmp_path / "old2.json").write_text(json.dumps(
        {"title": "No id", "messages": [{"role": "user", "content": "second"}]}))
    (tmp_path / "broken.json").write_text("{not valid json")

    s = SessionStore(tmp_path / "sessions.db")
    n = s.migrate_legacy(tmp_path)
    assert n == 2                                  # broken file skipped
    ids = {r["id"] for r in s.list()}
    assert "old1" in ids and "old2" in ids          # missing id derived from filename
    assert s.get("old1")["messages"][0]["content"] == "migrate me"
    # running again is a no-op (DB already populated)
    assert s.migrate_legacy(tmp_path) == 0


def test_schema_version_and_wal_mode(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    assert s.schema_version == SCHEMA_VERSION >= 2
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "s.db"
    s1 = SessionStore(db)
    s1.save(_payload("a", "T", ["x"], updated=1))
    v1 = s1.schema_version
    s1.close()
    # reopening an existing DB must not re-run migrations or lose data
    s2 = SessionStore(db)
    assert s2.schema_version == v1
    assert s2.get("a")["title"] == "T"


def test_conversation_persist_roundtrip(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    assert s.load_conversations() == {}
    s.save_conversation("work", {"messages": [{"role": "user", "content": "hi"}],
                                 "summary": "sm", "updated": 5})
    s.save_conversation("default", {"messages": [], "summary": "", "updated": 1})
    s.save_conversation("work", {"messages": [{"role": "user", "content": "hi again"}],
                                 "summary": "", "updated": 9})   # upsert
    convs = s.load_conversations()
    assert set(convs) == {"work", "default"}
    assert convs["work"]["messages"][0]["content"] == "hi again"   # latest wins
    s.delete_conversation("work")
    assert set(s.load_conversations()) == {"default"}


def test_concurrent_saves_are_safe(tmp_path):
    s = SessionStore(tmp_path / "s.db")

    def worker(n):
        for i in range(20):
            s.save(_payload(f"{n}-{i}", f"t{n}-{i}", [f"body {n} {i}"], updated=n * 100 + i))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.list()) == 100                     # 5 workers x 20, no lost/corrupt rows
