#pragma once
// Event bus: decouples the core from any front-end (web server / tests).
//
// Event kinds emitted by the core:
//   status              state=waiting_model|running_tool|waiting_user|idle, detail=str
//   thinking_delta      text
//   content_delta       text
//   llm_done            usage={prompt,completion,total}, elapsed, ttft
//   tool_call           name, args (object), id
//   tool_result         name, ok (bool), text (truncated)
//   command_output      text
//   approval_requested  request (object)
//   approval_resolved   allow (bool), auto (bool), reason
//   context_usage       tokens, max_tokens, ratio, exact
//   context_compressed  before, after, algorithm
//   turn_started        turn_id
//   turn_finished       turn_id, status
//   agent_finished      status, steps
//   warning / error     text

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <vector>
#include <nlohmann/json.hpp>

namespace ca {

using Json = nlohmann::json;

struct Event {
    std::string kind;
    std::string agent_id = "main";
    Json data = Json::object();
    double ts = 0;
};

class EventBus {
public:
    using Subscriber = std::function<void(const Event&)>;

    // Returns an unsubscribe callable. Subscribers run on the emitting thread.
    std::function<void()> subscribe(Subscriber fn);

    void emit(const std::string& kind, const std::string& agent_id = "main", Json data = Json::object());

private:
    std::vector<std::pair<std::shared_ptr<int>, Subscriber>> subs_;
    std::mutex lock_;
    std::atomic<int> next_id_{1};
};

} // namespace ca
