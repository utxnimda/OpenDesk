"""Reproduce a reviewed source-video foreground matte with an explicit alpha recipe.

This offline authoring tool consumes original RGB, a grayscale alpha seed and a
source-bound outline configuration. It does not generate image content or infer
missing body parts. Existing purple/teal video tooling is unchanged.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
from threading import Event, Timer

import numpy as np
from PIL import Image, __version__ as pillow_version

from companion_foreground_matte import ForegroundMatte, load_opencv


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def tool(command: list[str], stage: str, timeout: int, capture: bool = True):
    try:
        return subprocess.run(command, check=True, stdout=subprocess.PIPE if capture else None,
                              stderr=subprocess.PIPE, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{stage} timed out after {timeout} seconds") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"{stage} failed: {detail}") from error


def load_config(path: Path, alpha_seed: Path) -> dict:
    if path.stat().st_size > 256_000 or alpha_seed.stat().st_size > 4_000_000:
        raise ValueError("Authoring configuration or alpha seed exceeds supported limits")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schemaVersion") != 1 or config.get("algorithm") != "amu-thinking-foreground-v1":
        raise ValueError("Unsupported foreground recipe; expected amu-thinking-foreground-v1 schema 1")
    if config.get("sourceSize") != [720, 1280]:
        raise ValueError("This reviewed recipe requires sourceSize [720, 1280]")
    frames = config.get("sourceFrames")
    if type(frames) is not int or not 2 <= frames <= 600:
        raise ValueError("sourceFrames must be an integer in 2..600")
    fps = Fraction(config["framesPerSecond"])
    if not 0 < fps <= 60 or frames / fps > 30:
        raise ValueError("Recipe exceeds 60 FPS or 30 seconds")
    for key in ("sourceSha256", "alphaSeedSha256"):
        value = config.get(key, "")
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"{key} must be a lowercase SHA256")
    if sha256(alpha_seed) != config["alphaSeedSha256"]:
        raise ValueError("Alpha seed SHA256 differs from the reviewed configuration")
    with Image.open(alpha_seed) as image:
        if image.mode != "L" or image.size != (720, 1280):
            raise ValueError("Alpha seed must be grayscale L at 720x1280; generated RGB is not accepted")
    def points(values, polygon=False):
        if not isinstance(values, list) or not 1 <= len(values) <= 128:
            raise ValueError("Invalid or excessive recipe coordinates")
        for point in values:
            if not isinstance(point, list) or len(point) != 2 or any(type(n) is not int for n in point):
                raise ValueError("Recipe coordinates must be integer [x,y] pairs")
            x, y = point
            if not 0 <= x <= (720 if polygon else 719) or not 0 <= y <= (1280 if polygon else 1279):
                raise ValueError("Recipe coordinates exceed source dimensions")
    polygons = config.get("static_prop_polygons", {})
    if not isinstance(polygons, dict) or not 1 <= len(polygons) <= 32:
        raise ValueError("Invalid fixed foreground polygons")
    for polygon in polygons.values():
        points(polygon, True)
        if len(polygon) < 3:
            raise ValueError("Each foreground polygon needs at least three points")
    groups = config.get("background_seed_candidates", {})
    if not isinstance(groups, dict) or not 1 <= len(groups) <= 32:
        raise ValueError("Invalid background sample groups")
    for group in groups.values():
        points(group)
    points(config["foreground_sample_points"])
    geometry = config["normalization"]
    if any(type(geometry.get(key)) is not int for key in ("canvas", "extent", "baseline")):
        raise ValueError("Normalization values must be integers")
    if not 0 < geometry["extent"] <= geometry["baseline"] < geometry["canvas"] <= 2048:
        raise ValueError("Normalization must satisfy 0 < extent <= baseline < canvas <= 2048")
    return config


def verify_webm(path: Path, frames: list[Path], fps: Fraction, ffmpeg: str, ffprobe: str) -> dict:
    process = subprocess.Popen([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                                "-c:v", "libvpx-vp9", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)
    timed_out = Event()
    def expire():
        timed_out.set()
        process.kill()
    timer = Timer(90, expire)
    timer.daemon = True
    timer.start()
    rgb_error_sum = rgb_samples = rgb_max_error = 0
    alpha_error_sum = alpha_samples = alpha_max_error = alpha_changed_pixels = 0
    try:
        for index, frame_path in enumerate(frames):
            expected = np.asarray(Image.open(frame_path).convert("RGBA"))
            data = process.stdout.read(expected.size)
            if len(data) != expected.size:
                raise RuntimeError(f"VP9 decode ended before frame {index+1}")
            decoded = np.frombuffer(data, dtype=np.uint8).reshape(expected.shape)
            expected_alpha = expected[:, :, 3]
            decoded_alpha = decoded[:, :, 3]
            alpha_delta = np.abs(decoded_alpha.astype(np.int16)-expected_alpha.astype(np.int16))
            frame_alpha_max = int(alpha_delta.max())
            if frame_alpha_max > 1:
                raise RuntimeError(f"VP9 alpha exceeds the accepted one-level soft-edge tolerance in frame {index+1}")
            if np.any(decoded_alpha[expected_alpha == 0] != 0) or np.any(decoded_alpha[expected_alpha == 255] != 255):
                raise RuntimeError(f"VP9 changed a fully transparent or opaque source pixel in frame {index+1}")
            alpha_error_sum += int(alpha_delta.sum())
            alpha_samples += int(alpha_delta.size)
            alpha_max_error = max(alpha_max_error, frame_alpha_max)
            alpha_changed_pixels += int(np.count_nonzero(alpha_delta))
            visible = expected[:, :, 3] > 0
            delta = np.abs(decoded[:, :, :3].astype(np.int16)-expected[:, :, :3].astype(np.int16))[visible]
            rgb_error_sum += int(delta.sum())
            rgb_samples += int(delta.size)
            if delta.size:
                rgb_max_error = max(rgb_max_error, int(delta.max()))
        if process.stdout.read(1):
            raise RuntimeError("VP9 decoder emitted extra frames")
        error = process.stderr.read().decode(errors="replace")
        if process.wait(timeout=10) != 0:
            raise RuntimeError(f"VP9 decoder failed: {error}")
        if timed_out.is_set():
            raise RuntimeError("VP9 decode verification timed out after 90 seconds")
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    probe = json.loads(tool([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], "VP9 metadata probe", 30).stdout)
    if len(probe["streams"]) != 1 or probe["streams"][0].get("codec_name") != "vp9":
        raise RuntimeError("Foreground WebM must contain exactly one VP9 video stream")
    timestamp_probe = json.loads(tool([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)], "VP9 timing probe", 30).stdout)
    times = [float(frame["best_effort_timestamp_time"]) for frame in timestamp_probe["frames"]]
    if len(times) != len(frames):
        raise RuntimeError("VP9 timestamp frame count differs")
    maximum_error = max(abs(timestamp-float(index/fps)) for index, timestamp in enumerate(times))
    if maximum_error > .001:
        raise RuntimeError("VP9 timestamps exceed the allowed 1 ms container quantization")
    return {"frames": len(frames), "alphaExactToProcessedPng": alpha_max_error == 0,
            "maximumAlphaDifference": alpha_max_error, "meanAlphaDifference": alpha_error_sum/max(alpha_samples, 1),
            "alphaChangedPixels": alpha_changed_pixels, "fullyTransparentAndOpaqueAlphaPreserved": True,
            "alphaNote": "Intermediate alpha may differ by at most one 8-bit level; source alpha 0 and 255 must remain exact",
            "visibleRgbMeanAbsoluteError": rgb_error_sum/max(rgb_samples, 1), "visibleRgbMaximumError": rgb_max_error,
            "rgbNote": "YUV420 chroma sampling and RGB conversion are not pixel-exact RGB",
            "sourceDurationSeconds": float(len(frames)/fps), "encodedDurationSeconds": float(probe["format"]["duration"]),
            "encodedTimeBase": probe["streams"][0]["time_base"], "maximumTimestampErrorSeconds": maximum_error}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha-seed", type=Path, required=True)
    parser.add_argument("--outline-config", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--opencv-dir", type=Path, help="Optional isolated offline-authoring dependency directory")
    parser.add_argument("--export-webm", action="store_true", help="Encode and alpha-verify a VP9 WebM in addition to the lossless one-play APNG")
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error("Output must be a new or empty directory; existing media will not be mixed or overwritten")
    if not source.is_file() or source.stat().st_size > 500_000_000:
        parser.error("Input must be a file no larger than 500 MB")
    alpha_seed = args.alpha_seed.resolve(strict=True)
    config_path = args.outline_config.resolve(strict=True)
    config = load_config(config_path, alpha_seed)
    source_sha = sha256(source)
    if source_sha != config["sourceSha256"]:
        parser.error("Source video SHA256 differs from the reviewed foreground recipe")
    cv2 = load_opencv(args.opencv_dir)
    fps = Fraction(config["framesPerSecond"])
    probe = json.loads(tool([args.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(source)], "Source metadata probe", 30).stdout)
    stream = probe["streams"][0]
    if [stream["width"], stream["height"]] != config["sourceSize"] or Fraction(stream["avg_frame_rate"]) != fps or Fraction(stream["r_frame_rate"]) != fps:
        raise RuntimeError("Source size or frame rate differs from the reviewed recipe")
    frame_probe = json.loads(tool([args.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(source)], "Source frame timing probe", 30).stdout)
    times = [float(frame["best_effort_timestamp_time"]) for frame in frame_probe["frames"]]
    if len(times) != config["sourceFrames"] or not np.allclose(np.diff(times), float(1/fps), atol=.00001, rtol=0):
        raise RuntimeError("Source frame count or constant-rate timestamps differ from the reviewed recipe")
    helper_spec = importlib.util.spec_from_file_location("companion_video_assets", Path(__file__).with_name("prepare-companion-video.py"))
    video = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(video)
    output.mkdir(parents=True, exist_ok=True)
    original = output / "source-frames"
    native = output / "native-rgba"
    masks = output / "raw-masks"
    frames_dir = output / "rgba-frames"
    for directory in (original, native, masks, frames_dir):
        directory.mkdir()
    tool([args.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-noautorotate", "-i", str(source), "-map", "0:v:0", "-an", "-fps_mode", "passthrough", str(original / "frame-%04d.png")], "Source frame extraction", 180, False)
    paths = sorted(original.glob("frame-*.png"))
    if len(paths) != config["sourceFrames"]:
        raise RuntimeError("Extracted frame count differs from source metadata")
    matte = ForegroundMatte(alpha_seed, config)
    bounds, statistics = [], []
    for index, path in enumerate(paths, 1):
        rgb = np.asarray(Image.open(path).convert("RGB"))
        rgba, raw_mask = matte.process(rgb)
        alpha = rgba[:, :, 3]
        if not np.array_equal(rgba[:, :, :3], rgb):
            raise RuntimeError(f"Source RGB changed in frame {index}")
        if any(alpha[y, x] != 0 for x, y in matte.background_points):
            raise RuntimeError(f"Known background remains opaque in frame {index}")
        if any(alpha[y, x] != 255 for x, y in config["foreground_sample_points"]):
            raise RuntimeError(f"Known foreground became transparent in frame {index}")
        bounds.append(Image.fromarray(alpha).getbbox())
        if bounds[-1] is None:
            raise RuntimeError(f"No foreground remains in frame {index}")
        Image.fromarray(rgba, "RGBA").save(native / path.name)
        Image.fromarray(raw_mask*255).save(masks / path.name)
        statistics.append({"frame": index, "sourceRgbUnchanged": True, "backgroundSamplesClear": True, "foregroundSamplesOpaque": True,
                           "visiblePixels": int(np.count_nonzero(alpha)), "softAlphaPixels": int(np.count_nonzero((alpha>0)&(alpha<255)))})
        if index == 1 or index % 24 == 0 or index == len(paths):
            print(f"Prepared foreground {index}/{len(paths)}", flush=True)
    union = [min(box[0] for box in bounds), min(box[1] for box in bounds), max(box[2] for box in bounds), max(box[3] for box in bounds)]
    geometry = config["normalization"]
    normalized = []
    for index, path in enumerate(paths, 1):
        frame = video.normalize(Image.open(native / path.name), union, geometry["canvas"], geometry["extent"], geometry["baseline"])
        target = frames_dir / path.name
        frame.save(target)
        normalized.append(target)
        if index == 1:
            frame.save(output / "still.png")
        if index in (1, (len(paths)+1)//2, len(paths)):
            video.compare(frame, output / f"frame-{index:04d}-white-black.png")
    apng = output / "foreground.png"
    video.write_apng(normalized, apng, fps, plays=1)
    video.verify_apng(apng, normalized, plays=1)
    report = {"schemaVersion": 1, "source": {"name": source.name, "sha256": source_sha, "included": False},
              "alphaSeedSha256": sha256(alpha_seed), "outlineConfigSha256": sha256(config_path), "algorithm": config["algorithm"],
              "frames": len(paths), "framesPerSecond": str(fps), "sourceDurationSeconds": float(len(paths)/fps), "durationMs": round(float(len(paths)/fps)*1000),
              "normalization": {**geometry, "commonSourceBounds": union}, "generatedRgbUsed": False, "sourceRgbUnchangedBeforeNormalization": True,
              "apng": {"file": apng.name, "sha256": sha256(apng), "bytes": apng.stat().st_size, "plays": 1, "decodedRgbaExact": True},
              "still": {"file": "still.png", "sha256": sha256(output / "still.png")},
              "versions": {"python": platform.python_version(), "numpy": np.__version__, "pillow": pillow_version, "opencv": cv2.__version__,
                           "ffmpeg": tool([args.ffmpeg, "-version"], "FFmpeg version query", 10).stdout.decode().splitlines()[0]}, "perFrame": statistics}
    if args.export_webm:
        webm = output / "foreground.webm"
        tool([args.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-framerate", str(fps), "-start_number", "1",
              "-i", str(frames_dir / "frame-%04d.png"), "-frames:v", str(len(paths)), "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
              "-lossless", "1", "-auto-alt-ref", "0", "-deadline", "good", "-cpu-used", "3", "-row-mt", "1", "-threads", "4",
              "-map_metadata", "-1", "-fflags", "+bitexact", str(webm)], "Foreground VP9 encoding", 180, False)
        report["webm"] = {"file": webm.name, "sha256": sha256(webm), "bytes": webm.stat().st_size,
                          "verification": verify_webm(webm, normalized, fps, args.ffmpeg, args.ffprobe)}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "perFrame"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
