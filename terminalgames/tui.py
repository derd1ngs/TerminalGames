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

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, RichLog, Select, TextArea
from textual.widgets.option_list import Option

from .engine.session import CommandResult, GameSession
from .engine.shell import TerminalRunner, complete
from .engine.story import Choice


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
        if not self.history or self.password:
            return
        self.history_index = max(0, min(len(self.history), self.history_index + step))
        self.value = self.history[self.history_index] if self.history_index < len(self.history) else ""
        self.cursor_position = len(self.value)


class ComposeMailScreen(ModalScreen[tuple[str, str, str] | None]):
    """`mail compose`: write a message to an email contact. Returns
    (to, subject, body), or None if cancelled."""

    DEFAULT_CSS = """
    ComposeMailScreen { align: center middle; }
    #compose-box {
        width: 72; max-width: 100%; height: auto; max-height: 100%;
        overflow-y: auto; border: round $warning; background: $surface; padding: 0 2;
    }
    #compose-box Label { margin-top: 1; color: $text-muted; }
    #compose-box Label.first { margin-top: 0; }
    #compose-body { height: 6; }
    #compose-error { color: $error; }
    #compose-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #compose-buttons Button { margin-left: 2; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, contacts: list[tuple[str, str]]) -> None:
        super().__init__()
        self.contacts = contacts  # (id, display name)

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-box"):
            yield Label("To", classes="first")
            yield Select(
                [(name, npc_id) for npc_id, name in self.contacts],
                allow_blank=False,
                value=self.contacts[0][0],
                compact=True,
                id="compose-to",
            )
            # Compact widgets keep the form within an 80x24 terminal.
            yield Label("Subject")
            yield Input(compact=True, id="compose-subject")
            yield Label("Message")
            yield TextArea(compact=True, id="compose-body")
            yield Label("", id="compose-error")
            with Horizontal(id="compose-buttons"):
                yield Button("Cancel", compact=True, id="compose-cancel")
                yield Button("Send", variant="primary", compact=True, id="compose-send")

    def on_mount(self) -> None:
        self.query_one("#compose-box", Vertical).border_title = "NEW MESSAGE"
        self.query_one("#compose-subject", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compose-cancel":
            self.dismiss(None)
            return
        subject = self.query_one("#compose-subject", Input).value.strip()
        if not subject:
            self.query_one("#compose-error", Label).update("A subject is required.")
            self.query_one("#compose-subject", Input).focus()
            return
        to = str(self.query_one("#compose-to", Select).value)
        self.dismiss((to, subject, self.query_one("#compose-body", TextArea).text))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # Enter in the subject moves on to the message, it doesn't send
        self.query_one("#compose-body", TextArea).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


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

    def __init__(self, session: GameSession):
        super().__init__()
        self.session = session
        self.slot_path = session.slot_path
        self.title = session.story.title
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
            found, total = self.session.endings_found()
            self.log_text(f"Endings found: {found}/{total}", style="dim italic")
            decisions.display = False
            terminal_group.display = False
            return

        self.log_text(scene.text)

        if scene.type == "terminal":
            self.mode = "terminal"
            decisions.display = False
            terminal_group.display = True
            cmd_input.placeholder = self.runner.prompt
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
        if self.runner.awaiting_password:
            # A password line: masked, kept out of history, never a meta command.
            self.log_terminal(f"{self.runner.prompt} ********", style="dim")
            self.run_command(raw)
            return
        cmd_input.remember(raw)
        self.log_terminal(f"{self.runner.prompt} {escape(raw)}", style="dim")

        if raw == ":save":
            self.session.save()
            self.log_terminal("Saved.", style="italic green")
            return
        if raw in (":quit", ":exit"):
            self.session.save()
            self.log_terminal("Saved. Goodbye.", style="italic green")
            self.exit()
            return
        if " ".join(raw.split()) == "mail compose" and self.session.email_contacts():
            self.open_compose()
            return

        self.run_command(raw)

    def run_command(self, raw: str) -> None:
        self.show_result(self.session.run_command(raw))

    def open_compose(self) -> None:
        contacts = [(npc.id, npc.name) for npc in self.session.email_contacts()]
        self.push_screen(ComposeMailScreen(contacts), callback=self.send_mail)

    def send_mail(self, message: tuple[str, str, str] | None) -> None:
        if message is None:
            self.log_terminal("(message discarded)", style="dim")
            return
        to, subject, body = message
        self.log_terminal(f'(mail to {escape(to)}: "{escape(subject)}")', style="dim")
        self.show_result(self.session.compose_mail(to, subject, body))

    def show_result(self, result: CommandResult) -> None:
        if result.output:
            self.log_terminal(escape(result.output))
        cmd_input = self.query_one("#command-input", CommandInput)
        cmd_input.placeholder = self.runner.prompt
        cmd_input.password = self.runner.awaiting_password is not None
        if result.advanced:
            self.show_notices(result.notices)
            self.show_scene()

    def action_complete_command(self) -> None:
        cmd_input = self.query_one("#command-input", CommandInput)
        if cmd_input.password:
            return
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
