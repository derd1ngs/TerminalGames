"""Entry point.

Story/save selection happens as a plain pre-flight prompt (it's a launcher,
not "the story"); the actual game then runs full-screen as a split-pane
Textual app (see tui.py).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml
from rich.console import Console

from .engine.dialogue import load_npcs
from .engine.shell import Network
from .engine.state import GameState
from .engine.story import Story, StoryLoadError
from .tui import GameApp

STORIES_DIR = Path(__file__).parent / "stories"
SAVES_DIR = Path(__file__).resolve().parent.parent / "saves"
DEFAULT_SLOT = "default"

console = Console()


def discover_stories() -> list[Path]:
    if not STORIES_DIR.exists():
        return []
    return sorted(p for p in STORIES_DIR.iterdir() if (p / "manifest.yaml").exists())


def load_network(story_dir: Path) -> Network:
    network_path = story_dir / "network.yaml"
    return Network.load(network_path) if network_path.exists() else Network()


def load_npc_roster(story_dir: Path) -> dict:
    npcs_path = story_dir / "npcs.yaml"
    if not npcs_path.exists():
        return {}
    data = yaml.safe_load(npcs_path.read_text()) or {}
    return load_npcs(data)


def story_saves_dir(story_id: str) -> Path:
    return SAVES_DIR / story_id


def save_slot_path(story_id: str, slot: str) -> Path:
    return story_saves_dir(story_id) / f"{slot}.json"


def list_save_slots(story_id: str) -> list[str]:
    save_dir = story_saves_dir(story_id)
    if not save_dir.exists():
        return []
    return sorted(p.stem for p in save_dir.glob("*.json"))


def migrate_legacy_save(story_id: str) -> None:
    """Saves used to live flat at saves/<story_id>.json, one per story. The
    first time a story with such a file is loaded under the slot-based
    layout, move it into the '<DEFAULT_SLOT>' slot."""
    legacy_path = SAVES_DIR / f"{story_id}.json"
    if not legacy_path.exists():
        return
    default_path = save_slot_path(story_id, DEFAULT_SLOT)
    if default_path.exists():
        return
    default_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.rename(default_path)


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


def find_story(story_ref: str, stories: list[Path]) -> Path | None:
    """Match a --story argument against a story directory name, or (if that
    fails) each story's manifest id -- so both `story_01_zero_day` and
    `zero_day` work."""
    for story_dir in stories:
        if story_dir.name == story_ref:
            return story_dir
    for story_dir in stories:
        manifest = yaml.safe_load((story_dir / "manifest.yaml").read_text()) or {}
        if manifest.get("id") == story_ref:
            return story_dir
    return None


def _slot_summary(story_id: str, slot: str) -> str:
    state = GameState.load(save_slot_path(story_id, slot))
    return f"{slot} -- {state.chapter_id}:{state.scene_id} (saved {state.saved_at or 'unknown'})"


def print_save_slots(story_id: str) -> None:
    slots = list_save_slots(story_id)
    if not slots:
        console.print(f"[bold]No saves for '{story_id}'.[/bold]")
        return
    console.print(f"[bold]Save slots for '{story_id}':[/bold]")
    for slot in slots:
        console.print(f"  {_slot_summary(story_id, slot)}")


def select_slot(story_id: str) -> str:
    """Interactive slot picker: pick an existing slot to continue, or name a
    new one. With no slots yet, just ask for a name (Enter for the default)."""
    slots = list_save_slots(story_id)
    if not slots:
        name = console.input(f"Save slot name [{DEFAULT_SLOT}]: ").strip()
        return name or DEFAULT_SLOT
    console.print(f"[bold]Save slots for '{story_id}':[/bold]")
    for i, slot in enumerate(slots, start=1):
        console.print(f"  {i}. {_slot_summary(story_id, slot)}")
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


def new_or_continue(
    story: Story, slot_path: Path, *, new: bool = False, cont: bool = False
) -> tuple[GameState, bool]:
    """Returns (state, is_fresh). is_fresh is True whenever a brand-new
    GameState was constructed (no save yet, --new, or the player chose to
    restart) -- the caller uses it to decide whether the slot's sandbox
    directory needs to be wiped and rematerialized alongside it, rather than
    reused as-is."""
    has_save = slot_path.exists()
    if cont:
        if not has_save:
            console.print(f"[bold red]No save found at slot '{slot_path.stem}'.[/bold red]")
            sys.exit(1)
        return GameState.load(slot_path), False
    if has_save and not new:
        choice = console.input("Save found. (c)ontinue or (r)estart this slot? ").strip().lower()
        if choice.startswith("c"):
            return GameState.load(slot_path), False
    chapter_id, scene_id = story.start_ref()
    return GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id), True


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

    migrate_legacy_save(story.id)

    if args.list:
        print_save_slots(story.id)
        return

    slot = args.slot or (DEFAULT_SLOT if args.story else select_slot(story.id))
    slot_path = save_slot_path(story.id, slot)

    state, is_fresh = new_or_continue(story, slot_path, new=args.new, cont=args.cont)
    network = load_network(story_dir)
    sandbox_root = GameState.sandbox_dir_for(slot_path)
    if is_fresh:
        shutil.rmtree(sandbox_root, ignore_errors=True)
    network.materialize(sandbox_root)
    npcs = load_npc_roster(story_dir)

    GameApp(story=story, network=network, npcs=npcs, state=state, slot_path=slot_path).run()


if __name__ == "__main__":
    main()
