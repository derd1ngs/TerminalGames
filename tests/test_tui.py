"""Headless smoke test for the split-pane Textual UI (tui.py), driven via
Textual's `run_test()` pilot. Confirms the game screen actually wires the
engine correctly through the pane-based frontend: narrative choices via the
decisions pane, command execution via the input pane, and scene transitions
toggling which pane is visible.
"""
from pathlib import Path

import pytest
import yaml

from terminalgames.engine.dialogue import load_npcs
from terminalgames.engine.shell import Network
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story
from terminalgames.tui import GameApp

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def build_app(tmp_path: Path) -> GameApp:
    story = Story.load(STORY_DIR)
    network = Network.load(STORY_DIR / "network.yaml")
    npcs = load_npcs(yaml.safe_load((STORY_DIR / "npcs.yaml").read_text()))
    chapter_id, scene_id = story.start_ref()
    state = GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)
    return GameApp(story=story, network=network, npcs=npcs, state=state, slot_path=tmp_path / "save.json")


@pytest.mark.asyncio
async def test_narrative_scene_shows_decisions_pane(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        decisions = app.query_one("#decisions-pane")
        cmd_input = app.query_one("#command-input")
        assert app.mode == "narrative"
        assert decisions.display is True
        assert decisions.option_count == 2
        assert cmd_input.display is False


@pytest.mark.asyncio
async def test_selecting_choice_advances_scene(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # "ask GHOST" -> briefing
        await pilot.pause()
        assert app.runner.state.scene_id == "briefing"


@pytest.mark.asyncio
async def test_reaching_terminal_scene_shows_input_pane(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> briefing
        await pilot.pause()
        await pilot.press("enter")  # -> recon (terminal)
        await pilot.pause()
        decisions = app.query_one("#decisions-pane")
        cmd_input = app.query_one("#command-input")
        assert app.mode == "terminal"
        assert decisions.display is False
        assert cmd_input.display is True
        assert cmd_input.placeholder == "local$"


@pytest.mark.asyncio
async def test_terminal_command_solves_puzzle_and_advances(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> briefing
        await pilot.pause()
        await pilot.press("enter")  # -> recon (terminal)
        await pilot.pause()

        cmd_input = app.query_one("#command-input")
        cmd_input.value = "connect gateway"
        await pilot.press("enter")
        await pilot.pause()

        assert app.runner.state.scene_id == "gateway_shell"
        assert app.runner.current_host == "gateway"

        for cmd in [
            "set /etc/netmon/netmon.conf bind_address 0.0.0.0",
            "set /etc/netmon/netmon.conf allow_query allow",
            "systemctl restart netmon",
        ]:
            cmd_input.value = cmd
            await pilot.press("enter")
            await pilot.pause()

        assert app.runner.state.has_flag("netmon_fixed")
        assert app.runner.state.scene_id == "discovery"


@pytest.mark.asyncio
async def test_save_meta_command_persists_state(tmp_path):
    app = build_app(tmp_path)
    slot_path = app.slot_path
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> briefing
        await pilot.pause()
        await pilot.press("enter")  # -> recon (terminal)
        await pilot.pause()

        cmd_input = app.query_one("#command-input")
        cmd_input.value = ":save"
        await pilot.press("enter")
        await pilot.pause()

    assert slot_path.exists()
    restored = GameState.load(slot_path)
    assert restored.scene_id == "recon"


@pytest.mark.asyncio
async def test_ending_scene_hides_both_panes(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.runner.state.chapter_id, app.runner.state.scene_id = "chapter_01", "ending_ignored"
        app.show_scene()
        decisions = app.query_one("#decisions-pane")
        cmd_input = app.query_one("#command-input")
        assert app.mode == "ended"
        assert decisions.display is False
        assert cmd_input.display is False
