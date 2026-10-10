/** P8 client contracts + pure helpers: team seats/permissions, notification time/links/badge, privacy confirmation, staff queue order. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(file, apiClient = {}) {
  const source = readFileSync(join(__dirname, "../src/lib/api", file), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, JSON, Array, Object, Math, Number, String, Date, RegExp, Promise, URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} } }); // eslint-disable-line
  return exports;
}
const plain = (v) => JSON.parse(JSON.stringify(v));

test("team: seats text/limits, email validation and who may edit whom", () => {
  const m = load("team.ts");
  assert.equal(m.seatsText({ used: 3, limit: 10 }), "3 / 10");
  assert.equal(m.seatsText({ used: 3, limit: null }), "3 / ∞");
  assert.equal(m.seatsFull({ used: 3, limit: 3 }), true);
  assert.equal(m.seatsFull({ used: 2, limit: 3 }), false);
  assert.equal(m.seatsFull({ used: 99, limit: null }), false);
  for (const ok of ["a@b.sa", " name.x+1@company.com.sa "]) assert.equal(m.validEmail(ok), true, ok);
  for (const bad of ["", "a@b", "a b@c.sa", "@c.sa", "a@@c.sa"]) assert.equal(m.validEmail(bad), false, bad);
  const me = { user_id: "1" }, other = { user_id: "2" };
  assert.equal(m.canEditMember("1", me, true), false);          // never yourself
  assert.equal(m.canEditMember("1", other, true), true);
  assert.equal(m.canEditMember("1", other, false), false);      // auditors read only
  assert.match(m.ROLE_HELP.auditor, /قراءة|فقط/);
});

test("team: calls and error extraction", async () => {
  const calls = [];
  const api = { get: async (u) => (calls.push(["get", u]), { data: { members: [] } }), post: async (u, b) => (calls.push(["post", u, b]), { data: { ok: 1 } }),
    delete: async (u) => calls.push(["delete", u]), patch: async (u, b) => (calls.push(["patch", u, b]), { data: {} }) };
  const m = load("team.ts", api);
  await m.fetchTeam(); await m.inviteMember("  a@b.sa ", "agent"); await m.revokeInvitation("i1"); await m.updateMember("u1", { is_active: false });
  assert.deepEqual(plain(calls), [["get", "/team"], ["post", "/team/invitations", { email: "a@b.sa", role: "agent" }], ["delete", "/team/invitations/i1"], ["patch", "/team/members/u1", { is_active: false }]]);
  assert.equal(m.teamError({ response: { data: { detail: { message: "وصلت إلى حد المقاعد" } } } }, "fb"), "وصلت إلى حد المقاعد");
  assert.equal(m.teamError({}, "fb"), "fb");
});

test("notifications: relative time in Arabic, safe links, badge cap", () => {
  const m = load("notifications.ts");
  const now = Date.parse("2026-10-10T12:00:00Z");
  const ago = (s) => new Date(now - s * 1000).toISOString();
  assert.equal(m.timeAgo(ago(10), now), "الآن");
  assert.equal(m.timeAgo(ago(60), now), "قبل دقيقة");
  assert.equal(m.timeAgo(ago(120), now), "قبل دقيقتين");
  assert.equal(m.timeAgo(ago(5 * 60), now), "قبل 5 دقائق");
  assert.equal(m.timeAgo(ago(15 * 60), now), "قبل 15 دقيقة");
  assert.equal(m.timeAgo(ago(3600), now), "قبل ساعة");
  assert.equal(m.timeAgo(ago(3 * 3600), now), "قبل 3 ساعات");
  assert.equal(m.timeAgo(ago(86400), now), "أمس");
  assert.equal(m.timeAgo(ago(3 * 86400), now), "قبل 3 أيام");
  assert.equal(m.timeAgo(null, now), "");
  assert.equal(m.timeAgo("garbage", now), "");
  assert.equal(m.hrefFor("ar", "/settings#billing"), "/ar/settings#billing");
  assert.equal(m.hrefFor("ar", "https://evil.test"), null);
  assert.equal(m.hrefFor("ar", "//evil.test"), null);
  assert.equal(m.hrefFor("ar", null), null);
  assert.equal(m.badgeText(7), "7");
  assert.equal(m.badgeText(150), "99+");
});

test("notifications: prefs are saved as a kind → flags map", async () => {
  const calls = [];
  const m = load("notifications.ts", { put: async (u, b) => (calls.push([u, b]), { data: { items: [], email_available: false } }) });
  await m.savePrefs([{ kind: "handoff", label: "", description: "", in_app: false, email: true }, { kind: "payment_failed", label: "", description: "", in_app: true, email: true }]);
  assert.deepEqual(plain(calls[0]), ["/notifications/prefs", { handoff: { in_app: false, email: true }, payment_failed: { in_app: true, email: true } }]);
});

test("privacy: erasure needs the exact customer id; consent actions have Arabic labels", () => {
  const m = load("privacy.ts");
  assert.equal(m.canConfirmErase("abc-123", "abc-123"), true);
  assert.equal(m.canConfirmErase("  abc-123 ", "abc-123"), true);
  assert.equal(m.canConfirmErase("ABC-123", "abc-123"), false);
  assert.equal(m.canConfirmErase("", "abc-123"), false);
  assert.equal(m.canConfirmErase("نعم", "abc-123"), false);
  for (const k of ["customer.opt_in_granted", "customer.opt_out", "customer.created", "customer.merged"]) assert.ok(m.CONSENT_LABEL[k], k);
});

test("privacy: anonymize sends the id as confirmation", async () => {
  const calls = [];
  const m = load("privacy.ts", { post: async (u, b) => (calls.push([u, b]), { data: { messages_wiped: 2, conversations: 1 } }) });
  const r = await m.anonymizeCustomer("c1", "طلب العميل");
  assert.deepEqual(plain(calls[0]), ["/privacy/customers/c1/anonymize", { confirm: "c1", reason: "طلب العميل" }]);
  assert.equal(r.messages_wiped, 2);
});

test("support staff: queue shows unanswered + urgent first, closed last; non-staff probe fails closed", async () => {
  const t = (id, status, priority, awaiting) => ({ id, status, priority, awaiting_reply: awaiting });
  const m = load("support.ts", { get: async () => { throw new Error("404"); } });
  const sorted = [t("closed", "closed", "urgent", true), t("answered-urgent", "open", "urgent", false), t("new-low", "open", "low", true), t("new-urgent", "open", "urgent", true)].sort(m.staffQueueOrder);
  assert.deepEqual(sorted.map((x) => x.id), ["new-urgent", "new-low", "answered-urgent", "closed"]);
  assert.equal(await m.fetchIsStaff(), false);
});
