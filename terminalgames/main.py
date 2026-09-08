"""Entry point.

Story/save selection happens as a plain pre-flight prompt (it's a launcher,
not "the story"); the actual game then runs full-screen as a split-pane
Textual app (see tui.py).
"""

from __future__ import annotations

import argparse
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


def save_slot_path(story_id: str) -> Path:
    return SAVES_DIR / f"{story_id}.json"


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


def new_or_continue(story: Story, *, new: bool = False, cont: bool = False) -> GameState:
    slot_path = save_slot_path(story.id)
    has_save = slot_path.exists()
    if cont:
        if not has_save:
            console.print(f"[bold red]No save found for '{story.id}'.[/bold red]")
            sys.exit(1)
        return GameState.load(slot_path)
    if has_save and not new:
        choice = console.input("Save found. (c)ontinue or (n)ew game? ").strip().lower()
        if choice.startswith("c"):
            return GameState.load(slot_path)
    chapter_id, scene_id = story.start_ref()
    return GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)


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
        "--list", action="store_true", help="List available stories and exit, without launching."
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

    if args.list:
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

    state = new_or_continue(story, new=args.new, cont=args.cont)
    network = load_network(story_dir)
    npcs = load_npc_roster(story_dir)

    GameApp(story=story, network=network, npcs=npcs, state=state, slot_path=save_slot_path(story.id)).run()


if __name__ == "__main__":
    main()
