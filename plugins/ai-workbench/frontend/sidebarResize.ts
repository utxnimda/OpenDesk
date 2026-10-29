export const DEFAULT_SIDEBAR_WIDTH = 238;
export const MIN_SIDEBAR_WIDTH = 180;
export const MAX_SIDEBAR_WIDTH = 480;
const MIN_CHAT_WIDTH = 360;

export function savedSidebarWidth(value: number): number {
  return Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.round(Number.isFinite(value) ? value : DEFAULT_SIDEBAR_WIDTH)));
}
export function sidebarBounds(containerWidth: number) {
  return { min: MIN_SIDEBAR_WIDTH, max: Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.floor(containerWidth - MIN_CHAT_WIDTH))) };
}
export function visibleSidebarWidth(preferred: number, containerWidth: number): number {
  const bounds = sidebarBounds(containerWidth);
  return Math.max(bounds.min, Math.min(bounds.max, savedSidebarWidth(preferred)));
}
export function keyboardSidebarWidth(key: string, current: number, min: number, max: number, shift = false): number | null {
  const step = shift ? 32 : 8;
  const requested = key === 'ArrowLeft' ? current - step : key === 'ArrowRight' ? current + step : key === 'Home' ? min : key === 'End' ? max : key === 'Enter' ? DEFAULT_SIDEBAR_WIDTH : null;
  return requested === null ? null : Math.max(min, Math.min(max, Math.round(requested)));
}

/** Serialize writes and coalesce intermediate drag/keyboard commits into the newest preference. */
export function createSidebarWidthSaver(
  save: (width: number) => Promise<{ sidebarWidth: number }>,
  confirmed: (width: number) => void,
  failed: (error: unknown) => void,
) {
  let queued: number | undefined;
  let running = false;
  let disposed = false;
  async function drain() {
    if (running || disposed) return;
    running = true;
    try {
      while (queued !== undefined && !disposed) {
        const width = queued;
        queued = undefined;
        try {
          const result = await save(width);
          if (!Number.isInteger(result?.sidebarWidth) || result.sidebarWidth < MIN_SIDEBAR_WIDTH || result.sidebarWidth > MAX_SIDEBAR_WIDTH) throw new Error('布局保存响应无效，请刷新状态后重试。');
          // A newer user gesture owns the draft; an older response must not move it back.
          if (!disposed && queued === undefined) confirmed(result.sidebarWidth);
        } catch (error) {
          // A pending newer write will establish the final result for this preference.
          if (!disposed && queued === undefined) failed(error);
        }
      }
    } finally { running = false; }
  }
  return {
    queue(width: number) { if (!disposed) { queued = savedSidebarWidth(width); void drain(); } },
    dispose() { disposed = true; queued = undefined; },
  };
}
