#pragma once
// Approval policy: decide whether a command / file write runs automatically.

#include <filesystem>
#include <optional>
#include <string>
#include <vector>
#include "nlohmann/json.hpp"

namespace ca {

struct Decision {
    std::string action;  // allow | ask | deny
    std::string reason;
};

struct ApprovalRequest {
    std::string kind;  // command | write
    std::string tool;
    std::string title;
    std::string detail;  // command line, or unified diff
    nlohmann::json args = nlohmann::json::object();
    std::string suggested_rule;
};

struct ApprovalResponse {
    bool allow = false;
    std::string feedback;       // sent back to the model when rejected
    std::string remember_rule;  // add to policy when non-empty
    std::string remember_scope = "project";  // project | user
};

// Split a shell line into simple commands: (segments, unsafe_reason)
std::pair<std::vector<std::string>, std::string> split_command(const std::string& cmd);

// shlex-like tokenization; leading VAR=value stripped. nullopt on unbalanced quotes.
std::optional<std::vector<std::string>> shlex_tokens(const std::string& seg);

// Glob match where '*' also matches '/' (fnmatch semantics on relative paths).
bool path_glob_match(const std::string& path, const std::string& pattern);

class ApprovalPolicy {
public:
    ApprovalPolicy() = default;

    // user file is created from the built-in template if missing;
    // project file is <root>/.agent/approval.yaml when root given.
    static ApprovalPolicy load(const std::filesystem::path& root = std::filesystem::path());
    void reload();

    Decision check_command(const std::string& command) const;
    Decision check_write(const std::string& rel_path) const;

    static std::string suggest_rule(const std::string& kind, const nlohmann::json& args);
    // Add rule to the allow list of the given scope. Returns the file written, or nullopt.
    std::optional<std::filesystem::path> remember(const std::string& kind, const std::string& rule,
                                                   const std::string& scope = "project");

    nlohmann::json data;
    std::filesystem::path user_file;
    std::filesystem::path project_file;
};

} // namespace ca
