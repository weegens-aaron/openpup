"""Agent-facing stealth-browser tooling (CloakBrowser).

Gives the pup a real, fingerprint-evading browser to fetch pages that block
plain HTTP clients (Cloudflare/Turnstile/DataDome/etc). Built on CloakBrowser
(https://github.com/CloakHQ/cloakbrowser) -- a drop-in stealth Playwright
Chromium.

Design choices (kept deliberately tight):
* **Owner-only.** A stealth browser is a powerful, abusable capability (it's
  literally built to evade bot detection), so -- like email/scheduling -- only
  the owner may drive it. Easy to relax later if you want.
* **SSRF-guarded.** Every URL goes through ``security.url_safety.check_url``
  before we launch anything, closing the agent-controllable-fetch hole noted
  in ``webserver.py``.
* **Lazy + optional.** CloakBrowser is an optional dependency (``openpup[browser]``).
  Import is deferred so OpenPup runs fine without it; the tool returns a
  friendly "not installed" message instead of blowing up at boot.

Tool:
* ``openpup_browse(url, ...)`` -- fetch a page with the stealth browser and
  return its title + visible text (and optionally a screenshot).
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel
from pydantic_ai import RunContext


def _is_owner() -> bool:
    """Whether the message currently being served is from the owner.

    Fails CLOSED: if access can't be determined, nobody gets the browser.
    """
    try:
        from openpup.access import current_is_owner

        return current_is_owner()
    except Exception:
        return False


class BrowseResult(BaseModel):
    ok: bool
    url: str = ""
    final_url: str = ""
    title: str = ""
    text: str = ""
    screenshot_path: str = ""
    error: Optional[str] = None


# Default cap on returned page text so we don't blow the model's context.
_DEFAULT_MAX_CHARS = 8000
_MAX_MAX_CHARS = 50000


async def _launch_context(**kwargs: Any) -> Any:
    """Launch a CloakBrowser stealth browser context (indirection seam for tests).

    Uses ``launch_context_async`` rather than raw ``launch_async`` so that
    viewport, locale, and other context-level stealth settings are applied
    automatically.  Without this, Playwright's default 1280×720 viewport
    leaks an obvious automation signal.

    Raises ImportError if the optional dependency isn't installed; the caller
    turns that into a friendly message.
    """
    from cloakbrowser import launch_context_async

    return await launch_context_async(**kwargs)


def register_browse(agent: Any) -> None:
    @agent.tool
    async def openpup_browse(
        context: RunContext,
        url: str,
        max_chars: int = _DEFAULT_MAX_CHARS,
        wait_until: str = "domcontentloaded",
        screenshot_path: str = "",
    ) -> BrowseResult:
        """Fetch a web page with a STEALTH browser and return its text. Owner-only.

        Use this when a normal fetch would be blocked by bot detection
        (Cloudflare, Turnstile, DataDome, reCAPTCHA walls, "enable JavaScript"
        pages, etc.) or when a page needs JS to render. It drives a real
        fingerprint-evading Chromium, so it's heavier than a plain GET -- reach
        for it when you actually need it, not for every URL.

        Args:
            url: The http(s) URL to load. Private/loopback/metadata addresses
                are refused (SSRF guard).
            max_chars: Max characters of visible page text to return
                (default 8000, capped at 50000). Text is truncated, not
                summarized -- summarize it yourself if needed.
            wait_until: Playwright load state to wait for: "domcontentloaded"
                (default, fast), "load", or "networkidle" (slow, for heavy SPAs).
            screenshot_path: If set, also save a full-page PNG screenshot to
                this path and return it (handy for visual/CAPTCHA pages).

        Returns the page title, final URL (after redirects), and visible text.
        """
        if not _is_owner():
            return BrowseResult(ok=False, url=url, error="Only the owner can use the browser.")

        # SSRF pre-flight: never launch a browser at a private/metadata target.
        from openpup.security.url_safety import check_url

        verdict = check_url(url)
        if not verdict.allowed:
            return BrowseResult(ok=False, url=url, error=f"URL blocked: {verdict.reason}")

        try:
            cap = max(1, min(int(max_chars), _MAX_MAX_CHARS))
        except (TypeError, ValueError):
            cap = _DEFAULT_MAX_CHARS

        ctx = None
        try:
            ctx = await _launch_context(headless=True)
        except ImportError:
            return BrowseResult(
                ok=False,
                url=url,
                error=(
                    "The stealth browser isn't installed. Install it with "
                    "'pip install openpup[browser]' (or 'pip install cloakbrowser'); "
                    "the stealth Chromium binary downloads automatically on first use."
                ),
            )
        except Exception as exc:  # noqa: BLE001 — tools must never raise
            return BrowseResult(ok=False, url=url, error=f"could not launch browser: {exc!r}")

        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until=wait_until)
            title = await page.title()
            text = await page.evaluate("document.body ? document.body.innerText : ''")
            final_url = page.url
            saved = ""
            if screenshot_path:
                await page.screenshot(path=screenshot_path, full_page=True)
                saved = screenshot_path
        except Exception as exc:  # noqa: BLE001
            return BrowseResult(ok=False, url=url, error=f"browse failed: {exc!r}")
        finally:
            try:
                await ctx.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

        text = (text or "").strip()
        if len(text) > cap:
            text = text[:cap] + f"\n\n[... truncated at {cap} chars ...]"
        return BrowseResult(
            ok=True,
            url=url,
            final_url=final_url or url,
            title=title or "",
            text=text,
            screenshot_path=saved,
        )


_TOOL_NAMES = ("openpup_browse",)


def register_tools_callback() -> List[dict]:
    """``register_tools`` hook — define the stealth-browser tool."""
    return [{"name": "openpup_browse", "register_func": register_browse}]


def advertise_tools(agent_name: Optional[str] = None) -> List[str]:
    """``register_agent_tools`` hook — advertise the browser tool to the agent."""
    return list(_TOOL_NAMES)
