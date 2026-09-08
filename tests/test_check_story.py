"""Unit tests for terminalgames/tools/check_story.py, using small synthetic
stories built directly from the dataclasses (not via YAML) so each test
isolates exactly one reachability scenario."""

from terminalgames.engine.dialogue import NPC, Topic
from terminalgames.engine.shell import Host, Network
from terminalgames.engine.story import Chapter, Choice, Scene, Story, TerminalBlock
from terminalgames.tools.check_story import check_story


def _story(scenes: dict[str, Scene], start: str = "c1:start") -> Story:
    return Story(id="s", title="Test", start=start, chapters={"c1": Chapter(id="c1", scenes=scenes)})


def test_clean_linear_story_reports_ok():
    story = _story(
        {
            "start": Scene(
                id="start", type="terminal", terminal=TerminalBlock(win_flag="connected_x", next="c1:end")
            ),
            "end": Scene(id="end", type="ending"),
        }
    )
    network = Network(hosts={"x": Host(id="x", on_connect_flag="connected_x")})

    report = check_story(story, network, {})

    assert report.ok
    assert report.visited_scenes == {("c1", "start"), ("c1", "end")}
    assert report.visited_endings == {"end"}


def test_win_flag_never_set_anywhere_is_a_problem():
    story = _story(
        {
            "start": Scene(
                id="start", type="terminal", terminal=TerminalBlock(win_flag="typo_flag", next="c1:end")
            ),
            "end": Scene(id="end", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert not report.ok
    assert any("typo_flag" in p for p in report.problems)
    assert ("c1", "end") in report.unreached_scenes
    assert "end" in report.unreached_endings


def test_unreachable_scene_behind_an_unsatisfiable_requires():
    story = _story(
        {
            "start": Scene(
                id="start",
                type="narrative",
                choices=[
                    Choice(text="go", next="c1:end"),
                    Choice(text="secret", next="c1:secret", requires={"flag": "never_set"}),
                ],
            ),
            "end": Scene(id="end", type="ending"),
            "secret": Scene(id="secret", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert ("c1", "start") in report.visited_scenes
    assert ("c1", "end") in report.visited_scenes
    assert "end" in report.visited_endings
    assert ("c1", "secret") in report.unreached_scenes
    assert "secret" in report.unreached_endings
    assert not report.problems  # unreachable, but not a "never settable" bug


def test_connect_gate_makes_scene_unreachable_without_being_a_problem():
    """A flag can be legitimately *declared* settable (on_connect_flag) and
    still be practically unreachable if its own gate can never pass -- that's
    a design/content issue to report as unreached, not a hard "never set"
    problem, since the mechanism to set it does genuinely exist."""
    story = _story(
        {
            "start": Scene(
                id="start",
                type="terminal",
                terminal=TerminalBlock(win_flag="connected_vault", next="c1:end"),
            ),
            "end": Scene(id="end", type="ending"),
        }
    )
    network = Network(
        hosts={
            "vault": Host(id="vault", on_connect_flag="connected_vault", requires_to_connect={"flag": "key"})
        }
    )

    report = check_story(story, network, {})

    assert not report.problems
    assert ("c1", "end") in report.unreached_scenes


def test_connect_gate_satisfied_earlier_makes_scene_reachable():
    story = _story(
        {
            "start": Scene(
                id="start",
                type="narrative",
                choices=[Choice(text="get key", next="c1:vault", sets={"key": True})],
            ),
            "vault": Scene(
                id="vault",
                type="terminal",
                terminal=TerminalBlock(win_flag="connected_vault", next="c1:end"),
            ),
            "end": Scene(id="end", type="ending"),
        }
    )
    network = Network(
        hosts={
            "vault": Host(id="vault", on_connect_flag="connected_vault", requires_to_connect={"flag": "key"})
        }
    )

    report = check_story(story, network, {})

    assert report.ok


def test_dialogue_settable_flag_respects_the_topics_own_requires():
    story = _story(
        {
            "start": Scene(
                id="start", type="terminal", terminal=TerminalBlock(win_flag="asked_secret", next="c1:end")
            ),
            "end": Scene(id="end", type="ending"),
        }
    )
    npc = NPC(
        id="ghost",
        name="Ghost",
        channel="email",
        topics={
            "secret": Topic(
                id="secret",
                prompt="p",
                response="r",
                requires={"flag": "unlockable"},
                sets={"asked_secret": True},
            )
        },
    )

    report = check_story(story, Network(), {"ghost": npc})

    assert not report.problems  # the flag genuinely is declared settable via dialogue
    assert ("c1", "end") in report.unreached_scenes  # ...but its topic's own gate never opens


def test_dialogue_settable_flag_reachable_once_its_requires_is_satisfiable():
    story = _story(
        {
            "start": Scene(
                id="start",
                type="narrative",
                choices=[Choice(text="unlock", next="c1:ask", sets={"unlockable": True})],
            ),
            "ask": Scene(
                id="ask", type="terminal", terminal=TerminalBlock(win_flag="asked_secret", next="c1:end")
            ),
            "end": Scene(id="end", type="ending"),
        }
    )
    npc = NPC(
        id="ghost",
        name="Ghost",
        channel="email",
        topics={
            "secret": Topic(
                id="secret",
                prompt="p",
                response="r",
                requires={"flag": "unlockable"},
                sets={"asked_secret": True},
            )
        },
    )

    report = check_story(story, Network(), {"ghost": npc})

    assert report.ok


def test_hub_scene_loop_terminates():
    """A hub scene offering a requires-gated lead back to itself must not
    make the search loop forever."""
    story = _story(
        {
            "hub": Scene(
                id="hub",
                type="narrative",
                choices=[
                    Choice(text="find lead", next="c1:hub", sets={"found_lead": True}),
                    Choice(text="leave", next="c1:end", requires={"flag": "found_lead"}),
                ],
            ),
            "end": Scene(id="end", type="ending"),
        },
        start="c1:hub",
    )

    report = check_story(story, Network(), {})

    assert report.ok
