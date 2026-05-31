"""Tests for the deepened git panel: branches, switch, stash, show, log."""

from __future__ import annotations

import subprocess

import pytest


def _git(wd, *args):
    subprocess.run(["git", *args], cwd=wd, capture_output=True, text=True, check=True)


def _repo(tmp_path):
    wd = tmp_path
    _git(wd, "init", "-q")
    _git(wd, "config", "user.email", "t@t")
    _git(wd, "config", "user.name", "t")
    _git(wd, "config", "commit.gpgsign", "false")   # env enforces signing; off for the test
    (wd / "f.txt").write_text("one\n")
    _git(wd, "add", "-A")
    _git(wd, "commit", "-qm", "init")
    return wd


def _service(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    from aio.config import load_config
    from aio.service import AgentService

    return AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    _repo(tmp_path)
    return _service(tmp_path, monkeypatch)


def test_branches_and_new_branch_and_switch(svc, tmp_path):
    svc.git_action("new_branch", name="feature/x")
    # now on the new branch
    assert "feature/x" in svc.git_action("branches")["output"]
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path,
                         capture_output=True, text=True).stdout.strip()
    assert cur == "feature/x"
    # switch back to the original branch
    base = svc.git_action("branches")["output"]
    main_name = "master" if "master" in base else "main"
    svc.git_action("switch", name=main_name)
    cur = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path,
                         capture_output=True, text=True).stdout.strip()
    assert cur == main_name


def test_invalid_branch_name_rejected(svc):
    out = svc.git_action("new_branch", name="bad name; rm -rf /")["output"]
    assert "invalid branch name" in out


def test_stash_roundtrip(svc, tmp_path):
    (tmp_path / "f.txt").write_text("two\n")              # dirty the tree
    svc.git_action("stash")
    assert (tmp_path / "f.txt").read_text() == "one\n"    # stashed -> reverted
    svc.git_action("stash_pop")
    assert (tmp_path / "f.txt").read_text() == "two\n"    # restored


def test_show_and_log(svc):
    assert "init" in svc.git_action("log")["output"]
    assert "f.txt" in svc.git_action("show")["output"]


def test_unknown_action(svc):
    assert "unknown git action" in svc.git_action("frobnicate")["output"]
