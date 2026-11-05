// Stage 1: paths, util, events, config round-trip.
#include "core/config.hpp"
#include "core/events.hpp"
#include "core/paths.hpp"
#include "core/util.hpp"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>

using namespace ca;
namespace fs = std::filesystem;

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static void test_paths() {
    setenv("CODER_AGENT_HOME", "/tmp/ca_test_home", 1);
    CHECK(user_dir() == fs::path("/tmp/ca_test_home"));
    CHECK(project_cache_dir("/p") == fs::path("/p/.agent"));
    CHECK(github_dir("/p") == fs::path("/p/.github"));
    CHECK(!agent_read_roots().empty());
}

static void test_util() {
    CHECK(json_escape("a\"b\\c") == "a\\\"b\\\\c");
    CHECK(json_escape("\n") == "\\n");
    auto fm = parse_front_matter("---\nname: foo\n  desc : 'bar baz'\n---\nbody line\n");
    CHECK(fm.meta.size() == 2);
    CHECK(fm.meta[0].first == "name" && fm.meta[0].second == "foo");
    CHECK(fm.meta[1].second == "bar baz");
    CHECK(fm.body == "body line\n");
    CHECK(parse_front_matter("no fm").body == "no fm");
    auto t = truncate_middle(std::string(100, 'x'), 10);
    CHECK(t.size() < 100 && t.find("90 chars omitted") != std::string::npos);
    CHECK(truncate_middle("short", 10) == "short");
    CHECK(collapse_ws("a  b\n\tc") == "a b c");
    CHECK(shorten("hello world", 5) == "hell…");
    CHECK(shorten("hi", 5) == "hi");
}

static void test_events() {
    EventBus bus;
    int count = 0;
    std::string last_kind;
    auto un = bus.subscribe([&](const Event& e) { count++; last_kind = e.kind; });
    bus.emit("status", "main", {{"state", "idle"}});
    bus.emit("warning", "sub", {{"text", "hi"}});
    CHECK(count == 2 && last_kind == "warning");
    un();
    bus.emit("status");
    CHECK(count == 2);
}

static void test_config_roundtrip() {
    setenv("CODER_AGENT_HOME", "/tmp/ca_test_home2", 1);
    fs::remove_all("/tmp/ca_test_home2");
    AppConfig c;
    c.server.model = "qwen3.8-27b:latest";
    c.server.bypass_proxy = false;
    c.thinking.effort = "high";
    c.thinking.enable_thinking = true;
    c.thinking.token_budget = 4096;
    c.sampling.temperature = 0.3;
    c.context.max_tokens = 32768;
    c.agent.parallelism = 2;
    c.paths.prompt_dirs = {"a", "b"};
    auto p = save_config(c);
    CHECK(p == fs::path("/tmp/ca_test_home2/config.yaml"));
    auto c2 = AppConfig::from_json(c.to_json());
    CHECK(c2.server.model == "qwen3.8-27b:latest");
    // reload from disk
    auto c3 = load_config();
    CHECK(c3.server.model == "qwen3.8-27b:latest");
    CHECK(c3.server.bypass_proxy == false);
    CHECK(c3.thinking.effort == "high");
    CHECK(c3.thinking.enable_thinking.has_value() && *c3.thinking.enable_thinking);
    CHECK(c3.thinking.token_budget.has_value() && *c3.thinking.token_budget == 4096);
    CHECK(c3.sampling.temperature.has_value() && *c3.sampling.temperature == 0.3);
    CHECK(c3.context.max_tokens == 32768);
    CHECK(c3.agent.parallelism == 2);
    CHECK(c3.paths.prompt_dirs.size() == 2 && c3.paths.prompt_dirs[1] == "b");
    // project layer overrides user layer
    fs::create_directories("/tmp/proj/.agent");
    {
        std::ofstream f("/tmp/proj/.agent/config.yaml");
        f << "server:\n  model: proj-model\n";
    }
    auto c4 = load_config("/tmp/proj");
    CHECK(c4.server.model == "proj-model");
    CHECK(c4.server.bypass_proxy == false);  // inherited from user layer
    CHECK(c4.paths.project_root == "/tmp/proj");
}

int main() {
    test_paths();
    test_util();
    test_events();
    test_config_roundtrip();
    if (failures) { std::printf("%d FAILURES\n", failures); return 1; }
    std::printf("test_config OK\n");
    return 0;
}
