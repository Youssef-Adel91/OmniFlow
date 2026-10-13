/** Deals client contracts: a 404 means "feature not enabled" (null), other errors surface; PATCH/GET urls. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/deals.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, JSON, Array, Object }); // eslint-disable-line
  return exports;
}
const err = (status, detail) => Object.assign(new Error("x"), { response: { status, data: { detail } } });

test("board returns null when the feature is off (404) and rethrows other errors", async () => {
  assert.equal(await load({ get: async () => { throw err(404); } }).fetchBoard(), null);
  await assert.rejects(load({ get: async () => { throw err(500); } }).fetchBoard());
  const calls = [];
  const m = load({ get: async (u, o) => (calls.push([u, o]), { data: { stages: [], columns: {} } }) });
  await m.fetchBoard(true);
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [["/deals/board", { params: { mine: true } }]]);
});

test("moving a deal PATCHes the deal with stage and lost reason", async () => {
  const calls = [];
  const m = load({ patch: async (u, b) => (calls.push([u, b]), { data: { deal_id: "d1" } }) });
  await m.moveDeal("d1", { stage: "lost", lost_reason: "price" });
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [["/deals/d1", { stage: "lost", lost_reason: "price" }]]);
});

test("server messages are shown, unknown failures get a generic Arabic message; every stage has a label", () => {
  const m = load({});
  assert.equal(m.dealErrorMessage(err(422, { message: "اختر سبب الخسارة" })), "اختر سبب الخسارة");
  assert.match(m.dealErrorMessage(new Error("net")), /تعذّر/);
  for (const s of ["new", "qualified", "viewing", "negotiation", "won", "lost"]) assert.ok(m.STAGE_LABELS[s]);
});
