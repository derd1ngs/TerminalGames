# TerminalGames

[![Tests](https://github.com/derd1ngs/TerminalGames/actions/workflows/tests.yml/badge.svg)](https://github.com/derd1ngs/TerminalGames/actions/workflows/tests.yml)

A hacker-themed CLI text adventure engine with a fake terminal, in the spirit
of *Hackers*, *WarGames*, and *23*. Ships with two complete stories:

- **Zero Day** -- a 3-chapter campaign that also doubles as an in-fiction
  tutorial: by the end you'll have used the core shell commands (`help`,
  `whoami`, `scan`, `connect`/`disconnect`, `ls`/`cd`/`cat`/`grep`, `set` +
  `systemctl`, `decrypt` with both cipher types, `journal`, `chat`, and
  `mail`), plus trust-building, gated dialogue, and a branching set of six
  endings.
- **Dead Drop** -- a short one-chapter follow-up built on the newer shell
  features: filtering a years-long log with pipes (`cat ... | grep ... |
  grep ...`), an `ssh` login with a password you have to dig up, a runbook
  you must follow to the letter, `status`/`journal <category>`, and four
  endings gated on how far you trusted the friend who sent you.

## Running it

```bash
./setup.sh   # one-time: creates .venv and installs the package into it
./start.sh   # launches the game (re-run this any time to play)
```

`setup.sh` is safe to re-run (e.g. after pulling changes) -- it reuses the
existing `.venv` and just reinstalls the package. `start.sh` forwards any
arguments straight to `terminalgames`, e.g. `./start.sh zero_day --new`, and
tells you to run `setup.sh` first if `.venv` doesn't exist yet.

If you'd rather manage the virtual environment yourself:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/terminalgames
# or: .venv/bin/python -m terminalgames.main
```

With no arguments, story/save-slot selection is a plain pre-flight prompt. You
can skip it with CLI args instead (via `./start.sh` or `.venv/bin/terminalgames`
directly -- both take the same arguments):

```bash
./start.sh --list                       # list available stories, then exit
./start.sh zero_day --list               # list zero_day's save slots, then exit
./start.sh zero_day                       # launch directly (story dir name or manifest id), slot "default"
./start.sh zero_day --slot speedrun --new # launch a specific, named slot fresh
./start.sh zero_day --slot speedrun --continue # launch that slot, failing if it doesn't exist
```

Each story can have multiple save slots (`saves/<story_id>/<slot>.json`), so
you can run several playthroughs of the same story side by side. Launched
without `--slot`, a direct launch uses the `default` slot; the interactive
picker instead lists existing slots (with their current chapter/scene and
last-saved time) and lets you pick one to continue or name a new one.
(Saves from before slots existed, at the old flat `saves/<story_id>.json`
path, are migrated into the `default` slot automatically.)

Each slot also gets a real sandbox directory on disk
(`saves/<story_id>/<slot>_sandbox/hosts/<host_id>/`) -- every file a story's
`network.yaml` describes for a host is materialized there as an actual file,
and `cat`/`ls`/`grep`/`set`/`systemctl`/`decrypt` read and write those real
files rather than an in-memory simulation. Continuing a slot reuses its
sandbox as-is (so anything you've edited via `set` stays edited); restarting
a slot wipes it back to the story's original files.

The game itself then runs full-screen as a three-pane Textual app
(`terminalgames/tui.py`): a **story pane** (pure narration -- scene text and
choice echoes) with a small **choices pane** underneath it (narrative
scenes -- arrow keys + Enter), and a **terminal pane** filling the rest of
the screen (a command's echo/output log paired with the input line, terminal
scenes).

In-game, at any point during a terminal scene you can type:
- `:save` -- save your progress
- `:quit` / `:exit` -- save and quit
- `Ctrl+S` saves and `Ctrl+Q` saves and quits from anywhere, narrative scenes included

The terminal input behaves like a small shell: `Up`/`Down` walk your command
history, `Tab` completes command names, hosts, paths, services, contacts and
topics (listing the candidates when there's more than one), quotes group
words into one argument (`grep "failed login" /var/log/syslog`), and
`help <command>` prints that command's usage.

The current slot is also autosaved every time you cross into a new chapter,
so a crash or an accidental quit never costs you more than the current
chapter's progress.

Run the test suite with `.venv/bin/pip install -e ".[test]" && .venv/bin/pytest`
(the TUI tests drive the Textual app headlessly via `pytest-asyncio` +
`App.run_test()`, no real terminal needed).

## Playing in a browser

The same game runs in a browser with nothing to install:
**https://derd1ngs.github.io/TerminalGames/** (deployed from `main` by
`.github/workflows/pages.yml`). The unchanged Python engine runs client-side
in [Pyodide](https://pyodide.org) (CPython compiled to WebAssembly); the
first visit downloads about 10 MB, which the browser then caches. Saves are
kept in the browser's IndexedDB, so they're per browser and don't mix with
the terminal version's `saves/`.

It plays like the TUI -- story, choices and terminal panes, Tab completion,
Up/Down history, `Ctrl+S` -- with two browser-specific touches: number keys
pick a choice, and a **Mail** button (in terminal scenes) replaces writing a
draft file by hand for `mail sync`. It writes the draft into the slot's
`mail/draft/` directory and runs `mail sync`, so matching and bouncing follow
exactly the same rules.

To run it locally:

```bash
python3 web/build.py                     # writes _site/
python3 -m http.server -d _site 8000     # then open http://localhost:8000
```

`web/` holds the page (`index.html`, `app.js`, `style.css`) and `build.py`,
which zips only what the browser runs -- `engine/`, `stories/` and
`terminalgames/web_bridge.py`, the JSON facade over `GameSession` that
`app.js` calls.

## How a story is put together

A **story** lives in `terminalgames/stories/<story_id>/` and has:

```
story_dir/
  manifest.yaml     # id, title, start scene, list of chapter files
  chapters/
    chapter_01.yaml # one or more chapters -- a scene graph each
  network.yaml       # (optional) the virtual hosts the fake terminal exposes
  npcs.yaml           # (optional) chat/email contacts
```

A story can be a single short chapter, a few (like Zero Day's three), or many chapters
spanning a long, non-linear investigation -- the engine doesn't distinguish
between the two. Scenes reference each other as `chapter_id:scene_id`, so a
lead planted in chapter 3 can pay off in chapter 12.

### manifest.yaml

```yaml
id: zero_day
title: "Zero Day"
start: "chapter_01:intro"   # must be "chapter_id:scene_id"
chapters:
  - chapter_01.yaml
```

### Chapters and scenes

Each chapter file is a list of scenes:

```yaml
id: chapter_01
scenes:
  - id: intro
    type: narrative        # narrative | terminal | ending
    text: |
      Your story text. Rich markup ([bold]...[/bold]) is supported.
    choices:
      - text: "Do the thing"
        next: some_scene            # same chapter, or "other_chapter:scene_id"
        requires:                    # optional gate
          flag: some_flag
        sets:                        # optional effects on success
          some_flag: true
          trust.ghost: 1             # "trust.<npc_id>" adjusts trust instead of a flag
        logs:                        # optional journal entries
          - id: lead_1
            category: lead           # lead | trace | suspect | note
            text: "What the player learned."
```

`requires` supports: `flag`, `flag_equals: {key, value}`, `tool`,
`journal_has: <entry_id>`, `trust_at_least: {npc, value}`, and the
combinators `all: [...]`, `any: [...]` and `not: {...}`, which nest. Every
key in a block must hold, so a plain block is an implicit `all`:

```yaml
requires:
  flag: found_archive_report
  not: {any: [{flag: reported_t}, {flag: trusted_t}]}
```

Because choices are gated rather than strictly sequential, a **hub scene**
that offers several `requires`-gated leads (looping back to itself or to a
menu scene) is how you build a non-linear, "follow whichever thread you
want" investigation without any special engine support -- see the forward-
looking design note in the project's plan file for the long-form game this
was built to support.

### Terminal scenes

```yaml
  - id: gateway_shell
    type: terminal
    text: "You're in."
    terminal:
      host: gateway        # optional: auto-connect to this host on entry
      win_flag: netmon_fixed   # scene advances once this flag is set
      next: discovery
      logs: [...]           # logged once the scene is solved
```

`win_flag` is set by whichever shell command solves the puzzle (a service's
`on_fix_flag`, a cipher file's `on_success_flag`, a host's `on_connect_flag`
-- see network.yaml below), not by the terminal block itself -- with one
exception: a **procedure puzzle** lists the exact commands to run, in order,
and the scene sets its own `win_flag` once the player's most recent commands
in it match that sequence (whitespace-normalized; any other command in
between breaks the run):

```yaml
    terminal:
      win_flag: db_recovered
      next: aftermath
      ordered_commands:
        - systemctl status replica
        - set /etc/db/replica.conf mode primary
        - systemctl restart replica
```

If `host` is
omitted, the player stays on whatever host they were last connected to --
connection state persists across scenes and across save/continue.

### network.yaml -- the virtual network

```yaml
hosts:
  gateway:
    address: "10.44.0.1"
    banner: "shown by `scan`"
    requires_to_connect:            # optional gate on `connect`/`ssh`
      flag: some_flag
    on_connect_flag: connected_gateway   # optional: set when `connect`/`ssh` succeeds
    logins:                        # optional: reach this host with `ssh user@host` + password
      ops: "hunter2"               #   (and `connect` refuses it)
    services:
      netmon:
        config_path: /etc/netmon/netmon.conf   # must point at a `config` file below
        required_config:
          bind_address: "0.0.0.0"
          allow_query: "allow"
        on_fix_flag: netmon_fixed    # set when `systemctl restart` validates
    filesystem:
      etc:
        type: dir
        entries:
          netmon:
            type: dir
            entries:
              netmon.conf:
                type: config
                values: {bind_address: "127.0.0.1", allow_query: "denied"}
              README:
                type: text
                content: "a hint file"
          secret.enc:
            type: cipher
            cipher: caesar            # caesar | xor
            ciphertext: "..."
            plaintext: "the answer"    # what a correct `decrypt` must produce
            on_success_flag: found_secret
```

A successful `decrypt` writes the plaintext to a real sibling file (same
name, `.txt` suffix -- `secret.enc` -> `secret.txt`) rather than printing it
inline, so the player then `cat`s it like any other file. A wrong key just
prints the garbled result and writes nothing.

Filesystem node types: `dir`, `text`, `config`, `cipher`.

Shell commands available to the player: `help [command]`, `whoami`, `status`,
`scan <host>`, `connect <host>`, `ssh <user>@<host>` (prompts for the
password on the next line), `disconnect`/`exit`, `ls [path]`, `cd <path>`,
`cat <file>`, `grep <pattern> <file>`, `set <file> <key> <value>`,
`systemctl status|restart <service>`, `decrypt <file> <key>`,
`journal`/`notebook [lead|note|suspect|trace]`, `chat <npc> [topic]`,
`mail [list|read <id>|send <npc> <topic>|sync]`. Any command's output can be
piped into `grep` (`cat /var/log/syslog | grep ssh | grep failed`); `grep`
is the only pipe target, and a quoted `"|"` still counts as a pipe.

There's deliberately no `crack`-style instant password break. The
config-edit-and-restart puzzle (`cat` a config, `set` the wrong key, `systemctl
restart`) is the sysadmin-flavored core loop; `grep` a log and `decrypt` a
cipher round out the puzzle types a story can use (see
`terminalgames/engine/puzzles.py` for the underlying, independently reusable
validators), along with `ssh` logins with a password found somewhere in the
story, and `ordered_commands` procedure puzzles.

### npcs.yaml -- chat/email contacts

One shared mechanism for every talkable character (AI advisor, friend,
handler, or antagonist) -- a story can define as many as it wants, including
several distinct AI-advisor personas the player can choose between.

```yaml
npcs:
  - id: ghost
    name: "GHOST"
    channel: chat          # chat (sync) | email (async, delayed reply)
    ask_limit: 5            # optional: max asks before they stop responding
    email_delay_scenes: 2   # email only: scenes before a sent question gets a reply
    topics:
      - id: netmon
        prompt: "ask about netmon"
        response: "The response text."
        requires: {flag: some_flag}   # optional gate, same shape as choices
        sets: {...}                    # optional effects, same shape as choices
        logs: [...]
        reliability: truthful          # truthful | misleading | evasive (informational -- write the response text accordingly)
```

Conversations are **topic-based, not free text** -- the player sees a menu of
currently-available topics (`chat <npc>` with no topic lists them) and picks
one. This keeps every NPC fully scripted and deterministic (no LLM call, no
cost, and it's covered by `tests/test_dialogue.py`) while still supporting an
unreliable advisor (`reliability: misleading`), a friend who needs trust
built up first (`requires: {trust_at_least: {npc: ..., value: ...}}`), and a
slow-to-reply email contact -- all through the same data shape a real LLM-
backed persona could implement later behind the same `ask_topic`/
`send_topic_by_email` call shape, without touching the shell or any existing
NPC's content.

#### Mail as real files

Email works two ways. `mail send <npc> <topic>` is the guided path -- exact
topic id, no filesystem involved, same as `chat`. `mail sync` is the real-file
path: outside the game, in the slot's sandbox directory
(`saves/<story_id>/<slot>_sandbox/mail/draft/`), write a plain text file
with `To:`/`Subject:` headers and a body, e.g.

```
To: t
Subject: cold storage backup passphrase?

Saw in the access log you re-keyed it. Any chance you remember it?
```

then run `mail sync` in-game. It matches the `Subject:` line against any
email topic that declares `outbox_match: {subject_contains: "..."}` (a
loose, case-insensitive keyword match, not an exact topic id -- the player
writes a real message in their own words) on the recipient NPC, subject to
the same `requires`/`ask_limit` gating `chat`/`mail send` already enforce.
A match moves the draft to `mail/sent/`; no match renames it in place with a
`.bounced` suffix and a `[bounced] <reason>` line prepended, so it's always
clear what happened rather than the file just vanishing. Delivered replies
still arrive automatically on the same scene-count delay as always, and now
also land as real files in `mail/inbox/` (`mail list`/`mail read` still work
too, reading from the same underlying state).

### Checking a story for structural bugs

```bash
.venv/bin/python -m terminalgames.tools.check_story zero_day   # one story
.venv/bin/python -m terminalgames.tools.check_story --all      # every shipped story
```

Explores every reachable combination of choices via the real engine (not a
separate reimplementation) to report scenes and endings that can never be
reached, and terminal scenes whose `win_flag` is never actually set by
anything in that story's `network.yaml`/`npcs.yaml` -- the classic typo
between two files that's easy to introduce and hard to spot by reading
either file alone. `tests/test_story_reachability.py` runs this against
every shipped story as part of the normal test suite, so a broken story
fails CI the same way a broken test would. See
`terminalgames/tools/check_story.py`'s module docstring for how the search
works and what it deliberately doesn't model (e.g. an NPC's `ask_limit`
running out isn't factored into reachability).

## Project layout

`terminalgames/engine/` holds the engine modules (`story.py`, `shell.py`,
`dialogue.py`, `journal.py`, `state.py`, `puzzles.py`) -- pure game logic
with no UI dependency -- plus `session.py`, whose `GameSession` is the game
loop itself (choices, commands, scene transitions, mail delivery, autosave)
that any frontend drives (`GameSession.open` starts or continues a save
slot), and `loader.py`, which finds stories and save slots on disk. The
engine needs only PyYAML -- `tests/test_engine_is_ui_free.py` keeps Textual
and Rich out of it. `terminalgames/tui.py` is the split-pane Textual
frontend that renders it; `main.py` is just the pre-flight story/save picker
that hands off to it. `terminalgames/tools/` holds `check_story.py`, the
structural story validator described above. `terminalgames/stories/` holds
the two shipped stories as complete worked content examples:
`story_01_zero_day/` for the core features and `story_02_dead_drop/` for
`ssh` logins, pipes, `ordered_commands` and `requires` combinators.
