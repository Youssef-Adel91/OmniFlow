/** Regression checks for report list/detail and revenue API contracts. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function clientFor(get) {
  const source = readFileSync(join(__dirname, "../src/lib/api/reports.ts"), "utf8");
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: () => ({ apiClient: { get } }) });
  return exports;
}

test("report list preserves backend prices and availability without a file URL", async () => {
  const client = clientFor(async (url, { params }) => {
    assert.equal(url, "/reports");
    assert.equal(params.date_from, "2026-09-23");
    assert.equal(params.date_to, "2026-09-23");
    return { data: { items: [{ report_id: "r1", price_sar: 29, status: "ready" }], total: 1 } };
  });
  const page = await client.fetchReports({ date_from: "2026-09-23", date_to: "2026-09-23" });
  assert.equal(page.items[0].id, "r1");
  assert.equal(page.items[0].price, 29);
  assert.equal(page.items[0].status, "ready");
  assert.equal(page.items[0].s3_url, null);
});

test("revenue chart consumes the backend series including zero buckets", async () => {
  const client = clientFor(async () => ({ data: { series: [
    { month: "2026-08", total_revenue: 0, count: 0 },
    { month: "2026-09", total_revenue: 44, count: 2 },
  ] } }));
  const chart = await client.fetchRevenueAnalytics();
  assert.equal(chart.items.length, 2);
  assert.equal(chart.items[0].total_revenue, 0);
  assert.equal(chart.items[1].total_revenue, 44);
});

test("report detail uses each fresh download response and preserves a zero price", async () => {
  let calls = 0;
  const client = clientFor(async () => ({ data: { report_id: "r1", price_sar: 0, status: "ready", s3_url: `signed-${++calls}` } }));
  assert.equal((await client.fetchReport("r1")).s3_url, "signed-1");
  const report = await client.fetchReport("r1");
  assert.equal(report.s3_url, "signed-2");
  assert.equal(report.price, 0);
});
