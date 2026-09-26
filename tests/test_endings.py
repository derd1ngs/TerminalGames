"""engine/endings.py: which endings a player has found, across save slots."""

from pathlib import Path

from terminalgames.engine.endings import ending_title, found_endings, gallery, record_ending
from terminalgames.engine.loader import list_save_slots, save_slot_path
from terminalgames.engine.session import GameSession
from terminalgames.engine.story import Scene, Story

DEAD_DROP_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_02_dead_drop"
DEAD_DROP = Story.load(DEAD_DROP_DIR)


def decline(saves_root: Path, slot: str) -> GameSession:
    session = GameSession.open(
        DEAD_DROP, DEAD_DROP_DIR, save_slot_path(saves_root, "dead_drop", slot), fresh=True
    )
    session.choose(next(c for c in session.available_choices() if c.text.startswith('"Mara')))
    return session


def test_record_ending_only_counts_the_first_time(tmp_path):
    assert record_ending(tmp_path, "ending_a") is True
    assert record_ending(tmp_path, "ending_a") is False
    assert record_ending(tmp_path, "ending_b") is True
    assert list(found_endings(tmp_path)) == ["ending_a", "ending_b"]


def test_ending_title_comes_from_the_ending_line():
    assert (
        ending_title(Scene(id="e", type="ending", text="Bye.\n\n-- ENDING: Signal Boost --\n"))
        == "Signal Boost"
    )
    assert ending_title(Scene(id="e_plain", type="ending", text="Just the end.")) == "e_plain"


def test_reaching_an_ending_records_it_and_counts_across_slots(tmp_path):
    session = decline(tmp_path, "one")
    assert session.scene.id == "ending_declined"
    assert session.endings_found() == (1, 4)

    other = decline(tmp_path, "two")  # same ending from another slot: still one found
    assert other.endings_found() == (1, 4)
    assert gallery(DEAD_DROP, tmp_path / "dead_drop") == [
        ("Not Your Circus", True),
        ("Signal Boost", False),
        ("The Drop Worked", False),
        ("Scorched Earth", False),
    ]


def test_the_endings_file_is_never_listed_as_a_save_slot(tmp_path):
    decline(tmp_path, "one").save()
    assert (tmp_path / "dead_drop" / "found-endings.txt").exists()
    assert list_save_slots(tmp_path, "dead_drop") == ["one"]
