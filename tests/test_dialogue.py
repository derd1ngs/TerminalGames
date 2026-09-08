from pathlib import Path

import pytest

from terminalgames.engine.dialogue import (
    NPC,
    DialogueError,
    Topic,
    ask_topic,
    load_npcs,
    match_outbox_topic,
    materialize_delivered_mail,
    parse_mail_text,
    send_topic_by_email,
)
from terminalgames.engine.state import EmailMessage, GameState


def make_state() -> GameState:
    return GameState(story_id="s", chapter_id="c", scene_id="a")


def test_ask_topic_returns_response_and_sets_effects():
    npc = NPC(
        id="ghost",
        name="GHOST",
        topics={
            "hint": Topic(
                id="hint",
                prompt="ask for a hint",
                response="Try the config file.",
                sets={"asked_hint": True},
                logs=[{"id": "lead_hint", "category": "lead", "text": "Got a hint from GHOST."}],
            )
        },
    )
    state = make_state()
    response = ask_topic(npc, "hint", state, discovered_at="c:a")
    assert response == "Try the config file."
    assert state.has_flag("asked_hint")
    assert state.journal.has("lead_hint")
    assert state.get_ask_count("ghost") == 1


def test_topic_gated_by_flag_requirement():
    npc = NPC(
        id="ghost",
        name="GHOST",
        topics={"secret": Topic(id="secret", prompt="?", response="...", requires={"flag": "trusted"})},
    )
    state = make_state()
    with pytest.raises(DialogueError):
        ask_topic(npc, "secret", state, "c:a")
    state.set_flag("trusted", True)
    assert ask_topic(npc, "secret", state, "c:a") == "..."


def test_topic_gated_by_trust():
    npc = NPC(
        id="friend",
        name="Friend",
        topics={
            "deep": Topic(
                id="deep",
                prompt="?",
                response="...",
                requires={"trust_at_least": {"npc": "friend", "value": 2}},
            )
        },
    )
    state = make_state()
    with pytest.raises(DialogueError):
        ask_topic(npc, "deep", state, "c:a")
    state.adjust_trust("friend", 2)
    assert ask_topic(npc, "deep", state, "c:a") == "..."


def test_ask_limit_enforced():
    npc = NPC(
        id="ghost",
        name="GHOST",
        ask_limit=1,
        topics={"hint": Topic(id="hint", prompt="?", response="one shot")},
    )
    state = make_state()
    assert ask_topic(npc, "hint", state, "c:a") == "one shot"
    with pytest.raises(DialogueError):
        ask_topic(npc, "hint", state, "c:a")


def test_misleading_reliability_is_just_data_the_engine_does_not_correct():
    npc = NPC(
        id="ghost",
        name="GHOST",
        topics={
            "who": Topic(
                id="who", prompt="who are you?", response="Nobody important.", reliability="misleading"
            )
        },
    )
    state = make_state()
    topic = npc.topics["who"]
    assert topic.reliability == "misleading"
    assert ask_topic(npc, "who", state, "c:a") == "Nobody important."


def test_available_topics_filters_by_requires():
    npc = NPC(
        id="ghost",
        name="GHOST",
        topics={
            "open": Topic(id="open", prompt="a", response="a"),
            "locked": Topic(id="locked", prompt="b", response="b", requires={"flag": "unlocked"}),
        },
    )
    state = make_state()
    assert [t.id for t in npc.available_topics(state)] == ["open"]
    state.set_flag("unlocked", True)
    assert {t.id for t in npc.available_topics(state)} == {"open", "locked"}


def test_send_topic_by_email_queues_delayed_message():
    npc = NPC(
        id="handler",
        name="Handler",
        channel="email",
        email_delay_scenes=2,
        topics={"status": Topic(id="status", prompt="status?", response="All clear.")},
    )
    state = make_state()
    message = send_topic_by_email(npc, "status", state, "c:a")
    assert state.inbox() == []
    state.advance_scene()
    state.advance_scene()
    assert message in state.inbox()


def test_load_npcs_from_dict():
    data = {
        "npcs": [
            {
                "id": "ghost",
                "name": "GHOST",
                "channel": "chat",
                "topics": [{"id": "hi", "prompt": "hi", "response": "hello"}],
            }
        ]
    }
    npcs = load_npcs(data)
    assert "ghost" in npcs
    assert npcs["ghost"].topics["hi"].response == "hello"


def test_topic_from_dict_parses_outbox_match():
    topic = Topic.from_dict(
        {
            "id": "cold_storage",
            "prompt": "ask about it",
            "response": "...",
            "outbox_match": {"subject_contains": "cold storage"},
        }
    )
    assert topic.outbox_match == {"subject_contains": "cold storage"}
    assert Topic.from_dict({"id": "x", "prompt": "p", "response": "r"}).outbox_match is None


def test_parse_mail_text_extracts_headers_and_body():
    to, subject, body = parse_mail_text("To: t\nSubject: cold storage backup?\n\nAny idea what it was?\n")
    assert (to, subject, body) == ("t", "cold storage backup?", "Any idea what it was?")


def test_parse_mail_text_tolerates_missing_headers():
    to, subject, body = parse_mail_text("just some text with no headers at all")
    assert to == ""
    assert subject == ""
    assert body == ""


def test_match_outbox_topic_is_case_insensitive_keyword_not_exact_id():
    npc = NPC(
        id="t",
        name="T",
        channel="email",
        topics={
            "cold_storage": Topic(
                id="cold_storage",
                prompt="ask about cold storage",
                response="RAVEN",
                outbox_match={"subject_contains": "cold storage"},
            )
        },
    )
    state = make_state()
    topic = match_outbox_topic(npc, "Re: Cold Storage backup passphrase?", state)
    assert topic is not None
    assert topic.id == "cold_storage"
    assert match_outbox_topic(npc, "something unrelated", state) is None


def test_match_outbox_topic_respects_requires_gating():
    npc = NPC(
        id="t",
        name="T",
        channel="email",
        topics={
            "cold_storage": Topic(
                id="cold_storage",
                prompt="ask about cold storage",
                response="RAVEN",
                requires={"flag": "lead_found"},
                outbox_match={"subject_contains": "cold storage"},
            )
        },
    )
    state = make_state()
    assert match_outbox_topic(npc, "cold storage backup?", state) is None
    state.set_flag("lead_found", True)
    assert match_outbox_topic(npc, "cold storage backup?", state) is not None


def test_match_outbox_topic_ignores_topics_without_outbox_match():
    npc = NPC(
        id="t",
        name="T",
        channel="email",
        topics={"small_talk": Topic(id="small_talk", prompt="how are you", response="fine")},
    )
    state = make_state()
    assert match_outbox_topic(npc, "how are you doing these days", state) is None


def test_materialize_delivered_mail_writes_real_files(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    messages = [
        EmailMessage(
            id="t:cold_storage:1",
            npc_id="t",
            subject="ask about cold storage",
            body="Passphrase was RAVEN.",
            deliver_after_scene_count=0,
            delivered=True,
        )
    ]
    materialize_delivered_mail(messages, inbox_dir)

    written = inbox_dir / "t_cold_storage_1.txt"
    assert written.read_text() == "From: t\nSubject: ask about cold storage\n\nPassphrase was RAVEN."


def test_materialize_delivered_mail_is_a_noop_for_an_empty_list(tmp_path: Path):
    inbox_dir = tmp_path / "inbox"
    materialize_delivered_mail([], inbox_dir)
    assert not inbox_dir.exists()
