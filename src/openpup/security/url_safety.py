"""URL safety checks: SSRF guard for agent-controllable fetches.

Ported in spirit from hermes-agent (``tools/url_safety.py``), trimmed of
hermes's config/policy plumbing (allow-private toggles, trusted-host
exemptions). What survives is the core verdict: is this URL safe for an
always-on agent to fetch on someone's say-so?

Blocked outright:
* Non-http(s) schemes (``file:``, ``ftp:``, ``gopher:``, ``javascript:``,
  ``data:``) — none of them are legitimate remote-content fetches.
* Userinfo in the URL (``http://user:pass@host``) — credential smuggling
  and a classic parser-confusion trick (``http://trusted.com@evil.com``).
* Private / loopback / link-local / CGNAT / reserved address space
  (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, 100.64/10, ::1,
  fc00::/7, IPv4-mapped variants) — whether given as a literal IP, an
  obfuscated IP (decimal ``2130706433``, octal ``0177.0.0.1``, hex
  ``0x7f000001``), or a hostname that *resolves* there.
* Cloud metadata endpoints (169.254.169.254 and friends,
  ``metadata.google.internal``) — the #1 SSRF target, never legitimate.

One deliberate divergence from hermes: on DNS *resolution failure* (timeout
or NXDOMAIN) we ALLOW with the reason noted, where hermes fails closed.
Rationale: OpenPup is a personal companion, not a fleet gateway — if DNS
can't resolve the host, the subsequent fetch fails identically anyway, so
blocking buys no security while a flaky resolver would break legitimate
asks. Availability over strictness.

Known limitation (inherited from hermes, documented not fixed): DNS
rebinding (TOCTOU) — a TTL=0 resolver can answer public for the check and
private for the connection. Fixing that requires connection-level pinning,
which belongs in the fetch client, not a pre-flight check.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import List, Optional, Union
from urllib.parse import urlsplit

logger = logging.getLogger("openpup.url_safety")

# Seconds to wait for DNS before giving up (and allowing, reason noted).
_RESOLVE_TIMEOUT_S = 3.0

# Cloud metadata hostnames — always blocked, no resolution needed.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)

# Cloud metadata / credential endpoints — the non-negotiable floor.
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure/DO/Oracle metadata
        ipaddress.ip_address("169.254.170.2"),  # AWS ECS task metadata
        ipaddress.ip_address("169.254.169.253"),  # Azure IMDS wire server
        ipaddress.ip_address("fd00:ec2::254"),  # AWS metadata (IPv6)
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    }
)

# CGNAT (RFC 6598) is neither is_private nor is_global in the ipaddress
# module — must be blocked explicitly (Tailscale/WireGuard/cloud internals).
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Obfuscated IPv4 spellings inet_aton accepts but urlsplit treats as plain
# hostnames: pure decimal ("2130706433"), hex ("0x7f000001"), octal
# ("0177.0.0.1"), and dotted forms with hex/octal/short octets.
_OBFUSCATED_IPV4 = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}$", re.I)

_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


@dataclass(frozen=True)
class UrlVerdict:
    """The outcome of a URL safety check."""

    allowed: bool
    reason: str

    def __bool__(self) -> bool:  # `if check_url(u):` reads naturally
        return self.allowed


def _is_blocked_ip(ip: _IPAddress) -> bool:
    """True when the IP lands in private/loopback/link-local/reserved space."""
    # IPv4-mapped IPv6 (::ffff:x.x.x.x) — judge by the embedded IPv4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private  # 10/8, 172.16/12, 192.168/16, 127/8 (v4); fc00::/7, ::1 (v6)
        or ip.is_loopback
        or ip.is_link_local  # 169.254/16, fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in _CGNAT_NETWORK
    )


def _is_metadata_ip(ip: _IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip in _METADATA_IPS


def _parse_ip_literal(hostname: str) -> Optional[_IPAddress]:
    """Parse a hostname as an IP literal, including obfuscated IPv4 forms.

    ``ipaddress`` only accepts canonical dotted-quad / IPv6 text; attackers
    use the legacy ``inet_aton`` spellings (decimal, octal, hex, short
    forms) that HTTP clients happily resolve. Decode those here so
    ``http://2130706433/`` is judged as 127.0.0.1, not as a DNS name.
    """
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if not _OBFUSCATED_IPV4.match(hostname):
        return None
    try:
        packed = socket.inet_aton(hostname)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _resolve_host(hostname: str, timeout_s: float = _RESOLVE_TIMEOUT_S) -> Optional[List[str]]:
    """Resolve ``hostname`` to IP strings, or None on failure/timeout.

    ``socket.getaddrinfo`` has no timeout knob, so it runs on a throwaway
    thread we abandon if it overstays. Tests monkeypatch this function to
    keep verdicts deterministic and offline.
    """

    def _lookup() -> List[str]:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        return [info[4][0] for info in infos]

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(_lookup).result(timeout=timeout_s)
    except (socket.gaierror, FutureTimeoutError, OSError):
        return None
    finally:
        executor.shutdown(wait=False)


def check_url(url: str) -> UrlVerdict:
    """Pre-flight safety verdict for a URL the agent was asked to fetch.

    Fails closed on malformed input and unexpected errors; the single
    documented fail-open case is DNS resolution failure (see module
    docstring — availability over strictness).
    """
    try:
        return _check(url)
    except Exception as exc:  # noqa: BLE001 — parser edge cases must not become bypasses
        logger.warning("URL safety check error for %r: %s", url, exc)
        return UrlVerdict(False, f"unparseable URL ({exc})")


def _check(url: str) -> UrlVerdict:
    if not url or not url.strip():
        return UrlVerdict(False, "empty URL")
    parsed = urlsplit(url.strip())

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return UrlVerdict(False, f"scheme '{scheme or '(none)'}' is not http(s)")

    if parsed.username is not None or parsed.password is not None:
        return UrlVerdict(False, "URL embeds userinfo (user:pass@host)")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return UrlVerdict(False, "URL has no hostname")

    if hostname in _BLOCKED_HOSTNAMES:
        return UrlVerdict(False, f"'{hostname}' is a cloud metadata endpoint")

    ip = _parse_ip_literal(hostname)
    if ip is not None:
        return _judge_ips(hostname, [ip], literal=True)

    resolved = _resolve_host(hostname)
    if resolved is None:
        # Deliberate fail-open: an unresolvable host can't be fetched either,
        # so blocking adds nothing — see module docstring.
        return UrlVerdict(True, f"allowed: '{hostname}' did not resolve (verdict is unverified)")

    ips: List[_IPAddress] = []
    for ip_str in resolved:
        try:
            ips.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    return _judge_ips(hostname, ips, literal=False)


def _judge_ips(hostname: str, ips: List[_IPAddress], literal: bool) -> UrlVerdict:
    via = "is" if literal else "resolves to"
    for ip in ips:
        if _is_metadata_ip(ip):
            logger.warning("Blocked URL: %s %s metadata endpoint %s", hostname, via, ip)
            return UrlVerdict(False, f"'{hostname}' {via} cloud metadata address {ip}")
        if _is_blocked_ip(ip):
            logger.warning("Blocked URL: %s %s private address %s", hostname, via, ip)
            return UrlVerdict(False, f"'{hostname}' {via} private/internal address {ip}")
    return UrlVerdict(True, "allowed")


__all__ = ["UrlVerdict", "check_url"]
