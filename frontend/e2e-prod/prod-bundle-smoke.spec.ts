import { test, expect } from '@playwright/test';

/**
 * The app must RENDER when built for production.
 *
 * v0.3.22 shipped a black screen: a minifier temporal-dead-zone reorder threw
 * before React mounted (`Cannot access 'logs' before initialization`), so the
 * window came up empty. It only ever bit the MINIFIED bundle — the dev server
 * and the entire e2e suite run un-minified, so every check passed and it
 * shipped (#1178).
 *
 * These tests run against the real `dist/` output. The core assertion is
 * deliberately dumb and structural — "did anything mount?" — because that is
 * the one thing a pre-render crash always breaks, whatever its cause.
 */

// Errors that are expected in a bundle served without a backend / Tauri host.
// Keep this list TIGHT: every entry is a hole in the gate.
const IGNORABLE = [
  /Failed to load resource/i, // no backend on :3900 in this harness
  /net::ERR_/i, // ditto — network fetches to the API
  /ERR_CONNECTION_REFUSED/i,
  /__TAURI__/i, // not running inside the Tauri shell
  /favicon/i,
  // The app opens ws://…/ws/events at startup. With no backend, the static
  // preview server answers every path with the SPA's 200, so the WebSocket
  // upgrade is refused ("Unexpected response code: 200"). That's the absent
  // backend, not a render crash — the same class as the ERR_ entries above.
  /WebSocket connection to .* failed/i,
  /\/ws\//i,
];

function isIgnorable(text: string): boolean {
  return IGNORABLE.some((re) => re.test(text));
}

test('production bundle mounts the app (no blank screen)', async ({ page }) => {
  const fatal: string[] = [];
  // A pre-render crash surfaces as a pageerror, not a console.error.
  page.on('pageerror', (err) => fatal.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !isIgnorable(msg.text())) {
      fatal.push(`console.error: ${msg.text()}`);
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });

  // The actual anti-blank assertion: something must mount into #root.
  const root = page.locator('#root');
  await expect(root).toHaveCount(1);
  await expect
    .poll(() => root.evaluate((el) => el.childElementCount), {
      message:
        'production bundle rendered NOTHING into #root — this is the black-screen ' +
        'failure mode (#1178). Check for a crash before React mounts.',
      timeout: 20_000,
    })
    .toBeGreaterThan(0);

  // And it must not have thrown on the way there. A TDZ reorder throws a
  // ReferenceError, which is precisely what this catches.
  expect(fatal, `production bundle raised fatal errors:\n${fatal.join('\n')}`).toEqual([]);
});

test('production bundle renders visible content, not an empty shell', async ({ page }) => {
  // Guards the degenerate pass where #root gets a child that paints nothing.
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect
    .poll(
      () => page.locator('#root').evaluate((el) => (el as HTMLElement).innerText.trim().length),
      {
        message: 'production bundle mounted an element tree with no visible text',
        timeout: 20_000,
      },
    )
    .toBeGreaterThan(0);
});
