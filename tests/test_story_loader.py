from pathlib import Path

import pytest
import yaml

from terminalgames.engine.state import GameState
from terminalgames.engine.story import Story, StoryLoadError, apply_effects, check_requires

ZERO_DAY_DIR = Path(__file__).parent.parent / "terminalgames" / "stories" / "story_01_zero_day"


def test_zero_day_loads_and_validates():
    story = Story.load(ZERO_DAY_DIR)
    assert story.id == "zero_day"
    assert story.start_ref() == ("chapter_01", "intro")
    assert "gateway_shell" in story.chapters["chapter_01"].scenes


def write_multichapter_story(root: Path) -> Path:
    (root / "chapters").mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "multi_test",
                "title": "Multi Test",
                "start": "one:start",
                "chapters": ["one.yaml", "two.yaml"],
            }
        )
    )
    (root / "chapters" / "one.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "one",
                "scenes": [
                    {
                        "id": "start",
                        "text": "Chapter one.",
                        "choices": [{"text": "Continue", "next": "two:landing"}],
                    }
                ],
            }
        )
    )
    (root / "chapters" / "two.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "two",
                "scenes": [
                    {"id": "landing", "type": "ending", "text": "The end."},
                ],
            }
        )
    )
    return root


def test_cross_chapter_reference_resolves(tmp_path: Path):
    story = Story.load(write_multichapter_story(tmp_path))
    assert story.get_scene("two", "landing").type == "ending"
    scene = story.get_scene("one", "start")
    chapter_id, scene_id = story.resolve(scene.choices[0].next, "one")
    assert (chapter_id, scene_id) == ("two", "landing")


def test_dangling_reference_raises(tmp_path: Path):
    root = write_multichapter_story(tmp_path)
    chapter_path = root / "chapters" / "one.yaml"
    data = yaml.safe_load(chapter_path.read_text())
    data["scenes"][0]["choices"][0]["next"] = "two:nonexistent"
    chapter_path.write_text(yaml.safe_dump(data))
    with pytest.raises(StoryLoadError):
        Story.load(root)


def test_manifest_missing_key_raises_story_load_error(tmp_path: Path):
    root = write_multichapter_story(tmp_path)
    manifest_path = root / "manifest.yaml"
    data = yaml.safe_load(manifest_path.read_text())
    del data["start"]
    manifest_path.write_text(yaml.safe_dump(data))
    with pytest.raises(StoryLoadError, match="start"):
        Story.load(root)


def test_scene_missing_id_raises_story_load_error(tmp_path: Path):
    root = write_multichapter_story(tmp_path)
    chapter_path = root / "chapters" / "one.yaml"
    data = yaml.safe_load(chapter_path.read_text())
    del data["scenes"][0]["id"]
    chapter_path.write_text(yaml.safe_dump(data))
    with pytest.raises(StoryLoadError, match="one.yaml"):
        Story.load(root)


def test_choice_missing_next_raises_story_load_error(tmp_path: Path):
    root = write_multichapter_story(tmp_path)
    chapter_path = root / "chapters" / "one.yaml"
    data = yaml.safe_load(chapter_path.read_text())
    del data["scenes"][0]["choices"][0]["next"]
    chapter_path.write_text(yaml.safe_dump(data))
    with pytest.raises(StoryLoadError, match="next"):
        Story.load(root)


def test_check_requires_flag():
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    assert check_requires(None, state) is True
    assert check_requires({"flag": "has_key"}, state) is False
    state.set_flag("has_key", True)
    assert check_requires({"flag": "has_key"}, state) is True


def test_check_requires_trust_and_journal():
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    assert check_requires({"trust_at_least": {"npc": "ghost", "value": 1}}, state) is False
    state.adjust_trust("ghost", 1)
    assert check_requires({"trust_at_least": {"npc": "ghost", "value": 1}}, state) is True
    assert check_requires({"journal_has": "lead1"}, state) is False


def test_apply_effects_sets_flags_and_trust():
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    apply_effects(
        {"met_ghost": True, "trust.ghost": 2},
        [{"id": "lead1", "category": "lead", "text": "A lead."}],
        state,
        discovered_at="c:a",
    )
    assert state.has_flag("met_ghost")
    assert state.get_trust("ghost") == 2
    assert state.journal.has("lead1")
