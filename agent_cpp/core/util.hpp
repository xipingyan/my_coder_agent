#pragma once
// Small shared helpers: JSON text helpers, front-matter parser, text truncation.

#include <string>
#include <vector>
#include "nlohmann/json.hpp"

namespace ca {

// JSON string escaping (for embedding a string in a JSON document)
std::string json_escape(const std::string& s);

// Parse a leading '---' block of 'key: value' lines. Returns {meta, body};
// meta empty if no front matter.
struct FrontMatter {
    std::vector<std::pair<std::string, std::string>> meta;
    std::string body;
};
FrontMatter parse_front_matter(const std::string& text);

// Keep head (60%) and tail (40%), replace the middle with an omission marker.
std::string truncate_middle(const std::string& text, size_t max_chars,
                            const std::string& marker = "\n...[{n} chars omitted]...\n");

// YAML <-> JSON bridge (vendored yaml-cpp; nulls dropped on dump)
nlohmann::json yaml_to_json(const std::string& text);
std::string json_to_yaml(const nlohmann::json& j);

// "a  b\n c" -> "a b c"
std::string collapse_ws(const std::string& s);
// Shorten to n chars (by UTF-8-safe truncation is NOT guaranteed; n is in chars)
std::string shorten(const std::string& text, size_t n);

} // namespace ca
