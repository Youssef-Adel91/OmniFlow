import { defineConfig, devices } from '@playwright/test';

/**
 * playwright.config.ts — OmniFlow AI E2E Test Configuration
 *
 * Targets: http://localhost:3000  (Next.js dev server)
 *
 * Key design decisions:
 *   - navigationTimeout set high (30 s) to allow slow cold-start page loads
 *   - actionTimeout 15 s for UI interactions
 *   - No webServer auto-start (we assume the dev server is already running)
 *   - Screenshots + video on failure for post-mortem analysis
 *   - Workers = 1 so tests run sequentially (shared auth state is cookie-based)
 */
export default defineConfig({
  testDir: './e2e',

  /* Run tests sequentially – auth state is per-session */
  fullyParallel: false,
  workers: 1,

  /* Fail the build on CI if you accidentally left test.only in source code. */
  forbidOnly: !!process.env.CI,

  /* Retry twice on CI to handle flaky SSE timing */
  retries: process.env.CI ? 2 : 1,

  /* Rich HTML reporter + terminal summary */
  reporter: [
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    ['list'],
  ],

  /* Global test settings */
  use: {
    /* Base URL for all goto() calls */
    baseURL: 'http://localhost:3000',

    /* Viewport that mimics a typical agent workstation */
    viewport: { width: 1440, height: 900 },

    /* Screenshot on failure */
    screenshot: 'only-on-failure',

    /* Short video clip on failure */
    video: 'retain-on-failure',

    /* Generous but bounded timeouts */
    navigationTimeout: 30_000,
    actionTimeout:     15_000,

    /* Locale that matches the Arabic UI we're testing */
    locale: 'ar-SA',

    /* Include Arabic fonts to avoid missing-glyph issues in screenshots */
    timezoneId: 'Asia/Riyadh',

    /* Trace on first retry so you can debug in the Playwright trace viewer */
    trace: 'on-first-retry',

    /* Trust all self-signed certs in case nginx/local TLS is involved */
    ignoreHTTPSErrors: true,
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        /* Run headed if the env var HEADED=1 is set, otherwise headless */
        headless: process.env.HEADED !== '1',
      },
    },
  ],

  /* Output folder for test artifacts */
  outputDir: 'test-results',
});
