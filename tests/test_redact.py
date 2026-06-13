"""Corpus tests for deep secret redaction (openpup.security.redact).

Each pattern category gets a positive case (redacted) and a tricky negative
(prose, short hex, UUIDs, normal URLs must pass through untouched). Plus
env-value redaction of OpenPup's own loaded secrets, and idempotency.
"""

import pytest

from openpup.security.redact import MARKER, PRIVATE_KEY_MARKER, redact

# ---------------------------------------------------------------------------
# Positive corpus: (category, input) — every one must come out with MARKER
# and without the sensitive payload.
# ---------------------------------------------------------------------------
# NOTE: every "secret" below is a synthetic fixture, not a real credential.
# The provider-prefixed ones are assembled from fragments (e.g. "sk-" "proj-...")
# so the *static source* contains no complete, scannable token -- this keeps
# GitHub push-protection / secret scanners from false-positiving on test data.
# Python concatenates the adjacent literals at parse time, so ``redact()`` sees
# the identical full string and the assertions are unchanged.
POSITIVE = [
    ("openai key", "error calling api with sk-" "proj-Abc123Def456Ghi789Jkl"),
    ("anthropic key", "ANTHROPIC says sk-" "ant-api03-Zz9Yy8Xx7Ww6Vv5Uu4"),
    ("stripe key", "charge failed: sk_" "live_4eC39HqLyjWDarjtT1zdp7dc"),
    ("github pat", "git clone failed using ghp" "_AbCdEfGhIjKlMnOpQrStUvWx"),
    ("slack token", "slack said no: xoxb" "-1234567890-abcdefghijklmn"),
    ("google key", "AIza" "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    ("aws access key", "creds: AKIA" "IOSFODNN7EXAMPLE"),
    ("twilio sid", "sid AC" "0123456789abcdef0123456789abcdef rejected"),
    ("meta token", "graph api: EAA" "Gm0PX4ZCpsBAOZCZBxyzAbCdEfGh123456"),
    ("jwt", "session eyJ" "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dQw4w9WgXcQ_abc"),
    ("bearer header", "Authorization: Bearer abcdefghijklmnop"),
    ("telegram bot token", "failed for bot 123456789:" "AAErsz90abcdefghijklmnopqrstuvwx"),
    (
        "discord bot token",
        "login failed: MTAxMjM0NTY3ODkwMTIzNDU2Nzg" "." "GaBcDe" "." "AbCdEfGhIjKlMnOpQrStUvWxYz123456",
    ),
    ("db connstring", "postgres://pup:hunter2@db.example.com:5432/openpup"),
    ("url userinfo", "fetching https://admin:t0psecret@api.example.com/v1/status"),
    ("url query secret", "GET https://api.example.com/cb?access_token=opaque123&page=2"),
    ("env assign", "DISCORD_BOT_TOKEN=MTAx" "abcdef123456 exported"),
    ("env assign twilio", "TWILIO_AUTH_TOKEN='0123456789abcdef0123456789abcdef'"),
    ("generic assign", "token=abc123secret"),
    ("generic assign colon", "password: hunter22222"),
    ("json field", '{"api_key": "abc123def456", "model": "gpt"}'),
]

NEGATIVE = [
    ("plain prose", "hello world, the pup fetched the stick"),
    ("short hex", "commit deadbeef looks fine"),
    ("md5 hash", "checksum is d41d8cd98f00b204e9800998ecf8427e"),
    ("uuid", "request id 123e4567-e89b-12d3-a456-426614174000 done"),
    ("normal url", "see https://example.com/docs?page=2&q=hello#anchor"),
    ("author assign", "AUTHOR=JaneDoe wrote this module"),
    ("user id assign", "USER_ID=4216049 logged in"),
    ("ssh auth sock", "SSH_AUTH_SOCK_PATH=/tmp/ssh-XXXXXX/agent.1234 is set"),
    ("token_count", "token_count=512 and max_tokens=4096 for this run"),
    ("timestamp colon", "started at 12:34:56 on 2025-05-17"),
    ("semver", "openpup version 1.2.3 with python 3.12.1"),
]


@pytest.mark.parametrize("category,text", POSITIVE, ids=[c for c, _ in POSITIVE])
def test_positive_redacted(category, text):
    out = redact(text)
    assert MARKER in out, f"{category}: expected redaction in {out!r}"
    assert out != text


@pytest.mark.parametrize("category,text", NEGATIVE, ids=[c for c, _ in NEGATIVE])
def test_negative_untouched(category, text):
    assert redact(text) == text


# ---------------------------------------------------------------------------
# Category-specific shape checks (the marker lands in the right spot).
# ---------------------------------------------------------------------------
def test_db_connstring_keeps_host():
    out = redact("postgres://pup:hunter2@db.example.com:5432/openpup")
    assert out == f"postgres://pup:{MARKER}@db.example.com:5432/openpup"


def test_url_query_keeps_benign_params():
    out = redact("https://api.example.com/cb?access_token=opaque123&page=2")
    assert out == f"https://api.example.com/cb?access_token={MARKER}&page=2"


def test_private_key_block():
    block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA7example\nmorelines\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(f"dumped key:\n{block}\ndone")
    assert PRIVATE_KEY_MARKER in out
    assert "MIIEpAIBAAKCAQEA7example" not in out


def test_telegram_token_keeps_bot_id():
    out = redact("123456789:AAErsz90abcdefghijklmnopqrstuvwx")
    assert out == f"123456789:{MARKER}"


def test_env_assign_keeps_name_and_quotes():
    out = redact('WHATSAPP_ACCESS_TOKEN="EAAGsecretsecret"')
    assert out == f'WHATSAPP_ACCESS_TOKEN="{MARKER}"'


def test_generic_assign_preserves_separator():
    assert redact("password: hunter22222") == f"password: {MARKER}"
    assert redact("token=abc123secret") == f"token={MARKER}"


# ---------------------------------------------------------------------------
# Value-based redaction of known-loaded secrets.
# ---------------------------------------------------------------------------
def test_env_value_redaction_explicit_vars(monkeypatch):
    secret = "wOOf-such-secret-very-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    out = redact(f"adapter crashed while using {secret} to connect")
    assert secret not in out
    assert MARKER in out


def test_env_value_redaction_generic_suffix(monkeypatch):
    secret = "zq9-llm-provider-credential-77"
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", secret)
    out = redact(f"provider error: {secret} rejected")
    assert secret not in out


def test_env_value_redaction_skips_short_values(monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "true")
    assert redact("the check returned true") == "the check returned true"


def test_env_value_redaction_ignores_non_secret_names(monkeypatch):
    monkeypatch.setenv("OPENPUP_HEARTBEAT_BEHAVIORS", "reflect,outreach")
    out = redact("behaviors are reflect,outreach today")
    assert "reflect,outreach" in out


# ---------------------------------------------------------------------------
# Idempotency: redacting twice == redacting once, across the whole corpus.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "category,text", POSITIVE + NEGATIVE, ids=[c for c, _ in POSITIVE + NEGATIVE]
)
def test_idempotent(category, text):
    once = redact(text)
    assert redact(once) == once


def test_idempotent_env_values(monkeypatch):
    secret = "wOOf-such-secret-very-token"
    monkeypatch.setenv("DISCORD_BOT_TOKEN", secret)
    once = redact(f"DISCORD_BOT_TOKEN={secret} during login")
    assert redact(once) == once


# ---------------------------------------------------------------------------
# Continuity: governance re-export keeps old call sites working.
# ---------------------------------------------------------------------------
def test_governance_reexport():
    from openpup import governance
    from openpup.security import redact as redact_module

    assert governance.redact is redact_module.redact


def test_empty_and_falsy_passthrough():
    assert redact("") == ""
    assert redact(None) is None
