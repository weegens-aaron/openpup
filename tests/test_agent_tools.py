"""Tests for OpenPup agent tools (send_message, check_email, list_platforms)."""

from unittest.mock import AsyncMock

import pytest

from openpup import access, agent_tools
from openpup.messaging.registry import PlatformRegistry


@pytest.fixture(autouse=True)
def _as_owner():
    """Default tests to owner privileges; individual tests can override."""
    access.set_current_role(access.OWNER)
    yield
    access.set_current_role(access.ALLOWED)


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
        "openpup_unread_email",
        "openpup_search_email",
        "openpup_delete_email",
        "openpup_list_platforms",
        "openpup_config",
        "openpup_contacts",
        "openpup_session_search",
        "openpup_skill",
    }
    # register_tools only defines OpenPup's own tools (not core's UC tool).
    defs = agent_tools.register_tools_callback()
    assert {d["name"] for d in defs} == openpup_tools
    assert all(callable(d["register_func"]) for d in defs)
    # advertise always includes OpenPup's tools (plus UC when enabled).
    monkeypatch.setattr(agent_tools, "_uc_enabled", lambda: False)
    assert set(agent_tools.advertise_tools()) == openpup_tools


@pytest.mark.asyncio
async def test_send_message_ok(monkeypatch):
    import openpup.governance as governance_mod
    from openpup.governance import SendPolicy

    reg = PlatformRegistry()
    adapter = FakeAdapter("telegram")
    reg.register(adapter)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    # Isolate from the real .env send policy.
    monkeypatch.setattr(governance_mod, "get_send_policy", lambda: SendPolicy(per_minute=100))

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
async def test_unread_email_returns_items(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.fetch_unread = AsyncMock(
        return_value=[
            {
                "from_addr": "a@x.com",
                "subject": "Unseen",
                "date": "today",
                "preview": "new",
                "uid": "9",
            }
        ]
    )
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_unread_email(agent)
    result = await agent.tools["openpup_unread_email"](None, 10)
    assert result.count == 1
    assert result.emails[0].subject == "Unseen"
    email.fetch_unread.assert_awaited_once_with(limit=10)


@pytest.mark.asyncio
async def test_unread_email_clamps_limit(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.fetch_unread = AsyncMock(return_value=[])
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_unread_email(agent)
    await agent.tools["openpup_unread_email"](None, 999)
    email.fetch_unread.assert_awaited_once_with(limit=50)


@pytest.mark.asyncio
async def test_unread_email_not_connected(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_unread_email(agent)
    result = await agent.tools["openpup_unread_email"](None)
    assert result.count == 0
    assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_unread_email_blocked_for_non_owner(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("email"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_unread_email(agent)
    result = await agent.tools["openpup_unread_email"](None)
    assert result.count == 0
    assert "owner" in result.error.lower()


@pytest.mark.asyncio
async def test_config_tool_set_get_list_and_masks_secrets(monkeypatch, tmp_path):
    import openpup.config_store as cs

    store = cs.ConfigStore(tmp_path / "config.db")
    monkeypatch.setattr(cs, "get_config_store", lambda: store)

    agent = FakeAgent()
    agent_tools.register_config(agent)
    tool = agent.tools["openpup_config"]

    # set a normal key + a secret key
    r = await tool(None, action="set", key="OPENPUP_SEND_POLICY", value="owner_only")
    assert r.ok and r.action == "set"
    r = await tool(None, action="set", key="EMAIL_PASSWORD", value="hunter2")
    assert r.ok

    # get masks the secret, shows the normal value
    assert (await tool(None, action="get", key="OPENPUP_SEND_POLICY")).value == "owner_only"
    assert (await tool(None, action="get", key="EMAIL_PASSWORD")).value == "***set***"

    # list masks secrets too
    listing = await tool(None, action="list")
    assert listing.config["OPENPUP_SEND_POLICY"] == "owner_only"
    assert listing.config["EMAIL_PASSWORD"] == "***set***"


@pytest.mark.asyncio
async def test_config_tool_rejects_unknown_key(monkeypatch, tmp_path):
    import openpup.config_store as cs

    store = cs.ConfigStore(tmp_path / "config.db")
    monkeypatch.setattr(cs, "get_config_store", lambda: store)
    agent = FakeAgent()
    agent_tools.register_config(agent)
    r = await agent.tools["openpup_config"](None, action="set", key="NONSENSE", value="x")
    assert r.ok is False
    assert "unknown config key" in r.error.lower()


@pytest.mark.asyncio
async def test_config_tool_blocked_for_non_owner(monkeypatch):
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_config(agent)
    r = await agent.tools["openpup_config"](None, action="list")
    assert r.ok is False
    assert "owner" in r.error.lower()


@pytest.mark.asyncio
async def test_search_email_returns_items_and_passes_filters(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.search = AsyncMock(
        return_value=[
            {
                "from_addr": "billing@amazon.com",
                "subject": "Your invoice",
                "date": "today",
                "preview": "total due",
                "uid": "55",
            }
        ]
    )
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_search_email(agent)
    result = await agent.tools["openpup_search_email"](
        None, query="invoice", from_addr="amazon.com", since_days=7, limit=5, unread=True
    )
    assert result.count == 1
    assert result.emails[0].uid == "55"
    email.search.assert_awaited_once_with(
        query="invoice", from_addr="amazon.com", since_days=7, limit=5, unread=True
    )


@pytest.mark.asyncio
async def test_search_email_clamps_limit(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.search = AsyncMock(return_value=[])
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_search_email(agent)
    await agent.tools["openpup_search_email"](None, query="x", limit=999)
    assert email.search.await_args.kwargs["limit"] == 50
    # unread defaults to False when not requested
    assert email.search.await_args.kwargs["unread"] is False


@pytest.mark.asyncio
async def test_search_email_not_connected(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_search_email(agent)
    result = await agent.tools["openpup_search_email"](None, query="x")
    assert result.count == 0
    assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_search_email_blocked_for_non_owner(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("email"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_search_email(agent)
    result = await agent.tools["openpup_search_email"](None, query="x")
    assert result.count == 0
    assert "owner" in result.error.lower()


@pytest.mark.asyncio
async def test_delete_email_moves_to_trash(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.delete = AsyncMock(
        return_value={
            "deleted": 2,
            "uids": ["10", "11"],
            "subjects": ["Spam", "More spam"],
            "mode": "trash",
            "missing": [],
        }
    )
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_delete_email(agent)
    result = await agent.tools["openpup_delete_email"](None, ["10", "11"])
    assert result.ok is True
    assert result.deleted == 2
    assert result.mode == "trash"
    # default is the reversible path, not a permanent expunge
    email.delete.assert_awaited_once_with(["10", "11"], permanent=False)


@pytest.mark.asyncio
async def test_delete_email_permanent_flag_passes_through(monkeypatch):
    reg = PlatformRegistry()
    email = FakeAdapter("email")
    email.delete = AsyncMock(
        return_value={"deleted": 1, "uids": ["7"], "subjects": ["x"], "mode": "permanent", "missing": []}
    )
    reg.register(email)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    agent = FakeAgent()
    agent_tools.register_delete_email(agent)
    # a lone uid string should be accepted too
    result = await agent.tools["openpup_delete_email"](None, "7", permanent=True)
    assert result.ok is True
    assert result.mode == "permanent"
    email.delete.assert_awaited_once_with(["7"], permanent=True)


@pytest.mark.asyncio
async def test_delete_email_requires_uids(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("email"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_delete_email(agent)
    result = await agent.tools["openpup_delete_email"](None, [])
    assert result.ok is False
    assert "uid" in result.error.lower()


@pytest.mark.asyncio
async def test_delete_email_not_connected(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    agent = FakeAgent()
    agent_tools.register_delete_email(agent)
    result = await agent.tools["openpup_delete_email"](None, ["1"])
    assert result.ok is False
    assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_delete_email_blocked_for_non_owner(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("email"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_delete_email(agent)
    result = await agent.tools["openpup_delete_email"](None, ["1"])
    assert result.ok is False
    assert "owner" in result.error.lower()


@pytest.mark.asyncio
async def test_send_message_blocked_for_non_owner(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("telegram"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_send_message(agent)
    result = await agent.tools["openpup_send_message"](None, "telegram:1", "hi")
    assert result.ok is False
    assert "owner" in result.error.lower()


@pytest.mark.asyncio
async def test_check_email_blocked_for_non_owner(monkeypatch):
    reg = PlatformRegistry()
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner
    agent = FakeAgent()
    agent_tools.register_check_email(agent)
    result = await agent.tools["openpup_check_email"](None, 5)
    assert result.count == 0
    assert "owner" in result.error.lower()


@pytest.mark.asyncio
async def test_send_message_resolves_contact_name(monkeypatch, tmp_path):
    import openpup.directory as directory_mod
    import openpup.governance as governance_mod
    from openpup.directory import ContactDirectory
    from openpup.governance import SendPolicy

    reg = PlatformRegistry()
    adapter = FakeAdapter("telegram")
    reg.register(adapter)
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)

    d = ContactDirectory(tmp_path / "contacts.json")
    d.record("telegram", "111", "Mike")
    monkeypatch.setattr(directory_mod, "get_directory", lambda: d)
    monkeypatch.setattr(governance_mod, "get_send_policy", lambda: SendPolicy(per_minute=100))

    agent = FakeAgent()
    agent_tools.register_send_message(agent)
    result = await agent.tools["openpup_send_message"](None, "Mike", "hi mike")
    assert result.ok is True
    assert result.address == "telegram:111"
    assert adapter.sent[0].text == "hi mike"


@pytest.mark.asyncio
async def test_contacts_tool_lists(monkeypatch, tmp_path):
    import openpup.directory as directory_mod
    from openpup.directory import ContactDirectory

    d = ContactDirectory(tmp_path / "contacts.json")
    d.record("telegram", "111", "Mike")
    monkeypatch.setattr(directory_mod, "get_directory", lambda: d)

    agent = FakeAgent()
    agent_tools.register_contacts(agent)
    out = await agent.tools["openpup_contacts"](None)
    assert out.count == 1
    assert out.contacts[0].address == "telegram:111"


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


@pytest.mark.asyncio
async def test_contacts_blocked_for_non_owner(monkeypatch, tmp_path):
    import openpup.directory as directory_mod
    from openpup.directory import ContactDirectory

    d = ContactDirectory(tmp_path / "contacts.json")
    d.record("telegram", "111", "Mike")
    monkeypatch.setattr(directory_mod, "get_directory", lambda: d)
    access.set_current_role(access.ALLOWED)  # non-owner

    agent = FakeAgent()
    agent_tools.register_contacts(agent)
    out = await agent.tools["openpup_contacts"](None)
    assert out.count == 0 and not out.contacts
    assert "owner" in out.error.lower()


@pytest.mark.asyncio
async def test_list_platforms_hides_owner_address_from_non_owner(monkeypatch):
    reg = PlatformRegistry()
    reg.register(FakeAdapter("telegram"))
    monkeypatch.setattr(agent_tools, "get_registry", lambda: reg)
    access.set_current_role(access.ALLOWED)  # non-owner

    agent = FakeAgent()
    agent_tools.register_list_platforms(agent)
    result = await agent.tools["openpup_list_platforms"](None)
    assert result.platforms == ["telegram"]
    assert result.owner is None
