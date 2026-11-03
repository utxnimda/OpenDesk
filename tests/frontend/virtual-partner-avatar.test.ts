import { afterEach, expect, it, vi } from 'vitest';
import { renderToString } from '@vue/server-renderer';
import { createSSRApp } from 'vue';
import PartnerAvatar from '../../apps/features/virtual_partner/frontend/PartnerAvatar.vue';
import {
  avatarFrameFromRgba, clampAvatarCrop, defaultAvatarCrop, drawAvatarCrop, exportAvatarCrop,
  openAvatarSource, panAvatarCrop,
} from '../../apps/features/virtual_partner/frontend/avatar';
import { avatarImageInfo } from '../../apps/features/virtual_partner/frontend/avatarImage';

// Synthetic 10x4 source images. The GIF includes extensions, a global palette and partial frame rectangles.
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAECAYAAAC3OK7NAAAAFElEQVR4nGNgIBX8YmD4T7ImigAA3okB+sacTT8AAAAASUVORK5CYII=';
const gif = 'R0lGODlhCgAEAIEAAAAAAPoAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQFCAAAACwAAAAACgAEAAAIDwABCBxIUGCAgggTKgQQEAAh+QQJCAAAACwDAAEAAQABAIEAAAD6AAAA+gAAAAAIBAAFBAQAIfkEBQgAAAAsAAAAAAoABACBAAAAAPoAAAD6AAAACA8AAQgcSHBgAAEFEypUGBAAIfkECQgAAAAsAgABAAQAAQCCAAAA+gAAAPoAECBAAAD6AAAAAAAAAAAACAcAAwAAMCAgADs=';
const apng = 'iVBORw0KGgoAAAANSUhEUgAAAAoAAAAECAYAAAC3OK7NAAAACGFjVEwAAAADAAAAAM7tusAAAAAUSURBVHicY2AgFfxiYPhPsiaKAADeiQH6xpxNPwAAABpmY1RMAAAAAAAAAAoAAAAEAAAAAAAAAAAAAgAZAACaz3KOAAAAHGZkQVQAAAABeJxjYCAV/GJg+M/wi+E/yRrJBgCznwPzWEsHcgAAABpmY1RMAAAAAgAAAAMAAAABAAAAAgAAAAEAAgAZAAETgQDSAAAAF2ZkQVQAAAADeJxjYACBXwz/GRh+/QcADtED841DWKIAAAAaZmNUTAAAAAQAAAAEAAAAAQAAAAIAAAABAAIAGQAAw3LJdQAAAB1mZEFUAAAABXicY/jFwPCf4RfDfwaGX/8FFBwaADzQBtwCgNLKAAAAAElFTkSuQmCC';
const webp = 'UklGRhYBAABXRUJQVlA4WAoAAAASAAAACQAAAwAAQU5JTQYAAAAAAAAAAABBTk1GKgAAAAEAAAAAAAAAAAEAAFAAAAJWUDhMEQAAAC8AQAAQDxCjH/MfjAmI6H8IAEFOTUYqAAAAAQAAAAAAAQAAAQAAUAAAAFZQOEwRAAAALwFAABAPMPoR8x+MEYjofwgAQU5NRjAAAAABAAAAAAACAAABAABQAAACVlA4TBgAAAAvAkAAEBcw+iEkSEj+32ua/4APBiOi/yFBTk1GTgAAAAEAAAAAAAMAAAEAAFAAAAJWUDhMNgAAAC8DQAAQJ0CYbbTXOOZPdRxHIiQgTPB/pSwJCQjJLrutExAUPWd6ALLXHTkgEEhxDLMc0f/gAA==';
const decodeBytes = (base64: string) => Uint8Array.from(Buffer.from(base64, 'base64'));
const blob = (base64: string) => new File([decodeBytes(base64)], 'private-name', {type: 'text/plain'});

function mockCanvas() {
  const rgba = new Uint8ClampedArray(96 * 96 * 4);
  rgba.set([30, 40, 50, 128]);
  const ctx = {
    drawImage: vi.fn(), clearRect: vi.fn(), getImageData: vi.fn(() => ({data: rgba})),
    createImageData: vi.fn((w: number, h: number) => ({data: new Uint8ClampedArray(w * h * 4)})),
    putImageData: vi.fn(),
  };
  const canvas = () => ({width: 0, height: 0, getContext: () => ctx, toDataURL: () => 'data:image/png;base64,cHJldmlldw=='});
  vi.stubGlobal('document', {createElement: canvas});
  return {ctx, rgba};
}

afterEach(() => vi.unstubAllGlobals());

it('uses the name initial for older profiles and never embeds external URLs or other data types', async () => {
  for (const src of [undefined, null, 'https://example.invalid/avatar.png', 'data:image/svg+xml,<svg/>']) {
    const html = await renderToString(createSSRApp(PartnerAvatar, {name: ' 🌙小满', src}));
    expect(html).toContain('🌙');
    expect(html).not.toContain('<img');
  }
});

it.each([[png, 'image/png', 1], [gif, 'image/gif', 4], [apng, 'image/png', 3], [webp, 'image/webp', 4]] as const)(
  'counts composited frames independently of file names or MIME labels in fixture %#', (data, mime, frameCount) => {
    expect(avatarImageInfo(decodeBytes(data).buffer)).toEqual({mime, width: 10, height: 4, frameCount, animated: frameCount > 1});
  },
);

it('rejects oversized uploads, hostile dimensions, excessive animation work and disguised SVG before native decoding', async () => {
  const decode = vi.fn();
  vi.stubGlobal('createImageBitmap', decode);
  vi.stubGlobal('ImageDecoder', undefined);
  await expect(openAvatarSource(new Blob([new Uint8Array(8 * 1024 * 1024 + 1)]))).rejects.toThrow('8 MiB');
  await expect(openAvatarSource(new File(['<svg/>'], 'drawing.png', {type: 'image/png'}))).rejects.toThrow('请选择');
  await expect(openAvatarSource(new Blob([new Uint8Array([255, 216, 255])]))).rejects.toThrow('无法解码');
  const big = decodeBytes(png);
  new DataView(big.buffer).setUint32(16, 8193);
  await expect(openAvatarSource(new Blob([big]))).rejects.toThrow('图片尺寸');
  const frames = decodeBytes(apng), header = new DataView(frames.buffer);
  header.setUint32(41, 301);
  await expect(openAvatarSource(new Blob([frames]))).rejects.toThrow('300 帧');
  header.setUint32(41, 200);
  header.setUint32(16, 1024);
  header.setUint32(20, 1024);
  await expect(openAvatarSource(new Blob([frames]))).rejects.toThrow('总计');
  expect(decode).not.toHaveBeenCalled();
});

it('refuses to show an animated file as a static poster when frame access is unavailable', async () => {
  const still = vi.fn();
  vi.stubGlobal('ImageDecoder', undefined);
  vi.stubGlobal('createImageBitmap', still);
  for (const image of [gif, apng, webp]) await expect(openAvatarSource(blob(image))).rejects.toThrow('逐帧');
  expect(still).not.toHaveBeenCalled();
});

it('uses sniffed bytes and orientation for stills, then exports only the square crop and preserves alpha', async () => {
  const {ctx, rgba} = mockCanvas();
  const bitmap = {width: 10, height: 4, close: vi.fn()};
  const decode = vi.fn(async (_: Blob, _options: ImageBitmapOptions) => bitmap);
  vi.stubGlobal('createImageBitmap', decode);
  const source = await openAvatarSource(blob(png));
  expect(decode.mock.calls[0][0].type).toBe('image/png');
  expect(decode.mock.calls[0][1].imageOrientation).toBe('from-image');
  expect(bitmap.close).toHaveBeenCalledOnce();
  const frame = await source.frame(0);
  const target = document.createElement('canvas');
  target.width = target.height = 240;
  const crop = {x: .1, y: .8, zoom: 2};
  drawAvatarCrop(target, frame, crop);
  const image = exportAvatarCrop(frame, crop);
  expect(ctx.drawImage).toHaveBeenCalledWith(frame, 0, 2, 2, 2, 0, 0, 240, 240);
  expect(ctx.drawImage).toHaveBeenCalledWith(frame, 0, 2, 2, 2, 0, 0, 96, 96);
  expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 240, 240);
  expect(ctx.getImageData).toHaveBeenCalledWith(0, 0, 96, 96);
  expect(Buffer.from(image.pixels, 'base64')).toEqual(Buffer.from(rgba));
  expect(image.preview).toBe('data:image/png;base64,cHJldmlldw==');
  await expect(source.frame(1)).rejects.toThrow('范围');
  source.close();
  await expect(source.frame(0)).rejects.toThrow('关闭');
});

it('selects a complete frame, releases native frames and keeps earlier canvases valid after seeking', async () => {
  const {ctx} = mockCanvas();
  const image = {displayWidth: 10, displayHeight: 4, close: vi.fn()};
  const decode = vi.fn(async (_: ImageDecodeOptions) => ({complete: true, image}));
  const close = vi.fn();
  class Decoder {
    static isTypeSupported = vi.fn(async () => true);
    tracks = {ready: Promise.resolve(), selectedTrack: {frameCount: 4}};
    decode = decode;
    close = close;
  }
  vi.stubGlobal('ImageDecoder', Decoder);
  const source = await openAvatarSource(blob(gif));
  const first = await source.frame(0);
  const last = await source.frame(3);
  expect(last).not.toBe(first);
  expect(ctx.drawImage).toHaveBeenCalledTimes(2);
  expect(decode.mock.calls).toEqual([[{frameIndex: 0, completeFramesOnly: true}], [{frameIndex: 3, completeFramesOnly: true}]]);
  expect(image.close).toHaveBeenCalledTimes(2);
  await expect(source.frame(-1)).rejects.toThrow('范围');
  source.close(); source.close();
  expect(close).toHaveBeenCalledOnce();
  // APNG has 3 real frames and a default poster. Never expose a mismatched decoder track.
  await expect(openAvatarSource(blob(apng))).rejects.toThrow('逐帧');
  expect(close).toHaveBeenCalledTimes(2);
});

it('clamps centers at each zoom, pans in viewport pixels and keeps all source edges reachable', () => {
  expect(clampAvatarCrop(4, 10, {x: 0, y: 1, zoom: 0})).toEqual({x: .5, y: .8, zoom: 1});
  expect(clampAvatarCrop(4, 10, {x: NaN, y: Infinity, zoom: NaN})).toEqual(defaultAvatarCrop());
  const zoomed = {x: .5, y: .5, zoom: 2};
  expect(panAvatarCrop(10, 4, zoomed, 50, -100, 100)).toEqual({x: .4, y: .75, zoom: 2});
  expect(panAvatarCrop(10, 4, zoomed, -10000, 10000, 100)).toEqual({x: .9, y: .25, zoom: 2});
  expect(panAvatarCrop(10, 4, zoomed, 200, 12, 0)).toEqual(zoomed);
});

it('reconstructs the validated host RGBA snapshot and rejects mismatched pixel counts', () => {
  const {ctx} = mockCanvas();
  const rgba = new Uint8ClampedArray([8, 16, 32, 128]);
  const frame = avatarFrameFromRgba({width: 1, height: 1, rgba});
  expect([frame.width, frame.height]).toEqual([1, 1]);
  expect(ctx.putImageData).toHaveBeenCalledWith({data: rgba}, 0, 0);
  expect(() => avatarFrameFromRgba({width: 1, height: 2, rgba})).toThrow('无法解码');
});
