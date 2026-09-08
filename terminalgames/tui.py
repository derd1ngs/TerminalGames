"""The split-pane game screen: a tmux-style layout with a scrollable story
pane (narration + terminal output) and a separate decisions pane (choice
list for narrative scenes, a command input for terminal scenes).

This is purely presentation -- it drives the same engine (`story.py`,
`shell.py`, `dialogue.py`, `state.py`) the old single-stream console UI did,
which is exactly why swapping the frontend didn't require touching any of
that code.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, OptionList, RichLog
from textual.widgets.option_list import Option

from .engine.shell import Network, TerminalRunner
from .engine.state import GameState
from .engine.story import Choice, Scene, Story, apply_effects, check_requires


class GameApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #story-pane {
        height: 1fr;
        border: round $accent;
        padding: 0 1;
    }
    #decisions-pane {
        height: 30%;
        min-height: 5;
        border: round $secondary;
        display: none;
    }
    #command-input {
        dock: bottom;
        display: none;
        border: round $warning;
    }
    """

    BINDINGS = [("ctrl+q", "quit_game", "Save & quit")]

    def __init__(self, story: Story, network: Network, npcs: dict, state: GameState, slot_path: Path):
        super().__init__()
        self.story = story
        self.slot_path = slot_path
        self.runner = TerminalRunner(state=state, network=network, npcs=npcs, save_slot_path=slot_path)
        self.title = story.title
        self.mode = "narrative"
        self.current_scene: Scene | None = None
        self.available_choices: list[Choice] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield RichLog(id="story-pane", wrap=True, markup=True, highlight=False, auto_scroll=True)
            yield OptionList(id="decisions-pane")
            yield Input(id="command-input")
        yield Footer()

    def on_mount(self) -> None:
        story_pane = self.query_one("#story-pane", RichLog)
        story_pane.border_title = "STORY"
        self.query_one("#decisions-pane", OptionList).border_title = "CHOICES"
        self.query_one("#command-input", Input).border_title = "COMMAND"
        self.show_scene()

    def log_text(self, text: str, style: str | None = None) -> None:
        pane = self.query_one("#story-pane", RichLog)
        if style:
            pane.write(f"[{style}]{text}[/{style}]")
        else:
            pane.write(text)
        pane.write("")

    def show_scene(self) -> None:
        state = self.runner.state
        scene = self.story.get_scene(state.chapter_id, state.scene_id)
        self.current_scene = scene
        decisions = self.query_one("#decisions-pane", OptionList)
        cmd_input = self.query_one("#command-input", Input)

        if scene.type == "ending":
            self.mode = "ended"
            self.log_text(scene.text, style="bold yellow")
            self.log_text(f"-- THE END ({scene.id}) --", style="bold red")
            decisions.display = False
            cmd_input.display = False
            return

        self.log_text(scene.text)

        if scene.type == "terminal":
            assert scene.terminal is not None
            self.mode = "terminal"
            self.runner.current_chapter, self.runner.current_scene = state.chapter_id, scene.id
            if scene.terminal.host:
                self.runner.current_host = scene.terminal.host
                self.runner.cwd = "/"
            decisions.display = False
            cmd_input.display = True
            cmd_input.placeholder = f"{self.runner.current_host or 'local'}$"
            cmd_input.value = ""
            self.set_focus(cmd_input)
        else:
            self.mode = "narrative"
            self.available_choices = [c for c in scene.choices if check_requires(c.requires, state)]
            decisions.clear_options()
            for i, choice in enumerate(self.available_choices):
                decisions.add_option(Option(choice.text, id=str(i)))
            decisions.display = True
            cmd_input.display = False
            self.set_focus(decisions)
            if self.available_choices:
                decisions.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.mode != "narrative" or event.option.id is None:
            return
        choice = self.available_choices[int(event.option.id)]
        state = self.runner.state
        scene = self.current_scene
        assert scene is not None
        self.log_text(f"> {choice.text}", style="dim")
        apply_effects(choice.sets, choice.logs, state, f"{state.chapter_id}:{scene.id}")
        state.chapter_id, state.scene_id = self.story.resolve(choice.next, state.chapter_id)
        state.advance_scene()
        self.show_scene()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.mode != "terminal":
            return
        cmd_input = self.query_one("#command-input", Input)
        raw = event.value.strip()
        cmd_input.value = ""
        if not raw:
            return

        prompt = f"{self.runner.current_host or 'local'}$"
        self.log_text(f"{prompt} {raw}", style="dim")

        if raw == ":save":
            self.runner.state.save(self.slot_path)
            self.log_text("Saved.", style="italic green")
            return
        if raw in (":quit", ":exit"):
            self.runner.state.save(self.slot_path)
            self.log_text("Saved. Goodbye.", style="italic green")
            self.exit()
            return

        if raw.split()[0] == "notes":
            # `notes` shells out to a real external editor -- suspend the
            # TUI so that editor gets the real terminal, not our app.
            with self.suspend():
                output = self.runner.execute(raw)
        else:
            output = self.runner.execute(raw)
        if output:
            self.log_text(output)

        state = self.runner.state
        scene = self.current_scene
        assert scene is not None and scene.terminal is not None
        if state.has_flag(scene.terminal.win_flag):
            apply_effects({}, scene.terminal.logs, state, f"{state.chapter_id}:{scene.id}")
            state.chapter_id, state.scene_id = self.story.resolve(scene.terminal.next, state.chapter_id)
            state.advance_scene()
            self.show_scene()

    def action_quit_game(self) -> None:
        self.runner.state.save(self.slot_path)
        self.exit()
