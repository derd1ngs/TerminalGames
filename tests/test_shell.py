from pathlib import Path
from unittest.mock import patch

import yaml

from terminalgames.engine.dialogue import NPC, Topic
from terminalgames.engine.shell import Network, TerminalRunner
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
        }
    }
    path = tmp_path / "network.yaml"
    path.write_text(yaml.safe_dump(data))
    return Network.load(path)


def build_runner(tmp_path: Path, npcs=None) -> TerminalRunner:
    state = GameState(story_id="s", chapter_id="c", scene_id="a")
    network = build_network(tmp_path)
    runner = TerminalRunner(state=state, network=network, npcs=npcs, save_slot_path=tmp_path / "s.json")
    runner.current_chapter, runner.current_scene = "c", "a"
    return runner


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
    wrong = runner.execute("decrypt var/secret.enc 1")
    assert "garbage" in wrong
    assert not runner.state.has_flag("found_secret")

    right = runner.execute("decrypt var/secret.enc 3")
    assert "successful" in right
    assert runner.state.has_flag("found_secret")


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
    new_runner = TerminalRunner(state=restored_state, network=build_network(tmp_path))
    assert new_runner.current_host == "gateway"
    assert new_runner.cwd == "/etc"
    assert "not connected" not in new_runner.execute("cat README")


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


def test_notes_shells_out_to_editor(tmp_path, monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-editor")
    runner = build_runner(tmp_path)
    with patch("terminalgames.engine.shell.subprocess.call") as mock_call:
        mock_call.return_value = 0
        output = runner.execute("notes")
    mock_call.assert_called_once()
    called_editor = mock_call.call_args[0][0][0]
    assert called_editor == "fake-editor"
    assert "fake-editor" in output


def test_notes_falls_back_when_editor_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    runner = build_runner(tmp_path)
    with patch("terminalgames.engine.shell.subprocess.call", side_effect=FileNotFoundError):
        output = runner.execute("notes")
    assert "no editor found" in output
