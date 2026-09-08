"""Player save state: progress, flags/tools, journal, trust, and the async email queue."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .journal import Journal


@dataclass
class EmailMessage:
    id: str
    npc_id: str
    subject: str
    body: str
    deliver_after_scene_count: int
    delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "npc_id": self.npc_id,
            "subject": self.subject,
            "body": self.body,
            "deliver_after_scene_count": self.deliver_after_scene_count,
            "delivered": self.delivered,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmailMessage":
        return cls(
            id=data["id"],
            npc_id=data["npc_id"],
            subject=data["subject"],
            body=data["body"],
            deliver_after_scene_count=data["deliver_after_scene_count"],
            delivered=data.get("delivered", False),
        )


@dataclass
class GameState:
    story_id: str
    chapter_id: str
    scene_id: str
    flags: dict[str, Any] = field(default_factory=dict)
    tools: set[str] = field(default_factory=set)
    journal: Journal = field(default_factory=Journal)
    trust: dict[str, int] = field(default_factory=dict)
    ask_counts: dict[str, int] = field(default_factory=dict)
    email_queue: list[EmailMessage] = field(default_factory=list)
    scenes_visited: int = 0
    current_host: Optional[str] = None
    cwd: str = "/"
    saved_at: str = ""

    def set_flag(self, key: str, value: Any = True) -> None:
        self.flags[key] = value

    def has_flag(self, key: str, value: Any = True) -> bool:
        if value is True:
            return bool(self.flags.get(key))
        return self.flags.get(key) == value

    def add_tool(self, tool_id: str) -> None:
        self.tools.add(tool_id)

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self.tools

    def adjust_trust(self, npc_id: str, delta: int) -> int:
        self.trust[npc_id] = self.trust.get(npc_id, 0) + delta
        return self.trust[npc_id]

    def get_trust(self, npc_id: str) -> int:
        return self.trust.get(npc_id, 0)

    def increment_ask_count(self, npc_id: str) -> int:
        self.ask_counts[npc_id] = self.ask_counts.get(npc_id, 0) + 1
        return self.ask_counts[npc_id]

    def get_ask_count(self, npc_id: str) -> int:
        return self.ask_counts.get(npc_id, 0)

    def queue_email(self, message: EmailMessage) -> None:
        if self.scenes_visited >= message.deliver_after_scene_count:
            message.delivered = True
        self.email_queue.append(message)

    def advance_scene(self) -> list[EmailMessage]:
        """Returns the messages that transitioned to delivered on *this*
        call (not ones already delivered earlier), so a caller can react to
        newly-arrived mail -- e.g. materializing a real inbox file."""
        self.scenes_visited += 1
        newly_delivered = []
        for msg in self.email_queue:
            if not msg.delivered and self.scenes_visited >= msg.deliver_after_scene_count:
                msg.delivered = True
                newly_delivered.append(msg)
        return newly_delivered

    def inbox(self) -> list[EmailMessage]:
        return [m for m in self.email_queue if m.delivered]

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "chapter_id": self.chapter_id,
            "scene_id": self.scene_id,
            "flags": self.flags,
            "tools": sorted(self.tools),
            "journal": self.journal.to_dict(),
            "trust": self.trust,
            "ask_counts": self.ask_counts,
            "email_queue": [m.to_dict() for m in self.email_queue],
            "scenes_visited": self.scenes_visited,
            "current_host": self.current_host,
            "cwd": self.cwd,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        return cls(
            story_id=data["story_id"],
            chapter_id=data["chapter_id"],
            scene_id=data["scene_id"],
            flags=dict(data.get("flags", {})),
            tools=set(data.get("tools", [])),
            journal=Journal.from_dict(data.get("journal")),
            trust=dict(data.get("trust", {})),
            ask_counts=dict(data.get("ask_counts", {})),
            email_queue=[EmailMessage.from_dict(m) for m in data.get("email_queue", [])],
            scenes_visited=data.get("scenes_visited", 0),
            current_host=data.get("current_host"),
            cwd=data.get("cwd", "/"),
            saved_at=data.get("saved_at", ""),
        )

    def save(self, slot_path: Path) -> None:
        self.saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        slot_path.parent.mkdir(parents=True, exist_ok=True)
        slot_path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, slot_path: Path) -> "GameState":
        return cls.from_dict(json.loads(slot_path.read_text()))

    @staticmethod
    def sandbox_dir_for(slot_path: Path) -> Path:
        return slot_path.with_name(slot_path.stem + "_sandbox")
