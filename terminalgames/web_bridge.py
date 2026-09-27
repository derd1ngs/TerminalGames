"""JSON-string facade over `GameSession` for the browser build (`web/`).

The page runs this module in Pyodide and calls its functions from
JavaScript. Every function takes and returns plain strings/JSON, so the JS
side never touches Python objects. Like the engine it only needs PyYAML --
no Textual, no Rich (see tests/test_engine_is_ui_free.py).

One game at a time: `start()` opens a session, the other game functions act
on it. The saves root is set once with `init()` -- in the browser it is an
IndexedDB-backed mount, in tests a temp dir.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from .engine.endings import gallery
from .engine.loader import (
    discover_stories,
    find_story,
    list_save_slots,
    migrate_legacy_save,
    save_slot_path,
    slot_summary,
)
from .engine.savefile import SaveFileError, validate_import
from .engine.savefile import export_slot as export_slot_doc
from .engine.savefile import import_slot as import_slot_doc
from .engine.session import GameSession, StaleSaveError
from .engine.shell import complete as shell_complete
from .engine.story import Story

_saves_root = Path("/saves")
_session: Optional[GameSession] = None


def init(saves_root: str) -> None:
    global _saves_root, _session
    _saves_root = Path(saves_root)
    _session = None


# --- markup ------------------------------------------------------------------
#
# Scene text may use Rich-style markup (`[bold]...[/bold]`, `[bold red]`,
# `[/]`), which the TUI renders natively. Here each style word becomes a CSS
# class (`m-bold`, `m-red`); `\[` is a literal bracket, like in Rich.

_MARKUP_TAG = re.compile(r"\\\[|\[(/?)([a-z][a-z ]*)?\]")


def markup_to_html(text: str) -> str:
    out: list[str] = []
    depth = 0
    pos = 0
    for match in _MARKUP_TAG.finditer(text):
        out.append(html.escape(text[pos : match.start()], quote=False))
        pos = match.end()
        closing, style = match.group(1), match.group(2)
        if match.group(0) == "\\[":
            out.append("[")
        elif closing:
            if depth:
                out.append("</span>")
                depth -= 1
        elif style:
            out.append(f'<span class="{" ".join("m-" + word for word in style.split())}">')
            depth += 1
        else:
            out.append(html.escape(match.group(0), quote=False))
    out.append(html.escape(text[pos:], quote=False))
    out.append("</span>" * depth)
    return "".join(out)


_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def scene_html(text: str) -> str:
    """Scene text for the page: story YAML hard-wraps paragraphs at ~72
    columns for the terminal, so lines within a paragraph are joined (the
    browser wraps them to the pane) while blank lines still separate
    paragraphs; markup becomes HTML and `backtick` spans become <code>."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    joined = "\n\n".join(" ".join(line.strip() for line in p.splitlines()) for p in paragraphs)
    return _CODE_SPAN.sub(r"<code>\1</code>", markup_to_html(joined))


# --- menus -------------------------------------------------------------------


def list_stories() -> str:
    stories = []
    for story_dir in discover_stories():
        manifest = yaml.safe_load((story_dir / "manifest.yaml").read_text()) or {}
        stories.append({"ref": story_dir.name, "id": manifest.get("id"), "title": manifest.get("title")})
    return json.dumps(stories)


def list_slots(story_ref: str) -> str:
    story = _load_story(story_ref)
    migrate_legacy_save(_saves_root, story.id)
    slots = list_save_slots(_saves_root, story.id)
    return json.dumps([{"slot": s, "summary": slot_summary(_saves_root, story.id, s)} for s in slots])


def list_endings(story_ref: str) -> str:
    """The story's endings in order; titles only for the ones found."""
    story = _load_story(story_ref)
    return json.dumps(
        [
            {"title": title if found else None, "found": found}
            for title, found in gallery(story, _saves_root / story.id)
        ]
    )


def export_slot(story_ref: str, slot: str) -> str:
    """The slot as a downloadable save file (pretty-printed JSON)."""
    story = _load_story(story_ref)
    return json.dumps(export_slot_doc(_saves_root, story.id, slot), indent=2)


def import_slot(story_ref: str, text: str, overwrite: bool) -> str:
    """Import a save file's text into its slot. Returns {"slot": name} on
    success, {"error": message} if the file is unusable, or {"exists": name}
    -- without importing -- if that slot already has a save and `overwrite`
    is false, so the page can ask first."""
    story = _load_story(story_ref)
    try:
        data = json.loads(text)
        slot = validate_import(story, data)
        if not overwrite and save_slot_path(_saves_root, story.id, slot).exists():
            return json.dumps({"exists": slot})
        return json.dumps({"slot": import_slot_doc(_saves_root, story, data)})
    except ValueError:
        return json.dumps({"error": "not a Side Channel save file"})
    except SaveFileError as exc:
        return json.dumps({"error": str(exc)})


# --- game ----------------------------------------------------------------------


def start(story_ref: str, slot: str, fresh: bool) -> str:
    """Open a save slot. A slot without a save always starts fresh. If the
    save points at a scene the story no longer has, returns {"stale": message}
    instead of a view, so the page can offer a restart."""
    global _session
    story_dir = _story_dir(story_ref)
    story = Story.load(story_dir)
    slot_path = save_slot_path(_saves_root, story.id, slot)
    try:
        _session = GameSession.open(story, story_dir, slot_path, fresh=fresh or not slot_path.exists())
    except StaleSaveError as exc:
        return json.dumps({"stale": f"Can't continue slot '{slot}': {exc}."})
    if fresh:
        _session.save()  # a new game shows up in the slot list right away
    return _view(entered=True)


def choose(index: int) -> str:
    session = _require_session()
    notices = session.choose(session.available_choices()[index])
    return _view(entered=True, notices=notices)


def command(raw: str) -> str:
    """Run one line typed into the terminal. `:save` is handled here (as in
    the TUI); leaving the game (`:quit`) is the page's job."""
    session = _require_session()
    raw = raw.strip()
    if raw == ":save" and not session.runner.awaiting_password:
        session.save()
        return _view(output="Saved.")
    result = session.run_command(raw)
    return _view(entered=result.advanced, output=result.output, notices=result.notices, clear=result.clear)


def complete(line: str) -> str:
    return json.dumps(shell_complete(_require_session().runner, line))


def save() -> str:
    _require_session().save()
    return _view()


def email_contacts() -> str:
    return json.dumps([{"id": npc.id, "name": npc.name} for npc in _require_session().email_contacts()])


def compose_mail(to: str, subject: str, body: str) -> str:
    """The page's Mail dialog (see GameSession.compose_mail)."""
    result = _require_session().compose_mail(to, subject, body)
    return _view(entered=result.advanced, output=result.output, notices=result.notices)


# --- helpers -------------------------------------------------------------------


def _story_dir(story_ref: str) -> Path:
    story_dir = find_story(story_ref, discover_stories())
    if story_dir is None:
        raise ValueError(f"No story matching '{story_ref}'")
    return story_dir


def _load_story(story_ref: str) -> Story:
    return Story.load(_story_dir(story_ref))


def _require_session() -> GameSession:
    if _session is None:
        raise RuntimeError("No game in progress -- call start() first")
    return _session


def _view(
    *,
    entered: bool = False,
    output: Optional[str] = None,
    notices: Optional[list[str]] = None,
    clear: bool = False,
) -> str:
    """What the page needs to render after any action. `entered` means the
    player just arrived in `scene` (so its text should be shown)."""
    session = _require_session()
    scene = session.scene
    view: dict[str, Any] = {
        "title": session.story.title,
        "entered": entered,
        "scene": {"id": scene.id, "type": scene.type, "html": scene_html(scene.text)},
        "choices": [c.text for c in session.available_choices()] if scene.type == "narrative" else [],
        "prompt": session.runner.prompt,
        "secret": session.runner.awaiting_password is not None,  # next line is a password
        "output": output,
        "notices": notices or [],
        "clear": clear,  # `clear`: empty the terminal pane before showing output
    }
    if scene.type == "ending":
        found, total = session.endings_found()
        view["endings"] = {"found": found, "total": total}
    return json.dumps(view)
