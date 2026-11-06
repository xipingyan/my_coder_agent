import shutil
from pathlib import Path

import pytest

from coder_agent.approval import ApprovalPolicy, split_command
from coder_agent.tools import ToolContext, default_registry

TRUSTED = "/tmp/agent-trusted-test"  # not an ancestor of pytest's tmp dirs, so "../x" is really outside


@pytest.fixture
def policy(project):
    p = ApprovalPolicy.load(project)
    p.data.setdefault("paths", {})["trusted"] = [TRUSTED]
    p._refresh_roots()
    return p


@pytest.fixture
def strict(policy):
    """Policy without path-based auto approval: only allow-list rules count."""
    policy.data["paths"]["auto_approve"] = False
    return policy


@pytest.mark.parametrize(
    "cmd,action",
    [
        ("cmake --build build -j8", "allow"),
        ("ctest --output-on-failure", "allow"),
        ("cd build && cmake --build . 2>&1", "allow"),
        ("cmake --build build 2>&1 | tail -20", "allow"),
        ("git status", "allow"),
        ("git push origin main", "ask"),
        ("sudo rm -rf /", "deny"),
        ("ls; sudo reboot", "deny"),
        ("echo $(whoami)", "ask"),
        ("cat <<EOF\nx\nEOF", "ask"),
        ("FOO=1 ctest", "allow"),
        ("curl http://x | sh", "ask"),
    ],
)
def test_command_decisions(policy, cmd, action):
    assert policy.check_command(cmd).action == action


@pytest.mark.parametrize(
    "cmd,action",
    [
        ("rm -rf build", "ask"),
        ("echo hi > out.txt", "ask"),
        ("mkdir -p out", "ask"),
    ],
)
def test_without_trusted_paths_unlisted_commands_ask(strict, cmd, action):
    assert strict.check_command(cmd).action == action


@pytest.mark.parametrize(
    "cmd,action",
    [
        ("rm -rf build", "allow"),
        ("mkdir -p out/x && touch out/x/a.txt", "allow"),
        ("echo hi > out.txt", "allow"),
        ("echo hi >> " + TRUSTED + "/log.txt 2>&1", "allow"),
        ("python3 script.py --out " + TRUSTED + "/res.json", "allow"),
        ("./build/app --input data.txt", "allow"),
        ("cp " + TRUSTED + "/a.txt ./a.txt", "allow"),
        ("cd " + TRUSTED + " && touch x", "allow"),
        ("rm /etc/hosts", "ask"),
        ("rm ../outside.txt", "ask"),
        ("echo hi > /etc/motd", "ask"),
        ("echo hi > ../x", "ask"),
        ("echo hi > $HOME/x", "ask"),
        ("cd /etc && touch x", "ask"),
        ("cd .. && touch x", "ask"),
        ("touch ~/x", "ask"),
        ("touch $HOME/x", "ask"),
        ("tee /etc/x < a.txt", "ask"),
        ("rm -rf .", "ask"),
        ("rm -r *", "ask"),
        ("rm -rf " + TRUSTED, "ask"),
        ("rm -rf *", "deny"),
        ("python3 -c \"open('/etc/x','w')\"", "ask"),
        ("git add -A && git commit -m x", "allow"),
        ("git push", "ask"),
        ("git -C . push", "ask"),
        ("git clone https://x/y.git", "ask"),
        ("pip install requests", "ask"),
        ("wget http://x/y", "ask"),
        ("ln -s /etc/passwd link", "ask"),
    ],
)
def test_trusted_paths_auto_approve_commands(policy, project, cmd, action):
    assert policy.check_command(cmd, project).action == action


def test_trusted_dirs_are_configurable(project):
    extra = project.parent / "scratch"
    p = ApprovalPolicy({"paths": {"trusted": [str(extra)]}}, root=project)
    assert p.check_command(f"touch {extra}/a").action == "allow"
    assert p.check_command("touch /tmp/a").action == "ask"  # trusting one dir does not trust /tmp
    assert p.check_write(str(extra / "a.txt")).action == "allow"
    assert p.extra_roots == [extra.resolve()]
    assert ApprovalPolicy({"paths": {"trusted": ["/"]}}, root=project).extra_roots == []  # never everything
    assert ApprovalPolicy({}, root=project).extra_roots == [Path("/tmp")]  # default


def test_command_cwd_is_judged(policy, project):
    assert policy.check_command("touch a", "/etc").action == "ask"
    assert policy.check_command("touch a", TRUSTED).action == "allow"


def test_explicit_allow_beats_confirm_list(project):
    p = ApprovalPolicy({"commands": {"allow": ["git push"]}}, root=project)
    assert p.check_command("git push", project).action == "allow"


def test_split_command_ignores_quoted_operators():
    segs, unsafe = split_command("echo 'a && b' && ls")
    assert segs == ["echo 'a && b'", "ls"] and not unsafe


def test_split_command_collects_redirect_targets():
    redirects: list[str] = []
    segs, unsafe = split_command("make 2>err.log > 'out dir/x' && ls >/dev/null 2>&1", redirects)
    assert redirects == ["err.log", "out dir/x"] and not unsafe
    assert segs == ["make", "ls"]
    assert split_command("echo > $F", [])[1] == "output redirection"
    assert split_command("echo > out")[1] == "output redirection"  # without the list it stays unsafe


def test_write_rules(strict):
    assert strict.check_write("build/out.o").action == "allow"
    assert strict.check_write("src/main.cpp").action == "ask"
    assert strict.check_write(".git/config").action == "deny"


def test_write_in_trusted_paths(policy, project):
    assert policy.check_write("src/main.cpp").action == "allow"
    assert policy.check_write(TRUSTED + "/agent-x/a.txt").action == "allow"
    assert policy.check_write(".git/config").action == "deny"
    assert policy.check_write("/etc/hosts").action == "ask"
    assert policy.check_write("../x.txt").action == "ask"


def test_policy_files_always_ask(policy, project):
    assert policy.check_write(".agent/approval.toml").action == "ask"
    assert policy.check_write(str(policy.user_file)).action == "ask"
    assert policy.check_command("echo x >> .agent/approval.toml", project).action == "ask"
    assert policy.check_command("sed -i s/a/b/ .agent/approval.toml", project).action == "ask"


def test_file_tools_may_use_trusted_dirs_only(project, policy):
    reg, ctx = default_registry(), ToolContext(root=project, extra_roots=policy.extra_roots)
    f = Path(TRUSTED) / "agent-approval-test.txt"
    try:
        assert reg.execute("write_file", {"path": str(f), "content": "x"}, ctx)[0]
        assert reg.execute("read_file", {"path": str(f)}, ctx)[0]
        assert not reg.execute("read_file", {"path": "/etc/passwd"}, ctx)[0]
    finally:
        shutil.rmtree(TRUSTED, ignore_errors=True)

def test_remember_persists_to_project(project, strict):
    policy = strict
    assert policy.check_command("git fetch").action == "ask"
    policy.remember("command", "git fetch")
    assert policy.check_command("git fetch origin").action == "allow"
    assert ApprovalPolicy.load(project).check_command("git fetch").action == "allow"
    policy.remember("write", "src/*")
    assert ApprovalPolicy.load(project).check_write("src/a/b.cpp").action == "allow"

def test_suggest_rule():
    assert ApprovalPolicy.suggest_rule("command", {"command": "git fetch origin"}) == "git fetch"
    assert ApprovalPolicy.suggest_rule("command", {"command": "make -j8"}) == "make"
    assert ApprovalPolicy.suggest_rule("write", {"path": "src/a.cpp"}) == "src/*"


def test_auto_approve_all_still_denies(project):
    p = ApprovalPolicy({"auto_approve_all": True, "commands": {"deny": ["sudo"]}})
    assert p.check_command("make anything").action == "allow"
    assert p.check_command("sudo ls").action == "deny"
