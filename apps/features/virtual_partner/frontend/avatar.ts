import { avatarImageInfo, checkAvatarDimensions, invalidImage, MAX_AVATAR_FILE_BYTES } from './avatarImage';

const AVATAR_SIZE = 96;
const MAX_PREVIEW_PIXELS = 2 * 1024 * 1024;

export interface PreparedAvatar { pixels: string; preview: string }
export interface AvatarCrop { x: number; y: number; zoom: number }
export interface AvatarSource {
  readonly width: number;
  readonly height: number;
  readonly frameCount: number;
  frame(index: number): Promise<HTMLCanvasElement>;
  close(): void;
}

function canvasOfSize(width: number, height: number) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function context2d(canvas: HTMLCanvasElement) {
  const context = canvas.getContext('2d');
  if (!context) throw new Error('无法生成头像预览，请重新打开页签后再试。');
  return context;
}

function snapshot(image: CanvasImageSource, width: number, height: number) {
  checkAvatarDimensions(width, height);
  const scale = Math.min(1, Math.sqrt(MAX_PREVIEW_PIXELS / (width * height)));
  const canvas = canvasOfSize(Math.max(1, Math.round(width * scale)), Math.max(1, Math.round(height * scale)));
  const ctx = context2d(canvas);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas;
}

export function avatarFrameFromRgba(frame: {width: number; height: number; rgba: Uint8ClampedArray}) {
  const {width, height, rgba} = frame;
  checkAvatarDimensions(width, height);
  if (rgba.length !== width * height * 4) throw invalidImage();
  const canvas = canvasOfSize(width, height);
  const ctx = context2d(canvas);
  const data = ctx.createImageData(width, height);
  data.data.set(rgba);
  ctx.putImageData(data, 0, 0);
  return canvas;
}

const closedSource = () => new Error('已关闭这张图片，请重新选择。');
function checkFrame(index: number, count: number) {
  if (!Number.isInteger(index) || index < 0 || index >= count) throw new Error('所选帧超出图片范围。');
}

async function staticSource(file: Blob, mime: string): Promise<AvatarSource> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file.slice(0, file.size, mime), {imageOrientation: 'from-image'});
  } catch {
    throw invalidImage();
  }
  let current: HTMLCanvasElement | undefined;
  try {
    current = snapshot(bitmap, bitmap.width, bitmap.height);
  } finally {
    bitmap.close();
  }
  return {
    width: current.width, height: current.height, frameCount: 1,
    async frame(index) {
      if (!current) throw closedSource();
      checkFrame(index, 1);
      return current;
    },
    close() { current = undefined; },
  };
}

export async function openAvatarSource(file: Blob): Promise<AvatarSource> {
  if (file.size > MAX_AVATAR_FILE_BYTES) throw new Error('图片超过 8 MiB，请选择较小的图片。');
  const data = await file.arrayBuffer();
  const info = avatarImageInfo(data);
  if (!info.animated) return staticSource(file, info.mime);

  const unsupported = () => new Error('当前环境无法逐帧读取这张动图，请更新 WebView2 或选择静态图片。');
  if (typeof ImageDecoder === 'undefined' || !await ImageDecoder.isTypeSupported(info.mime)) throw unsupported();
  const decoder = new ImageDecoder({data, type: info.mime, preferAnimation: true});
  let closed = false;
  let generation = 0;
  let decoding = false;
  let cached: {index: number; canvas: HTMLCanvasElement} | undefined;
  async function frame(index: number) {
    if (closed) throw closedSource();
    checkFrame(index, info.frameCount);
    if (decoding) decoder.reset();
    const ticket = ++generation;
    decoding = false;
    if (cached?.index === index) return cached.canvas;
    decoding = true;
    try {
      let result: ImageDecodeResult;
      try {
        result = await decoder.decode({frameIndex: index, completeFramesOnly: true});
      } catch {
        throw invalidImage();
      }
      const {image} = result;
      try {
        if (closed || ticket !== generation) throw closedSource();
        if (!result.complete) throw invalidImage();
        const canvas = snapshot(image, image.displayWidth, image.displayHeight);
        cached = {index, canvas};
        return canvas;
      } finally {
        image.close();
      }
    } finally {
      if (generation === ticket) decoding = false;
    }
  }
  function close() {
    if (closed) return;
    closed = true;
    cached = undefined;
    decoder.close();
  }
  try {
    try {
      await decoder.tracks.ready;
    } catch {
      throw invalidImage();
    }
    // The file is complete. A decoder exposing only the default poster cannot select its frames.
    if (decoder.tracks.selectedTrack?.frameCount !== info.frameCount) throw unsupported();
    const first = await frame(0);
    return {width: first.width, height: first.height, frameCount: info.frameCount, frame, close};
  } catch (cause) {
    close();
    throw cause;
  }
}

export function defaultAvatarCrop(): AvatarCrop {
  return {x: 0.5, y: 0.5, zoom: 1};
}

function clamp(value: number, min: number, max: number, fallback: number) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : fallback));
}

export function clampAvatarCrop(width: number, height: number, crop: AvatarCrop): AvatarCrop {
  const zoom = clamp(crop.zoom, 1, 8, 1);
  const radius = Math.min(width, height) / (2 * zoom);
  return {
    x: clamp(crop.x, radius / width, 1 - radius / width, 0.5),
    y: clamp(crop.y, radius / height, 1 - radius / height, 0.5),
    zoom,
  };
}

export function panAvatarCrop(width: number, height: number, crop: AvatarCrop, dx: number, dy: number, viewportSize: number) {
  const center = clampAvatarCrop(width, height, crop);
  if (!Number.isFinite(viewportSize) || viewportSize <= 0) return center;
  const scale = Math.min(width, height) / (center.zoom * viewportSize);
  return clampAvatarCrop(width, height, {x: center.x - dx * scale / width, y: center.y - dy * scale / height, zoom: center.zoom});
}

export function drawAvatarCrop(target: HTMLCanvasElement, frame: HTMLCanvasElement, crop: AvatarCrop) {
  const {width, height} = frame;
  const {x, y, zoom} = clampAvatarCrop(width, height, crop);
  const side = Math.min(width, height) / zoom;
  const ctx = context2d(target);
  ctx.clearRect(0, 0, target.width, target.height);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(frame, x * width - side / 2, y * height - side / 2, side, side, 0, 0, target.width, target.height);
}

export function exportAvatarCrop(frame: HTMLCanvasElement, crop: AvatarCrop): PreparedAvatar {
  const canvas = canvasOfSize(AVATAR_SIZE, AVATAR_SIZE);
  drawAvatarCrop(canvas, frame, crop);
  const rgba = context2d(canvas).getImageData(0, 0, AVATAR_SIZE, AVATAR_SIZE).data;
  let bytes = '';
  for (const channel of rgba) bytes += String.fromCharCode(channel);
  return {pixels: btoa(bytes), preview: canvas.toDataURL('image/png')};
}
