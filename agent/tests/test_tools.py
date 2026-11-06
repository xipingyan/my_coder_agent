import threading
import time

import pytest

from coder_agent.tools import ToolContext, default_registry


@pytest.fixture
def ctx(project):
    return ToolContext(root=project)


@pytest.fixture
def reg():
    return default_registry()


def run(reg, ctx, name, **args):
    return reg.execute(name, args, ctx)


def test_sandbox_blocks_escape(reg, ctx):
    ok, text = run(reg, ctx, "read_file", path="../../etc/passwd")
    assert not ok and "outside" in text
    ok, text = run(reg, ctx, "read_file", path="/etc/passwd")
    assert not ok and "outside" in text


def test_write_read_list_find_grep(reg, ctx, project):
    assert run(reg, ctx, "write_file", path="src/a.cpp", content="int main() {\n  return 0;\n}\n")[0]
    ok, text = run(reg, ctx, "read_file", path="src/a.cpp")
    assert ok and "    2 |   return 0;" in text
    assert "a.cpp" in run(reg, ctx, "list_dir", path="src")[1]
    assert "src/a.cpp" in run(reg, ctx, "find_files", pattern="*.cpp")[1]
    assert "src/a.cpp:2" in run(reg, ctx, "grep_search", pattern="return")[1]


def test_apply_patch_exact_unique_and_errors(reg, ctx, project):
    (project / "f.txt").write_text("a\nb\nc\nb\n")
    ok, text = run(reg, ctx, "apply_patch", path="f.txt", search="a\nb", replace="A\nb")
    assert ok and (project / "f.txt").read_text() == "A\nb\nc\nb\n"
    ok, text = run(reg, ctx, "apply_patch", path="f.txt", search="b", replace="X")
    assert not ok and "matches 2" in text
    assert run(reg, ctx, "apply_patch", path="f.txt", search="b", replace="X", replace_all=True)[0]
    ok, text = run(reg, ctx, "apply_patch", path="f.txt", search="nope", replace="x")
    assert not ok and "not found" in text


def test_apply_patch_trailing_whitespace_tolerant_and_crlf(reg, ctx, project):
    (project / "w.txt").write_bytes(b"x = 1   \r\ny = 2\r\n")
    assert run(reg, ctx, "apply_patch", path="w.txt", search="x = 1\ny = 2", replace="x = 9\ny = 2")[0]
    assert (project / "w.txt").read_bytes() == b"x = 9\r\ny = 2\r\n"


def test_missing_required_param_message(reg, ctx):
    ok, text = run(reg, ctx, "read_file")
    assert not ok and "missing required" in text


def test_type_coercion(reg, ctx, project):
    (project / "n.txt").write_text("1\n2\n3\n")
    ok, text = run(reg, ctx, "read_file", path="n.txt", start_line="2")
    assert ok and "2 | 2" in text


def test_run_command_output_exit_and_stdin_closed(reg, ctx):
    ok, text = run(reg, ctx, "run_command", command="echo hi; exit 3")
    assert ok and "exit code: 3" in text and "hi" in text
    ok, text = run(reg, ctx, "run_command", command="cat")  # would hang if stdin were open
    assert "exit code: 0" in text


def test_run_command_timeout_and_cancel(reg, ctx):
    t = time.time()
    ok, text = run(reg, ctx, "run_command", command="sleep 30", timeout=1)
    assert "timed out" in text and time.time() - t < 10
    threading.Timer(0.5, ctx.cancel.set).start()
    ok, text = run(reg, ctx, "run_command", command="sleep 30")
    assert "cancelled" in text


def test_run_command_cwd_confined(reg, ctx):
    ok, text = run(reg, ctx, "run_command", command="pwd", cwd="/")
    assert not ok and "outside" in text


def test_remember_tool(reg, ctx, project):
    assert run(reg, ctx, "remember", note="build with cmake -B build")[0]
    assert "build with cmake" in (project / ".agent" / "memory.md").read_text()
