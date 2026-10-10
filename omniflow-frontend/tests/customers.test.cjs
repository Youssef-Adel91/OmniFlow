/** Customers client contracts (identity fields, filters, consent, merge, error shapes). */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/customers.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  const adapters = [];
  const req = (m) => (m.endsWith("propertyImport") ? { makeImportAdapter: (...a) => (adapters.push(a), { kind: a[0] }) } : { apiClient });
  vm.runInNewContext(code, { exports, require: req, Error, Array, Object }); // eslint-disable-line
  return { m: exports, adapters };
}

const plain = (v) => JSON.parse(JSON.stringify(v));
const recorder = (data = { items: [], total: 0 }) => {
  const calls = [];
  const api = {
    get: async (url, cfg) => { calls.push(["get", url, cfg]); return { data }; },
    post: async (url, body) => { calls.push(["post", url, body]); return { data: { customer_id: "c1", phone_e164: "+966501234567" } }; },
    patch: async (url, body) => { calls.push(["patch", url, body]); return { data: { customer_id: "c1" } }; },
    delete: async (url) => { calls.push(["delete", url]); },
  };
  return { api, calls };
};

test("customers import adapter targets /customers/import with its own template file", () => {
  const { adapters, m } = load(recorder().api);
  assert.deepEqual(plain(adapters[0]), ["customers", "/customers/import", "omniflow-customers-template.xlsx"]);
  assert.equal(m.customersImportAdapter.kind, "customers");
});

test("normalisation keeps identity + consent fields and defaults the missing ones safely", async () => {
  const { api } = recorder({ items: [{ customer_id: "c1", unified_phone: "1784", is_vip: false, is_processing_restricted: false }], total: 1 });
  const { m } = load(api);
  const page = await m.fetchCustomers();
  const c = page.items[0];
  assert.equal(c.id, "c1");
  assert.equal(c.phone_e164, null);
  assert.equal(c.source, "whatsapp");
  assert.deepEqual(plain(c.tags), []);
  assert.equal(c.marketing_opt_in, false);        // no consent unless the server says so
  assert.equal(c.opt_out_at, null);
});

test("list + export forward the new filters (trimmed, empty dropped)", async () => {
  const { api, calls } = recorder();
  const { m } = load(api);
  await m.fetchCustomers({ source: "instagram", tag: "  vip ", city: "  ", opt_in: false, archived: true });
  await m.exportCustomersCsv({ search: " x ", opt_in: true, is_vip: true });
  const p = calls[0][2].params;
  assert.deepEqual(plain({ source: p.source, tag: p.tag, city: p.city, opt_in: p.opt_in, archived: p.archived }),
    { source: "instagram", tag: "vip", opt_in: false, archived: true });
  assert.equal(calls[1][1], "/customers/export.csv");
  assert.deepEqual(plain({ s: calls[1][2].params.search, o: calls[1][2].params.opt_in, st: calls[1][2].params.status }), { s: "x", o: true, st: "vip" });
});

test("create / patch / merge / delete hit the documented endpoints", async () => {
  const { api, calls } = recorder();
  const { m } = load(api);
  await m.createCustomer({ phone: "0501234567", marketing_opt_in: false });
  await m.updateCustomer("c1", { marketing_opt_in: true, archived: false });
  await m.mergeCustomers("c1", "c2");
  await m.deleteCustomer("c1");
  assert.deepEqual(plain(calls.map((c) => c.slice(0, 3))), [
    ["post", "/customers", { phone: "0501234567", marketing_opt_in: false }],
    ["patch", "/customers/c1", { marketing_opt_in: true, archived: false }],
    ["post", "/customers/c1/merge", { source_customer_id: "c2" }],
    ["delete", "/customers/c1"],
  ]);
});

test("structured API errors keep their code and the existing customer id (duplicate phone → merge flow)", () => {
  const { m } = load(recorder().api);
  const e = (detail) => ({ response: { data: { detail } } });
  assert.deepEqual(plain(m.customerErrorInfo(e({ code: "DUPLICATE_PHONE", message: "مكرر", existing_customer_id: "c9" }))), { message: "مكرر", code: "DUPLICATE_PHONE", existingId: "c9" });
  assert.deepEqual(plain(m.customerErrorInfo(e("نص"))), { message: "نص" });
  assert.deepEqual(plain(m.customerErrorInfo(e([{ msg: "حقل" }]))), { message: "حقل" });
  assert.equal(m.customerErrorInfo(new Error("net")).message, "net");
});
