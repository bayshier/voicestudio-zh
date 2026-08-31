import { describe, expect, it, vi } from 'vitest';

const { toastErrorWithReport } = vi.hoisted(() => ({
  toastErrorWithReport: vi.fn(),
}));

vi.mock('./errorToast', () => ({ toastErrorWithReport }));

import { installGlobalErrorHandlers } from './globalErrorHandlers';

function dispatchUnhandledRejection(reason) {
  const event = new Event('unhandledrejection');
  Object.defineProperty(event, 'reason', { value: reason });
  window.dispatchEvent(event);
}

describe('global unhandled rejection reporting', () => {
  it('ignores a named AbortError while still surfacing a real rejection', () => {
    installGlobalErrorHandlers();

    const cancelled = new DOMException('BodyStreamBuffer was aborted', 'AbortError');
    dispatchUnhandledRejection(cancelled);
    expect(toastErrorWithReport).not.toHaveBeenCalled();

    const failure = new Error('waveform request failed');
    dispatchUnhandledRejection(failure);
    expect(toastErrorWithReport).toHaveBeenCalledOnce();
    expect(toastErrorWithReport.mock.calls[0][1]).toBe(failure);
  });
});
