"""Reproducible local matte for a purple- or teal-background character video.

Requires only NumPy, Pillow, and ffmpeg. This is an image-processing operation:
no model inference, retargeting, interpolation, or pose generation is used.
The explicit profiles are tuned for particular clean purple or teal backgrounds.
The default purple profile is unchanged. This is not general person segmentation.
Always inspect the white/dark composites before integrating the generated assets.
Optional one-based inclusive frame bounds select a continuous default loop.
Every source frame still defines the common canvas, still.png remains source
frame 1, and a shortened selection also writes the complete full-cycle.png.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import platform
import struct
import subprocess
import zlib

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, __version__ as pillow_version


def box_sum(a: np.ndarray, radius: int) -> np.ndarray:
    p = np.pad(a, ((radius, radius), (radius, radius)), mode="edge")
    s = np.pad(p, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    k = radius * 2 + 1
    return s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]


def enclosed_background(core: np.ndarray) -> np.ndarray:
    """Classify holes enclosed by opaque source pixels; never modify alpha."""
    parents: list[int] = []
    borders: list[bool] = []
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    height, width = core.shape

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    for y, row in enumerate(~core):
        changes = np.flatnonzero(np.diff(np.r_[False, row, False]))
        current = []
        for start, end in zip(changes[::2], changes[1::2]):
            label = len(parents)
            parents.append(label)
            borders.append(y == 0 or y == height - 1 or start == 0 or end == width)
            for previous_start, previous_end, previous_label in previous:
                if previous_end <= start:
                    continue
                if previous_start >= end:
                    break
                a, b = find(label), find(previous_label)
                if a != b:
                    parents[b] = a
                    borders[a] = borders[a] or borders[b]
            current.append((int(start), int(end), label))
            runs.append((y, int(start), int(end), label))
        previous = current
    result = np.zeros_like(core)
    for y, start, end, label in runs:
        if not borders[find(label)]:
            result[y, start:end] = True
    return result


def remove_small_components(alpha: np.ndarray, core: np.ndarray, minimum_area: int = 64) -> None:
    """Remove isolated codec speckles while keeping connected 1px wheel spokes.

    Run-length union-find labels foreground with eight-neighbor connectivity.
    This avoids both a full-image Python BFS and a morphology operation that
    would remove thin lines regardless of whether they belong to the subject.
    """
    parents: list[int] = []
    areas: list[int] = []
    core_counts: list[int] = []
    enclosed_counts: list[int] = []
    # Weak isolated spoke segments can be wholly inside a closed tire and have
    # no opaque core of their own. Classify exterior space without filling any
    # output pixels; preserve those enclosed source segments as-is.
    enclosed = enclosed_background(core)
    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    for y, row in enumerate(alpha > 0):
        changes = np.flatnonzero(np.diff(np.r_[False, row, False]))
        current = []
        for start, end in zip(changes[::2], changes[1::2]):
            label = len(parents)
            parents.append(label)
            areas.append(int(end - start))
            core_counts.append(int(np.count_nonzero(core[y, start:end])))
            enclosed_counts.append(int(np.count_nonzero(enclosed[y, start:end])))
            for previous_start, previous_end, previous_label in previous:
                if previous_end < start:
                    continue
                if previous_start > end:
                    break
                a, b = find(label), find(previous_label)
                if a != b:
                    parents[b] = a
                    areas[a] += areas[b]
                    core_counts[a] += core_counts[b]
                    enclosed_counts[a] += enclosed_counts[b]
            current.append((int(start), int(end), label))
            runs.append((y, int(start), int(end), label))
        previous = current

    for y, start, end, label in runs:
        root = find(label)
        if areas[root] < minimum_area or (core_counts[root] == 0 and enclosed_counts[root] == 0):
            alpha[y, start:end] = 0


def matte_teal(path: Path) -> Image.Image:
    source = Image.open(path).convert("RGB")
    rgb = np.asarray(source, dtype=np.float64)
    key = rgb[:, :, 1] - (rgb[:, :, 0] + rgb[:, :, 2]) / 2
    blue = rgb[:, :, 2] - rgb[:, :, 1]

    # Median follows spatially varying teal in wheel openings and shadows while
    # suppressing thin spokes only in the background estimate, never in output.
    median = np.asarray(source.filter(ImageFilter.MedianFilter(9)), dtype=np.float64)
    median_key = median[:, :, 1] - (median[:, :, 0] + median[:, :, 2]) / 2
    median_blue = median[:, :, 2] - median[:, :, 1]
    background_mask = (median_key > 9) & (median_blue < 12) & (median[:, :, 1] - median[:, :, 0] > 22)
    _, width, _ = rgb.shape
    left = np.median(rgb[:, :32], axis=1)
    right = np.median(rgb[:, -32:], axis=1)
    x = np.linspace(0, 1, width)[None, :, None]
    background = left[:, None, :] * (1 - x) + right[:, None, :] * x
    weight = box_sum(background_mask.astype(float), 8)
    for channel in range(3):
        np.divide(box_sum(median[:, :, channel] * background_mask, 8), weight,
                  out=background[:, :, channel], where=weight > 0)
    background[background_mask] = median[background_mask]

    # Keep the blue cloth underside/flowers as foreground. Erosion applies only
    # to reliable opaque seeds; final alpha is not eroded.
    blue_foreground = (blue >= 12) & (key < 14)
    core_mask = (key < 3) | blue_foreground
    core = np.asarray(Image.fromarray(np.uint8(core_mask) * 255).filter(ImageFilter.MinFilter(5))) > 0
    core_weight = box_sum(core.astype(float), 4)
    foreground = np.full_like(rgb, 90)
    for channel in range(3):
        np.divide(box_sum(rgb[:, :, channel] * core, 4), core_weight,
                  out=foreground[:, :, channel], where=core_weight > 0)

    # Color-line projection uses all three channels rather than a low-contrast
    # single chroma key. Local core color prevents low-alpha white fringe blowup.
    direction = foreground - background
    alpha = np.clip(np.sum((rgb - background) * direction, axis=2)
                    / np.maximum(np.sum(direction * direction, axis=2), 16), 0, 1)
    dark_line = (rgb.mean(axis=2) < median.mean(axis=2) - 2) & (key < median_key - 0.5) & background_mask
    seed = (key < 8) | blue_foreground | dark_line
    support = np.asarray(Image.fromarray(np.uint8(seed) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    alpha[core] = 1
    alpha[(~support) | (alpha < 0.035)] = 0
    remove_small_components(alpha, core)

    output = foreground.copy()
    output[core] = rgb[core]
    output[alpha > 0.97] = rgb[alpha > 0.97]
    output[alpha == 0] = 0
    return Image.fromarray(np.dstack((np.uint8(np.clip(np.rint(output), 0, 255)),
                                     np.uint8(np.rint(alpha * 255)))), "RGBA")


def matte(path: Path, profile: str = "purple") -> Image.Image:
    if profile == "teal":
        return matte_teal(path)
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    h, w, _ = rgb.shape
    # Background gradient measured from two clear vertical borders.
    left = np.median(rgb[:, :32], axis=1)
    right = np.median(rgb[:, -32:], axis=1)
    x = np.linspace(0, 1, w)[None, :, None]
    bg = left[:, None, :] * (1 - x) + right[:, None, :] * x
    key = rgb[:, :, 2] - rgb[:, :, 1]
    bg_key = bg[:, :, 2] - bg[:, :, 1]
    seed = Image.fromarray(np.uint8(key < 28) * 255)
    support = np.asarray(seed.filter(ImageFilter.MaxFilter(9))) > 0
    core = np.asarray(Image.fromarray(np.uint8(key < 22) * 255).filter(ImageFilter.MinFilter(3))) > 0
    weight = box_sum(core.astype(np.float64), 6)
    fg_key = np.divide(box_sum(key * core, 6), weight, out=np.zeros_like(weight), where=weight > 0)
    fg_key = np.clip(fg_key, -65, 22)
    alpha = np.clip((bg_key - key) / np.maximum(bg_key - fg_key, 12), 0, 1)
    alpha[core] = 1
    alpha[~support | (key >= 40)] = 0
    # Unmix purple only at the soft edge. Preserve every opaque core RGB pixel.
    out = (rgb - (1 - alpha[:, :, None]) * bg) / np.maximum(alpha[:, :, None], 0.03)
    out[core] = rgb[core]
    out = np.clip(out, 0, 255)
    out[alpha < 1 / 255] = 0
    return Image.fromarray(np.dstack((np.rint(out).astype(np.uint8), np.rint(alpha * 255).astype(np.uint8))), "RGBA")


def bbox(image: Image.Image, threshold: int = 4) -> tuple[int, int, int, int]:
    a = np.asarray(image)[:, :, 3]
    ys, xs = np.nonzero(a > threshold)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def normalize(image: Image.Image, bounds: tuple[int, int, int, int], canvas_size: int, extent: int, baseline: int) -> Image.Image:
    x0, y0, x1, y1 = bounds
    scale = extent / max(x1 - x0, y1 - y0)
    # One common crop, scale and baseline for all frames: retain the original motion.
    cropped = image.crop(bounds)
    size = round((x1 - x0) * scale), round((y1 - y0) * scale)
    resized = cropped.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (canvas_size, canvas_size))
    canvas.alpha_composite(resized, ((canvas_size - size[0]) // 2, baseline - size[1]))
    return canvas


def compare(image: Image.Image, path: Path) -> None:
    w, h = image.size
    sheet = Image.new("RGB", (w * 2, h + 32), (230, 230, 230))
    for x, color, label in [(0, (255, 255, 255, 255), "White background"), (w, (10, 12, 16, 255), "Dark background")]:
        panel = Image.new("RGBA", image.size, color)
        panel.alpha_composite(image)
        sheet.paste(panel.convert("RGB"), (x, 32))
        ImageDraw.Draw(sheet).text((x + 12, 8), label, fill=(20, 20, 20))
    sheet.save(path)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_apng(frames: list[Path], output: Path, fps: Fraction) -> None:
    """Keep exact rational timing and full SOURCE frames, avoiding OVER differences.

    Frame PNGs are standard Pillow RGBA output. Reuse their lossless IDAT data;
    animation control/chunk CRCs are written using only Python's standard library.
    """
    if fps.numerator > 65535 or fps.denominator > 65535:
        raise ValueError("Frame rate exceeds APNG rational-delay limits")
    with Image.open(frames[0]) as first:
        width, height = first.size
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("wb") as out:
        out.write(b"\x89PNG\r\n\x1a\n")
        def chunk(kind: bytes, data: bytes) -> None:
            out.write(struct.pack(">I", len(data)))
            out.write(kind + data)
            out.write(struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        chunk(b"acTL", struct.pack(">II", len(frames), 0))
        sequence = 0
        for i, path in enumerate(frames):
            data = path.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"Not a PNG frame: {path.name}")
            chunk(b"fcTL", struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, fps.denominator, fps.numerator, 0, 0))
            sequence += 1
            offset = 8
            while offset < len(data):
                length = struct.unpack(">I", data[offset:offset + 4])[0]
                kind = data[offset + 4:offset + 8]
                payload = data[offset + 8:offset + 8 + length]
                if kind == b"IHDR" and payload != struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0):
                    raise ValueError(f"Mismatched RGBA PNG frame: {path.name}")
                if kind == b"IDAT":
                    if i == 0:
                        chunk(b"IDAT", payload)
                    else:
                        chunk(b"fdAT", struct.pack(">I", sequence) + payload)
                        sequence += 1
                offset += length + 12
        chunk(b"IEND", b"")
    temporary.replace(output)


def verify_apng(path: Path, frames: list[Path]) -> list[float]:
    """Decode every frame and verify the exact processed RGBA pixels."""
    with Image.open(path) as animation:
        if animation.n_frames != len(frames) or animation.info.get("loop") != 0:
            raise RuntimeError("APNG frame count or infinite-loop metadata differs")
        durations = []
        for i, expected_path in enumerate(frames):
            animation.seek(i)
            durations.append(animation.info["duration"])
            expected = np.asarray(Image.open(expected_path).convert("RGBA"))
            if not np.array_equal(np.asarray(animation.convert("RGBA")), expected):
                raise RuntimeError(f"APNG read-back pixels differ from processed frame {expected_path.name}")
    return durations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="User-authorized source video; never modified")
    parser.add_argument("--output", type=Path, required=True, help="Local output directory")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable or absolute path")
    parser.add_argument("--ffprobe", default="ffprobe", help="FFprobe executable or absolute path")
    parser.add_argument("--matte-profile", choices=("purple", "teal"), default="purple", help="Explicit background profile; purple is the unchanged default")
    parser.add_argument("--start-frame", type=int, help="First animation frame, one-based inclusive; requires --end-frame")
    parser.add_argument("--end-frame", type=int, help="Last animation frame, one-based inclusive; requires --start-frame")
    parser.add_argument("--canvas", type=int, default=768)
    parser.add_argument("--extent", type=int, default=700, help="Longest union silhouette dimension")
    parser.add_argument("--baseline", type=int, default=734, help="Common source floor baseline on output canvas")
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()
    if (args.start_frame is None) != (args.end_frame is None):
        parser.error("Provide both --start-frame and --end-frame, or neither.")
    if args.start_frame is not None and not 1 <= args.start_frame < args.end_frame <= 600:
        parser.error("Frame range must satisfy 1 <= start-frame < end-frame <= 600.")
    if not 0 < args.extent <= args.baseline < args.canvas <= 2048:
        parser.error("Require 0 < extent <= baseline < canvas <= 2048")
    source_video = args.input.resolve(strict=True)
    root = args.output.resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        parser.error("Output must be a new or empty directory; existing frames must not be mixed.")
    if not source_video.is_file() or source_video.stat().st_size > 500_000_000:
        parser.error("Input must be a video file no larger than 500 MB.")
    root.mkdir(parents=True, exist_ok=True)
    creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    try:
        probe_output = subprocess.check_output([args.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(source_video)], creationflags=creationflags, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Video metadata probe timed out after 30 seconds.") from error
    probe = json.loads(probe_output)
    stream = probe["streams"][0]
    fps = Fraction(stream["avg_frame_rate"])
    if not 0 < fps <= 60 or Fraction(stream["r_frame_rate"]) != fps:
        raise RuntimeError("This deterministic pipeline requires a constant-frame-rate input.")
    width, height = int(stream["width"]), int(stream["height"])
    duration = float(stream.get("duration", probe["format"]["duration"]))
    if not (2 <= width <= 2048 and 2 <= height <= 2048 and width * height <= 4_000_000 and 0 < duration <= 30 and float(fps) * duration <= 601):
        raise RuntimeError("Input exceeds supported limits: 2048 px edge, 4 M pixels, 30 seconds or 600 frames.")
    try:
        timestamp_output = subprocess.check_output([args.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source_video)], creationflags=creationflags, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Video frame timestamp probe timed out after 30 seconds.") from error
    timestamps = json.loads(timestamp_output)["frames"]
    times = [float(frame["best_effort_timestamp_time"]) for frame in timestamps]
    if not 2 <= len(times) <= 600 or not np.allclose(np.diff(times), float(1 / fps), atol=0.00001, rtol=0):
        raise RuntimeError("Actual video frame timestamps are not a supported constant-rate sequence.")
    start_frame = args.start_frame if args.start_frame is not None else 1
    end_frame = args.end_frame if args.end_frame is not None else len(times)
    if end_frame > len(times):
        parser.error(f"end-frame exceeds the source's {len(times)} frames.")
    source_dir = root / "source-frames"
    source_dir.mkdir(exist_ok=False)
    try:
        subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-noautorotate", "-i", str(source_video), "-map", "0:v:0", "-an", "-fps_mode", "passthrough", str(source_dir / "frame-%04d.png")], check=True, creationflags=creationflags, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Video frame extraction timed out after 180 seconds.") from error
    sources = sorted(source_dir.glob("frame-*.png"))
    if len(sources) != len(times) or ("nb_frames" in stream and int(stream["nb_frames"]) != len(sources)):
        raise RuntimeError("Extracted frame count does not match source metadata/timestamps.")
    for source in sources:
        with Image.open(source) as decoded:
            if decoded.size != (width, height):
                raise RuntimeError("Extracted frame dimensions do not match source video.")
    first = matte(sources[0], args.matte_profile)
    first.save(root / "first-native-transparent.png")
    first_preview = normalize(first, bbox(first), args.canvas, args.extent, args.baseline)
    first_preview.save(root / "first-preview-transparent.png")
    compare(first_preview, root / "first-white-black.png")
    print(json.dumps({"preview": str(root / "first-white-black.png"), "first_bbox": bbox(first)}, ensure_ascii=False), flush=True)
    if args.preview_only:
        return
    native = root / "native-rgba"
    final = root / "rgba-frames"
    native.mkdir(exist_ok=True)
    final.mkdir(exist_ok=True)
    bounds = []
    for i, source in enumerate(sources):
        frame = first if i == 0 else matte(source, args.matte_profile)
        bounds.append(bbox(frame))
        frame.save(native / source.name)
        if i % 24 == 0:
            print(f"matted {i + 1}/{len(sources)}", flush=True)
    union = (min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds))
    qa = Image.new("RGB", (4 * 384, 2 * 416), (220, 220, 220))
    qa_ids = np.linspace(0, len(sources) - 1, 8, dtype=int).tolist()
    for i, source in enumerate(sources):
        frame = normalize(Image.open(native / source.name), union, args.canvas, args.extent, args.baseline)
        frame.save(final / source.name)
        if i == 0:
            frame.save(root / "still.png")
            compare(frame, root / "still-white-black.png")
        if i in qa_ids:
            idx = qa_ids.index(i)
            panel = Image.new("RGBA", frame.size, (255, 255, 255, 255) if idx % 2 == 0 else (10, 12, 16, 255))
            panel.alpha_composite(frame)
            pos = ((idx % 4) * 384, (idx // 4) * 416)
            qa.paste(panel.convert("RGB").resize((384, 384), Image.Resampling.LANCZOS), (pos[0], pos[1] + 32))
            ImageDraw.Draw(qa).text((pos[0] + 8, pos[1] + 8), f"Source frame {i+1} / {float(i/fps):.3f}s", fill=(20, 20, 20))
    qa.save(root / "contact-white-black.png")
    output = root / "idle-candidate.png"
    animation_sources = sources[start_frame - 1:end_frame]
    animation_frames = [final / source.name for source in animation_sources]
    write_apng(animation_frames, output, fps)
    full_output = None
    if len(animation_sources) != len(sources):
        full_output = root / "full-cycle.png"
        all_frames = [final / source.name for source in sources]
        write_apng(all_frames, full_output, fps)
        verify_apng(full_output, all_frames)
    diffs = []
    first_premul = previous = None
    frame_stats = []
    for source in sources:
        frame = np.asarray(Image.open(final / source.name), dtype=np.float32) / 255
        premul = np.dstack((frame[:, :, :3] * frame[:, :, 3:4], frame[:, :, 3:4]))
        if previous is None:
            first_premul = premul
        else:
            diffs.append(float(np.abs(premul - previous).mean()))
        previous = premul
        a = frame[:, :, 3]
        frame_stats.append({"visible_pixels": int(np.count_nonzero(a)), "partial_alpha_pixels": int(np.count_nonzero((a > 0) & (a < 1))), "border_max_alpha": float(max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max()))})
    full_seam = float(np.abs(previous - first_premul).mean())
    selected_endpoints = []
    for path in (animation_frames[0], animation_frames[-1]):
        frame = np.asarray(Image.open(path), dtype=np.float32) / 255
        selected_endpoints.append(np.dstack((frame[:, :, :3] * frame[:, :, 3:4], frame[:, :, 3:4])))
    seam = float(np.abs(selected_endpoints[1] - selected_endpoints[0]).mean())
    selected_diffs = diffs[start_frame - 1:end_frame - 1]
    encoded_durations = verify_apng(output, animation_frames)
    encoded_count = len(encoded_durations)
    try:
        ffmpeg_version = subprocess.check_output([args.ffmpeg, "-version"], creationflags=creationflags, timeout=10).decode().splitlines()[0]
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("FFmpeg version query timed out after 10 seconds.") from error
    report = {
        "source": {"name": source_video.name, "bytes": source_video.stat().st_size, "sha256": digest(source_video)},
        "matte_profile": args.matte_profile,
        "versions": {"python": platform.python_version(), "numpy": np.__version__, "pillow": pillow_version, "ffmpeg": ffmpeg_version},
        "source_frame_count": len(sources), "output_frame_count": encoded_count, "fps": str(fps),
        "animation_frame_range": {"start": start_frame, "end": end_frame, "indexing": "one-based inclusive"},
        "source_duration_seconds": float(len(sources) / fps),
        "duration_seconds": float(encoded_count / fps), "encoded_frame_durations_ms": encoded_durations,
        "normalization_source_frame_count": len(sources), "still_source_frame": 1,
        "canvas": [args.canvas, args.canvas], "union_bounds_source": union,
        "source_frame_bounds": bounds, "frame_alpha_stats": frame_stats,
        "constant_scale": args.extent / max(union[2]-union[0], union[3]-union[1]),
        "constant_baseline": args.baseline, "source_first_last_equal": digest(sources[0]) == digest(sources[-1]),
        "seam_premultiplied_rgba_mean_absolute_difference": seam,
        "adjacent_premultiplied_rgba_difference_median": float(np.median(selected_diffs)),
        "adjacent_premultiplied_rgba_difference_max": max(selected_diffs),
        "full_source_seam_premultiplied_rgba_mean_absolute_difference": full_seam,
        "full_source_adjacent_premultiplied_rgba_difference_median": float(np.median(diffs)),
        "source_sequence_timing_unchanged": encoded_count == len(sources),
        "source_frame_cadence_preserved": True, "pose_generation_or_interpolation": False,
        "apng": {"path": str(output), "bytes": output.stat().st_size, "sha256": digest(output)},
        "still": {"path": str(root / "still.png"), "sha256": digest(root / "still.png")},
        "apng_encoding": "lossless PNG IDAT, full SOURCE frames, exact rational timing, infinite loop",
        "decoded_frames_pixel_identical_to_processed_pngs": True,
    }
    if full_output:
        report["full_animation"] = {"path": str(full_output), "frames": len(sources), "duration_seconds": float(len(sources) / fps), "bytes": full_output.stat().st_size, "sha256": digest(full_output)}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["source_frame_count", "output_frame_count", "fps", "duration_seconds", "union_bounds_source", "seam_premultiplied_rgba_mean_absolute_difference", "adjacent_premultiplied_rgba_difference_median", "apng", "still", "versions"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
