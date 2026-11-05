#include "approval.hpp"
#include "config.hpp"   // toml helpers are in config.cpp; we reuse toml++ directly here
#include "paths.hpp"
#include <fstream>
#include <regex>
#include <set>
#include <toml.hpp>

namespace ca {
using json = nlohmann::json;

// ------------------------------------------------------------- shell split
static const std::regex SAFE_REDIRECT("^(?:&>\\s*/dev/null|\\d*>{1,2}\\s*/dev/null|\\d*>{1,2}&\\d+)");

std::pair<std::vector<std::string>, std::string> split_command(const std::string& cmd) {
    std::vector<std::string> segs;
    std::string cur;
    std::string unsafe;
    char quote = 0;
    size_t i = 0, n = cmd.size();
    auto starts_with = [&](size_t at, const char* s) {
        return cmd.compare(at, std::strlen(s), s) == 0;
    };
    while (i < n) {
        char c = cmd[i];
        if (quote) {
            if (c == quote) {
                quote = 0;
            } else if (quote == '"' && c == '\\' && i + 1 < n) {
                cur += c;
                i += 1;
                c = cmd[i];
            } else if (quote == '"' && (starts_with(i, "$(") || c == '`')) {
                unsafe = unsafe.empty() ? "command substitution" : unsafe;
            }
            cur += c;
            i += 1;
            continue;
        }
        if (c == '\'' || c == '"') {
            quote = c;
            cur += c;
        } else if (c == '\\' && i + 1 < n) {
            cur += c;
            cur += cmd[i + 1];
            i += 2;
            continue;
        } else if (starts_with(i, "$(") || c == '`' || starts_with(i, "<(") || starts_with(i, ">(")) {
            unsafe = unsafe.empty() ? "command substitution" : unsafe;
            cur += c;
        } else if (starts_with(i, "<<")) {
            unsafe = unsafe.empty() ? "heredoc" : unsafe;
            cur += c;
        } else if (c == '<' || c == '>' || (c == '&' && starts_with(i, "&>"))) {
            std::smatch m;
            std::string rest = cmd.substr(i);
            if (std::regex_search(rest, m, SAFE_REDIRECT)) {
                if (!cur.empty() && std::isdigit((unsigned char)cur.back()) &&
                    (cur.size() == 1 || cur[cur.size() - 2] == ' '))
                    cur.pop_back();  // the fd number of "2>&1"
                i += m[0].length();
                continue;
            }
            if (c == '>' || c == '&')
                unsafe = unsafe.empty() ? "output redirection" : unsafe;
            cur += c;
        } else if (c == ';' || c == '\n' || c == '|' || c == '&') {
            if ((c == '|' || c == '&') && i + 1 < n && cmd[i + 1] == c)
                i += 1;  // && or ||
            segs.push_back(cur);
            cur.clear();
        } else {
            cur += c;
        }
        i += 1;
    }
    segs.push_back(cur);
    if (quote) unsafe = unsafe.empty() ? "unbalanced quote" : unsafe;
    std::vector<std::string> out;
    for (auto& s : segs) {
        auto a = s.find_first_not_of(" \t");
        if (a == std::string::npos) continue;
        auto b = s.find_last_not_of(" \t");
        out.push_back(s.substr(a, b - a + 1));
    }
    return {out, unsafe};
}

// shlex-style split (POSIX-ish): handles single/double quotes and backslash.
std::optional<std::vector<std::string>> shlex_tokens(const std::string& seg) {
    std::vector<std::string> toks;
    std::string cur;
    bool in_tok = false;
    char quote = 0;
    for (size_t i = 0; i < seg.size(); i++) {
        char c = seg[i];
        if (quote) {
            if (c == quote) {
                quote = 0;
            } else if (quote == '"' && c == '\\' && i + 1 < seg.size() &&
                       (seg[i + 1] == '"' || seg[i + 1] == '\\')) {
                cur += seg[++i];
            } else {
                cur += c;
            }
            in_tok = true;
        } else if (c == '\'' || c == '"') {
            quote = c;
            in_tok = true;
        } else if (c == '\\' && i + 1 < seg.size()) {
            cur += seg[++i];
            in_tok = true;
        } else if (c == ' ' || c == '\t') {
            if (in_tok) {
                toks.push_back(cur);
                cur.clear();
                in_tok = false;
            }
        } else {
            cur += c;
            in_tok = true;
        }
    }
    if (quote) return std::nullopt;  // unbalanced quote
    if (in_tok) toks.push_back(cur);
    static const std::regex env_re(R"([A-Za-z_][A-Za-z0-9_]*=)");
    while (!toks.empty() && std::regex_match(toks.front(), env_re)) toks.erase(toks.begin());
    return toks;
}

static bool prefix_match(const std::vector<std::string>& toks, const std::string& rule) {
    auto rt = shlex_tokens(rule);
    if (!rt || rt->empty()) return false;
    if (toks.size() < rt->size()) return false;
    for (size_t i = 0; i < rt->size(); i++)
        if (toks[i] != (*rt)[i]) return false;
    return true;
}

// fnmatch-style: '*' matches any chars INCLUDING '/', '?' matches one, [...] classes.
bool path_glob_match(const std::string& path, const std::string& pattern) {
    // recursive DP
    size_t n = path.size(), m = pattern.size();
    std::vector<std::vector<bool>> dp(n + 1, std::vector<bool>(m + 1, false));
    dp[0][0] = true;
    for (size_t j = 1; j <= m; j++)
        if (pattern[j - 1] == '*') dp[0][j] = dp[0][j - 1];
    for (size_t i = 1; i <= n; i++) {
        for (size_t j = 1; j <= m; j++) {
            char p = pattern[j - 1];
            if (p == '*') dp[i][j] = dp[i - 1][j] || dp[i][j - 1];
            else if (p == '?') dp[i][j] = dp[i - 1][j - 1];
            else if (p == '[') {
                // find closing ']'
                size_t k = j;
                bool neg = false;
                if (k < m && (pattern[k] == '!' || pattern[k] == '^')) { neg = true; k++; }
                if (k < m && pattern[k] == ']') k++;
                while (k < m && pattern[k] != ']') k++;
                if (k >= m) { dp[i][j] = dp[i - 1][j - 1]; continue; }  // treat as literal
                char c = path[i - 1];
                bool inside = false;
                for (size_t t = (neg ? j + 1 : j); t < k; t++) {
                    if (t + 2 < k && pattern[t + 1] == '-') {
                        if (c >= pattern[t] && c <= pattern[t + 2]) { inside = true; t += 2; }
                    } else if (c == pattern[t]) inside = true;
                }
                if (inside == neg) inside = !inside;
                dp[i][j] = dp[i - 1][j - 1] && inside;
                // skip past ']' handled by loop increment (j advances by 1; acceptable for our patterns)
            } else if (p == path[i - 1]) {
                dp[i][j] = dp[i - 1][j - 1];
            }
        }
    }
    return dp[n][m];
}

// ------------------------------------------------------------------- policy
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

static toml::table json_to_toml(const json& j) {
    if (j.is_null()) return toml::table{};
    if (j.is_boolean()) return toml::table{{"", toml::node(j.get<bool>())}};
    if (j.is_number_integer()) return toml::table{{"", toml::node((int64_t)j.get<int64_t>())}};
    if (j.is_number_float()) return toml::table{{"", toml::node(j.get<double>())}};
    if (j.is_string()) return toml::table{{"", toml::node(j.get<std::string>())}};
    if (j.is_array()) {
        std::vector<toml::node> v;
        for (auto& e : j) v.push_back(json_to_toml(e));
        return toml::table{{"", toml::node(std::move(v))}};
    }
    if (j.is_object()) {
        std::map<std::string, toml::node> m;
        for (auto it = j.begin(); it != j.end(); ++it)
            if (!it.value().is_null()) m[it.key()] = json_to_toml(it.value());
        return toml::table(std::move(m));
    }
    return toml::table{};
}

static json read_toml_file(const std::filesystem::path& p) {
    if (!std::filesystem::is_file(p)) return json::object();
    std::ifstream f(p);
    if (!f) return json::object();
    try {
        return toml_to_json(toml::parse(f));
    } catch (const std::exception&) {
        return json::object();
    }
}

static json merge_policy(const json& a, const json& b) {
    json out = a;
    for (auto it = b.begin(); it != b.end(); ++it) {
        if (it.value().is_object()) {
            if (!out.contains(it.key()) || !out[it.key()].is_object()) out[it.key()] = it.value();
            for (auto jt = it.value().begin(); jt != it.value().end(); ++jt) {
                if (jt.value().is_array() && out[it.key()].contains(jt.key()) &&
                    out[it.key()][jt.key()].is_array()) {
                    for (auto& x : jt.value())
                        if (std::find(out[it.key()][jt.key()].begin(), out[it.key()][jt.key()].end(), x) ==
                            out[it.key()][jt.key()].end())
                            out[it.key()][jt.key()].push_back(x);
                } else {
                    out[it.key()][jt.key()] = jt.value();
                }
            }
        } else {
            out[it.key()] = it.value();
        }
    }
    return out;
}

static const char* APPROVAL_TEMPLATE = R"(# Approval policy for the coding agent.
#
# Decision order:  deny  >  allow  >  default (ask the user).
# Rules for commands are token prefixes: "cmake --build" matches "cmake --build build -j8".
# Compound commands (&&, ||, ;, |) are split; EVERY part must be allowed.
# Commands using $(...), backticks, heredocs or output redirection always ask.
# Rules for files are globs on the project-relative path ('*' also matches '/').
#
# This file is the user-wide policy. A project can add rules in <project>/.agent/approval.toml
# (lists are merged, scalars are overridden).

auto_approve_all = false   # true: run everything not denied without asking (use with care)

[commands]
default = "ask"            # ask | deny
allow = [
    "ls", "pwd", "cd", "cat", "head", "tail", "wc", "echo", "which", "tree", "diff", "stat", "file",
    "grep", "rg", "sort", "uniq",
    "git status", "git diff", "git log", "git show", "git branch", "git rev-parse",
    "cmake --version", "cmake -S", "cmake -B", "cmake --build", "ctest",
    "make", "ninja",
    "g++ --version", "clang++ --version",
    "node --version", "npm run build", "npm test", "npx tsc",
    "python --version", "python3 --version",
]
allow_regex = []
deny = [
    "sudo", "su", "mkfs", "dd", "shutdown", "reboot", "poweroff",
    "rm -rf /", "rm -rf ~", "rm -rf *", "chmod -R 777",
]

[files]
default = "ask"            # ask | deny
allow = ["build/*", "tmp/*"]
deny = [".git/*"]
)";

static std::filesystem::path ensure_user_policy() {
    std::filesystem::path p = user_dir() / "approval.toml";
    if (!std::filesystem::exists(p)) {
        std::filesystem::create_directories(p.parent_path());
        std::ofstream f(p, std::ios::binary);
        f << APPROVAL_TEMPLATE;
    }
    return p;
}

ApprovalPolicy ApprovalPolicy::load(const std::filesystem::path& root) {
    ApprovalPolicy p;
    p.user_file = ensure_user_policy();
    if (!root.empty()) p.project_file = project_cache_dir(root) / "approval.toml";
    p.data = merge_policy(read_toml_file(p.user_file), read_toml_file(p.project_file));
    return p;
}

void ApprovalPolicy::reload() {
    data = merge_policy(read_toml_file(user_file), read_toml_file(project_file));
}

static const json& section(const json& data, const std::string& name) {
    static json empty;
    if (data.contains(name) && data[name].is_object()) return data[name];
    return empty;
}

Decision ApprovalPolicy::check_command(const std::string& command) const {
    const json& cfg = section(data, "commands");
    auto [segs, unsafe] = split_command(command);
    if (segs.empty()) return {"deny", "empty command"};
    std::vector<std::pair<std::string, std::optional<std::vector<std::string>>>> parsed;
    for (auto& s : segs) parsed.emplace_back(s, shlex_tokens(s));

    for (auto& [s, toks] : parsed)
        if (toks)
            for (auto& rule : cfg.value("deny", json::array()))
                if (!toks->empty() && prefix_match(*toks, rule.get<std::string>()))
                    return {"deny", "matches deny rule '" + rule.get<std::string>() + "'"};
    if (!unsafe.empty()) return {"ask", "contains " + unsafe};
    if (data.value("auto_approve_all", false)) return {"allow", "auto_approve_all"};

    for (auto& [s, toks] : parsed) {
        if (!toks || toks->empty()) continue;
        bool ok = false;
        for (auto& r : cfg.value("allow", json::array()))
            if (prefix_match(*toks, r.get<std::string>())) ok = true;
        if (!ok)
            for (auto& r : cfg.value("allow_regex", json::array())) {
                try {
                    if (std::regex_match(s, std::regex(r.get<std::string>()))) { ok = true; break; }
                } catch (const std::exception&) {}
            }
        if (!ok) {
            std::string first = toks->front();
            std::string def = section(data, "commands").value("default", std::string("ask"));
            return {def == "deny" ? "deny" : "ask",
                    "'" + first + "' is not in the allow list"};
        }
    }
    return {"allow", "all parts match allow rules"};
}

Decision ApprovalPolicy::check_write(const std::string& rel_path) const {
    const json& cfg = section(data, "files");
    std::string p = rel_path;
    for (auto& c : p) if (c == '\\') c = '/';
    for (auto& g : cfg.value("deny", json::array()))
        if (path_glob_match(p, g.get<std::string>()))
            return {"deny", "matches deny pattern '" + g.get<std::string>() + "'"};
    if (data.value("auto_approve_all", false)) return {"allow", "auto_approve_all"};
    for (auto& g : cfg.value("allow", json::array()))
        if (path_glob_match(p, g.get<std::string>()))
            return {"allow", "matches allow pattern '" + g.get<std::string>() + "'"};
    std::string def = section(data, "files").value("default", std::string("ask"));
    return {def == "deny" ? "deny" : "ask", "no files rule matched"};
}

std::string ApprovalPolicy::suggest_rule(const std::string& kind, const json& args) {
    if (kind == "command") {
        auto [segs, _] = split_command(args.value("command", std::string()));
        auto toks = segs.empty() ? std::vector<std::string>{} : shlex_tokens(segs.back()).value_or(std::vector<std::string>{});
        if (toks.size() >= 2 && toks[1].rfind("-", 0) != 0)
            return toks[0] + " " + toks[1];
        return toks.empty() ? "" : toks[0];
    }
    std::string path = args.value("path", std::string());
    auto slash = path.find_last_of('/');
    return slash != std::string::npos ? path.substr(0, slash) + "/*" : path;
}

std::optional<std::filesystem::path> ApprovalPolicy::remember(const std::string& kind,
                                                              const std::string& rule,
                                                              const std::string& scope) {
    std::string r = rule;
    auto a = r.find_first_not_of(" \t");
    if (a == std::string::npos) return std::nullopt;
    r = r.substr(a);
    auto b = r.find_last_not_of(" \t");
    r = r.substr(0, b + 1);
    std::filesystem::path file;
    if (scope == "project" && !project_file.empty()) file = project_file;
    else file = user_file;
    if (r.empty() || file.empty()) return std::nullopt;
    json raw = read_toml_file(file);
    json& sec = raw[kind == "command" ? "commands" : "files"];
    if (!sec.is_object()) sec = json::object();
    json& lst = sec["allow"];
    if (!lst.is_array()) lst = json::array();
    bool found = false;
    for (auto& x : lst) if (x.is_string() && x.get<std::string>() == r) found = true;
    if (!found) lst.push_back(r);
    std::filesystem::create_directories(file.parent_path());
    std::ofstream f(file, std::ios::binary);
    auto node = json_to_toml(raw);
    f << toml::toml_formatter{node};
    reload();
    return file;
}

} // namespace ca
