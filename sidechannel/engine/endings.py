"""Which of a story's endings a player has found, across all save slots.

Kept next to the story's slots as `found-endings.txt` (one "ending_id<TAB>
first found at" line each). Deliberately not a .json file: the slot list is
every *.json in that directory.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .story import Scene, Story

ENDINGS_FILE = "found-endings.txt"
_TITLE = re.compile(r"--\s*ENDING:\s*(.+?)\s*--")


def found_endings(story_saves_dir: Path) -> dict[str, str]:
    """ending id -> when it was first found (ISO timestamp)."""
    path = story_saves_dir / ENDINGS_FILE
    if not path.exists():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        ending_id, _, when = line.partition("\t")
        if ending_id:
            found.setdefault(ending_id, when)
    return found


def record_ending(story_saves_dir: Path, ending_id: str) -> bool:
    """Note that `ending_id` was reached. Returns True the first time."""
    if ending_id in found_endings(story_saves_dir):
        return False
    story_saves_dir.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (story_saves_dir / ENDINGS_FILE).open("a") as f:
        f.write(f"{ending_id}\t{when}\n")
    return True


def ending_title(scene: Scene) -> str:
    """The name from an ending's "-- ENDING: Name --" line, else its id."""
    match = _TITLE.search(scene.text)
    return match.group(1) if match else scene.id


def gallery(story: Story, story_saves_dir: Path) -> list[tuple[str, bool]]:
    """Every ending in story order as (title, found)."""
    found = found_endings(story_saves_dir)
    return [
        (ending_title(scene), scene.id in found)
        for chapter in story.chapters.values()
        for scene in chapter.scenes.values()
        if scene.type == "ending"
    ]
