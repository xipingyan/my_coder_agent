#include "util.hpp"
#include <cstdio>
#include <sstream>

namespace ca {

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20) {
                char buf[8];
                std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                out += buf;
            } else {
                out += static_cast<char>(c);
            }
        }
    }
    return out;
}

FrontMatter parse_front_matter(const std::string& text) {
    size_t pos = 0;
    if (text.size() >= 3 && text.compare(0, 3, "\xEF\xBB\xBF") == 0) pos = 3; // BOM
    std::string t = text.substr(pos);
    if (t.rfind("---", 0) != 0) return {{}, text};
    std::vector<std::string> lines;
    std::istringstream iss(t);
    std::string line;
    while (std::getline(iss, line)) lines.push_back(line);
    int end = -1;
    for (size_t i = 1; i < lines.size(); i++) {
        std::string s = lines[i];
        // trim
        auto a = s.find_first_not_of(" \t\r");
        if (a == std::string::npos || s.substr(a) == "---") { end = (int)i; break; }
    }
    if (end < 0) return {{}, text};
    FrontMatter fm;
    for (int i = 1; i < end; i++) {
        const std::string& l = lines[i];
        auto c = l.find(':');
        if (c == std::string::npos) continue;
        auto trim = [](std::string s) {
            auto a = s.find_first_not_of(" \t\r");
            auto b = s.find_last_not_of(" \t\r");
            return a == std::string::npos ? "" : s.substr(a, b - a + 1);
        };
        std::string k = trim(l.substr(0, c));
        std::string v = trim(l.substr(c + 1));
        if (!v.empty() && (v.front() == '"' || v.front() == '\''))
            v = v.substr(1, v.size() - 2);
        fm.meta.emplace_back(k, v);
    }
    std::string body;
    for (int i = end + 1; i < (int)lines.size(); i++) body += lines[i] + "\n";
    while (!body.empty() && body.front() == '\n') body.erase(body.begin());
    return fm;
}

std::string truncate_middle(const std::string& text, size_t max_chars, const std::string& marker) {
    if (max_chars == 0 || text.size() <= max_chars) return text;
    size_t head = max_chars * 6 / 10;
    size_t tail = max_chars - head;
    size_t omitted = text.size() - head - tail;
    std::string m = marker;
    auto p = m.find("{n}");
    if (p != std::string::npos) m.replace(p, 2, std::to_string(omitted));
    return text.substr(0, head) + m + text.substr(text.size() - tail);
}

std::string collapse_ws(const std::string& s) {
    std::string out;
    bool space = true;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            if (!space && !out.empty()) out += ' ';
            space = true;
        } else {
            out += c;
            space = false;
        }
    }
    while (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

std::string shorten(const std::string& text, size_t n) {
    std::string s = collapse_ws(text);
    if (s.size() <= n) return s;
    return s.substr(0, n - 1) + "\xE2\x80\xA6"; // …
}

} // namespace ca
