"""engine/loader.py: story discovery and save-slot paths, with the saves
root passed in (no module global to patch)."""

from pathlib import Path

from terminalgames.engine.loader import (
    DEFAULT_SLOT,
    discover_stories,
    find_story,
    list_save_slots,
    migrate_legacy_save,
    save_slot_path,
    slot_summary,
)
from terminalgames.engine.state import GameState

ZERO_DAY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def _save_state_at(saves_root: Path, slot: str, scene_id: str = "gateway_shell") -> Path:
    slot_path = save_slot_path(saves_root, "zero_day", slot)
    GameState(story_id="zero_day", chapter_id="chapter_01", scene_id=scene_id).save(slot_path)
    return slot_path


def test_discover_stories_finds_zero_day():
    assert ZERO_DAY_DIR in discover_stories()


def test_discover_stories_in_a_missing_dir_is_empty(tmp_path):
    assert discover_stories(tmp_path / "nope") == []


def test_find_story_matches_directory_name():
    assert find_story("story_01_zero_day", discover_stories()) == ZERO_DAY_DIR


def test_find_story_matches_manifest_id():
    assert find_story("zero_day", discover_stories()) == ZERO_DAY_DIR


def test_find_story_returns_none_for_unknown_ref():
    assert find_story("nonexistent", discover_stories()) is None


def test_save_slot_path_layout(tmp_path):
    assert save_slot_path(tmp_path, "zero_day", "run1") == tmp_path / "zero_day" / "run1.json"


def test_list_save_slots_empty_when_no_saves(tmp_path):
    assert list_save_slots(tmp_path, "zero_day") == []


def test_list_save_slots_returns_sorted_slot_names(tmp_path):
    _save_state_at(tmp_path, "speedrun")
    _save_state_at(tmp_path, "careful")
    assert list_save_slots(tmp_path, "zero_day") == ["careful", "speedrun"]


def test_slot_summary_shows_position(tmp_path):
    _save_state_at(tmp_path, "run1")
    assert slot_summary(tmp_path, "zero_day", "run1").startswith("run1 -- chapter_01:gateway_shell (saved ")


def test_migrate_legacy_save_moves_flat_file_into_default_slot(tmp_path):
    legacy_path = tmp_path / "zero_day.json"
    GameState(story_id="zero_day", chapter_id="chapter_01", scene_id="gateway_shell").save(legacy_path)

    migrate_legacy_save(tmp_path, "zero_day")

    assert not legacy_path.exists()
    assert save_slot_path(tmp_path, "zero_day", DEFAULT_SLOT).exists()


def test_migrate_legacy_save_is_a_noop_when_default_slot_already_exists(tmp_path):
    legacy_path = tmp_path / "zero_day.json"
    GameState(story_id="zero_day", chapter_id="chapter_01", scene_id="legacy").save(legacy_path)
    _save_state_at(tmp_path, DEFAULT_SLOT)

    migrate_legacy_save(tmp_path, "zero_day")

    assert legacy_path.exists()  # untouched -- the default slot was already taken


def test_migrate_legacy_save_is_a_noop_when_no_legacy_file(tmp_path):
    migrate_legacy_save(tmp_path, "zero_day")  # should not raise
    assert list_save_slots(tmp_path, "zero_day") == []
