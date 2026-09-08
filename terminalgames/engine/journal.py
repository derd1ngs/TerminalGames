"""Structured, engine-tracked evidence log the player can review.

Distinct from player notes (engine/shell.py `notes` command): journal entries
are added by story content and read by engine logic (to gate hub choices,
NPC topics, etc). Notes are free-form and never parsed by the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

VALID_CATEGORIES = {"lead", "trace", "suspect", "note"}


@dataclass
class JournalEntry:
    id: str
    category: str
    text: str
    discovered_at: str = ""
    related_entry_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "text": self.text,
            "discovered_at": self.discovered_at,
            "related_entry_ids": list(self.related_entry_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JournalEntry":
        return cls(
            id=data["id"],
            category=data["category"],
            text=data["text"],
            discovered_at=data.get("discovered_at", ""),
            related_entry_ids=list(data.get("related_entry_ids", [])),
        )


class Journal:
    def __init__(self, entries: Optional[list[JournalEntry]] = None):
        self._entries: list[JournalEntry] = list(entries or [])

    def add(self, entry: JournalEntry) -> None:
        if self.has(entry.id):
            return
        self._entries.append(entry)

    def has(self, entry_id: str) -> bool:
        return any(e.id == entry_id for e in self._entries)

    def count(self, category: Optional[str] = None) -> int:
        if category is None:
            return len(self._entries)
        return sum(1 for e in self._entries if e.category == category)

    def by_category(self, category: str) -> list[JournalEntry]:
        return [e for e in self._entries if e.category == category]

    def all(self) -> list[JournalEntry]:
        return list(self._entries)

    def to_dict(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    @classmethod
    def from_dict(cls, data: Optional[list[dict[str, Any]]]) -> "Journal":
        return cls([JournalEntry.from_dict(d) for d in (data or [])])
