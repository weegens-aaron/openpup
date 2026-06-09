"""Tests for OpenPup agent tools (send_message, check_email, list_platforms)."""

from unittest.mock import AsyncMock

import pytest

from openpup import agent_tools
from openpup.messaging.registry import PlatformRegistry


class FakeAgent:
    """Captures @agent.tool-decorated functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, func):
        self.tools[func.__name__] = func
        return func


class FakeAdapter:
    def __init__(self, name):
        self.name = name
        self.sent = []

    async def send(self, envelope):
        self.sent.append(envelope)


def test_advertise_and_registry_shape(monkeypatch):
    openpup_tools = {
        "openpup_send_message",
        "openpup_check_email",
        "openpup_list_platforms",
    }
    # register_tools only defines OpenPup's own tools (not core's UC tool).
    defs = agent_tools.register_tools_callback()
    assert {d["name"] for d in defs} == openpup_tools
    assert all(callable(d["register_func"]) for d in defs)
    # advertise always includes OpenPup's tools (plus UC when enabled).
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: False)
    assert set(agent_tools.advertise_tools()) == openpup_tools


def test_identity_prompt_mentions_openpup():
    prompt = agent_tools.openpup_identity_prompt()
    assert prompt and "OpenPup" in prompt
    assert "openpup_send_message" in prompt


@pytest.mark.asyncio
async def test_send_message_ok(monkeypatch):
    reg = PlatformRegistry()
    adapter = FakeAdapter("telegram")
    reg.register(adapter)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_send_message(agent)
    result = await agent.tools["openpup_send_message"](None, "telegram:123", "hi there")
    assert result.ok is True
    assert adapter.sent[0].text == "hi there"


@pytest.mark.asyncio
async def test_send_message_unknown_platform(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_send_message(agent)
    result = await agent.tools["openpup_send_message"](None, "telegram:1", "x")
    assert result.ok is False
    assert "not connected" in result.error


@pytest.mark.asyncio
async def test_send_message_bad_address(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_send_message(agent)
    result = await agent.tools["openpup_send_message"](None, "noformat", "x")
    assert result.ok is False
    assert "platform:channel" in result.error


@pytest.mark.asyncio
async def test_check_email_not_connected(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_check_email(agent)
    result = await agent.tools["openpup_check_email"](None, 5)
    assert result.count == 0
    assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_check_email_returns_items(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.fetch_recent = AsyncMock(
        return_value=[
            {"from_addr": "a@x.com", "subject": "Hi", "date": "today", "preview": "hello"}
        ]
    )
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_check_email(agent)
    result = await agent.tools["openpup_check_email"](None, 5)
    assert result.count == 1
    assert result.emails[0].subject == "Hi"


@pytest.mark.asyncio
async def test_list_platforms(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("telegram"))
    reg.register(FakeAdapter("email"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_list_platforms(agent)
    result = await agent.tools["openpup_list_platforms"](None)
    assert set(result.platforms) == {"telegram", "email"}
