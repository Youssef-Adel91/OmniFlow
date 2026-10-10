/** Broadcast client contracts + pure helpers (Riyadh time, template preview, error shapes). */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/broadcasts.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, Intl, Date, Number, Array, Object, JSON }); // eslint-disable-line
  return exports;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const recorder = (data = {}) => {
  const calls = [];
  return { calls, api: { get: async (u, c) => (calls.push(["get", u, c]), { data }), post: async (u, b) => (calls.push(["post", u, b]), { data }) } };
};

test("a wall-clock datetime-local value is interpreted as Asia/Riyadh (UTC+3)", () => {
  const m = load(recorder().api);
  assert.equal(m.riyadhInputToIso("2026-10-12T21:30"), "2026-10-12T21:30:00+03:00");
  assert.equal(new Date(m.riyadhInputToIso("2026-10-12T21:30")).toISOString(), "2026-10-12T18:30:00.000Z");
  assert.throws(() => m.riyadhInputToIso("tomorrow"));
});

test("template preview fills {{n}} from variables with sample {name}/{city}, leaving unfilled ones visible", () => {
  const m = load(recorder().api);
  assert.equal(m.renderTemplatePreview("مرحباً {{1}}، عرض في {{2}}", ["{name}", ""]), "مرحباً أحمد، عرض في {{2}}");
  assert.equal(m.renderTemplatePreview("{{1}} - {{1}}", ["{city}"]), "الرياض - الرياض");
});

test("campaign id comes from campaign_id (the old client read `id` and lost it)", () => {
  const m = load(recorder().api);
  const b = m.normalizeBroadcast({ campaign_id: "c-1", title: "t", target_audience: { segment: "vip", tags: ["a"] }, template_params: ["{name}"], status: "scheduled" });
  assert.deepEqual(plain({ id: b.id, seg: b.target_audience.segment, tags: b.target_audience.tags, p: b.template_params, rate: b.send_rate_per_second }),
    { id: "c-1", seg: "vip", tags: ["a"], p: ["{name}"], rate: 20 });
  assert.equal(m.normalizeBroadcast({ id: "x", target_audience: "all_vip" }).target_audience.segment, "all_vip");
});

test("schedule sends the confirmed reach, preview forwards tags/city/sources, create carries the template", async () => {
  const { api, calls } = recorder({ campaign_id: "c-1" });
  const m = load(api);
  await m.scheduleBroadcast("c-1", { scheduledAt: "2026-10-12T21:30:00+03:00", confirmedRecipients: 42 });
  await m.scheduleBroadcast("c-1", {});
  await m.previewAudience({ segment: "vip", tags: ["a", "b"], city: " جدة ", sources: ["instagram"] });
  await m.createBroadcast({ title: "x", target_audience: { segment: "all" }, meta_template_id: "promo", template_language: "ar", template_params: ["{name}"], send_rate_per_second: 20 });
  assert.deepEqual(plain(calls[0].slice(1)), ["/broadcasts/c-1/schedule", { scheduled_at: "2026-10-12T21:30:00+03:00", confirmed_recipients: 42 }]);
  assert.deepEqual(plain(calls[1].slice(1)), ["/broadcasts/c-1/schedule", {}]);
  assert.deepEqual(plain(calls[2][2].params), { target_audience: "vip", tags: "a,b", city: "جدة", sources: "instagram" });
  assert.equal(calls[3][2].meta_template_id, "promo");
});

test("structured errors keep their code and the current audience size (AUDIENCE_CHANGED)", () => {
  const m = load(recorder().api);
  const e = (detail) => ({ response: { data: { detail } } });
  assert.deepEqual(plain(m.broadcastErrorInfo(e({ code: "AUDIENCE_CHANGED", message: "تغيّر", current_recipients: 7 }))), { message: "تغيّر", code: "AUDIENCE_CHANGED", currentRecipients: 7 });
  assert.equal(m.broadcastErrorInfo(e("نص")).message, "نص");
  assert.equal(m.broadcastErrorInfo(new Error("net")).message, "net");
});

test("segment labels describe tags/city/channels", () => {
  const m = load(recorder().api);
  assert.equal(m.segmentLabel({ segment: "vip", tags: ["a"], city: "جدة", sources: ["instagram"] }), "عملاء VIP · وسوم: a · مدينة: جدة · قنوات: instagram");
  assert.equal(m.segmentLabel("dormant"), "dormant");
  assert.equal(m.segmentLabel(null), "كل العملاء المؤهلين");
});
