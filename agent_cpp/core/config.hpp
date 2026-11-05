#pragma once
// Application configuration: built-in defaults < ~/.coder_agent/config.toml
// < <project>/.agent/config.toml  (later wins, deep-merged).

#include <filesystem>
#include <optional>
#include <string>
#include <vector>
#include "nlohmann/json.hpp"

namespace ca {

struct ServerConfig {
    std::string provider = "openai_compat";
    std::string base_url = "http://127.0.0.1:11434/v1";
    std::string api_key = "EMPTY";
    std::string model = "qwen3.8-27b";
    double timeout = 900.0;          // seconds between streamed chunks
    double connect_timeout = 10.0;
    bool bypass_proxy = true;        // ignore HTTP(S)_PROXY env (LAN servers)
};

struct ThinkingConfig {
    std::string effort = "medium";   // default|none|low|medium|high
    bool send_effort = true;
    std::optional<bool> enable_thinking;
    std::optional<int> token_budget;
    int max_think_chars = 0;         // >0: abort overlong thinking and retry with thinking off
    nlohmann::json extra_body = nlohmann::json::object();
};

struct SamplingConfig {
    std::optional<double> temperature = 0.6;
    std::optional<double> top_p = 0.95;
    std::optional<int> max_tokens;
};

struct ContextConfig {
    int max_tokens = 131072;
    double compress_threshold = 0.95;
    double compress_target = 0.60;
    std::string algorithm = "summarize";
    int keep_recent_units = 10;
    int tool_output_max_chars = 12000;
};

struct AgentConfig {
    int max_steps = 40;
    int parallelism = 1;
    int loop_limit = 3;
    bool auto_skills = true;
    int failure_hint_after = 2;
    int gdb_hint_after = 4;
};

struct OrchestratorConfig {
    int max_subtasks = 8;
    int subtask_max_steps = 20;
    bool planner_thinking = false;
    bool doc_analysis = true;
    bool confirm_each_stage = true;
};

struct PathsConfig {
    std::string project_root;
    std::vector<std::string> prompt_dirs;
};

struct AppConfig {
    ServerConfig server;
    ThinkingConfig thinking;
    SamplingConfig sampling;
    ContextConfig context;
    AgentConfig agent;
    OrchestratorConfig orchestrator;
    PathsConfig paths;

    // ---- (de)serialization ---------------------------------------------
    nlohmann::json to_json() const;
    static AppConfig from_json(const nlohmann::json& j);
};

std::filesystem::path user_config_path();
std::filesystem::path project_config_path(const std::filesystem::path& root);

// Load defaults, then user config, then project config (if root given or in user config).
AppConfig load_config(const std::filesystem::path& project_root = std::filesystem::path());

// Write cfg to path (defaults to user config). Returns the path written.
std::filesystem::path save_config(const AppConfig& cfg,
                                  const std::filesystem::path& path = std::filesystem::path());

} // namespace ca
