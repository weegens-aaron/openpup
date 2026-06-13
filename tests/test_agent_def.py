"""Generated named-agent definition (hermes-style identity)."""

import json

from openpup import agent_def


def test_is_auto():
    assert agent_def.is_auto(None)
    assert agent_def.is_auto("")
    assert agent_def.is_auto("auto")
    assert agent_def.is_auto("  AUTO ")
    assert not agent_def.is_auto("code-puppy")


def test_slugify_basic():
    assert agent_def.slugify("Rex the Pup!") == "rex-the-pup"
    assert agent_def.slugify("OpenPup") == "openpup"


def test_slugify_never_empty_or_reserved():
    assert agent_def.slugify("!!!") == "openpup"
    # Must not shadow a built-in code-puppy agent.
    assert agent_def.slugify("Code Puppy") == "code-puppy-pup"


def test_build_agent_config_carries_name_and_tools():
    cfg = agent_def.build_agent_config("Rex", tools=["read_file", "grep"])
    assert cfg["name"] == "rex"
    assert cfg["display_name"] == "Rex"
    assert "Rex" in "\n".join(cfg["system_prompt"])
    assert cfg["tools"] == ["read_file", "grep"]


def test_build_agent_config_uses_base_toolset(monkeypatch):
    monkeypatch.setattr(agent_def, "_base_tools", lambda: ["read_file"])
    cfg = agent_def.build_agent_config("Rex")
    assert cfg["tools"] == ["read_file"]


def test_base_tools_fallback(monkeypatch):
    """If code-puppy can't load, we still get a sane coding toolset."""
    import sys

    monkeypatch.setitem(sys.modules, "code_puppy.agents.agent_manager", None)
    tools = agent_def._base_tools()
    assert "agent_run_shell_command" in tools
    assert "replace_in_file" in tools


def test_ensure_agent_writes_json(monkeypatch, tmp_path):
    monkeypatch.setattr("code_puppy.config.get_user_agents_directory", lambda: str(tmp_path))
    monkeypatch.setattr(agent_def, "_base_tools", lambda: ["read_file"])
    name = agent_def.ensure_agent("Rex")
    assert name == "rex"
    path = tmp_path / "openpup-rex.json"
    assert path.exists()
    cfg = json.loads(path.read_text())
    assert cfg["name"] == "rex"
    assert cfg["tools"] == ["read_file"]


def test_resolve_agent_name_passthrough():
    assert agent_def.resolve_agent_name("code-puppy") == "code-puppy"
    assert agent_def.resolve_agent_name("  web-researcher ") == "web-researcher"


def test_resolve_agent_name_auto(monkeypatch, tmp_path):
    monkeypatch.setattr("code_puppy.config.get_user_agents_directory", lambda: str(tmp_path))
    monkeypatch.setattr(agent_def, "_base_tools", lambda: ["read_file"])
    assert agent_def.resolve_agent_name("auto", "Rex") == "rex"


def test_resolve_agent_name_falls_back_on_failure(monkeypatch):
    def boom(name=None):
        raise RuntimeError("no agents dir")

    monkeypatch.setattr(agent_def, "ensure_agent", boom)
    assert agent_def.resolve_agent_name("auto", "Rex") == "code-puppy"
