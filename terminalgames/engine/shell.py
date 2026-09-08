"""The fake terminal: virtual filesystem, virtual network of hosts, and the
command dispatcher the player types into during terminal-type scenes.

No `crack`-style instant password break -- the core "breaking into a system"
puzzle is sysadmin-flavored: `cat` a config, diagnose what's wrong, `set` the
right key, `systemctl restart` to apply. Commands can be gated behind
flags/tools so later stories can unlock more of the shell without engine
changes (the "escalating depth" plan).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

import yaml

from .dialogue import NPC, DialogueError, ask_topic, send_topic_by_email
from .puzzles import decode_cipher, grep_lines, validate_config
from .state import GameState
from .story import check_requires


class StoryLoadError(Exception):
    pass


class CommandError(Exception):
    pass


# --- virtual filesystem -----------------------------------------------------


@dataclass
class Directory:
    entries: dict[str, "FSNode"] = field(default_factory=dict)


@dataclass
class TextFile:
    content: str = ""


@dataclass
class ConfigFile:
    values: dict[str, str] = field(default_factory=dict)


@dataclass
class CipherFile:
    ciphertext: str
    cipher: str
    plaintext: str
    on_success_flag: Optional[str] = None


FSNode = Union[Directory, TextFile, ConfigFile, CipherFile]


def parse_fs_node(data: dict[str, Any]) -> FSNode:
    node_type = data.get("type", "dir")
    if node_type == "dir":
        return Directory({name: parse_fs_node(child) for name, child in data.get("entries", {}).items()})
    if node_type == "text":
        return TextFile(content=data.get("content", ""))
    if node_type == "config":
        return ConfigFile(values=dict(data.get("values", {})))
    if node_type == "cipher":
        return CipherFile(
            ciphertext=data["ciphertext"],
            cipher=data.get("cipher", "caesar"),
            plaintext=data.get("plaintext", ""),
            on_success_flag=data.get("on_success_flag"),
        )
    raise StoryLoadError(f"Unknown filesystem node type '{node_type}'")


def resolve_path(root: Directory, path: str) -> Optional[FSNode]:
    node: FSNode = root
    for part in (p for p in path.strip("/").split("/") if p):
        if not isinstance(node, Directory) or part not in node.entries:
            return None
        node = node.entries[part]
    return node


def normalize_path(cwd: str, arg: Optional[str]) -> str:
    if not arg:
        return cwd
    target = arg if arg.startswith("/") else cwd.rstrip("/") + "/" + arg
    parts: list[str] = []
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


# --- virtual network ---------------------------------------------------------


@dataclass
class Service:
    id: str
    config_path: str
    required_config: dict[str, str]
    on_fix_flag: Optional[str] = None
    running: bool = False


@dataclass
class Host:
    id: str
    address: str = ""
    banner: str = ""
    requires_to_connect: Optional[dict[str, Any]] = None
    on_connect_flag: Optional[str] = None
    services: dict[str, Service] = field(default_factory=dict)
    root: Directory = field(default_factory=Directory)


@dataclass
class Network:
    hosts: dict[str, Host] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Network":
        data = yaml.safe_load(path.read_text()) or {}
        hosts: dict[str, Host] = {}
        for host_id, hd in (data.get("hosts") or {}).items():
            services = {
                svc_id: Service(
                    id=svc_id,
                    config_path=sd["config_path"],
                    required_config=dict(sd.get("required_config", {})),
                    on_fix_flag=sd.get("on_fix_flag"),
                )
                for svc_id, sd in (hd.get("services") or {}).items()
            }
            root = parse_fs_node({"type": "dir", "entries": hd.get("filesystem", {})})
            if not isinstance(root, Directory):
                raise StoryLoadError(f"host '{host_id}' filesystem root did not parse as a directory")
            hosts[host_id] = Host(
                id=host_id,
                address=hd.get("address", ""),
                banner=hd.get("banner", ""),
                requires_to_connect=hd.get("requires_to_connect"),
                on_connect_flag=hd.get("on_connect_flag"),
                services=services,
                root=root,
            )
        return cls(hosts=hosts)


# --- command dispatch ---------------------------------------------------------

COMMANDS: dict[str, Callable[[list[str], "TerminalRunner"], str]] = {}


def command(*names: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        for name in names:
            COMMANDS[name] = fn
        return fn

    return decorator


class TerminalRunner:
    def __init__(
        self,
        state: GameState,
        network: Network,
        npcs: Optional[dict[str, NPC]] = None,
        save_slot_path: Optional[Path] = None,
    ):
        self.state = state
        self.network = network
        self.npcs = npcs or {}
        self.save_slot_path = save_slot_path
        self.current_chapter = ""
        self.current_scene = ""

    @property
    def current_host(self) -> Optional[str]:
        """Delegates to GameState so connection state survives save/continue,
        not just the lifetime of this (per-process) TerminalRunner."""
        return self.state.current_host

    @current_host.setter
    def current_host(self, value: Optional[str]) -> None:
        self.state.current_host = value

    @property
    def cwd(self) -> str:
        return self.state.cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        self.state.cwd = value

    @property
    def host(self) -> Optional[Host]:
        return self.network.hosts.get(self.current_host) if self.current_host else None

    def discovered_at(self) -> str:
        return f"{self.current_chapter}:{self.current_scene}"

    def execute(self, raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return ""
        name, *args = raw.split()
        handler = COMMANDS.get(name)
        if handler is None:
            return f"command not found: {name}"
        try:
            return handler(args, self)
        except CommandError as exc:
            return str(exc)


def _require_host(runner: TerminalRunner) -> Host:
    if runner.host is None:
        raise CommandError("not connected to a remote host. Use 'connect <host>' first.")
    return runner.host


@command("help")
def cmd_help(args: list[str], runner: TerminalRunner) -> str:
    return "Available commands: " + ", ".join(sorted(COMMANDS.keys()))


@command("whoami")
def cmd_whoami(args: list[str], runner: TerminalRunner) -> str:
    return f"user@{runner.host.id}" if runner.host else "user@localhost"


@command("scan")
def cmd_scan(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: scan <host>")
    host = runner.network.hosts.get(args[0])
    if host is None:
        return f"scan: no response from '{args[0]}'"
    lines = [f"Host {args[0]} ({host.address}) is up."]
    if host.banner:
        lines.append(host.banner)
    if host.services:
        lines.append("Open services: " + ", ".join(sorted(host.services.keys())))
    return "\n".join(lines)


@command("connect")
def cmd_connect(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: connect <host>")
    host_id = args[0]
    host = runner.network.hosts.get(host_id)
    if host is None:
        return f"connect: unknown host '{host_id}'"
    if not check_requires(host.requires_to_connect, runner.state):
        return f"connect: access denied to '{host_id}'"
    runner.current_host = host_id
    runner.cwd = "/"
    if host.on_connect_flag:
        runner.state.set_flag(host.on_connect_flag, True)
    return f"Connected to {host_id} ({host.address})."


@command("disconnect", "exit")
def cmd_disconnect(args: list[str], runner: TerminalRunner) -> str:
    if runner.current_host is None:
        return "not connected to any host"
    host_id = runner.current_host
    runner.current_host = None
    runner.cwd = "/"
    return f"Disconnected from {host_id}."


@command("ls")
def cmd_ls(args: list[str], runner: TerminalRunner) -> str:
    host = _require_host(runner)
    path = normalize_path(runner.cwd, args[0] if args else None)
    node = resolve_path(host.root, path)
    if node is None:
        return f"ls: no such path: {path}"
    if not isinstance(node, Directory):
        return path
    if not node.entries:
        return "(empty)"
    return "  ".join(
        name + "/" if isinstance(child, Directory) else name for name, child in sorted(node.entries.items())
    )


@command("cd")
def cmd_cd(args: list[str], runner: TerminalRunner) -> str:
    host = _require_host(runner)
    path = normalize_path(runner.cwd, args[0] if args else "/")
    node = resolve_path(host.root, path)
    if not isinstance(node, Directory):
        return f"cd: not a directory: {path}"
    runner.cwd = path
    return path


@command("cat")
def cmd_cat(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: cat <file>")
    host = _require_host(runner)
    path = normalize_path(runner.cwd, args[0])
    node = resolve_path(host.root, path)
    if node is None:
        return f"cat: no such file: {path}"
    if isinstance(node, Directory):
        return f"cat: {path} is a directory"
    if isinstance(node, TextFile):
        return node.content
    if isinstance(node, ConfigFile):
        return "\n".join(f"{k}={v}" for k, v in node.values.items())
    if isinstance(node, CipherFile):
        return f"[ENCRYPTED] {node.ciphertext}"
    return "cat: unreadable file"


@command("grep")
def cmd_grep(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: grep <pattern> <file>")
    pattern, file_arg = args[0], args[1]
    host = _require_host(runner)
    path = normalize_path(runner.cwd, file_arg)
    node = resolve_path(host.root, path)
    if not isinstance(node, TextFile):
        return f"grep: not a text file: {path}"
    matches = grep_lines(node.content, pattern)
    return "\n".join(matches) if matches else f"grep: no matches for '{pattern}'"


@command("set")
def cmd_set(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 3:
        raise CommandError("usage: set <file> <key> <value>")
    file_arg, key, value = args[0], args[1], " ".join(args[2:])
    host = _require_host(runner)
    path = normalize_path(runner.cwd, file_arg)
    node = resolve_path(host.root, path)
    if not isinstance(node, ConfigFile):
        return f"set: not a config file: {path}"
    if key not in node.values:
        return f"set: unknown key '{key}' in {path}"
    node.values[key] = value
    return f"{path}: {key} = {value}"


@command("systemctl")
def cmd_systemctl(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: systemctl <status|restart> <service>")
    action, service_id = args[0], args[1]
    host = _require_host(runner)
    service = host.services.get(service_id)
    if service is None:
        return f"systemctl: unknown service '{service_id}'"
    if action == "status":
        return f"{service_id}.service - {'active (running)' if service.running else 'failed'}"
    if action == "restart":
        node = resolve_path(host.root, service.config_path)
        if not isinstance(node, ConfigFile):
            return f"systemctl: config missing for '{service_id}'"
        ok, reason = validate_config(node.values, service.required_config)
        service.running = ok
        if ok:
            if service.on_fix_flag:
                runner.state.set_flag(service.on_fix_flag, True)
            return f"Restarting {service_id}.service... done.\n{service_id}.service is now active (running)."
        return f"Restarting {service_id}.service... failed.\n{reason}"
    return f"systemctl: unknown action '{action}'"


@command("decrypt")
def cmd_decrypt(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: decrypt <file> <key>")
    file_arg, key = args[0], " ".join(args[1:])
    host = _require_host(runner)
    path = normalize_path(runner.cwd, file_arg)
    node = resolve_path(host.root, path)
    if not isinstance(node, CipherFile):
        return f"decrypt: not an encrypted file: {path}"
    decoded = decode_cipher(node.cipher, node.ciphertext, key)
    if decoded.strip().lower() == node.plaintext.strip().lower():
        if node.on_success_flag:
            runner.state.set_flag(node.on_success_flag, True)
        return f"Decryption successful:\n{decoded}"
    return f"Decryption produced garbage:\n{decoded}"


@command("journal", "notebook")
def cmd_journal(args: list[str], runner: TerminalRunner) -> str:
    entries = runner.state.journal.all()
    if not entries:
        return "Journal is empty."
    return "\n".join(f"[{e.category}] {e.text} ({e.discovered_at})" for e in entries)


@command("chat")
def cmd_chat(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: chat <contact> [topic]")
    npc = runner.npcs.get(args[0])
    if npc is None:
        return f"chat: unknown contact '{args[0]}'"
    if npc.channel != "chat":
        return f"chat: {npc.name} can only be reached by email"
    if len(args) == 1:
        topics = npc.available_topics(runner.state)
        if not topics:
            return f"{npc.name} has nothing to discuss right now."
        lines = [f"Topics you can ask {npc.name} about:"]
        lines += [f"  {t.id} - {t.prompt}" for t in topics]
        return "\n".join(lines)
    try:
        response = ask_topic(npc, args[1], runner.state, runner.discovered_at())
    except DialogueError as exc:
        return str(exc)
    return f"{npc.name}: {response}"


@command("mail")
def cmd_mail(args: list[str], runner: TerminalRunner) -> str:
    if not args or args[0] == "list":
        inbox = runner.state.inbox()
        return "\n".join(f"[{m.id}] {m.subject}" for m in inbox) if inbox else "(no mail)"
    if args[0] == "read":
        if len(args) < 2:
            raise CommandError("usage: mail read <id>")
        msg = next((m for m in runner.state.inbox() if m.id == args[1]), None)
        if msg is None:
            return f"mail: no such message '{args[1]}'"
        return f"From: {msg.npc_id}\nSubject: {msg.subject}\n\n{msg.body}"
    if args[0] == "send":
        if len(args) < 3:
            raise CommandError("usage: mail send <contact> <topic>")
        npc = runner.npcs.get(args[1])
        if npc is None or npc.channel != "email":
            return f"mail: unknown contact '{args[1]}'"
        try:
            send_topic_by_email(npc, args[2], runner.state, runner.discovered_at())
        except DialogueError as exc:
            return str(exc)
        return f"Message sent to {npc.name}. Expect a reply later."
    return "usage: mail [list|read <id>|send <contact> <topic>]"


@command("notes")
def cmd_notes(args: list[str], runner: TerminalRunner) -> str:
    if runner.save_slot_path is None:
        return "notes: no active save slot"
    notes_path = GameState.notes_path_for(runner.save_slot_path)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    if not notes_path.exists():
        notes_path.write_text("")
    candidates = [os.environ.get("VISUAL"), os.environ.get("EDITOR"), "nano", "vi"]
    for editor in candidates:
        if not editor:
            continue
        try:
            subprocess.call([editor, str(notes_path)])
            return f"(closed notes editor: {editor})"
        except FileNotFoundError:
            continue
    return "notes: no editor found (set $EDITOR)"
