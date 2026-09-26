"""Structural validator for story content.

Explicit-state BFS over the *real* engine (`Story`, `GameState`,
`check_requires`, `apply_effects` -- nothing about their semantics is
reimplemented here, so there's no risk of this tool drifting from what the
game actually does) to find:

  - scenes never reachable under any combination of choices
  - endings never reachable under any combination of choices
  - terminal scenes whose `win_flag` is never structurally set by anything
    in `network.yaml`/`npcs.yaml` -- the easiest bug to introduce (a typo
    between two files) and the hardest to spot by reading either file alone

Each choice/topic produces its own real, cloned `GameState` (via the same
`to_dict`/`from_dict` round-trip save/load already uses), so mutually
exclusive branches are explored separately rather than merged into one
over-approximated state -- this is a sound search, not a heuristic guess.
States are deduped by a canonical key so hub-scene loops terminate.

Known limitation: doesn't model an NPC's `ask_limit` running out across
multiple asks: a dialogue-driven win_flag is treated as reachable once
per fresh state as long as the topic's own `requires` are satisfiable,
which is the common case but wouldn't catch an ask_limit set so low it
makes a *specific* topic unreachable in practice.

Also lints cross-file references without searching (see `lint_story`):
flags that are required but never set, journal ids that are required but
never logged, `tool` requirements (which nothing can grant), and -- as
warnings only -- flags that are set but never read. `--graph` prints the
scene graph as a Mermaid flowchart instead of checking.

Usage:
    python -m terminalgames.tools.check_story <story_dir_name_or_id>
    python -m terminalgames.tools.check_story --all
    python -m terminalgames.tools.check_story <story> --graph
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..engine.loader import discover_stories, find_story, load_network, load_npc_roster
from ..engine.shell import Network
from ..engine.state import GameState
from ..engine.story import Story, StoryLoadError, apply_effects, check_requires


@dataclass
class SettableBy:
    kind: str  # "connect" | "fix" | "decrypt" | "dialogue" | "ordered"
    description: str
    gate: Optional[dict[str, Any]] = None  # a `requires`-shaped dict, checked before assuming set
    topic: Any = None  # the Topic, for "dialogue" -- lets us apply its full sets/logs, not just the flag


def collect_settable_flags(network: Network, npcs: dict) -> dict[str, SettableBy]:
    """flag_name -> the one mechanism in the story data that can set it.
    Anything not in here can never become true, in any playthrough."""
    settable: dict[str, SettableBy] = {}
    for host in network.hosts.values():
        if host.on_connect_flag:
            how = f"ssh {'|'.join(host.logins)}@{host.id}" if host.logins else f"connect {host.id}"
            settable[host.on_connect_flag] = SettableBy("connect", how, gate=host.requires_to_connect)
        for svc in host.services.values():
            if svc.on_fix_flag:
                settable[svc.on_fix_flag] = SettableBy("fix", f"fix+restart {svc.id}@{host.id}")
        for path, meta in host.ciphers.items():
            if meta.on_success_flag:
                settable[meta.on_success_flag] = SettableBy("decrypt", f"decrypt {path}@{host.id}")
    for npc in npcs.values():
        for topic in npc.topics.values():
            for key in topic.sets:
                if not key.startswith("trust."):
                    settable[key] = SettableBy(
                        "dialogue", f"{npc.id}:{topic.id} (chat/mail)", gate=topic.requires, topic=topic
                    )
    return settable


# --- static lint: flags, journal ids and tools across every file -----------------


@dataclass
class _Usage:
    """name -> where it was first seen, for each kind of reference."""

    flags_set: dict[str, str] = field(default_factory=dict)
    flags_read: dict[str, str] = field(default_factory=dict)
    journal_logged: dict[str, str] = field(default_factory=dict)
    journal_read: dict[str, str] = field(default_factory=dict)
    tools_read: dict[str, str] = field(default_factory=dict)

    def add_requires(self, requires: Optional[dict[str, Any]], where: str) -> None:
        if not requires:
            return
        if "flag" in requires:
            self.flags_read.setdefault(requires["flag"], where)
        if "flag_equals" in requires:
            self.flags_read.setdefault(requires["flag_equals"]["key"], where)
        if "journal_has" in requires:
            self.journal_read.setdefault(requires["journal_has"], where)
        if "tool" in requires:
            self.tools_read.setdefault(requires["tool"], where)
        for sub in [*requires.get("all", []), *requires.get("any", [])]:
            self.add_requires(sub, where)
        if "not" in requires:
            self.add_requires(requires["not"], where)

    def add_effects(self, sets: dict[str, Any], logs: list[dict[str, Any]], where: str) -> None:
        for key in sets or {}:
            if not key.startswith("trust."):
                self.flags_set.setdefault(key, where)
        for entry in logs or []:
            self.journal_logged.setdefault(entry["id"], where)


def lint_story(story: Story, network: Network, npcs: dict) -> tuple[list[str], list[str]]:
    """Cross-file reference checks that don't need the reachability search.
    Returns (problems, warnings): a flag some `requires` reads but nothing
    ever sets, a `journal_has` id nothing ever logs, and any `tool`
    requirement (no story content can grant tools) are problems -- that gate
    can never open. A flag that is set but never read is only a warning:
    harmless, but usually a leftover or a typo. (A win_flag nothing sets is
    reported by the search itself.)"""
    usage = _Usage()
    win_flags: set[str] = set()
    for chapter in story.chapters.values():
        for scene in chapter.scenes.values():
            here = f"{chapter.id}:{scene.id}"
            for choice in scene.choices:
                usage.add_requires(choice.requires, here)
                usage.add_effects(choice.sets, choice.logs, here)
            if scene.terminal:
                win_flags.add(scene.terminal.win_flag)
                usage.flags_read.setdefault(scene.terminal.win_flag, here)
                usage.add_effects({}, scene.terminal.logs, here)
                if scene.terminal.ordered_commands:
                    usage.flags_set.setdefault(scene.terminal.win_flag, here)
    for host in network.hosts.values():
        usage.add_requires(host.requires_to_connect, f"host {host.id}")
        if host.on_connect_flag:
            usage.flags_set.setdefault(host.on_connect_flag, f"host {host.id}")
        for svc in host.services.values():
            if svc.on_fix_flag:
                usage.flags_set.setdefault(svc.on_fix_flag, f"service {svc.id}@{host.id}")
        for path, meta in host.ciphers.items():
            if meta.on_success_flag:
                usage.flags_set.setdefault(meta.on_success_flag, f"{path}@{host.id}")
    for npc in npcs.values():
        for topic in npc.topics.values():
            where = f"topic {npc.id}:{topic.id}"
            usage.add_requires(topic.requires, where)
            usage.add_effects(topic.sets, topic.logs, where)

    problems = [
        f"flag '{flag}' is required at {where}, but nothing ever sets it"
        for flag, where in sorted(usage.flags_read.items())
        if flag not in usage.flags_set and flag not in win_flags
    ]
    problems += [
        f"journal entry '{entry}' is required at {where}, but nothing ever logs it"
        for entry, where in sorted(usage.journal_read.items())
        if entry not in usage.journal_logged
    ]
    problems += [
        f"tool '{tool}' is required at {where}, but no story content can grant tools"
        for tool, where in sorted(usage.tools_read.items())
    ]
    warnings = [
        f"flag '{flag}' is set at {where}, but nothing ever reads it"
        for flag, where in sorted(usage.flags_set.items())
        if flag not in usage.flags_read
    ]
    return problems, warnings


def _state_key(state: GameState) -> tuple:
    return (
        state.chapter_id,
        state.scene_id,
        tuple(sorted(state.flags.items())),
        tuple(sorted(state.trust.items())),
        tuple(sorted(state.tools)),
        tuple(sorted(e.id for e in state.journal.all())),
    )


def _clone(state: GameState) -> GameState:
    return GameState.from_dict(state.to_dict())


@dataclass
class Report:
    all_scenes: set[tuple[str, str]]
    visited_scenes: set[tuple[str, str]]
    all_endings: set[str]
    visited_endings: set[str]
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # reported, but don't affect `ok`

    @property
    def unreached_scenes(self) -> set[tuple[str, str]]:
        return self.all_scenes - self.visited_scenes

    @property
    def unreached_endings(self) -> set[str]:
        return self.all_endings - self.visited_endings

    @property
    def ok(self) -> bool:
        return not self.problems and not self.unreached_scenes and not self.unreached_endings


def check_story(story: Story, network: Network, npcs: dict, *, max_states: int = 20000) -> Report:
    settable = collect_settable_flags(network, npcs)
    visited_scenes: set[tuple[str, str]] = set()
    visited_endings: set[str] = set()
    problems, warnings = lint_story(story, network, npcs)
    seen_keys: set[tuple] = set()

    start_chapter, start_scene = story.start_ref()
    frontier = deque([GameState(story_id=story.id, chapter_id=start_chapter, scene_id=start_scene)])

    while frontier:
        if len(seen_keys) > max_states:
            problems.append(f"search cap ({max_states} states) reached -- results may be incomplete")
            break
        state = frontier.popleft()
        key = _state_key(state)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        visited_scenes.add((state.chapter_id, state.scene_id))

        scene = story.get_scene(state.chapter_id, state.scene_id)

        if scene.type == "ending":
            visited_endings.add(scene.id)
            continue

        if scene.type == "terminal":
            assert scene.terminal is not None
            win_flag = scene.terminal.win_flag
            info = settable.get(win_flag)
            if info is None and scene.terminal.ordered_commands:
                info = SettableBy("ordered", f"ordered_commands in {state.chapter_id}:{scene.id}")
            if info is None:
                problems.append(
                    f"scene '{state.chapter_id}:{scene.id}': win_flag '{win_flag}' is never "
                    f"set by anything in network.yaml/npcs.yaml"
                )
                continue
            if info.gate is not None and not check_requires(info.gate, state):
                continue  # this path can't satisfy the gate (yet) -- don't propagate, don't error
            nxt = _clone(state)
            discovered_at = f"{state.chapter_id}:{scene.id}"
            if info.kind == "dialogue" and info.topic is not None:
                apply_effects(info.topic.sets, info.topic.logs, nxt, discovered_at)
            else:
                nxt.set_flag(win_flag, True)
            apply_effects({}, scene.terminal.logs, nxt, discovered_at)
            nxt.chapter_id, nxt.scene_id = story.resolve(scene.terminal.next, state.chapter_id)
            nxt.advance_scene()
            frontier.append(nxt)
            continue

        for choice in scene.choices:
            if not check_requires(choice.requires, state):
                continue
            nxt = _clone(state)
            apply_effects(choice.sets, choice.logs, nxt, f"{state.chapter_id}:{scene.id}")
            nxt.chapter_id, nxt.scene_id = story.resolve(choice.next, state.chapter_id)
            nxt.advance_scene()
            frontier.append(nxt)

    all_scenes = {(cid, sid) for cid, ch in story.chapters.items() for sid in ch.scenes}
    all_endings = {s.id for ch in story.chapters.values() for s in ch.scenes.values() if s.type == "ending"}
    return Report(all_scenes, visited_scenes, all_endings, visited_endings, problems, warnings)


def print_report(story: Story, report: Report) -> None:
    print(
        f"{story.title}: {len(report.visited_scenes)}/{len(report.all_scenes)} scenes reachable, "
        f"{len(report.visited_endings)}/{len(report.all_endings)} endings reachable."
    )
    if report.unreached_scenes:
        print("Unreached scenes:")
        for chapter_id, scene_id in sorted(report.unreached_scenes):
            print(f"  {chapter_id}:{scene_id}")
    if report.unreached_endings:
        print("Unreached endings:")
        for ending_id in sorted(report.unreached_endings):
            print(f"  {ending_id}")
    if report.problems:
        print("Problems:")
        for problem in report.problems:
            print(f"  {problem}")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  {warning}")
    if report.ok:
        print("OK.")


# --- scene graph -----------------------------------------------------------------


def _mermaid_id(*parts: str) -> str:
    # Prefixed, since bare `end` is a Mermaid keyword.
    return "n_" + "__".join(re.sub(r"\W", "_", part) for part in parts)


def _mermaid_label(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace('"', "#quot;")


def mermaid_graph(story: Story) -> str:
    """The story's scene graph as a Mermaid flowchart (GitHub renders
    ```mermaid blocks): narrative scenes are boxes, terminal scenes
    double-bordered boxes, endings rounded; choice edges carry the choice
    text (dashed when gated by `requires`), and a terminal scene's exit is a
    thick edge labelled with its win_flag. One subgraph per chapter."""
    lines = ["flowchart TD"]
    for chapter in story.chapters.values():
        lines.append(f'  subgraph {_mermaid_id(chapter.id)}["{_mermaid_label(chapter.id)}"]')
        for scene in chapter.scenes.values():
            node, name = _mermaid_id(chapter.id, scene.id), _mermaid_label(scene.id)
            shape = {"ending": '(["{}"])', "terminal": '[["{}"]]'}.get(scene.type, '["{}"]')
            lines.append(f"    {node}{shape.format(name)}")
        lines.append("  end")
    lines.append(f'  start((" ")) --> {_mermaid_id(*story.start_ref())}')
    for chapter in story.chapters.values():
        for scene in chapter.scenes.values():
            node = _mermaid_id(chapter.id, scene.id)
            for choice in scene.choices:
                target = _mermaid_id(*story.resolve(choice.next, chapter.id))
                arrow = "-.->" if choice.requires else "-->"
                lines.append(f'  {node} {arrow}|"{_mermaid_label(choice.text)}"| {target}')
            if scene.terminal:
                target = _mermaid_id(*story.resolve(scene.terminal.next, chapter.id))
                lines.append(f'  {node} ==>|"{_mermaid_label(scene.terminal.win_flag)}"| {target}')
    return "\n".join(lines)


def check_story_dir(story_dir: Path) -> Report:
    story = Story.load(story_dir)
    network = load_network(story_dir)
    npcs = load_npc_roster(story_dir)
    return check_story(story, network, npcs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m terminalgames.tools.check_story",
        description="Check a shipped story for unreachable scenes/endings and win_flags "
        "that are never structurally set.",
    )
    parser.add_argument("story", nargs="?", help="story directory name or manifest id")
    parser.add_argument("--all", action="store_true", help="check every shipped story")
    parser.add_argument(
        "--graph",
        action="store_true",
        help="instead of checking, print the story's scene graph as a Mermaid flowchart",
    )
    args = parser.parse_args(argv)

    if not args.all and not args.story:
        parser.error("pass a story, or --all")
    if args.graph and args.all:
        parser.error("--graph takes a single story, not --all")

    stories = discover_stories()
    if args.all:
        targets = stories
    else:
        story_dir = find_story(args.story, stories)
        if story_dir is None:
            print(f"no story matching '{args.story}'", file=sys.stderr)
            sys.exit(1)
        targets = [story_dir]

    all_ok = True
    for story_dir in targets:
        try:
            story = Story.load(story_dir)
        except StoryLoadError as exc:
            print(f"{story_dir.name}: failed to load: {exc}", file=sys.stderr)
            all_ok = False
            continue
        if args.graph:
            print(mermaid_graph(story))
            return
        network = load_network(story_dir)
        npcs = load_npc_roster(story_dir)
        report = check_story(story, network, npcs)
        print_report(story, report)
        all_ok = all_ok and report.ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
