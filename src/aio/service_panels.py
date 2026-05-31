"""Dashboard side panels: shell exec, git, static web server, filesystem."""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .service_base import ServiceBase
from .tools import ToolError


class PanelsMixin(ServiceBase):
    def exec_command(self, command: str, shell: str = "bash", timeout: int = 120) -> dict[str, Any]:
        """Run a command directly in the working directory (not via the model)."""

        command = (command or "").strip()
        if not command:
            return {"output": "", "exit_code": 0}
        exe = shutil.which(shell)
        try:
            if exe:
                proc = subprocess.run(
                    [exe, "-c", command], cwd=str(self.config.workdir),
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                proc = subprocess.run(
                    command, shell=True, cwd=str(self.config.workdir),
                    capture_output=True, text=True, timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            return {"output": f"command timed out after {timeout}s", "exit_code": 124}
        out = (proc.stdout or "") + (proc.stderr or "")
        return {"output": out, "exit_code": proc.returncode}

    # -- Git --------------------------------------------------------------
    def _run_git(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            return "git is not installed or not on PATH."
        return ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"

    def git_action(self, action: str, message: str = "", pathspec: str = "-A",
                   name: str = "") -> dict[str, Any]:
        # Read-only / fixed-argv presets.
        presets = {
            "status": ["status", "--short", "--branch"],
            "diff": ["diff"],
            "diff_staged": ["diff", "--staged"],
            "log": ["log", "--oneline", "-15"],
            "add": ["add", pathspec or "-A"],
            "branches": ["branch", "-vv", "--all"],
            "show": ["show", "--stat", "HEAD"],
            "push": ["push"],
            "pull": ["pull", "--ff-only"],
            "stash": ["stash"],
            "stash_pop": ["stash", "pop"],
        }
        if action == "commit":
            self._run_git(["add", pathspec or "-A"])
            return {"output": self._run_git(["commit", "-m", message or "update"])}
        # Actions that take a branch name (validated to a safe ref shape).
        if action in ("switch", "new_branch"):
            ref = (name or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", ref):
                return {"output": "invalid branch name"}
            argv = ["checkout", "-b", ref] if action == "new_branch" else ["checkout", ref]
            return {"output": self._run_git(argv)}
        if action not in presets:
            return {"output": f"unknown git action: {action}"}
        return {"output": self._run_git(presets[action])}

    # -- Static preview web server ---------------------------------------
    def server_status(self) -> dict[str, Any]:
        running = self._static_httpd is not None
        return {
            "running": running,
            "port": self._static_port if running else None,
            "url": f"http://{self._static_host}:{self._static_port}" if running else None,
        }

    def server_start(self, port: int = 8080) -> dict[str, Any]:
        with self._lock:
            if self._static_httpd is not None:
                return self.server_status()
            handler = functools.partial(
                SimpleHTTPRequestHandler, directory=str(self.config.workdir)
            )
            httpd = ThreadingHTTPServer((self._static_host, int(port)), handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self._static_httpd = httpd
            self._static_thread = thread
            self._static_port = int(port)
            return self.server_status()

    def server_stop(self) -> dict[str, Any]:
        with self._lock:
            if self._static_httpd is not None:
                self._static_httpd.shutdown()
                self._static_httpd.server_close()
                self._static_httpd = None
                self._static_thread = None
                self._static_port = None
            return self.server_status()

    # -- Editor (in-browser IDE) -----------------------------------------
    def fs_tree(self, limit: int = 1000) -> dict[str, Any]:
        from .tools.search import IGNORE_DIRS

        root = self.config.workdir
        files: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            files.append(str(p.relative_to(root)))
            if len(files) >= limit:
                break
        return {"root": str(root), "files": files}

    def fs_read(self, path: str) -> dict[str, Any]:
        try:
            p = self.agent.ctx.safe_path(path)
        except ToolError as exc:
            return {"error": str(exc)}
        if not p.is_file():
            return {"error": f"not a file: {path}"}
        try:
            content = p.read_text("utf-8")
        except UnicodeDecodeError:
            return {"error": f"binary file (cannot edit as text): {path}"}
        return {"path": path, "content": content}

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        try:
            p = self.agent.ctx.safe_path(path)
        except ToolError as exc:
            return {"error": str(exc)}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(content)}


