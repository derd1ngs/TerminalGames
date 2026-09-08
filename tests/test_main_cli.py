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


def test_parse_args_story_positional_and_new_flag():
    args = main_module.parse_args(["zero_day", "--new"])
    assert args.story == "zero_day"
    assert args.new is True


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


def _save_state_at(story: Story) -> Path:
    slot_path = main_module.save_slot_path(story.id)
    GameState(story_id=story.id, chapter_id="chapter_01", scene_id="gateway_shell").save(slot_path)
    return slot_path


def test_new_or_continue_new_flag_ignores_existing_save(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story)

    state = main_module.new_or_continue(story, new=True)
    assert (state.chapter_id, state.scene_id) == story.start_ref()


def test_new_or_continue_continue_flag_loads_save(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story)

    state = main_module.new_or_continue(story, cont=True)
    assert (state.chapter_id, state.scene_id) == ("chapter_01", "gateway_shell")


def test_new_or_continue_continue_flag_without_save_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)

    with pytest.raises(SystemExit):
        main_module.new_or_continue(story, cont=True)


def test_new_or_continue_prompts_when_save_exists_and_no_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = Story.load(ZERO_DAY_DIR)
    _save_state_at(story)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "c")

    state = main_module.new_or_continue(story)
    assert (state.chapter_id, state.scene_id) == ("chapter_01", "gateway_shell")
