"""Strict story loading: a typo or invalid value in any story file is a load
error naming the file, place and the closest valid key -- never silently
ignored (a misspelled `requries:` used to quietly delete a gate)."""

import shutil
from pathlib import Path

import pytest
import yaml

from sidechannel.engine.loader import load_network, load_npc_roster
from sidechannel.engine.story import Story, StoryLoadError

DEAD_DROP = Path(__file__).parent.parent / "sidechannel" / "stories" / "story_02_dead_drop"


@pytest.fixture
def story_dir(tmp_path):
    return Path(shutil.copytree(DEAD_DROP, tmp_path / "story"))


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def chapter(story_dir: Path) -> Path:
    return story_dir / "chapters" / "chapter_01.yaml"


def test_shipped_stories_load_strictly():
    for story_dir in DEAD_DROP.parent.iterdir():
        Story.load(story_dir)
        load_network(story_dir)
        load_npc_roster(story_dir)


def test_misspelled_requires_is_rejected_with_a_suggestion(story_dir):
    replace_in(
        chapter(story_dir),
        "        requires:\n          not: {flag: asked_juno}",
        "        requries:\n          not: {flag: asked_juno}",
    )
    with pytest.raises(StoryLoadError) as exc:
        Story.load(story_dir)
    assert str(exc.value) == (
        "In chapter 'chapter_01' (chapter_01.yaml): scene 'vault_hub', choice 2: "
        "choice: unknown key 'requries' (did you mean 'requires'?)"
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("category: trace", "category: tracee", "invalid journal category 'tracee' (did you mean 'trace'?)"),
        (
            "    type: narrative\n    text: |\n      11:52",
            "    type: narative\n    text: |\n      11:52",
            "invalid scene type 'narative'",
        ),
        (
            "      win_flag: on_relay",
            "      winflag: on_relay",
            "unknown key 'winflag' (did you mean 'win_flag'?)",
        ),
        (
            "          not: {flag: asked_juno}\n        sets",
            "          nott: {flag: asked_juno}\n        sets",
            "unknown key 'nott' (did you mean 'not'?)",
        ),
        (
            "trust_at_least: {npc: juno, value: 2}",
            "trust_at_least: {npc: juno, value: two}",
            "value must be a whole number",
        ),
        ("          trust.juno: 1", "          trust.juno: lots", "'trust.juno' must be a whole number"),
        (
            "    type: ending\n    text: |\n      JUNO doesn't",
            "    type: ending\n    choices: [{text: x, next: intro}]\n    text: |\n      JUNO doesn't",
            "an ending can't have choices",
        ),
        (
            "  - id: relay_shell\n    type: terminal",
            "  - id: relay_shell\n    type: narrative",
            "only terminal scenes may have a 'terminal' block",
        ),
        ('        - "`connect relay`."', "        - 42", "every hint must be a non-empty string"),
        (
            "      win_flag: on_relay",
            "      win_flag: on_relay\n      trace: {limit: 0, on_trace: intro}",
            "limit must be a whole number of at least 1",
        ),
        (
            "      win_flag: on_relay",
            "      win_flag: on_relay\n      trace: {limit: 3, ontrace: intro}",
            "unknown key 'ontrace'",
        ),
        (
            '      hints:\n        - "Juno',
            '      hint:\n        - "Juno',
            "unknown key 'hint' (did you mean 'hints'?)",
        ),
    ],
)
def test_chapter_mistakes_are_rejected(story_dir, old, new, message):
    replace_in(chapter(story_dir), old, new)
    with pytest.raises(
        StoryLoadError, match=message.replace("(", r"\(").replace(")", r"\)").replace("?", r"\?")
    ):
        Story.load(story_dir)


def test_narrative_scene_without_choices_is_a_dead_end(story_dir):
    data = yaml.safe_load(chapter(story_dir).read_text())
    data["scenes"].append({"id": "stuck", "text": "nowhere to go"})
    chapter(story_dir).write_text(yaml.safe_dump(data))
    with pytest.raises(StoryLoadError, match="scene 'stuck': a narrative scene needs at least one choice"):
        Story.load(story_dir)


def test_manifest_typo_is_rejected(story_dir):
    replace_in(story_dir / "manifest.yaml", "title:", "titel:")
    with pytest.raises(
        StoryLoadError, match=r"manifest.yaml: unknown key 'titel' \(did you mean 'title'\?\)"
    ):
        Story.load(story_dir)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "    logins:",
            "    login:",
            "network.yaml host 'vault': unknown key 'login' \\(did you mean 'logins'\\?\\)",
        ),
        ("        config_path:", "        configpath:", "service 'replica': unknown key 'configpath'"),
        (
            "                type: config",
            "                type: conf",
            "invalid filesystem node type 'conf' \\(did you mean 'config'\\?\\)",
        ),
        (
            "                content: |\n                  REPLICA",
            "                contents: |\n                  REPLICA",
            "'/srv/drop/RUNBOOK': unknown key 'contents' \\(did you mean 'content'\\?\\)",
        ),
    ],
)
def test_network_mistakes_are_rejected(story_dir, old, new, message):
    replace_in(story_dir / "network.yaml", old, new)
    with pytest.raises(StoryLoadError, match=message):
        load_network(story_dir)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("    channel: chat", "    channel: sms", "npcs.yaml npc 'juno': invalid channel 'sms'"),
        (
            "        reliability: truthful\n\n      - id: vault",
            "        reliability: truthfull\n\n      - id: vault",
            "topic 'mara': invalid reliability 'truthfull' \\(did you mean 'truthful'\\?\\)",
        ),
        (
            '        prompt: "ask about Mara"',
            '        promt: "ask about Mara"',
            "topic 'mara': unknown key 'promt' \\(did you mean 'prompt'\\?\\)",
        ),
        ("      - id: vault\n", "      - id: mara\n", "duplicate topic id 'mara'"),
    ],
)
def test_npc_mistakes_are_rejected(story_dir, old, new, message):
    replace_in(story_dir / "npcs.yaml", old, new)
    with pytest.raises(StoryLoadError, match=message):
        load_npc_roster(story_dir)
