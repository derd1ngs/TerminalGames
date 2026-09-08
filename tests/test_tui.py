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
from terminalgames.engine.journal import JournalEntry
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


def build_multichapter_story(tmp_path: Path) -> Story:
    root = tmp_path / "story"
    (root / "chapters").mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {"id": "multi", "title": "Multi", "start": "one:start", "chapters": ["one.yaml", "two.yaml"]}
        )
    )
    (root / "chapters" / "one.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "one",
                "scenes": [
                    {
                        "id": "start",
                        "text": "Chapter one.",
                        "choices": [{"text": "Continue", "next": "two:landing"}],
                    }
                ],
            }
        )
    )
    (root / "chapters" / "two.yaml").write_text(
        yaml.safe_dump({"id": "two", "scenes": [{"id": "landing", "type": "ending", "text": "The end."}]})
    )
    return Story.load(root)


@pytest.mark.asyncio
async def test_narrative_scene_shows_decisions_pane(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        decisions = app.query_one("#decisions-pane")
        terminal_group = app.query_one("#terminal-group")
        assert app.mode == "narrative"
        assert decisions.display is True
        assert decisions.option_count == 2
        assert terminal_group.display is False


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
        terminal_group = app.query_one("#terminal-group")
        cmd_input = app.query_one("#command-input")
        assert app.mode == "terminal"
        assert decisions.display is False
        assert terminal_group.display is True
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


def _pane_text(app: GameApp, selector: str, lines: int = 80) -> str:
    """Rich markup is enabled on both logs so authored `[bold]...[/bold]`
    text renders; a lowercase bracketed word in *dynamic* output (a journal
    category, a mail id) looks like an invalid markup tag to Rich and gets
    silently dropped unless escaped first. Render the pane's actual lines
    (not the raw command return value) to catch that class of bug.

    Joins with a single space rather than a newline, and strips trailing
    padding from each line, so a phrase that word-wraps across lines in a
    narrow pane still forms one contiguous, matchable string."""
    pane = app.query_one(selector)
    rendered = (pane.render_line(y).text.rstrip() for y in range(lines))
    return " ".join(line for line in rendered if line)


@pytest.mark.asyncio
async def test_command_output_with_brackets_is_not_swallowed_by_markup(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> briefing
        await pilot.pause()
        await pilot.press("enter")  # -> recon (terminal)
        await pilot.pause()

        app.runner.state.journal.add(
            JournalEntry(id="t1", category="trace", text="Something happened.", discovered_at="c:s")
        )
        cmd_input = app.query_one("#command-input")
        cmd_input.value = "journal"
        await pilot.press("enter")
        await pilot.pause()

        assert "[trace] Something happened." in _pane_text(app, "#terminal-pane")


@pytest.mark.asyncio
async def test_story_pane_holds_narration_only_terminal_pane_holds_commands(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # -> briefing
        await pilot.pause()
        await pilot.press("enter")  # -> recon (terminal)
        await pilot.pause()

        cmd_input = app.query_one("#command-input")
        cmd_input.value = "whoami"
        await pilot.press("enter")
        await pilot.pause()

        story_text = _pane_text(app, "#story-pane")
        terminal_text = _pane_text(app, "#terminal-pane")

        # The scene's narrative intro is story-only.
        assert "Get your bearings first" in story_text
        assert "Get your bearings first" not in terminal_text

        # The command echo and its output are terminal-only. (The scene's
        # own narrative text mentions `whoami` by name while teaching it, so
        # check for the actual echoed command line rather than the bare word.)
        assert "local$ whoami" in terminal_text
        assert "user@localhost" in terminal_text
        assert "local$ whoami" not in story_text
        assert "user@localhost" not in story_text


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
async def test_crossing_chapter_boundary_autosaves(tmp_path):
    story = build_multichapter_story(tmp_path)
    state = GameState(story_id=story.id, chapter_id="one", scene_id="start")
    slot_path = tmp_path / "save.json"
    app = GameApp(story=story, network=Network(), npcs={}, state=state, slot_path=slot_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not slot_path.exists()
        await pilot.press("enter")  # crosses from chapter "one" into chapter "two"
        await pilot.pause()

    assert slot_path.exists()
    restored = GameState.load(slot_path)
    assert (restored.chapter_id, restored.scene_id) == ("two", "landing")


@pytest.mark.asyncio
async def test_staying_in_same_chapter_does_not_autosave(tmp_path):
    app = build_app(tmp_path)
    slot_path = app.slot_path
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # briefing -- still chapter_01
        await pilot.pause()

    assert not slot_path.exists()


@pytest.mark.asyncio
async def test_ending_scene_hides_both_panes(tmp_path):
    app = build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.runner.state.chapter_id, app.runner.state.scene_id = "chapter_01", "ending_ignored"
        app.show_scene()
        decisions = app.query_one("#decisions-pane")
        terminal_group = app.query_one("#terminal-group")
        assert app.mode == "ended"
        assert decisions.display is False
        assert terminal_group.display is False
