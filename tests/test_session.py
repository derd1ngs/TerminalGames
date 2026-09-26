"""GameSession: the UI-free game loop every frontend drives."""

import json
from pathlib import Path

import pytest

from terminalgames.engine.journal import JournalEntry
from terminalgames.engine.puzzles import parse_config_text
from terminalgames.engine.session import GameSession, StaleSaveError
from terminalgames.engine.shell import Host, Network
from terminalgames.engine.state import GameState
from terminalgames.engine.story import Chapter, Choice, Scene, Story, TerminalBlock

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def build_session(tmp_path: Path) -> GameSession:
    return GameSession.open(Story.load(STORY_DIR), STORY_DIR, tmp_path / "save.json", fresh=True)


def test_starts_on_the_story_start_scene(tmp_path):
    session = build_session(tmp_path)
    assert (session.state.chapter_id, session.scene.id) == session.story.start_ref()
    assert session.scene.type == "narrative"


def test_choose_advances_and_counts_the_scene(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])
    assert session.scene.id == "briefing"
    assert session.state.scenes_visited == 1


def test_run_command_without_solving_stays_on_the_scene(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])  # -> briefing
    session.choose(session.available_choices()[0])  # -> recon (terminal)
    result = session.run_command("whoami")
    assert result.output == "user@localhost"
    assert not result.advanced
    assert session.scene.id == "recon"


def test_solving_a_terminal_scene_advances_and_auto_connects(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])  # -> briefing
    session.choose(session.available_choices()[0])  # -> recon (terminal)
    result = session.run_command("connect gateway")
    assert result.advanced
    assert session.scene.id == "gateway_shell"
    assert session.runner.current_host == "gateway"
    assert session.runner.current_scene == "gateway_shell"


def test_crossing_a_chapter_autosaves_and_reports_it(tmp_path):
    session = build_session(tmp_path)
    landing = next(iter(session.story.chapters["chapter_02"].scenes))
    notices = session.choose(Choice.from_dict({"text": "Jump ahead", "next": f"chapter_02:{landing}"}))
    assert session.state.chapter_id == "chapter_02"
    assert "Autosaved -- entering chapter 'chapter_02'." in notices
    assert GameState.load(session.slot_path).chapter_id == "chapter_02"


def test_open_continue_loads_the_saved_state(tmp_path):
    session = build_session(tmp_path)
    session.choose(session.available_choices()[0])  # -> briefing
    session.save()

    story = Story.load(STORY_DIR)
    continued = GameSession.open(story, STORY_DIR, session.slot_path, fresh=False)
    assert continued.scene.id == "briefing"
    assert continued.state.scenes_visited == 1


def test_open_continue_reuses_the_sandbox_but_fresh_wipes_it(tmp_path):
    story = Story.load(STORY_DIR)
    slot_path = tmp_path / "run1.json"
    session = GameSession.open(story, STORY_DIR, slot_path, fresh=True)
    session.save()
    netmon_conf = (
        GameState.sandbox_dir_for(slot_path) / "hosts" / "gateway" / "etc" / "netmon" / "netmon.conf"
    )
    netmon_conf.write_text("bind_address=0.0.0.0\nallow_query=allow")  # simulates a `set`

    # Continuing must not touch the player's edit.
    GameSession.open(story, STORY_DIR, slot_path, fresh=False)
    assert parse_config_text(netmon_conf.read_text()) == {"bind_address": "0.0.0.0", "allow_query": "allow"}

    # Starting the slot fresh must wipe it back to the story's original values.
    GameSession.open(story, STORY_DIR, slot_path, fresh=True)
    assert parse_config_text(netmon_conf.read_text()) == {
        "bind_address": "127.0.0.1",
        "allow_query": "denied",
    }


def _procedure_session(tmp_path: Path) -> GameSession:
    story = Story(
        id="s",
        title="Procedure",
        start="c1:console",
        chapters={
            "c1": Chapter(
                id="c1",
                scenes={
                    "console": Scene(
                        id="console",
                        type="terminal",
                        terminal=TerminalBlock(
                            win_flag="recovered",
                            next="c1:done",
                            host="box",
                            ordered_commands=["systemctl status db", "whoami", "status"],
                        ),
                    ),
                    "done": Scene(id="done", type="ending"),
                },
            )
        },
    )
    network = Network(hosts={"box": Host(id="box")})
    state = GameState(story_id="s", chapter_id="c1", scene_id="console")
    return GameSession(story, network, {}, state, tmp_path / "save.json")


def test_ordered_commands_solve_the_scene_only_in_sequence(tmp_path):
    session = _procedure_session(tmp_path)
    assert not session.run_command("whoami").advanced
    assert not session.run_command("status").advanced  # right tail, missing the first step
    session.run_command("systemctl   status db")  # whitespace is normalized
    session.run_command("whoami")
    result = session.run_command("status")
    assert result.advanced
    assert session.scene.id == "done"


def test_a_wrong_step_breaks_the_sequence(tmp_path):
    session = _procedure_session(tmp_path)
    for raw in ["systemctl status db", "ls", "whoami"]:
        session.run_command(raw)
    assert not session.run_command("status").advanced


def test_continuing_a_save_on_a_removed_scene_raises_stale_save_error(tmp_path):
    session = build_session(tmp_path)
    session.save()
    data = json.loads(session.slot_path.read_text())
    data["scene_id"] = "cut_in_a_rewrite"
    session.slot_path.write_text(json.dumps(data))

    with pytest.raises(StaleSaveError, match="'chapter_01:cut_in_a_rewrite', which the story no longer has"):
        GameSession.open(Story.load(STORY_DIR), STORY_DIR, session.slot_path, fresh=False)


def test_compose_mail_writes_a_real_draft_and_runs_mail_sync(tmp_path):
    session = build_session(tmp_path)
    session.state.journal.add(JournalEntry(id="lead_t_contact", category="lead", text="T exists."))
    session.state.chapter_id, session.state.scene_id = "chapter_02", "contact_t"
    session.enter_scene()
    assert [npc.id for npc in session.email_contacts()] == ["t"]

    bounced = session.compose_mail("t", "hello", "hi")
    assert bounced.output.startswith("Bounced: draft_1.txt")
    sent = session.compose_mail("t", "About the cold storage backup", "hi")
    assert sent.output == "Sent: draft_2.txt -> T"
    assert sent.advanced and session.scene.id == "waiting"
    assert (session.runner.sandbox_root / "mail" / "sent" / "draft_2.txt").exists()
