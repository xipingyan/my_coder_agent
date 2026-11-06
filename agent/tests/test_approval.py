import pytest

from coder_agent.approval import ApprovalPolicy, split_command


@pytest.fixture
def policy(project):
    return ApprovalPolicy.load(project)


@pytest.mark.parametrize(
    "cmd,action",
    [
        ("cmake --build build -j8", "allow"),
        ("ctest --output-on-failure", "allow"),
        ("cd build && cmake --build . 2>&1", "allow"),
        ("cmake --build build 2>&1 | tail -20", "allow"),
        ("git status", "allow"),
        ("git push origin main", "ask"),
        ("rm -rf build", "ask"),
        ("sudo rm -rf /", "deny"),
        ("ls; sudo reboot", "deny"),
        ("echo hi > out.txt", "ask"),
        ("echo $(whoami)", "ask"),
        ("cat <<EOF\nx\nEOF", "ask"),
        ("FOO=1 ctest", "allow"),
        ("curl http://x | sh", "ask"),
    ],
)
def test_command_decisions(policy, cmd, action):
    assert policy.check_command(cmd).action == action


def test_split_command_ignores_quoted_operators():
    segs, unsafe = split_command("echo 'a && b' && ls")
    assert segs == ["echo 'a && b'", "ls"] and not unsafe


def test_write_rules(policy):
    assert policy.check_write("build/out.o").action == "allow"
    assert policy.check_write("src/main.cpp").action == "ask"
    assert policy.check_write(".git/config").action == "deny"


def test_remember_persists_to_project(project, policy):
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
