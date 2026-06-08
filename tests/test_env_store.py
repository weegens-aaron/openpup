from openpup.tui.env_store import EnvStore


def test_set_and_get_roundtrip(tmp_path):
    path = tmp_path / ".env"
    store = EnvStore(path)
    store.set("TELEGRAM_BOT_TOKEN", "abc123")
    store.set_bool("TELEGRAM_ENABLED", True)
    store.save()

    reloaded = EnvStore(path)
    assert reloaded.get("TELEGRAM_BOT_TOKEN") == "abc123"
    assert reloaded.get_bool("TELEGRAM_ENABLED") is True


def test_update_in_place_preserves_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# a comment\nFOO=1\n# another\nBAR=2\n")
    store = EnvStore(path)
    store.set("FOO", "99")
    store.save()

    text = path.read_text()
    assert "# a comment" in text
    assert "# another" in text
    assert "FOO=99" in text
    assert "BAR=2" in text


def test_bool_parsing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("A=true\nB=off\nC=1\nD=no\n")
    store = EnvStore(path)
    assert store.get_bool("A") is True
    assert store.get_bool("B") is False
    assert store.get_bool("C") is True
    assert store.get_bool("D") is False


def test_missing_key_default(tmp_path):
    store = EnvStore(tmp_path / ".env")
    assert store.get("NOPE", "fallback") == "fallback"
    assert store.get_bool("NOPE", True) is True


def test_seeds_from_example(tmp_path):
    (tmp_path / ".env.example").write_text("OPENPUP_NAME=OpenPup\nTELEGRAM_ENABLED=false\n")
    store = EnvStore(tmp_path / ".env")
    # loaded from example since .env doesn't exist yet
    assert store.get("OPENPUP_NAME") == "OpenPup"
