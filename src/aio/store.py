"""SQLite-backed session store.

Sessions used to be one JSON file each, re-read and re-parsed in full on every
list/search — O(n) scans, no indexing, no transactions, and racy under the
threaded web server. This replaces that with a real database using the stdlib
``sqlite3`` (still zero third-party deps):

* an indexed ``sessions`` table (``updated DESC`` for instant listing),
* full-text search via FTS5 when the bundled SQLite supports it, with a
  graceful ``LIKE`` fallback when it doesn't,
* atomic upserts and a single shared connection guarded by a lock so concurrent
  dashboard requests can't corrupt state,
* a one-time migration that imports any legacy ``*.json`` sessions so upgrading
  users keep their history.

Return shapes match the previous file-based implementation exactly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


# Versioned schema migrations, applied in order via PRAGMA user_version. Add
# new (version, [statements]) tuples here; never edit a shipped one.
_MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        """CREATE TABLE IF NOT EXISTS sessions(
               id TEXT PRIMARY KEY, title TEXT, provider TEXT, model TEXT,
               created REAL, updated REAL, count INTEGER, data TEXT, body TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated DESC)",
    ]),
    (2, [
        """CREATE TABLE IF NOT EXISTS conversations(
               conv_id TEXT PRIMARY KEY, data TEXT, updated REAL)""",
    ]),
]
SCHEMA_VERSION = _MIGRATIONS[-1][0]


class SessionStore:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._fts = self._init_schema()

    # -- schema -----------------------------------------------------------
    def _init_schema(self) -> bool:
        c = self._conn
        # WAL lets readers run concurrently with the single writer; NORMAL sync
        # is the standard durable-enough setting for a local app DB.
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:  # pragma: no cover - exotic FS
            pass
        self._apply_migrations()
        fts = False
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts "
                "USING fts5(id UNINDEXED, title, body)"
            )
            fts = True
        except sqlite3.OperationalError:  # FTS5 not compiled in -> LIKE fallback
            fts = False
        c.commit()
        return fts

    def _apply_migrations(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for version, statements in _MIGRATIONS:
            if version > current:
                for sql in statements:
                    self._conn.execute(sql)
                self._conn.execute(f"PRAGMA user_version={int(version)}")
        self._conn.commit()

    @property
    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    @property
    def fts_enabled(self) -> bool:
        return self._fts

    # -- writes -----------------------------------------------------------
    def save(self, payload: dict[str, Any]) -> str:
        sid = payload["id"]
        title = payload.get("title", "")
        msgs = payload.get("messages", [])
        body = "\n".join((m.get("content") or "") for m in msgs)
        with self._lock:
            row = self._conn.execute(
                "SELECT created FROM sessions WHERE id=?", (sid,)
            ).fetchone()
            created = row["created"] if row else payload.get("updated")
            self._conn.execute(
                """INSERT INTO sessions(id,title,provider,model,created,updated,count,data,body)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, provider=excluded.provider, model=excluded.model,
                     updated=excluded.updated, count=excluded.count, data=excluded.data,
                     body=excluded.body""",
                (sid, title, payload.get("provider", ""), payload.get("model", ""),
                 created, payload.get("updated"), len(msgs), json.dumps(payload), body),
            )
            if self._fts:
                self._conn.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
                self._conn.execute(
                    "INSERT INTO sessions_fts(id,title,body) VALUES(?,?,?)", (sid, title, body)
                )
            self._conn.commit()
        return sid

    def delete(self, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            if self._fts:
                self._conn.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
            self._conn.commit()
            return cur.rowcount > 0

    # -- reads ------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,title,provider,model,updated,count FROM sessions "
                "ORDER BY updated DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def search(self, query: str) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        ql = q.lower()
        with self._lock:
            rows = self._search_rows(q)
        results = []
        for r in rows:
            body = r["body"] or ""
            low = body.lower()
            matches = low.count(ql)
            snippet = ""
            i = low.find(ql)
            if i >= 0:
                snippet = ("…" if i > 30 else "") + body[max(0, i - 30):i + 60]
            results.append({
                "id": r["id"], "title": r["title"], "count": r["count"],
                "matches": matches, "snippet": snippet,
            })
        results.sort(key=lambda x: x["matches"], reverse=True)
        return results

    def _search_rows(self, q: str):
        if self._fts:
            try:
                phrase = '"' + q.replace('"', '""') + '"'   # treat as a literal phrase
                return self._conn.execute(
                    "SELECT s.id,s.title,s.count,s.body FROM sessions_fts f "
                    "JOIN sessions s ON s.id=f.id WHERE sessions_fts MATCH ? ORDER BY rank",
                    (phrase,),
                ).fetchall()
            except sqlite3.OperationalError:  # pragma: no cover - malformed match
                pass
        like = f"%{q}%"
        return self._conn.execute(
            "SELECT id,title,count,body FROM sessions "
            "WHERE lower(title) LIKE lower(?) OR lower(body) LIKE lower(?)",
            (like, like),
        ).fetchall()

    # -- live conversations (auto-persisted working state) ----------------
    def save_conversation(self, conv_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations(conv_id,data,updated) VALUES(?,?,?) "
                "ON CONFLICT(conv_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
                (conv_id, json.dumps(payload), payload.get("updated")),
            )
            self._conn.commit()

    def load_conversations(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT conv_id, data FROM conversations ORDER BY updated"
            ).fetchall()
        return {r["conv_id"]: json.loads(r["data"]) for r in rows}

    def delete_conversation(self, conv_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM conversations WHERE conv_id=?", (conv_id,))
            self._conn.commit()

    # -- migration --------------------------------------------------------
    def migrate_legacy(self, sessions_dir: str | Path) -> int:
        """Import legacy ``*.json`` sessions once (only when the DB is empty)."""
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        if n:
            return 0
        imported = 0
        for f in sorted(Path(sessions_dir).glob("*.json")):
            try:
                payload = json.loads(f.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if "id" not in payload:
                payload["id"] = f.stem
            payload.setdefault("title", f.stem)
            payload.setdefault("updated", f.stat().st_mtime)
            self.save(payload)
            imported += 1
        return imported

    def close(self) -> None:  # pragma: no cover - lifecycle
        with self._lock:
            self._conn.close()
