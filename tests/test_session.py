"""GameSession: the UI-free game loop every frontend drives."""

from pathlib import Path

import yaml

from terminalgames.engine.dialogue import load_npcs
from terminalgames.engine.session import GameSession
from terminalgames.engine.shell import Network
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Choice, Story

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def build_session(tmp_path: Path) -> GameSession:
    story = Story.load(STORY_DIR)
    network = Network.load(STORY_DIR / "network.yaml")
    npcs = load_npcs(yaml.safe_load((STORY_DIR / "npcs.yaml").read_text()))
    chapter_id, scene_id = story.start_ref()
    state = GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)
    slot_path = tmp_path / "save.json"
    network.materialize(GameState.sandbox_dir_for(slot_path))
    return GameSession(story=story, network=network, npcs=npcs, state=state, slot_path=slot_path)


def test_starts_on_the_story_start_scene(tmp_path):
    session = build_session(tmp_path)
    assert (session.state.chapter_id, session.scene.id) == session.story.start_ref()
    assert session.scene.type == "narrative"


def test_choose_advances_and_counts_the_scene(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])
    assert session.scene.id == "briefing"
    assert session.state.scenes_visited == 1


def test_run_command_without_solving_stays_on_the_scene(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])  # -> briefing
    session.choose(session.available_choices()[0])  # -> recon (terminal)
    result = session.run_command("whoami")
    assert result.output == "user@localhost"
    assert not result.advanced
    assert session.scene.id == "recon"


def test_solving_a_terminal_scene_advances_and_auto_connects(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])  # -> briefing
    session.choose(session.available_choices()[0])  # -> recon (terminal)
    result = session.run_command("connect gateway")
    assert result.advanced
    assert session.scene.id == "gateway_shell"
    assert session.runner.current_host == "gateway"
    assert session.runner.current_scene == "gateway_shell"


def test_crossing_a_chapter_autosaves_and_reports_it(tmp_path):
    session = build_session(tmp_path)
    landing = next(iter(session.story.chapters["chapter_02"].scenes))
    notices = session.choose(Choice.from_dict({"text": "Jump ahead", "next": f"chapter_02:{landing}"}))
    assert session.state.chapter_id == "chapter_02"
    assert "Autosaved -- entering chapter 'chapter_02'." in notices
    assert GameState.load(session.slot_path).chapter_id == "chapter_02"
