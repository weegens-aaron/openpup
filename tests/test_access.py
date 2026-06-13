"""Tests for OpenPup access control (owner + per-platform allowlists)."""

from openpup.access import (
    ALLOWED,
    DENIED,
    MODE_ALLOWLIST,
    MODE_OWNER_ONLY,
    OWNER,
    AccessControl,
)
from openpup.messaging.envelope import Envelope


def _ac(tmp_path, owner="telegram:111"):
    return AccessControl(tmp_path / "access.json", owner_address=owner)


def _env(platform="telegram", channel="111", sender="me", sender_id=None):
    return Envelope(platform=platform, channel=channel, sender=sender, sender_id=sender_id)


def test_owner_is_recognized(tmp_path):
    ac = _ac(tmp_path)
    d = ac.check(_env(channel="111"))
    assert d.allowed and d.role == OWNER


def test_open_mode_allows_strangers(tmp_path):
    ac = _ac(tmp_path)
    d = ac.check(_env(channel="999", sender="stranger"))
    assert d.allowed and d.role == ALLOWED


def test_allowlist_blocks_unknown(tmp_path):
    ac = _ac(tmp_path)
    ac.set_mode("telegram", MODE_ALLOWLIST)
    d = ac.check(_env(channel="999", sender="stranger"))
    assert not d.allowed and d.role == DENIED


def test_allowlist_permits_listed(tmp_path):
    ac = _ac(tmp_path)
    ac.allow("telegram", "999")  # also flips mode to allowlist
    d = ac.check(_env(channel="999", sender="friend"))
    assert d.allowed and d.role == ALLOWED


def test_allow_sets_allowlist_mode(tmp_path):
    ac = _ac(tmp_path)
    ac.allow("telegram", "999")
    assert ac._cfg("telegram")["mode"] == MODE_ALLOWLIST


def test_owner_always_allowed_even_owner_only(tmp_path):
    ac = _ac(tmp_path)
    ac.set_mode("telegram", MODE_OWNER_ONLY)
    assert ac.check(_env(channel="111")).role == OWNER
    assert not ac.check(_env(channel="222")).allowed


def test_deny_removes(tmp_path):
    ac = _ac(tmp_path)
    ac.allow("telegram", "999")
    assert ac.deny("telegram", "999") is True
    assert not ac.check(_env(channel="999")).allowed


def test_match_by_sender_id(tmp_path):
    # Discord-style: channel is a channel id, identity is sender_id
    ac = _ac(tmp_path, owner="discord:owner_user_id")
    ac.allow("discord", "friend_user_id")
    env = _env(platform="discord", channel="chan123", sender="Friend", sender_id="friend_user_id")
    assert ac.check(env).allowed
    owner_env = _env(platform="discord", channel="chan999", sender="Me", sender_id="owner_user_id")
    assert ac.check(owner_env).role == OWNER


def test_persistence(tmp_path):
    ac = _ac(tmp_path)
    ac.allow("telegram", "999")
    ac.set_mode("discord", MODE_OWNER_ONLY)

    reloaded = AccessControl(tmp_path / "access.json", owner_address="telegram:111")
    assert "999" in reloaded._cfg("telegram")["allowed"]
    assert reloaded._cfg("discord")["mode"] == MODE_OWNER_ONLY


def test_multiple_owner_addresses(tmp_path):
    ac = AccessControl(
        tmp_path / "access.json",
        owner_address="telegram:111",
        owner_addresses=["telegram:111", "sms:+15559876543"],
    )
    # owner recognized on telegram
    assert ac.check(_env(platform="telegram", channel="111")).role == OWNER
    # AND on sms (their cell)
    sms_env = _env(platform="sms", channel="+15559876543")
    assert ac.check(sms_env).role == OWNER
    # a different sms number is not the owner
    assert ac.check(_env(platform="sms", channel="+19999999999")).role != OWNER


class FakeDirectory:
    def __init__(self, roles):
        # roles: {(platform, channel): role}
        self._roles = roles

    def role_of(self, platform, channel):
        return self._roles.get((platform, str(channel)), "")


def test_roster_blocked_role_denies(tmp_path):
    d = FakeDirectory({("telegram", "999"): "blocked"})
    ac = AccessControl(tmp_path / "access.json", owner_address="telegram:111", directory=d)
    assert not ac.check(_env(channel="999")).allowed


def test_roster_allowed_role_permits_in_allowlist_mode(tmp_path):
    d = FakeDirectory({("telegram", "999"): "allowed"})
    ac = AccessControl(tmp_path / "access.json", owner_address="telegram:111", directory=d)
    ac.set_mode("telegram", MODE_ALLOWLIST)
    assert ac.check(_env(channel="999")).allowed


def test_roster_owner_role_is_owner(tmp_path):
    d = FakeDirectory({("telegram", "999"): "owner"})
    ac = AccessControl(tmp_path / "access.json", owner_address="telegram:111", directory=d)
    assert ac.check(_env(channel="999")).role == OWNER


def test_roster_role_matches_sender_id(tmp_path):
    d = FakeDirectory({("discord", "user42"): "blocked"})
    ac = AccessControl(tmp_path / "access.json", directory=d)
    env = _env(platform="discord", channel="chan", sender_id="user42")
    assert not ac.check(env).allowed


def test_no_owner_configured(tmp_path):
    ac = AccessControl(tmp_path / "access.json", owner_address=None)
    # open mode by default -> allowed but not owner
    d = ac.check(_env(channel="111"))
    assert d.allowed and d.role == ALLOWED


def test_role_token_save_and_restore():
    """Per-message role scoping: reset_current_role restores the outer role."""
    from openpup import access

    access.set_current_role(access.OWNER)
    token = access.set_current_role(access.ALLOWED)
    assert access.get_current_role() == access.ALLOWED
    access.reset_current_role(token)
    assert access.get_current_role() == access.OWNER
