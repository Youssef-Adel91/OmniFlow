/** Billing client contracts + the pure helpers behind the banner, plan cards and usage bar. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(apiClient) {
  const source = readFileSync(join(__dirname, "../src/lib/api/billing.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient }), Error, JSON, Array, Object, Math, Number, String }); // eslint-disable-line
  return exports;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const st = (o) => ({ mode: "full", status: "active", reason: null, banner: null, days_left: null, plan_code: "economic", is_exempt: false, ...o });
const plan = (code, m, y) => ({ code, name_ar: code, monthly: { subtotal_halalas: m, total_halalas: m }, yearly: { subtotal_halalas: y, total_halalas: y }, limits: {}, is_placeholder: true });

test("money is shown in riyals with two decimals", () => {
  const m = load({});
  assert.equal(m.formatSar(34385), "343.85 ر.س");
  assert.equal(m.formatSar(0), "0.00 ر.س");
  assert.equal(m.formatSar(79900000), "799,000.00 ر.س");
});

test("banner: nothing for healthy/exempt, escalating copy for trial → past due → blocked", () => {
  const m = load({});
  assert.equal(m.bannerFor(null), null);
  assert.equal(m.bannerFor(st({})), null);
  assert.equal(m.bannerFor(st({ mode: "blocked", status: "suspended", is_exempt: true })), null);
  const trial = m.bannerFor(st({ status: "trial", banner: "trial_ending", days_left: 2 }));
  assert.deepEqual(plain([trial.tone, trial.cta]), ["info", "اختر باقة"]);
  assert.match(trial.text, /خلال 2 أيام/);
  assert.match(m.bannerFor(st({ status: "trial", banner: "trial_ending", days_left: 1 })).text, /خلال 1 يوم/);
  assert.match(m.bannerFor(st({ status: "trial", banner: "trial_ending", days_left: 0 })).text, /اليوم/);
  const due = m.bannerFor(st({ mode: "grace", status: "past_due", banner: "past_due", days_left: 3 }));
  assert.deepEqual(plain([due.tone, due.cta]), ["warn", "ادفع الآن"]);
  assert.match(due.text, /بعد 3 أيام/);
  assert.match(m.bannerFor(st({ mode: "blocked", status: "suspended", reason: "suspended" })).text, /موقوف لعدم السداد.*وضع القراءة/);
  assert.match(m.bannerFor(st({ mode: "blocked", status: "trial", reason: "trial_expired" })).text, /انتهت الفترة التجريبية/);
  assert.equal(m.bannerFor(st({ mode: "blocked", status: "cancelled", reason: "cancelled" })).tone, "danger");
});

test("usage percent is capped and absent without a limit", () => {
  const m = load({});
  assert.equal(m.usagePercent(500, 1000), 50);
  assert.equal(m.usagePercent(1500, 1000), 100);
  assert.equal(m.usagePercent(5, null), null);
  assert.equal(m.usagePercent(5, 0), null);
});

test("yearly saving and plan-card actions", () => {
  const m = load({});
  assert.equal(m.yearlySavingPercent(plan("p", 10000, 100000)), 17);
  assert.equal(m.yearlySavingPercent(plan("p", 10000, 120000)), 0);
  const rank = { economic: 0, professional: 1, enterprise: 2 };
  const cur = (o) => ({ code: "professional", interval: "monthly", status: "active", is_exempt: false, ...o });
  assert.deepEqual(plain(m.planAction(cur({}), plan("professional"), "monthly", rank)), { label: "الباقة الحالية — جدّد مبكرًا", disabled: false });
  assert.equal(m.planAction(cur({}), plan("enterprise"), "monthly", rank).label, "ترقية الآن");
  assert.equal(m.planAction(cur({}), plan("economic"), "monthly", rank).label, "التغيير عند التجديد القادم");
  assert.equal(m.planAction(cur({}), plan("enterprise"), "yearly", rank).label, "التغيير عند التجديد القادم");
  assert.equal(m.planAction(cur({ status: "trial" }), plan("economic"), "monthly", rank).label, "اشترك");
  assert.equal(m.planAction(cur({ status: "suspended" }), plan("economic"), "monthly", rank).label, "جدّد بهذه الباقة");
  assert.deepEqual(plain(m.planAction(cur({ is_exempt: true }), plan("economic"), "monthly", rank)), { label: "حساب داخلي", disabled: true });
});

test("API calls hit the right endpoints and unwrap items", async () => {
  const calls = [];
  const api = {
    get: async (u) => (calls.push(["get", u]), { data: u.includes("invoices") || u.includes("transactions") && !u.includes("/t1") ? { items: [{ x: 1 }] } : { ok: true } }),
    post: async (u, b) => (calls.push(["post", u, b]), { data: { action: "checkout", checkout_url: "https://pay" } }),
  };
  const m = load(api);
  assert.deepEqual(plain(await m.fetchInvoices()), [{ x: 1 }]);
  assert.deepEqual(plain(await m.fetchTransactions()), [{ x: 1 }]);
  const r = await m.startCheckout("professional", "yearly");
  assert.equal(r.checkout_url, "https://pay");
  assert.deepEqual(plain(calls.at(-1)), ["post", "/billing/checkout", { plan_code: "professional", interval: "yearly" }]);
  await m.cancelSubscription();
  assert.equal(calls.at(-1)[1], "/billing/cancel");
});

test("server error messages are surfaced", () => {
  const m = load({});
  assert.equal(m.billingError({ response: { data: { detail: { message: "الدفع غير مفعّل" } } } }, "fb"), "الدفع غير مفعّل");
  assert.equal(m.billingError(new Error("x"), "fb"), "fb");
});

test("plan card lines come from the limits; enterprise is contact-us", () => {
  const m = load({});
  const pro = { contact_sales: false, limits: { messages_per_month: 8000, seats: 5, whatsapp_numbers: 1, properties: 1000, contacts: 10000, campaigns: true } };
  assert.deepEqual(plain(m.planFeatureLines(pro)), ["8,000 رد ذكاء اصطناعي / شهر", "5 مقاعد", "1 رقم واتساب", "1,000 عقار", "10,000 جهة اتصال", "حملات تسويقية (بموافقة العملاء)"]);
  const starter = { limits: { messages_per_month: 1500, seats: 2, campaigns: false } };
  assert.ok(m.planFeatureLines(starter).includes("الحملات التسويقية غير متاحة"));
  assert.ok(m.planFeatureLines({ contact_sales: true, limits: {} }).includes("تواصل معنا للحصول على عرض"));
  assert.equal(m.planFeatureLines({ limits: { channels: 1 } })[0], "1 قناة");
});

test("top-up labels and quota bar tones", () => {
  const m = load({});
  assert.equal(m.topupLabel(5000), "5,000 رد");
  assert.equal(m.quotaTone(10, 100), "ok");
  assert.equal(m.quotaTone(80, 100), "warn");
  assert.equal(m.quotaTone(100, 100), "warn");
  assert.equal(m.quotaTone(101, 100), "over");
});

test("new billing calls hit the documented endpoints", async () => {
  const calls = [];
  const api = {
    get: async (u, o) => { calls.push(["GET", u, o]); return { data: { items: [], packs: [], addons: [], credits: { remaining: 0, packs: [] } } }; },
    post: async (u, b) => { calls.push(["POST", u, b]); return { data: { checkout_url: "https://pay.example/1" } }; },
    put: async (u, b) => { calls.push(["PUT", u, b]); return { data: b }; },
    delete: async (u, o) => { calls.push(["DELETE", u, o]); return { data: { addons: { seats: 1, whatsapp_numbers: 0 } } }; },
  };
  const m = load(api);
  await m.fetchTopupCatalog();
  await m.buyTopup("replies_5k");
  await m.buyAddon("seat", 2);
  await m.removeAddon("seat", 1);
  await m.saveQuotaSettings({ quota_exhausted_action: "stop" });
  await m.fetchCreditNotes();
  assert.deepEqual(plain(calls.map((c) => [c[0], c[1]])), [
    ["GET", "/billing/topup-packs"], ["POST", "/billing/topups"], ["POST", "/billing/addons"], ["DELETE", "/billing/addons/seat"], ["PUT", "/billing/quota-settings"], ["GET", "/billing/credit-notes"],
  ]);
  assert.deepEqual(plain(calls[1][2]), { pack_code: "replies_5k" });
  assert.deepEqual(plain(calls[2][2]), { code: "seat", quantity: 2 });
  assert.deepEqual(plain(calls[3][2]), { params: { quantity: 1 } });
});
