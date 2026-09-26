// End-to-end test of the browser build in headless Firefox (Playwright).
//
//   python web/build.py && python -m http.server -d _site 8000 &
//   cd web/e2e && npm ci && npx playwright install firefox
//   node e2e.mjs http://127.0.0.1:8000/
//
// Plays real scenes of both stories against a built site: boot (Pyodide
// from the CDN), menus, choices, the terminal (Tab, history, pipes, ssh
// password masking, a procedure puzzle), saves surviving a reload, moving a
// save to another browser via export/import, the phone layout, and no
// console errors. Screenshots land in ./shots/.
// Exits non-zero if any check fails.

import { mkdirSync } from "node:fs";
import { firefox } from "playwright";

const SITE = process.argv[2] ?? "http://127.0.0.1:8000/";
const SHOTS = new URL("shots/", import.meta.url).pathname;
const BOOT_TIMEOUT = 180_000; // first Pyodide download can be slow on CI
mkdirSync(SHOTS, { recursive: true });

let failures = 0;
function check(ok, what) {
  console.log(`${ok ? "PASS" : "FAIL"} ${what}`);
  if (!ok) failures++;
}

// A fresh context per scenario: its own IndexedDB, so saves never leak between scenarios.
async function openPage(browser, viewport = { width: 1280, height: 800 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.errors = [];
  page.on("console", (m) => m.type() === "error" && page.errors.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => page.errors.push(`pageerror: ${e.message}`));
  const started = Date.now();
  await page.goto(SITE);
  await page.waitForSelector("#screen-menu:not([hidden])", { timeout: BOOT_TIMEOUT });
  console.log(`  (boot took ${((Date.now() - started) / 1000).toFixed(1)}s)`);
  return page;
}

async function newGame(page, story, slot) {
  await page.getByRole("button", { name: story }).click();
  if (slot) await page.fill("#new-slot-name", slot);
  await page.click("#new-slot-form button[type=submit]");
  await page.waitForSelector("#screen-game:not([hidden])");
}

async function run(page, command) {
  await page.keyboard.type(command);
  await page.keyboard.press("Enter");
}

const waitForPrompt = (page, prompt) =>
  page.waitForFunction((p) => document.getElementById("prompt").textContent === p, prompt);

function noErrors(page, scenario) {
  check(page.errors.length === 0, `${scenario}: no console/page errors${page.errors.length ? ": " + page.errors.join(" | ") : ""}`);
}

async function zeroDay(browser) {
  console.log("Zero Day (desktop)");
  const page = await openPage(browser);
  await page.screenshot({ path: SHOTS + "menu.png" });
  check((await page.locator("#story-list button").count()) === 2, "menu lists both stories");
  await page.getByRole("button", { name: "Zero Day" }).click();
  check((await page.inputValue("#new-slot-name")) === "default", "new slot name defaults to 'default'");
  await page.fill("#new-slot-name", "e2e");
  await page.click("#new-slot-form button[type=submit]");
  await page.waitForSelector("#screen-game:not([hidden])");
  check((await page.textContent("#game-title")).includes("[e2e]"), "title shows slot");
  check((await page.locator("#choice-list button").count()) > 0, "choices rendered");
  await page.keyboard.press("1"); // number keys pick choices
  await page.keyboard.press("1");
  await page.waitForSelector("#terminal-pane:not(.inactive)");
  check((await page.evaluate(() => document.activeElement.id)) === "command-input", "terminal input focused");
  check(!(await page.isVisible("#key-buttons")), "on-screen key buttons hidden on desktop");
  check((await page.inputValue("#command-input")) === "", "choice digit did not leak into the input");

  await page.keyboard.type("conn");
  await page.keyboard.press("Tab");
  check((await page.inputValue("#command-input")) === "connect ", "Tab completes a command");
  await page.keyboard.type("gat");
  await page.keyboard.press("Tab");
  check((await page.inputValue("#command-input")) === "connect gateway ", "Tab completes a host");
  await page.keyboard.press("Enter");
  await waitForPrompt(page, "gateway$");
  check(true, "connect advanced the scene");

  await run(page, "cat /etc/netmon/netmon.conf");
  check((await page.textContent("#terminal-log")).includes("bind_address=127.0.0.1"), "cat output shown");
  await page.keyboard.press("ArrowUp");
  check((await page.inputValue("#command-input")) === "cat /etc/netmon/netmon.conf", "ArrowUp recalls history");
  await page.fill("#command-input", "");
  await run(page, 'grep "allow_query" /etc/netmon/netmon.conf');
  check((await page.textContent("#terminal-log")).includes("allow_query=denied"), "quoted grep works");
  await page.keyboard.press("Control+s");
  check((await page.textContent("#story-log")).includes("Saved."), "Ctrl+S saves");
  await page.screenshot({ path: SHOTS + "zero-day.png" });

  await page.reload();
  await page.waitForSelector("#screen-menu:not([hidden])", { timeout: BOOT_TIMEOUT });
  await page.getByRole("button", { name: "Zero Day" }).click();
  const slots = await page.textContent("#slot-list");
  check(slots.includes("e2e") && slots.includes("gateway_shell"), "save survives a reload");
  await page.getByRole("button", { name: /Continue e2e/ }).click();
  await waitForPrompt(page, "gateway$");
  check(true, "continue resumes in place");
  await run(page, ":quit");
  await page.waitForSelector("#menu-slots:not([hidden])");
  check(true, ":quit returns to the slot menu");
  noErrors(page, "Zero Day");
  await page.context().close();
}

async function deadDrop(browser) {
  console.log("Dead Drop (desktop)");
  const page = await openPage(browser);
  await newGame(page, "Dead Drop", "dd-e2e");
  await page.keyboard.press("1");
  await page.waitForSelector("#terminal-pane:not(.inactive)");
  check((await page.innerHTML("#story-log")).includes("<code>status</code>"), "scene text renders code spans");
  await run(page, "hint");
  check((await page.textContent("#terminal-log")).includes("Hint 1/2: Juno gave you the host: relay."), "hint shows the first hint");
  await run(page, "connect relay");
  await waitForPrompt(page, "relay$");
  await run(page, "cat /var/log/relay.log | grep mara | grep vault");
  const log = await page.textContent("#terminal-log");
  check(log.includes("new one: lighthouse-42") && !log.includes("coffee run"), "piped grep narrows the log");

  await run(page, "ssh mara@vault");
  await waitForPrompt(page, "password:");
  check((await page.getAttribute("#command-input", "type")) === "password", "password input is masked");
  check(await page.isDisabled("#key-buttons [data-key=Tab]"), "key buttons disabled at the password prompt");
  await run(page, "lighthouse-42");
  await page.waitForSelector("#choices-pane:not([hidden])");
  const terminal = await page.textContent("#terminal-log");
  check(terminal.includes("password: ********") && !terminal.includes("password: lighthouse-42"), "password echo is masked");
  check((await page.getAttribute("#command-input", "type")) === "text", "input unmasked afterwards");

  await page.keyboard.press("1"); // Follow the runbook
  await page.waitForSelector("#terminal-pane:not(.inactive)");
  await page.keyboard.press("ArrowUp");
  check(!(await page.inputValue("#command-input")).includes("lighthouse"), "password kept out of history");
  await page.fill("#command-input", "");
  for (const step of ["systemctl status replica", "set /etc/replica/replica.conf mode primary", "systemctl restart replica"]) {
    await run(page, step);
  }
  await page.waitForSelector("#choices-pane:not([hidden])");
  check((await page.textContent("#story-log")).includes("It's yours now."), "runbook procedure solved");
  const pinned = await page.evaluate(() => {
    const l = document.getElementById("story-log");
    return l.scrollHeight - l.scrollTop - l.clientHeight < 2;
  });
  check(pinned, "story log pinned to the newest text");
  await page.screenshot({ path: SHOTS + "dead-drop.png" });
  await page.keyboard.press("2"); // Burn it (went in alone)
  await page.waitForSelector("#screen-game.ended");
  check((await page.textContent("#story-log")).includes("Scorched Earth"), "reached an ending");
  noErrors(page, "Dead Drop");
  await page.context().close();
}

async function saveFiles(browser) {
  console.log("Save export/import (two separate browsers)");
  const a = await openPage(browser);
  await newGame(a, "Zero Day", "moveme");
  await a.keyboard.press("1");
  await a.keyboard.press("1");
  await a.waitForSelector("#terminal-pane:not(.inactive)");
  await run(a, "connect gateway");
  await waitForPrompt(a, "gateway$");
  await run(a, "set /etc/netmon/netmon.conf bind_address 0.0.0.0");
  await a.click("#btn-menu"); // Save & exit
  await a.waitForSelector("#menu-slots:not([hidden])");
  const [download] = await Promise.all([a.waitForEvent("download"), a.getByRole("button", { name: "Export" }).click()]);
  check(download.suggestedFilename() === "terminalgames-zero_day-moveme.json", "export downloads a named save file");
  const saveFile = SHOTS + download.suggestedFilename();
  await download.saveAs(saveFile);
  noErrors(a, "Export");
  await a.context().close();

  const b = await openPage(browser); // fresh context: empty IndexedDB, like another device
  await b.getByRole("button", { name: "Zero Day" }).click();
  check((await b.locator("#slot-list li").count()) === 0, "second browser starts with no saves");
  await b.setInputFiles("#import-file", saveFile);
  await b.waitForFunction(() => document.getElementById("slot-message").textContent.includes("Imported"));
  check((await b.textContent("#slot-message")) === 'Imported slot "moveme".', "import reports success");
  check((await b.textContent("#slot-list")).includes("gateway_shell"), "imported slot is listed where it was saved");

  b.once("dialog", (dialog) => dialog.accept()); // "already exists -- replace?"
  await b.setInputFiles("#import-file", saveFile);
  await b.waitForFunction(() => document.getElementById("slot-message").textContent.includes("Imported"));
  check((await b.locator("#slot-list li").count()) === 1, "re-import after confirming replaces the slot");

  await b.setInputFiles("#import-file", { name: "junk.json", mimeType: "application/json", buffer: Buffer.from("{nope") });
  await b.waitForFunction(() => document.getElementById("slot-message").classList.contains("error"));
  check((await b.textContent("#slot-message")) === "Import failed: not a TerminalGames save file", "junk file is rejected");

  await b.getByRole("button", { name: /Continue moveme/ }).click();
  await waitForPrompt(b, "gateway$");
  await run(b, "cat /etc/netmon/netmon.conf");
  check((await b.textContent("#terminal-log")).includes("bind_address=0.0.0.0"), "imported save continues with the player's edits");
  noErrors(b, "Import");
  await b.context().close();
}

async function phone(browser) {
  console.log("Phone layout (390px)");
  const page = await openPage(browser, { width: 390, height: 780 });
  await newGame(page, "Zero Day");
  await page.screenshot({ path: SHOTS + "phone-narrative.png" });
  await page.keyboard.press("1");
  await page.keyboard.press("1");
  await page.waitForSelector("#terminal-pane:not(.inactive)");
  await run(page, "help");
  check(await page.isVisible("#key-buttons"), "on-screen key buttons shown at phone width");
  await page.keyboard.type("conn");
  await page.click("#key-buttons [data-key=Tab]");
  check((await page.inputValue("#command-input")) === "connect ", "Tab button completes");
  check((await page.evaluate(() => document.activeElement.id)) === "command-input", "input keeps focus after a tap");
  await page.fill("#command-input", "");
  await page.click("#key-buttons [data-key=ArrowUp]");
  check((await page.inputValue("#command-input")) === "help", "Up button recalls history");
  await page.click("#key-buttons [data-key=ArrowDown]");
  check((await page.inputValue("#command-input")) === "", "Down button steps back to an empty line");
  await page.screenshot({ path: SHOTS + "phone-terminal.png" });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  check(!overflow, "no horizontal scroll at phone width");
  await page.click("#btn-menu"); // the slot menu, with its three-button rows
  await page.waitForSelector("#menu-slots:not([hidden])");
  await page.screenshot({ path: SHOTS + "phone-slots.png" });
  const menuOverflow = await page.evaluate(() => {
    const menu = document.getElementById("screen-menu");
    return menu.scrollWidth > menu.clientWidth;
  });
  check(!menuOverflow, "slot menu fits at phone width");
  noErrors(page, "Phone");
  await page.context().close();
}

const browser = await firefox.launch();
try {
  for (const scenario of [zeroDay, deadDrop, saveFiles, phone]) {
    try {
      await scenario(browser);
    } catch (err) {
      check(false, `${scenario.name} crashed: ${err.message.split("\n")[0]}`);
    }
  }
} finally {
  await browser.close();
}
console.log(failures ? `\n${failures} check(s) failed` : "\nAll checks passed");
process.exit(failures ? 1 : 0);
