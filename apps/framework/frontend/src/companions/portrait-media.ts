import { MAX_PORTRAIT_FRAMES, MAX_PORTRAIT_SIDE, type PetPortraitFrame } from '../../../module-sdk/src/pet-portraits';

export type PortraitAsset =
  | { url: string; kind: 'still' | 'animation' }
  | { url: string; kind: 'video'; count: number; fps: number };

export interface PortraitReader {
  count: number;
  read(index: number, signal: AbortSignal): Promise<PetPortraitFrame>;
  close(): void;
}

function raster(source: CanvasImageSource, w: number, h: number, index: number, frameCount: number): PetPortraitFrame {
  if (!Number.isSafeInteger(w) || !Number.isSafeInteger(h) || w < 1 || h < 1) throw new Error('桌宠媒体尺寸无效。');
  const scale = Math.min(1, MAX_PORTRAIT_SIDE / Math.max(w, h));
  const canvas = document.createElement('canvas');
  const width = canvas.width = Math.max(1, Math.round(w * scale));
  const height = canvas.height = Math.max(1, Math.round(h * scale));
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('无法创建桌宠头像画布。');
  ctx.drawImage(source, 0, 0, width, height);
  return { width, height, rgba: ctx.getImageData(0, 0, width, height).data, index, frameCount };
}

function checkCount(count: number) {
  if (!Number.isInteger(count) || count < 1 || count > MAX_PORTRAIT_FRAMES) throw new Error('桌宠媒体帧数超出头像接口限制。');
}

/** Packaged images are trusted, but keep a hard byte ceiling even if Content-Length is absent. */
async function fetchPortraitMedia(url: string, type: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(url, { signal, mode: 'same-origin', credentials: 'omit', redirect: 'error' });
  const limit = 64 * 1024 * 1024;
  if (!response.ok || !response.body || Number(response.headers.get('content-length')) > limit) {
    await response.body?.cancel();
    throw new Error('无法读取已登记的桌宠媒体，或文件超过 64 MiB。');
  }
  const stream = response.body.getReader();
  const chunks: BlobPart[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { value, done } = await stream.read();
      if (done) break;
      if ((bytes += value.byteLength) > limit) {
        await stream.cancel();
        throw new Error('桌宠媒体超过 64 MiB。');
      }
      chunks.push(value as Uint8Array<ArrayBuffer>);
    }
  } finally { stream.releaseLock(); }
  return new Blob(chunks, { type });
}

async function openAnimation(url: string, signal: AbortSignal): Promise<PortraitReader> {
  if (typeof ImageDecoder === 'undefined' || !await ImageDecoder.isTypeSupported('image/png')) {
    throw new Error('当前桌面运行环境无法逐帧解码 PNG 动画，请选择静态形象。');
  }
  const data = await (await fetchPortraitMedia(url, 'image/png', signal)).arrayBuffer();
  signal.throwIfAborted();
  const decoder = new ImageDecoder({ data, type: 'image/png', preferAnimation: true });
  const close = () => decoder.close();
  signal.addEventListener('abort', close, { once: true });
  try {
    await decoder.tracks.ready;
    const count = decoder.tracks.selectedTrack?.frameCount ?? 0;
    checkCount(count);
    return { count, close, async read(index, readSignal) {
      readSignal.throwIfAborted();
      readSignal.addEventListener('abort', close, { once: true });
      try {
        const { image } = await decoder.decode({ frameIndex: index, completeFramesOnly: true });
        try { return raster(image, image.displayWidth, image.displayHeight, index, count); }
        finally { image.close(); }
      } finally { readSignal.removeEventListener('abort', close); }
    } };
  } catch (error) { close(); throw error; }
  finally { signal.removeEventListener('abort', close); }
}

function waitForVideo(video: HTMLVideoElement, event: 'loadeddata' | 'seeked', signal: AbortSignal, start: () => void): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener(event, done); video.removeEventListener('error', fail); signal.removeEventListener('abort', fail);
    };
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error('桌宠视频读取失败或已超时，请重新选帧。')); };
    video.addEventListener(event, done);
    video.addEventListener('error', fail);
    signal.addEventListener('abort', fail, { once: true });
    try { start(); } catch { fail(); }
  });
}

async function openVideo(url: string, count: number, fps: number, signal: AbortSignal): Promise<PortraitReader> {
  checkCount(count);
  if (!Number.isFinite(fps) || fps <= 0) throw new Error('桌宠视频缺少有效的帧率记录。');
  // The Tauri asset protocol returns whole resources. A local blob supports exact backward/forward seeks without HTTP ranges.
  const blob = await fetchPortraitMedia(url, 'video/webm', signal);
  signal.throwIfAborted();
  const videoUrl = URL.createObjectURL(blob);
  const video = document.createElement('video');
  video.preload = 'auto'; video.muted = true; video.playsInline = true;
  const close = () => { video.removeAttribute('src'); video.load(); URL.revokeObjectURL(videoUrl); };
  try {
    await waitForVideo(video, 'loadeddata', signal, () => { video.src = videoUrl; });
    if (!Number.isFinite(video.duration) || Math.abs(video.duration - count / fps) >= 0.5 / fps) {
      throw new Error('桌宠视频时长与登记的帧数、帧率不一致。');
    }
    return { count, close, async read(index, readSignal) {
      // Seek to the middle of the registered CFR frame, away from rounded WebM PTS boundaries.
      const time = (index + 0.5) / fps;
      if (video.currentTime !== time) await waitForVideo(video, 'seeked', readSignal, () => { video.currentTime = time; });
      if (Math.abs(video.currentTime - time) >= 0.5 / fps) throw new Error('桌宠视频未能定位到指定帧。');
      return raster(video, video.videoWidth, video.videoHeight, index, count);
    } };
  } catch (error) { close(); throw error; }
}

export async function openPortraitMedia(asset: PortraitAsset, signal: AbortSignal): Promise<PortraitReader> {
  signal.throwIfAborted();
  const url = new URL(asset.url, document.baseURI);
  if (!['http:', 'https:', 'tauri:'].includes(url.protocol) || url.origin !== window.location.origin || url.username || url.password) {
    throw new Error('桌宠头像源必须是宿主内置的同源资源。');
  }
  if (asset.kind === 'animation') return openAnimation(url.href, signal);
  if (asset.kind === 'video') return openVideo(url.href, asset.count, asset.fps, signal);
  const image = new Image();
  const close = () => { image.removeAttribute('src'); };
  image.src = url.href;
  signal.addEventListener('abort', close, { once: true });
  try {
    await image.decode();
    return { count: 1, close, async read() { return raster(image, image.naturalWidth, image.naturalHeight, 0, 1); } };
  } catch { close(); throw new Error('无法解码桌宠静态形象，请检查宿主内置资源。'); }
  finally { signal.removeEventListener('abort', close); }
}
