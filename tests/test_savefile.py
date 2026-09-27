"""engine/savefile.py: a save slot exported as one JSON document and back."""

import json
from pathlib import Path

import pytest

from sidechannel.engine.loader import list_save_slots, save_slot_path
from sidechannel.engine.savefile import SaveFileError, export_slot, import_slot
from sidechannel.engine.session import GameSession
from sidechannel.engine.state import GameState
from sidechannel.engine.story import Story

STORY_DIR = Path(__file__).parent.parent / "sidechannel" / "stories" / "story_01_zero_day"
STORY = Story.load(STORY_DIR)


def played_slot(saves_root: Path, slot: str = "run1") -> GameSession:
    """A slot on gateway_shell with one config edit in its sandbox, saved."""
    session = GameSession.open(STORY, STORY_DIR, save_slot_path(saves_root, STORY.id, slot), fresh=True)
    session.choose(session.available_choices()[0])
    session.choose(session.available_choices()[0])
    session.run_command("connect gateway")
    session.run_command("set /etc/netmon/netmon.conf bind_address 0.0.0.0")
    session.save()
    return session


def test_export_contains_state_and_sandbox_files(tmp_path):
    played_slot(tmp_path)
    doc = export_slot(tmp_path, STORY.id, "run1")
    assert doc["format"] == "terminalgames-save" and doc["version"] == 1
    assert (doc["story_id"], doc["slot"]) == ("zero_day", "run1")
    assert doc["state"]["scene_id"] == "gateway_shell"
    assert "bind_address=0.0.0.0" in doc["sandbox"]["hosts/gateway/etc/netmon/netmon.conf"]
    json.dumps(doc)  # the whole document is plain JSON


def test_round_trip_into_another_saves_root_continues_in_place(tmp_path):
    played_slot(tmp_path / "a")
    doc = json.loads(json.dumps(export_slot(tmp_path / "a", STORY.id, "run1")))

    assert import_slot(tmp_path / "b", STORY, doc) == "run1"

    slot_path = save_slot_path(tmp_path / "b", STORY.id, "run1")
    session = GameSession.open(STORY, STORY_DIR, slot_path, fresh=False)
    assert session.scene.id == "gateway_shell"
    assert session.runner.current_host == "gateway"
    assert "bind_address=0.0.0.0" in session.run_command("cat /etc/netmon/netmon.conf").output


def test_import_into_a_new_slot_name_and_overwrite_replaces_the_sandbox(tmp_path):
    played_slot(tmp_path)
    doc = export_slot(tmp_path, STORY.id, "run1")
    stale = GameState.sandbox_dir_for(save_slot_path(tmp_path, STORY.id, "copy")) / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")

    assert import_slot(tmp_path, STORY, doc, slot="copy") == "copy"

    assert list_save_slots(tmp_path, STORY.id) == ["copy", "run1"]
    assert not stale.exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="zip"), "not a Side Channel save file"),
        (lambda d: d.update(version=99), "unsupported save file version"),
        (lambda d: d.update(story_id="dead_drop"), "this save is for 'dead_drop'"),
        (lambda d: d.update(slot="../evil"), "invalid slot name"),
        (lambda d: d["state"].pop("scene_id"), "damaged game state"),
        (lambda d: d["state"].update(scene_id="nope"), "scene this story doesn't have"),
        (lambda d: d["sandbox"].update({"../../escape.txt": "x"}), "unsafe or damaged sandbox file"),
        (lambda d: d["sandbox"].update({"/abs.txt": "x"}), "unsafe or damaged sandbox file"),
        (lambda d: d["sandbox"].update({"hosts/x": 5}), "unsafe or damaged sandbox file"),
    ],
)
def test_import_rejects_bad_documents_without_writing_anything(tmp_path, change, message):
    played_slot(tmp_path / "src")
    doc = export_slot(tmp_path / "src", STORY.id, "run1")
    change(doc)

    with pytest.raises(SaveFileError, match=message):
        import_slot(tmp_path / "dst", STORY, doc)

    assert not (tmp_path / "dst").exists()


def test_import_rejects_non_objects(tmp_path):
    with pytest.raises(SaveFileError, match="not a Side Channel save file"):
        import_slot(tmp_path, STORY, ["a", "list"])


def test_export_of_a_missing_slot_fails(tmp_path):
    with pytest.raises(SaveFileError, match="no save in slot 'ghost'"):
        export_slot(tmp_path, STORY.id, "ghost")
