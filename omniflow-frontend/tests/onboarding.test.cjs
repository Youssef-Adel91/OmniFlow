/** Onboarding client contracts + the Embedded Signup message parser (trusts only facebook.com origins). */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/onboarding.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), URL, JSON, Promise, Error }); // eslint-disable-line
  return exports;
}

test("signup config: null when the backend answers 404 (flag off) or enabled=false", async () => {
  const off = load({ get: async () => { throw new Error("404"); } });
  assert.equal(await off.fetchSignupConfig(), null);
  const disabled = load({ get: async () => ({ data: { enabled: false } }) });
  assert.equal(await disabled.fetchSignupConfig(), null);
  const on = load({ get: async () => ({ data: { enabled: true, app_id: "1", config_id: "c" } }) });
  assert.equal((await on.fetchSignupConfig()).config_id, "c");
});

test("parseSignupMessage accepts FINISH from facebook.com only", () => {
  const m = load({});
  const finish = JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "999888", phone_number_id: "555111" } });
  assert.deepEqual(JSON.parse(JSON.stringify(m.parseSignupMessage("https://www.facebook.com", finish))), { waba_id: "999888", phone_number_id: "555111" });
  assert.deepEqual(JSON.parse(JSON.stringify(m.parseSignupMessage("https://business.facebook.com", JSON.parse(finish)))), { waba_id: "999888", phone_number_id: "555111" });
  assert.equal(m.parseSignupMessage("https://evil.example", finish), null);
  assert.equal(m.parseSignupMessage("https://facebook.com.evil.example", finish), null);
  assert.equal(m.parseSignupMessage("not a url", finish), null);
});

test("parseSignupMessage rejects malformed payloads and handles CANCEL", () => {
  const m = load({});
  const o = "https://www.facebook.com";
  assert.equal(m.parseSignupMessage(o, "not json"), null);
  assert.equal(m.parseSignupMessage(o, JSON.stringify({ type: "OTHER" })), null);
  assert.equal(m.parseSignupMessage(o, JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { waba_id: "x", phone_number_id: "1" } })), null);
  assert.equal(m.parseSignupMessage(o, JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event: "CANCEL" })).cancelled, true);
});

test("every checklist step has copy", () => {
  const m = load({});
  for (const k of ["company", "persona", "properties", "channel", "bot_test"]) assert.ok(m.STEP_COPY[k].title && m.STEP_COPY[k].hint, k);
});
