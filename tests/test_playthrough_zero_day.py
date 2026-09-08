"""End-to-end smoke test: scripts a full playthrough of the shipped "Zero Day"
story via the engine directly (no interactive I/O), proving the whole content
pipeline -- scenes, the fake terminal, config-restart and cipher puzzles, and
journal logging -- is actually completable.
"""
from pathlib import Path

import yaml

from terminalgames.engine.dialogue import load_npcs
from terminalgames.engine.shell import Network, TerminalRunner
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story, apply_effects, check_requires

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def choose(story, state, index):
    scene = story.get_scene(state.chapter_id, state.scene_id)
    available = [c for c in scene.choices if check_requires(c.requires, state)]
    choice = available[index]
    apply_effects(choice.sets, choice.logs, state, f"{state.chapter_id}:{scene.id}")
    state.chapter_id, state.scene_id = story.resolve(choice.next, state.chapter_id)
    state.advance_scene()


def run_terminal(story, state, runner, commands):
    scene = story.get_scene(state.chapter_id, state.scene_id)
    runner.current_chapter, runner.current_scene = state.chapter_id, scene.id
    if scene.terminal.host:
        runner.current_host = scene.terminal.host
        runner.cwd = "/"
    for cmd in commands:
        runner.execute(cmd)
        if state.has_flag(scene.terminal.win_flag):
            break
    assert state.has_flag(scene.terminal.win_flag), f"scene '{scene.id}' not solved by {commands}"
    apply_effects({}, scene.terminal.logs, state, f"{state.chapter_id}:{scene.id}")
    state.chapter_id, state.scene_id = story.resolve(scene.terminal.next, state.chapter_id)
    state.advance_scene()


def test_full_playthrough_reaches_reported_ending():
    story = Story.load(STORY_DIR)
    network = Network.load(STORY_DIR / "network.yaml")
    npcs = load_npcs(yaml.safe_load((STORY_DIR / "npcs.yaml").read_text()))

    chapter_id, scene_id = story.start_ref()
    state = GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)
    runner = TerminalRunner(state=state, network=network, npcs=npcs)

    assert story.get_scene(state.chapter_id, state.scene_id).id == "intro"
    choose(story, state, 0)  # ask GHOST -> briefing
    assert state.scene_id == "briefing"
    choose(story, state, 0)  # accept -> recon (terminal)

    run_terminal(story, state, runner, ["scan gateway", "connect gateway"])
    assert state.scene_id == "gateway_shell"
    assert state.has_flag("connected_gateway")

    run_terminal(
        story,
        state,
        runner,
        [
            "cat /etc/netmon/README",
            "set /etc/netmon/netmon.conf bind_address 0.0.0.0",
            "set /etc/netmon/netmon.conf allow_query allow",
            "systemctl restart netmon",
        ],
    )
    assert state.scene_id == "discovery"
    assert state.has_flag("netmon_fixed")

    choose(story, state, 0)  # push further -> sentinel_recon (terminal)

    run_terminal(
        story,
        state,
        runner,
        [
            "grep handoff var/log/ops/access.log",
            "decrypt var/log/ops/handoff.enc 5",
        ],
    )
    assert state.scene_id == "confrontation"
    assert state.has_flag("found_override_code")

    choose(story, state, 2)  # log everything and disappear -> ending_reported

    final_scene = story.get_scene(state.chapter_id, state.scene_id)
    assert final_scene.type == "ending"
    assert final_scene.id == "ending_reported"

    # Journal accumulated the traces logged along the way.
    trace_ids = {e.id for e in state.journal.by_category("trace")}
    assert {"trace_gateway_connect", "trace_netmon_fixed", "trace_sentinel_uplink"} <= trace_ids


def test_ghost_chat_available_during_gateway_shell():
    story = Story.load(STORY_DIR)
    network = Network.load(STORY_DIR / "network.yaml")
    npcs = load_npcs(yaml.safe_load((STORY_DIR / "npcs.yaml").read_text()))
    chapter_id, scene_id = story.start_ref()
    state = GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)
    runner = TerminalRunner(state=state, network=network, npcs=npcs)
    runner.current_chapter, runner.current_scene = "chapter_01", "gateway_shell"

    listing = runner.execute("chat ghost")
    assert "netmon" in listing
    assert "who_are_you" in listing
    assert "sentinel" not in listing  # gated behind netmon_fixed

    reply = runner.execute("chat ghost netmon")
    assert "bind_address" in reply

    state.set_flag("netmon_fixed", True)
    listing_after = runner.execute("chat ghost")
    assert "sentinel" in listing_after
