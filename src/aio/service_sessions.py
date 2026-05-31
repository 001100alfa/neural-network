"""Session persistence + live-conversation durability for AgentService."""

from __future__ import annotations

import json
import os
from typing import Any

from ._msgio import msg_from_dict as _msg_from_dict, msg_to_dict as _msg_to_dict

from .service_base import ServiceBase


class SessionsMixin(ServiceBase):
    # -- sessions: save / load conversation history ----------------------
    @staticmethod
    def _sessions_dir():
        from pathlib import Path

        env = os.environ.get("AIO_SESSIONS_DIR")
        d = Path(env) if env else (Path.home() / ".config" / "aio" / "sessions")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _session_store(self):
        """Lazily open the SQLite store, importing any legacy JSON once."""
        if self._store is None:
            from .store import SessionStore

            sdir = self._sessions_dir()
            self._store = SessionStore(sdir / "sessions.db")
            self._store.migrate_legacy(sdir)
        return self._store

    def _restore_conversations(self) -> None:
        """Reload auto-persisted conversations so tabs survive a restart/crash."""
        try:
            saved = self._session_store().load_conversations()
        except Exception:  # pragma: no cover - never block startup on the store
            return
        for conv_id, payload in saved.items():
            msgs = [_msg_from_dict(m) for m in payload.get("messages", [])]
            if msgs:
                self.conversations[conv_id] = msgs
                self._summaries[conv_id] = payload.get("summary", "")

    def _persist_conversation(self, conv_id: str) -> None:
        """Snapshot a conversation's working state to the store after a turn."""
        import time as _t

        conv_id = conv_id or "default"
        try:
            msgs = self.conversations.get(conv_id, [])
            self._session_store().save_conversation(conv_id, {
                "messages": [_msg_to_dict(m) for m in msgs],
                "summary": self._summaries.get(conv_id, ""),
                "provider": self.config.provider,
                "model": self.config.active.model,
                "updated": _t.time(),
            })
        except Exception:  # pragma: no cover - persistence must never break a turn
            pass

    def save_session(self, title: str | None = None, session_id: str | None = None,
                     conv_id: str = "default") -> dict[str, Any]:
        import time

        with self._lock:
            self._select_conv(conv_id)
            msgs = self.agent.messages
            if not title:
                first = next((m.content for m in msgs if m.role == "user" and m.content), "")
                title = (first[:48] + "…") if len(first) > 48 else (first or "session")
            sid = session_id or f"{int(time.time() * 1000):x}"
            self._session_store().save({
                "id": sid,
                "title": title,
                "provider": self.config.provider,
                "model": self.config.active.model,
                "updated": time.time(),
                "messages": [_msg_to_dict(m) for m in msgs],
            })
            return {"ok": True, "id": sid, "title": title, "count": len(msgs)}

    def list_sessions(self) -> dict[str, Any]:
        return {"sessions": self._session_store().list_sessions()}

    def search_sessions(self, query: str) -> dict[str, Any]:
        """Full-text search across saved sessions (title + message content)."""
        return {"results": self._session_store().search(query)}

    def load_session(self, session_id: str, conv_id: str = "default") -> dict[str, Any]:
        with self._lock:
            d = self._session_store().get(session_id)
            if d is None:
                return {"ok": False, "error": "session not found"}
            conv_id = conv_id or "default"
            self.conversations[conv_id] = [_msg_from_dict(m) for m in d.get("messages", [])]
            self._select_conv(conv_id)
            return {"ok": True, "id": session_id, "title": d.get("title", ""),
                    "messages": d.get("messages", [])}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        self._session_store().delete(session_id)
        return {"ok": True}

    # -- export / import a conversation ----------------------------------
    def export_session(self, conv_id: str = "default", fmt: str = "md") -> dict[str, Any]:
        msgs = self.conversations.get(conv_id or "default", [])
        if fmt == "json":
            content = json.dumps(
                {
                    "provider": self.config.provider,
                    "model": self.config.active.model,
                    "messages": [_msg_to_dict(m) for m in msgs],
                },
                indent=2,
            )
            return {"filename": "conversation.json", "mime": "application/json", "content": content}
        # markdown
        lines = [f"# AIO conversation ({self.config.provider} / {self.config.active.model})", ""]
        for m in msgs:
            if m.role == "user":
                lines += ["## 🧑 User", "", m.content or "", ""]
                if m.images:
                    lines += [f"_({len(m.images)} image attachment(s))_", ""]
            elif m.role == "assistant":
                lines += ["## 🤖 Assistant", ""]
                if m.content:
                    lines += [m.content, ""]
                for tc in m.tool_calls:
                    lines += [f"- 🔧 `{tc.name}` `{json.dumps(tc.arguments)}`"]
                if m.tool_calls:
                    lines += [""]
            elif m.role == "tool":
                body = (m.content or "")[:1000]
                lines += ["> **tool result:**", "", "```", body, "```", ""]
        return {"filename": "conversation.md", "mime": "text/markdown", "content": "\n".join(lines)}

    def import_session(self, data: Any, conv_id: str | None = None) -> dict[str, Any]:
        import time

        if isinstance(data, dict):
            raw = data.get("messages", [])
            title = data.get("title")
        elif isinstance(data, list):
            raw, title = data, None
        else:
            return {"ok": False, "error": "import expects JSON with a 'messages' list"}
        with self._lock:
            cid = conv_id or f"imp{int(time.time() * 1000):x}"
            self.conversations[cid] = [_msg_from_dict(m) for m in raw]
            self._select_conv(cid)
            return {
                "ok": True, "conv": cid, "title": title or "imported",
                "messages": [_msg_to_dict(m) for m in self.conversations[cid]],
            }

