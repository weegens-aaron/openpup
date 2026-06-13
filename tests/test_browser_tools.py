"""Tests for the stealth-browser tool (openpup_browse / CloakBrowser).

CloakBrowser itself is mocked via the ``_launch`` indirection seam, so these
run with no browser binary, no network, and no optional dependency installed.
"""

import pytest

from openpup import access, browser_tools


class FakeAgent:
    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


class FakePage:
    def __init__(self, *, title="Example", text="hello world", final_url=None):
        self._title = title
        self._text = text
        self.url = final_url or "https://example.com/"
        self.goto_args = None
        self.screenshotted = None

    async def goto(self, url, wait_until="domcontentloaded"):
        self.goto_args = (url, wait_until)

    async def title(self):
        return self._title

    async def evaluate(self, _script):
        return self._text

    async def screenshot(self, path, full_page=False):
        self.screenshotted = (path, full_page)


class FakeContext:
    """Stands in for the BrowserContext returned by launch_context_async."""

    def __init__(self, page):
        self._page = page
        self.closed = False

    async def new_page(self):
        return self._page

    async def close(self):
        self.closed = True


@pytest.fixture
def owner():
    access.set_current_role(access.OWNER)
    yield
    access.set_current_role(access.ALLOWED)


def _tool():
    agent = FakeAgent()
    browser_tools.register_browse(agent)
    return agent.tools["openpup_browse"]


def _allow_all_urls(monkeypatch):
    """Skip real DNS/SSRF resolution — verdict logic is tested elsewhere."""
    from openpup.security.url_safety import UrlVerdict

    monkeypatch.setattr(
        browser_tools, "check_url", lambda url: UrlVerdict(True, "ok"), raising=False
    )
    # check_url is imported lazily inside the tool, so patch the source module too.
    import openpup.security.url_safety as us

    monkeypatch.setattr(us, "check_url", lambda url: UrlVerdict(True, "ok"))


@pytest.mark.asyncio
async def test_browse_happy_path(monkeypatch, owner):
    _allow_all_urls(monkeypatch)
    page = FakePage(title="Hello", text="the page text", final_url="https://example.com/final")
    ctx = FakeContext(page)

    async def fake_launch(**kw):
        assert kw.get("headless") is True
        return ctx

    monkeypatch.setattr(browser_tools, "_launch_context", fake_launch)

    out = await _tool()(None, "https://example.com")
    assert out.ok is True
    assert out.title == "Hello"
    assert out.text == "the page text"
    assert out.final_url == "https://example.com/final"
    assert page.goto_args == ("https://example.com", "domcontentloaded")
    assert ctx.closed is True  # cleanup always runs


@pytest.mark.asyncio
async def test_browse_truncates_long_text(monkeypatch, owner):
    _allow_all_urls(monkeypatch)
    page = FakePage(text="x" * 5000)
    monkeypatch.setattr(browser_tools, "_launch_context", lambda **kw: _async(FakeContext(page)))

    out = await _tool()(None, "https://example.com", max_chars=100)
    assert out.ok is True
    assert out.text.startswith("x" * 100)
    assert "truncated at 100 chars" in out.text


@pytest.mark.asyncio
async def test_browse_screenshot(monkeypatch, owner):
    _allow_all_urls(monkeypatch)
    page = FakePage()
    monkeypatch.setattr(browser_tools, "_launch_context", lambda **kw: _async(FakeContext(page)))

    out = await _tool()(None, "https://example.com", screenshot_path="/tmp/shot.png")
    assert out.screenshot_path == "/tmp/shot.png"
    assert page.screenshotted == ("/tmp/shot.png", True)


@pytest.mark.asyncio
async def test_browse_blocked_for_non_owner(monkeypatch):
    access.set_current_role(access.ALLOWED)
    out = await _tool()(None, "https://example.com")
    assert out.ok is False
    assert "owner" in out.error.lower()


@pytest.mark.asyncio
async def test_browse_blocks_ssrf(owner):
    # No URL allow-listing here: the real SSRF guard must refuse a metadata IP.
    out = await _tool()(None, "http://169.254.169.254/latest/meta-data/")
    assert out.ok is False
    assert "blocked" in out.error.lower()


@pytest.mark.asyncio
async def test_browse_not_installed(monkeypatch, owner):
    _allow_all_urls(monkeypatch)

    async def boom(**kw):
        raise ImportError("No module named 'cloakbrowser'")

    monkeypatch.setattr(browser_tools, "_launch_context", boom)
    out = await _tool()(None, "https://example.com")
    assert out.ok is False
    assert "install" in out.error.lower()


def test_advertise_and_register():
    assert browser_tools.advertise_tools() == ["openpup_browse"]
    reg = browser_tools.register_tools_callback()
    assert reg[0]["name"] == "openpup_browse"


async def _async(value):
    """Tiny coroutine wrapper so a plain value can stand in for an async call."""
    return value
