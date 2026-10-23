import type { CompanionAction, CompanionTransientAction } from './contracts';

export interface CompanionPlaybackFrame {
  generation: number;
  modelId: string;
  action: CompanionAction | 'still';
  source: string;
  media: 'image' | 'video';
  phase: 'loading' | 'ready' | 'playing' | 'failed';
  error?: string;
}

interface BaseFrame { modelId: string; action: CompanionAction | 'still'; source: string }
interface ActionRequest {
  modelId: string;
  action: CompanionTransientAction;
  source: string;
  media: 'image' | 'video';
  idle: string;
  durationMs: number;
}

/** Images use a post-load timer; a video completes only on its current ended event. */
export function createCompanionPlayback(changed: (frame: CompanionPlaybackFrame) => void, completed?: (action: CompanionTransientAction) => void) {
  let generation = 0;
  let frame: CompanionPlaybackFrame | undefined;
  let action: ActionRequest | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  const current = (token: number, source: string) => !disposed && frame?.generation === token && frame.source === source;
  function notify(value: CompanionPlaybackFrame) { frame = value; changed(value); }
  function clear() { generation++; clearTimeout(timer); timer = undefined; action = undefined; frame = undefined; }
  function show(value: BaseFrame) {
    if (disposed) return;
    clear();
    notify({ ...value, generation, media: 'image', phase: 'loading' });
  }
  function play(request: ActionRequest) {
    if (disposed) return;
    clear();
    action = request;
    const validDuration = Number.isFinite(request.durationMs) && request.durationMs > 0 && request.durationMs <= 2_147_483_647;
    notify({ generation, modelId: request.modelId, action: request.action, source: request.source, media: request.media,
      phase: validDuration ? 'loading' : 'failed', ...(validDuration ? {} : { error: '动作时长无效，请更新宠物资源。' }) });
  }
  function finish(token: number, source: string) {
    if (!current(token, source) || frame!.phase !== 'playing' || !action) return false;
    const request = action;
    if (completed) { clear(); completed(request.action); }
    else show({ modelId: request.modelId, action: 'idle', source: request.idle });
    return true;
  }
  function loaded(token: number, source: string) {
    if (!current(token, source) || frame!.phase !== 'loading') return false;
    notify({ ...frame!, phase: frame!.media === 'video' ? 'ready' : 'playing' });
    if (action && frame!.media === 'image') {
      const request = action;
      timer = setTimeout(() => {
        finish(token, source);
      }, request.durationMs);
    }
    return true;
  }
  function started(token: number, source: string) {
    if (!current(token, source) || frame!.media !== 'video' || frame!.phase !== 'ready') return false;
    notify({ ...frame!, phase: 'playing' });
    return true;
  }
  function ended(token: number, source: string) {
    if (!current(token, source) || frame!.media !== 'video' || frame!.phase !== 'playing' || !action) return false;
    return finish(token, source);
  }
  function failed(token: number, source: string, error = '角色资源未能加载，请重新播放或切换角色。') {
    if (!current(token, source)) return false;
    clearTimeout(timer); timer = undefined;
    notify({ ...frame!, phase: 'failed', error });
    return true;
  }
  return {
    show, play, loaded, started, ended, failed,
    get current() { return frame; },
    cancel: clear,
    dispose() { clear(); disposed = true; },
  };
}
