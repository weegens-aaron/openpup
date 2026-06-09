"""Tests for the native macOS iMessage adapter (logic that's OS-independent)."""

import struct

from openpup.platforms.imessage_adapter import applescript_send, decode_attributed_body


def _attributed_blob(text: str) -> bytes:
    """Build a minimal attributedBody-like blob with a 1-byte length prefix."""
    body = text.encode("utf-8")
    return b"\x04\x0bstreamtyped" + b"NSString" + b"\x01\x94\x84\x01+" + bytes([len(body)]) + body


def test_decode_attributed_body_short():
    blob = _attributed_blob("hello there")
    assert decode_attributed_body(blob) == "hello there"


def test_decode_attributed_body_empty():
    assert decode_attributed_body(None) == ""
    assert decode_attributed_body(b"") == ""
    assert decode_attributed_body(b"no marker here") == ""


def test_decode_attributed_body_two_byte_length():
    text = "x" * 300
    body = text.encode("utf-8")
    length = struct.pack("<H", len(body))
    blob = b"NSString" + b"\x01\x94\x84\x01+" + b"\x81" + length + body
    assert decode_attributed_body(blob) == text


def test_applescript_send_command():
    cmd = applescript_send("+15551234567", "hi there")
    assert cmd[0] == "osascript"
    assert cmd[-2] == "+15551234567"
    assert cmd[-1] == "hi there"
    # the script targets the Messages app + iMessage service
    script = cmd[2]
    assert "Messages" in script
    assert "iMessage" in script
