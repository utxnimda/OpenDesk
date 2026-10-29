import { describe, expect, it, vi } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import SidebarDivider from '../frontend/SidebarDivider.vue';
import { createSidebarWidthSaver, DEFAULT_SIDEBAR_WIDTH, keyboardSidebarWidth, savedSidebarWidth, sidebarBounds, visibleSidebarWidth } from '../frontend/sidebarResize';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
async function tick() { await Promise.resolve(); await Promise.resolve(); }

describe('resizable workspace sidebar', () => {
  it('keeps a useful chat column without changing the preferred desktop width', () => {
    const preferred = savedSidebarWidth(450);
    expect(visibleSidebarWidth(preferred, 800)).toBe(440);
    expect(visibleSidebarWidth(preferred, 1200)).toBe(450);
    expect(preferred).toBe(450);
    expect(sidebarBounds(701)).toEqual({ min: 180, max: 341 });
    expect(savedSidebarWidth(10)).toBe(180);
    expect(savedSidebarWidth(900)).toBe(480);
    expect(savedSidebarWidth(Number.NaN)).toBe(DEFAULT_SIDEBAR_WIDTH);
  });

  it('supports bounded keyboard steps, endpoints and a reset without consuming unrelated keys', () => {
    expect(keyboardSidebarWidth('ArrowRight', 238, 180, 480)).toBe(246);
    expect(keyboardSidebarWidth('ArrowLeft', 190, 180, 480, true)).toBe(180);
    expect(keyboardSidebarWidth('End', 238, 180, 340)).toBe(340);
    expect(keyboardSidebarWidth('Home', 300, 180, 480)).toBe(180);
    expect(keyboardSidebarWidth('Enter', 300, 180, 480)).toBe(238);
    expect(keyboardSidebarWidth('Tab', 238, 180, 480)).toBeNull();
  });

  it('exposes an accessible focusable vertical separator with its actual limits', async () => {
    const output = await renderToString(createSSRApp({ render: () => h(SidebarDivider, { width: 300, min: 180, max: 400 }) }));
    expect(output).toContain('role="separator"');
    expect(output).toContain('aria-orientation="vertical"');
    expect(output).toContain('aria-valuenow="300"');
    expect(output).toContain('aria-valuemin="180"');
    expect(output).toContain('aria-valuemax="400"');
    expect(output).toContain('tabindex="0"');
  });

  it('serializes requests and skips intermediate widths without applying an old response', async () => {
    const first = deferred<{ sidebarWidth: number }>();
    const second = deferred<{ sidebarWidth: number }>();
    const save = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const confirmed = vi.fn();
    const failed = vi.fn();
    const saver = createSidebarWidthSaver(save, confirmed, failed);
    saver.queue(260); saver.queue(300); saver.queue(340);
    expect(save.mock.calls.map(args => args[0])).toEqual([260]);
    first.resolve({ sidebarWidth: 260 }); await tick();
    expect(save.mock.calls.map(args => args[0])).toEqual([260, 340]);
    expect(confirmed).not.toHaveBeenCalled();
    second.resolve({ sidebarWidth: 340 }); await tick();
    expect(confirmed).toHaveBeenCalledExactlyOnceWith(340);
    expect(failed).not.toHaveBeenCalled();
  });

  it('reports a failed final save and supports an explicit retry', async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ sidebarWidth: 310 });
    const confirmed = vi.fn();
    const failed = vi.fn();
    const saver = createSidebarWidthSaver(save, confirmed, failed);
    saver.queue(310); await tick();
    expect(failed).toHaveBeenCalledOnce();
    expect(confirmed).not.toHaveBeenCalled();
    saver.queue(310); await tick();
    expect(save).toHaveBeenCalledTimes(2);
    expect(confirmed).toHaveBeenCalledExactlyOnceWith(310);
  });

  it('stops queued writes and callbacks when the view is disposed', async () => {
    const pending = deferred<{ sidebarWidth: number }>();
    const save = vi.fn(() => pending.promise);
    const confirmed = vi.fn();
    const saver = createSidebarWidthSaver(save, confirmed, vi.fn());
    saver.queue(250); saver.queue(350); saver.dispose();
    pending.resolve({ sidebarWidth: 250 }); await tick();
    expect(save).toHaveBeenCalledOnce();
    expect(confirmed).not.toHaveBeenCalled();
  });

  it('reports malformed save acknowledgements rather than replacing a draft with defaults', async () => {
    const confirmed = vi.fn();
    const failed = vi.fn();
    const saver = createSidebarWidthSaver(vi.fn().mockResolvedValue({}), confirmed, failed);
    saver.queue(320); await tick();
    expect(confirmed).not.toHaveBeenCalled();
    expect(failed).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('响应无效') }));
  });
});
