"""NPC conversation engine: topic-based dialogue trees shared by every
talkable character (AI advisor, friend, handler, antagonist) over chat or
email channels.

Deliberately scripted rather than LLM-backed in v1 -- fully deterministic,
free, and testable. A future persona could implement its response via a real
API call behind the same `ask_topic`/`send_topic_by_email` call shape without
changing `shell.py` or any story content for the existing scripted NPCs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .state import EmailMessage, GameState
from .story import apply_effects, check_requires


@dataclass
class Topic:
    id: str
    prompt: str
    response: str
    requires: Optional[dict[str, Any]] = None
    sets: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    reliability: str = "truthful"  # "truthful" | "misleading" | "evasive"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Topic":
        return cls(
            id=data["id"],
            prompt=data["prompt"],
            response=data["response"],
            requires=data.get("requires"),
            sets=dict(data.get("sets", {})),
            logs=list(data.get("logs", [])),
            reliability=data.get("reliability", "truthful"),
        )


@dataclass
class NPC:
    id: str
    name: str
    persona: str = ""
    channel: str = "chat"  # "chat" | "email"
    ask_limit: Optional[int] = None
    email_delay_scenes: int = 1
    topics: dict[str, Topic] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NPC":
        topics = {t["id"]: Topic.from_dict(t) for t in data.get("topics", [])}
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            persona=data.get("persona", ""),
            channel=data.get("channel", "chat"),
            ask_limit=data.get("ask_limit"),
            email_delay_scenes=data.get("email_delay_scenes", 1),
            topics=topics,
        )

    def available_topics(self, state: GameState) -> list[Topic]:
        return [t for t in self.topics.values() if check_requires(t.requires, state)]


def load_npcs(data: dict[str, Any]) -> dict[str, NPC]:
    return {n["id"]: NPC.from_dict(n) for n in data.get("npcs", [])}


class DialogueError(Exception):
    pass


def _check_ask_allowed(npc: NPC, topic_id: str, state: GameState) -> Topic:
    topic = npc.topics.get(topic_id)
    if topic is None or not check_requires(topic.requires, state):
        raise DialogueError(f"{npc.name} has nothing to say about that.")
    if npc.ask_limit is not None and state.get_ask_count(npc.id) >= npc.ask_limit:
        raise DialogueError(f"{npc.name} isn't responding anymore for now.")
    return topic


def ask_topic(npc: NPC, topic_id: str, state: GameState, discovered_at: str) -> str:
    """Chat: synchronous, returns the response text immediately."""
    topic = _check_ask_allowed(npc, topic_id, state)
    state.increment_ask_count(npc.id)
    apply_effects(topic.sets, topic.logs, state, discovered_at)
    return topic.response


def send_topic_by_email(npc: NPC, topic_id: str, state: GameState, discovered_at: str) -> EmailMessage:
    """Email: asynchronous, queues a reply delivered after `email_delay_scenes` more scenes."""
    topic = _check_ask_allowed(npc, topic_id, state)
    state.increment_ask_count(npc.id)
    apply_effects(topic.sets, topic.logs, state, discovered_at)
    message = EmailMessage(
        id=f"{npc.id}:{topic.id}:{state.get_ask_count(npc.id)}",
        npc_id=npc.id,
        subject=topic.prompt,
        body=topic.response,
        deliver_after_scene_count=state.scenes_visited + npc.email_delay_scenes,
    )
    state.queue_email(message)
    return message
