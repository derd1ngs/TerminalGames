import pytest

from terminalgames.engine.dialogue import (
    DialogueError,
    NPC,
    Topic,
    ask_topic,
    load_npcs,
    send_topic_by_email,
)
from terminalgames.engine.state import GameState


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
        topics={
            "secret": Topic(id="secret", prompt="?", response="...", requires={"flag": "trusted"})
        },
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
                id="deep", prompt="?", response="...",
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
            "who": Topic(id="who", prompt="who are you?", response="Nobody important.", reliability="misleading")
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
