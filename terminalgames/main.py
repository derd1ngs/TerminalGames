"""Entry point.

Story/save selection happens as a plain pre-flight prompt (it's a launcher,
not "the story"); the actual game then runs full-screen as a split-pane
Textual app (see tui.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from .engine.loader import (
    DEFAULT_SLOT,
    STORIES_DIR,
    discover_stories,
    find_story,
    list_save_slots,
    migrate_legacy_save,
    save_slot_path,
    slot_summary,
)
from .engine.session import GameSession
from .engine.story import Story, StoryLoadError
from .tui import GameApp

SAVES_DIR = Path(__file__).resolve().parent.parent / "saves"

console = Console()


def select_story(stories: list[Path]) -> Path:
    if not stories:
        console.print(f"[bold red]No stories found in {STORIES_DIR}[/bold red]")
        sys.exit(1)
    console.print("[bold]Available stories:[/bold]")
    for i, story_dir in enumerate(stories, start=1):
        console.print(f"  {i}. {story_dir.name}")
    while True:
        choice = console.input("Select a story #: ")
        if choice.isdigit() and 1 <= int(choice) <= len(stories):
            return stories[int(choice) - 1]
        console.print("[bold red]Invalid choice.[/bold red]")


def print_save_slots(story_id: str) -> None:
    slots = list_save_slots(SAVES_DIR, story_id)
    if not slots:
        console.print(f"[bold]No saves for '{story_id}'.[/bold]")
        return
    console.print(f"[bold]Save slots for '{story_id}':[/bold]")
    for slot in slots:
        console.print(f"  {slot_summary(SAVES_DIR, story_id, slot)}")


def select_slot(story_id: str) -> str:
    """Interactive slot picker: pick an existing slot to continue, or name a
    new one. With no slots yet, just ask for a name (Enter for the default)."""
    slots = list_save_slots(SAVES_DIR, story_id)
    if not slots:
        name = console.input(f"Save slot name [{DEFAULT_SLOT}]: ").strip()
        return name or DEFAULT_SLOT
    console.print(f"[bold]Save slots for '{story_id}':[/bold]")
    for i, slot in enumerate(slots, start=1):
        console.print(f"  {i}. {slot_summary(SAVES_DIR, story_id, slot)}")
    console.print("  n. new slot")
    while True:
        choice = console.input("Select a slot #, or 'n' for a new one: ").strip().lower()
        if choice == "n":
            name = console.input("New slot name: ").strip()
            if not name:
                console.print("[bold red]Slot name can't be empty.[/bold red]")
                continue
            if name in slots:
                console.print(f"[bold red]Slot '{name}' already exists.[/bold red]")
                continue
            return name
        if choice.isdigit() and 1 <= int(choice) <= len(slots):
            return slots[int(choice) - 1]
        console.print("[bold red]Invalid choice.[/bold red]")


def new_or_continue(slot_path: Path, *, new: bool = False, cont: bool = False) -> bool:
    """Decide whether to start the slot fresh (True) or continue its save
    (False): no save yet, --new, or the player choosing to restart all mean
    fresh. `GameSession.open` then builds or loads the state to match."""
    has_save = slot_path.exists()
    if cont:
        if not has_save:
            console.print(f"[bold red]No save found at slot '{slot_path.stem}'.[/bold red]")
            sys.exit(1)
        return False
    if has_save and not new:
        choice = console.input("Save found. (c)ontinue or (r)estart this slot? ").strip().lower()
        if choice.startswith("c"):
            return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="terminalgames", description="A hacker-themed CLI text adventure engine."
    )
    parser.add_argument(
        "story",
        nargs="?",
        help="Story to launch directly (directory name or manifest id), skipping the picker.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available stories and exit, without launching. "
        "If a story is also given, lists that story's save slots instead.",
    )
    parser.add_argument(
        "--slot",
        help=f"Save slot name (default: '{DEFAULT_SLOT}' when launching a story directly).",
    )
    save_state = parser.add_mutually_exclusive_group()
    save_state.add_argument(
        "--new", action="store_true", help="Start a new game, ignoring any existing save."
    )
    save_state.add_argument(
        "--continue",
        dest="cont",
        action="store_true",
        help="Continue from the existing save (fails if there isn't one).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    stories = discover_stories()

    if args.list and not args.story:
        if not stories:
            console.print(f"[bold red]No stories found in {STORIES_DIR}[/bold red]")
            sys.exit(1)
        console.print("[bold]Available stories:[/bold]")
        for available in stories:
            console.print(f"  {available.name}")
        return

    if args.story:
        story_dir = find_story(args.story, stories)
        if story_dir is None:
            console.print(f"[bold red]No story matching '{args.story}'.[/bold red]")
            sys.exit(1)
    else:
        story_dir = select_story(stories)

    try:
        story = Story.load(story_dir)
    except StoryLoadError as exc:
        console.print(f"[bold red]Failed to load story: {exc}[/bold red]")
        sys.exit(1)

    migrate_legacy_save(SAVES_DIR, story.id)

    if args.list:
        print_save_slots(story.id)
        return

    slot = args.slot or (DEFAULT_SLOT if args.story else select_slot(story.id))
    slot_path = save_slot_path(SAVES_DIR, story.id, slot)
    fresh = new_or_continue(slot_path, new=args.new, cont=args.cont)
    GameApp(GameSession.open(story, story_dir, slot_path, fresh=fresh)).run()


if __name__ == "__main__":
    main()
