from pathlib import Path

from terminalgames.engine.journal import JournalEntry
from terminalgames.engine.state import EmailMessage, GameState


def build_state() -> GameState:
    state = GameState(story_id="zero_day", chapter_id="chapter_01", scene_id="intro")
    state.set_flag("met_ghost", True)
    state.add_tool("scanner")
    state.journal.add(
        JournalEntry(id="lead1", category="lead", text="A lead.", discovered_at="chapter_01:intro")
    )
    state.adjust_trust("ghost", 2)
    state.increment_ask_count("ghost")
    state.queue_email(
        EmailMessage(
            id="ghost:topic:1",
            npc_id="ghost",
            subject="re: sentinel",
            body="Be careful.",
            deliver_after_scene_count=3,
        )
    )
    state.advance_scene()
    return state


def test_roundtrip_via_dict():
    state = build_state()
    restored = GameState.from_dict(state.to_dict())
    assert restored.story_id == "zero_day"
    assert restored.has_flag("met_ghost")
    assert restored.has_tool("scanner")
    assert restored.journal.has("lead1")
    assert restored.get_trust("ghost") == 2
    assert restored.get_ask_count("ghost") == 1
    assert len(restored.email_queue) == 1
    assert restored.scenes_visited == 1


def test_roundtrip_via_file(tmp_path: Path):
    state = build_state()
    slot_path = tmp_path / "zero_day.json"
    assert state.saved_at == ""
    state.save(slot_path)
    assert state.saved_at != ""
    restored = GameState.load(slot_path)
    assert restored.to_dict() == state.to_dict()


def test_notes_path_for():
    slot_path = Path("/tmp/saves/zero_day.json")
    notes = GameState.notes_path_for(slot_path)
    assert notes == Path("/tmp/saves/zero_day_notes.txt")


def test_email_delivery_after_advancing_scenes():
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    state.queue_email(
        EmailMessage(id="m1", npc_id="ghost", subject="s", body="b", deliver_after_scene_count=2)
    )
    assert state.inbox() == []
    assert state.advance_scene() == []
    assert state.inbox() == []
    assert len(state.advance_scene()) == 1
    assert len(state.inbox()) == 1


def test_advance_scene_only_returns_messages_newly_delivered_this_call():
    """Not every message that's already delivered -- just the ones that
    crossed the threshold on this specific call, so a caller (e.g. the TUI
    materializing a real inbox file) doesn't re-notify for old mail."""
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    state.queue_email(
        EmailMessage(id="early", npc_id="ghost", subject="s1", body="b1", deliver_after_scene_count=1)
    )
    state.queue_email(
        EmailMessage(id="late", npc_id="ghost", subject="s2", body="b2", deliver_after_scene_count=2)
    )

    first = state.advance_scene()
    assert [m.id for m in first] == ["early"]

    second = state.advance_scene()
    assert [m.id for m in second] == ["late"]

    third = state.advance_scene()
    assert third == []
