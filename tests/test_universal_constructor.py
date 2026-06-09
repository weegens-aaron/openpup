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


def test_prompt_mentions_uc_when_enabled(monkeypatch):
    import openpup.prompting as prompting

    monkeypatch.setattr(
        "code_puppy.config.get_universal_constructor_enabled", lambda: True, raising=False
    )
    block = prompting._capabilities_block()
    assert "BUILD YOUR OWN TOOLS" in block


def test_prompt_no_uc_when_disabled(monkeypatch):
    import openpup.prompting as prompting

    monkeypatch.setattr(
        "code_puppy.config.get_universal_constructor_enabled", lambda: False, raising=False
    )
    block = prompting._capabilities_block()
    assert "BUILD YOUR OWN TOOLS" not in block
