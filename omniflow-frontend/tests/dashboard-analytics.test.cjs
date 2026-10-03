/** Analytics client params, CSV formula-injection guard, and duration formatting. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(rel, requireImpl = () => ({})) {
  const source = readFileSync(join(__dirname, "..", rel), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: requireImpl, Intl, Date, Math, Number, String });
  return exports;
}

test("analytics client sends only the params that apply", async () => {
  const calls = [];
  const api = load("src/lib/api/dashboardAnalytics.ts", () => ({
    apiClient: { get: async (url, cfg) => { calls.push({ url, params: cfg.params }); return { data: { ok: 1 } }; } },
  }));
  await api.fetchDashboardAnalytics({ range: "30d", from: "2026-01-01", to: "2026-01-02", channel: "instagram" });
  await api.fetchDashboardAnalytics({ range: "custom", from: "2026-09-01", to: "2026-09-07", refresh: true });
  assert.equal(calls[0].url, "/dashboard/analytics");
  assert.deepEqual({ ...calls[0].params }, { range: "30d", channel: "instagram" }); // from/to ignored for presets
  assert.deepEqual({ ...calls[1].params }, { range: "custom", from: "2026-09-01", to: "2026-09-07", refresh: true });
});

test("CSV cells neutralise spreadsheet formulas and escape quotes", () => {
  const csv = load("src/components/dashboard/exportCsv.ts", () => ({ CHANNEL_LABELS: {} }));
  assert.equal(csv.csvCell("=HYPERLINK(\"x\")"), "\"'=HYPERLINK(\"\"x\"\")\"");
  assert.equal(csv.csvCell("+966500000000"), "\"'+966500000000\"");
  assert.equal(csv.csvCell("عادي"), "\"عادي\"");
  assert.equal(csv.csvCell(null), "\"\"");
  assert.equal(csv.csvCell(12.5), "\"12.5\"");
});

test("duration formatting picks seconds, minutes, hours and handles null", () => {
  const f = load("src/components/dashboard/format.ts");
  const digits = (s) => s.replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d));
  assert.equal(f.fmtDuration(null), "—");
  assert.equal(digits(f.fmtDuration(45)), "45 ث");
  assert.equal(digits(f.fmtDuration(300)), "5 د");
  assert.equal(digits(f.fmtDuration(5400)), "1 س 30 د");
  assert.equal(digits(f.fmtDuration(7200)), "2 س");
});

test("reports client: search is trimmed/forwarded and revenue by_type is normalised", async () => {
  const seen = [];
  const api = load("src/lib/api/reports.ts", () => ({
    apiClient: { get: async (url, cfg) => { seen.push({ url, params: cfg.params }); return {
      data: url.includes("analytics") ? { series: [], by_type: [{ report_type: "deed_check_29", total_revenue: "29", count: 1 }] } : { items: [], total: 0 },
    }; } },
  }));
  await api.fetchReports({ search: "  REF_29 " });
  await api.fetchReports({ search: "   " });
  assert.equal(seen[0].params.search, "REF_29");
  assert.equal(seen[1].params.search, undefined);
  const rev = await api.fetchRevenueAnalytics("weekly");
  assert.deepEqual(JSON.parse(JSON.stringify(rev.by_type)), [{ report_type: "deed_check_29", total_revenue: 29, count: 1 }]);
  const old = await load("src/lib/api/reports.ts", () => ({ apiClient: { get: async () => ({ data: { series: [] } }) } })).fetchRevenueAnalytics();
  assert.deepEqual(JSON.parse(JSON.stringify(old.by_type)), []); // older backend without by_type
});
