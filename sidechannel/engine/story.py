"""Story/Chapter/Scene graph: loading from YAML, validation, and condition/effect evaluation.

A Story is a manifest plus an ordered list of Chapter files, each a scene graph.
This shape is identical whether a story is one short chapter or dozens of
chapters spanning a long investigation -- scenes can reference scenes in other
chapters via "chapter_id:scene_id", so a lead found in chapter 3 can pay off
in chapter 7 without any special-casing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from . import schema
from .journal import JournalEntry
from .schema import StoryLoadError
from .state import GameState

SCENE_TYPES = {"narrative", "terminal", "ending"}


@dataclass
class Choice:
    text: str
    next: str
    requires: Optional[dict[str, Any]] = None
    sets: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Choice":
        schema.check_keys(data, {"text", "next", "requires", "sets", "logs"}, "choice")
        schema.check_effects(data, "choice")
        try:
            return cls(
                text=data["text"],
                next=data["next"],
                requires=data.get("requires"),
                sets=dict(data.get("sets", {})),
                logs=list(data.get("logs", [])),
            )
        except KeyError as exc:
            raise StoryLoadError(f"Choice missing required key {exc}") from exc


@dataclass
class Trace:
    """A terminal scene's trace meter: every command that touches a host
    raises it, and at `limit` the connection drops and the story moves to
    `on_trace` (a "you got traced" scene -- a retry, a setback, an ending)."""

    limit: int
    on_trace: str


@dataclass
class TerminalBlock:
    """A terminal-type scene. `win_flag` is the flag the main loop watches for
    to know the puzzle is solved -- it's set directly by whichever shell
    command satisfies the puzzle (e.g. a service's `on_fix_flag` in
    network.yaml, set by `systemctl restart` once its config validates).

    `ordered_commands` makes the scene itself a procedure puzzle: the scene
    sets `win_flag` as soon as the player's most recent commands in it are
    exactly this sequence (whitespace-normalized).

    `hints` are revealed one at a time by the `hint` command, so order them
    from a gentle nudge to nearly the answer."""

    win_flag: str
    next: str
    host: Optional[str] = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    ordered_commands: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    trace: Optional[Trace] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalBlock":
        schema.check_keys(
            data, {"win_flag", "next", "host", "logs", "ordered_commands", "hints", "trace"}, "terminal block"
        )
        trace = None
        if "trace" in data:
            schema.check_keys(
                data["trace"], {"limit", "on_trace"}, "terminal block trace", {"limit", "on_trace"}
            )
            limit = data["trace"]["limit"]
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise StoryLoadError("terminal block trace: limit must be a whole number of at least 1")
            trace = Trace(limit=limit, on_trace=str(data["trace"]["on_trace"]))
        for hint in schema.check_list(data.get("hints", []), "terminal block hints"):
            if not isinstance(hint, str) or not hint.strip():
                raise StoryLoadError("terminal block hints: every hint must be a non-empty string")
        schema.check_logs(data.get("logs"), "terminal block")
        schema.check_list(data.get("ordered_commands", []), "terminal block ordered_commands")
        try:
            return cls(
                win_flag=data["win_flag"],
                next=data["next"],
                host=data.get("host"),
                logs=list(data.get("logs", [])),
                ordered_commands=[" ".join(str(c).split()) for c in data.get("ordered_commands", [])],
                hints=[" ".join(h.split()) for h in data.get("hints", [])],
                trace=trace,
            )
        except KeyError as exc:
            raise StoryLoadError(f"terminal block missing required key {exc}") from exc


@dataclass
class Scene:
    id: str
    text: str = ""
    type: str = "narrative"
    choices: list[Choice] = field(default_factory=list)
    terminal: Optional[TerminalBlock] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        schema.check_keys(data, {"id", "text", "type", "choices", "terminal"}, "scene")
        try:
            scene_id = data["id"]
        except KeyError as exc:
            raise StoryLoadError(f"scene missing required key {exc}") from exc
        where = f"scene '{scene_id}'"
        scene_type = data.get("type", "narrative")
        schema.check_value(scene_type, SCENE_TYPES, "scene type", where)
        choices = []
        for i, choice_data in enumerate(
            schema.check_list(data.get("choices", []), f"{where} choices"), start=1
        ):
            try:
                choices.append(Choice.from_dict(choice_data))
            except StoryLoadError as exc:
                raise StoryLoadError(f"{where}, choice {i}: {exc}") from exc
        terminal = None
        if "terminal" in data:
            try:
                terminal = TerminalBlock.from_dict(data["terminal"])
            except StoryLoadError as exc:
                raise StoryLoadError(f"{where}: {exc}") from exc
        if scene_type == "terminal" and terminal is None:
            raise StoryLoadError(f"{where}: a terminal scene needs a 'terminal' block")
        if scene_type != "terminal" and terminal is not None:
            raise StoryLoadError(f"{where}: only terminal scenes may have a 'terminal' block")
        if scene_type == "ending" and choices:
            raise StoryLoadError(f"{where}: an ending can't have choices")
        if scene_type == "narrative" and not choices:
            raise StoryLoadError(f"{where}: a narrative scene needs at least one choice (or type: ending)")
        return cls(
            id=scene_id, text=data.get("text", ""), type=scene_type, choices=choices, terminal=terminal
        )


@dataclass
class Chapter:
    id: str
    scenes: dict[str, Scene]

    @classmethod
    def from_file(cls, path: Path) -> "Chapter":
        data = yaml.safe_load(path.read_text()) or {}
        schema.check_keys(data, {"id", "scenes"}, path.name)
        chapter_id = data.get("id") or path.stem
        scenes: dict[str, Scene] = {}
        for scene_data in schema.check_list(data.get("scenes", []), f"{path.name} scenes"):
            try:
                scene = Scene.from_dict(scene_data)
            except StoryLoadError as exc:
                raise StoryLoadError(f"In chapter '{chapter_id}' ({path.name}): {exc}") from exc
            if scene.id in scenes:
                raise StoryLoadError(f"Duplicate scene id '{scene.id}' in chapter '{chapter_id}'")
            scenes[scene.id] = scene
        return cls(id=chapter_id, scenes=scenes)


@dataclass
class Story:
    id: str
    title: str
    start: str
    chapters: dict[str, Chapter]
    published: Optional[date] = None  # manifest `published: 2026-09-08`, shown in story lists
    description: str = ""  # manifest `description`: one line for story lists

    def resolve(self, ref: str, current_chapter: str) -> tuple[str, str]:
        """Resolve a `next` reference ("scene_id" or "chapter_id:scene_id")."""
        if ":" in ref:
            chapter_id, scene_id = ref.split(":", 1)
            return chapter_id, scene_id
        return current_chapter, ref

    def get_scene(self, chapter_id: str, scene_id: str) -> Scene:
        chapter = self.chapters.get(chapter_id)
        if chapter is None:
            raise StoryLoadError(f"Unknown chapter '{chapter_id}'")
        scene = chapter.scenes.get(scene_id)
        if scene is None:
            raise StoryLoadError(f"Unknown scene '{scene_id}' in chapter '{chapter_id}'")
        return scene

    def start_ref(self) -> tuple[str, str]:
        chapter_id, scene_id = self.start.split(":", 1)
        return chapter_id, scene_id

    @classmethod
    def load(cls, story_dir: Path) -> "Story":
        try:
            manifest = yaml.safe_load((story_dir / "manifest.yaml").read_text()) or {}
        except (yaml.YAMLError, ValueError) as exc:  # e.g. `published: 2026-13-40` fails inside YAML
            raise StoryLoadError(f"manifest.yaml: {exc}") from exc
        schema.check_keys(
            manifest, {"id", "title", "start", "chapters", "published", "description"}, "manifest.yaml"
        )
        try:
            manifest_id = manifest["id"]
            manifest_start = manifest["start"]
            chapter_filenames = manifest["chapters"]
        except KeyError as exc:
            raise StoryLoadError(f"manifest.yaml missing required key {exc}") from exc
        chapters: dict[str, Chapter] = {}
        for chapter_filename in chapter_filenames:
            chapter = Chapter.from_file(story_dir / "chapters" / chapter_filename)
            if chapter.id in chapters:
                raise StoryLoadError(f"Duplicate chapter id '{chapter.id}'")
            chapters[chapter.id] = chapter
        story = cls(
            id=manifest_id,
            title=manifest.get("title", manifest_id),
            start=manifest_start,
            chapters=chapters,
            published=_published_date(manifest.get("published")),
            description=" ".join(str(manifest.get("description", "")).split()),
        )
        story.validate()
        return story

    def validate(self) -> None:
        for chapter in self.chapters.values():
            for scene in chapter.scenes.values():
                targets = [c.next for c in scene.choices]
                if scene.terminal:
                    targets.append(scene.terminal.next)
                    if scene.terminal.trace:
                        targets.append(scene.terminal.trace.on_trace)
                for target in targets:
                    chapter_id, scene_id = self.resolve(target, chapter.id)
                    target_chapter = self.chapters.get(chapter_id)
                    if target_chapter is None or scene_id not in target_chapter.scenes:
                        raise StoryLoadError(
                            f"Dangling reference '{target}' from scene '{chapter.id}:{scene.id}'"
                        )
        if ":" not in self.start:
            raise StoryLoadError("manifest 'start' must be 'chapter_id:scene_id'")
        start_chapter, start_scene = self.start_ref()
        self.get_scene(start_chapter, start_scene)


def _published_date(value: Any) -> Optional[date]:
    """YAML reads an unquoted `2026-09-08` as a date and a quoted one as a
    string; accept both, but only a real YYYY-MM-DD date."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise StoryLoadError(
            f"manifest.yaml: published must be a date like 2026-09-08, not {value!r}"
        ) from None


def check_requires(requires: Optional[dict[str, Any]], state: GameState) -> bool:
    """Evaluate a `requires` block. Supported keys: flag, flag_equals
    ({key,value}), tool, journal_has (entry id), trust_at_least ({npc,value}),
    and the combinators all ([blocks]), any ([blocks]) and not (block), which
    nest. Every key present must hold (so a plain block is an implicit `all`)."""
    if not requires:
        return True
    if "flag" in requires and not state.has_flag(requires["flag"]):
        return False
    if "flag_equals" in requires:
        fe = requires["flag_equals"]
        if not state.has_flag(fe["key"], fe["value"]):
            return False
    if "tool" in requires and not state.has_tool(requires["tool"]):
        return False
    if "journal_has" in requires and not state.journal.has(requires["journal_has"]):
        return False
    if "trust_at_least" in requires:
        ta = requires["trust_at_least"]
        if state.get_trust(ta["npc"]) < ta["value"]:
            return False
    if "all" in requires and not all(check_requires(r, state) for r in requires["all"]):
        return False
    if "any" in requires and not any(check_requires(r, state) for r in requires["any"]):
        return False
    if "not" in requires and check_requires(requires["not"], state):
        return False
    return True


def apply_effects(
    sets: dict[str, Any], logs: list[dict[str, Any]], state: GameState, discovered_at: str
) -> None:
    """Apply a choice/terminal-block's `sets`/`logs` to the GameState.
    A `sets` key of the form "trust.<npc_id>" adjusts trust instead of a flag,
    and "tool.<tool_id>" grants that tool (or, with a false value, takes it
    away) -- what `requires: {tool: ...}` checks."""
    for key, value in (sets or {}).items():
        if key.startswith("trust."):
            state.adjust_trust(key.split(".", 1)[1], value)
        elif key.startswith("tool."):
            tool_id = key.split(".", 1)[1]
            if value:
                state.add_tool(tool_id)
            else:
                state.tools.discard(tool_id)
        else:
            state.set_flag(key, value)
    for entry in logs or []:
        state.journal.add(
            JournalEntry(
                id=entry["id"],
                category=entry.get("category", "note"),
                text=entry["text"],
                discovered_at=discovered_at,
                related_entry_ids=list(entry.get("related_entry_ids", [])),
            )
        )
