import shutil
from pathlib import Path

import pytest

from terminalgames import main as main_module
from terminalgames.engine.endings import record_ending
from terminalgames.engine.loader import DEFAULT_SLOT, save_slot_path
from terminalgames.engine.state import GameState


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


def _save_state_at(story_id: str, slot: str) -> Path:
    slot_path = save_slot_path(main_module.SAVES_DIR, story_id, slot)
    GameState(story_id=story_id, chapter_id="chapter_01", scene_id="gateway_shell").save(slot_path)
    return slot_path


def test_select_slot_with_no_saves_prompts_for_a_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "")
    assert main_module.select_slot("zero_day") == DEFAULT_SLOT


def test_select_slot_with_no_saves_accepts_a_custom_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "speedrun")
    assert main_module.select_slot("zero_day") == "speedrun"


def test_select_slot_selects_existing_slot_by_number(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    _save_state_at("zero_day", "careful")
    _save_state_at("zero_day", "speedrun")
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "2")

    assert main_module.select_slot("zero_day") == "speedrun"


def test_select_slot_new_slot_rejects_duplicate_then_accepts_unique_name(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    _save_state_at("zero_day", "speedrun")
    answers = iter(["n", "speedrun", "n", "careful"])
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": next(answers))

    assert main_module.select_slot("zero_day") == "careful"


def test_new_or_continue_new_flag_ignores_existing_save(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    slot_path = _save_state_at("zero_day", DEFAULT_SLOT)
    assert main_module.new_or_continue(slot_path, new=True) is True


def test_new_or_continue_with_no_save_starts_fresh_without_prompting(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": pytest.fail("prompted"))
    assert main_module.new_or_continue(save_slot_path(tmp_path, "zero_day", DEFAULT_SLOT)) is True


def test_new_or_continue_continue_flag_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    slot_path = _save_state_at("zero_day", DEFAULT_SLOT)
    assert main_module.new_or_continue(slot_path, cont=True) is False


def test_new_or_continue_continue_flag_without_save_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    slot_path = save_slot_path(tmp_path, "zero_day", DEFAULT_SLOT)
    with pytest.raises(SystemExit):
        main_module.new_or_continue(slot_path, cont=True)


def test_new_or_continue_prompts_when_save_exists_and_no_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    slot_path = _save_state_at("zero_day", DEFAULT_SLOT)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "c")
    assert main_module.new_or_continue(slot_path) is False


def test_new_or_continue_restart_choice_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    slot_path = _save_state_at("zero_day", DEFAULT_SLOT)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "r")
    assert main_module.new_or_continue(slot_path) is True


def test_saves_stay_in_the_repo_for_a_checkout(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    assert main_module.default_saves_dir(tmp_path / "terminalgames") == tmp_path / "saves"


def test_an_installed_copy_saves_to_the_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "user_data_path", lambda app: tmp_path / "data" / app)
    site_packages = tmp_path / "site-packages"
    assert (
        main_module.default_saves_dir(site_packages / "terminalgames")
        == tmp_path / "data" / "terminalgames" / "saves"
    )


def test_this_checkout_uses_the_repo_saves_dir():
    assert main_module.SAVES_DIR == Path(__file__).resolve().parent.parent / "saves"


def _stale_slot(tmp_path):
    story_dir = main_module.find_story("zero_day", main_module.discover_stories())
    story = main_module.Story.load(story_dir)
    slot_path = save_slot_path(tmp_path, story.id, "old")
    GameState(story_id=story.id, chapter_id="chapter_01", scene_id="cut_scene").save(slot_path)
    return story, story_dir, slot_path


def test_stale_save_with_continue_flag_exits_with_a_clear_message(tmp_path, capsys):
    story, story_dir, slot_path = _stale_slot(tmp_path)
    with pytest.raises(SystemExit):
        main_module.open_session(story, story_dir, slot_path, fresh=False, cont=True)
    assert "Can't continue slot 'old'" in capsys.readouterr().out


def test_stale_save_offers_a_restart(tmp_path, monkeypatch):
    story, story_dir, slot_path = _stale_slot(tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "y")
    session = main_module.open_session(story, story_dir, slot_path, fresh=False, cont=False)
    assert (session.state.chapter_id, session.scene.id) == story.start_ref()


def test_stale_save_restart_declined_exits(tmp_path, monkeypatch):
    story, story_dir, slot_path = _stale_slot(tmp_path)
    monkeypatch.setattr(main_module.console, "input", lambda prompt="": "")
    with pytest.raises(SystemExit):
        main_module.open_session(story, story_dir, slot_path, fresh=False, cont=False)


def test_broken_story_file_exits_with_a_message_not_a_traceback(tmp_path, capsys):
    story_dir = main_module.find_story("dead_drop", main_module.discover_stories())
    broken = shutil.copytree(story_dir, tmp_path / "story")
    (broken / "npcs.yaml").write_text(
        (broken / "npcs.yaml").read_text().replace("channel: chat", "channel: sms")
    )
    story = main_module.Story.load(broken)
    with pytest.raises(SystemExit):
        main_module.open_session(
            story, broken, save_slot_path(tmp_path, story.id, "x"), fresh=True, cont=False
        )
    assert "Failed to load story: npcs.yaml npc 'juno': invalid channel 'sms'" in capsys.readouterr().out


def test_print_endings_lists_found_titles_and_hides_the_rest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_module, "SAVES_DIR", tmp_path)
    story = main_module.Story.load(main_module.find_story("dead_drop", main_module.discover_stories()))
    main_module.print_endings(story)
    assert capsys.readouterr().out == ""  # nothing found yet: no line at all
    record_ending(tmp_path / "dead_drop", "ending_burned")
    main_module.print_endings(story)
    assert capsys.readouterr().out.strip() == "Endings found: 1/4 -- Scorched Earth, and 3 more"
