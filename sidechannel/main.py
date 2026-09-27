"""Entry point.

Story/save selection happens as a plain pre-flight prompt (it's a launcher,
not "the story"); the actual game then runs full-screen as a split-pane
Textual app (see tui.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from platformdirs import user_data_path
from rich.console import Console
from rich.markup import escape

from .engine.catalog import StoryInfo, catalog
from .engine.endings import gallery
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
from .engine.session import GameSession, StaleSaveError
from .engine.story import Story, StoryLoadError
from .tui import GameApp


def default_saves_dir(package_dir: Path) -> Path:
    """A repo checkout (pyproject.toml next to the package) keeps its saves
    in the repo's gitignored `saves/`, as it always has; an installed copy
    uses the per-user data directory (e.g. ~/.local/share/sidechannel/saves)
    rather than writing into site-packages."""
    repo_root = package_dir.parent
    if (repo_root / "pyproject.toml").exists():
        return repo_root / "saves"
    return user_data_path("sidechannel") / "saves"


SAVES_DIR = default_saves_dir(Path(__file__).resolve().parent)

console = Console()


def select_story(stories: list[StoryInfo], per_page: int = 9) -> StoryInfo:
    """Interactive story picker over the catalog (newest first), a page at a
    time. Numbers are global (1..N), so a number always means the same story;
    `n`/`p` flip pages when there's more than one."""
    if not stories:
        console.print(f"[bold red]No stories found in {STORIES_DIR}[/bold red]")
        sys.exit(1)
    pages = -(-len(stories) // per_page)
    page = 0
    while True:
        heading = "Available stories" + (f" (page {page + 1}/{pages})" if pages > 1 else "")
        console.print(f"[bold]{heading}:[/bold]")
        first = page * per_page
        for i, info in enumerate(stories[first : first + per_page], start=first + 1):
            console.print(f"  {i}. [bold]{escape(info.title)}[/bold]  [dim]{escape(info.summary)}[/dim]")
            if info.description:
                console.print(f"     [dim]{escape(info.description)}[/dim]")
        paging = ", n/p for the next/previous page" if pages > 1 else ""
        choice = console.input(f"Select a story #{paging}: ").strip().lower()
        if choice.isdigit() and 1 <= int(choice) <= len(stories):
            return stories[int(choice) - 1]
        if choice == "n" and page + 1 < pages:
            page += 1
        elif choice == "p" and page > 0:
            page -= 1
        else:
            console.print("[bold red]Invalid choice.[/bold red]")


def print_save_slots(story_id: str) -> None:
    slots = list_save_slots(SAVES_DIR, story_id)
    if not slots:
        console.print(f"[bold]No saves for '{story_id}'.[/bold]")
        return
    console.print(f"[bold]Save slots for '{story_id}':[/bold]")
    for slot in slots:
        console.print(f"  {slot_summary(SAVES_DIR, story_id, slot)}")


def print_endings(story: Story) -> None:
    """The story's endings gallery, if any ending has been found yet."""
    entries = gallery(story, SAVES_DIR / story.id)
    found = [title for title, was_found in entries if was_found]
    if found:
        hidden = len(entries) - len(found)
        listing = ", ".join(found) + (f", and {hidden} more" if hidden else "")
        console.print(f"[bold]Endings found: {len(found)}/{len(entries)}[/bold] -- {listing}")


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
        prog="sidechannel", description="Side Channel -- a hacker-themed text adventure in your terminal."
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
        for info in catalog(SAVES_DIR):
            published = f" (published {info.published_label})" if info.published else ""
            console.print(f"  {info.ref:24} {escape(info.title)}{published}")
        return

    if args.story:
        story_dir = find_story(args.story, stories)
        if story_dir is None:
            console.print(f"[bold red]No story matching '{args.story}'.[/bold red]")
            sys.exit(1)
    else:
        story_dir = find_story(select_story(catalog(SAVES_DIR)).ref, stories)
        assert story_dir is not None

    try:
        story = Story.load(story_dir)
    except StoryLoadError as exc:
        console.print(f"[bold red]Failed to load story: {exc}[/bold red]")
        sys.exit(1)

    migrate_legacy_save(SAVES_DIR, story.id)

    if args.list:
        print_save_slots(story.id)
        print_endings(story)
        return
    if not args.story:
        print_endings(story)

    slot = args.slot or (DEFAULT_SLOT if args.story else select_slot(story.id))
    slot_path = save_slot_path(SAVES_DIR, story.id, slot)
    fresh = new_or_continue(slot_path, new=args.new, cont=args.cont)
    GameApp(open_session(story, story_dir, slot_path, fresh=fresh, cont=args.cont)).run()


def open_session(story: Story, story_dir: Path, slot_path: Path, *, fresh: bool, cont: bool) -> GameSession:
    """GameSession.open, with story-file errors and stale saves explained
    instead of ending in a traceback. A stale save offers a restart, unless
    --continue was given (which promises not to touch the slot)."""
    try:
        return GameSession.open(story, story_dir, slot_path, fresh=fresh)
    except StaleSaveError as exc:
        console.print(f"[bold red]Can't continue slot '{slot_path.stem}': {exc}.[/bold red]")
        if cont:
            sys.exit(1)
        choice = console.input("Restart this slot from the beginning? (y/N) ").strip().lower()
        if not choice.startswith("y"):
            sys.exit(1)
        return open_session(story, story_dir, slot_path, fresh=True, cont=False)
    except StoryLoadError as exc:
        console.print(f"[bold red]Failed to load story: {exc}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
