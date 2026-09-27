"""Reusable, story-parameterized puzzle primitives.

These are pure functions with no dependency on the fake terminal or story
graph, so they're trivial to unit test in isolation. `shell.py` commands
(`systemctl`, `decrypt`, `grep`) call into these to do the actual validation.
"""

from __future__ import annotations

from typing import Optional


def validate_config(current: dict[str, str], required: dict[str, str]) -> tuple[bool, Optional[str]]:
    """Config-edit-and-restart puzzle: does `current` satisfy every required key/value?"""
    for key, expected in required.items():
        actual = current.get(key)
        if actual != expected:
            return False, f"{key} is set to '{actual}', expected '{expected}'."
    return True, None


def parse_config_text(text: str) -> dict[str, str]:
    """Parse a real `key=value`-per-line config file's content."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def render_config_text(values: dict[str, str]) -> str:
    """The inverse of `parse_config_text` -- also what a config file looks
    like the moment it's materialized from story content."""
    return "\n".join(f"{k}={v}" for k, v in values.items())


def grep_lines(text: str, pattern: str) -> list[str]:
    """Log-grep puzzle: substring-match lines, like a simplified `grep`."""
    return [line for line in text.splitlines() if pattern in line]


def caesar_decode(text: str, shift: int) -> str:
    result = []
    for ch in text:
        if ch.isalpha():
            base = ord("A") if ch.isupper() else ord("a")
            result.append(chr((ord(ch) - base - shift) % 26 + base))
        else:
            result.append(ch)
    return "".join(result)


def xor_decode_hex(hex_ciphertext: str, key: str) -> str:
    try:
        data = bytes.fromhex(hex_ciphertext)
    except ValueError:
        return ""
    key_bytes = key.encode("utf-8") or b"\x00"
    decoded = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return decoded.decode("utf-8", errors="replace")


def decode_cipher(cipher: str, ciphertext: str, key: str) -> str:
    """Cipher-decode puzzle. Player supplies a guessed key; wrong guesses just
    produce garbage rather than raising, so 'trying things' stays in-fiction."""
    if cipher == "caesar":
        try:
            shift = int(key)
        except ValueError:
            return ""
        return caesar_decode(ciphertext, shift)
    if cipher == "xor":
        return xor_decode_hex(ciphertext, key)
    return ""


def check_command_order(submitted: list[str], expected: list[str]) -> bool:
    """Command-ordering puzzle: did the player run the right commands in the right sequence?"""
    return list(submitted) == list(expected)
