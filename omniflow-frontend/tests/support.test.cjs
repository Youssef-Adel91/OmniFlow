/** Support client contracts + pure helpers (attachment pre-check, response-target wording, id normalisation). */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/support.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, JSON, Array, Object, FormData: class { append(k, v) { this[k] = v; } } }); // eslint-disable-line
  return exports;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const MB = 1024 * 1024;

test("attachment pre-check mirrors the server rules", () => {
  const m = load({});
  assert.equal(m.validateAttachment({ name: "a.png", size: 100 }, 0), null);
  assert.equal(m.validateAttachment({ name: "A.PDF", size: 100 }, 4), null);
  assert.match(m.validateAttachment({ name: "a.png", size: 100 }, 5), /الحد الأقصى 5/);
  assert.match(m.validateAttachment({ name: "big.pdf", size: 6 * MB }, 0), /يتجاوز 5 ميجابايت/);
  assert.match(m.validateAttachment({ name: "run.exe", size: 100 }, 0), /غير مسموح/);
  assert.match(m.validateAttachment({ name: "noext", size: 100 }, 0), /غير مسموح/);
  assert.match(m.validateAttachment({ name: "e.csv", size: 0 }, 0), /فارغ/);
});

test("response targets are worded as targets in Arabic", () => {
  const m = load({});
  assert.deepEqual(plain([4, 8, 24, 48, 1, 2, 72, 12].map(m.formatTarget)), ["خلال 4 ساعات", "خلال 8 ساعات", "خلال يوم", "خلال يومين", "خلال ساعة", "خلال ساعتين", "خلال 3 أيام", "خلال 12 ساعة"]);
});

test("tickets are normalised from the API shape (ticket_id → id, unknown status → open)", () => {
  const m = load({});
  const t = m.normalizeTicket({ ticket_id: "t1", reference: "OF-ABCD2345", subject: "s", message: "m", status: "weird", priority: "high" });
  assert.deepEqual(plain([t.id, t.reference, t.status, t.priority, t.category]), ["t1", "OF-ABCD2345", "open", "high", "other"]);
});

test("create/list/thread/message/upload hit the right endpoints and unwrap the thread", async () => {
  const calls = [];
  const api = {
    get: async (u, o) => (calls.push(["get", u, o?.params]), u === "/support/tickets/t1"
      ? { data: { ticket: { ticket_id: "t1", status: "closed" }, messages: [{ message_id: "m1", author_type: "support", body: "hi", created_at: "x" }], attachments: [{ attachment_id: "a1", filename: "f.png", content_type: "image/png", size_bytes: 3, created_at: "x" }] } }
      : { data: { items: [{ ticket_id: "t1" }], total: 1 } }),
    post: async (u, b, o) => (calls.push(["post", u, b, o?.headers]), { data: { ticket_id: "t1", message_id: "m2", attachment_id: "a2", author_type: "customer", body: "x", filename: "f.png" } }),
    patch: async (u, b) => (calls.push(["patch", u, b]), { data: { ticket_id: "t1", status: "closed" } }),
  };
  const m = load(api);
  await m.createTicket({ subject: "s", message: "m", category: "ai", priority: "high" });
  const page = await m.fetchTickets({ status: "open", category: "", priority: "high" });
  assert.deepEqual(plain(calls[1][2]), { page: 1, page_size: 20, status: "open", priority: "high" });
  assert.equal(page.items[0].id, "t1");
  const d = await m.fetchTicket("t1");
  assert.deepEqual(plain([d.ticket.status, d.messages[0].id, d.attachments[0].id]), ["closed", "m1", "a1"]);
  assert.equal((await m.sendMessage("t1", "x")).id, "m2");
  assert.equal((await m.updateTicket("t1", "closed")).status, "closed");
  const up = await m.uploadAttachment("t1", { name: "f.png" });
  assert.equal(up.id, "a2");
  assert.equal(calls.at(-1)[1], "/support/tickets/t1/attachments");
  assert.equal(calls.at(-1)[3]["Content-Type"], "multipart/form-data");
});

test("server error messages are surfaced, otherwise the fallback", () => {
  const m = load({});
  assert.equal(m.apiErrorMessage({ response: { data: { detail: { message: "حجم الملف" } } } }, "fb"), "حجم الملف");
  assert.equal(m.apiErrorMessage({ response: { data: { detail: "نص" } } }, "fb"), "نص");
  assert.equal(m.apiErrorMessage(new Error("boom"), "fb"), "fb");
});
