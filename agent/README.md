# Coder Agent

面向本地大模型（Ollama / vLLM / OVMS 等任何 OpenAI 兼容服务）的编程 Agent，带 GUI 和命令行。
针对"本地模型慢、能力有限"做了专门设计：任务拆小、每步可见、危险操作需确认、思考可限制、可回退。

## 功能一览

| 功能 | 说明 |
|---|---|
| 单 agent 模式 | 一个 prompt 一个 agent，循环调用工具（读写文件、搜索、执行命令）直到完成 |
| 计划模式 | planner 把目标拆成小的子任务（DAG），每个子任务由独立 agent 完成；用户先审阅/编辑计划，**每个阶段完成后确认再继续** |
| 任务回退 | 历史列表里可"编辑某条 prompt 重跑"或"回退到此处"；文件改动用自己的副本恢复（不依赖 git） |
| 审批 | `approval.toml` 配置命令/文件的自动放行与拒绝规则，其余弹窗让人确认（可一键"记住规则"） |
| 任务类型前缀 | 按任务类型（C++ 实现、调试、CMake、TypeScript UI、重构、文档、评审…）给 prompt 加前缀，可自动识别、可手选、可自行修改 |
| 上下文压缩 | 上下文长度可配（默认 128k），占用达 95% 自动压缩到约 60%；压缩算法可插拔 |
| 知识库 | `.github/` 的 instructions / skills、每个子模块的 `MODULE.md`、agent 自己的记忆（`remember` 工具） |
| 调试纪律 | 内置 `debug-cpp` skill：先列疑点、逐个验证、两次不行就用 gdb；命令连续失败时自动提醒；`debug_backtrace` 工具封装 gdb batch 取回溯 |
| 文档分析 | 子任务开始前，先读取相关 `MODULE.md` 并生成精简摘要给子 agent，同时显示在 "Docs read" 窗口 |
| 活动可见 | Activity 窗口按时间线显示所有 agent 的动作；状态栏显示当前状态/耗时/上下文占用/tok/s |
| thinking 控制 | `reasoning_effort`、`enable_thinking`、`thinking_token_budget`、思考字数上限（超限自动关闭思考重试） |
| MCP | 暂未实现（工具注册表已预留接口） |

## 环境要求

- Python >= 3.11（开发时使用 3.14）
- 可访问的 OpenAI 兼容服务（例如 Ollama 的 `http://<ip>:11434/v1`，或 vLLM 的 `http://<ip>:8000/v1`）
- Linux 桌面运行 GUI 需要 Qt 的 xcb 依赖；Ubuntu 上如果提示 `xcb-cursor` 缺失：`sudo apt install libxcb-cursor0`
- 无图形界面的机器可以只用命令行版本（`coder-agent-cli`）

## 安装

```bash
cd agent
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt     # 只装运行依赖
pip install -e .                    # 安装本项目，得到 coder-agent / coder-agent-cli 命令

# 开发者（测试 + 打包工具）
pip install -r requirements-dev.txt
```

依赖：`openai`、`pydantic`、`PySide6`（见 [requirements.txt](requirements.txt)）。

## 运行

### GUI

```bash
coder-agent                          # 首次会让你选择工程目录，之后记住上次的工程
coder-agent --root /path/to/project  # 指定工程目录
python -m coder_agent.gui.app        # 不安装命令时的等价写法
```

第一次使用：

1. 菜单 `File > Settings...`（`Ctrl+,`），在 **Model** 页填写 `base_url`、`model`，点 "Test connection / list models" 确认连通。
   模型名要和服务端 `/v1/models` 返回的一致（Ollama 通常带 `:latest`，例如 `qwen3.8-27b:latest`）。
2. 在 **Context & agent** 页把 `max_tokens` 设成服务端实际的上下文长度（Ollama 的 `num_ctx`），压缩是按这个值计算的。
3. 点 Save。设置保存到 `~/.coder_agent/config.toml`。

界面说明：

- 左侧两个标签页：
  - **Turns**：当前会话的每一轮。右键可 "编辑 prompt 重跑" / "回退到此处（删除后续 或 保留为分支）"。
  - **Sessions**：本工程的历史会话（按时间倒序，● 表示当前会话，悬停显示轮数和状态）。双击打开，右键可删除；`New session` 按钮新建会话。
    会话在每轮开始时就保存，程序意外退出后重新打开，未完成的那一轮显示为 `interrupted`。
    切换/新建会话时右侧的 Activity、Plan、Thinking、Commands、Docs read 都会清空（这些是实时日志，不随会话保存；对话和各轮记录会恢复）。
- 中间：对话区 + 输入框（`Ctrl+Enter` 发送）。
- 右侧标签页：
  - **Activity**：所有 agent 的动作时间线（工具调用、审批结果、耗时、压缩…）
  - **Plan**：计划模式下各阶段、各子任务的状态
  - **Thinking**：模型的思考内容（对话区不显示，避免刷屏）
  - **Commands**：命令的实时输出
  - **Docs read**：agent 读取/分析的文档及摘要
- 工具栏：Task type（`(auto)` 自动识别）、Thinking effort、Mode（单 agent / 计划）、Stop。
- 底部状态栏：当前状态和已耗时（例如 `Waiting for model: step 3 (35s) thinking 1200 chars`）、首字延迟和 tok/s，以及上下文长度：
  - `context 12.3k / 128.0k (9%)`：**当前 cache 长度 / 配置的最大值**。没有 `~` 表示是服务端对上一次请求实际报告的 token 数（prompt + 已生成），带 `~` 表示本地估算（例如刚打开历史会话时）。悬停可看精确数字和压缩触发点（95%）。
  - `server cache 128.0k | model max 256.0k`：**服务端实际运行的 cache 长度 / 模型本身支持的最大长度**（Ollama 读 `/api/ps` 和 `/api/show`，vLLM 读 `/v1/models` 的 `max_model_len`）。
    打开工程、保存设置、每轮结束时自动刷新，点击可手动刷新。如果配置的 `context.max_tokens` 与服务端不一致会变色（橙色：配置更小，红色：配置更大，压缩会触发得太晚），
    `Settings > Context & agent > Detect...` 可一键把配置改成服务端的值。命令行 `coder-agent-cli --check` 也会打印这两个数。

### 计划模式（拆子任务）

工具栏 Mode 选 **Plan & subtasks**，输入目标后：

1. planner 生成子任务列表，弹出 **Review plan** 对话框：可修改 goal、依赖、类型，增删子任务，然后 "Run plan"。
2. 按依赖分成若干阶段（stage）依次执行；同一阶段里无依赖的子任务可并行，并行度由 `agent.parallelism` 控制（单卡 Ollama 保持 1）。
3. 每个阶段结束弹出确认框：**继续 / 重试失败项 / 停止**（`orchestrator.confirm_each_stage = false` 可关闭）。
4. 整个计划是会话里的一个 Turn，同样可以回退，文件会整体恢复。

### 命令行

```bash
coder-agent-cli --root /path/to/project --check                         # 测试服务连通并列出模型
coder-agent-cli --root /path/to/project --prompt "给 foo 模块加一个 bar()"  # 单次运行
coder-agent-cli --root /path/to/project --plan --prompt "..."            # 计划模式
coder-agent-cli --root /path/to/project                                  # 交互模式
```

常用参数：`--base-url`、`--model`、`--effort {default,none,low,medium,high}`、`--task-type`、`--show-thinking`。
交互模式命令：`/plan <目标>`、`/turns`、`/rewind <序号>`、`/exit`。审批时输入 `y` 允许、`n` 拒绝、`a` 允许并记住规则，其他文字作为反馈发给模型。
并行压力测试见下文"并行子 agent 压力测试"（`coder-agent-stress`）。

## 配置

### 文件位置

| 位置 | 内容 |
|---|---|
| `~/.coder_agent/config.toml` | 全局配置（服务、thinking、上下文…） |
| `~/.coder_agent/approval.toml` | 全局审批规则（首次运行自动生成，带注释的模板） |
| `~/.coder_agent/prompts/*.md` | 覆盖/新增任务类型前缀；`base.md` 覆盖基础 system prompt |
| `~/.coder_agent/skills/*/SKILL.md` | agent 共享的 skills（覆盖同名内置 skill） |
| `~/.coder_agent/memory/global.md` | agent 的全局记忆 |
| `<工程>/.agent/config.toml` | 工程级配置，覆盖全局 |
| `<工程>/.agent/approval.toml` | 工程级审批规则（列表与全局合并） |
| `<工程>/.agent/prompts/*.md` | 工程级任务前缀 |
| `<工程>/.agent/memory.md` | agent 对本工程的记忆 |
| `<工程>/.agent/sessions/`、`snapshots/`、`doc_cache.json` | 会话、文件快照、文档摘要缓存（建议把 `.agent/` 加入工程的 `.gitignore`，`config.toml` / `approval.toml` / `prompts/` 想共享的话可单独提交） |
| `<工程>/.github/copilot-instructions.md` | 工程指令（始终加入 prompt） |
| `<工程>/.github/instructions/*.instructions.md` | 带 `applyTo` 的指令（`applyTo: "**"` 的直接加入，其余只放索引，按需读取） |
| `<工程>/.github/skills/*/SKILL.md` | 工程 skills（只放索引，按需读取） |
| 各模块目录下的 `MODULE.md` | 模块功能描述（索引进 prompt，计划模式下自动分析） |

环境变量 `CODER_AGENT_HOME` 可以改变 `~/.coder_agent` 的位置。

### config.toml 示例

```toml
[server]
provider = "openai_compat"          # openai_compat | ollama（ovms 暂未支持）
base_url = "http://10.112.106.102:8000/v1"
api_key = "EMPTY"
model = "qwen3.8-27b:latest"
timeout = 900.0                     # 两个流式数据块之间的最长等待（秒）
bypass_proxy = true                 # 局域网服务：忽略 HTTP(S)_PROXY

[thinking]
effort = "low"                      # default | none | low | medium | high
send_effort = true                  # 发送 reasoning_effort 字段
# enable_thinking = false           # 发送 chat_template_kwargs.enable_thinking
# token_budget = 2000               # 发送 thinking_token_budget（vLLM）
max_think_chars = 6000              # 思考超过该字符数则中止并关闭思考重试；0 = 不限制

[thinking.extra_body]               # 原样合并进请求体
# top_k = 20

[sampling]
temperature = 0.6
top_p = 0.95

[context]
max_tokens = 131072                 # 服务端上下文长度（128k）
compress_threshold = 0.95           # 占用达到 95% 时压缩
compress_target = 0.60              # 压缩到 60%
algorithm = "summarize"             # summarize | truncate | 自己注册的算法
keep_recent_units = 10              # 最近这些消息单元不会被压缩

[agent]
max_steps = 40
parallelism = 1                     # 并行子 agent 数；多卡时再调大
auto_skills = true                  # 命令失败时自动注入匹配 triggers 的 skill
failure_hint_after = 2              # 连续失败几次后提醒"先列疑点"（0 = 关闭）
gdb_hint_after = 4                  # 连续失败几次后提醒"改用调试器"（0 = 关闭）

[orchestrator]
max_subtasks = 8
subtask_max_steps = 20
planner_thinking = false
doc_analysis = true
confirm_each_stage = true
```

所有设置都能在 GUI 的 Settings 里修改；设置表单由配置模型自动生成，新增配置项会自动出现。

### 审批规则

`~/.coder_agent/approval.toml`（工程可在 `.agent/approval.toml` 追加）：

```toml
auto_approve_all = false            # true：除 deny 外全部自动放行

[commands]
default = "ask"                     # ask | deny
allow = ["cmake --build", "ctest", "git status", "make", "ninja"]   # 按词前缀匹配
allow_regex = []
deny = ["sudo", "rm -rf /", "mkfs"]

[files]
default = "ask"
allow = ["build/*", "tmp/*"]        # glob，'*' 也匹配 '/'
deny = [".git/*"]
```

规则顺序：`deny` > `allow` > `default`。复合命令（`&&`、`||`、`;`、`|`）会拆开，**每一段都必须被允许**；
含 `$(...)`、反引号、heredoc、输出重定向（`>`）的命令一律需要人工确认。

### 任务类型前缀

内置：`general`、`cpp_impl`、`cpp_debug`、`cmake`、`typescript_ui`、`refactor`、`doc`、`review`（只读工具）。
文件是带 front-matter 的 Markdown，放到 `~/.coder_agent/prompts/` 或 `<工程>/.agent/prompts/` 即可覆盖或新增，GUI Settings 的 "Task prompts" 页也能直接编辑：

```markdown
---
name: cpp_impl
description: Implement or extend C++ code
keywords: c++, class, 实现, 新增        # 自动识别用的关键词
globs: *.cpp,*.hpp                       # prompt 中提到这些文件时加分
effort: medium
tools: read_file, list_dir               # 可选：限制可用工具
---
Task type: C++ implementation.
- ...
```

### Skills（技能）与调试纪律

skill 是一份 Markdown 说明书，prompt 里只放一行索引（名称 + 描述 + 路径），模型需要时用 `read_file` 读取全文，所以 `description` 要写清楚"什么情况下用"。
三层来源，**同名时后者覆盖前者**：

| 层 | 位置 | 说明 |
|---|---|---|
| 内置 | `coder_agent/skills/<名字>/SKILL.md` | 随程序发布，目前有 `debug-cpp` |
| 全局（agent 自己的） | `~/.coder_agent/skills/<名字>/SKILL.md` | 所有工程共享，手工创建文件即可 |
| 工程 | `<工程>/.github/skills/<名字>/SKILL.md` | 只对这个工程生效 |

不需要重启，下一次提交就会读取。全局和内置 skill 位于工程之外，`read_file` 对这两个目录开放**只读**访问，其他工程外路径仍然禁止。

手动添加一个 skill：

```bash
mkdir -p ~/.coder_agent/skills/my-skill
cat > ~/.coder_agent/skills/my-skill/SKILL.md <<'EOF'
---
name: my-skill
description: 一句话说明什么时候该用它
triggers: error:|Segmentation fault     # 可选：命令失败且输出匹配这个正则时，自动把全文附到工具结果里
---
正文（建议 600 字以内，步骤化，小模型更容易照做）
EOF
```

`triggers` 是可选的正则（不区分大小写）：只有**失败的 `run_command`** 输出匹配时才会触发，每次运行同一个 skill 只注入一次，不依赖模型主动去读。

**调试纪律（`debug-cpp`）**

要让本地模型"不要无目的地尝试"，只靠一份说明书不够，所以分四层：

1. 内置 skill `debug-cpp`：读第一条错误 → 写出 2-3 个疑点（带证据和最小检查）→ 一次验证一个，每个最多 2 次，没用就回退 → 两个疑点失败、或崩溃/挂死/结果错误时用 gdb → 读回溯，找到第一个属于自己代码的栈帧。
2. 基础 prompt 和 `cpp_impl` / `cmake` / `cpp_debug` 前缀里都有一句"失败时先按 `debug-cpp` 做"。
3. 命令失败且输出匹配 `triggers` 时，自动把 skill 全文附到工具结果里。
4. agent 循环统计**连续失败**的 `run_command`：第 `failure_hint_after` 次（默认 2）追加"停止盲目修改，先列疑点"的提醒；第 `gdb_hint_after` 次（默认 4，之后每 2 次重复）追加"改用调试器"的提醒。`ls`、`cat`、`grep` 等不证明任何事的命令不会重置计数，真正成功的命令（构建、运行）会重置，使用 `debug_backtrace` 也会重置。

对应配置（`[agent]`）：`auto_skills = true`、`failure_hint_after = 2`、`gdb_hint_after = 4`，设为 0 即关闭对应提醒。

**`debug_backtrace` 工具**：模型自己拼 gdb 命令很容易出错（忘了 batch、没有 `-g`、卡在交互提示），这个工具统一处理：

- 参数：`program`（可执行文件）、`args`、`breakpoint`（`文件:行号` 或函数名，停下后显示参数和局部变量）、`timeout`、`cwd`。
- 崩溃：给出信号和调用栈、参数、局部变量；挂死：超时后中断并显示每个线程卡在哪里；正常退出：明确说"没有崩溃"并建议下断点。
- 程序没有调试信息时直接报错并提示 `cmake -DCMAKE_BUILD_TYPE=Debug`；没装 gdb 时提示安装，并给出 `-fsanitize=address,undefined` 的替代方法。
- 审批：它会**运行**程序，所以按"运行该程序"的命令（如 `./build/app`）走审批规则，和直接 `run_command` 一致；`gdb` 本身没有加入默认放行列表（gdb 可以执行任意命令）。

### 增加新的上下文压缩算法

```python
from coder_agent.context import CompressionStrategy, register_strategy

class MyStrategy(CompressionStrategy):
    name = "mine"
    def compress(self, messages, target_tokens, ctx):
        # ctx.count(msgs) 估算 token，ctx.summarizer(msgs) 可调用模型做摘要
        return messages[:1] + messages[-6:]

register_strategy(MyStrategy())      # 然后在配置里写 algorithm = "mine"
```

### 增加新的模型后端

在 `coder_agent/llm/__init__.py` 中 `register_provider("ovms", factory)`，实现 `LLMProvider.stream_chat()` 和 `list_models()` 即可。

## 项目结构

```
coder_agent/
  config.py  events.py  approval.py  knowledge.py  agent.py  engine.py  cli.py
  llm/            provider 抽象 + OpenAI 兼容实现 + <think> 解析 + 文本形式 tool call 兜底解析
  tools/          read_file / list_dir / find_files / grep_search / write_file / apply_patch / run_command / debug_backtrace / remember
  skills/         内置 skills（debug-cpp）
  context/        token 估算（可用服务端 usage 校准）+ 可插拔压缩算法
  prompts/        任务类型注册表 + system prompt 组装 + 内置前缀（defaults/*.md）
  session/        会话树、回退、文件快照
  orchestrator/   planner、文档分析、阶段调度
  gui/            PySide6 界面
  defaults/       approval.toml 模板
  stress.py       并行子 agent 压力测试（coder-agent-stress）
tests/            单元测试 + GUI 冒烟测试
packaging/        PyInstaller 入口脚本
```

core 与界面完全解耦：core 只通过 `EventBus` 发事件，GUI 和 CLI 都是订阅者。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q                              # 全部测试（GUI 测试自动使用 offscreen 平台）
QT_QPA_PLATFORM=offscreen python -m pytest -q    # 无显示环境明确指定时
```

### 并行子 agent 压力测试

并行度默认是 1（单卡 Ollama 不能真并行）。需要调大之前，先用压力测试确认服务端真的能并行、并且没有出错。

**1. 并发逻辑测试（不需要 server，包含在 pytest 里）**：[tests/test_parallel_stress.py](tests/test_parallel_stress.py) 用带延迟的假模型，在多线程下检查：

- 12 个子任务并行度 4 比串行快（<70% 耗时），实际并发不超过上限；
- 事件不丢失、文件快照清单完整，整个并行计划可以一次回退；
- 声明了同一个文件的子任务不会同时运行；
- 并行时审批弹窗一次只问一个；
- 个别子任务失败不会死锁，也不影响其他子任务；
- 并行运行中途取消能及时停止，已写的文件可回退；
- 30 个子任务混合写文件和执行 shell 命令后结果一致。

**2. 真实 server 压力测试**：

```bash
coder-agent-stress --base-url http://<ip>:11434/v1 --model qwen3.8-27b:latest \
    --effort low --subtasks 8 --parallelism 1,2,4 -v --report stress.json
```

每个并行度各建一个临时工程，执行相同的 N 个独立子任务（各写一个小头文件），并**校验文件内容**。输出对比表：

| 列 | 含义 |
|---|---|
| wall s / speedup | 总耗时 / 相对第一档的加速比 |
| done / failed / verified | 完成数 / 失败数 / 文件内容校验通过数 |
| max conc | 实际观察到的最大并发子 agent 数 |
| avg sub s | 单个子任务平均耗时 |
| avg / max ttft s | 首字延迟，反映服务端排队情况 |
| tok/s agg | 总生成吞吐（所有子 agent 合计） |
| errors / warn | 错误 / 警告事件数 |

末尾给出 `RESULT: healthy` 或具体问题（有失败、并发超限、超时、"没有加速"），出现问题时退出码为 1。其它参数：`--ctx`（压测时使用的 `context.max_tokens`）、`--max-steps`、`--timeout`、`--keep`（保留临时工程）。

服务端注意事项：

- **Ollama**：默认一次只处理一个请求，要真并行必须设置环境变量 `OLLAMA_NUM_PARALLEL=N`；
  并且 `num_ctx` 会**被 N 个槽位平分**，所以要把 `context.max_tokens` 改成 `num_ctx / N`，否则压缩触发得太晚会超出服务端上下文。
- **vLLM / OVMS**：连续批处理，通常无需额外设置，但并发越高单请求越慢，看 `avg ttft` 和 `tok/s agg` 找平衡点。
- 如果表中 `speedup` 约等于 1.0x，说明服务端把请求排队了，保持 `agent.parallelism = 1` 即可。

实测示例（qwen3.8-27b，单卡，未设置并行槽位，8 个子任务）：

```
par  wall s  speedup  done  failed  verified  max conc  avg sub s  avg ttft s  max ttft s  tok/s agg
1    136.72  1.00x    8     0       8         1         17.09      2.83        5.61        13.7
2    160.09  0.85x    8     0       8         2         39.00      10.47       13.89       12.4
4    157.77  0.87x    8     0       8         4         73.39      24.40       30.37       11.5
```

全部完成且校验通过，但总耗时没有变短，平均首字延迟随并发线性增长，总吞吐不变：服务端在串行处理请求，
并行只会让每个子任务变慢，这种环境下应保持 `agent.parallelism = 1`。

## 打包发布

### 方式一：wheel（推荐，体积小，目标机器需要 Python >= 3.11）

```bash
pip install build
python -m build --wheel --outdir dist          # 生成 dist/coder_agent-0.1.0-py3-none-any.whl
```

在目标机器上：

```bash
python -m venv ~/coder-agent-env
~/coder-agent-env/bin/pip install coder_agent-0.1.0-py3-none-any.whl    # 依赖会自动安装
~/coder-agent-env/bin/coder-agent                                       # 或 coder-agent-cli
```

离线机器：先在联网机器上 `pip download -r requirements.txt -d wheels/`，把 `wheels/` 和 whl 一起拷过去，
然后 `pip install --no-index --find-links wheels coder_agent-0.1.0-py3-none-any.whl`。

发布新版本：修改 [pyproject.toml](pyproject.toml) 中的 `version`，重新执行 build。
内置的任务前缀（`prompts/defaults/*.md`）和审批模板（`defaults/approval.toml`）已作为 package data 打进 wheel。

### 方式二：PyInstaller（独立可执行目录，目标机器无需 Python）

```bash
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --name coder-agent \
    --paths . --collect-data coder_agent \
    --distpath dist-app --workpath build-app --specpath build-app \
    packaging/launcher.py
```

产物在 `dist-app/coder-agent/`（约 230 MB），运行 `dist-app/coder-agent/coder-agent [--root 工程目录]`，
整个目录拷走即可。注意：

- 必须在目标操作系统和架构上构建（Linux 上构建的不能在 Windows 运行）。
- `--paths .` 是必须的：`pip install -e .` 的可编辑安装 PyInstaller 找不到，要么加这个参数，要么先装 wheel 再打包。
- 如果在 **conda 的 Python** 下打包，运行时报 `libcrypto.so.3: version OPENSSL_3.x not found`，
  构建时加上 `LD_LIBRARY_PATH=$CONDA_PREFIX/lib`（或改用 python.org / 系统 Python 构建）：
  `LD_LIBRARY_PATH=$CONDA_PREFIX/lib pyinstaller ...`
- 命令行版本如需打包，用同样方式再打一个入口为 `coder_agent.cli:main` 的启动脚本即可。

## 常见问题

- **连不上服务**：先 `coder-agent-cli --check --base-url ...`。局域网服务默认忽略代理环境变量（`bypass_proxy = true`）。
- **模型不会调用工具/命令不执行**：工具调用解析失败时会自动尝试解析正文里的 `<tool_call>` JSON、Qwen3-Coder 的 XML 格式、```json 块，并把错误反馈给模型重试；仍不稳定时降低 `temperature`、降低 thinking effort。
- **思考太久**：把 effort 调低，或设置 `max_think_chars`；状态栏会显示当前已思考的字符数。
- **上下文压缩过早/过晚**：确认 `context.max_tokens` 与服务端 `num_ctx` 一致。实际 token 数会用服务端返回的 `usage` 校准估算值。
- **回退后命令的副作用**：回退只恢复 agent 通过 `write_file` / `apply_patch` 改动的文件；`run_command` 产生的构建产物、安装的包等不会被撤销（界面会提示）。
