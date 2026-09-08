"""The 3-pane game screen: a left column with the **story pane** (pure
narration -- scene text and choice echoes, never command output) stacked
above a small **choices** pane (the decision list, narrative scenes only),
and a **terminal** pane filling the right column -- a command's echo/output
log paired with the command input, terminal scenes only.

This is purely presentation -- it drives the same engine (`story.py`,
`shell.py`, `dialogue.py`, `state.py`) the old single-stream console UI did,
which is exactly why swapping the frontend didn't require touching any of
that code.
"""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
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
    #body {
        height: 1fr;
    }
    #left-pane {
        width: 38%;
        min-width: 28;
        height: 1fr;
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
    #terminal-group {
        width: 1fr;
        height: 1fr;
        border: round $warning;
        display: none;
    }
    #terminal-pane {
        height: 1fr;
        padding: 0 1;
    }
    #command-input {
        height: 3;
        border-top: solid $warning;
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
        with Horizontal(id="body"):
            with Vertical(id="left-pane"):
                # min_width defaults to 78 and floors the wrap width even
                # with shrink=True -- without overriding it, a pane narrower
                # than 78 columns (as both of these now routinely are) would
                # wrap wider than it can display and crop text horizontally.
                yield RichLog(
                    id="story-pane", wrap=True, min_width=1, markup=True, highlight=False, auto_scroll=True
                )
                yield OptionList(id="decisions-pane")
            with Vertical(id="terminal-group"):
                yield RichLog(
                    id="terminal-pane",
                    wrap=True,
                    min_width=1,
                    markup=True,
                    highlight=False,
                    auto_scroll=True,
                )
                yield Input(id="command-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#story-pane", RichLog).border_title = "STORY"
        self.query_one("#decisions-pane", OptionList).border_title = "CHOICES"
        self.query_one("#terminal-group", Vertical).border_title = "TERMINAL"
        self.show_scene()

    def _write_pane(self, pane_id: str, text: str, style: str | None = None) -> None:
        pane = self.query_one(pane_id, RichLog)
        if style:
            pane.write(f"[{style}]{text}[/{style}]")
        else:
            pane.write(text)
        pane.write("")

    def log_text(self, text: str, style: str | None = None) -> None:
        """Pure narration: scene text, choice echoes, endings, system messages."""
        self._write_pane("#story-pane", text, style)

    def log_terminal(self, text: str, style: str | None = None) -> None:
        """A command's echo or its output -- the terminal pane's transcript."""
        self._write_pane("#terminal-pane", text, style)

    def maybe_autosave(self, previous_chapter_id: str) -> None:
        """Crossing into a new chapter autosaves -- long stories can span many
        chapters, and this is the natural "checkpoint" granularity."""
        state = self.runner.state
        if state.chapter_id == previous_chapter_id:
            return
        state.save(self.slot_path)
        self.log_text(f"Autosaved -- entering chapter '{state.chapter_id}'.", style="dim italic")

    def show_scene(self) -> None:
        state = self.runner.state
        scene = self.story.get_scene(state.chapter_id, state.scene_id)
        self.current_scene = scene
        decisions = self.query_one("#decisions-pane", OptionList)
        terminal_group = self.query_one("#terminal-group", Vertical)
        cmd_input = self.query_one("#command-input", Input)

        if scene.type == "ending":
            self.mode = "ended"
            self.log_text(scene.text, style="bold yellow")
            self.log_text(f"-- THE END ({scene.id}) --", style="bold red")
            decisions.display = False
            terminal_group.display = False
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
            terminal_group.display = True
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
            terminal_group.display = False
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
        previous_chapter_id = state.chapter_id
        state.chapter_id, state.scene_id = self.story.resolve(choice.next, state.chapter_id)
        state.advance_scene()
        self.maybe_autosave(previous_chapter_id)
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
        self.log_terminal(f"{prompt} {escape(raw)}", style="dim")

        if raw == ":save":
            self.runner.state.save(self.slot_path)
            self.log_terminal("Saved.", style="italic green")
            return
        if raw in (":quit", ":exit"):
            self.runner.state.save(self.slot_path)
            self.log_terminal("Saved. Goodbye.", style="italic green")
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
            self.log_terminal(escape(output))

        state = self.runner.state
        scene = self.current_scene
        assert scene is not None and scene.terminal is not None
        if state.has_flag(scene.terminal.win_flag):
            apply_effects({}, scene.terminal.logs, state, f"{state.chapter_id}:{scene.id}")
            previous_chapter_id = state.chapter_id
            state.chapter_id, state.scene_id = self.story.resolve(scene.terminal.next, state.chapter_id)
            state.advance_scene()
            self.maybe_autosave(previous_chapter_id)
            self.show_scene()

    def action_quit_game(self) -> None:
        self.runner.state.save(self.slot_path)
        self.exit()
