from pathlib import Path

import pytest

from terminalgames import main as main_module
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story

ZERO_DAY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def test_parse_args_defaults():
    args = main_module.parse_args([])
    assert args.story is None
    assert args.list is False
    assert args.new is False
    assert args.cont is False
    assert args.slot is None


def test_parse_args_story_positional_and_new_flag():
    args = main_module.parse_args(["zero_day", "--new", "--slot", "speedrun"])
    assert args.story == "zero_day"
    assert args.new is True
    assert args.slot == "speedrun"


def test_parse_args_new_and_continue_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main_module.parse_args(["--new", "--continue"])


def test_find_story_matches_directory_name():
    stories = main_module.discover_stories()
    assert main_module.find_story("story_01_zero_day", stories) == ZERO_DAY_DIR


def test_find_story_matches_manifest_id():
    stories = main_module.discover_stories()
    assert main_module.find_story("zero_day", stories) == ZERO_DAY_DIR


def test_find_story_returns_none_for_unknown_ref():
    stories = main_module.discover_stories()
    assert main_module.find_story("nonexistent", stories) is None


def _save_state_at(story: Story, slot: str) -> Path:
    slot_path = main_module.save_slot_path(story.id, slot)
    GameState(story_id=story.id, chapter_id="chapter_01", scene_id="gateway_shell").save(slot_path)
    return slot_path


def test_list_save_slots_empty_when_no_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    assert main_module.list_save_slots("zero_day") == []


def test_list_save_slots_returns_sorted_slot_names(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story, "speedrun")
    _save_state_at(story, "careful")

    assert main_module.list_save_slots(story.id) == ["careful", "speedrun"]


def test_migrate_legacy_save_moves_flat_file_into_default_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    legacy_path = tmp_path / f"{story.id}.json"
    GameState(story_id=story.id, chapter_id="chapter_01", scene_id="gateway_shell").save(legacy_path)
    GameState.notes_path_for(legacy_path).write_text("old notes")

    main_module.migrate_legacy_save(story.id)

    default_path = main_module.save_slot_path(story.id, main_module.DEFAULT_SLOT)
    assert not legacy_path.exists()
    assert default_path.exists()
    assert GameState.notes_path_for(default_path).read_text() == "old notes"


def test_migrate_legacy_save_is_a_noop_when_default_slot_already_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    legacy_path = tmp_path / f"{story.id}.json"
    GameState(story_id=story.id, chapter_id="chapter_01", scene_id="legacy").save(legacy_path)
    _save_state_at(story, main_module.DEFAULT_SLOT)

    main_module.migrate_legacy_save(story.id)

    assert legacy_path.exists()  # untouched -- the default slot was already taken


def test_migrate_legacy_save_is_a_noop_when_no_legacy_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    main_module.migrate_legacy_save("zero_day")  # should not raise
    assert main_module.list_save_slots("zero_day") == []


def test_select_slot_with_no_saves_prompts_for_a_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "")
    assert main_module.select_slot("zero_day") == main_module.DEFAULT_SLOT


def test_select_slot_with_no_saves_accepts_a_custom_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "speedrun")
    assert main_module.select_slot("zero_day") == "speedrun"


def test_select_slot_selects_existing_slot_by_number(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story, "careful")
    _save_state_at(story, "speedrun")
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "2")

    assert main_module.select_slot(story.id) == "speedrun"


def test_select_slot_new_slot_rejects_duplicate_then_accepts_unique_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story, "speedrun")
    answers = iter(["n", "speedrun", "n", "careful"])
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": next(answers))

    assert main_module.select_slot(story.id) == "careful"


def test_new_or_continue_new_flag_ignores_existing_save(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    slot_path = _save_state_at(story, main_module.DEFAULT_SLOT)

    state = main_module.new_or_continue(story, slot_path, new=True)
    assert (state.chapter_id, state.scene_id) == story.start_ref()


def test_new_or_continue_continue_flag_loads_save(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    slot_path = _save_state_at(story, main_module.DEFAULT_SLOT)

    state = main_module.new_or_continue(story, slot_path, cont=True)
    assert (state.chapter_id, state.scene_id) == ("chapter_01", "gateway_shell")


def test_new_or_continue_continue_flag_without_save_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    slot_path = main_module.save_slot_path(story.id, main_module.DEFAULT_SLOT)

    with pytest.raises(SystemExit):
        main_module.new_or_continue(story, slot_path, cont=True)


def test_new_or_continue_prompts_when_save_exists_and_no_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    slot_path = _save_state_at(story, main_module.DEFAULT_SLOT)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "c")

    state = main_module.new_or_continue(story, slot_path)
    assert (state.chapter_id, state.scene_id) == ("chapter_01", "gateway_shell")
