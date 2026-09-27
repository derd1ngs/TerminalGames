"""Finding stories and save slots on disk, and loading a story's optional
data files. UI-free (no Textual, no console), so the CLI, `check_story` and
a browser build all share it. The saves root is always a parameter: the CLI
passes the repo's `saves/`, a browser build a virtual-filesystem path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .dialogue import NPC, load_npcs
from .shell import Network
from .state import GameState, SaveFormatError

STORIES_DIR = Path(__file__).resolve().parent.parent / "stories"
DEFAULT_SLOT = "default"


def discover_stories(stories_dir: Path = STORIES_DIR) -> list[Path]:
    if not stories_dir.exists():
        return []
    return sorted(p for p in stories_dir.iterdir() if (p / "manifest.yaml").exists())


def find_story(story_ref: str, stories: list[Path]) -> Path | None:
    """Match a story reference against a story directory name, or (if that
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


def load_network(story_dir: Path) -> Network:
    network_path = story_dir / "network.yaml"
    return Network.load(network_path) if network_path.exists() else Network()


def load_npc_roster(story_dir: Path) -> dict[str, NPC]:
    npcs_path = story_dir / "npcs.yaml"
    if not npcs_path.exists():
        return {}
    data = yaml.safe_load(npcs_path.read_text()) or {}
    return load_npcs(data)


def save_slot_path(saves_root: Path, story_id: str, slot: str) -> Path:
    return saves_root / story_id / f"{slot}.json"


def list_save_slots(saves_root: Path, story_id: str) -> list[str]:
    save_dir = saves_root / story_id
    if not save_dir.exists():
        return []
    return sorted(p.stem for p in save_dir.glob("*.json"))


def slot_summary(saves_root: Path, story_id: str, slot: str) -> str:
    try:
        state = GameState.load(save_slot_path(saves_root, story_id, slot))
    except SaveFormatError:
        return f"{slot} -- saved by a newer version of Side Channel"
    return f"{slot} -- {state.chapter_id}:{state.scene_id} (saved {state.saved_at or 'unknown'})"


def migrate_legacy_save(saves_root: Path, story_id: str) -> None:
    """Saves used to live flat at <saves_root>/<story_id>.json, one per
    story. The first time a story with such a file is loaded under the
    slot-based layout, move it into the '<DEFAULT_SLOT>' slot."""
    legacy_path = saves_root / f"{story_id}.json"
    if not legacy_path.exists():
        return
    default_path = save_slot_path(saves_root, story_id, DEFAULT_SLOT)
    if default_path.exists():
        return
    default_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.rename(default_path)
