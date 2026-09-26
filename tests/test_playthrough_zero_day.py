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
from terminalgames.engine.session import GameSession
from terminalgames.engine.shell import Network, TerminalRunner
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def new_playthrough(tmp_path: Path) -> GameSession:
    return GameSession.open(Story.load(STORY_DIR), STORY_DIR, tmp_path / "playthrough.json", fresh=True)


def choose(session: GameSession, index: int) -> None:
    session.choose(session.available_choices()[index])


def run_terminal(session: GameSession, commands: list[str]) -> None:
    """Runs commands until one solves the current terminal scene."""
    scene = session.scene
    assert scene.type == "terminal", f"scene '{scene.id}' is not a terminal scene"
    for cmd in commands:
        if session.run_command(cmd).advanced:
            return
    raise AssertionError(f"scene '{scene.id}' not solved by {commands}")


def play_to_confrontation(session: GameSession) -> None:
    """Drives the shared spine of the story -- chapter 1 through the
    trust_call choice -- exercising every documented shell command along the
    way. Returns after the caller still needs to make the trust_call choice
    and the final confrontation choice."""
    state = session.state
    assert session.scene.id == "intro"
    choose(session, 0)  # ask GHOST -> briefing
    assert state.scene_id == "briefing"
    choose(session, 0)  # accept -> recon (terminal)

    run_terminal(session, ["help", "whoami", "scan gateway", "connect gateway"])
    assert state.scene_id == "gateway_shell"
    assert state.has_flag("connected_gateway")

    run_terminal(
        session,
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

    choose(session, 0)  # push further -> chapter_02:reach_sentinel

    run_terminal(session, ["disconnect", "scan sentinel", "connect sentinel"])
    assert state.scene_id == "sentinel_shell"
    assert state.has_flag("connected_sentinel")

    run_terminal(
        session,
        ["grep handoff /var/log/ops/access.log", "decrypt /var/log/ops/handoff.enc 5"],
    )
    assert state.scene_id == "contact_t"
    assert state.has_flag("found_override_code")

    run_terminal(session, ["mail send t cold_storage"])
    assert state.scene_id == "waiting"
    assert state.has_flag("emailed_t")

    choose(session, 0)  # check back later -> blackbox
    assert state.scene_id == "blackbox"

    run_terminal(
        session,
        ["mail list", "mail read t:cold_storage:1", "decrypt /var/log/ops/blackbox.enc RAVEN"],
    )
    assert state.scene_id == "trust_call"
    assert state.has_flag("found_true_operator")
    assert state.journal.has("suspect_oracle")


def test_mail_sync_reaches_t_via_a_real_drafted_message(tmp_path):
    """Same win_flag ('emailed_t') as the mail-send shortcut, but reached by
    actually writing a real draft file and running `mail sync` -- proving
    the freeform path works against T's real outbox_match content, not just
    a synthetic NPC fixture."""
    session = new_playthrough(tmp_path)
    state, runner = session.state, session.runner
    choose(session, 0)
    choose(session, 0)
    run_terminal(session, ["connect gateway"])
    run_terminal(
        session,
        [
            "set /etc/netmon/netmon.conf bind_address 0.0.0.0",
            "set /etc/netmon/netmon.conf allow_query allow",
            "systemctl restart netmon",
        ],
    )
    choose(session, 0)
    run_terminal(session, ["connect sentinel"])
    run_terminal(session, ["decrypt /var/log/ops/handoff.enc 5"])
    assert state.scene_id == "contact_t"

    draft_dir = runner.sandbox_root / "mail" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "to_t.txt").write_text(
        "To: t\nSubject: cold storage backup passphrase?\n\n"
        "Saw in the access log you re-keyed it. Any chance you remember it?"
    )

    run_terminal(session, ["mail sync"])
    assert state.scene_id == "waiting"
    assert state.has_flag("emailed_t")
    assert (runner.sandbox_root / "mail" / "sent" / "to_t.txt").exists()


def test_archive_sidequest_returns_to_the_discovery_hub(tmp_path):
    """The 'dig through that old archive box' choice is a genuine sidequest:
    it branches off the discovery hub into its own terminal puzzle, then
    loops back to that exact same hub scene, which still offers all of its
    original choices afterward -- the main quest is entirely unaffected."""
    session = new_playthrough(tmp_path)
    state = session.state
    choose(session, 0)  # intro -> briefing
    choose(session, 0)  # briefing -> recon
    run_terminal(session, ["connect gateway"])
    run_terminal(
        session,
        [
            "set /etc/netmon/netmon.conf bind_address 0.0.0.0",
            "set /etc/netmon/netmon.conf allow_query allow",
            "systemctl restart netmon",
        ],
    )
    assert state.scene_id == "discovery"
    hub_scene = session.scene
    assert len(hub_scene.choices) == 3

    choose(session, 1)  # "Dig through that old archive box first."
    assert state.scene_id == "archive_shell"

    run_terminal(
        session,
        [
            "disconnect",
            "connect archive",
            "grep shift /var/backups/log.txt",
            "decrypt /var/backups/incident_report.enc 7",
        ],
    )
    assert state.has_flag("found_archive_report")
    assert state.journal.has("suspect_sentinel_history")
    assert state.scene_id == "archive_return"

    choose(session, 0)  # "Back to it." -> loops back to the hub
    assert (state.chapter_id, state.scene_id) == ("chapter_01", "discovery")

    # The hub still works exactly as before -- the sidequest was a detour,
    # not a detour that broke anything.
    choose(session, 0)  # push further -> chapter_02:reach_sentinel
    assert state.chapter_id == "chapter_02"


def test_loyalist_path_unlocks_bonus_ending(tmp_path):
    session = new_playthrough(tmp_path)
    state = session.state
    play_to_confrontation(session)

    choose(session, 0)  # loyalist: tell GHOST everything
    assert state.scene_id == "confrontation"
    assert state.flags["allegiance"] == "loyalist"
    assert state.get_trust("ghost") == 3

    available = session.available_choices()
    assert len(available) == 4, "loyalist path with enough trust should unlock the 4th option"

    choose(session, 3)  # the trust+allegiance-gated option
    final_scene = session.scene
    assert final_scene.id == "ending_partners"
    assert final_scene.type == "ending"

    # All four journal categories got exercised somewhere along the way.
    assert {e.category for e in state.journal.all()} == {"trace", "lead", "suspect", "note"}


def test_wary_path_hides_bonus_ending_and_gated_topic(tmp_path):
    session = new_playthrough(tmp_path)
    state, runner = session.state, session.runner
    play_to_confrontation(session)

    choose(session, 1)  # wary: keep the ORACLE lead to yourself
    assert state.flags["allegiance"] == "wary"
    assert state.get_trust("ghost") == 1

    scene = session.scene
    available = session.available_choices()
    assert len(available) == 3, "insufficient trust/allegiance should hide the bonus option"

    # The high-trust GHOST topic is also gated behind the same trust level.
    runner.current_chapter, runner.current_scene = state.chapter_id, scene.id
    listing = runner.execute("chat ghost")
    assert "why_gateway" not in listing

    choose(session, 2)  # log everything and disappear -> ending_reported
    final_scene = session.scene
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


def test_mail_ask_limit_enforced_for_t(tmp_path):
    session = new_playthrough(tmp_path)
    runner = session.runner
    play_to_confrontation(session)
    # Two asks (cold_storage during the playthrough, then small_talk) reach
    # T's ask_limit of 2; a third should be refused rather than silently
    # accepted.
    reply = runner.execute("mail send t small_talk")
    assert "sent" in reply.lower()
    refusal = runner.execute("mail send t small_talk")
    assert "isn't responding" in refusal
