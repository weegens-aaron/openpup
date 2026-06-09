"""Tests for the contact directory."""

from openpup.directory import ContactDirectory


def _dir(tmp_path):
    return ContactDirectory(tmp_path / "contacts.json")


def test_record_and_list(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    d.record("telegram", "222", "Sara")
    contacts = d.search()
    assert {c["name"] for c in contacts} == {"Mike", "Sara"}


def test_record_upsert_increments(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    d.record("telegram", "111", "Mike")
    assert d.search()[0]["count"] == 2


def test_resolve_explicit_address(tmp_path):
    d = _dir(tmp_path)
    assert d.resolve("telegram:999") == "telegram:999"
    assert d.resolve("email:foo@bar.com") == "email:foo@bar.com"


def test_resolve_by_name(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    assert d.resolve("Mike") == "telegram:111"
    assert d.resolve("mike") == "telegram:111"


def test_resolve_platform_scoped_name(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    d.record("discord", "222", "Mike")
    # ambiguous bare name -> None; platform-scoped -> resolved
    assert d.resolve("Mike") is None
    assert d.resolve("telegram:Mike") == "telegram:111"
    assert d.resolve("discord:Mike") == "discord:222"


def test_resolve_unambiguous_prefix(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Michael")
    assert d.resolve("Mich") == "telegram:111"


def test_is_known(tmp_path):
    d = _dir(tmp_path)
    d.record("sms", "+15551112222", "Bob")
    assert d.is_known("sms", "+15551112222")
    assert not d.is_known("sms", "+19999999999")


def test_persistence(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    again = ContactDirectory(tmp_path / "contacts.json")
    assert again.resolve("Mike") == "telegram:111"


# ---- roster editing ------------------------------------------------------
def test_upsert_creates_with_role_and_notes(tmp_path):
    d = _dir(tmp_path)
    d.upsert("sms", "+15551112222", name="Bob", role="allowed", notes="plumber")
    entry = d.get("sms", "+15551112222")
    assert entry["name"] == "Bob"
    assert entry["role"] == "allowed"
    assert entry["notes"] == "plumber"


def test_upsert_updates_existing(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "111", "Mike")
    d.upsert("telegram", "111", role="blocked")
    assert d.role_of("telegram", "111") == "blocked"
    assert d.get("telegram", "111")["name"] == "Mike"  # unchanged


def test_by_platform(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "1", "A")
    d.record("telegram", "2", "B")
    d.record("discord", "9", "C")
    assert {c["channel"] for c in d.by_platform("telegram")} == {"1", "2"}
    assert len(d.by_platform("discord")) == 1


def test_remove_entry(tmp_path):
    d = _dir(tmp_path)
    d.record("telegram", "1", "A")
    assert d.remove("telegram", "1") is True
    assert d.get("telegram", "1") is None
    assert d.remove("telegram", "1") is False


def test_role_of_default_empty(tmp_path):
    d = _dir(tmp_path)
    assert d.role_of("telegram", "nope") == ""
