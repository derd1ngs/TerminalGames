"""End-to-end playthroughs of "Dead Drop", the story built around the Phase 4
shell features: piping a long log through `grep`, `ssh` with a password
found in that log, an `ordered_commands` procedure puzzle, and endings
gated by `all`/`not` requires combinators."""

from pathlib import Path

from terminalgames.engine.session import GameSession
from terminalgames.engine.story import Story

STORY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_02_dead_drop"
RUNBOOK = [
    "systemctl status replica",
    "set /etc/replica/replica.conf mode primary",
    "systemctl restart replica",
]


def new_session(tmp_path: Path) -> GameSession:
    return GameSession.open(Story.load(STORY_DIR), STORY_DIR, tmp_path / "dd.json", fresh=True)


def choose(session: GameSession, text_start: str) -> None:
    """Pick a choice by its text, so the tests read like the story."""
    choice = next(c for c in session.available_choices() if c.text.startswith(text_start))
    session.choose(choice)


def choice_texts(session: GameSession) -> list[str]:
    return [c.text for c in session.available_choices()]


def reach_vault(session: GameSession) -> None:
    choose(session, '"Send me the address.')
    assert session.run_command("connect relay").advanced
    assert session.scene.id == "relay_log"

    # The log is too long to read; the double grep narrows it to a few lines.
    narrowed = session.run_command("cat /var/log/relay.log | grep mara | grep vault").output
    assert len(narrowed.splitlines()) == 4
    password_line = next(line for line in narrowed.splitlines() if "new one:" in line)
    password = password_line.rsplit(" ", 1)[-1]

    assert session.run_command("ssh mara@vault").output == "mara@vault's password:"
    result = session.run_command(password)
    assert result.advanced, result.output
    assert session.scene.id == "vault_hub"
    assert session.runner.execute("whoami") == "mara@vault"


def run_runbook(session: GameSession) -> None:
    choose(session, "Follow the runbook.")
    assert session.scene.id == "recover"
    for raw in RUNBOOK:
        result = session.run_command(raw)
    assert result.advanced
    assert session.scene.id == "decide"


def test_connect_is_refused_on_the_vault(tmp_path):
    session = new_session(tmp_path)
    choose(session, '"Send me the address.')
    session.run_command("connect relay")
    assert "use 'ssh <user>@vault'" in session.run_command("connect vault").output


def test_runbook_out_of_order_does_not_recover_until_redone_in_order(tmp_path):
    session = new_session(tmp_path)
    reach_vault(session)
    choose(session, "Follow the runbook.")
    for raw in [RUNBOOK[1], RUNBOOK[2]]:  # skipped step 1
        assert not session.run_command(raw).advanced
    assert session.scene.id == "recover"
    for raw in RUNBOOK:
        result = session.run_command(raw)
    assert result.advanced


def test_going_alone_offers_publish_and_burn_but_not_hand_off(tmp_path):
    session = new_session(tmp_path)
    reach_vault(session)
    run_runbook(session)
    texts = choice_texts(session)
    assert any(t.startswith("Burn it.") for t in texts)
    assert not any(t.startswith("Hand everything to Juno") for t in texts)
    choose(session, "Burn it.")
    assert session.scene.id == "ending_burned"
    assert {e.category for e in session.state.journal.all()} == {"trace", "lead", "suspect"}


def test_confiding_in_juno_unlocks_the_hand_off_ending(tmp_path):
    session = new_session(tmp_path)
    reach_vault(session)
    choose(session, "Message Juno")
    choose(session, "Tell her everything")
    assert session.scene.id == "vault_hub"
    assert not any(t.startswith("Message Juno") for t in choice_texts(session))  # `not` gate: once only
    run_runbook(session)
    texts = choice_texts(session)
    assert not any(t.startswith("Burn it.") for t in texts)
    choose(session, "Hand everything to Juno")
    assert session.scene.id == "ending_trusted"


def test_asking_juno_but_staying_vague_leaves_only_publish(tmp_path):
    session = new_session(tmp_path)
    reach_vault(session)
    choose(session, "Message Juno")
    choose(session, "Keep it vague")
    run_runbook(session)
    assert choice_texts(session) == ["Publish the whole drop tonight."]
    choose(session, "Publish")
    assert session.scene.id == "ending_published"


def test_declining_ends_immediately(tmp_path):
    session = new_session(tmp_path)
    choose(session, "\"Mara's a grown-up.")
    assert session.scene.type == "ending"
