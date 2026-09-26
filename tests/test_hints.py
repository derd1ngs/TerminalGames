"""The `hint` command, and the shipped stories' hints themselves: every
terminal scene has hints, and playing each scene using *only* the commands
its hints spell out (in `backticks`, in order) actually solves it."""

import re
from pathlib import Path

import pytest

from terminalgames.engine.session import GameSession
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story

STORIES = Path(__file__).parent.parent / "terminalgames" / "stories"

# Choices (by text prefix) that route through every terminal scene of each story.
ROUTES = {
    "story_01_zero_day": [
        "Ask GHOST",
        "Fine. Let's see",
        "Dig through that old archive box",
        "Back to it.",
        "Push further",
        "Check back later",
    ],
    "story_02_dead_drop": ['"Send me the address.', "Follow the runbook."],
    "story_03_night_shift": ["Pull on your hoodie", "Take the key", "Go in now."],
}


def open_story(tmp_path: Path, story_dir: Path) -> GameSession:
    return GameSession.open(Story.load(story_dir), story_dir, tmp_path / "hints.json", fresh=True)


def hinted_commands(hints: list[str]) -> list[str]:
    return [cmd for hint in hints for cmd in re.findall(r"`([^`]+)`", hint)]


@pytest.mark.parametrize("story_name", sorted(ROUTES))
def test_every_terminal_scene_is_solved_by_its_own_hints(tmp_path, story_name):
    story_dir = STORIES / story_name
    session = open_story(tmp_path, story_dir)
    route = list(ROUTES[story_name])
    solved = set()
    while session.scene.type != "ending" and (route or session.scene.type == "terminal"):
        scene = session.scene
        if scene.type == "narrative":
            prefix = route.pop(0)
            session.choose(next(c for c in session.available_choices() if c.text.startswith(prefix)))
            continue
        commands = hinted_commands(scene.terminal.hints)
        assert any(session.run_command(cmd).advanced for cmd in commands), (
            f"{scene.id}: hints {commands} don't solve it"
        )
        solved.add(scene.id)
    all_terminal = {s.id for ch in session.story.chapters.values() for s in ch.scenes.values() if s.terminal}
    assert solved == all_terminal


def test_hint_reveals_one_at_a_time_then_lists_them_all(tmp_path):
    session = open_story(tmp_path, STORIES / "story_02_dead_drop")
    session.choose(session.available_choices()[0])  # -> relay_shell (2 hints)
    assert session.run_command("hint").output == "Hint 1/2: Juno gave you the host: relay."
    assert session.run_command("hint").output == "Hint 2/2: `connect relay`."
    assert session.run_command("hint").output == (
        "That's every hint for this scene:\n  1. Juno gave you the host: relay.\n  2. `connect relay`."
    )


def test_revealed_hints_survive_save_and_continue_and_are_per_scene(tmp_path):
    story_dir = STORIES / "story_02_dead_drop"
    session = open_story(tmp_path, story_dir)
    session.choose(session.available_choices()[0])
    session.run_command("hint")
    session.save()

    session = GameSession.open(Story.load(story_dir), story_dir, session.slot_path, fresh=False)
    assert session.run_command("hint").output.startswith("Hint 2/2:")
    session.run_command("connect relay")  # -> relay_log: its own count starts over
    assert session.run_command("hint").output.startswith("Hint 1/3:")
    assert GameState.load(session.slot_path).hints_shown == {"chapter_01:relay_shell": 1}  # last save


def test_scene_without_hints_points_elsewhere(tmp_path):
    session = open_story(tmp_path, STORIES / "story_02_dead_drop")
    session.choose(session.available_choices()[0])
    session.scene.terminal.hints.clear()
    session.enter_scene()
    assert session.run_command("hint").output.startswith("No hints for this one.")
