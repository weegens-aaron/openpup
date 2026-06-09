"""Tests for Universal Constructor wiring in OpenPup."""

from openpup import agent_tools


def test_advertise_includes_uc_when_enabled(monkeypatch):
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: True)
    names = agent_tools.advertise_tools()
    assert "universal_constructor" in names
    # OpenPup's own tools are still present
    assert "openpup_send_message" in names


def test_advertise_excludes_uc_when_disabled(monkeypatch):
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: False)
    names = agent_tools.advertise_tools()
    assert "universal_constructor" not in names
    assert "openpup_check_email" in names


def test_identity_prompt_mentions_uc_when_enabled(monkeypatch):
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: True)
    prompt = agent_tools.openpup_identity_prompt()
    assert prompt and "universal_constructor" in prompt
    assert "BUILD YOUR OWN TOOLS" in prompt


def test_identity_prompt_no_uc_when_disabled(monkeypatch):
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: False)
    prompt = agent_tools.openpup_identity_prompt()
    assert prompt and "BUILD YOUR OWN TOOLS" not in prompt
