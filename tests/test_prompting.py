"""Tests for the layered system-prompt builder (ported from hermes)."""

from openpup import prompting


def test_build_system_prompt_has_layers():
    prompt = prompting.build_system_prompt()
    assert prompt
    # identity (SOUL default)
    assert "always-on AI companion" in prompt
    # agentic guidance
    assert "Finishing the job" in prompt
    assert "Take action" in prompt
    assert "task list" in prompt.lower()
    # environment
    assert "Current time:" in prompt


def test_load_soul_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    (tmp_path / "SOUL.md").write_text("You are Rex, a very good boy.")
    assert "Rex, a very good boy" in prompting.load_soul()


def test_default_soul_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    assert "always-on AI companion" in prompting.load_soul("Buddy")


def test_ensure_templates_writes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    prompting.ensure_templates("Buddy")
    assert (tmp_path / "SOUL.md").exists()
    assert (tmp_path / "USER.md").exists()


def test_user_profile_template_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    prompting.ensure_templates("Buddy")
    # untouched template (empty fields) should not be injected
    assert prompting.load_user_profile() is None


def test_user_profile_with_facts_is_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    (tmp_path / "USER.md").write_text("# User Profile\n\n- Name: Mike\n- Timezone: US/Pacific\n")
    profile = prompting.load_user_profile()
    assert profile and "Mike" in profile
