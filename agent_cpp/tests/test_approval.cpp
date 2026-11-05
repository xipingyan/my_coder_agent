// Stage 1: shell splitting, tokenization, glob match, policy checks.
#include "core/approval.hpp"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>

using namespace ca;
namespace fs = std::filesystem;

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static void test_split() {
    auto [segs, unsafe] = split_command("ls -la && git status | grep x");
    CHECK(segs.size() == 3);
    CHECK(segs[0] == "ls -la" && segs[1] == "git status" && segs[2] == "grep x");
    CHECK(unsafe.empty());

    auto r2 = split_command("echo $(whoami)");
    CHECK(!r2.second.empty());  // command substitution

    auto r3 = split_command("make 2>&1");
    CHECK(r3.first.size() == 1 && r3.first[0] == "make");
    CHECK(r3.second.empty());

    auto r4 = split_command("echo hi > out.txt");
    CHECK(!r4.second.empty());  // redirection

    auto r5 = split_command("echo 'a b' \"c d\"");
    CHECK(r5.first.size() == 1);
    auto toks = shlex_tokens(r5.first[0]);
    CHECK(toks && toks->size() == 3 && (*toks)[1] == "a b" && (*toks)[2] == "c d");

    auto r6 = split_command("FOO=bar baz --x");
    auto t6 = shlex_tokens(r6.first[0]);
    CHECK(t6 && t6->size() == 2 && (*t6)[0] == "baz");

    auto r7 = split_command("echo 'unbalanced");
    CHECK(!shlex_tokens(r7.first[0]).has_value());
}

static void test_glob() {
    CHECK(path_glob_match("build/x.o", "build/*"));
    CHECK(path_glob_match("build/sub/x.o", "build/*"));  // * crosses /
    CHECK(!path_glob_match("src/x.o", "build/*"));
    CHECK(path_glob_match("a/b/c", "*"));
    CHECK(path_glob_match(".git/config", ".git/*"));
    CHECK(path_glob_match("a.c", "a.?"));
    CHECK(!path_glob_match("ab.c", "a.?"));
}

static void test_policy() {
    setenv("CODER_AGENT_HOME", "/tmp/ca_test_approval_home", 1);
    fs::remove_all("/tmp/ca_test_approval_home");
    auto p = ApprovalPolicy::load();
    CHECK(fs::exists(p.user_file));  // template created

    CHECK(p.check_command("ls -la").action == "allow");
    CHECK(p.check_command("cmake --build build -j8").action == "allow");
    CHECK(p.check_command("git status && ls").action == "allow");
    CHECK(p.check_command("sudo rm x").action == "deny");
    CHECK(p.check_command("rm -rf /").action == "deny");
    CHECK(p.check_command("python3 app.py").action == "ask");
    CHECK(p.check_command("echo $(id)").action == "ask");
    CHECK(p.check_command("").action == "deny");
    CHECK(p.check_command("ls > out").action == "ask");

    CHECK(p.check_write("build/x.o").action == "allow");
    CHECK(p.check_write(".git/config").action == "deny");
    CHECK(p.check_write("src/main.cpp").action == "ask");

    // remember rule -> next time allowed
    CHECK(p.suggest_rule("command", {{"command", "pytest -q"}}) == "pytest -q");
    CHECK(p.suggest_rule("write", {{"path", "src/sub/a.cpp"}}) == "src/sub/*");
    auto f = p.remember("command", "pytest -q", "project");
    CHECK(f.has_value());
    CHECK(p.check_command("pytest -q tests/").action == "allow");
    // file rule remember
    p.remember("write", "src/sub/*", "project");
    CHECK(p.check_write("src/sub/a.cpp").action == "allow");

    // suggested rule for a bare command
    CHECK(p.suggest_rule("command", {{"command", "pytest"}}) == "pytest");
    CHECK(p.suggest_rule("write", {{"path", "a.cpp"}}) == "a.cpp");
}

int main() {
    test_split();
    test_glob();
    test_policy();
    if (failures) { std::printf("%d FAILURES\n", failures); return 1; }
    std::printf("test_approval OK\n");
    return 0;
}
