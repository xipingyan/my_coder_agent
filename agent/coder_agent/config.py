"""Application configuration (pydantic models + layered TOML loading).

Layers, later wins:
    built-in defaults  <  ~/.coder_agent/config.toml  <  <project>/.agent/config.toml
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .paths import project_cache_dir, user_dir
from .util import dump_toml


class ServerConfig(BaseModel):
    provider: str = "openai_compat"  # openai_compat | ollama (| ovms later)
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "EMPTY"
    model: str = "qwen3.8-27b"
    timeout: float = 900.0  # seconds between streamed chunks
    connect_timeout: float = 10.0
    bypass_proxy: bool = True  # ignore HTTP(S)_PROXY env (LAN servers)


class ThinkingConfig(BaseModel):
    """How "thinking" is requested. Servers differ, so every knob is optional."""

    effort: Literal["default", "none", "low", "medium", "high"] = "medium"
    send_effort: bool = True  # send `reasoning_effort` (skipped when effort == "default")
    enable_thinking: Optional[bool] = None  # chat_template_kwargs.enable_thinking
    token_budget: Optional[int] = None  # extra_body.thinking_token_budget (vLLM)
    max_think_chars: int = 0  # >0: abort overlong thinking and retry with thinking off
    extra_body: dict[str, Any] = Field(default_factory=dict)  # merged into request body


class SamplingConfig(BaseModel):
    temperature: Optional[float] = 0.6
    top_p: Optional[float] = 0.95
    max_tokens: Optional[int] = None


class ContextConfig(BaseModel):
    max_tokens: int = 131072  # server-side cache / context length (128k)
    compress_threshold: float = 0.95  # compress when usage >= this ratio
    compress_target: float = 0.60  # ... down to this ratio
    algorithm: str = "summarize"
    keep_recent_units: int = 10  # newest message units never summarised
    tool_output_max_chars: int = 12000  # cap for a single tool result


class AgentConfig(BaseModel):
    max_steps: int = 40
    parallelism: int = 1  # concurrent sub-agents (1 for single-GPU Ollama)
    loop_limit: int = 3  # identical tool calls tolerated before intervening


class OrchestratorConfig(BaseModel):
    max_subtasks: int = 8  # planner is told to stay below this
    subtask_max_steps: int = 20  # agent steps allowed per subtask
    planner_thinking: bool = False  # let the planner think (slower)
    doc_analysis: bool = True  # summarise relevant MODULE.md before each subtask
    confirm_each_stage: bool = True  # pause for the user after every stage


class PathsConfig(BaseModel):
    project_root: str = ""
    prompt_dirs: list[str] = Field(default_factory=list)  # extra task-prefix dirs


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)


# ------------------------------------------------------------------ loading
def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def user_config_path() -> Path:
    return user_dir() / "config.toml"


def project_config_path(root: Path) -> Path:
    return project_cache_dir(root) / "config.toml"


def load_config(project_root: Optional[Path] = None) -> AppConfig:
    data = _read_toml(user_config_path())
    root = project_root
    if root is None and data.get("paths", {}).get("project_root"):
        root = Path(data["paths"]["project_root"])
    if root:
        data = _deep_merge(data, _read_toml(project_config_path(Path(root))))
        data = _deep_merge(data, {"paths": {"project_root": str(root)}})
    return AppConfig.model_validate(data)


def save_config(cfg: AppConfig, path: Optional[Path] = None) -> Path:
    path = path or user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(cfg.model_dump(mode="json")), encoding="utf-8")
    return path
