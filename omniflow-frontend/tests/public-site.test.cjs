/** Public-site helpers: origin comes from env, sitemap/robots, catalog maths, and a guard that the landing copy has no invented claims. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function load(rel, deps = {}) {
  const source = readFileSync(join(__dirname, "../src", rel), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: (m) => deps[m] ?? {}, process: { env: {} }, URL, Math, Number, Array, Object, String, JSON, Intl }); // eslint-disable-line
  return exports;
}

test("siteOrigin uses PUBLIC_BASE_URL, falls back to NEXT_PUBLIC_BASE_URL, never invents a domain", () => {
  const s = load("lib/public/site.ts");
  assert.equal(s.siteOrigin({ PUBLIC_BASE_URL: "https://app.example.com/some/path/" }), "https://app.example.com");
  assert.equal(s.siteOrigin({ NEXT_PUBLIC_BASE_URL: "https://x.test" }), "https://x.test");
  assert.equal(s.siteOrigin({}), "");
  assert.equal(s.siteOrigin({ PUBLIC_BASE_URL: "not a url" }), "");
  assert.equal(s.absoluteUrl("/ar/pricing", {}), "/ar/pricing");
  assert.equal(s.absoluteUrl("ar", { PUBLIC_BASE_URL: "https://x.test" }), "https://x.test/ar");
  assert.equal(s.asLocale("en"), "en");
  assert.equal(s.asLocale("fr"), "ar");
});

test("catalog maths: VAT on top, yearly saving", () => {
  const c = load("lib/public/catalog.ts", { "@/lib/api/client": { API_BASE_URL: "http://x" } });
  assert.equal(c.withVat(79900, 1500), 91885);
  assert.equal(c.sarWhole(79900), "799");
  assert.equal(c.sar(91885), "918.85");
  assert.equal(c.yearlySaving(100, 1000), 17);
  assert.equal(c.yearlySaving(0, 0), 0);
});

test("fallback catalog prices stay VAT-exclusive and yearly = 10x monthly", () => {
  const c = load("lib/public/catalog.ts", { "@/lib/api/client": { API_BASE_URL: "http://x" } });
  for (const p of c.FALLBACK_CATALOG.plans.filter((p) => !p.contact_sales)) assert.equal(p.yearly_halalas, p.monthly_halalas * 10, p.code);
});

test("public copy: no invented statistics or superlatives, mentions VAT and Meta fees, draft banner for legal pages", () => {
  const src = readFileSync(join(__dirname, "../src/lib/public/copy.ts"), "utf8");
  assert.doesNotMatch(src, /98٪|\+40|أول مساعد|الأفضل|#1|number one/i);
  assert.match(src, /ضريبة/);
  assert.match(src, /Meta/);
  assert.match(src, /مراجعة محامٍ/);
});
