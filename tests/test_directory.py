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
