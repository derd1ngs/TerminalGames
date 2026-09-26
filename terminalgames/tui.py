"""The 3-pane game screen: a left column with the **story pane** (pure
narration -- scene text and choice echoes, never command output) stacked
above a small **choices** pane (the decision list, narrative scenes only),
and a **terminal** pane filling the right column -- a command's echo/output
log paired with the command input, terminal scenes only.

This is purely presentation -- the game loop itself (choices, commands,
scene transitions, mail delivery, autosave) lives in `engine/session.py`'s
`GameSession`; this app only renders its current scene and forwards input.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, OptionList, RichLog
from textual.widgets.option_list import Option

from .engine.session import GameSession
from .engine.shell import Network, TerminalRunner, complete
from .engine.state import GameState
from .engine.story import Choice, Story


class CommandInput(Input):
    """The terminal's input line, with shell-style Up/Down history. Tab is
    bound here (so it wins over the screen's focus-next binding) but handled
    by the app, which owns the runner that completion needs."""

    BINDINGS = [
        Binding("up", "history(-1)", "Previous command", show=False),
        Binding("down", "history(1)", "Next command", show=False),
        Binding("tab", "app.complete_command", "Complete", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.history: list[str] = []
        self.history_index = 0

    def remember(self, command: str) -> None:
        self.history.append(command)
        self.history_index = len(self.history)

    def action_history(self, step: int) -> None:
        if not self.history:
            return
        self.history_index = max(0, min(len(self.history), self.history_index + step))
        self.value = self.history[self.history_index] if self.history_index < len(self.history) else ""
        self.cursor_position = len(self.value)


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

    BINDINGS = [("ctrl+s", "save_game", "Save"), ("ctrl+q", "quit_game", "Save & quit")]

    def __init__(self, story: Story, network: Network, npcs: dict, state: GameState, slot_path: Path):
        super().__init__()
        self.session = GameSession(story=story, network=network, npcs=npcs, state=state, slot_path=slot_path)
        self.slot_path = slot_path
        self.title = story.title
        self.mode = "narrative"
        self.available_choices: list[Choice] = []

    @property
    def runner(self) -> TerminalRunner:
        return self.session.runner

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
                yield CommandInput(id="command-input")
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

    def show_notices(self, notices: list[str]) -> None:
        for notice in notices:
            self.log_text(notice, style="dim italic")

    def show_scene(self) -> None:
        scene = self.session.scene
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
            self.mode = "terminal"
            decisions.display = False
            terminal_group.display = True
            cmd_input.placeholder = f"{self.runner.current_host or 'local'}$"
            cmd_input.value = ""
            self.set_focus(cmd_input)
        else:
            self.mode = "narrative"
            self.available_choices = self.session.available_choices()
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
        self.log_text(f"> {choice.text}", style="dim")
        self.show_notices(self.session.choose(choice))
        self.show_scene()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.mode != "terminal":
            return
        cmd_input = self.query_one("#command-input", CommandInput)
        raw = event.value.strip()
        cmd_input.value = ""
        if not raw:
            return
        cmd_input.remember(raw)

        prompt = f"{self.runner.current_host or 'local'}$"
        self.log_terminal(f"{prompt} {escape(raw)}", style="dim")

        if raw == ":save":
            self.session.save()
            self.log_terminal("Saved.", style="italic green")
            return
        if raw in (":quit", ":exit"):
            self.session.save()
            self.log_terminal("Saved. Goodbye.", style="italic green")
            self.exit()
            return

        result = self.session.run_command(raw)
        if result.output:
            self.log_terminal(escape(result.output))
        if result.advanced:
            self.show_notices(result.notices)
            self.show_scene()

    def action_complete_command(self) -> None:
        cmd_input = self.query_one("#command-input", CommandInput)
        line = cmd_input.value
        candidates = complete(self.runner, line)
        if not candidates:
            return
        prefix = line.split(" ")[-1]
        common = os.path.commonprefix(candidates)
        if len(candidates) == 1 and not common.endswith("/"):
            common += " "
        elif common == prefix:
            self.log_terminal(escape("  ".join(candidates)), style="dim")
        cmd_input.value = line[: len(line) - len(prefix)] + common
        cmd_input.cursor_position = len(cmd_input.value)

    def action_save_game(self) -> None:
        self.session.save()
        self.log_text("Saved.", style="italic green")

    def action_quit_game(self) -> None:
        self.session.save()
        self.exit()
