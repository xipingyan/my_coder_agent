from coder_agent.llm.think_parser import ThinkParser
from coder_agent.llm.toolparse import extract_text_tool_calls


def _collect(parser, pieces):
    out = []
    for p in pieces:
        out += parser.feed(p)
    out += parser.flush()
    merged = []
    for k, t in out:
        if merged and merged[-1][0] == k:
            merged[-1] = (k, merged[-1][1] + t)
        else:
            merged.append((k, t))
    return merged


def test_plain_content():
    assert _collect(ThinkParser(), ["hello ", "world"]) == [("content", "hello world")]


def test_think_split_across_chunks():
    pieces = ["<th", "ink>reason", "ing</thi", "nk>answer"]
    assert _collect(ThinkParser(), pieces) == [("thinking", "reasoning"), ("content", "answer")]


def test_partial_tag_that_is_not_a_tag():
    assert _collect(ThinkParser(), ["a <th", "ing b"]) == [("content", "a <thing b")]


def test_start_in_think():
    assert _collect(ThinkParser(start_in_think=True), ["abc</think>def"]) == [("thinking", "abc"), ("content", "def")]


def test_toolcall_hermes_json():
    text = 'ok\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.txt"}}\n</tool_call>'
    clean, calls = extract_text_tool_calls(text, {"read_file"})
    assert clean == "ok"
    assert calls[0].name == "read_file" and '"a.txt"' in calls[0].arguments


def test_toolcall_qwen_xml():
    text = "<tool_call><function=run_command><parameter=command>\nls -la\n</parameter></function></tool_call>"
    _, calls = extract_text_tool_calls(text, {"run_command"})
    assert calls and calls[0].name == "run_command" and "ls -la" in calls[0].arguments


def test_toolcall_fenced_json_and_unknown_filtered():
    text = '```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```'
    assert extract_text_tool_calls(text, {"list_dir"})[1]
    assert extract_text_tool_calls(text, {"other"})[1] == []


def test_no_toolcall_in_normal_text():
    assert extract_text_tool_calls("just text", {"a"}) == ("just text", [])
