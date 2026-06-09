"""Tests for the persona / SOUL rendering."""

from openpup import prompting


def test_render_soul_default_is_warm_sassy_relentless():
    soul = prompting.render_soul("Biscuit", "warm_loyal_sassy", "relentless")
    assert "Biscuit" in soul
    assert "sassy" in soul.lower()
    assert "RELENTLESS" in soul


def test_render_soul_presets_change_text():
    calm = prompting.render_soul("X", "calm_pro", "reserved")
    chaotic = prompting.render_soul("X", "chaotic_retriever", "proactive")
    assert "unflappable" in calm.lower()
    assert "retriever" in chaotic.lower()
    assert calm != chaotic


def test_render_soul_unknown_preset_falls_back():
    soul = prompting.render_soul("X", "nonsense", "nonsense")
    # falls back to defaults rather than crashing
    assert "X" in soul and len(soul) > 50


def test_write_and_load_soul(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    prompting.write_soul("Rex", "sharp_dry", "balanced")
    assert (tmp_path / "SOUL.md").exists()
    loaded = prompting.load_soul()
    assert "Rex" in loaded


def test_load_soul_generates_from_presets_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting, "openpup_home", lambda: tmp_path)
    monkeypatch.setattr(
        prompting, "_persona_from_settings", lambda: ("Spot", "chaotic_retriever", "proactive")
    )
    soul = prompting.load_soul()
    assert "Spot" in soul and "retriever" in soul.lower()
