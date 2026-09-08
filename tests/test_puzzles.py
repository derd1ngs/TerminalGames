from terminalgames.engine.puzzles import (
    caesar_decode,
    check_command_order,
    decode_cipher,
    grep_lines,
    validate_config,
    xor_decode_hex,
)


def test_validate_config_success():
    ok, reason = validate_config({"a": "1", "b": "2"}, {"a": "1", "b": "2"})
    assert ok is True
    assert reason is None


def test_validate_config_failure_reports_reason():
    ok, reason = validate_config({"a": "1", "b": "wrong"}, {"a": "1", "b": "2"})
    assert ok is False
    assert "b" in reason


def test_grep_lines():
    text = "one\ntwo needle\nthree"
    assert grep_lines(text, "needle") == ["two needle"]
    assert grep_lines(text, "missing") == []


def test_caesar_decode_roundtrip():
    plaintext = "Hello World"
    shift = 5
    encoded = "".join(
        chr((ord(c) - (65 if c.isupper() else 97) + shift) % 26 + (65 if c.isupper() else 97))
        if c.isalpha()
        else c
        for c in plaintext
    )
    assert caesar_decode(encoded, shift) == plaintext


def test_decode_cipher_caesar():
    assert decode_cipher("caesar", "KFQHTS", 5) is not None
    assert decode_cipher("caesar", "abc", "not-a-number") == ""


def test_decode_cipher_xor():
    key = "k"
    plaintext = "secret"
    ciphertext_hex = bytes(b ^ ord(key) for b in plaintext.encode()).hex()
    assert decode_cipher("xor", ciphertext_hex, key) == plaintext
    assert xor_decode_hex("not-hex", key) == ""


def test_check_command_order():
    assert check_command_order(["scan", "connect"], ["scan", "connect"]) is True
    assert check_command_order(["connect", "scan"], ["scan", "connect"]) is False
