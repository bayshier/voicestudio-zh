/**
 * openExternal — open a URL in the user's default browser.
 *
 * In a Tauri desktop app `window.open()` is blocked by the webview.
 * This helper uses `@tauri-apps/plugin-opener` when available and
 * falls back to `window.open()` for browser-based dev mode.
 */

const isTauri =
  typeof window !== 'undefined' &&
  !!((window as any).__TAURI_INTERNALS__ || (window as any).__TAURI__);

let _openUrl: ((url: string) => Promise<void>) | null = null;

/**
 * Open an external URL in the system default browser.
 * @param {string} url — the URL to open
 */
export async function openExternal(url: string) {
  if (isTauri) {
    try {
      if (!_openUrl) {
        const mod = await import('@tauri-apps/plugin-opener');
        _openUrl = mod.openUrl as (url: string) => Promise<void>;
      }
      await _openUrl(url);
      return;
    } catch (err) {
      console.warn('[openExternal] Tauri opener failed, falling back:', err);
    }
  }
  // Fallback for browser dev mode
  window.open(url, '_blank', 'noopener,noreferrer');
}
