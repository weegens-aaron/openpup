"""Tests for outbound comms governance (rate limit, send policy, redaction)."""

from openpup.governance import (
    POLICY_CONTACTS,
    POLICY_OPEN,
    POLICY_OWNER_ONLY,
    RateLimiter,
    SendPolicy,
    redact,
)


class FakeDir:
    def __init__(self, known):
        self._known = set(known)

    def is_known(self, platform, channel):
        return (platform, channel) in self._known


def test_rate_limiter_window():
    rl = RateLimiter(per_minute=2)
    assert rl.allow("telegram", now=0)
    assert rl.allow("telegram", now=1)
    assert not rl.allow("telegram", now=2)  # 3rd within window
    # after the window slides, allowed again
    assert rl.allow("telegram", now=61)


def test_rate_limiter_per_platform():
    rl = RateLimiter(per_minute=1)
    assert rl.allow("telegram", now=0)
    assert rl.allow("discord", now=0)  # separate bucket
    assert not rl.allow("telegram", now=0)


def test_policy_open_allows_anyone():
    p = SendPolicy(policy=POLICY_OPEN, per_minute=100, owner_address="telegram:1")
    assert p.check("sms:+15550001111").allowed


def test_policy_owner_only():
    p = SendPolicy(policy=POLICY_OWNER_ONLY, per_minute=100, owner_address="telegram:1")
    assert p.check("telegram:1").allowed
    assert not p.check("telegram:2").allowed


def test_policy_contacts():
    p = SendPolicy(policy=POLICY_CONTACTS, per_minute=100, owner_address="telegram:1")
    d = FakeDir({("telegram", "999")})
    assert p.check("telegram:1", directory=d).allowed  # owner
    assert p.check("telegram:999", directory=d).allowed  # known
    assert not p.check("telegram:888", directory=d).allowed  # stranger


def test_policy_rate_limit_blocks():
    p = SendPolicy(policy=POLICY_OPEN, per_minute=1, owner_address=None)
    assert p.check("telegram:1", now=0).allowed
    assert not p.check("telegram:2", now=0).allowed


def test_bad_address():
    p = SendPolicy()
    assert not p.check("noformat").allowed


def test_redact_secrets():
    assert "***" in redact("token=abc123secret")
    assert "***" in redact("Authorization: Bearer abcdefghijklmnop")
    assert "***" in redact("failed for bot 123456789:AAErsz90abcdefghijklmnopqrstuvwx")
    assert "hello world" == redact("hello world")
