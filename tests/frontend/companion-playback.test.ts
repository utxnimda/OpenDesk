import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createCompanionPlayback, type CompanionPlaybackFrame } from '../../apps/framework/frontend/src/companions/playback';

const request = { modelId: 'amu', action: 'thinking' as const, source: '/thinking.mp4', media: 'video' as const, idle: '/cycling.png', durationMs: 6042 };
function player() {
  const changed = vi.fn<(frame: CompanionPlaybackFrame) => void>();
  return { playback: createCompanionPlayback(changed), changed };
}

describe('companion media playback lifecycle', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('waits for image load, then gives the declared action its entire duration', () => {
    const { playback } = player();
    playback.play({ ...request, source: '/wave.png', media: 'image', action: 'wave' });
    const token = playback.current!.generation;
    vi.advanceTimersByTime(20_000);
    expect(playback.current?.phase).toBe('loading');
    expect(playback.loaded(token, '/wave.png')).toBe(true);
    vi.advanceTimersByTime(6041);
    expect(playback.current?.action).toBe('wave');
    expect(playback.loaded(token, '/wave.png')).toBe(false);
    vi.advanceTimersByTime(1);
    expect(playback.current).toMatchObject({ action: 'idle', source: '/cycling.png' });
  });

  it('uses video ended as authority, regardless of metadata or play startup delay', () => {
    const { playback } = player();
    playback.play(request);
    const token = playback.current!.generation;
    vi.advanceTimersByTime(10_000);
    expect(playback.current?.phase).toBe('loading');
    expect(playback.ended(token, request.source)).toBe(false);
    expect(playback.loaded(token, request.source)).toBe(true);
    expect(playback.current?.phase).toBe('ready');
    vi.advanceTimersByTime(10_000);
    expect(playback.current?.action).toBe('thinking');
    expect(playback.started(token, request.source)).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(playback.current).toMatchObject({ action: 'thinking', phase: 'playing' });
    expect(playback.ended(token, request.source)).toBe(true);
    expect(playback.current).toMatchObject({ action: 'idle', source: '/cycling.png', media: 'image' });
    expect(playback.ended(token, request.source)).toBe(false);
  });

  it('assigns a fresh instance to replay and ignores every late event from its predecessor', () => {
    const { playback } = player();
    playback.play(request);
    const old = playback.current!.generation;
    playback.loaded(old, request.source);
    playback.started(old, request.source);
    playback.play(request);
    const replay = playback.current!.generation;
    expect(replay).not.toBe(old);
    expect(playback.loaded(old, request.source)).toBe(false);
    expect(playback.started(old, request.source)).toBe(false);
    expect(playback.failed(old, request.source, 'late play rejection')).toBe(false);
    expect(playback.ended(old, request.source)).toBe(false);
    expect(playback.current).toMatchObject({ generation: replay, phase: 'loading' });
    expect(playback.loaded(replay, '/unrelated.mp4')).toBe(false);
    expect(playback.loaded(replay, request.source)).toBe(true);
    expect(playback.started(replay, request.source)).toBe(true);
    expect(playback.current?.phase).toBe('playing');
  });

  it.each(['pause', 'hidden', 'reduced-motion', 'changed-model'])('cancels active playback when the component shows a base frame for %s', (reason) => {
    const { playback } = player();
    playback.play({ ...request, source: '/wave.png', media: 'image', action: 'wave' });
    const old = playback.current!.generation;
    playback.loaded(old, '/wave.png');
    playback.show({ modelId: reason === 'changed-model' ? 'abao' : 'amu', action: 'still', source: '/still.png' });
    vi.advanceTimersByTime(20_000);
    expect(playback.current).toMatchObject({ action: 'still', source: '/still.png' });
    expect(playback.failed(old, '/wave.png')).toBe(false);
    expect(playback.ended(old, '/wave.png')).toBe(false);
  });

  it('keeps a decode or play failure visible without silently completing the action', () => {
    const { playback } = player();
    playback.play({ ...request, source: '/wave.png', media: 'image', action: 'wave' });
    const imageToken = playback.current!.generation;
    playback.loaded(imageToken, '/wave.png');
    playback.failed(imageToken, '/wave.png', 'decode failed');
    vi.advanceTimersByTime(60_000);
    expect(playback.current).toMatchObject({ action: 'wave', phase: 'failed', error: 'decode failed' });
    playback.play(request);
    const videoToken = playback.current!.generation;
    playback.loaded(videoToken, request.source);
    playback.failed(videoToken, request.source, 'play was blocked');
    expect(playback.started(videoToken, request.source)).toBe(false);
    expect(playback.ended(videoToken, request.source)).toBe(false);
    vi.advanceTimersByTime(60_000);
    expect(playback.current).toMatchObject({ phase: 'failed', error: 'play was blocked' });
  });

  it('unmount cancels timers and rejects pending play promises and media events', () => {
    const { playback, changed } = player();
    playback.play(request);
    const token = playback.current!.generation;
    playback.loaded(token, request.source);
    playback.dispose();
    const count = changed.mock.calls.length;
    expect(playback.started(token, request.source)).toBe(false);
    expect(playback.failed(token, request.source)).toBe(false);
    expect(playback.ended(token, request.source)).toBe(false);
    playback.play(request);
    playback.show({ modelId: 'amu', action: 'idle', source: '/cycling.png' });
    vi.runAllTimers();
    expect(changed).toHaveBeenCalledTimes(count);
    expect(playback.current).toBeUndefined();
  });

  it('invalidates a completed generation before handing control to the selected-mode owner', () => {
    const completed = vi.fn();
    const playback = createCompanionPlayback(vi.fn(), completed);
    playback.play(request);
    const token = playback.current!.generation;
    playback.loaded(token, request.source);
    playback.started(token, request.source);
    expect(playback.ended(token, request.source)).toBe(true);
    expect(completed).toHaveBeenCalledExactlyOnceWith('thinking');
    expect(playback.current).toBeUndefined();
    expect(playback.ended(token, request.source)).toBe(false);
    expect(playback.failed(token, request.source)).toBe(false);
    playback.play(request);
    expect(playback.current?.generation).not.toBe(token);
  });

  it('lets the owner loop a selected video and later return a one-shot to a different selected mode', () => {
    let selected: 'thinking' | 'sleep' = 'thinking';
    const playback = createCompanionPlayback(vi.fn(), () => {
      if (selected === 'thinking') playback.play(request);
      else playback.show({ modelId: 'amu', action: 'sleep', source: '/sleep.png' });
    });
    playback.play(request);
    const first = playback.current!.generation;
    playback.loaded(first, request.source); playback.started(first, request.source);
    playback.ended(first, request.source);
    const loop = playback.current!.generation;
    expect(loop).not.toBe(first);
    expect(playback.current).toMatchObject({ action: 'thinking', phase: 'loading' });
    expect(playback.ended(first, request.source)).toBe(false);
    selected = 'sleep';
    playback.loaded(loop, request.source); playback.started(loop, request.source); playback.ended(loop, request.source);
    expect(playback.current).toMatchObject({ action: 'sleep', source: '/sleep.png' });
    playback.loaded(playback.current!.generation, '/sleep.png');
    vi.advanceTimersByTime(60_000);
    expect(playback.current?.action).toBe('sleep');
    expect(vi.getTimerCount()).toBe(0);
    playback.show({ modelId: 'amu', action: 'idle', source: '/idle.png' });
    playback.loaded(playback.current!.generation, '/idle.png');
    expect(vi.getTimerCount()).toBe(0);
  });
});
