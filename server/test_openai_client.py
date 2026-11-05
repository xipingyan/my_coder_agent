import argparse
import time
from pathlib import Path

from openai import OpenAI

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

thinking_header_printed = False
context_header_printed = False
in_think_tag = False
buffer = ""
thinking_text = ""
context_text = ""


# streamer output context
def print_context_header():
    global context_header_printed
    if not context_header_printed:
        print("\n\n【 💬 Context 回答内容 】")
        context_header_printed = True


# streamer output thinking
def print_thinking_header():
    global thinking_header_printed
    if not thinking_header_printed:
        print("【 🧠 Thinking 思考过程 】")
        thinking_header_printed = True


def emit_thinking(text):
    global thinking_text
    if text:
        print_thinking_header()
        thinking_text += text
        print(text, end="", flush=True)


def emit_context(text):
    global context_text
    if text:
        print_context_header()
        context_text += text
        print(text, end="", flush=True)


def partial_tag_len(text, tag):
    """text 末尾可能是 tag 前缀的最大长度（用于跨 chunk 的标签拆分）。"""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


def process_content(text, final=False):
    """方式二：从 content 中解析 <think>...</think> 标签，分别输出 thinking / context。"""
    global buffer, in_think_tag
    buffer += text
    while buffer:
        tag = THINK_CLOSE if in_think_tag else THINK_OPEN
        emit = emit_thinking if in_think_tag else emit_context
        idx = buffer.find(tag)
        if idx != -1:
            emit(buffer[:idx])
            buffer = buffer[idx + len(tag):]
            in_think_tag = not in_think_tag
            continue
        keep = 0 if final else partial_tag_len(buffer, tag)
        emit(buffer[:len(buffer) - keep])
        buffer = buffer[len(buffer) - keep:]
        break


def load_prompt(prompt: str) -> str:
    """prompt 是 .txt 文件路径时读取内容，否则按原文使用。"""
    path = Path(prompt).expanduser()
    if path.suffix.lower() != ".txt":
        return prompt
    if not path.is_file():
        raise FileNotFoundError(f"prompt 文件不存在: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"prompt 文件为空: {path}")
    return text


def parse_args():
    parser = argparse.ArgumentParser(description="流式测试 thinking / context 输出")
    parser.add_argument("--base-url", default="http://10.112.106.102:8000/v1")
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument(
        "--prompt",
        default="请用 100 字左右解释一下什么是张量并行。",
        help="提示词文本，或 .txt 文件路径（存在则读取文件内容）",
    )
    parser.add_argument("--no-think", action="store_true",
                        help="关闭 thinking (chat_template_kwargs.enable_thinking=False)")
    parser.add_argument("--effort", choices=["none", "low", "medium", "high"],
                        help="reasoning_effort (取决于模型/服务端是否支持)")
    parser.add_argument("--budget", type=int,
                        help="thinking_token_budget (需要 vLLM 版本支持)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    prompt = load_prompt(args.prompt)

    client = OpenAI(
        base_url=args.base_url,
        api_key="EMPTY"
    )

    extra_body = {}
    request_kwargs = {}
    if args.no_think:
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    if args.effort:
        request_kwargs["reasoning_effort"] = args.effort
    if args.budget is not None:
        extra_body["thinking_token_budget"] = args.budget
    if extra_body:
        request_kwargs["extra_body"] = extra_body

    start_time = time.time()
    first_token_time = None

    stream = client.chat.completions.create(
        model=args.model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        **request_kwargs,
    )

    print("=== 开始接收流式响应 ===\n")

    first_thinking_time = None
    first_context_time = None

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # ---------------------------------------------------------
        # 方式一：处理专用字段 (reasoning_content / reasoning)
        # ---------------------------------------------------------
        reasoning_delta = (
            getattr(delta, "reasoning_content", None)
            or getattr(delta, "reasoning", None)
        )
        if reasoning_delta:
            now = time.time()
            first_token_time = first_token_time or now
            first_thinking_time = first_thinking_time or now
            emit_thinking(reasoning_delta)

        # ---------------------------------------------------------
        # 方式二：content 中夹带 <think>...</think> 标签
        # ---------------------------------------------------------
        content_delta = getattr(delta, "content", None)
        if content_delta:
            now = time.time()
            first_token_time = first_token_time or now
            process_content(content_delta)
            if context_text and first_context_time is None:
                first_context_time = now

    process_content("", final=True)
    end_time = time.time()

    print("\n\n=== 统计 ===")
    if first_token_time is not None:
        print(f"首字/首包延迟 TTFT: {(first_token_time - start_time) * 1000:.2f} ms")
    if first_thinking_time is not None and first_context_time is not None:
        print(f"思考耗时: {(first_context_time - first_thinking_time) * 1000:.2f} ms")
    print(f"Thinking 字数: {len(thinking_text)}")
    print(f"Context 字数: {len(context_text)}")
    print(f"总耗时: {(end_time - start_time) * 1000:.2f} ms")