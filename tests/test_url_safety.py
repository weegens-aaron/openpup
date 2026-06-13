"""URL safety verdicts (openpup.security.url_safety).

Every blocked category gets a case: bad schemes, each private range,
metadata endpoints, userinfo, obfuscated IP literals, and hostnames that
resolve into private space. DNS is monkeypatched throughout so verdicts
are deterministic and offline.
"""

import pytest

from openpup.security import url_safety
from openpup.security.url_safety import UrlVerdict, check_url

PUBLIC_IP = "93.184.216.34"  # example.com


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve every hostname to a public IP unless a test overrides it."""
    monkeypatch.setattr(url_safety, "_resolve_host", lambda host, timeout_s=3.0: [PUBLIC_IP])


def resolve_to(monkeypatch, ips):
    monkeypatch.setattr(url_safety, "_resolve_host", lambda host, timeout_s=3.0: ips)


# ---------------------------------------------------------------------------
# Schemes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://ftp.example.com/pub",
        "gopher://example.com:70/1",
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
    ],
    ids=["file", "ftp", "gopher", "javascript", "data"],
)
def test_non_http_schemes_rejected(url):
    verdict = check_url(url)
    assert not verdict.allowed
    assert "http" in verdict.reason


def test_http_and_https_allowed():
    assert check_url("http://example.com/").allowed
    assert check_url("https://example.com/path?q=1").allowed


# ---------------------------------------------------------------------------
# Private / loopback / link-local / metadata literals
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/admin",
        "http://172.16.4.2/",
        "http://172.31.255.255/",
        "http://192.168.1.1/router",
        "http://127.0.0.1:8080/",
        "http://169.254.1.1/",
        "http://100.64.0.1/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fd12:3456::1]/",
        "http://[::ffff:10.0.0.5]/",
    ],
    ids=[
        "10/8",
        "172.16/12-low",
        "172.16/12-high",
        "192.168/16",
        "loopback",
        "link-local",
        "cgnat",
        "v6-loopback",
        "fc00::/7",
        "fd-ula",
        "v4-mapped-v6",
    ],
)
def test_private_ip_literals_rejected(url):
    verdict = check_url(url)
    assert not verdict.allowed
    assert "private" in verdict.reason


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials",
        "http://100.100.100.200/",
        "http://[fd00:ec2::254]/",
    ],
    ids=["aws-imds", "ecs-task", "alibaba", "aws-v6"],
)
def test_metadata_ips_rejected(url):
    verdict = check_url(url)
    assert not verdict.allowed
    assert "metadata" in verdict.reason


def test_metadata_hostname_rejected_without_resolution():
    verdict = check_url("http://metadata.google.internal/computeMetadata/v1/")
    assert not verdict.allowed
    assert "metadata" in verdict.reason


# ---------------------------------------------------------------------------
# Userinfo & obfuscated IP forms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/",
        "https://admin@example.com/",
        "http://trusted.com:443@evil.com/",
    ],
    ids=["user-pass", "user-only", "parser-confusion"],
)
def test_userinfo_rejected(url):
    verdict = check_url(url)
    assert not verdict.allowed
    assert "userinfo" in verdict.reason


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex 127.0.0.1
        "http://0177.0.0.1/",  # octal first octet
        "http://0xa.0.0.1/",  # hex first octet -> 10.0.0.1
        "http://192.168.1/",  # short form -> 192.168.0.1
    ],
    ids=["decimal", "hex", "octal", "hex-octet", "short-form"],
)
def test_obfuscated_private_ips_rejected(url):
    assert not check_url(url).allowed


# ---------------------------------------------------------------------------
# DNS-resolved verdicts
# ---------------------------------------------------------------------------
def test_hostname_resolving_to_private_rejected(monkeypatch):
    resolve_to(monkeypatch, ["10.0.0.5"])
    verdict = check_url("http://internal.example.com/secrets")
    assert not verdict.allowed
    assert "resolves to" in verdict.reason


def test_hostname_resolving_to_metadata_rejected(monkeypatch):
    resolve_to(monkeypatch, ["169.254.169.254"])
    assert not check_url("http://sneaky.example.com/").allowed


def test_hostname_with_mixed_resolution_rejected(monkeypatch):
    # One public + one private answer: any private answer blocks.
    resolve_to(monkeypatch, [PUBLIC_IP, "192.168.0.10"])
    assert not check_url("http://dual.example.com/").allowed


def test_resolution_failure_allows_with_reason(monkeypatch):
    # Documented fail-open: unresolvable hosts can't be fetched anyway.
    resolve_to(monkeypatch, None)
    verdict = check_url("http://no-such-host.example.com/")
    assert verdict.allowed
    assert "did not resolve" in verdict.reason


def test_benign_public_url_allowed():
    verdict = check_url("https://api.example.com/v1/data?page=2")
    assert verdict.allowed
    assert verdict.reason == "allowed"


# ---------------------------------------------------------------------------
# Malformed input fails closed; verdict ergonomics
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    ["", "   ", "http://", "https:///path"],
    ids=["empty", "blank", "no-host", "no-host-path"],
)
def test_malformed_urls_rejected(url):
    assert not check_url(url).allowed


def test_verdict_is_truthy_on_allow():
    assert UrlVerdict(True, "allowed")
    assert not UrlVerdict(False, "nope")
