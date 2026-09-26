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
from pathlib import Path
from typing import Any, Optional

from . import schema
from .schema import StoryLoadError
from .state import EmailMessage, GameState
from .story import apply_effects, check_requires

TOPIC_KEYS = {"id", "prompt", "response", "requires", "sets", "logs", "reliability", "outbox_match"}
NPC_KEYS = {"id", "name", "persona", "channel", "ask_limit", "email_delay_scenes", "topics"}


@dataclass
class Topic:
    id: str
    prompt: str
    response: str
    requires: Optional[dict[str, Any]] = None
    sets: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    reliability: str = "truthful"  # "truthful" | "misleading" | "evasive"
    # Only email topics that declare this are matchable by `mail sync`
    # against a real drafted message's Subject line (see match_outbox_topic
    # below); the direct `mail send <npc> <topic>` shortcut addresses a
    # topic by id and doesn't need it.
    outbox_match: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str = "topic") -> "Topic":
        where = f"{where} '{data.get('id')}'" if isinstance(data, dict) else where
        schema.check_keys(data, TOPIC_KEYS, where, required={"id", "prompt", "response"})
        schema.check_effects(data, where)
        schema.check_value(
            data.get("reliability", "truthful"), {"truthful", "misleading", "evasive"}, "reliability", where
        )
        if "outbox_match" in data:
            schema.check_keys(
                data["outbox_match"], {"subject_contains"}, f"{where} outbox_match", {"subject_contains"}
            )
        return cls(
            id=data["id"],
            prompt=data["prompt"],
            response=data["response"],
            requires=data.get("requires"),
            sets=dict(data.get("sets", {})),
            logs=list(data.get("logs", [])),
            reliability=data.get("reliability", "truthful"),
            outbox_match=data.get("outbox_match"),
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
    def from_dict(cls, data: dict[str, Any], where: str = "npc") -> "NPC":
        where = f"{where} '{data.get('id')}'" if isinstance(data, dict) else where
        schema.check_keys(data, NPC_KEYS, where, required={"id"})
        schema.check_value(data.get("channel", "chat"), {"chat", "email"}, "channel", where)
        topics = {}
        for topic_data in schema.check_list(data.get("topics", []), f"{where} topics"):
            topic = Topic.from_dict(topic_data, f"{where} topic")
            if topic.id in topics:
                raise StoryLoadError(f"{where}: duplicate topic id '{topic.id}'")
            topics[topic.id] = topic
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
    schema.check_keys(data, {"npcs"}, "npcs.yaml")
    npcs = {}
    for npc_data in schema.check_list(data.get("npcs", []), "npcs.yaml npcs"):
        npc = NPC.from_dict(npc_data, "npcs.yaml npc")
        if npc.id in npcs:
            raise StoryLoadError(f"npcs.yaml: duplicate npc id '{npc.id}'")
        npcs[npc.id] = npc
    return npcs


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


# --- mail as real files -------------------------------------------------------
#
# Sending is explicit (a real draft file + `mail sync`, see shell.py);
# receiving stays automatic, tied to the same scene-count delay as always --
# newly delivered replies just also materialize as real files now.


def parse_mail_text(text: str) -> tuple[str, str, str]:
    """Parse a minimal `To:`/`Subject:` + blank line + body message, the
    same shape `mail read` already renders. Missing headers come back as
    empty strings rather than raising -- an unrecognized recipient or
    subject is a normal "bounce", not a parse error."""
    to = ""
    subject = ""
    lines = text.splitlines()
    body_start = len(lines)
    for i, line in enumerate(lines):
        if line.startswith("To:"):
            to = line[len("To:") :].strip()
        elif line.startswith("Subject:"):
            subject = line[len("Subject:") :].strip()
        elif line.strip() == "":
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return to, subject, body


def match_outbox_topic(npc: NPC, subject: str, state: GameState) -> Optional[Topic]:
    """Which of `npc`'s currently-available topics (already `requires`-
    gated, same rule the `mail send` shortcut follows) a drafted message's
    subject line matches, by loose keyword rather than an exact topic id --
    lets the player write a real message in their own words."""
    subject_lower = subject.lower()
    for topic in npc.available_topics(state):
        match = topic.outbox_match
        if match and match.get("subject_contains", "").lower() in subject_lower:
            return topic
    return None


def materialize_delivered_mail(messages: list[EmailMessage], inbox_dir: Path) -> None:
    """Write one real file per newly-delivered message into `inbox_dir`,
    same From:/Subject:/body shape `mail read` renders. `:` in a message id
    isn't a valid filename character on every platform, so it's replaced."""
    if not messages:
        return
    inbox_dir.mkdir(parents=True, exist_ok=True)
    for msg in messages:
        filename = msg.id.replace(":", "_") + ".txt"
        (inbox_dir / filename).write_text(f"From: {msg.npc_id}\nSubject: {msg.subject}\n\n{msg.body}")
