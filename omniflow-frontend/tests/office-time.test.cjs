/** Viewing times are rendered in the OFFICE's timezone (default Asia/Riyadh), whatever the browser's zone is; and the screens use that helper. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load() {
  const source = readFileSync(join(__dirname, "../src/lib/officeTime.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, Intl, Date, Number }); // eslint-disable-line
  return exports;
}
const read = (p) => readFileSync(join(__dirname, "..", p), "utf8");

test("07:00 UTC is 10:00 in Riyadh (not the browser's zone) and 11:00 in Dubai", () => {
  const { formatOfficeTime } = load();
  assert.match(formatOfficeTime("2026-11-20T07:00:00Z", undefined, "en-GB"), /10:00/);
  assert.match(formatOfficeTime("2026-11-20T07:00:00Z", "Asia/Riyadh", "en-GB"), /10:00/);
  assert.match(formatOfficeTime("2026-11-20T07:00:00Z", "Asia/Dubai", "en-GB"), /11:00/);
  assert.match(formatOfficeTime("2026-11-20T21:30:00Z", "Asia/Riyadh", "en-GB"), /21 Nov 2026.*00:30/);   // the date follows the office zone too
});

test("missing / invalid input and an unknown zone never throw", () => {
  const { formatOfficeTime } = load();
  assert.equal(formatOfficeTime(null), "—");
  assert.equal(formatOfficeTime("not a date"), "—");
  assert.match(formatOfficeTime("2026-11-20T07:00:00Z", "Mars/Olympus", "en-GB"), /10:00/);
});

test("the sales summary and the customer drawer render viewings with the office timezone", () => {
  const summary = read("src/components/features/inbox/SalesSummary.tsx");
  assert.match(summary, /formatOfficeTime\(data\.appointment\.scheduled_at, data\.appointment\.timezone\)/);
  assert.doesNotMatch(summary, /new Date\(data\.appointment\.scheduled_at\)/);
  const drawer = read("src/components/customers/CustomerDetailDrawer.tsx");
  assert.match(drawer, /viewingTime\(x\.scheduled_at, data\.office_timezone\)/);
});

test("voice replies are an explicit office toggle and the voice label is only for audio messages", () => {
  const ai = read("src/components/settings/AiSection.tsx");
  assert.match(ai, /voice_replies_enabled/);
  assert.match(ai, /Boolean\(bd\.voice_replies_enabled\)/);
  const chat = read("src/components/features/inbox/ChatArea.tsx");
  assert.match(chat, /isAudio && msg\.mediaUrl/);
  assert.match(chat, /رد صوتي من المساعد/);
});
