"""Live credential validators.

Each returns ``(ok: bool, detail: str)`` where ``detail`` is a human-friendly
success summary (e.g. the bot's username) or an error explanation. These make
the setup wizard "on-rails": you can't save a broken credential.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Tuple

import httpx

TIMEOUT = 15.0


async def validate_telegram(token: str) -> Tuple[bool, str]:
    if not token:
        return False, "No token provided."
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            u = data["result"]
            return True, f"Connected as @{u.get('username')} ({u.get('first_name')})"
        return False, f"Telegram rejected the token: {data.get('description', r.text[:200])}"
    except Exception as exc:
        return False, f"Network error: {exc}"


async def telegram_discover_chat_id(token: str) -> Optional[str]:
    """Return the chat id of the most recent message sent to the bot, if any."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getUpdates")
        data = r.json()
        for upd in reversed(data.get("result", [])):
            msg = upd.get("message") or upd.get("edited_message")
            if msg and msg.get("chat"):
                return str(msg["chat"]["id"])
    except Exception:
        return None
    return None


async def validate_discord(token: str) -> Tuple[bool, str]:
    if not token:
        return False, "No token provided."
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
            )
        if r.status_code == 200:
            u = r.json()
            return True, f"Connected as {u.get('username')}#{u.get('discriminator', '0')}"
        return False, f"Discord rejected the token (HTTP {r.status_code}): {r.text[:200]}"
    except Exception as exc:
        return False, f"Network error: {exc}"


async def validate_twilio(sid: str, auth_token: str) -> Tuple[bool, str]:
    if not (sid and auth_token):
        return False, "Need both Account SID and Auth Token."
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                auth=(sid, auth_token),
            )
        if r.status_code == 200:
            data = r.json()
            return True, f"Twilio account OK: {data.get('friendly_name')} ({data.get('status')})"
        return False, f"Twilio rejected the credentials (HTTP {r.status_code})."
    except Exception as exc:
        return False, f"Network error: {exc}"


async def validate_whatsapp(phone_number_id: str, access_token: str) -> Tuple[bool, str]:
    if not (phone_number_id and access_token):
        return False, "Need both Phone Number ID and Access Token."
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"https://graph.facebook.com/v19.0/{phone_number_id}",
                params={
                    "fields": "verified_name,display_phone_number",
                    "access_token": access_token,
                },
            )
        if r.status_code == 200:
            data = r.json()
            name = data.get("verified_name", "?")
            num = data.get("display_phone_number", "?")
            return True, f"WhatsApp number OK: {name} ({num})"
        return False, f"Meta rejected the credentials (HTTP {r.status_code}): {r.text[:200]}"
    except Exception as exc:
        return False, f"Network error: {exc}"


async def validate_email(
    imap_host: str, imap_port: int, username: str, password: str
) -> Tuple[bool, str]:
    if not (imap_host and username and password):
        return False, "Need IMAP host, username and password."

    def _check() -> Tuple[bool, str]:
        try:
            from imap_tools import MailBox

            with MailBox(imap_host, imap_port).login(username, password):
                return True, f"IMAP login OK for {username}"
        except Exception as exc:
            return False, f"IMAP login failed: {exc}"

    return await asyncio.to_thread(_check)
