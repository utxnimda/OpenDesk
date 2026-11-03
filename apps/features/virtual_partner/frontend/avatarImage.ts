// Read dimensions and animation metadata before asking a native decoder to allocate pixels.
export const MAX_AVATAR_FILE_BYTES = 8 * 1024 * 1024;
const MAX_SIDE = 8192;
const MAX_PIXELS = 24_000_000;
const MAX_FRAMES = 300;
const MAX_ANIMATION_PIXELS = 100_000_000;

interface ImageInfo {
  mime: string;
  width: number;
  height: number;
  frameCount: number;
  animated: boolean;
}

export function checkAvatarDimensions(width: number, height: number, frameCount = 1) {
  if (![width, height, frameCount].every(n => Number.isSafeInteger(n) && n > 0)) throw invalidImage();
  if (Math.max(width, height) > MAX_SIDE || width * height > MAX_PIXELS) {
    throw new Error('图片尺寸过大，请缩小到每边 8192 像素以内且不超过 2400 万像素。');
  }
  if (frameCount > MAX_FRAMES || width * height * frameCount > MAX_ANIMATION_PIXELS) {
    throw new Error('动画过大，请缩小图片或减少帧数（最多 300 帧，总计 1 亿像素）。');
  }
}

export function invalidImage() {
  return new Error('无法解码这张图片，请重新选择可正常打开的图片。');
}

export function avatarImageInfo(buffer: ArrayBuffer): ImageInfo {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  const tag = (offset: number, count = 4) => String.fromCharCode(...bytes.subarray(offset, offset + count));
  const le16 = (offset: number) => view.getUint16(offset, true);
  const le24 = (offset: number) => le16(offset) + view.getUint8(offset + 2) * 65536;
  const info = (mime: string, width: number, height: number, frameCount = 1, animated = false) => {
    checkAvatarDimensions(width, height, frameCount);
    return {mime, width, height, frameCount, animated};
  };
  try {
    if (tag(0, 8) === '\x89PNG\r\n\x1a\n') {
      if (view.getUint32(8) !== 13 || tag(12) !== 'IHDR') throw invalidImage();
      const width = view.getUint32(16), height = view.getUint32(20);
      // APNG requires acTL ahead of the first IDAT. Its count excludes a separate default poster.
      for (let offset = 33; offset + 12 <= bytes.length;) {
        const size = view.getUint32(offset), kind = tag(offset + 4);
        if (offset + 12 + size > bytes.length) throw invalidImage();
        if (kind === 'IDAT') return info('image/png', width, height);
        if (kind === 'acTL') {
          if (size !== 8) throw invalidImage();
          return info('image/png', width, height, view.getUint32(offset + 8), true);
        }
        offset += 12 + size;
      }
      throw invalidImage();
    }
    if (tag(0, 6) === 'GIF89a' || tag(0, 6) === 'GIF87a') {
      const width = le16(6), height = le16(8);
      const paletteBytes = (flags: number) => flags & 0x80 ? 3 * (2 << (flags & 7)) : 0;
      let offset = 13 + paletteBytes(view.getUint8(10));
      let count = 0;
      while (offset < bytes.length) {
        const marker = bytes[offset++];
        if (marker === 0x3b) return info('image/gif', width, height, count, count > 1);
        if (marker === 0x2c) {
          const w = le16(offset + 4), h = le16(offset + 6);
          if (!w || !h || le16(offset) + w > width || le16(offset + 2) + h > height) throw invalidImage();
          checkAvatarDimensions(width, height, ++count);
          offset += 10 + paletteBytes(view.getUint8(offset + 8)); // descriptor, palette and LZW code size
        } else if (marker === 0x21) {
          offset++; // extension label
        } else throw invalidImage();
        for (let size = view.getUint8(offset++); size; size = view.getUint8(offset++)) offset += size;
      }
      throw invalidImage();
    }
    if (tag(0) === 'RIFF' && tag(8) === 'WEBP') {
      const end = 8 + view.getUint32(4, true);
      if (end > bytes.length || end < 20) throw invalidImage();
      let width = 0, height = 0, count = 0, animated = false;
      for (let offset = 12; offset + 8 <= end;) {
        const kind = tag(offset), size = view.getUint32(offset + 4, true), data = offset + 8;
        if (data + size > end) throw invalidImage();
        if (kind === 'VP8X') {
          if (size !== 10) throw invalidImage();
          width = le24(data + 4) + 1;
          height = le24(data + 7) + 1;
          animated = Boolean(bytes[data] & 2);
        } else if (kind === 'VP8L' && !width) {
          if (size < 5 || bytes[data] !== 0x2f) throw invalidImage();
          const dims = view.getUint32(data + 1, true);
          width = (dims & 0x3fff) + 1;
          height = ((dims >>> 14) & 0x3fff) + 1;
        } else if (kind === 'VP8 ' && !width) {
          if (size < 10 || tag(data + 3, 3) !== '\x9d\x01\x2a') throw invalidImage();
          width = le16(data + 6) & 0x3fff;
          height = le16(data + 8) & 0x3fff;
        } else if (kind === 'ANMF') {
          if (!animated || size < 16) throw invalidImage();
          checkAvatarDimensions(width, height, ++count);
        }
        offset = data + size + (size % 2);
      }
      return info('image/webp', width, height, animated ? count : 1, animated);
    }
    if (tag(0, 3) === '\xff\xd8\xff') {
      // SOF contains dimensions before compressed image data, including for progressive JPEG.
      for (let offset = 2; offset < bytes.length;) {
        if (bytes[offset++] !== 0xff) throw invalidImage();
        while (bytes[offset] === 0xff) offset++;
        const marker = view.getUint8(offset++);
        if (marker === 0xda || marker === 0xd9) throw invalidImage();
        if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
        const size = view.getUint16(offset);
        if (size < 2 || offset + size > bytes.length) throw invalidImage();
        if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
          if (size < 8) throw invalidImage();
          return info('image/jpeg', view.getUint16(offset + 5), view.getUint16(offset + 3));
        }
        offset += size;
      }
      throw invalidImage();
    }
  } catch (error) {
    if (error instanceof RangeError) throw invalidImage();
    throw error;
  }
  throw new Error('请选择 PNG、APNG、JPEG、GIF 或 WebP 图片。');
}
