# Changelog

## Unreleased

- Stories can belong to a series: manifests take `series`, `part` and `hat`,
  and the story list shows e.g. "The Seven Hats · Part 1 · Black Hat".
  Manifests also take a per-story `version`, since new or revised stories
  don't change the game's version.
- Saves carry a format version (`save_version`), so future versions can
  migrate them. A save from a newer version of the game is refused with a
  clear "update the game" message instead of misbehaving.
- Reaching an ending saves the slot, so it shows the finished run rather than
  an earlier save.
- Story lists show each story's publishing date, a one-line description,
  its size and the endings you've found, newest first. The browser pages
  them six at a time, and the terminal picker nine at a time (`n`/`p`), with
  titles instead of folder names. Story manifests take optional `published`
  and `description` fields.

## 1.0.1 -- 2026-09-27

- The browser version has a favicon: a green prompt and an amber cursor
  (SVG, plus PNGs for older browsers and iOS home screens).
- The Python package is now `sidechannel`, matching the game's name. It was
  `terminalgames`, the project's original name. The `sidechannel` command is
  unchanged, and save files exported from the browser still import.

## 1.0.0 -- 2026-09-27

First release, on PyPI as `sidechannel`: the game is now called **Side
Channel** (it started out as "TerminalGames"). Everything below is new since
the 0.1.0 prototype.

### Stories
- **Zero Day** (3 chapters, 6 endings): the in-fiction tutorial.
- **Dead Drop** (1 chapter, 4 endings): pipes, `ssh`, a runbook puzzle.
- **Night Shift** (1 chapter, 4 endings): hidden files, tool grants and a
  trace meter.
- Every puzzle scene has progressively revealed `hint`s.

### Playing
- A browser version with nothing to install
  (https://derd1ngs.github.io/sidechannel/). It runs the same Python engine
  in Pyodide, works offline after the first visit, has on-screen Tab and
  history buttons on phones, and can export and import save files.
- The terminal version is a full-screen Textual app with Tab completion,
  command history, Ctrl+S, and a `mail compose` form.
- An endings gallery per story, across all save slots.
- Shell: `ssh` logins, pipes into `grep`, `head`/`tail`, `find`, `ls -a`,
  `history`, `clear`, `man`, `status`, `journal <category>`, `hint`.

### Story authoring
- New story features:
  - `requires` combinators (`all`/`any`/`not`);
  - `tool.<id>` grants;
  - `ordered_commands` procedure puzzles;
  - trace meters (`trace: {limit, on_trace}`);
  - per-scene `hints`.
- Strict loading: typos and invalid values in any story file are errors,
  with a did-you-mean hint.
- `check_story` checks every scene and ending is reachable, lints
  references across files, and exports the scene graph as Mermaid
  (`--graph`).

### Fixes
- Installed copies (not just checkouts) now ship the stories and save to
  the user data directory.
- A save whose scene was removed by a story edit now offers a restart
  instead of crashing.
