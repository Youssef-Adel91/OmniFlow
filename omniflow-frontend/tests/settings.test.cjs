/** Settings client contracts + the pure helpers behind the hours editor / dirty tracking. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/settings.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, JSON, Array, Object, FormData: class {} }); // eslint-disable-line
  return exports;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const recorder = (data = {}) => {
  const calls = [];
  return { calls, api: { get: async (u) => (calls.push(["get", u]), { data }), put: async (u, b) => (calls.push(["put", u, b]), { data: { value: b } }), post: async (u, b) => (calls.push(["post", u, b]), { data }) } };
};
const hours = (days, extra = {}) => ({ enabled: true, timezone: "Asia/Riyadh", out_of_hours_message: "x", days, ...extra });

test("hours validation mirrors the server rules (order, overlap, empty week, bad format)", () => {
  const m = load(recorder().api);
  assert.deepEqual(plain(m.validateHours(hours({ sun: [["09:00", "17:00"]] }))), []);
  assert.match(m.validateHours(hours({ mon: [["17:00", "09:00"]] }))[0], /الإثنين: نهاية الفترة/);
  assert.match(m.validateHours(hours({ tue: [["09:00", "13:00"], ["12:00", "15:00"]] }))[0], /متداخلة/);
  assert.match(m.validateHours(hours({ wed: [["9:00", "17:00"]] }))[0], /صيغة الوقت/);
  assert.deepEqual(plain(m.validateHours(hours({}))), ["فعّلت ساعات العمل لكن كل الأيام مغلقة"]);
  assert.deepEqual(plain(m.validateHours(hours({}, { enabled: false }))), []);
});

test("hours summary groups consecutive identical days and marks closed ones", () => {
  const m = load(recorder().api);
  const week = { sun: [["09:00", "17:00"]], mon: [["09:00", "17:00"]], tue: [["09:00", "17:00"]], wed: [["09:00", "17:00"]], thu: [["09:00", "17:00"]] };
  assert.equal(m.summarizeHours(hours(week)), "الأحد–الخميس 09:00–17:00 · الجمعة–السبت مغلق");
  assert.equal(m.summarizeHours(hours({ sun: [["09:00", "13:00"], ["16:00", "20:00"]] })), "الأحد 09:00–13:00، 16:00–20:00 · الإثنين–السبت مغلق");
  assert.match(m.summarizeHours(hours(week, { enabled: false })), /غير مفعّلة/);
});

test("keywords are split on newlines/commas/Arabic commas and de-duplicated; sameJson drives the dirty flag", () => {
  const m = load(recorder().api);
  assert.deepEqual(plain(m.parseKeywords("اكلم موظف\n  خدمة العملاء، human ,human;;")), ["اكلم موظف", "خدمة العملاء", "human"]);
  assert.equal(m.sameJson({ a: [1, 2] }, { a: [1, 2] }), true);
  assert.equal(m.sameJson({ a: [1, 2] }, { a: [2, 1] }), false);
});

test("preferences, channels and bot-test hit the documented endpoints", async () => {
  const { api, calls } = recorder({ ok: true });
  const m = load(api);
  await m.fetchPreferences();
  await m.savePreferences("handoff", { sla_minutes: 20 });
  await m.fetchChannels();
  await m.checkChannel("whatsapp");
  await m.disconnectFacebook();
  await m.testBot("مرحبا");
  assert.deepEqual(plain(calls.map((c) => c.slice(0, 3).filter((x) => x !== undefined))), [
    ["get", "/settings/preferences"], ["put", "/settings/preferences/handoff", { sla_minutes: 20 }], ["get", "/settings/channels"],
    ["post", "/settings/channels/whatsapp/check"], ["post", "/settings/channels/facebook/disconnect"], ["post", "/settings/ai/test", { message: "مرحبا" }],
  ]);
});

test("error messages come from structured, string and validation shapes", () => {
  const m = load(recorder().api);
  const e = (detail) => ({ response: { data: { detail } } });
  assert.equal(m.settingsErrorMessage(e({ code: "INVALID_PREFERENCES", message: "منطقة زمنية غير معروفة" })), "منطقة زمنية غير معروفة");
  assert.equal(m.settingsErrorMessage(e("نص")), "نص");
  assert.equal(m.settingsErrorMessage(e([{ msg: "حقل" }])), "حقل");
  assert.equal(m.settingsErrorMessage(new Error("net")), "net");
});

test("comment activity: every decision has an honest Arabic label and a tone", () => {
  const m = load(recorder().api);
  assert.deepEqual(plain(m.commentActionLabel("hidden", "abusive")), { text: "أُخفي (إساءة)", tone: "warn" });
  assert.deepEqual(plain(m.commentActionLabel("hidden", "spam")), { text: "أُخفي (سبام/رابط)", tone: "warn" });
  assert.equal(m.commentActionLabel("hide_failed", "abusive").tone, "warn");           // a failed hide must not look like a success
  assert.deepEqual(plain(m.commentActionLabel("public_and_private", "ok")), { text: "رد علني + رد خاص", tone: "ok" });
  assert.equal(m.commentActionLabel("ignored", "hourly_cap").text, "تجاهل — تجاوز حد الساعة");
  assert.equal(m.commentActionLabel("ignored", "something_new").text, "تجاهل — something_new");   // unknown reasons are shown, not hidden
});

test("comment prefs: defaults are conservative and reconnect/diagnostics/test-message use the right endpoints", async () => {
  const r = recorder({ ok: true, detail: "x", items: [] });
  const m = load(r.api);
  assert.equal(m.DEFAULT_SOCIAL_COMMENTS.enabled, false);
  assert.equal(m.DEFAULT_SOCIAL_COMMENTS.allow_price_in_private, false);
  assert.equal(m.DEFAULT_SOCIAL_COMMENTS.auto_hide_abusive, true);
  await m.fetchFacebookDiagnostics();
  await m.fetchCommentActivity();
  await m.sendWhatsappTestMessage("+966501234567");
  assert.deepEqual(r.calls.map((c) => c.slice(0, 2)), [["get", "/settings/channels/facebook/diagnostics"], ["get", "/settings/comments/activity"], ["post", "/settings/channels/whatsapp/test-message"]]);
  assert.deepEqual(plain(r.calls[2][2]), { to: "+966501234567" });
});

test("plan labels never show the placeholder «الاقتصادية» plan; legacy tiers map to the real plans", () => {
  const m = load(recorder().api);
  assert.equal(m.TIER_LABELS.starter, "الأساسية (Starter)");
  assert.equal(m.TIER_LABELS.pro, "الاحترافية (Pro)");
  assert.equal(m.TIER_LABELS.business, "الأعمال (Business)");
  for (const label of Object.values(plain(m.TIER_LABELS))) assert.ok(!label.includes("الاقتصادية"), label);
  assert.equal(m.TIER_LABELS.economic, m.TIER_LABELS.starter);
  assert.equal(m.TIER_LABELS.professional, m.TIER_LABELS.pro);
});
