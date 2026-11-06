from coder_agent.config import AppConfig, load_config, save_config, user_config_path
from coder_agent.util import dump_toml, parse_front_matter, truncate_middle
import tomllib


def test_config_roundtrip(project):
    cfg = AppConfig()
    cfg.server.base_url = "http://10.0.0.2:8000/v1"
    cfg.thinking.effort = "low"
    cfg.thinking.extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    save_config(cfg)
    loaded = load_config()
    assert loaded.server.base_url == "http://10.0.0.2:8000/v1"
    assert loaded.thinking.effort == "low"
    assert loaded.thinking.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert loaded.context.max_tokens == 131072


def test_project_config_overrides_user(project):
    save_config(AppConfig())
    (project / ".agent").mkdir()
    (project / ".agent" / "config.toml").write_text('[server]\nmodel = "other"\n')
    cfg = load_config(project)
    assert cfg.server.model == "other"
    assert cfg.paths.project_root == str(project)


def test_dump_toml_valid():
    data = {"a": 1, "s": 'q"uote\n', "l": ["x", "y"], "t": {"n": None, "k": True, "sub": {"z": 1.5}}}
    parsed = tomllib.loads(dump_toml(data))
    assert parsed["s"] == 'q"uote\n' and parsed["t"] == {"k": True, "sub": {"z": 1.5}}


def test_front_matter_and_truncate():
    meta, body = parse_front_matter("---\nname: x\nkeywords: a, b\n---\nbody")
    assert meta == {"name": "x", "keywords": "a, b"} and body == "body"
    assert parse_front_matter("no meta")[0] == {}
    t = truncate_middle("a" * 1000, 100)
    assert len(t) < 200 and "omitted" in t
