"""Deep secret redaction, ported in spirit from hermes-agent.

Scrubs credentials from any text before it reaches the model, a chat
platform, or a log line. Two complementary strategies:

* **Pattern-based** — vendor key prefixes (``sk-``, ``ghp_``, ``AKIA``...),
  bot tokens (Telegram/Discord), JWTs, bearer headers, private key blocks,
  DB connection-string passwords, URL userinfo/query secrets, and
  secret-named assignments (env / JSON / generic ``key=value``).
* **Value-based** — the actual values of secret env vars OpenPup itself
  loads (Discord/Telegram bot tokens, Twilio creds, SMTP password, etc.)
  are looked up at call time and replaced wherever they appear, even when
  no shape-based pattern would catch them.

Replacement marker is ``***`` (the convention :mod:`openpup.governance`
established); private key blocks become ``[REDACTED PRIVATE KEY]``.
``redact`` is idempotent: redacting twice equals redacting once.

Deliberately NOT ported from hermes: E.164 phone masking (OpenPup routes
messages by ``sms:+1...`` addresses, masking them breaks usability),
form-body / HTTP access-log redaction (no such surfaces here), and the
``HERMES_REDACT_SECRETS`` kill switch (redaction is always on).
"""

from __future__ import annotations

import os
import re
from typing import Iterator, List

MARKER = "***"
PRIVATE_KEY_MARKER = "[REDACTED PRIVATE KEY]"

# --- vendor key prefixes ----------------------------------------------------
# Match a known credential prefix + contiguous token chars. Generic vendors
# only; hermes-specific integrations (BrowserBase, Matrix, Mem0, ...) dropped.
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",  # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    r"sk_[A-Za-z0-9_]{10,}",  # Stripe (sk_live_/sk_test_), ElevenLabs
    r"rk_live_[A-Za-z0-9]{10,}",  # Stripe restricted key
    r"ghp_[A-Za-z0-9]{10,}",  # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",  # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",  # GitHub OAuth access token
    r"ghu_[A-Za-z0-9]{10,}",  # GitHub user-to-server token
    r"ghs_[A-Za-z0-9]{10,}",  # GitHub server-to-server token
    r"ghr_[A-Za-z0-9]{10,}",  # GitHub refresh token
    r"glpat-[A-Za-z0-9_-]{10,}",  # GitLab PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}",  # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",  # Google API keys
    r"AKIA[A-Z0-9]{16}",  # AWS access key ID
    r"ASIA[A-Z0-9]{16}",  # AWS temporary access key ID
    r"SG\.[A-Za-z0-9_-]{10,}",  # SendGrid API key
    r"hf_[A-Za-z0-9]{10,}",  # HuggingFace token
    r"r8_[A-Za-z0-9]{10,}",  # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",  # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",  # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",  # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",  # DigitalOcean OAuth
    r"gsk_[A-Za-z0-9]{10,}",  # Groq Cloud API key
    r"xai-[A-Za-z0-9]{30,}",  # xAI (Grok) API key
    r"tvly-[A-Za-z0-9]{10,}",  # Tavily search API key
    r"exa_[A-Za-z0-9]{10,}",  # Exa search API key
    r"pplx-[A-Za-z0-9]{10,}",  # Perplexity API key
    r"gAAAA[A-Za-z0-9_=-]{20,}",  # Fernet-encrypted tokens
    r"EAA[A-Za-z0-9]{20,}",  # Meta Graph API tokens (WhatsApp Cloud)
    r"AC[0-9a-f]{32}",  # Twilio account SID
    r"SK[0-9a-f]{32}",  # Twilio API key SID
]
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])")


def _extract_literal_prefix(pattern: str) -> str:
    """Leading literal chars of a regex pattern (stops at first metachar).

    Any match of the pattern MUST contain this literal, so the pre-screen
    below can never produce a false negative.
    """
    meta = "[(\\.?*+|{^$"
    for i, ch in enumerate(pattern):
        if ch in meta:
            return pattern[:i]
    return pattern


# Cheap substring gate for the (large) prefix alternation. Derived from
# _PREFIX_PATTERNS at import so new patterns can't silently bypass it.
_PREFIX_SUBSTRINGS = tuple(_extract_literal_prefix(p) for p in _PREFIX_PATTERNS)

# --- assignments ------------------------------------------------------------
# ENV-style: NAME=value where NAME *ends* in a secret-like underscore segment
# (DISCORD_BOT_TOKEN, TWILIO_ACCOUNT_SID, ...). Suffix-anchored so AUTHOR,
# USER_ID, TOKEN_COUNT, and SSH_AUTH_SOCK-style names do NOT match.
_SECRET_NAME_SEGMENT = r"(?:API_?KEY|APIKEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|AUTH|SID)"
_ENV_ASSIGN_RE = re.compile(
    # (?<![?&]) — names preceded by ? or & are URL query params, which the
    # URL pass handles with a value charset that stops at the next param.
    rf"(?<![?&])\b((?:[A-Za-z0-9]+_)*{_SECRET_NAME_SEGMENT})\s*=\s*(['\"]?)(\S+)\2",
    re.IGNORECASE,
)

# Generic prose/CLI style: ``token=...``, ``password: ...`` (separator kept).
_ASSIGN_SECRET_RE = re.compile(
    r"\b(access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token"
    r"|api[_-]?key|apikey|client[_-]?secret|webhook[_-]?secret|password"
    r"|passwd|secret|signature|sig|token)\b"
    r"(\s*[=:]\s*)([^\s,;&]+)",  # & excluded so query strings aren't over-eaten
    re.IGNORECASE,
)

# JSON fields: "apiKey": "value", "token": "value", ...
_JSON_KEY_NAMES = (
    r"(?:api_?key|apikey|token|secret|password|access_token|refresh_token"
    r"|auth_token|bearer|client_secret|private_key|authorization)"
)
_JSON_FIELD_RE = re.compile(rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"', re.IGNORECASE)

# --- headers & tokens ---------------------------------------------------------
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]{8,})", re.IGNORECASE)

# Telegram-style bot tokens: [bot]<digits>:<token> (thresholds match the
# previous governance pattern for continuity).
_BOT_TOKEN_RE = re.compile(r"\b(bot)?(\d{6,}):([A-Za-z0-9_\-]{20,})\b")

# Discord bot tokens: three dot-separated base64url chunks
# (base64(snowflake) "." timestamp "." HMAC).
_DISCORD_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,}\b")

# JWTs: header[.payload[.signature]] — always start with eyJ (base64 "{").
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}")

# --- key blocks & URLs --------------------------------------------------------
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# DB connection strings: protocol://user:PASSWORD@host
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@\s]+)(@)",
    re.IGNORECASE,
)

# Web URLs with userinfo: scheme://user:password@host
_URL_USERINFO_RE = re.compile(r"(https?|wss?|ftp)://([^/\s:@]+):([^/\s@]+)@")

# Secret-named URL query params. Narrower than hermes's full list on purpose:
# "code"/"key"/"session" break OAuth-callback and share-link flows, so they
# stay (the prefix/JWT patterns still catch known credential shapes in URLs).
_URL_SECRET_RE = re.compile(
    r"([?&](?:access_token|refresh_token|id_token|token|api[_-]?key|apikey"
    r"|auth[_-]?token|client_secret|password|secret|jwt|signature|sig)=)"
    r"([^&#\s]+)",
    re.IGNORECASE,
)

# --- value-based redaction of OpenPup's own loaded secrets --------------------
# Every secret-bearing setting from openpup.config / .env.example.
_OPENPUP_SECRET_ENV_VARS = (
    "OPENPUP_WEBHOOK_SECRET",
    "DISCORD_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_VERIFY_TOKEN",
    "EMAIL_PASSWORD",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
)
# Generic suffix rule for anything else in the environment (OPENAI_API_KEY,
# ANTHROPIC_API_KEY, ...). Suffix-anchored, not substring, so SSH_AUTH_SOCK
# and friends never match.
_SECRET_ENV_SUFFIXES = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_PASSWD",
    "_CREDENTIALS",
    "_SID",
    "APIKEY",
)
# Values shorter than this are skipped — too likely to be "true", a port, etc.
_MIN_SECRET_VALUE_LEN = 6


def _is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in _OPENPUP_SECRET_ENV_VARS or upper.endswith(_SECRET_ENV_SUFFIXES)


def _known_secret_values() -> Iterator[str]:
    """Values of secret-named env vars, longest first.

    Read at call time (not import) so secrets loaded or rotated after import
    are still covered, and tests can monkeypatch the environment.
    Longest-first ordering keeps one secret that is a substring of another
    from leaving fragments behind.
    """
    values: List[str] = [
        value.strip()
        for name, value in os.environ.items()
        if _is_secret_env_name(name) and len(value.strip()) >= _MIN_SECRET_VALUE_LEN
    ]
    return iter(sorted(set(values), key=len, reverse=True))


# --- public API ----------------------------------------------------------------
def redact(text: str) -> str:
    """Scrub likely secrets from a string before surfacing it.

    Safe on any string — non-matching text passes through unchanged.
    Idempotent: ``redact(redact(s)) == redact(s)``.
    """
    if not text:
        return text

    # 1. Exact values of known-loaded secrets (no shape required).
    for value in _known_secret_values():
        if value in text:
            text = text.replace(value, MARKER)

    # 2. Private key blocks (multi-line; before line-oriented patterns).
    if "BEGIN" in text and "-----" in text:
        text = _PRIVATE_KEY_RE.sub(PRIVATE_KEY_MARKER, text)

    # 3. Vendor key prefixes (substring-gated: skip the big alternation
    #    entirely when no literal prefix appears in the text).
    if any(p in text for p in _PREFIX_SUBSTRINGS):
        text = _PREFIX_RE.sub(MARKER, text)

    # 4. JWTs.
    if "eyJ" in text:
        text = _JWT_RE.sub(MARKER, text)

    # 5. Bearer headers (Authorization: Bearer xyz, or bare Bearer xyz).
    if "earer" in text or "EARER" in text:
        text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{MARKER}", text)

    # 6. Bot tokens: Telegram digits:token, Discord triple-dotted base64.
    if ":" in text:
        text = _BOT_TOKEN_RE.sub(lambda m: f"{m.group(1) or ''}{m.group(2)}:{MARKER}", text)
    if "." in text:
        text = _DISCORD_TOKEN_RE.sub(MARKER, text)

    # 7. Credentials embedded in URLs.
    if "://" in text:
        text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}{MARKER}{m.group(3)}", text)
        text = _URL_USERINFO_RE.sub(lambda m: f"{m.group(1)}://{m.group(2)}:{MARKER}@", text)
    if "?" in text or "&" in text:
        text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}{MARKER}", text)

    # 8. Secret-named assignments: ENV style, generic prose, JSON fields.
    if "=" in text:
        text = _ENV_ASSIGN_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}{MARKER}{m.group(2)}", text)
    if "=" in text or ":" in text:
        text = _ASSIGN_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MARKER}", text)
    if ":" in text and '"' in text:
        text = _JSON_FIELD_RE.sub(lambda m: f'{m.group(1)}: "{MARKER}"', text)

    return text
