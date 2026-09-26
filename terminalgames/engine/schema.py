"""Strict shape checks for story files (chapters, network.yaml, npcs.yaml).

Unknown keys and invalid values are load errors, with a did-you-mean hint,
instead of being silently ignored -- a misspelled `requries:` would
otherwise quietly delete a gate, and nothing downstream could tell.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable

from .journal import VALID_CATEGORIES


class StoryLoadError(Exception):
    pass


def check_keys(data: Any, allowed: Iterable[str], where: str, required: Iterable[str] = ()) -> None:
    if not isinstance(data, dict):
        raise StoryLoadError(f"{where}: expected a mapping, got {type(data).__name__}")
    allowed = sorted(allowed)
    for key in data:
        if key not in allowed:
            hint = difflib.get_close_matches(str(key), allowed, n=1)
            suggestion = f"did you mean '{hint[0]}'?" if hint else f"expected one of: {', '.join(allowed)}"
            raise StoryLoadError(f"{where}: unknown key '{key}' ({suggestion})")
    for key in required:
        if key not in data:
            raise StoryLoadError(f"{where}: missing required key '{key}'")


def check_value(value: Any, allowed: Iterable[str], what: str, where: str) -> None:
    allowed = sorted(allowed)
    if value not in allowed:
        hint = difflib.get_close_matches(str(value), allowed, n=1)
        suggestion = f"did you mean '{hint[0]}'?" if hint else f"expected one of: {', '.join(allowed)}"
        raise StoryLoadError(f"{where}: invalid {what} '{value}' ({suggestion})")


def check_list(data: Any, where: str) -> list[Any]:
    if not isinstance(data, list):
        raise StoryLoadError(f"{where}: expected a list, got {type(data).__name__}")
    return data


REQUIRES_KEYS = {"flag", "flag_equals", "tool", "journal_has", "trust_at_least", "all", "any", "not"}


def check_requires(requires: Any, where: str) -> None:
    if requires is None:
        return
    where = f"{where} requires"
    check_keys(requires, REQUIRES_KEYS, where)
    if "flag_equals" in requires:
        check_keys(
            requires["flag_equals"], {"key", "value"}, f"{where} flag_equals", required={"key", "value"}
        )
    if "trust_at_least" in requires:
        ta_where = f"{where} trust_at_least"
        check_keys(requires["trust_at_least"], {"npc", "value"}, ta_where, required={"npc", "value"})
        if not isinstance(requires["trust_at_least"]["value"], int):
            raise StoryLoadError(f"{ta_where}: value must be a whole number")
    for combinator in ("all", "any"):
        if combinator in requires:
            for sub in check_list(requires[combinator], f"{where} {combinator}"):
                check_requires(sub, f"{where} {combinator}")
    if "not" in requires:
        check_requires(requires["not"], f"{where} not")


def check_sets(sets: Any, where: str) -> None:
    if sets is None:
        return
    if not isinstance(sets, dict):  # any flag name is a valid key
        raise StoryLoadError(f"{where} sets: expected a mapping, got {type(sets).__name__}")
    for key, value in sets.items():
        if str(key).startswith("trust.") and (not isinstance(value, int) or isinstance(value, bool)):
            raise StoryLoadError(f"{where} sets: '{key}' must be a whole number (a trust adjustment)")


LOG_KEYS = {"id", "category", "text", "related_entry_ids"}


def check_logs(logs: Any, where: str) -> None:
    if logs is None:
        return
    for i, entry in enumerate(check_list(logs, f"{where} logs"), start=1):
        entry_where = f"{where} log entry {i}"
        check_keys(entry, LOG_KEYS, entry_where, required={"id", "text"})
        check_value(entry.get("category", "note"), VALID_CATEGORIES, "journal category", entry_where)


def check_effects(data: dict[str, Any], where: str) -> None:
    """The requires/sets/logs trio shared by choices and NPC topics."""
    check_requires(data.get("requires"), where)
    check_sets(data.get("sets"), where)
    check_logs(data.get("logs"), where)
