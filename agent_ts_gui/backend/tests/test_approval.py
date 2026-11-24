"""Unit tests for the approval policy (approval.py).

Focus: the default policy must auto-approve the agent's own test workflow,
notably ``source ./python-env/bin/activate`` (and compound lines that chain a
``python3 -c`` after it), both with normal rules and with auto_approve_all.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

from coder_agent.approval import ApprovalPolicy

HERE = Path(__file__).parent
BACKEND = HERE.parent


def _default_data() -> dict:
    """The packaged default policy, exactly as shipped to new installations."""
    return tomllib.loads(
        (resources.files("coder_agent") / "defaults" / "approval.toml").read_text(encoding="utf-8")
    )


def _policy(tmp_path: Path, **overrides) -> ApprovalPolicy:
    data = _default_data()
    data.update(overrides)
    return ApprovalPolicy(data, root=tmp_path)


def test_source_venv_activate_is_allowed(tmp_path):
    p = _policy(tmp_path)
    d = p.check_command("source ./python-env/bin/activate", cwd=tmp_path)
    assert d.action == "allow", d


def test_source_then_python_test_is_allowed(tmp_path):
    """The exact pattern the agent uses to run its own python tests."""
    p = _policy(tmp_path)
    d = p.check_command(
        'source ./python-env/bin/activate && python3 -c "import csv; print(1)"', cwd=tmp_path
    )
    assert d.action == "allow", d


def test_git_status_is_allowed(tmp_path):
    p = _policy(tmp_path)
    d = p.check_command("git status", cwd=tmp_path)
    assert d.action == "allow", d


def test_auto_approve_all_allows_source(tmp_path):
    p = _policy(tmp_path, auto_approve_all=True)
    d = p.check_command("source ./python-env/bin/activate", cwd=tmp_path)
    assert d.action == "allow", d
    # auto_approve_all covers even non-allow-listed commands
    d = p.check_command("source /some/other/script.sh", cwd=tmp_path)
    assert d.action == "allow", d


def test_unsafe_commands_still_ask(tmp_path):
    """Without auto_approve_all, command substitution always asks."""
    p = _policy(tmp_path)
    d = p.check_command("echo $(rm -rf /)", cwd=tmp_path)
    assert d.action == "ask", d


def test_intranet_urls_are_allowed(tmp_path):
    """Intranet hosts (localhost / loopback / RFC1918) are auto-approved, incl. curl/wget."""
    p = _policy(tmp_path)
    for cmd in (
        "curl http://127.0.0.1:8931/health",
        "curl http://localhost:8931/health",
        "wget http://192.168.1.10/file.tar.gz -O build/file.tar.gz",
        "curl http://10.0.0.5:8080/api",
        "curl http://172.16.4.2:9000/x",
    ):
        d = p.check_command(cmd, cwd=tmp_path)
        assert d.action == "allow", (cmd, d)


def test_external_urls_still_ask(tmp_path):
    """External (non-intranet) hosts always ask, even for curl/wget downloads."""
    p = _policy(tmp_path)
    for cmd in (
        "curl https://example.com/file.tar.gz -O build/file.tar.gz",
        "wget http://1.2.3.4/file.tar.gz",
        "curl http://public.example.org:8080/x",
    ):
        d = p.check_command(cmd, cwd=tmp_path)
        assert d.action == "ask", (cmd, d)


def test_deny_rules_still_win(tmp_path):
    p = _policy(tmp_path, auto_approve_all=True)
    d = p.check_command("sudo ls", cwd=tmp_path)
    assert d.action == "deny", d
