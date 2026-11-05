#include "config.hpp"
#include "paths.hpp"
#include <fstream>
#include <sstream>
#include <toml.hpp>

namespace ca {
using json = nlohmann::json;

// ---------------------------------------------------------------- helpers
static json toml_to_json(const toml::node& n) {
    if (n.is_string()) return n.value<std::string>().value();
    if (n.is_boolean()) return n.value<bool>().value();
    if (n.is_integer()) return (double)n.value<int64_t>().value();
    if (n.is_floating_point()) return n.value<double>().value();
    if (n.is_array()) {
        json arr = json::array();
        for (auto& e : *n.as_array()) arr.push_back(toml_to_json(e));
        return arr;
    }
    if (n.is_table()) {
        json obj = json::object();
        for (auto& kv : *n.as_table()) obj[kv.first] = toml_to_json(kv.second);
        return obj;
    }
    return nullptr;
}

// Insert a json value into a toml table under `key` (skips null).
static void json_into(toml::table& tbl, const std::string& key, const json& j) {
    if (j.is_null()) return;
    if (j.is_boolean()) tbl.emplace(key, j.get<bool>());
    else if (j.is_number_integer()) tbl.emplace(key, (int64_t)j.get<int64_t>());
    else if (j.is_number_float()) tbl.emplace(key, j.get<double>());
    else if (j.is_string()) tbl.emplace(key, j.get<std::string>());
    else if (j.is_array()) {
        toml::array arr;
        for (auto& e : j) {
            if (e.is_null()) continue;
            if (e.is_boolean()) arr.emplace_back(e.get<bool>());
            else if (e.is_number_integer()) arr.emplace_back((int64_t)e.get<int64_t>());
            else if (e.is_number_float()) arr.emplace_back(e.get<double>());
            else if (e.is_string()) arr.emplace_back(e.get<std::string>());
        }
        tbl.emplace(key, std::move(arr));
    } else if (j.is_object()) {
        toml::table sub;
        for (auto it = j.begin(); it != j.end(); ++it) json_into(sub, it.key(), it.value());
        tbl.emplace(key, std::move(sub));
    }
}

static json deep_merge(const json& base, const json& over) {
    if (!base.is_object() || !over.is_object()) return over;
    json out = base;
    for (auto it = over.begin(); it != over.end(); ++it)
        if (out.contains(it.key()) && out[it.key()].is_object())
            out[it.key()] = deep_merge(out[it.key()], it.value());
        else
            out[it.key()] = it.value();
    return out;
}

static json read_toml(const std::filesystem::path& p) {
    std::ifstream f(p);
    if (!f) return json::object();
    try {
        auto res = toml::parse(f);
        return toml_to_json(res);
    } catch (const std::exception&) {
        return json::object();
    }
}

// ---------------------------------------------------------------- serde
static json opt_or_null(const std::optional<double>& v) { return v ? json(*v) : json(nullptr); }
static std::optional<double> null_or_val(const json& j) { return j.is_null() ? std::optional<double>{} : std::optional<double>(j.get<double>()); }
static std::optional<int> null_or_int(const json& j) { return j.is_null() ? std::optional<int>{} : std::optional<int>(j.get<int>()); }
static std::optional<bool> null_or_bool(const json& j) { return j.is_null() ? std::optional<bool>{} : std::optional<bool>(j.get<bool>()); }

json AppConfig::to_json() const {
    json t = json::object();
    if (thinking.enable_thinking) t["enable_thinking"] = *thinking.enable_thinking;
    if (thinking.token_budget) t["token_budget"] = *thinking.token_budget;
    t["effort"] = thinking.effort;
    t["send_effort"] = thinking.send_effort;
    t["max_think_chars"] = thinking.max_think_chars;
    if (!thinking.extra_body.empty()) t["extra_body"] = thinking.extra_body;
    json s = json::object();
    s["temperature"] = opt_or_null(sampling.temperature);
    s["top_p"] = opt_or_null(sampling.top_p);
    s["max_tokens"] = sampling.max_tokens ? json(*sampling.max_tokens) : json(nullptr);
    json out = json::object();
    out["server"] = json{{"provider", server.provider}, {"base_url", server.base_url},
                          {"api_key", server.api_key}, {"model", server.model},
                          {"timeout", server.timeout}, {"connect_timeout", server.connect_timeout},
                          {"bypass_proxy", server.bypass_proxy}};
    out["thinking"] = std::move(t);
    out["sampling"] = std::move(s);
    out["context"] = json{{"max_tokens", context.max_tokens},
                          {"compress_threshold", context.compress_threshold},
                          {"compress_target", context.compress_target},
                          {"algorithm", context.algorithm},
                          {"keep_recent_units", context.keep_recent_units},
                          {"tool_output_max_chars", context.tool_output_max_chars}};
    out["agent"] = json{{"max_steps", agent.max_steps}, {"parallelism", agent.parallelism},
                        {"loop_limit", agent.loop_limit}, {"auto_skills", agent.auto_skills},
                        {"failure_hint_after", agent.failure_hint_after},
                        {"gdb_hint_after", agent.gdb_hint_after}};
    out["orchestrator"] = json{{"max_subtasks", orchestrator.max_subtasks},
                               {"subtask_max_steps", orchestrator.subtask_max_steps},
                               {"planner_thinking", orchestrator.planner_thinking},
                               {"doc_analysis", orchestrator.doc_analysis},
                               {"confirm_each_stage", orchestrator.confirm_each_stage}};
    out["paths"] = json{{"project_root", paths.project_root}, {"prompt_dirs", paths.prompt_dirs}};
    return out;
}

AppConfig AppConfig::from_json(const json& j) {
    AppConfig c;
    if (auto v = j.value("server", json::object())) {
        c.server.provider = v.value("provider", c.server.provider);
        c.server.base_url = v.value("base_url", c.server.base_url);
        c.server.api_key = v.value("api_key", c.server.api_key);
        c.server.model = v.value("model", c.server.model);
        c.server.timeout = v.value("timeout", c.server.timeout);
        c.server.connect_timeout = v.value("connect_timeout", c.server.connect_timeout);
        c.server.bypass_proxy = v.value("bypass_proxy", c.server.bypass_proxy);
    }
    if (auto v = j.value("thinking", json::object())) {
        c.thinking.effort = v.value("effort", c.thinking.effort);
        c.thinking.send_effort = v.value("send_effort", c.thinking.send_effort);
        if (v.contains("enable_thinking") && !v["enable_thinking"].is_null())
            c.thinking.enable_thinking = v["enable_thinking"].get<bool>();
        if (v.contains("token_budget") && !v["token_budget"].is_null())
            c.thinking.token_budget = v["token_budget"].get<int>();
        c.thinking.max_think_chars = v.value("max_think_chars", c.thinking.max_think_chars);
        if (v.contains("extra_body")) c.thinking.extra_body = v["extra_body"];
    }
    if (auto v = j.value("sampling", json::object())) {
        c.sampling.temperature = null_or_val(v.value("temperature", json(nullptr)));
        c.sampling.top_p = null_or_val(v.value("top_p", json(nullptr)));
        c.sampling.max_tokens = null_or_int(v.value("max_tokens", json(nullptr)));
    }
    if (auto v = j.value("context", json::object())) {
        c.context.max_tokens = v.value("max_tokens", c.context.max_tokens);
        c.context.compress_threshold = v.value("compress_threshold", c.context.compress_threshold);
        c.context.compress_target = v.value("compress_target", c.context.compress_target);
        c.context.algorithm = v.value("algorithm", c.context.algorithm);
        c.context.keep_recent_units = v.value("keep_recent_units", c.context.keep_recent_units);
        c.context.tool_output_max_chars = v.value("tool_output_max_chars", c.context.tool_output_max_chars);
    }
    if (auto v = j.value("agent", json::object())) {
        c.agent.max_steps = v.value("max_steps", c.agent.max_steps);
        c.agent.parallelism = v.value("parallelism", c.agent.parallelism);
        c.agent.loop_limit = v.value("loop_limit", c.agent.loop_limit);
        c.agent.auto_skills = v.value("auto_skills", c.agent.auto_skills);
        c.agent.failure_hint_after = v.value("failure_hint_after", c.agent.failure_hint_after);
        c.agent.gdb_hint_after = v.value("gdb_hint_after", c.agent.gdb_hint_after);
    }
    if (auto v = j.value("orchestrator", json::object())) {
        c.orchestrator.max_subtasks = v.value("max_subtasks", c.orchestrator.max_subtasks);
        c.orchestrator.subtask_max_steps = v.value("subtask_max_steps", c.orchestrator.subtask_max_steps);
        c.orchestrator.planner_thinking = v.value("planner_thinking", c.orchestrator.planner_thinking);
        c.orchestrator.doc_analysis = v.value("doc_analysis", c.orchestrator.doc_analysis);
        c.orchestrator.confirm_each_stage = v.value("confirm_each_stage", c.orchestrator.confirm_each_stage);
    }
    if (auto v = j.value("paths", json::object())) {
        c.paths.project_root = v.value("project_root", c.paths.project_root);
        if (v.contains("prompt_dirs"))
            for (auto& s : v["prompt_dirs"]) c.paths.prompt_dirs.push_back(s.get<std::string>());
    }
    return c;
}

// ---------------------------------------------------------------- loading
std::filesystem::path user_config_path() { return user_dir() / "config.toml"; }
std::filesystem::path project_config_path(const std::filesystem::path& root) {
    return project_cache_dir(root) / "config.toml";
}

AppConfig load_config(const std::filesystem::path& project_root) {
    json data = read_toml(user_config_path());
    std::filesystem::path root = project_root;
    if (root.empty()) {
        if (auto v = data.value("paths", json::object()).value("project_root", json(nullptr)))
            if (v.is_string() && !v.get<std::string>().empty())
                root = v.get<std::string>();
    }
    if (!root.empty()) {
        data = deep_merge(data, read_toml(project_config_path(root)));
        data["paths"]["project_root"] = root.string();
    }
    return AppConfig::from_json(data);
}

std::filesystem::path save_config(const AppConfig& cfg, const std::filesystem::path& path) {
    std::filesystem::path p = path.empty() ? user_config_path() : path;
    std::filesystem::create_directories(p.parent_path());
    toml::table root;
    json data = cfg.to_json();
    for (auto it = data.begin(); it != data.end(); ++it)
        json_into(root, it.key(), it.value());
    std::ostringstream oss;
    oss << root;
    std::string text = oss.str();
    std::ofstream f(p, std::ios::binary);
    f << text;
    return p;
}

} // namespace ca
