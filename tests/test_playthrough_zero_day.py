"""End-to-end smoke tests: script full playthroughs of the shipped "Zero Day"
story via the engine directly (no interactive I/O), proving the whole content
pipeline -- scenes across all three chapters, the fake terminal, config-restart
and both cipher puzzles, chat/email dialogue, and journal logging -- is
actually completable, and that every documented shell command has somewhere
in the story it's put to use.
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


def new_playthrough():
    story = Story.load(STORY_DIR)
    network = Network.load(STORY_DIR / "network.yaml")
    npcs = load_npcs(yaml.safe_load((STORY_DIR / "npcs.yaml").read_text()))
    chapter_id, scene_id = story.start_ref()
    state = GameState(story_id=story.id, chapter_id=chapter_id, scene_id=scene_id)
    runner = TerminalRunner(state=state, network=network, npcs=npcs)
    return story, state, runner


def play_to_confrontation(story, state, runner):
    """Drives the shared spine of the story -- chapter 1 through the
    trust_call choice -- exercising every documented shell command along the
    way. Returns after the caller still needs to make the trust_call choice
    and the final confrontation choice."""
    assert story.get_scene(state.chapter_id, state.scene_id).id == "intro"
    choose(story, state, 0)  # ask GHOST -> briefing
    assert state.scene_id == "briefing"
    choose(story, state, 0)  # accept -> recon (terminal)

    run_terminal(story, state, runner, ["help", "whoami", "scan gateway", "connect gateway"])
    assert state.scene_id == "gateway_shell"
    assert state.has_flag("connected_gateway")

    run_terminal(
        story,
        state,
        runner,
        [
            "ls",
            "cd etc",
            "cd netmon",
            "cat netmon.conf",
            "cat README",
            "grep bind /etc/netmon/netmon.conf",
            "set /etc/netmon/netmon.conf bind_address 0.0.0.0",
            "set /etc/netmon/netmon.conf allow_query allow",
            "systemctl status netmon",
            "systemctl restart netmon",
        ],
    )
    assert state.scene_id == "discovery"
    assert state.has_flag("netmon_fixed")
    assert state.journal.has("lead_t_contact")

    choose(story, state, 0)  # push further -> chapter_02:reach_sentinel

    run_terminal(story, state, runner, ["disconnect", "scan sentinel", "connect sentinel"])
    assert state.scene_id == "sentinel_shell"
    assert state.has_flag("connected_sentinel")

    run_terminal(
        story,
        state,
        runner,
        ["grep handoff /var/log/ops/access.log", "decrypt /var/log/ops/handoff.enc 5"],
    )
    assert state.scene_id == "contact_t"
    assert state.has_flag("found_override_code")

    run_terminal(story, state, runner, ["mail send t cold_storage"])
    assert state.scene_id == "waiting"
    assert state.has_flag("emailed_t")

    choose(story, state, 0)  # check back later -> blackbox
    assert state.scene_id == "blackbox"

    run_terminal(
        story,
        state,
        runner,
        ["mail list", "mail read t:cold_storage:1", "decrypt /var/log/ops/blackbox.enc RAVEN"],
    )
    assert state.scene_id == "trust_call"
    assert state.has_flag("found_true_operator")
    assert state.journal.has("suspect_oracle")


def test_loyalist_path_unlocks_bonus_ending():
    story, state, runner = new_playthrough()
    play_to_confrontation(story, state, runner)

    choose(story, state, 0)  # loyalist: tell GHOST everything
    assert state.scene_id == "confrontation"
    assert state.flags["allegiance"] == "loyalist"
    assert state.get_trust("ghost") == 3

    scene = story.get_scene(state.chapter_id, state.scene_id)
    available = [c for c in scene.choices if check_requires(c.requires, state)]
    assert len(available) == 4, "loyalist path with enough trust should unlock the 4th option"

    choose(story, state, 3)  # the trust+allegiance-gated option
    final_scene = story.get_scene(state.chapter_id, state.scene_id)
    assert final_scene.id == "ending_partners"
    assert final_scene.type == "ending"

    # All four journal categories got exercised somewhere along the way.
    assert {e.category for e in state.journal.all()} == {"trace", "lead", "suspect", "note"}


def test_wary_path_hides_bonus_ending_and_gated_topic():
    story, state, runner = new_playthrough()
    play_to_confrontation(story, state, runner)

    choose(story, state, 1)  # wary: keep the ORACLE lead to yourself
    assert state.flags["allegiance"] == "wary"
    assert state.get_trust("ghost") == 1

    scene = story.get_scene(state.chapter_id, state.scene_id)
    available = [c for c in scene.choices if check_requires(c.requires, state)]
    assert len(available) == 3, "insufficient trust/allegiance should hide the bonus option"

    # The high-trust GHOST topic is also gated behind the same trust level.
    runner.current_chapter, runner.current_scene = state.chapter_id, scene.id
    listing = runner.execute("chat ghost")
    assert "why_gateway" not in listing

    choose(story, state, 2)  # log everything and disappear -> ending_reported
    final_scene = story.get_scene(state.chapter_id, state.scene_id)
    assert final_scene.type == "ending"
    assert final_scene.id == "ending_reported"


def test_ghost_chat_topics_gated_by_flag_and_trust():
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
    assert "why_you" in listing
    assert "sentinel" not in listing  # gated behind netmon_fixed
    assert "why_gateway" not in listing  # gated behind trust_at_least(ghost, 3)

    reply = runner.execute("chat ghost netmon")
    assert "bind_address" in reply

    state.set_flag("netmon_fixed", True)
    assert "sentinel" in runner.execute("chat ghost")

    state.adjust_trust("ghost", 3)
    assert "why_gateway" in runner.execute("chat ghost")


def test_mail_ask_limit_enforced_for_t():
    story, state, runner = new_playthrough()
    play_to_confrontation(story, state, runner)
    # Two asks (cold_storage during the playthrough, then small_talk) reach
    # T's ask_limit of 2; a third should be refused rather than silently
    # accepted.
    reply = runner.execute("mail send t small_talk")
    assert "sent" in reply.lower()
    refusal = runner.execute("mail send t small_talk")
    assert "isn't responding" in refusal
