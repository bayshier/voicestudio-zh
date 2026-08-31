import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';

// PRODUCTION-BUNDLE smoke gate. The regular e2e config (playwright.config.ts)
// drives the Vite DEV server, which is un-minified — so a bug that exists ONLY
// in the minified release bundle passes every check and ships. That is exactly
// how v0.3.22 went out with a black screen: a minifier temporal-dead-zone
// reorder threw before React mounted, leaving an empty #root. Dev runs and the
// whole e2e suite never saw it (#1178).
//
// This config closes that hole: it BUILDS the app and serves the real `dist/`
// through `vite preview`, so the bytes under test are the bytes we ship.
// Separate port from the dev server so both can run without colliding.
const PORT = Number(process.env.E2E_PROD_PORT || 4173);

// PLAYWRIGHT_CHROMIUM wins; else a system chromium if it's actually there
// (CI); else undefined, which lets Playwright use its bundled browser.
const SYSTEM_CHROMIUM = '/usr/bin/chromium';
const browserPath =
  process.env.PLAYWRIGHT_CHROMIUM || (existsSync(SYSTEM_CHROMIUM) ? SYSTEM_CHROMIUM : undefined);

export default defineConfig({
  testDir: './e2e-prod',
  timeout: 120_000, // includes the production build
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0, // a blank screen must never be "flaky-passed" away
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    headless: true,
    trace: 'retain-on-failure',
    // Use an explicit browser when one is named (CI installs a system
    // chromium), otherwise fall back to Playwright's own download. The dev
    // e2e config hardcodes /usr/bin/chromium, which simply doesn't exist on
    // Windows/macOS — this gate has to be runnable on a contributor's laptop
    // too, or it won't get run before release.
    ...(browserPath ? { launchOptions: { executablePath: browserPath } } : {}),
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // Build then serve the real production output. `--strictPort` so a port
    // clash fails loudly instead of silently testing some other server.
    command: `bun run build && bun x vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // CI must always build fresh — the whole point is testing the bytes we
    // ship. Locally, reuse a `vite preview` you already have up so iterating
    // doesn't pay a full production build every run.
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
