#include "events.hpp"
#include <chrono>

namespace ca {

std::function<void()> EventBus::subscribe(Subscriber fn) {
    auto id = std::make_shared<int>(next_id_++);
    {
        std::lock_guard<std::mutex> g(lock_);
        subs_.emplace_back(id, std::move(fn));
    }
    return [this, id]() {
        std::lock_guard<std::mutex> g(lock_);
        for (auto it = subs_.begin(); it != subs_.end(); ++it)
            if (it->first == id) { subs_.erase(it); return; }
    };
}

void EventBus::emit(const std::string& kind, const std::string& agent_id, Json data) {
    Event ev;
    ev.kind = kind;
    ev.agent_id = agent_id;
    ev.data = std::move(data);
    ev.ts = std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    std::vector<std::pair<std::shared_ptr<int>, Subscriber>> copy;
    {
        std::lock_guard<std::mutex> g(lock_);
        copy = subs_;
    }
    for (auto& [id, fn] : copy) fn(ev);
}

} // namespace ca
