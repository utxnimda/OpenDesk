"""Offline source-RGB foreground recipe. OpenCV is an authoring dependency only.

The reviewed recipe is bound to a source hash and an explicit alpha seed/config.
No generated RGB, optical-flow-warped video frames, or synthesized poses are used.
"""
from pathlib import Path
import sys

import numpy as np
from PIL import Image

cv2 = None


def load_opencv(directory: Path | None = None):
    global cv2
    if directory is not None:
        location = directory.resolve(strict=True)
        if not location.is_dir():
            raise ValueError("--opencv-dir must name an existing authoring dependency directory")
        sys.path.insert(0, str(location))
    try:
        import cv2 as module
    except ImportError as error:
        raise RuntimeError("OpenCV is required for offline foreground authoring; install packaging/requirements-companion-assets.txt or provide --opencv-dir.") from error
    cv2 = module
    return module


class ForegroundMatte:
    def __init__(self, alpha_seed: Path, config: dict):
        if cv2 is None:
            raise RuntimeError("Call load_opencv before constructing the foreground recipe")
        self.config = config
        self.width, self.height = config["sourceSize"]
        with Image.open(alpha_seed) as image:
            if image.mode != "L" or image.size != (self.width, self.height):
                raise ValueError("Alpha seed must be a grayscale L PNG matching sourceSize")
            self.reference = np.array(image)
        self.fixed_props = np.zeros((self.height, self.width), dtype=np.uint8)
        for points in config["static_prop_polygons"].values():
            cv2.fillPoly(self.fixed_props, [np.asarray(points, dtype=np.int32)], 1)
        self.fixed_prop_core = cv2.erode(self.fixed_props, np.ones((9, 9), np.uint8))
        self.background_points = [point for group in config["background_seed_candidates"].values() for point in group]
        self.reference_background_near = cv2.dilate((self.reference < 64).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        self.yy, self.xx = np.indices((self.height, self.width), dtype=np.float32)
        self.previous_gray = None
        self.previous_mask = None
        self.frame_index = 0

    def process(self, rgb: np.ndarray):
        if rgb.dtype != np.uint8 or rgb.shape != (self.height, self.width, 3):
            raise ValueError("Foreground input must be the source-sized uint8 RGB frame")
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if self.previous_mask is None:
            prior = self.reference
        else:
            backward = cv2.calcOpticalFlowFarneback(gray, self.previous_gray, None, .5, 4, 21, 4, 7, 1.5, 0)
            prior = cv2.remap(self.previous_mask.astype(np.float32)*255, self.xx+backward[:, :, 0], self.yy+backward[:, :, 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        mask = self.refine(rgb, prior, 5 if self.frame_index == 0 else 3)
        alpha = self.clean_crown_wall(rgb, self.clean_lower_wall(rgb, self.soft_alpha(rgb, mask)))
        self.previous_gray, self.previous_mask = gray, mask
        self.frame_index += 1
        return np.dstack((rgb, alpha)), mask

    def refine(self, rgb, prior, iterations=4):
        cv2.setRNGSeed(0)
        binary = (prior > 127).astype(np.uint8)
        fg = cv2.erode(binary, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
        possible = cv2.dilate(binary, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (51, 51)))
        # A warped prior is not authoritative at moving hair boundaries. Confident
        # current-frame pink/skin/white and saturated blue ribbons may expand it,
        # while room-blue pixels there are never hard foreground merely by history.
        color = rgb.astype(np.int16)
        reliable = (color[:, :, 0] > 65) & (color[:, :, 0]-color[:, :, 1] > -12) & (color[:, :, 0]-color[:, :, 2] > -25)
        blue_ribbon = (color[:, :, 0] > 30) & (color[:, :, 0]-color[:, :, 1] > -30) & (color[:, :, 2]-color[:, :, 1] > 20)
        yy, xx = np.indices(binary.shape)
        moving_outline = ((xx > 300) & (yy > 250) & (yy < 700)) | ((xx > 275) & (xx < 405) & (yy >= 700) & (yy < 950))
        current_foreground = (reliable & moving_outline).astype(np.uint8)
        current_core = cv2.erode(current_foreground, np.ones((3, 3), np.uint8))
        possible |= cv2.dilate(current_foreground, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (65, 65)))
        fg[moving_outline] &= current_foreground[moving_outline]
        fg |= current_core
        labels = np.where(binary > 0, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
        labels[possible == 0] = cv2.GC_BGD
        labels[fg > 0] = cv2.GC_FGD
        labels[self.fixed_prop_core > 0] = cv2.GC_FGD
        # Measured room pixels stay background across every source frame. Conditional
        # chair-gap points are deliberately excluded because hair crosses them.
        for x, y in self.background_points:
            cv2.circle(labels, (x, y), 2, int(cv2.GC_BGD), -1)
        bg_model = np.zeros((1, 65), dtype=np.float64)
        fg_model = np.zeros((1, 65), dtype=np.float64)
        cv2.grabCut(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), labels, None, bg_model, fg_model, iterations, cv2.GC_INIT_WITH_MASK)
        mask = ((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)).astype(np.uint8)
        return mask

    def soft_alpha(self, rgb, mask):
        core = cv2.erode(mask, np.ones((3, 3), np.uint8))
        background = cv2.erode(1-mask, np.ones((3, 3), np.uint8))
        foreground_count = cv2.boxFilter(core.astype(np.float32), -1, (7, 7), normalize=False)
        background_count = cv2.boxFilter(background.astype(np.float32), -1, (7, 7), normalize=False)
        color = rgb.astype(np.float32)
        foreground_rgb = cv2.boxFilter(color * core[:, :, None], -1, (7, 7), normalize=False) / np.maximum(foreground_count[:, :, None], 1)
        background_rgb = cv2.boxFilter(color * background[:, :, None], -1, (7, 7), normalize=False) / np.maximum(background_count[:, :, None], 1)
        direction = foreground_rgb - background_rgb
        projection = np.clip(np.sum((color-background_rgb)*direction, axis=2) / np.maximum(np.sum(direction*direction, axis=2), 25), 0, 1)
        band = (foreground_count > 0) & (background_count > 0) & (core == 0) & (background == 0)
        alpha = mask.astype(np.float32)
        alpha[band] = projection[band]
        alpha[alpha < .025] = 0
        alpha[alpha > .975] = 1
        return np.rint(alpha*255).astype(np.uint8)

    def clean_lower_wall(self, rgb, alpha):
        """Remove the measured open blue wall under the desk, not desk/skin pixels.

        The static prop outlines and reference-background prior limit this correction;
        current source color protects a moving thigh or other foreground occlusion.
        """
        color = rgb.astype(np.int16)
        yy, xx = np.indices(alpha.shape)
        lower_opening = (xx > 32) & (xx < 340) & (yy > 1120)
        wall_blue = (color[:, :, 0] < 115) & (color[:, :, 1]-color[:, :, 0] > 12) & (color[:, :, 2]-color[:, :, 0] > 18) & (color[:, :, 2]-color[:, :, 1] < 45)
        remove = lower_opening & wall_blue & self.reference_background_near & (self.fixed_props == 0)
        result = alpha.copy()
        result[remove] = 0
        return result

    def clean_crown_wall(self, rgb, alpha):
        """Clear only exterior-connected room blue above the pink hair crown.

        The right/left blue ribbons lie below this inspected crown region. Pink
        source hair and its neutral/dark outlines do not satisfy the wall color test.
        """
        color = rgb.astype(np.int16)
        yy, xx = np.indices(alpha.shape)
        inspected_crown = (xx > 300) & (yy > 230) & (((xx < 720) & (yy < 370)) | ((xx < 600) & (yy < 410)))
        wall_blue = (color[:, :, 0] < 130) & (color[:, :, 1]-color[:, :, 0] >= 0) & (color[:, :, 2]-color[:, :, 0] > 12) & (color[:, :, 2]-color[:, :, 1] > 8) & (color[:, :, 2]-color[:, :, 1] < 40)
        candidate = (wall_blue & inspected_crown).astype(np.uint8)
        count, labels = cv2.connectedComponents(candidate, connectivity=8)
        exterior_labels = np.unique(labels[(alpha == 0) & (candidate > 0)])
        exterior_labels = exterior_labels[exterior_labels != 0]
        remove = np.isin(labels, exterior_labels) & (candidate > 0)
        result = alpha.copy()
        result[remove] = 0
        return result
