"""Structural check, run against every shipped story: no unreachable
scenes/endings, and every terminal scene's win_flag is actually settable
by something in that story's network.yaml/npcs.yaml. See
terminalgames/tools/check_story.py for how the search works and what it
deliberately doesn't model (documented there rather than repeated here).
"""

import pytest

from terminalgames.main import discover_stories
from terminalgames.tools.check_story import check_story_dir

STORIES = discover_stories()


@pytest.mark.parametrize("story_dir", STORIES, ids=[s.name for s in STORIES])
def test_story_has_no_unreachable_content(story_dir):
    report = check_story_dir(story_dir)
    assert not report.problems, report.problems
    assert not report.unreached_scenes, report.unreached_scenes
    assert not report.unreached_endings, report.unreached_endings
