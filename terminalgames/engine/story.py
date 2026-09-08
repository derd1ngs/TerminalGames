"""Story/Chapter/Scene graph: loading from YAML, validation, and condition/effect evaluation.

A Story is a manifest plus an ordered list of Chapter files, each a scene graph.
This shape is identical whether a story is one short chapter or dozens of
chapters spanning a long investigation -- scenes can reference scenes in other
chapters via "chapter_id:scene_id", so a lead found in chapter 3 can pay off
in chapter 7 without any special-casing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .journal import JournalEntry
from .state import GameState


class StoryLoadError(Exception):
    pass


@dataclass
class Choice:
    text: str
    next: str
    requires: Optional[dict[str, Any]] = None
    sets: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Choice":
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
class TerminalBlock:
    """A terminal-type scene. `win_flag` is the flag the main loop watches for
    to know the puzzle is solved -- it's set directly by whichever shell
    command satisfies the puzzle (e.g. a service's `on_fix_flag` in
    network.yaml, set by `systemctl restart` once its config validates)."""

    win_flag: str
    next: str
    host: Optional[str] = None
    logs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalBlock":
        try:
            return cls(
                win_flag=data["win_flag"],
                next=data["next"],
                host=data.get("host"),
                logs=list(data.get("logs", [])),
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
        try:
            scene_id = data["id"]
        except KeyError as exc:
            raise StoryLoadError(f"scene missing required key {exc}") from exc
        return cls(
            id=scene_id,
            text=data.get("text", ""),
            type=data.get("type", "narrative"),
            choices=[Choice.from_dict(c) for c in data.get("choices", [])],
            terminal=TerminalBlock.from_dict(data["terminal"]) if "terminal" in data else None,
        )


@dataclass
class Chapter:
    id: str
    scenes: dict[str, Scene]

    @classmethod
    def from_file(cls, path: Path) -> "Chapter":
        data = yaml.safe_load(path.read_text()) or {}
        chapter_id = data.get("id") or path.stem
        scenes: dict[str, Scene] = {}
        for scene_data in data.get("scenes", []):
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
        manifest = yaml.safe_load((story_dir / "manifest.yaml").read_text()) or {}
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
        )
        story.validate()
        return story

    def validate(self) -> None:
        for chapter in self.chapters.values():
            for scene in chapter.scenes.values():
                targets = [c.next for c in scene.choices]
                if scene.terminal:
                    targets.append(scene.terminal.next)
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


def check_requires(requires: Optional[dict[str, Any]], state: GameState) -> bool:
    """Evaluate a `requires` block. Supported keys: flag, flag_equals
    ({key,value}), tool, journal_has (entry id), trust_at_least ({npc,value})."""
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
    return True


def apply_effects(
    sets: dict[str, Any], logs: list[dict[str, Any]], state: GameState, discovered_at: str
) -> None:
    """Apply a choice/terminal-block's `sets`/`logs` to the GameState.
    A `sets` key of the form "trust.<npc_id>" adjusts trust instead of a flag."""
    for key, value in (sets or {}).items():
        if key.startswith("trust."):
            state.adjust_trust(key.split(".", 1)[1], value)
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
