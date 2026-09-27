// Browser frontend for Side Channel. The game itself is the unchanged
// Python engine, run in Pyodide; this file only renders the JSON views
// sidechannel/web_bridge.py returns and forwards the player's input.
// Saves live in IndexedDB (Pyodide's IDBFS mounted at /saves).

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const BUILD_ID = "__BUILD_ID__"; // replaced by web/build.py with the zip's content hash
const SAVES = "/saves";

const $ = (id) => document.getElementById(id);
let py = null;
let bridge = null;
let currentStory = null; // {ref, id, title}
let mode = "menu"; // menu | narrative | terminal | ended
let secret = false; // the next terminal line is a password (after `ssh`)
const history = [];
let historyIndex = 0;

// --- boot ----------------------------------------------------------------------

function bootLine(text, cls) {
  const line = document.createElement("span");
  line.textContent = text + "\n";
  if (cls) line.className = cls;
  $("boot-log").append(line);
}

async function boot() {
  try {
    bootLine("> loading python runtime (first visit downloads ~10 MB) ...");
    const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
    py = await loadPyodide({ indexURL: PYODIDE_URL });
    bootLine("> loading pyyaml ...");
    await py.loadPackage("pyyaml");
    bootLine("> loading game engine ...");
    const response = await fetch(`sidechannel.zip?v=${BUILD_ID}`);
    if (!response.ok) throw new Error(`sidechannel.zip: HTTP ${response.status}`);
    py.unpackArchive(await response.arrayBuffer(), "zip", { extractDir: "/app" });
    py.runPython("import sys; sys.path.insert(0, '/app')");
    bootLine("> mounting save storage ...");
    py.FS.mkdirTree(SAVES);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, SAVES);
    await syncSaves(true);
    bridge = py.pyimport("sidechannel.web_bridge");
    bridge.init(SAVES);
    bootLine("> ready.");
    showStoryMenu();
    setUpOffline();
  } catch (err) {
    bootLine(`boot failed: ${err.message || err}`, "fail");
    bootLine("Reload to try again.", "fail");
    console.error(err);
  }
}

// Offline play (see sw.js): register the service worker, then hand it the
// Pyodide files this page loaded -- on a first visit they were fetched before
// the worker existed, so it couldn't have cached them itself.
async function setUpOffline() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("sw.js");
    const registration = await navigator.serviceWorker.ready;
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.offlineReady) $("offline-status").hidden = false;
    });
    const urls = performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .filter((url) => url.startsWith(PYODIDE_URL));
    registration.active.postMessage({ cachePyodide: urls });
  } catch (err) {
    console.warn("offline play unavailable:", err);
  }
}

// IDBFS syncs are async and must not overlap, so they're chained.
let syncChain = Promise.resolve();
function syncSaves(populate = false) {
  syncChain = syncChain.then(
    () =>
      new Promise((resolve) =>
        py.FS.syncfs(populate, (err) => {
          if (err) console.error("save sync failed", err);
          resolve();
        }),
      ),
  );
  return syncChain;
}

function call(fn, ...args) {
  return JSON.parse(bridge[fn](...args));
}

// --- screens -------------------------------------------------------------------

function showScreen(name) {
  for (const s of ["boot", "menu", "game"]) $(`screen-${s}`).hidden = s !== name;
  $("game-actions").hidden = name !== "game";
  if (name !== "game") $("game-title").textContent = "";
}

function showStoryMenu() {
  mode = "menu";
  showScreen("menu");
  $("menu-stories").hidden = false;
  $("menu-slots").hidden = true;
  const list = $("story-list");
  list.replaceChildren();
  for (const story of call("list_stories")) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = story.title;
    button.addEventListener("click", () => showSlotMenu(story));
    list.append(li(button));
  }
  list.querySelector("button")?.focus();
}

function showSlotMenu(story) {
  currentStory = story;
  mode = "menu";
  showScreen("menu");
  $("menu-stories").hidden = true;
  $("menu-slots").hidden = false;
  $("slots-heading").textContent = story.title;
  renderEndings(call("list_endings", story.ref));
  const slots = call("list_slots", story.ref);
  const list = $("slot-list");
  list.replaceChildren();
  for (const { slot, summary } of slots) {
    const row = document.createElement("div");
    row.className = "slot-row";
    const cont = document.createElement("button");
    cont.type = "button";
    cont.innerHTML = `Continue <strong></strong><span class="sub"></span>`;
    cont.querySelector("strong").textContent = slot;
    cont.querySelector(".sub").textContent = summary.replace(`${slot} -- `, "");
    cont.addEventListener("click", () => startGame(slot, false));
    const restart = document.createElement("button");
    restart.type = "button";
    restart.textContent = "Restart";
    restart.addEventListener("click", () => {
      if (confirm(`Restart slot "${slot}" from the beginning? Its progress will be lost.`)) startGame(slot, true);
    });
    const exportButton = document.createElement("button");
    exportButton.type = "button";
    exportButton.textContent = "Export";
    exportButton.title = `Download slot "${slot}" as a save file`;
    exportButton.addEventListener("click", () => downloadSlot(slot));
    row.append(cont, restart, exportButton);
    list.append(li(row));
  }
  showSlotMessage("");
  const names = new Set(slots.map((s) => s.slot));
  let suggestion = "default";
  for (let n = 2; names.has(suggestion); n++) suggestion = `run${n}`;
  $("new-slot-name").value = suggestion;
  (list.querySelector("button") || $("new-slot-name")).focus();
}

function renderEndings(endings) {
  const found = endings.filter((e) => e.found).length;
  $("endings-gallery").hidden = found === 0;
  $("endings-count").textContent = `Endings found: ${found}/${endings.length}`;
  const list = $("endings-list");
  list.replaceChildren();
  for (const ending of endings) {
    const item = document.createElement("li");
    item.textContent = ending.found ? ending.title : "???";
    item.className = ending.found ? "found" : "hidden-ending";
    list.append(item);
  }
}

function li(child) {
  const item = document.createElement("li");
  item.append(child);
  return item;
}

$("new-slot-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const slot = $("new-slot-name").value.trim();
  const exists = call("list_slots", currentStory.ref).some((s) => s.slot === slot);
  if (exists && !confirm(`Slot "${slot}" already exists. Overwrite it with a new game?`)) return;
  startGame(slot, true);
});
$("btn-back-stories").addEventListener("click", showStoryMenu);

// --- save files ------------------------------------------------------------------

function showSlotMessage(text, cls = "") {
  $("slot-message").textContent = text;
  $("slot-message").className = `menu-message ${cls}`;
}

function downloadSlot(slot) {
  const text = bridge.export_slot(currentStory.ref, slot);
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `sidechannel-${currentStory.id}-${slot}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("btn-import").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = ""; // so picking the same file again still fires `change`
  if (!file) return;
  const text = await file.text();
  let result = call("import_slot", currentStory.ref, text, false);
  if (result.exists) {
    if (!confirm(`Slot "${result.exists}" already exists here. Replace it with the imported save?`)) return;
    result = call("import_slot", currentStory.ref, text, true);
  }
  if (result.error) {
    showSlotMessage(`Import failed: ${result.error}`, "error");
    return;
  }
  await syncSaves();
  showSlotMenu(currentStory);
  showSlotMessage(`Imported slot "${result.slot}".`);
});

// --- game ------------------------------------------------------------------------

function startGame(slot, fresh) {
  $("story-log").replaceChildren();
  $("terminal-log").replaceChildren();
  history.length = 0;
  historyIndex = 0;
  const view = call("start", currentStory.ref, slot, fresh);
  if (view.stale) {
    if (confirm(`${view.stale}\n\nRestart this slot from the beginning?`)) startGame(slot, true);
    else showSlotMessage(view.stale, "error");
    return;
  }
  $("btn-mail").hidden = call("email_contacts").length === 0;
  showScreen("game");
  $("game-title").textContent = `${view.title} [${slot}]`;
  render(view);
  syncSaves();
}

function append(logId, text, cls, html = false) {
  const el = document.createElement("p");
  if (html) el.innerHTML = text;
  else el.textContent = text;
  if (cls) el.className = cls;
  const log = $(logId);
  log.append(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

// Showing or hiding the choices pane resizes the logs after text was
// appended, so re-pin both to the bottom once the layout has settled.
function scrollLogsToEnd() {
  requestAnimationFrame(() => {
    for (const id of ["story-log", "terminal-log"]) $(id).scrollTop = $(id).scrollHeight;
  });
}

function render(view) {
  scrollLogsToEnd();
  if (view.clear) $("terminal-log").replaceChildren();
  if (view.output) append("terminal-log", view.output, view.output === "Saved." ? "saved" : "");
  for (const notice of view.notices) append("story-log", notice, "notice");
  $("prompt").textContent = view.prompt;
  secret = view.secret;
  $("command-input").type = secret ? "password" : "text";
  updateKeyButtons();
  if (!view.entered) return;

  const scene = view.scene;
  const game = $("screen-game");
  game.classList.remove("narrative", "terminal-mode", "ended");
  if (scene.type === "ending") {
    mode = "ended";
    game.classList.add("ended", "narrative");
    append("story-log", scene.html, "ending", true);
    append("story-log", `-- THE END (${scene.id}) --`, "the-end");
    append("story-log", `Endings found: ${view.endings.found}/${view.endings.total}`, "notice");
    renderChoices([]);
    const back = document.createElement("button");
    back.type = "button";
    back.textContent = "Back to menu";
    back.addEventListener("click", () => showSlotMenu(currentStory));
    $("choice-list").append(li(back));
    $("choices-pane").hidden = false;
    setTerminalActive(false);
    back.focus();
    return;
  }
  append("story-log", scene.html, "", true);
  if (scene.type === "terminal") {
    mode = "terminal";
    game.classList.add("terminal-mode");
    $("choices-pane").hidden = true;
    setTerminalActive(true);
    $("command-input").focus();
  } else {
    mode = "narrative";
    game.classList.add("narrative");
    $("choices-pane").hidden = false;
    renderChoices(view.choices);
    setTerminalActive(false);
    $("choice-list").querySelector("button")?.focus();
  }
}

function renderChoices(choices) {
  const list = $("choice-list");
  list.replaceChildren();
  choices.forEach((text, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.innerHTML = `<span class="key">${index + 1}.</span><span></span>`;
    button.lastChild.textContent = text;
    button.addEventListener("click", () => choose(index, text));
    list.append(li(button));
  });
}

function setTerminalActive(active) {
  $("terminal-pane").classList.toggle("inactive", !active);
  $("command-input").disabled = !active;
  $("btn-mail").disabled = !active; // mail goes out via `mail sync`, a terminal command
  updateKeyButtons();
}

function updateKeyButtons() {
  const off = $("command-input").disabled || secret; // no completion or history for passwords
  for (const button of $("key-buttons").children) button.disabled = off;
}

function choose(index, text) {
  if (mode !== "narrative") return;
  append("story-log", `> ${text}`, "echo");
  act(() => call("choose", index));
}

// Runs a bridge call that changes the game, renders its view and persists.
function act(fn) {
  try {
    render(fn());
  } catch (err) {
    append(mode === "terminal" ? "terminal-log" : "story-log", `internal error: ${err.message || err}`, "error");
    console.error(err);
  }
  syncSaves();
}

// --- terminal input ------------------------------------------------------------------

$("prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("command-input");
  const raw = input.value.trim();
  input.value = "";
  if (!raw || mode !== "terminal") return;
  if (secret) {
    // A password line: masked, kept out of history, never a meta command.
    append("terminal-log", `${$("prompt").textContent} ********`, "cmd-echo");
    act(() => call("command", raw));
    return;
  }
  history.push(raw);
  historyIndex = history.length;
  append("terminal-log", `${$("prompt").textContent} ${raw}`, "cmd-echo");
  if (raw === ":quit" || raw === ":exit") {
    saveAndExit();
    return;
  }
  if (raw.split(/\s+/).join(" ") === "mail compose" && !$("btn-mail").hidden) {
    $("btn-mail").click(); // the same dialog as the Mail button
    return;
  }
  act(() => call("command", raw));
});

$("command-input").addEventListener("keydown", (event) => {
  const input = event.target;
  if (secret) {
    if (event.key === "Tab") event.preventDefault(); // no completion (or focus jump) for passwords
    return;
  }
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    if (!history.length) return;
    event.preventDefault();
    stepHistory(input, event.key === "ArrowUp" ? -1 : 1);
  } else if (event.key === "Tab" && !event.shiftKey) {
    event.preventDefault();
    completeInput(input);
  }
});

function stepHistory(input, step) {
  if (!history.length) return;
  historyIndex = Math.max(0, Math.min(history.length, historyIndex + step));
  input.value = history[historyIndex] ?? "";
}

// The on-screen key buttons act like the real keys. They must not take focus
// from the input -- on a phone that would close the keyboard after every tap.
for (const button of $("key-buttons").children) {
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", () => {
    const input = $("command-input");
    if (input.disabled || secret) return;
    if (button.dataset.key === "Tab") completeInput(input);
    else stepHistory(input, button.dataset.key === "ArrowUp" ? -1 : 1);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
}

function completeInput(input) {
  const line = input.value;
  const candidates = call("complete", line);
  if (!candidates.length) return;
  const prefix = line.split(" ").at(-1);
  let common = candidates.reduce((a, b) => {
    let i = 0;
    while (i < a.length && a[i] === b[i]) i++;
    return a.slice(0, i);
  });
  if (candidates.length === 1 && !common.endsWith("/")) common += " ";
  else if (common === prefix) append("terminal-log", candidates.join("  "), "echo");
  input.value = line.slice(0, line.length - prefix.length) + common;
}

// --- save / exit / mail ----------------------------------------------------------------

function save() {
  if (mode === "menu") return;
  call("save");
  append("story-log", "Saved.", "saved");
  syncSaves();
}

async function saveAndExit() {
  call("save");
  await syncSaves();
  showSlotMenu(currentStory);
}

$("btn-save").addEventListener("click", save);
$("btn-menu").addEventListener("click", saveAndExit);
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save();
  } else if (mode === "narrative" && /^[1-9]$/.test(event.key) && !event.target.closest("input, textarea, dialog")) {
    // Consume the key: picking a choice can move focus into the terminal
    // input mid-keypress, which would otherwise receive the digit.
    event.preventDefault();
    $("choice-list").querySelectorAll("button")[Number(event.key) - 1]?.click();
  }
});

$("btn-mail").addEventListener("click", () => {
  const select = $("compose-to");
  select.replaceChildren();
  for (const npc of call("email_contacts")) select.append(new Option(npc.name, npc.id));
  $("compose-form").reset();
  $("compose-dialog").showModal();
});
$("compose-cancel").addEventListener("click", () => $("compose-dialog").close());
$("compose-form").addEventListener("submit", () => {
  // method="dialog" closes the dialog itself once this handler returns.
  const to = $("compose-to").value;
  const subject = $("compose-subject").value.trim();
  const body = $("compose-body").value;
  append("terminal-log", `${$("prompt").textContent} (mail to ${to}: "${subject}")`, "cmd-echo");
  act(() => call("compose_mail", to, subject, body));
});

boot();
