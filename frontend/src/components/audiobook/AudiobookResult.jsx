import React from 'react';
import { Download } from 'lucide-react';
import { audioUrl } from '../../api/generate';
import { Button } from '@/components/ui/button.tsx';
import { downloadMedia } from '../../utils/mediaDownload';

/**
 * The finished-render result: the "ready" note (with cached/failed chapter
 * summaries), the player, and the Download button. Extracted from AudiobookTab
 * to keep that page under the line lint; behaviour is unchanged.
 *
 * Download goes through the shared `downloadMedia` util (#1218), NOT a raw
 * `<a href={audioUrl(output)} download>`. In the Tauri WebView that anchor does
 * not download an m4b the engine can play — WebKit navigates the whole webview
 * to the file and plays it fullscreen, hijacking the app. `downloadMedia` uses
 * the native save dialog + a server-side copy from OUTPUTS_DIR instead.
 */
export default function AudiobookResult({ t, output, done }) {
  const filename = output.split('/').pop();
  return (
    <div className="audiobook-done">
      <div style={{ marginBottom: 8 }}>✅ {t('audiobook.ready')}</div>
      {done && done.failed_chapters.length > 0 && (
        <div className="muted" style={{ marginBottom: 8 }}>
          {t('audiobook.failed_note', { count: done.failed_chapters.length })}
        </div>
      )}
      {done && done.cached_chapters > 0 && (
        <div className="muted" style={{ marginBottom: 8 }}>
          {t('audiobook.cached_note', { count: done.cached_chapters })}
        </div>
      )}
      <audio controls src={audioUrl(output)} style={{ width: '100%' }} />
      <div style={{ marginTop: 8 }}>
        <Button
          variant="subtle"
          size="omniMd"
          onClick={() => downloadMedia(audioUrl(output), filename, { sourceFilename: filename })}
        >
          <Download size={14} /> {t('audiobook.download')}
        </Button>
      </div>
    </div>
  );
}
