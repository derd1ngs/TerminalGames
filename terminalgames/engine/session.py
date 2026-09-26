"""The game loop: which scene the player is on, what picking a choice or
running a terminal command does, and what crossing into the next scene
triggers (async mail delivery, the chapter-boundary autosave).

UI-free on purpose -- a frontend (the Textual TUI today) only renders
`session.scene` and forwards the player's input, so every frontend plays by
exactly the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .dialogue import NPC
from .shell import Network, TerminalRunner
from .state import GameState
from .story import Choice, Scene, Story, apply_effects, check_requires


@dataclass
class CommandResult:
    output: str
    advanced: bool = False  # the command solved the scene and moved on
    notices: list[str] = field(default_factory=list)  # new mail, autosave


class GameSession:
    def __init__(
        self, story: Story, network: Network, npcs: dict[str, NPC], state: GameState, slot_path: Path
    ):
        self.story = story
        self.slot_path = slot_path
        self.runner = TerminalRunner(state=state, network=network, npcs=npcs, save_slot_path=slot_path)
        self.scene: Scene = self.enter_scene()

    @property
    def state(self) -> GameState:
        return self.runner.state

    def enter_scene(self) -> Scene:
        """Load the scene the state points at and apply its on-entry
        effects: a terminal scene tells the runner where it is (for journal
        timestamps) and auto-connects to its `host`, if it names one."""
        state = self.state
        scene = self.story.get_scene(state.chapter_id, state.scene_id)
        self.scene = scene
        if scene.type == "terminal":
            assert scene.terminal is not None
            self.runner.current_chapter, self.runner.current_scene = state.chapter_id, scene.id
            if scene.terminal.host:
                self.runner.current_host = scene.terminal.host
                self.runner.cwd = "/"
        return scene

    def available_choices(self) -> list[Choice]:
        return [c for c in self.scene.choices if check_requires(c.requires, self.state)]

    def choose(self, choice: Choice) -> list[str]:
        """Apply a narrative choice and move to its target scene. Returns notices."""
        apply_effects(choice.sets, choice.logs, self.state, self._here())
        return self._advance(choice.next)

    def run_command(self, raw: str) -> CommandResult:
        """Run a shell command in a terminal scene; if it sets the scene's
        `win_flag`, log the scene's entries and move on."""
        terminal = self.scene.terminal
        assert terminal is not None
        output = self.runner.execute(raw)
        if not self.state.has_flag(terminal.win_flag):
            return CommandResult(output)
        apply_effects({}, terminal.logs, self.state, self._here())
        return CommandResult(output, advanced=True, notices=self._advance(terminal.next))

    def save(self) -> None:
        self.state.save(self.slot_path)

    def _here(self) -> str:
        return f"{self.state.chapter_id}:{self.scene.id}"

    def _advance(self, ref: str) -> list[str]:
        state = self.state
        previous_chapter_id = state.chapter_id
        state.chapter_id, state.scene_id = self.story.resolve(ref, state.chapter_id)
        notices = [f"New mail from {msg.npc_id}: {msg.subject}" for msg in self.runner.advance_scene()]
        # Crossing into a new chapter autosaves -- long stories can span many
        # chapters, and this is the natural "checkpoint" granularity.
        if state.chapter_id != previous_chapter_id:
            self.save()
            notices.append(f"Autosaved -- entering chapter '{state.chapter_id}'.")
        self.enter_scene()
        return notices
