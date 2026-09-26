from pathlib import Path

import yaml

from terminalgames.engine.dialogue import NPC, Topic
from terminalgames.engine.puzzles import parse_config_text
from terminalgames.engine.shell import Network, TerminalRunner, complete
from terminalgames.engine.state import GameState


def build_network(tmp_path: Path) -> Network:
    data = {
        "hosts": {
            "gateway": {
                "address": "10.0.0.1",
                "banner": "gateway online",
                "on_connect_flag": "connected_gateway",
                "services": {
                    "netmon": {
                        "config_path": "/etc/netmon.conf",
                        "required_config": {"bind_address": "0.0.0.0", "allow_query": "allow"},
                        "on_fix_flag": "netmon_fixed",
                    }
                },
                "filesystem": {
                    "etc": {
                        "type": "dir",
                        "entries": {
                            "netmon.conf": {
                                "type": "config",
                                "values": {"bind_address": "127.0.0.1", "allow_query": "denied"},
                            },
                            "README": {"type": "text", "content": "fix netmon"},
                        },
                    },
                    "var": {
                        "type": "dir",
                        "entries": {
                            "log": {
                                "type": "dir",
                                "entries": {
                                    "access.log": {"type": "text", "content": "line1\nneedle here\nline3"},
                                },
                            },
                            "secret.enc": {
                                "type": "cipher",
                                "cipher": "caesar",
                                "ciphertext": "khoor",
                                "plaintext": "hello",
                                "on_success_flag": "found_secret",
                            },
                        },
                    },
                },
            },
            "vault": {
                "address": "10.0.0.9",
                "banner": "vault",
                "requires_to_connect": {"flag": "netmon_fixed"},
                "filesystem": {},
            },
            "mainframe": {
                "address": "10.0.0.20",
                "logins": {"ops": "hunter2"},
                "on_connect_flag": "on_mainframe",
                "filesystem": {"motd": {"type": "text", "content": "welcome, operator"}},
            },
        }
    }
    path = tmp_path / "network.yaml"
    path.write_text(yaml.safe_dump(data))
    return Network.load(path)


def build_runner(tmp_path: Path, npcs=None) -> TerminalRunner:
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    network = build_network(tmp_path)
    slot_path = tmp_path / "s.json"
    network.materialize(GameState.sandbox_dir_for(slot_path))
    runner = TerminalRunner(state=state, network=network, npcs=npcs, save_slot_path=slot_path)
    runner.current_chapter, runner.current_scene = "c", "a"
    return runner


def test_materialize_writes_real_files_to_disk(tmp_path):
    network = build_network(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    network.materialize(sandbox_root)

    gateway_dir = sandbox_root / "hosts" / "gateway"
    assert parse_config_text((gateway_dir / "etc" / "netmon.conf").read_text()) == {
        "bind_address": "127.0.0.1",
        "allow_query": "denied",
    }
    assert (gateway_dir / "etc" / "README").read_text() == "fix netmon"
    assert (gateway_dir / "var" / "secret.enc").read_text() == "khoor"
    assert (sandbox_root / "hosts" / "vault").is_dir()


def test_materialize_skips_a_host_whose_directory_already_exists(tmp_path):
    """Continuing a save must reuse the sandbox as-is, not clobber whatever
    the player already edited via `set`."""
    network = build_network(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    network.materialize(sandbox_root)
    edited = sandbox_root / "hosts" / "gateway" / "etc" / "README"
    edited.write_text("player was here")

    network.materialize(sandbox_root)

    assert edited.read_text() == "player was here"


def test_set_command_persists_to_the_real_file_on_disk(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    runner.execute("set /etc/netmon.conf bind_address 0.0.0.0")

    real_path = GameState.sandbox_dir_for(tmp_path / "s.json") / "hosts" / "gateway" / "etc" / "netmon.conf"
    assert parse_config_text(real_path.read_text())["bind_address"] == "0.0.0.0"


def test_scan_reports_services(tmp_path):
    runner = build_runner(tmp_path)
    output = runner.execute("scan gateway")
    assert "gateway online" in output
    assert "netmon" in output


def test_connect_sets_flag_and_host(tmp_path):
    runner = build_runner(tmp_path)
    output = runner.execute("connect gateway")
    assert "Connected" in output
    assert runner.current_host == "gateway"
    assert runner.state.has_flag("connected_gateway")


def test_connect_denied_without_required_flag(tmp_path):
    runner = build_runner(tmp_path)
    output = runner.execute("connect vault")
    assert "access denied" in output
    assert runner.current_host is None


def test_commands_require_connection(tmp_path):
    runner = build_runner(tmp_path)
    assert "not connected" in runner.execute("ls")
    assert "not connected" in runner.execute("cat /etc/README")


def test_ls_cd_cat_navigation(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    assert "etc" in runner.execute("ls")
    assert runner.execute("cd etc") == "/etc"
    assert runner.execute("cat README") == "fix netmon"


def test_grep_finds_needle(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    runner.execute("cd var/log")
    assert runner.execute("grep needle access.log") == "needle here"
    assert "no matches" in runner.execute("grep missing access.log")


def test_set_and_systemctl_restart_flow(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    fail_output = runner.execute("systemctl restart netmon")
    assert "failed" in fail_output
    assert not runner.state.has_flag("netmon_fixed")

    runner.execute("set /etc/netmon.conf bind_address 0.0.0.0")
    runner.execute("set /etc/netmon.conf allow_query allow")
    success_output = runner.execute("systemctl restart netmon")
    assert "now active" in success_output
    assert runner.state.has_flag("netmon_fixed")


def test_set_rejects_unknown_key(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    output = runner.execute("set /etc/netmon.conf nonexistent_key value")
    assert "unknown key" in output


def test_decrypt_success_and_failure(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    output_path = GameState.sandbox_dir_for(tmp_path / "s.json") / "hosts" / "gateway" / "var" / "secret.txt"

    wrong = runner.execute("decrypt var/secret.enc 1")
    assert "garbage" in wrong
    assert not runner.state.has_flag("found_secret")
    assert not output_path.exists()

    right = runner.execute("decrypt var/secret.enc 3")
    assert "successful" in right
    assert "hello" not in right  # the plaintext is in the written file, not the message
    assert runner.state.has_flag("found_secret")
    assert output_path.read_text() == "hello"

    # And the real output file behaves like any other real text file.
    assert runner.execute("cat var/secret.txt") == "hello"


def test_unknown_command_reports_not_found(tmp_path):
    runner = build_runner(tmp_path)
    assert "command not found" in runner.execute("frobnicate")


def test_connection_state_survives_save_and_continue(tmp_path):
    """Regression: current_host/cwd must live on GameState, not just the
    TerminalRunner instance, or continuing a save mid-terminal-scene drops
    the connection a scene like gateway_shell silently depends on."""
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    runner.execute("cd etc")
    slot_path = tmp_path / "slot.json"
    runner.state.save(slot_path)

    restored_state = GameState.load(slot_path)
    # Same save_slot_path as the original runner (from build_runner), not the
    # state-only "slot.json" above -- that's what makes this new runner reach
    # the same already-materialized sandbox directory.
    new_runner = TerminalRunner(
        state=restored_state, network=build_network(tmp_path), save_slot_path=tmp_path / "s.json"
    )
    assert new_runner.current_host == "gateway"
    assert new_runner.cwd == "/etc"
    assert new_runner.execute("cat README") == "fix netmon"


def test_service_running_state_survives_save_and_continue(tmp_path):
    """Regression: a fixed service used to report 'failed' again after
    save/continue, because `running` lived on the (reloaded) Network."""
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    runner.execute("set /etc/netmon.conf bind_address 0.0.0.0")
    runner.execute("set /etc/netmon.conf allow_query allow")
    runner.execute("systemctl restart netmon")
    slot_path = tmp_path / "slot.json"
    runner.state.save(slot_path)

    new_runner = TerminalRunner(
        state=GameState.load(slot_path), network=build_network(tmp_path), save_slot_path=tmp_path / "s.json"
    )
    assert "active (running)" in new_runner.execute("systemctl status netmon")


def test_failed_restart_marks_service_not_running(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    runner.execute("set /etc/netmon.conf bind_address 0.0.0.0")
    runner.execute("set /etc/netmon.conf allow_query allow")
    runner.execute("systemctl restart netmon")
    runner.execute("set /etc/netmon.conf allow_query denied")
    runner.execute("systemctl restart netmon")
    assert "failed" in runner.execute("systemctl status netmon")


def test_quoted_arguments_can_contain_spaces(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    assert runner.execute('grep "needle here" /var/log/access.log') == "needle here"


def test_unclosed_quote_reports_parse_error(tmp_path):
    runner = build_runner(tmp_path)
    assert runner.execute('grep "needle /var/log/access.log').startswith("parse error:")


def test_help_shows_usage_for_one_command(tmp_path):
    runner = build_runner(tmp_path)
    assert runner.execute("help decrypt") == "usage: decrypt <file> <key>"
    assert "no such command" in runner.execute("help frobnicate")


def test_complete_command_names(tmp_path):
    runner = build_runner(tmp_path)
    assert complete(runner, "con") == ["connect"]
    assert complete(runner, "c") == ["cat", "cd", "chat", "connect"]


def test_complete_hosts_and_services(tmp_path):
    runner = build_runner(tmp_path)
    assert complete(runner, "connect ") == ["gateway", "mainframe", "vault"]
    runner.execute("connect gateway")
    assert complete(runner, "systemctl re") == ["restart"]
    assert complete(runner, "systemctl restart ") == ["netmon"]


def test_complete_paths_relative_and_absolute(tmp_path):
    runner = build_runner(tmp_path)
    assert complete(runner, "cat ") == []  # not connected: no filesystem
    runner.execute("connect gateway")
    assert complete(runner, "cd ") == ["etc/", "var/"]
    assert complete(runner, "cat /etc/ne") == ["/etc/netmon.conf"]
    runner.execute("cd var")
    assert complete(runner, "cat log/") == ["log/access.log"]
    assert complete(runner, "grep needle s") == ["secret.enc"]
    assert complete(runner, "cat nowhere/x") == []


def test_chat_lists_and_asks_topics(tmp_path):
    npc = NPC(
        id="ghost",
        name="GHOST",
        channel="chat",
        topics={"hello": Topic(id="hello", prompt="say hi", response="Hi there.")},
    )
    runner = build_runner(tmp_path, npcs={"ghost": npc})
    listing = runner.execute("chat ghost")
    assert "hello" in listing
    reply = runner.execute("chat ghost hello")
    assert "Hi there." in reply


def test_mail_send_and_read(tmp_path):
    npc = NPC(
        id="handler",
        name="Handler",
        channel="email",
        email_delay_scenes=0,
        topics={"status": Topic(id="status", prompt="status?", response="All clear.")},
    )
    runner = build_runner(tmp_path, npcs={"handler": npc})
    assert "(no mail)" in runner.execute("mail")
    runner.execute("mail send handler status")
    inbox = runner.execute("mail")
    assert "status?" in inbox
    msg_id = inbox.split("[")[1].split("]")[0]
    read = runner.execute(f"mail read {msg_id}")
    assert "All clear." in read


def _draft_dir(tmp_path) -> Path:
    draft_dir = GameState.sandbox_dir_for(tmp_path / "s.json") / "mail" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    return draft_dir


def _handler_npc(ask_limit=None) -> NPC:
    return NPC(
        id="handler",
        name="Handler",
        channel="email",
        ask_limit=ask_limit,
        email_delay_scenes=0,
        topics={
            "status": Topic(
                id="status",
                prompt="status?",
                response="All clear.",
                sets={"asked_status": True},
                outbox_match={"subject_contains": "status"},
            )
        },
    )


def test_mail_sync_matches_draft_and_moves_to_sent(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc()})
    draft = _draft_dir(tmp_path) / "to_handler.txt"
    draft.write_text("To: handler\nSubject: quick status check\n\nWhat's going on?")

    result = runner.execute("mail sync")

    assert "Sent: to_handler.txt -> Handler" in result
    assert not draft.exists()
    sent_path = draft.parent.parent / "sent" / "to_handler.txt"
    assert sent_path.exists()
    assert runner.state.has_flag("asked_status")


def test_mail_sync_bounces_unknown_contact(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc()})
    draft = _draft_dir(tmp_path) / "to_nobody.txt"
    draft.write_text("To: nobody\nSubject: status\n\nHello?")

    result = runner.execute("mail sync")

    assert "Bounced: to_nobody.txt" in result
    bounced = draft.with_name("to_nobody.txt.bounced")
    assert bounced.exists()
    assert bounced.read_text().startswith("[bounced]")
    assert "unknown contact 'nobody'" in bounced.read_text()


def test_mail_sync_bounces_when_no_topic_matches_subject(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc()})
    draft = _draft_dir(tmp_path) / "to_handler.txt"
    draft.write_text("To: handler\nSubject: completely unrelated topic\n\nHi.")

    result = runner.execute("mail sync")

    assert "Bounced: to_handler.txt -> Handler doesn't recognize" in result
    assert draft.with_name("to_handler.txt.bounced").exists()


def test_mail_sync_bounces_when_ask_limit_exceeded(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc(ask_limit=0)})
    draft = _draft_dir(tmp_path) / "to_handler.txt"
    draft.write_text("To: handler\nSubject: status update please\n\nHi.")

    result = runner.execute("mail sync")

    assert "Bounced: to_handler.txt -> Handler isn't responding anymore for now." in result


def test_mail_sync_with_no_drafts_reports_nothing_to_send(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc()})
    assert runner.execute("mail sync") == "mail sync: no drafts to send."


def test_mail_sync_does_not_reprocess_a_bounced_draft(tmp_path):
    runner = build_runner(tmp_path, npcs={"handler": _handler_npc()})
    draft = _draft_dir(tmp_path) / "to_nobody.txt"
    draft.write_text("To: nobody\nSubject: status\n\nHello?")
    runner.execute("mail sync")

    assert runner.execute("mail sync") == "mail sync: no drafts to send."


def test_ssh_logs_in_with_the_right_password(tmp_path):
    runner = build_runner(tmp_path)
    assert runner.execute("ssh ops@mainframe") == "ops@mainframe's password:"
    assert runner.prompt == "password:"
    assert runner.execute("hunter2") == "Logged in to mainframe (10.0.0.20) as ops."
    assert runner.prompt == "mainframe$"
    assert runner.state.has_flag("on_mainframe")
    assert runner.execute("whoami") == "ops@mainframe"
    assert runner.execute("cat motd") == "welcome, operator"
    runner.execute("disconnect")
    assert runner.state.current_user is None


def test_ssh_wrong_password_or_user_is_denied_and_prompt_resets(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("ssh ops@mainframe")
    assert runner.execute("letmein") == "Permission denied."
    assert runner.prompt == "local$"
    runner.execute("ssh root@mainframe")
    assert runner.execute("hunter2") == "Permission denied."
    assert runner.current_host is None
    assert not runner.state.has_flag("on_mainframe")


def test_password_line_is_not_parsed_as_a_command(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("ssh ops@mainframe")
    assert runner.execute('"unclosed | quote') == "Permission denied."


def test_connect_refuses_a_login_host_and_ssh_refuses_a_plain_one(tmp_path):
    runner = build_runner(tmp_path)
    assert "use 'ssh <user>@mainframe'" in runner.execute("connect mainframe")
    assert "use 'connect gateway'" in runner.execute("ssh ops@gateway")
    assert runner.execute("ssh ops@nowhere") == "ssh: could not resolve hostname nowhere"
    assert runner.execute("ssh vault").startswith("usage:")


def test_ssh_respects_requires_to_connect(tmp_path):
    runner = build_runner(tmp_path)
    runner.network.hosts["mainframe"].requires_to_connect = {"flag": "netmon_fixed"}
    assert "connection refused" in runner.execute("ssh ops@mainframe")
    assert runner.awaiting_password is None


def test_ssh_user_survives_save_and_continue(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("ssh ops@mainframe")
    runner.execute("hunter2")
    runner.state.save(tmp_path / "slot.json")
    new_runner = TerminalRunner(
        state=GameState.load(tmp_path / "slot.json"),
        network=build_network(tmp_path),
        save_slot_path=tmp_path / "s.json",
    )
    assert new_runner.execute("whoami") == "ops@mainframe"


def test_pipe_into_grep_filters_output(tmp_path):
    runner = build_runner(tmp_path)
    runner.execute("connect gateway")
    assert runner.execute("cat /var/log/access.log | grep needle") == "needle here"
    assert runner.execute('cat /var/log/access.log|grep "line"') == "line1\nline3"
    assert runner.execute("help | grep grep | grep nosuchword") == ""  # stages chain
    assert "whoami" in runner.execute("help | grep whoami")


def test_pipe_rejects_other_targets_and_bad_syntax(tmp_path):
    runner = build_runner(tmp_path)
    assert runner.execute("help | cat x") == "pipe: only 'grep <pattern>' can read from a pipe"
    assert runner.execute("help | grep") == "pipe: only 'grep <pattern>' can read from a pipe"
    assert runner.execute("help |") == "parse error: empty pipeline stage"
    assert runner.execute("help || whoami") == "parse error: unsupported operator '||'"


def test_journal_filters_by_category(tmp_path):
    from terminalgames.engine.journal import JournalEntry

    runner = build_runner(tmp_path)
    assert runner.execute("journal lead") == "No lead entries yet."
    runner.state.journal.add(JournalEntry(id="l1", category="lead", text="follow the money"))
    runner.state.journal.add(JournalEntry(id="t1", category="trace", text="odd login"))
    assert runner.execute("journal lead") == "[lead] follow the money ()"
    assert "odd login" in runner.execute("journal")
    assert runner.execute("journal gossip").startswith("journal: unknown category 'gossip'")
    assert complete(runner, "journal s") == ["suspect"]


def test_status_summarizes_position(tmp_path):
    from terminalgames.engine.journal import JournalEntry

    runner = build_runner(tmp_path)
    assert runner.execute("status") == (
        "Location:    c:a\n"
        "Connection:  not connected\n"
        "Latest lead: none yet\n"
        "Journal:     0 lead, 0 note, 0 suspect, 0 trace"
    )
    runner.execute("connect gateway")
    runner.execute("cd etc")
    runner.state.journal.add(JournalEntry(id="l1", category="lead", text="first"))
    runner.state.journal.add(JournalEntry(id="l2", category="lead", text="second"))
    status = runner.execute("status")
    assert "Connection:  user@gateway:/etc" in status
    assert "Latest lead: second" in status
    assert "2 lead" in status


def test_complete_after_a_pipe_uses_the_last_stage(tmp_path):
    runner = build_runner(tmp_path)
    assert complete(runner, "help | gr") == ["grep"]
