"""web_bridge: the JSON facade the browser build calls from JavaScript.
Runs under normal CPython here; the same module runs in Pyodide in the page."""

import json

import pytest

from terminalgames import web_bridge as bridge

# The Zero Day spine up to the trust_call choice, as the page would send it:
# ("choose", index) or ("cmd", line).
SPINE = [
    ("choose", 0),
    ("choose", 0),
    ("cmd", "connect gateway"),
    ("cmd", "set /etc/netmon/netmon.conf bind_address 0.0.0.0"),
    ("cmd", "set /etc/netmon/netmon.conf allow_query allow"),
    ("cmd", "systemctl restart netmon"),
    ("choose", 0),
    ("cmd", "connect sentinel"),
    ("cmd", "decrypt /var/log/ops/handoff.enc 5"),
    ("cmd", "mail send t cold_storage"),
    ("choose", 0),
    ("cmd", "decrypt /var/log/ops/blackbox.enc RAVEN"),
]


@pytest.fixture(autouse=True)
def saves_root(tmp_path):
    bridge.init(str(tmp_path))
    return tmp_path


def play(steps):
    view = None
    for kind, arg in steps:
        view = json.loads(bridge.choose(arg) if kind == "choose" else bridge.command(arg))
    return view


def test_list_stories_includes_zero_day():
    stories = json.loads(bridge.list_stories())
    assert {"ref": "story_01_zero_day", "id": "zero_day", "title": "Zero Day"} in stories


def test_start_fresh_shows_the_first_scene_with_choices():
    view = json.loads(bridge.start("zero_day", "web", True))
    assert view["title"] == "Zero Day"
    assert view["entered"] is True
    assert view["scene"]["id"] == "intro"
    assert view["scene"]["type"] == "narrative"
    assert view["choices"]
    assert view["prompt"] == "local$"


def test_a_new_game_appears_in_the_slot_list():
    bridge.start("zero_day", "web", True)
    slots = json.loads(bridge.list_slots("zero_day"))
    assert [s["slot"] for s in slots] == ["web"]
    assert slots[0]["summary"].startswith("web -- chapter_01:intro")


def test_command_output_and_scene_advance():
    bridge.start("zero_day", "web", True)
    play([("choose", 0), ("choose", 0)])
    view = json.loads(bridge.command("whoami"))
    assert view["output"] == "user@localhost"
    assert view["entered"] is False
    view = json.loads(bridge.command("connect gateway"))
    assert view["entered"] is True
    assert view["scene"]["id"] == "gateway_shell"
    assert view["prompt"] == "gateway$"


def test_full_spine_through_the_bridge_reaches_trust_call_and_an_ending():
    bridge.start("zero_day", "web", True)
    view = play(SPINE)
    assert view["scene"]["id"] == "trust_call"
    view = play([("choose", 1), ("choose", 2)])  # wary -> log everything and disappear
    assert view["scene"]["type"] == "ending"
    assert view["choices"] == []


def test_chapter_crossing_autosave_is_reported_as_a_notice():
    bridge.start("zero_day", "web", True)
    view = play(SPINE[:7])  # the 7th step crosses into chapter_02
    assert any("Autosaved" in n for n in view["notices"])


def test_save_and_continue_resumes_the_slot():
    bridge.start("zero_day", "web", True)
    play([("choose", 0), ("choose", 0), ("cmd", "connect gateway")])
    assert json.loads(bridge.command(":save"))["output"] == "Saved."

    view = json.loads(bridge.start("zero_day", "web", False))
    assert view["scene"]["id"] == "gateway_shell"
    assert view["prompt"] == "gateway$"


def test_continue_on_a_slot_without_a_save_starts_fresh():
    view = json.loads(bridge.start("zero_day", "nothing-here", False))
    assert view["scene"]["id"] == "intro"


def test_complete_uses_the_engine_completion():
    bridge.start("zero_day", "web", True)
    assert json.loads(bridge.complete("con")) == ["connect"]


def test_compose_mail_goes_through_mail_sync_matching():
    bridge.start("zero_day", "web", True)
    play(SPINE[:9])  # up to contact_t
    assert json.loads(bridge.email_contacts()) == [{"id": "t", "name": "T"}]

    bounced = json.loads(bridge.compose_mail("t", "hello there", "hi"))
    assert bounced["output"].startswith("Bounced: draft_1.txt")
    assert bounced["entered"] is False

    view = json.loads(bridge.compose_mail("t", "Cold storage passphrase?", "Remember it?"))
    assert view["output"] == "Sent: draft_2.txt -> T"
    assert view["entered"] is True
    assert view["scene"]["id"] == "waiting"


def test_game_calls_before_start_fail_clearly():
    with pytest.raises(RuntimeError, match="start"):
        bridge.command("help")


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("plain & <safe>", "plain &amp; &lt;safe&gt;"),
        ("[bold]hi[/bold] there", '<span class="m-bold">hi</span> there'),
        ("[bold red]alert[/]", '<span class="m-bold m-red">alert</span>'),
        ("[dim]unclosed", '<span class="m-dim">unclosed</span>'),
        ("stray [/bold] close", "stray  close"),
        (r"literal \[bold] text", "literal [bold] text"),
        ("[1] and [] stay", "[1] and [] stay"),
    ],
)
def test_markup_to_html(markup, expected):
    assert bridge.markup_to_html(markup) == expected


def test_scene_html_reflows_paragraphs_and_marks_code():
    text = "You're in. Run\n`whoami` first.\n\n-- ENDING: [bold]Done[/bold] --\n"
    assert bridge.scene_html(text) == (
        'You\'re in. Run <code>whoami</code> first.\n\n-- ENDING: <span class="m-bold">Done</span> --'
    )


def test_ssh_password_line_is_flagged_secret_and_not_a_meta_command():
    bridge.start("zero_day", "web", True)
    play([("choose", 0), ("choose", 0)])
    bridge._session.runner.network.hosts["gateway"].logins = {"ops": ":save"}
    view = json.loads(bridge.command("ssh ops@gateway"))
    assert view["prompt"] == "password:"
    assert view["secret"] is True
    view = json.loads(bridge.command(":save"))  # the password, not a save
    assert view["secret"] is False
    assert view["scene"]["id"] == "gateway_shell"


def test_export_then_import_round_trip_with_overwrite_handshake(saves_root, tmp_path):
    bridge.start("zero_day", "web", True)
    play([("choose", 0), ("choose", 0), ("cmd", "connect gateway")])
    bridge.save()
    text = bridge.export_slot("zero_day", "web")
    assert json.loads(text)["sandbox"]

    # Same browser: the slot exists, so the page is asked first.
    assert json.loads(bridge.import_slot("zero_day", text, False)) == {"exists": "web"}
    assert json.loads(bridge.import_slot("zero_day", text, True)) == {"slot": "web"}

    # Another browser (fresh saves root): imports straight away and continues in place.
    bridge.init(str(tmp_path / "other-browser"))
    assert json.loads(bridge.import_slot("zero_day", text, False)) == {"slot": "web"}
    view = json.loads(bridge.start("zero_day", "web", False))
    assert view["scene"]["id"] == "gateway_shell"
    assert view["prompt"] == "gateway$"


def test_import_reports_unusable_files():
    assert json.loads(bridge.import_slot("zero_day", "not json {", False)) == {
        "error": "not a Side Channel save file"
    }
    bridge.start("dead_drop", "dd", True)
    other_story = bridge.export_slot("dead_drop", "dd")
    assert json.loads(bridge.import_slot("zero_day", other_story, False)) == {
        "error": "this save is for 'dead_drop', not 'zero_day'"
    }


def test_start_on_a_stale_save_reports_it_instead_of_failing(saves_root):
    bridge.start("zero_day", "old", True)
    slot = saves_root / "zero_day" / "old.json"
    data = json.loads(slot.read_text())
    data["scene_id"] = "cut_scene"
    slot.write_text(json.dumps(data))

    result = json.loads(bridge.start("zero_day", "old", False))
    assert result == {
        "stale": "Can't continue slot 'old': this save is at 'chapter_01:cut_scene', "
        "which the story no longer has."
    }
    assert "scene" in json.loads(bridge.start("zero_day", "old", True))  # restarting works


def test_endings_count_at_an_ending_and_gallery_hides_undiscovered_titles():
    bridge.start("dead_drop", "dd", True)
    assert [e["found"] for e in json.loads(bridge.list_endings("dead_drop"))] == [False] * 4
    view = json.loads(bridge.choose(1))  # "Mara's a grown-up." -> ending_declined
    assert view["scene"]["type"] == "ending"
    assert view["endings"] == {"found": 1, "total": 4}
    assert json.loads(bridge.list_endings("dead_drop")) == [
        {"title": "Not Your Circus", "found": True},
        {"title": None, "found": False},
        {"title": None, "found": False},
        {"title": None, "found": False},
    ]


def test_clear_is_in_the_view():
    bridge.start("zero_day", "web", True)
    play([("choose", 0), ("choose", 0)])
    assert json.loads(bridge.command("clear"))["clear"] is True
    assert json.loads(bridge.command("whoami"))["clear"] is False
