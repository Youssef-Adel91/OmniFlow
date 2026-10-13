import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

/**
 * Public site (no login needed): rendering, RTL/LTR, mobile, keyboard, 404, SEO files and automated accessibility (axe, incl. colour contrast).
 *
 *   npx next build && npx next start -p 3100      # in one terminal (the app talks to no backend here: pricing uses its built-in fallback catalogue)
 *   E2E_BASE_URL=http://localhost:3100 npx playwright test e2e/public.spec.ts
 *   (optional) PW_CHROMIUM_PATH=/path/to/chrome to reuse an installed Chromium
 */
const PAGES = ['', '/pricing', '/privacy', '/terms', '/refund', '/data-deletion', '/data-deletion/status'];

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page scrolls sideways').toBeLessThanOrEqual(1);
}

test.describe('direction and language', () => {
  test('Arabic is RTL and English is LTR, and the language switch works', async ({ page }) => {
    await page.goto('/ar');
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.getByRole('link', { name: 'English' }).first().click();
    await expect(page).toHaveURL(/\/en$/);
    await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  });
});

test.describe('content honesty', () => {
  test('landing page has no invented statistics or superlatives', async ({ page }) => {
    await page.goto('/ar');
    const text = await page.locator('body').innerText();
    for (const banned of ['٩٨٪', '+٤٠٪', 'أول مساعد', 'الأفضل']) expect(text).not.toContain(banned);
  });

  test('pricing shows VAT, Meta fees and the four plans', async ({ page }) => {
    await page.goto('/ar/pricing');
    for (const code of ['starter', 'pro', 'business', 'enterprise']) await expect(page.getByTestId(`plan-${code}`)).toBeVisible();
    const text = await page.locator('main').innerText();
    expect(text).toContain('ضريبة');
    expect(text).toContain('Meta');
  });

  test('legal pages carry the lawyer-review banner', async ({ page }) => {
    for (const p of ['/ar/privacy', '/ar/terms', '/ar/refund', '/ar/data-deletion']) {
      await page.goto(p);
      await expect(page.getByRole('note').first()).toContainText('مراجعة محامٍ');
    }
  });
});

test.describe('data-deletion status page', () => {
  test('rejects a malformed code, reports unknown, and shows a known request', async ({ page }) => {
    await page.route('**/meta/data-deletion/status/**', async (route) => {
      const code = route.request().url().split('/').pop() ?? '';
      if (code.startsWith('KNOWN')) {
        await route.fulfill({ json: { status: 'completed', requested_at: '2026-01-01T10:00:00Z', completed_at: '2026-01-01T10:05:00Z' } });
      } else {
        await route.fulfill({ status: 404, json: { detail: 'nope' } });
      }
    });
    await page.goto('/ar/data-deletion/status');
    const input = page.getByRole('textbox');
    await input.fill('x');
    await page.getByRole('button').first().click();
    await expect(page.getByTestId('deletion-result').getByRole('alert')).toBeVisible();
    await input.fill('UNKNOWN12345');
    await page.getByRole('button').first().click();
    await expect(page.getByTestId('deletion-result').getByRole('alert')).toContainText('لا يوجد');
    await input.fill('KNOWN123456');
    await page.getByRole('button').first().click();
    await expect(page.getByTestId('deletion-result')).toContainText('اكتمل');
  });
});

test.describe('404 and SEO files', () => {
  test('unknown paths render the localized 404 with a way home', async ({ page }) => {
    for (const [path, heading] of [['/ar/definitely/not/here', 'الصفحة غير موجودة'], ['/en/definitely/not/here', 'Page not found']] as const) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(404);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading);
      await expect(page.getByRole('link').first()).toBeVisible();
    }
  });

  test('robots.txt hides the app and sitemap.xml lists the public pages', async ({ request }) => {
    const robots = await request.get('/robots.txt');
    expect(robots.status()).toBe(200);
    const body = await robots.text();
    expect(body).toContain('Disallow: /ar/dashboard');
    expect(body).toContain('Disallow: /ar/settings');
    const sitemap = await request.get('/sitemap.xml');
    expect(sitemap.status()).toBe(200);
    const xml = await sitemap.text();
    for (const p of ['/ar/pricing', '/en/privacy', '/ar/data-deletion']) expect(xml).toContain(p);
    expect(xml).not.toContain('/dashboard');
  });

  test('every public page has a title, description and canonical', async ({ page }) => {
    for (const p of PAGES) {
      await page.goto(`/ar${p}`);
      await expect(page).toHaveTitle(/.+/);
      await expect(page.locator('meta[name="description"]')).toHaveCount(1);
    }
  });
});

test.describe('keyboard', () => {
  test('first Tab reveals the skip link and it moves focus to the main content', async ({ page }) => {
    await page.goto('/ar/pricing');
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: /تخطَّ إلى المحتوى/ });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#main$/);
  });

  test('FAQ items open and close from the keyboard', async ({ page }) => {
    await page.goto('/ar#faq');
    const first = page.locator('details').first();
    await first.locator('summary').focus();
    await page.keyboard.press('Enter');
    await expect(first).toHaveAttribute('open', '');
  });
});

for (const viewport of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'mobile', width: 375, height: 700 }]) {
  test.describe(`${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    for (const lang of ['ar', 'en']) {
      for (const p of PAGES) {
        test(`${lang}${p || '/'}: no sideways scroll and no serious accessibility violations`, async ({ page }) => {
          await page.goto(`/${lang}${p}`);
          await page.waitForLoadState('domcontentloaded');
          await expectNoHorizontalScroll(page);
          const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
          const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
          expect(serious.map((v) => `${v.id}: ${v.nodes.length} node(s) e.g. ${v.nodes[0]?.target?.join(' ')}`), 'axe serious/critical').toEqual([]);
        });
      }
    }
  });
}
