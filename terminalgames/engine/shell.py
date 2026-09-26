"""The fake terminal: virtual filesystem, virtual network of hosts, and the
command dispatcher the player types into during terminal-type scenes.

No `crack`-style instant password break -- the core "breaking into a system"
puzzle is sysadmin-flavored: `cat` a config, diagnose what's wrong, `set` the
right key, `systemctl restart` to apply. Commands can be gated behind
flags/tools so later stories can unlock more of the shell without engine
changes (the "escalating depth" plan).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

import yaml

from .dialogue import (
    NPC,
    DialogueError,
    ask_topic,
    match_outbox_topic,
    materialize_delivered_mail,
    parse_mail_text,
    send_topic_by_email,
)
from .journal import VALID_CATEGORIES
from .puzzles import decode_cipher, grep_lines, parse_config_text, render_config_text, validate_config
from .state import EmailMessage, GameState
from .story import check_requires


class StoryLoadError(Exception):
    pass


class CommandError(Exception):
    pass


# --- virtual filesystem -----------------------------------------------------
#
# A host's filesystem is real: `Network.materialize()` writes the YAML-
# described tree to real files under a per-save-slot sandbox directory, and
# every filesystem command below (ls/cd/cat/grep/set/systemctl/decrypt)
# operates on those real files via `pathlib` -- not an in-memory structure.
# The engine still parses and dispatches every command itself, so this stays
# exactly as safe and deterministic as the old in-memory model; only the
# artifacts backing it became real.


@dataclass
class CipherMeta:
    """What `decrypt` needs to know about an encrypted file that the real
    file's own bytes (just the ciphertext) can't tell it: which cipher, what
    the correct plaintext is, and what flag a correct decrypt sets."""

    cipher: str
    plaintext: str
    on_success_flag: Optional[str] = None


def collect_cipher_meta(fs_data: dict[str, Any], prefix: str = "") -> dict[str, CipherMeta]:
    """Recursively collect `{virtual_path: CipherMeta}` for every cipher-type
    node in a `filesystem:` YAML block, keyed in the same normalized form
    `normalize_path` produces. Also validates node types up front, the same
    way the old in-memory tree-builder did, so a malformed story fails at
    load time rather than the first time a player reaches that host."""
    ciphers: dict[str, CipherMeta] = {}
    for name, node in fs_data.items():
        path = f"{prefix}/{name}"
        node_type = node.get("type", "dir")
        if node_type == "dir":
            ciphers.update(collect_cipher_meta(node.get("entries", {}), path))
        elif node_type == "cipher":
            ciphers[path] = CipherMeta(
                cipher=node.get("cipher", "caesar"),
                plaintext=node.get("plaintext", ""),
                on_success_flag=node.get("on_success_flag"),
            )
        elif node_type not in ("text", "config"):
            raise StoryLoadError(f"Unknown filesystem node type '{node_type}'")
    return ciphers


def materialize_fs_node(fs_data: dict[str, Any], dest: Path) -> None:
    """Write a `filesystem:` YAML block to real files/dirs under `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, node in fs_data.items():
        child = dest / name
        node_type = node.get("type", "dir")
        if node_type == "dir":
            materialize_fs_node(node.get("entries", {}), child)
        elif node_type == "text":
            child.write_text(node.get("content", ""))
        elif node_type == "config":
            child.write_text(render_config_text(dict(node.get("values", {}))))
        elif node_type == "cipher":
            child.write_text(node["ciphertext"])
        else:
            raise StoryLoadError(f"Unknown filesystem node type '{node_type}'")


def resolve_real_path(host_dir: Path, normalized: str) -> Path:
    """Join a normalized virtual path (e.g. "/etc/netmon.conf") onto the
    host's real sandbox directory. `normalize_path` already can't produce a
    path that escapes it, but we're touching real disk now, so this is
    defense-in-depth against that ever changing."""
    real = host_dir.joinpath(*(p for p in normalized.strip("/").split("/") if p))
    if not real.resolve().is_relative_to(host_dir.resolve()):
        raise CommandError(f"path escapes the sandbox: {normalized}")
    return real


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


@dataclass
class Host:
    id: str
    address: str = ""
    banner: str = ""
    requires_to_connect: Optional[dict[str, Any]] = None
    on_connect_flag: Optional[str] = None
    # user -> password; a host with logins is reached with `ssh user@host`, not `connect`
    logins: dict[str, str] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    ciphers: dict[str, CipherMeta] = field(default_factory=dict)
    # Raw `filesystem:` YAML block, kept so `Network.materialize()` can write
    # it to real files later -- decoupled from `load()` since materializing
    # needs a per-save-slot sandbox path that isn't known yet.
    filesystem: dict[str, Any] = field(default_factory=dict)


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
            filesystem = hd.get("filesystem", {})
            hosts[host_id] = Host(
                id=host_id,
                address=hd.get("address", ""),
                banner=hd.get("banner", ""),
                requires_to_connect=hd.get("requires_to_connect"),
                on_connect_flag=hd.get("on_connect_flag"),
                logins={str(user): str(pw) for user, pw in (hd.get("logins") or {}).items()},
                services=services,
                ciphers=collect_cipher_meta(filesystem),
                filesystem=filesystem,
            )
        return cls(hosts=hosts)

    def materialize(self, sandbox_root: Path) -> None:
        """Write each host's filesystem to real files under
        `sandbox_root/hosts/<host_id>/`, once per host -- skips a host whose
        directory already exists, so continuing a save reuses (and doesn't
        clobber) whatever the player already edited via `set`."""
        for host_id, host in self.hosts.items():
            host_dir = sandbox_root / "hosts" / host_id
            if host_dir.exists():
                continue
            materialize_fs_node(host.filesystem, host_dir)


# --- command dispatch ---------------------------------------------------------

COMMANDS: dict[str, Callable[[list[str], "TerminalRunner"], str]] = {}
USAGE: dict[str, str] = {}


def command(*names: str, usage: str = "") -> Callable:
    def decorator(fn: Callable) -> Callable:
        for name in names:
            COMMANDS[name] = fn
            USAGE[name] = usage or name
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
        # (host_id, user) while `ssh` waits for the password line; not saved --
        # an interrupted login just starts over.
        self.awaiting_password: Optional[tuple[str, str]] = None

    @property
    def current_host(self) -> Optional[str]:
        """Delegates to GameState so connection state survives save/continue,
        not just the lifetime of this (per-process) TerminalRunner."""
        return self.state.current_host

    @current_host.setter
    def current_host(self, value: Optional[str]) -> None:
        self.state.current_host = value

    @property
    def prompt(self) -> str:
        """What a frontend shows before the input line. While `ssh` waits for
        a password it's "password:", and frontends should mask the echo."""
        if self.awaiting_password:
            return "password:"
        return f"{self.current_host or 'local'}$"

    @property
    def cwd(self) -> str:
        return self.state.cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        self.state.cwd = value

    @property
    def host(self) -> Optional[Host]:
        return self.network.hosts.get(self.current_host) if self.current_host else None

    @property
    def sandbox_root(self) -> Optional[Path]:
        """Where this slot's real host filesystems live on disk. Derived
        from `save_slot_path` rather than a separate constructor argument."""
        if self.save_slot_path is None:
            return None
        return GameState.sandbox_dir_for(self.save_slot_path)

    def discovered_at(self) -> str:
        return f"{self.current_chapter}:{self.current_scene}"

    def advance_scene(self) -> list[EmailMessage]:
        """Advances the scene counter and, since that's also what drives
        async email delivery timing, materializes any mail that just came
        due into a real inbox file. Returns the newly-delivered messages so
        a caller (the TUI) can notify the player."""
        newly_delivered = self.state.advance_scene()
        if newly_delivered and self.sandbox_root:
            materialize_delivered_mail(newly_delivered, self.sandbox_root / "mail" / "inbox")
        return newly_delivered

    def execute(self, raw: str) -> str:
        if self.awaiting_password:
            return _finish_ssh(self, raw.strip())
        raw = raw.strip()
        if not raw:
            return ""
        try:
            stages = split_pipeline(raw)
        except ValueError as exc:
            return f"parse error: {exc}"
        output = self._run(stages[0])
        for stage in stages[1:]:
            if stage[:1] != ["grep"] or len(stage) != 2:
                return "pipe: only 'grep <pattern>' can read from a pipe"
            output = "\n".join(grep_lines(output, stage[1]))
        return output

    def _run(self, argv: list[str]) -> str:
        if not argv or not argv[0]:
            return ""
        name, *args = argv
        handler = COMMANDS.get(name)
        if handler is None:
            return f"command not found: {name}"
        try:
            return handler(args, self)
        except CommandError as exc:
            return str(exc)


def split_pipeline(raw: str) -> list[list[str]]:
    """Split a command line into pipeline stages (`cat log | grep x`), with
    shell quoting. Raises ValueError on an unclosed quote or an empty stage.
    (A quoted "|" can't be told apart from a pipe -- a known limitation.)"""
    lexer = shlex.shlex(raw, posix=True, punctuation_chars="|")
    lexer.whitespace_split = True
    stages: list[list[str]] = [[]]
    for token in lexer:
        if token == "|":
            stages.append([])
        elif set(token) == {"|"}:
            raise ValueError(f"unsupported operator '{token}'")
        else:
            stages[-1].append(token)
    if len(stages) > 1 and not all(stages):
        raise ValueError("empty pipeline stage")
    return stages


# --- tab completion ------------------------------------------------------------
#
# Pure engine logic (no UI), so any frontend can offer the same completions.

# command -> the argument positions that take a path on the current host
PATH_ARGS: dict[str, set[int]] = {"ls": {1}, "cd": {1}, "cat": {1}, "grep": {2}, "set": {1}, "decrypt": {1}}


def complete(runner: TerminalRunner, line: str) -> list[str]:
    """Sorted candidates for the last (possibly empty) word of `line` --
    command names for the first word, then hosts/paths/services/contacts/
    topics depending on the command. Each candidate is a full replacement
    for that last word; directories end in "/"."""
    words = line.rsplit("|", 1)[-1].lstrip().split(" ")  # complete within the last pipe stage
    prefix = words[-1]
    pool = COMMANDS.keys() if len(words) == 1 else _argument_candidates(runner, words, prefix)
    return sorted(c for c in set(pool) if c.startswith(prefix))


def _argument_candidates(runner: TerminalRunner, words: list[str], prefix: str) -> list[str]:
    cmd, position = words[0], len(words) - 1
    if position in PATH_ARGS.get(cmd, set()):
        return _path_candidates(runner, prefix)
    if cmd == "help" and position == 1:
        return list(COMMANDS)
    if cmd in ("journal", "notebook") and position == 1:
        return list(VALID_CATEGORIES)
    if cmd in ("scan", "connect") and position == 1:
        return list(runner.network.hosts)
    if cmd == "systemctl":
        if position == 1:
            return ["status", "restart"]
        if position == 2 and runner.host:
            return list(runner.host.services)
    if cmd == "chat":
        if position == 1:
            return [npc_id for npc_id, npc in runner.npcs.items() if npc.channel == "chat"]
        npc = runner.npcs.get(words[1])
        if position == 2 and npc:
            return [t.id for t in npc.available_topics(runner.state)]
    if cmd == "mail":
        if position == 1:
            return ["list", "read", "send", "sync"]
        if position == 2 and words[1] == "read":
            return [m.id for m in runner.state.inbox()]
        if position == 2 and words[1] == "send":
            return [npc_id for npc_id, npc in runner.npcs.items() if npc.channel == "email"]
        npc = runner.npcs.get(words[2]) if len(words) > 2 else None
        if position == 3 and words[1] == "send" and npc:
            return [t.id for t in npc.available_topics(runner.state)]
    return []


def _path_candidates(runner: TerminalRunner, prefix: str) -> list[str]:
    if runner.host is None or runner.sandbox_root is None:
        return []
    dir_part = prefix[: prefix.rfind("/") + 1]  # "etc/ne" -> "etc/", "ne" -> ""
    virtual_dir = normalize_path(runner.cwd, dir_part or None)
    real_dir = resolve_real_path(_host_dir(runner, runner.host), virtual_dir)
    if not real_dir.is_dir():
        return []
    return [dir_part + p.name + ("/" if p.is_dir() else "") for p in real_dir.iterdir()]


def _require_host(runner: TerminalRunner) -> Host:
    if runner.host is None:
        raise CommandError("not connected to a remote host. Use 'connect <host>' first.")
    return runner.host


def _host_dir(runner: TerminalRunner, host: Host) -> Path:
    if runner.sandbox_root is None:
        raise CommandError("no active save slot -- can't reach the filesystem.")
    return runner.sandbox_root / "hosts" / host.id


@command("help", usage="help [command]")
def cmd_help(args: list[str], runner: TerminalRunner) -> str:
    if args:
        if args[0] not in USAGE:
            return f"help: no such command '{args[0]}'"
        return f"usage: {USAGE[args[0]]}"
    return "Available commands: " + ", ".join(sorted(COMMANDS.keys())) + "\nType 'help <command>' for usage."


@command("whoami", usage="whoami")
def cmd_whoami(args: list[str], runner: TerminalRunner) -> str:
    user = runner.state.current_user or "user"
    return f"{user}@{runner.host.id}" if runner.host else "user@localhost"


@command("scan", usage="scan <host>")
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


@command("connect", usage="connect <host>")
def cmd_connect(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: connect <host>")
    host_id = args[0]
    host = runner.network.hosts.get(host_id)
    if host is None:
        return f"connect: unknown host '{host_id}'"
    if not check_requires(host.requires_to_connect, runner.state):
        return f"connect: access denied to '{host_id}'"
    if host.logins:
        return f"connect: {host_id} requires a login -- use 'ssh <user>@{host_id}'"
    _log_in(runner, host, user=None)
    return f"Connected to {host_id} ({host.address})."


def _log_in(runner: TerminalRunner, host: Host, user: Optional[str]) -> None:
    runner.current_host = host.id
    runner.state.current_user = user
    runner.cwd = "/"
    if host.on_connect_flag:
        runner.state.set_flag(host.on_connect_flag, True)


@command("ssh", usage="ssh <user>@<host>  (then type the password)")
def cmd_ssh(args: list[str], runner: TerminalRunner) -> str:
    if len(args) != 1 or "@" not in args[0]:
        raise CommandError("usage: ssh <user>@<host>")
    user, _, host_id = args[0].partition("@")
    host = runner.network.hosts.get(host_id)
    if host is None:
        return f"ssh: could not resolve hostname {host_id}"
    if not check_requires(host.requires_to_connect, runner.state):
        return f"ssh: connect to host {host_id}: connection refused"
    if not host.logins:
        return f"ssh: {host_id} doesn't take logins -- use 'connect {host_id}'"
    runner.awaiting_password = (host_id, user)
    return f"{user}@{host_id}'s password:"


def _finish_ssh(runner: TerminalRunner, password: str) -> str:
    assert runner.awaiting_password is not None
    host_id, user = runner.awaiting_password
    runner.awaiting_password = None
    host = runner.network.hosts[host_id]
    if user not in host.logins or host.logins[user] != password:
        return "Permission denied."
    _log_in(runner, host, user)
    return f"Logged in to {host_id} ({host.address}) as {user}."


@command("disconnect", "exit", usage="disconnect")
def cmd_disconnect(args: list[str], runner: TerminalRunner) -> str:
    if runner.current_host is None:
        return "not connected to any host"
    host_id = runner.current_host
    runner.current_host = None
    runner.state.current_user = None
    runner.cwd = "/"
    return f"Disconnected from {host_id}."


@command("ls", usage="ls [path]")
def cmd_ls(args: list[str], runner: TerminalRunner) -> str:
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, args[0] if args else None)
    real_path = resolve_real_path(host_dir, path)
    if not real_path.exists():
        return f"ls: no such path: {path}"
    if not real_path.is_dir():
        return path
    entries = sorted(real_path.iterdir(), key=lambda p: p.name)
    if not entries:
        return "(empty)"
    return "  ".join(p.name + "/" if p.is_dir() else p.name for p in entries)


@command("cd", usage="cd <path>")
def cmd_cd(args: list[str], runner: TerminalRunner) -> str:
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, args[0] if args else "/")
    real_path = resolve_real_path(host_dir, path)
    if not real_path.is_dir():
        return f"cd: not a directory: {path}"
    runner.cwd = path
    return path


@command("cat", usage="cat <file>")
def cmd_cat(args: list[str], runner: TerminalRunner) -> str:
    if not args:
        raise CommandError("usage: cat <file>")
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, args[0])
    real_path = resolve_real_path(host_dir, path)
    if not real_path.exists():
        return f"cat: no such file: {path}"
    if real_path.is_dir():
        return f"cat: {path} is a directory"
    content = real_path.read_text()
    if path in host.ciphers:
        return f"[ENCRYPTED] {content}"
    return content


@command("grep", usage='grep <pattern> <file>  (quote a pattern with spaces: grep "failed login" <file>)')
def cmd_grep(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: grep <pattern> <file>")
    pattern, file_arg = args[0], args[1]
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, file_arg)
    real_path = resolve_real_path(host_dir, path)
    if not real_path.is_file() or path in host.ciphers:
        return f"grep: not a text file: {path}"
    matches = grep_lines(real_path.read_text(), pattern)
    return "\n".join(matches) if matches else f"grep: no matches for '{pattern}'"


@command("set", usage="set <file> <key> <value>")
def cmd_set(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 3:
        raise CommandError("usage: set <file> <key> <value>")
    file_arg, key, value = args[0], args[1], " ".join(args[2:])
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, file_arg)
    real_path = resolve_real_path(host_dir, path)
    if not real_path.is_file() or path in host.ciphers:
        return f"set: not a config file: {path}"
    values = parse_config_text(real_path.read_text())
    if key not in values:
        return f"set: unknown key '{key}' in {path}"
    values[key] = value
    real_path.write_text(render_config_text(values))
    return f"{path}: {key} = {value}"


@command("systemctl", usage="systemctl <status|restart> <service>")
def cmd_systemctl(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: systemctl <status|restart> <service>")
    action, service_id = args[0], args[1]
    host = _require_host(runner)
    service = host.services.get(service_id)
    if service is None:
        return f"systemctl: unknown service '{service_id}'"
    running_key = f"{host.id}/{service_id}"
    if action == "status":
        running = running_key in runner.state.running_services
        return f"{service_id}.service - {'active (running)' if running else 'failed'}"
    if action == "restart":
        host_dir = _host_dir(runner, host)
        real_path = resolve_real_path(host_dir, service.config_path)
        if not real_path.is_file():
            return f"systemctl: config missing for '{service_id}'"
        values = parse_config_text(real_path.read_text())
        ok, reason = validate_config(values, service.required_config)
        if ok:
            runner.state.running_services.add(running_key)
            if service.on_fix_flag:
                runner.state.set_flag(service.on_fix_flag, True)
            return f"Restarting {service_id}.service... done.\n{service_id}.service is now active (running)."
        runner.state.running_services.discard(running_key)
        return f"Restarting {service_id}.service... failed.\n{reason}"
    return f"systemctl: unknown action '{action}'"


@command("decrypt", usage="decrypt <file> <key>")
def cmd_decrypt(args: list[str], runner: TerminalRunner) -> str:
    if len(args) < 2:
        raise CommandError("usage: decrypt <file> <key>")
    file_arg, key = args[0], " ".join(args[1:])
    host = _require_host(runner)
    host_dir = _host_dir(runner, host)
    path = normalize_path(runner.cwd, file_arg)
    meta = host.ciphers.get(path)
    if meta is None:
        return f"decrypt: not an encrypted file: {path}"
    real_path = resolve_real_path(host_dir, path)
    decoded = decode_cipher(meta.cipher, real_path.read_text(), key)
    if decoded.strip().lower() == meta.plaintext.strip().lower():
        if meta.on_success_flag:
            runner.state.set_flag(meta.on_success_flag, True)
        real_path.with_suffix(".txt").write_text(decoded)
        virtual_output_path = str(PurePosixPath(path).with_suffix(".txt"))
        return f"Decryption successful. Wrote {virtual_output_path}."
    return f"Decryption produced garbage:\n{decoded}"


@command("journal", "notebook", usage=f"journal [{'|'.join(sorted(VALID_CATEGORIES))}]")
def cmd_journal(args: list[str], runner: TerminalRunner) -> str:
    journal = runner.state.journal
    if args:
        if args[0] not in VALID_CATEGORIES:
            return f"journal: unknown category '{args[0]}' (one of: {', '.join(sorted(VALID_CATEGORIES))})"
        entries = journal.by_category(args[0])
        if not entries:
            return f"No {args[0]} entries yet."
    else:
        entries = journal.all()
        if not entries:
            return "Journal is empty."
    return "\n".join(f"[{e.category}] {e.text} ({e.discovered_at})" for e in entries)


@command("status", usage="status")
def cmd_status(args: list[str], runner: TerminalRunner) -> str:
    state = runner.state
    if runner.host:
        user = state.current_user or "user"
        connection = f"{user}@{runner.host.id}:{runner.cwd}"
    else:
        connection = "not connected"
    leads = state.journal.by_category("lead")
    counts = ", ".join(f"{state.journal.count(c)} {c}" for c in sorted(VALID_CATEGORIES))
    return "\n".join(
        [
            f"Location:    {runner.discovered_at()}",
            f"Connection:  {connection}",
            f"Latest lead: {leads[-1].text if leads else 'none yet'}",
            f"Journal:     {counts}",
        ]
    )


@command("chat", usage="chat <contact> [topic]")
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


def _mail_sync(runner: TerminalRunner) -> str:
    sandbox_root = runner.sandbox_root
    if sandbox_root is None:
        raise CommandError("no active save slot -- can't reach mail.")
    draft_dir = sandbox_root / "mail" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    drafts = sorted(p for p in draft_dir.iterdir() if p.is_file() and not p.name.endswith(".bounced"))
    if not drafts:
        return "mail sync: no drafts to send."

    results = []
    for draft in drafts:
        to, subject, _ = parse_mail_text(draft.read_text())
        npc = runner.npcs.get(to)
        reason: Optional[str] = None
        if npc is None or npc.channel != "email":
            reason = f"unknown contact '{to}'"
        else:
            topic = match_outbox_topic(npc, subject, runner.state)
            if topic is None:
                reason = f"{npc.name} doesn't recognize what you're asking about."
            else:
                try:
                    send_topic_by_email(npc, topic.id, runner.state, runner.discovered_at())
                except DialogueError as exc:
                    reason = str(exc)
                else:
                    sent_dir = sandbox_root / "mail" / "sent"
                    sent_dir.mkdir(parents=True, exist_ok=True)
                    draft.rename(sent_dir / draft.name)
                    results.append(f"Sent: {draft.name} -> {npc.name}")
                    continue

        original = draft.read_text()
        bounced_path = draft.with_name(draft.name + ".bounced")
        draft.rename(bounced_path)
        bounced_path.write_text(f"[bounced] {reason}\n\n{original}")
        results.append(f"Bounced: {draft.name} -> {reason}")

    return "\n".join(results)


@command("mail", usage="mail [list|read <id>|send <contact> <topic>|sync]")
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
    if args[0] == "sync":
        return _mail_sync(runner)
    return "usage: mail [list|read <id>|send <contact> <topic>|sync]"
