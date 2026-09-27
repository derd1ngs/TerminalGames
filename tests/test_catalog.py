"""engine/catalog.py: the story list both frontends show."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from sidechannel.engine.catalog import StoryInfo, catalog
from sidechannel.engine.endings import record_ending
from sidechannel.engine.story import Story, StoryLoadError

STORIES = Path(__file__).parent.parent / "sidechannel" / "stories"


def info(
    title: str, published: date | None, chapters: int = 1, endings: int = 4, found: int = 0
) -> StoryInfo:
    return StoryInfo("ref", "id", title, published, "", chapters, endings, found)


def test_shipped_stories_newest_first(tmp_path):
    stories = catalog(tmp_path)
    assert [(s.title, s.published_label) for s in stories] == [
        ("Dead Drop", "26 Sep 2026"),
        ("Night Shift", "26 Sep 2026"),  # same date: then by title
        ("Zero Day", "8 Sep 2026"),
    ]
    assert all(s.description for s in stories)


def test_undated_stories_come_last(tmp_path):
    stories_dir = tmp_path / "stories"
    for name in ("story_01_zero_day", "story_02_dead_drop"):
        shutil.copytree(STORIES / name, stories_dir / name)
    manifest = stories_dir / "story_01_zero_day" / "manifest.yaml"
    manifest.write_text(
        "\n".join(line for line in manifest.read_text().splitlines() if not line.startswith("published:"))
    )
    assert [s.title for s in catalog(tmp_path, stories_dir)] == ["Dead Drop", "Zero Day"]
    assert catalog(tmp_path, stories_dir)[1].published is None


def test_summary_line():
    assert (
        info("x", date(2026, 9, 8), chapters=3, endings=6).summary
        == "Published 8 Sep 2026 · 3 chapters · 6 endings"
    )
    assert info("x", None, chapters=1, endings=1, found=1).summary == "1 chapter · 1 ending · 1/1 found"


def test_endings_found_come_from_the_gallery(tmp_path):
    record_ending(tmp_path / "night_shift", "ending_snooze")
    night_shift = next(s for s in catalog(tmp_path) if s.id == "night_shift")
    assert night_shift.endings_found == 1
    assert night_shift.summary.endswith("· 1/4 found")


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("published: yesterday", "published must be a date like 2026-09-08, not 'yesterday'"),
        ('published: "2026-9-8"', "published must be a date like 2026-09-08"),
        ("published: 2026-13-40", "manifest.yaml: month must be in 1..12"),
    ],
)
def test_bad_published_dates_are_load_errors(tmp_path, line, error):
    story_dir = shutil.copytree(STORIES / "story_02_dead_drop", tmp_path / "story")
    manifest = story_dir / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("published: 2026-09-26", line))
    with pytest.raises(StoryLoadError, match=error):
        Story.load(story_dir)


def test_quoted_published_date_is_accepted(tmp_path):
    story_dir = shutil.copytree(STORIES / "story_02_dead_drop", tmp_path / "story")
    manifest = story_dir / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("published: 2026-09-26", 'published: "2026-09-26"'))
    assert Story.load(story_dir).published == date(2026, 9, 26)
