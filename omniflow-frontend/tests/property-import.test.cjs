/** Property import + list/bulk client contracts. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(rel, apiClient) {
  const source = readFileSync(join(__dirname, "..", rel), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), URL: { createObjectURL() {}, revokeObjectURL() {} }, document: {}, Error });
  return exports;
}

const plain = (v) => JSON.parse(JSON.stringify(v));   // vm contexts have their own Object/Array prototypes

const recorder = () => {
  const calls = [];
  const api = {
    get: async (url, cfg) => { calls.push(["get", url, cfg]); return { data: { items: [], total: 0 } }; },
    post: async (url, body, cfg) => { calls.push(["post", url, body, cfg]); return { data: {} }; },
    delete: async (url) => { calls.push(["delete", url]); },
  };
  return { api, calls };
};

test("list params: trims search/city, drops empties, forwards price and sort", async () => {
  const { api, calls } = recorder();
  const m = load("src/lib/api/properties.ts", api);
  await m.listProperties({ page: 2, search: "  النرجس ", city: "   ", price_min: 100, sort: "price_desc" });
  const p = calls[0][2].params;
  assert.equal(p.search, "النرجس");
  assert.equal(p.city, undefined);
  assert.deepEqual(plain({ page: p.page, price_min: p.price_min, sort: p.sort, limit: p.limit }), { page: 2, price_min: 100, sort: "price_desc", limit: 20 });
});

test("bulk endpoints and export", async () => {
  const { api, calls } = recorder();
  const m = load("src/lib/api/properties.ts", api);
  await m.bulkDeleteProperties(["a", "b"]);
  await m.bulkSetPropertyStatus(["a"], "SOLD");
  await m.exportPropertiesCsv({ search: " x ", status: "SOLD" });
  assert.deepEqual(plain(calls[0].slice(0, 3)), ["post", "/properties/bulk/delete", { ids: ["a", "b"] }]);
  assert.deepEqual(plain(calls[1].slice(0, 3)), ["post", "/properties/bulk/status", { ids: ["a"], status: "SOLD" }]);
  assert.equal(calls[2][1], "/properties/export.csv");
  assert.equal(calls[2][2].responseType, "blob");
  assert.equal(calls[2][2].params.search, "x");
});

test("import client talks to the documented endpoints", async () => {
  const { api, calls } = recorder();
  const m = load("src/lib/api/propertyImport.ts", api);
  await m.validateImport("J1", { mapping: { city: "المدينة" }, options: { on_duplicate: "skip", defaults: {} }, save_template_as: "t" });
  await m.commitImport("J1");
  await m.fetchImport("J1");
  await m.cancelImport("J1");
  await m.listMappingTemplates();
  await m.deleteMappingTemplate("T1");
  assert.deepEqual(calls.map((c) => `${c[0]} ${c[1]}`), [
    "post /properties/import/J1/validate", "post /properties/import/J1/commit", "get /properties/import/J1",
    "post /properties/import/J1/cancel", "get /properties/import/templates", "delete /properties/import/templates/T1",
  ]);
  assert.deepEqual(plain(m.TERMINAL_STATUSES), ["completed", "failed", "cancelled"]);
});

test("upload sends multipart form data and reports progress", async () => {
  let form;
  const api = { post: async (url, body, cfg) => { form = body; cfg.onUploadProgress({ loaded: 50, total: 200 }); return { data: { import_id: "J" } }; } };
  const seen = [];
  const vmForm = class { constructor() { this.entries = []; } append(k, v) { this.entries.push([k, v]); } };
  // FormData is resolved inside the module's own context, so load it with a stub in scope.
  const source = readFileSync(join(__dirname, "../src/lib/api/propertyImport.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient: api }), FormData: vmForm, Error });
  const res = await exports.uploadImportFile({ name: "x.csv" }, (p) => seen.push(p));
  assert.equal(res.import_id, "J");
  assert.deepEqual(plain(form.entries[0]), ["file", { name: "x.csv" }]);
  assert.deepEqual(plain(seen), [25]);
});

test("import errors are surfaced in Arabic from every backend shape", () => {
  const m = load("src/lib/api/propertyImport.ts", {});
  const e = (detail) => ({ response: { data: { detail } } });
  assert.equal(m.importErrorMessage(e({ code: "file_too_large", message: "كبير" })), "كبير");
  assert.equal(m.importErrorMessage(e("نص")), "نص");
  assert.equal(m.importErrorMessage(e([{ msg: "حقل مفقود" }])), "حقل مفقود");
  assert.equal(m.importErrorMessage(new Error("net"), "x"), "net");
  assert.equal(m.importErrorMessage({}, "افتراضي"), "افتراضي");
});

test("bulk by filters / re-index: ids vs 'all matching' scope, filters are trimmed, indexed flag is forwarded", async () => {
  const { api, calls } = recorder();
  const m = load("src/lib/api/properties.ts", api);
  await m.bulkSetPropertyStatus({ filters: { status: "PENDING_VERIFICATION", city: "  جدة ", search: "  ", indexed: false } }, "VERIFIED_ACTIVE");
  await m.reindexProperties({ ids: ["a"] });
  await m.reindexProperties({ filters: { indexed: false } });
  await m.listProperties({ indexed: false });
  await m.exportPropertiesCsv({ indexed: true });
  assert.deepEqual(plain(calls[0].slice(1, 3)), ["/properties/bulk/status", { filters: { status: "PENDING_VERIFICATION", city: "جدة", indexed: false }, status: "VERIFIED_ACTIVE" }]);
  assert.deepEqual(plain(calls[1].slice(1, 3)), ["/properties/reindex", { ids: ["a"] }]);
  assert.deepEqual(plain(calls[2].slice(1, 3)), ["/properties/reindex", { filters: { indexed: false } }]);
  assert.equal(calls[3][2].params.indexed, false);
  assert.equal(calls[4][2].params.indexed, true);
});

test("warnings: pending shows unless activating or another default status; index totals count missing listings", () => {
  const m = load("src/lib/api/propertyImport.ts", {});
  assert.equal(m.showPendingWarning({ defaults: {} }), true);
  assert.equal(m.showPendingWarning({ defaults: { status: "PENDING_VERIFICATION" } }), true);
  assert.equal(m.showPendingWarning({ defaults: { status: "VERIFIED_ACTIVE" } }), false);
  assert.equal(m.showPendingWarning({ defaults: {}, activate: true }), false);
  assert.equal(m.PENDING_WARNING, "العقارات قيد التحقق لن يراها المساعد حتى يتم اعتمادها");
  assert.match(m.ACTIVATE_WARNING, /REGA/);
  assert.deepEqual(plain(m.indexTotals({ kind: "properties", created: 4, updated: 1, indexed: 3, index_failed: 2, total_rows: 9 })), { expected: 5, indexed: 3, failed: 2, missing: 2 });
  assert.deepEqual(plain(m.indexTotals({ kind: "reindex", created: 0, updated: 0, indexed: 7, index_failed: 0, total_rows: 7 })), { expected: 7, indexed: 7, failed: 0, missing: 0 });
});
