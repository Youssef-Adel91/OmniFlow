/** Developers client contracts + delivery wording. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/developers.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Promise }); // eslint-disable-line
  return exports;
}

test("endpoints and bodies", async () => {
  const calls = [];
  const rec = (m) => async (url, body) => { calls.push([m, url, body]); return { data: { items: [], key: "k", secret: "s" } }; };
  const m = load({ get: rec("get"), post: rec("post"), patch: rec("patch"), delete: rec("delete") });
  await m.fetchOverview(); await m.createApiKey("crm"); await m.revokeApiKey("1");
  await m.createWebhook({ url: "https://x", events: ["customer.created"] }); await m.updateWebhook("2", { active: false });
  await m.deleteWebhook("2"); await m.rotateSecret("2"); await m.testWebhook("2"); await m.fetchDeliveries("2");
  assert.deepEqual(calls.map((c) => `${c[0]} ${c[1]}`), [
    "get /developers/overview", "post /developers/api-keys", "delete /developers/api-keys/1", "post /developers/webhooks",
    "patch /developers/webhooks/2", "delete /developers/webhooks/2", "post /developers/webhooks/2/rotate-secret", "post /developers/webhooks/2/test", "get /developers/webhooks/2/deliveries"]);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1][2])), { name: "crm" });
});

test("delivery wording covers each state", () => {
  const m = load({});
  assert.match(m.deliveryLabel({ status: "delivered", last_status_code: 200, attempts: 1, last_error: null }), /HTTP 200/);
  assert.match(m.deliveryLabel({ status: "failed", last_status_code: 500, attempts: 6, last_error: "http_500" }), /6/);
  assert.match(m.deliveryLabel({ status: "pending", last_status_code: 503, attempts: 1, last_error: "http_503" }), /http_503/);
  assert.equal(m.deliveryLabel({ status: "pending", last_status_code: null, attempts: 0, last_error: null }), "في الانتظار");
});

test("every event has an Arabic label", () => {
  const m = load({});
  for (const e of ["conversation.created", "conversation.handoff", "customer.created"]) assert.ok(m.EVENT_LABELS[e]);
});
