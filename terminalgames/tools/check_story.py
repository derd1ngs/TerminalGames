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

Usage:
    python -m terminalgames.tools.check_story <story_dir_name_or_id>
    python -m terminalgames.tools.check_story --all
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..engine.shell import Network
from ..engine.state import GameState
from ..engine.story import Story, StoryLoadError, apply_effects, check_requires
from ..main import discover_stories, find_story, load_network, load_npc_roster


@dataclass
class SettableBy:
    kind: str  # "connect" | "fix" | "decrypt" | "dialogue"
    description: str
    gate: Optional[dict[str, Any]] = None  # a `requires`-shaped dict, checked before assuming set
    topic: Any = None  # the Topic, for "dialogue" -- lets us apply its full sets/logs, not just the flag


def collect_settable_flags(network: Network, npcs: dict) -> dict[str, SettableBy]:
    """flag_name -> the one mechanism in the story data that can set it.
    Anything not in here can never become true, in any playthrough."""
    settable: dict[str, SettableBy] = {}
    for host in network.hosts.values():
        if host.on_connect_flag:
            settable[host.on_connect_flag] = SettableBy(
                "connect", f"connect {host.id}", gate=host.requires_to_connect
            )
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
    problems: list[str] = []
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
    return Report(all_scenes, visited_scenes, all_endings, visited_endings, problems)


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
    if report.ok:
        print("OK.")


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
    args = parser.parse_args(argv)

    if not args.all and not args.story:
        parser.error("pass a story, or --all")

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
        network = load_network(story_dir)
        npcs = load_npc_roster(story_dir)
        report = check_story(story, network, npcs)
        print_report(story, report)
        all_ok = all_ok and report.ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
