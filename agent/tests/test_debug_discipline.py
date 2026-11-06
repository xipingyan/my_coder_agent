import shutil
import subprocess

import pytest

from coder_agent import knowledge
from coder_agent.approval import ApprovalPolicy, ApprovalResponse
from coder_agent.config import AppConfig
from coder_agent.engine import Engine
from coder_agent.events import EventBus
from coder_agent.paths import user_dir
from coder_agent.prompts import PromptBuilder, TaskTypeRegistry
from coder_agent.tools import ToolContext, default_registry

from conftest import FakeProvider

have_gdb = shutil.which("gdb") and shutil.which("gcc")
needs_gdb = pytest.mark.skipif(not have_gdb, reason="gdb and gcc are required")


# ------------------------------------------------------------------ skills
def test_builtin_skill_is_indexed_with_a_readable_path(project):
    idx = {e.name: e for e in knowledge.skill_index(project)}
    assert "debug-cpp" in idx and "debug" in idx["debug-cpp"].description.lower()
    reg, ctx = default_registry(), ToolContext(root=project)
    ok, text = reg.execute("read_file", {"path": idx["debug-cpp"].path}, ctx)
    assert ok and "Suspect 1" in text and "debug_backtrace" in text
    # the exception is read-only and narrow: other outside paths stay blocked
    assert not reg.execute("read_file", {"path": "/etc/passwd"}, ctx)[0]
    assert not reg.execute("write_file", {"path": idx["debug-cpp"].path, "content": "x"}, ctx)[0]


def test_user_skill_is_readable_and_layers_override_by_name(project):
    skill = user_dir() / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: my-skill\ndescription: user level\n---\nUSER BODY\n")
    reg, ctx = default_registry(), ToolContext(root=project)
    assert "USER BODY" in reg.execute("read_file", {"path": str(skill)}, ctx)[1]

    over = project / ".github" / "skills" / "debug-cpp" / "SKILL.md"
    over.parent.mkdir(parents=True)
    over.write_text("---\nname: debug-cpp\ndescription: project override\n---\nPROJECT BODY\n")
    skills = knowledge.load_skills(project)
    assert skills["debug-cpp"].source == "project" and skills["debug-cpp"].body == "PROJECT BODY"
    assert skills["my-skill"].source == "user"
    idx = {e.name: e for e in knowledge.skill_index(project)}
    assert idx["debug-cpp"].path == ".github/skills/debug-cpp/SKILL.md"  # project skills are shown relative


def test_triggers_match_failures_only_and_bad_regex_is_ignored(project):
    assert [s.name for s in knowledge.matching_skills(project, "x.cpp:3:5: error: 'foo' was not declared")] == ["debug-cpp"]
    assert knowledge.matching_skills(project, "[100%] Built target app") == []
    assert knowledge.matching_skills(project, "error: x", exclude={"debug-cpp"}) == []
    bad = project / ".github" / "skills" / "bad" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("---\nname: bad\ndescription: d\ntriggers: (unclosed\n---\nbody\n")
    assert knowledge.load_skills(project)["bad"].triggers is None


def test_prompts_tell_the_model_to_follow_the_debug_skill(project):
    types = TaskTypeRegistry.load(project)
    assert "debug-cpp" in types.base_prompt
    for name in ("cpp_impl", "cmake", "cpp_debug"):
        assert "debug-cpp" in types.get(name).prefix
    sysprompt = PromptBuilder(project, types).build(types.get("cpp_impl"))
    assert "Skills (read the SKILL.md when relevant)" in sysprompt and "debug-cpp" in sysprompt


# --------------------------------------------------- failure discipline loop
def run_script(project, commands, cfg=None, final="done"):
    script = [{"tool_calls": [("run_command", {"command": c})]} for c in commands] + [{"content": final}]
    cfg = cfg or AppConfig()
    cfg.agent.loop_limit = 99
    prov = FakeProvider(script, report_usage=False)
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    eng = Engine(cfg, project, bus, provider=prov)
    eng.policy = ApprovalPolicy({"auto_approve_all": True})
    eng.submit("go")
    results = [m["content"] for m in prov.requests[-1] if m["role"] == "tool"]
    return results, events


def test_suspect_nudge_then_debugger_nudge(project):
    results, events = run_script(project, ["exit 1", "exit 2", "exit 3", "exit 4", "exit 5", "exit 6"])
    has = lambda r, s: s in r
    assert not has(results[0], "[agent hint]")
    assert has(results[1], "STOP trying fixes") and not has(results[1], "debug_backtrace")
    assert not has(results[2], "[agent hint]")
    assert has(results[3], "debug_backtrace") and has(results[3], "4 failed attempts")
    assert not has(results[4], "[agent hint]")
    assert has(results[5], "6 failed attempts")  # repeats every 2 failures after the threshold
    assert any(e.kind == "warning" and "failed commands in a row" in e.data["text"] for e in events)


def test_success_resets_but_neutral_commands_do_not(project):
    results, _ = run_script(project, ["exit 1", "true", "exit 1"])  # a real success resets the streak
    assert all("[agent hint]" not in r for r in results)
    results, _ = run_script(project, ["exit 1", "ls", "exit 1"])  # `ls` proves nothing about the fix
    assert "STOP trying fixes" in results[2]
    results, _ = run_script(project, ["exit 1", "cd . && true", "exit 1"])  # compound with a real command resets
    assert all("[agent hint]" not in r for r in results)


def test_thresholds_are_configurable_and_can_be_disabled(project):
    cfg = AppConfig()
    cfg.agent.failure_hint_after = 1
    cfg.agent.gdb_hint_after = 0
    results, _ = run_script(project, ["exit 1", "exit 2", "exit 3"], cfg)
    assert "STOP trying fixes" in results[0] and "[agent hint]" not in results[1] + results[2]
    cfg = AppConfig()
    cfg.agent.failure_hint_after = cfg.agent.gdb_hint_after = 0
    results, _ = run_script(project, ["exit 1"] * 5, cfg)
    assert all("[agent hint]" not in r for r in results)


def test_skill_is_injected_once_when_a_command_fails_with_a_matching_error(project):
    cmd = "echo 'main.cpp:3:5: error: x was not declared'; exit 1"
    results, events = run_script(project, [cmd, cmd, "echo fine; exit 0"])
    assert "[skill 'debug-cpp' was loaded" in results[0] and "Suspect 1" in results[0]
    assert "[skill 'debug-cpp'" not in results[1]  # only once per run
    assert "[skill" not in results[2]
    assert any(e.kind == "warning" and "injected" in e.data["text"] for e in events)

    cfg = AppConfig()
    cfg.agent.auto_skills = False
    results, _ = run_script(project, [cmd], cfg)
    assert "[skill" not in results[0]


# ---------------------------------------------------------------- gdb tool
CRASH_C = """#include <stdio.h>
int *pass(int *p) { return p; }
int crash(int *p) {
    return *pass(p);
}
int main(void) {
    int *p = 0;
    printf("start\\n");
    return crash(p);
}
"""


@pytest.fixture
def prog(project):
    def build(name, source, flags="-g -O0"):
        (project / f"{name}.c").write_text(source)
        subprocess.run(f"gcc {flags} {name}.c -o {name}", shell=True, cwd=project, check=True)
        return name
    return build


def debug(project, **args):
    return default_registry().execute("debug_backtrace", args, ToolContext(root=project))


@needs_gdb
def test_backtrace_of_a_crash_names_function_line_and_signal(project, prog):
    prog("crash", CRASH_C)
    ok, text = debug(project, program="crash")
    assert ok and "SIGSEGV" in text and "VERDICT: the program was stopped by SIGSEGV" in text
    assert "crash (p=0x0)" in text and "crash.c:4" in text and "main ()" in text
    assert "libthread_db" not in text  # noise is filtered


@needs_gdb
def test_breakpoint_shows_arguments_and_locals(project, prog):
    prog("crash", CRASH_C)
    ok, text = debug(project, program="crash", breakpoint="crash")
    assert ok and "stopped at your breakpoint" in text and "p = 0x0" in text


@needs_gdb
def test_normal_exit_and_exit_code_are_explained(project, prog):
    prog("fine", "int main(void){return 0;}")
    prog("bad", "int main(void){return 3;}")
    assert "exited normally" in debug(project, program="fine")[1]
    assert "exited with code 3" in debug(project, program="bad")[1]


@needs_gdb
def test_hang_is_interrupted_and_located(project, prog):
    prog("hang", "int spin(void){volatile int x=0; for(;;) x++; return x;}\nint main(void){return spin();}")
    ok, text = debug(project, program="hang", timeout=5)
    assert ok and "was interrupted" in text and "spin" in text


@needs_gdb
def test_missing_debug_info_and_bad_input_give_actionable_errors(project, prog):
    prog("nodbg", CRASH_C, flags="-O2 -s")
    ok, text = debug(project, program="nodbg")
    assert not ok and "no debug info" in text and "CMAKE_BUILD_TYPE=Debug" in text
    assert "not found" in debug(project, program="nope")[1]
    assert "outside" in debug(project, program="../../bin/ls")[1]


@needs_gdb
def test_debugger_runs_the_program_so_it_needs_the_same_approval_as_running_it(project, prog):
    prog("crash", CRASH_C)
    asked = []

    def approver(req):
        asked.append(req)
        return ApprovalResponse(True)

    script = [{"tool_calls": [("debug_backtrace", {"program": "crash"})]}, {"content": "found it"}]
    prov = FakeProvider(script, report_usage=False)
    eng = Engine(AppConfig(), project, EventBus(), approver=approver, provider=prov)
    eng.submit("why does crash crash?")
    assert len(asked) == 1 and asked[0].kind == "command"
    assert asked[0].detail.startswith("gdb -batch") and "./crash" in asked[0].detail
    assert asked[0].suggested_rule == "./crash"
    assert "SIGSEGV" in prov.requests[-1][-1]["content"]


@needs_gdb
def test_using_the_debugger_resets_the_failure_streak(project, prog):
    prog("crash", CRASH_C)
    script = [{"tool_calls": [("run_command", {"command": "exit 1"})]},
              {"tool_calls": [("debug_backtrace", {"program": "crash"})]},
              {"tool_calls": [("run_command", {"command": "exit 2"})]},
              {"content": "done"}]
    prov = FakeProvider(script, report_usage=False)
    eng = Engine(AppConfig(), project, EventBus(), provider=prov)
    eng.policy = ApprovalPolicy({"auto_approve_all": True})
    eng.submit("x")
    tool_msgs = [m["content"] for m in prov.requests[-1] if m["role"] == "tool"]
    assert all("STOP trying fixes" not in m for m in tool_msgs)  # streak restarted after the debugger run


# ------------------------------- failures that hide their exit status (pipes, `; echo $?`)
def test_failure_markers_in_output_count_even_when_exit_code_is_zero(project):
    piped = "printf 'a.cpp:1:1: error: bad\\n' | cat"  # like `cmake --build . | tail`: exit status 0
    crashed = "printf 'Segmentation fault (core dumped)\\n'; echo exit=$?"
    results, _ = run_script(project, [piped, crashed])
    assert "STOP trying fixes" in results[1]  # second "failure" in a row
    assert "[skill 'debug-cpp'" in results[0]  # first one also loaded the skill


def test_text_markers_do_not_apply_to_read_only_commands(project):
    # grepping for the word "error:" in a file is not a failure
    (project / "log.txt").write_text("x.cpp:1: error: old\n")
    results, _ = run_script(project, ["grep error: log.txt", "cat log.txt", "grep error: log.txt"])
    assert all("[agent hint]" not in r and "[skill" not in r for r in results)


@needs_gdb
def test_gdb_output_has_no_debuginfod_noise(project, prog):
    prog("crash", CRASH_C)
    ok, text = debug(project, program="crash")
    assert ok and "debuginfod" not in text.lower() and "auto-downloading" not in text
