#pragma once
// Well-known locations.
// * user dir      (~/.coder_agent): config shared by every project
// * project cache (<root>/.agent): sessions, snapshots, project-level overrides
// * <root>/.github: instructions / skills that belong to the repository

#include <filesystem>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace ca {

// $HOME/.coder_agent, overridable via CODER_AGENT_HOME (~ expansion supported)
inline fs::path user_dir() {
    if (const char* env = std::getenv("CODER_AGENT_HOME")) {
        fs::path p(env);
        if (p.is_relative() && p.string().front() == '~') {
            const char* home = std::getenv("HOME");
            p = fs::path(home ? home : "/root") / p.string().substr(2);
        }
        return p;
    }
    const char* home = std::getenv("HOME");
    return fs::path(home ? home : "/root") / ".coder_agent";
}

inline fs::path project_cache_dir(const fs::path& root) { return root / ".agent"; }
inline fs::path github_dir(const fs::path& root) { return root / ".github"; }

// Skills shipped with the binary (set at build time via -DBUILTIN_SKILLS_DIR)
inline fs::path builtin_skills_dir() {
#ifdef BUILTIN_SKILLS_DIR
    return fs::path(BUILTIN_SKILLS_DIR);
#else
    return fs::path();
#endif
}

// Directories outside the project that tools may READ (never write)
inline std::vector<fs::path> agent_read_roots() {
    std::vector<fs::path> v;
    fs::path b = builtin_skills_dir();
    if (!b.empty()) v.push_back(b);
    v.push_back(user_dir() / "skills");
    return v;
}

} // namespace ca
