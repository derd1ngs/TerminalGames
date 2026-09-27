"""End-to-end playthroughs of "Night Shift", the story built on Phase 8:
`tail`, `find` and hidden files on forge, tool grants (the deploy key gates
mirror, Dex's sniffer gates an ending), and a trace meter on mirror with a
single retry."""

from pathlib import Path

import pytest

from sidechannel.engine.session import GameSession
from sidechannel.engine.story import Story

STORY_DIR = Path(__file__).parent.parent / "sidechannel" / "stories" / "story_03_night_shift"
FIX = [
    "ssh deploy@mirror",
    "tidewater-9",
    "set /etc/publisher/publisher.conf serve_unsigned no",
    "systemctl restart publisher",
]


def new_session(tmp_path: Path) -> GameSession:
    return GameSession.open(Story.load(STORY_DIR), STORY_DIR, tmp_path / "ns.json", fresh=True)


def choose(session: GameSession, text_start: str) -> None:
    session.choose(next(c for c in session.available_choices() if c.text.startswith(text_start)))


def texts(session: GameSession) -> list[str]:
    return [c.text for c in session.available_choices()]


def to_dex(session: GameSession) -> None:
    choose(session, "Pull on your hoodie")
    run = session.run_command
    run("connect forge")
    assert "caesar 11" in run("tail -n 4 /var/log/build.log").output
    assert "deploy_key.enc" not in run("ls /home/build").output  # hidden in .cache
    assert run("find / -name *.enc").output == "/home/build/.cache/deploy_key.enc"
    assert run("decrypt /home/build/.cache/deploy_key.enc 11").advanced
    assert session.scene.id == "key_found"
    choose(session, "Take the key")
    assert session.state.has_tool("deploy_key")
    assert session.scene.id == "dex_call"


def get_traced(session: GameSession) -> None:
    session.run_command("ssh deploy@mirror")
    session.run_command("tidewater-9")
    for _ in range(5):
        result = session.run_command("ls")
    assert result.advanced and session.scene.id == "burned"


def test_quick_fix_without_the_sniffer_reports_it(tmp_path):
    session = new_session(tmp_path)
    to_dex(session)
    choose(session, "Go in now.")
    for raw in ["status", "hint", "man ssh", "chat dex"]:  # local: never traced
        assert "[trace" not in session.run_command(raw).output
    results = [session.run_command(raw) for raw in FIX]
    assert results[-1].advanced and session.scene.id == "pulled"
    assert results[0].output.endswith("[trace 1/6]")
    assert texts(session) == ["Report everything to Lantern's security team."]
    choose(session, "Report everything")
    assert session.scene.id == "ending_reported"


def test_the_sniffer_is_offered_once_and_unlocks_the_unmasked_ending(tmp_path):
    session = new_session(tmp_path)
    to_dex(session)
    choose(session, "Borrow Dex's packet sniffer")
    assert session.state.has_tool("sniffer")
    assert texts(session) == ["Go in now."]  # `not: {tool: sniffer}`: offered once
    choose(session, "Go in now.")
    for raw in FIX:
        session.run_command(raw)
    choose(session, "Follow the attacker's session")
    assert session.scene.id == "ending_unmasked"


def test_traced_once_allows_one_retry_with_a_fresh_meter(tmp_path):
    session = new_session(tmp_path)
    to_dex(session)
    choose(session, "Go in now.")
    get_traced(session)
    assert session.runner.current_host is None
    choose(session, "Go back in, faster")
    assert session.run_command("ssh deploy@mirror").output.endswith("[trace 1/6]")  # fresh meter
    for raw in FIX[1:]:
        result = session.run_command(raw)
    assert result.advanced and session.scene.id == "pulled"


@pytest.mark.parametrize("retry", [False, True])
def test_walking_away_or_a_second_trace_ends_in_traced(tmp_path, retry):
    session = new_session(tmp_path)
    to_dex(session)
    choose(session, "Go in now.")
    get_traced(session)
    if retry:
        choose(session, "Go back in, faster")
        get_traced(session)
        assert texts(session) == ["Walk away and write it up."]  # no second retry
    choose(session, "Walk away")
    assert session.scene.id == "ending_burned"


def test_snoozing_ends_immediately(tmp_path):
    session = new_session(tmp_path)
    choose(session, "It's 3 AM.")
    assert session.scene.id == "ending_snooze"
