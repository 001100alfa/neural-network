"""Tests for the unified permission modes (plan/default/accept_edits/admin)."""

from __future__ import annotations

from aio.permissions import MODES, decision, describe, normalize
from aio.tools import ToolContext


# -- mode resolution --------------------------------------------------------

def test_normalize_aliases():
    assert normalize("acceptEdits") == "accept_edits"
    assert normalize("bypassPermissions") == "admin"
    assert normalize("yes") == "admin"
    assert normalize("readonly") == "plan"
    assert normalize("PLAN") == "plan"
    assert normalize(None) == "default"
    assert normalize("nonsense") == "default"
    assert set(MODES) == {"plan", "default", "accept_edits", "admin"}


def test_decision_matrix():
    # (mode, tool, needs_approval) -> expected
    assert decision("plan", "write_file", True) == "deny"
    assert decision("plan", "read_file", False) == "allow"      # read-only ok in plan
    assert decision("plan", "run_shell", True) == "deny"

    assert decision("default", "write_file", True) == "ask"
    assert decision("default", "run_shell", True) == "ask"
    assert decision("default", "read_file", False) == "allow"

    assert decision("accept_edits", "write_file", True) == "allow"   # edits auto
    assert decision("accept_edits", "edit_file", True) == "allow"
    assert decision("accept_edits", "run_shell", True) == "ask"      # shell still asks
    assert decision("accept_edits", "git_commit", True) == "ask"

    assert decision("admin", "write_file", True) == "allow"
    assert decision("admin", "run_shell", True) == "allow"           # elevated: all


def test_describe_each_mode():
    for m in MODES:
        assert describe(m) and m.split("_")[0] in describe(m).lower()


# -- ToolContext.permission integration -------------------------------------

def test_context_permission_uses_mode():
    ctx = ToolContext(workdir=".", permission_mode="accept_edits")
    assert ctx.permission("write_file", True) == "allow"
    assert ctx.permission("run_shell", True) == "ask"
    ctx_admin = ToolContext(workdir=".", permission_mode="admin")
    assert ctx_admin.permission("run_shell", True) == "allow"


def test_explicit_rule_overrides_mode():
    # an admin mode still respects an explicit deny rule
    ctx = ToolContext(workdir=".", permission_mode="admin",
                      permissions={"run_shell": "deny"})
    assert ctx.permission("run_shell", True) == "deny"
    # wildcard rule also overrides the mode
    ctx2 = ToolContext(workdir=".", permission_mode="default", permissions={"*": "allow"})
    assert ctx2.permission("edit_file", True) == "allow"


# -- config + service wiring ------------------------------------------------

def test_config_reads_permission_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    from aio.config import load_config
    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic", "permission_mode": "admin"})
    assert cfg.permission_mode == "admin"
    cfg2 = load_config(workdir=tmp_path, overrides={"provider": "anthropic", "permission_mode": "acceptEdits"})
    assert cfg2.permission_mode == "accept_edits"     # normalised


def test_service_set_permission_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    from aio.config import load_config
    from aio.service import AgentService
    svc = AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))

    out = svc.set_permission_mode("admin")
    assert out["permission_mode"] == "admin"
    assert svc.gated is False                          # admin ungates the web mode
    assert svc.info()["permission_mode"] == "admin"

    out2 = svc.set_permission_mode("plan")
    assert out2["permission_mode"] == "plan" and svc.plan_mode is True and svc.gated is True


def test_cli_flags_map_to_modes():
    from aio.cli import build_parser
    p = build_parser()
    assert p.parse_args(["--permission-mode", "admin"]).permission_mode == "admin"
    assert p.parse_args(["-y"]).yes is True
    assert p.parse_args(["--plan"]).plan is True
