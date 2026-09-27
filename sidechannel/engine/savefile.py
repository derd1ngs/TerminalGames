"""Exporting a save slot as one self-contained JSON document, and importing
it back: the slot's GameState plus every file in its sandbox (host
filesystems as the player left them, `set` edits included, and mail).

The browser build uses this for save download/upload, since its saves
otherwise live only in that browser's IndexedDB. Everything in a sandbox is
text, so the document is plain JSON.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .loader import save_slot_path
from .state import GameState, SaveFormatError
from .story import Story, StoryLoadError

FORMAT = "terminalgames-save"
VERSION = 1
SLOT_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


class SaveFileError(Exception):
    pass


def export_slot(saves_root: Path, story_id: str, slot: str) -> dict[str, Any]:
    slot_path = save_slot_path(saves_root, story_id, slot)
    if not slot_path.exists():
        raise SaveFileError(f"no save in slot '{slot}'")
    sandbox = GameState.sandbox_dir_for(slot_path)
    files = {}
    if sandbox.exists():
        files = {
            p.relative_to(sandbox).as_posix(): p.read_text()
            for p in sorted(sandbox.rglob("*"))
            if p.is_file()
        }
    return {
        "format": FORMAT,
        "version": VERSION,
        "story_id": story_id,
        "slot": slot,
        "state": json.loads(slot_path.read_text()),
        "sandbox": files,
    }


def validate_import(story: Story, data: Any, slot: str | None = None) -> str:
    """Check an exported document completely without writing anything.
    Returns the slot it would be imported into (`slot`, or by default the
    one it was exported from). Raises SaveFileError if the document is
    malformed, from another story, or points at a scene this story no longer
    has."""
    return _validated(story, data, slot)[0]


def import_slot(saves_root: Path, story: Story, data: Any, slot: str | None = None) -> str:
    """Validate an exported document completely (see `validate_import`),
    then write it into its slot, replacing that slot's save and sandbox.
    Returns the slot name. Nothing is written if validation fails."""
    slot, state, files = _validated(story, data, slot)
    slot_path = save_slot_path(saves_root, story.id, slot)
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    slot_path.write_text(json.dumps(state.to_dict(), indent=2))
    sandbox = GameState.sandbox_dir_for(slot_path)
    shutil.rmtree(sandbox, ignore_errors=True)
    for name, content in files.items():
        target = sandbox.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return slot


def _validated(story: Story, data: Any, slot: str | None) -> tuple[str, GameState, dict[str, str]]:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SaveFileError("not a Side Channel save file")
    if data.get("version") != VERSION:
        raise SaveFileError(f"unsupported save file version {data.get('version')!r}")
    if data.get("story_id") != story.id:
        raise SaveFileError(f"this save is for '{data.get('story_id')}', not '{story.id}'")
    slot = slot or data.get("slot")
    if not isinstance(slot, str) or not SLOT_NAME.fullmatch(slot):
        raise SaveFileError(f"invalid slot name {slot!r}")

    try:
        state = GameState.from_dict(data["state"])
    except SaveFormatError as exc:
        raise SaveFileError(str(exc)) from exc
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SaveFileError(f"damaged game state ({exc})") from exc
    if state.story_id != story.id:
        raise SaveFileError("game state belongs to another story")
    try:
        story.get_scene(state.chapter_id, state.scene_id)
    except StoryLoadError as exc:
        raise SaveFileError(f"save points at a scene this story doesn't have: {exc}") from exc

    files = data.get("sandbox", {})
    if not isinstance(files, dict):
        raise SaveFileError("damaged sandbox")
    for name, content in files.items():
        path = PurePosixPath(name)
        if not isinstance(content, str) or path.is_absolute() or ".." in path.parts or not path.parts:
            raise SaveFileError(f"unsafe or damaged sandbox file {name!r}")
    return slot, state, files
