from coder_agent.context import ContextManager, TokenCounter, available_strategies, register_strategy
from coder_agent.context.compression import CompressionStrategy, split_units
from coder_agent.config import ContextConfig


def _conversation(n_units=30, tool_chars=3000):
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "build a thing"}]
    for i in range(n_units):
        msgs.append({"role": "assistant", "content": f"step {i}", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_chars})
    return msgs


def _valid(msgs):
    """every tool message must directly follow an assistant with its tool_call (or another tool)."""
    ids = set()
    for m in msgs:
        if m["role"] == "assistant":
            ids = {c["id"] for c in m.get("tool_calls", [])}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in ids
        else:
            ids = set()
    return True


def test_no_compression_below_threshold():
    cm = ContextManager(ContextConfig(max_tokens=1_000_000))
    assert not cm.needs_compression(_conversation(5))


def test_trigger_at_95_percent():
    msgs = _conversation()
    cfg = ContextConfig(max_tokens=1000)
    assert ContextManager(cfg).needs_compression(msgs)
    n = ContextManager(cfg).tokens(msgs)
    assert not ContextManager(ContextConfig(max_tokens=int(n / 0.9))).needs_compression(msgs)
    assert ContextManager(ContextConfig(max_tokens=int(n / 0.96))).needs_compression(msgs)


def test_summarize_replaces_old_and_keeps_recent():
    msgs = _conversation(30)
    cm = ContextManager(ContextConfig(max_tokens=8000, keep_recent_units=4))
    new, info = cm.compress(msgs, summarizer=lambda m: "GOAL: build a thing. Did steps.")
    assert new[0]["content"] == "SYS"
    assert any("GOAL: build a thing" in str(m["content"]) for m in new)
    assert new[-1] == msgs[-1]
    assert info.after < info.before and info.after <= 8000 * 0.6 + 50
    assert _valid(new)


def test_fallback_when_summarizer_fails():
    def boom(_):
        raise RuntimeError("server down")

    cm = ContextManager(ContextConfig(max_tokens=6000, keep_recent_units=4))
    new, info = cm.compress(_conversation(30), summarizer=boom)
    assert info.after < info.before and _valid(new)


def test_truncate_algorithm_and_custom_registration():
    assert {"summarize", "truncate"} <= set(available_strategies())

    class KeepLast(CompressionStrategy):
        name = "keep_last"

        def compress(self, messages, target_tokens, ctx):
            return [messages[0], messages[-1]]

    register_strategy(KeepLast())
    cm = ContextManager(ContextConfig(max_tokens=1000, algorithm="keep_last"))
    new, _ = cm.compress(_conversation(5))
    assert len(new) == 2


def test_units_group_tool_results():
    units = split_units(_conversation(2)[1:])
    assert [len(u) for u in units] == [1, 2, 2]


def test_calibration_scales_estimate():
    c = TokenCounter()
    msgs = _conversation(3)
    raw = c.count_raw(msgs)
    c.calibrate(raw, raw * 2)
    assert abs(c.count(msgs) - raw * 2) <= 2
