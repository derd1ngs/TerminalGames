"""Unit tests for terminalgames/tools/check_story.py, using small synthetic
stories built directly from the dataclasses (not via YAML) so each test
isolates exactly one reachability scenario."""

import pytest

from terminalgames.engine.dialogue import NPC, Topic
from terminalgames.engine.shell import Host, Network
from terminalgames.engine.story import Chapter, Choice, Scene, Story, TerminalBlock
from terminalgames.tools.check_story import check_story, main, mermaid_graph


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
    # The search doesn't call an unopened gate a bug; the lint names its cause.
    assert report.problems == ["flag 'never_set' is required at c1:start, but nothing ever sets it"]


def test_connect_gate_makes_scene_unreachable_and_lint_names_the_gate():
    """A flag can be legitimately *declared* settable (on_connect_flag) and
    still be practically unreachable if its own gate can never pass. The
    search reports that as unreached content, not as a "win_flag never set"
    problem (the mechanism does exist); the lint names the gate's flag."""
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

    # No "win_flag never set" from the search -- connecting does set it; the
    # lint reports the real cause, the gate's flag.
    assert report.problems == ["flag 'key' is required at host vault, but nothing ever sets it"]
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

    # The win_flag is genuinely settable via dialogue (no search problem); the
    # lint reports the real cause, the topic's own gate.
    assert report.problems == [
        "flag 'unlockable' is required at topic ghost:secret, but nothing ever sets it"
    ]
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


def test_ordered_commands_scene_counts_as_solvable():
    story = _story(
        {
            "start": Scene(
                id="start",
                type="terminal",
                terminal=TerminalBlock(win_flag="procedure_done", next="c1:end", ordered_commands=["a", "b"]),
            ),
            "end": Scene(id="end", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert report.ok


def test_combinator_gates_are_explored_per_branch():
    story = _story(
        {
            "start": Scene(
                id="start",
                choices=[
                    Choice(text="left", next="c1:hub", sets={"left": True}),
                    Choice(text="right", next="c1:hub"),
                ],
            ),
            "hub": Scene(
                id="hub",
                choices=[
                    Choice(text="went left", next="c1:end_left", requires={"flag": "left"}),
                    Choice(text="did not", next="c1:end_right", requires={"not": {"flag": "left"}}),
                    Choice(
                        text="impossible",
                        next="c1:end_never",
                        requires={"all": [{"flag": "left"}, {"not": {"flag": "left"}}]},
                    ),
                ],
            ),
            "end_left": Scene(id="end_left", type="ending"),
            "end_right": Scene(id="end_right", type="ending"),
            "end_never": Scene(id="end_never", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert report.visited_endings == {"end_left", "end_right"}
    assert report.unreached_endings == {"end_never"}


def _lint_story(choices: list[Choice]) -> Story:
    return _story({"start": Scene(id="start", choices=choices), "end": Scene(id="end", type="ending")})


def test_flag_required_but_never_set_is_a_problem_even_when_nested():
    story = _lint_story(
        [
            Choice(
                text="go",
                next="c1:end",
                requires={"any": [{"not": {"flag_equals": {"key": "mood", "value": 1}}}]},
            )
        ]
    )

    report = check_story(story, Network(), {})

    assert report.problems == ["flag 'mood' is required at c1:start, but nothing ever sets it"]


def test_flag_required_by_a_topic_or_host_counts_too():
    npc = NPC(
        id="n",
        name="N",
        channel="chat",
        topics={"t": Topic(id="t", prompt="p", response="r", requires={"flag": "a"})},
    )
    network = Network(hosts={"h": Host(id="h", requires_to_connect={"flag": "b"})})
    story = _lint_story([Choice(text="go", next="c1:end")])

    report = check_story(story, network, {"n": npc})

    assert report.problems == [
        "flag 'a' is required at topic n:t, but nothing ever sets it",
        "flag 'b' is required at host h, but nothing ever sets it",
    ]


def test_journal_entry_required_but_never_logged_is_a_problem():
    story = _lint_story(
        [
            Choice(text="log", next="c1:end", logs=[{"id": "seen", "text": "x"}]),
            Choice(text="gated", next="c1:end", requires={"journal_has": "sen"}),  # typo
        ]
    )

    report = check_story(story, Network(), {})

    assert report.problems == ["journal entry 'sen' is required at c1:start, but nothing ever logs it"]


def test_tool_required_but_never_granted_is_a_problem():
    story = _lint_story([Choice(text="go", next="c1:end", requires={"tool": "wireshark"})])

    report = check_story(story, Network(), {})

    assert report.problems == ["tool 'wireshark' is required at c1:start, but nothing ever grants it"]


def test_granted_tool_opens_its_gate_and_unused_grants_warn():
    story = _story(
        {
            "start": Scene(
                id="start",
                choices=[
                    Choice(text="pick up", next="c1:hub", sets={"tool.sniffer": True, "tool.spare": True})
                ],
            ),
            "hub": Scene(id="hub", choices=[Choice(text="use", next="c1:end", requires={"tool": "sniffer"})]),
            "end": Scene(id="end", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert report.ok  # reachable: the search applies the grant for real
    assert report.warnings == ["tool 'spare' is granted at c1:start, but nothing ever requires it"]


def test_a_topic_granting_a_tool_is_not_mistaken_for_a_flag():
    npc = NPC(
        id="n",
        name="N",
        channel="chat",
        topics={"t": Topic(id="t", prompt="p", response="r", sets={"tool.key": True})},
    )
    story = _lint_story([Choice(text="go", next="c1:end", requires={"tool": "key"})])

    report = check_story(story, Network(), {"n": npc})

    assert not report.problems
    assert not report.warnings


def test_flag_set_but_never_read_is_only_a_warning():
    story = _lint_story([Choice(text="go", next="c1:end", sets={"orphan": True, "trust.x": 1})])
    network = Network(hosts={"h": Host(id="h", on_connect_flag="on_h")})

    report = check_story(story, network, {})

    assert report.ok
    assert report.warnings == [
        "flag 'on_h' is set at host h, but nothing ever reads it",
        "flag 'orphan' is set at c1:start, but nothing ever reads it",
    ]


def test_set_and_read_flags_produce_no_lint():
    story = _story(
        {
            "start": Scene(id="start", choices=[Choice(text="a", next="c1:hub", sets={"k": True})]),
            "hub": Scene(id="hub", choices=[Choice(text="b", next="c1:end", requires={"flag": "k"})]),
            "end": Scene(id="end", type="ending"),
        }
    )

    report = check_story(story, Network(), {})

    assert report.ok and not report.warnings


def test_mermaid_graph_shapes_edges_and_escaping():
    story = _story(
        {
            "start": Scene(
                id="start",
                choices=[
                    Choice(text='Say "hi"', next="c1:shell"),
                    Choice(text="gated", next="c1:end", requires={"flag": "k"}),
                ],
            ),
            "shell": Scene(
                id="shell", type="terminal", terminal=TerminalBlock(win_flag="done", next="c1:end")
            ),
            "end": Scene(id="end", type="ending"),
        }
    )

    graph = mermaid_graph(story).splitlines()

    assert graph[0] == "flowchart TD"
    assert '    n_c1__start["start"]' in graph
    assert '    n_c1__shell[["shell"]]' in graph
    assert '    n_c1__end(["end"])' in graph  # prefixed id: bare `end` is a Mermaid keyword
    assert '  start((" ")) --> n_c1__start' in graph
    assert '  n_c1__start -->|"Say #quot;hi#quot;"| n_c1__shell' in graph
    assert '  n_c1__start -.->|"gated"| n_c1__end' in graph
    assert '  n_c1__shell ==>|"done"| n_c1__end' in graph


def test_graph_cli_prints_a_story_graph(capsys):
    main(["dead_drop", "--graph"])
    out = capsys.readouterr().out
    assert out.startswith("flowchart TD")
    assert "n_chapter_01__recover[[" in out


def test_graph_cli_rejects_all(capsys):
    with pytest.raises(SystemExit):
        main(["--all", "--graph"])
