"""
Scoreboard overlay detection and region-of-interest analysis.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
from PIL import Image

from .ocr import ScoreboardOCR
from .presets import ROI, ScoreboardPreset, load_preset


@dataclass
class ScoreboardReading:
    """Represents the parsed scoreboard state from a single video frame."""

    present: bool
    timestamp: float
    period: Optional[int] = None
    clock_seconds: Optional[float] = None
    score: Optional[Tuple[int, int]] = None
    confidence: float = 0.0

    @property
    def clock_formatted(self) -> str:
        if self.clock_seconds is None:
            return "--:--"
        total_sec = int(self.clock_seconds)
        mins = total_sec // 60
        secs = total_sec % 60
        return f"{mins:02d}:{secs:02d}"


class ScoreboardDetector:
    """
    Detects scoreboard overlay presence and extracts period, clock, and score.
    """

    def __init__(
        self,
        preset: str = "blackbear",
        preset_file: Optional[str] = None,
        custom_roi: Optional[ROI] = None,
        clock_roi: Optional[ROI] = None,
        period_roi: Optional[ROI] = None,
        score_roi: Optional[ROI] = None,
        scan_scoreboard: bool = True,
        ocr: Optional[ScoreboardOCR] = None,
    ):
        self.preset: ScoreboardPreset = load_preset(preset, preset_file)
        self.preset_name = self.preset.name
        if custom_roi is not None:
            self.preset.full_roi = custom_roi
            self.preset.candidate_rois = []
        if clock_roi is not None:
            self.preset.clock_roi = clock_roi
        if period_roi is not None:
            self.preset.period_roi = period_roi
        if score_roi is not None:
            self.preset.score_roi = score_roi

        self.scan_scoreboard = scan_scoreboard
        self.active_full_roi = self.preset.full_roi
        self.ocr = ocr or ScoreboardOCR(config=self.preset.ocr)

    def _get_pixel_bbox(
        self,
        norm_bbox: ROI,
        width: int,
        height: int,
    ) -> Tuple[int, int, int, int]:
        x1, y1, x2, y2 = norm_bbox
        if x2 <= 1.0 and y2 <= 1.0:
            return (
                int(x1 * width),
                int(y1 * height),
                int(x2 * width),
                int(y2 * height),
            )
        return (int(x1), int(y1), int(x2), int(y2))

    def crop_subregion(
        self,
        img: Image.Image,
        norm_sub_bbox: ROI,
    ) -> Image.Image:
        w, h = img.size
        px1, py1, px2, py2 = self._get_pixel_bbox(norm_sub_bbox, w, h)
        return img.crop((px1, py1, px2, py2))

    def _presence_for_roi(
        self, frame: Image.Image, full_roi: ROI
    ) -> Tuple[bool, float]:
        """
        Check whether the scoreboard is present in the frame.
        Uses high-contrast rectangular edge density and color profile.
        """
        w, h = frame.size
        px1, py1, px2, py2 = self._get_pixel_bbox(full_roi, w, h)
        crop = frame.crop((px1, py1, px2, py2)).convert("RGB")
        arr = np.array(crop)

        if arr.size == 0:
            return False, 0.0

        # Presence check 1: Scoreboard white box pixels (team/clock boxes)
        detection = self.preset.detection
        white_threshold = detection.get("white_threshold", 220)
        white_pixels = np.sum(
            (arr[:, :, 0] > white_threshold)
            & (arr[:, :, 1] > white_threshold)
            & (arr[:, :, 2] > white_threshold)
        )
        total_pixels = arr.shape[0] * arr.shape[1]
        white_ratio = white_pixels / total_pixels

        # Presence check 2: Blue header/period box (standard Black Bear TV)
        blue_pixels = np.sum(
            (arr[:, :, 2] > detection.get("blue_min_b", 160))
            & (arr[:, :, 0] < detection.get("blue_max_r", 70))
            & (arr[:, :, 1] < detection.get("blue_max_g", 160))
        )
        blue_ratio = blue_pixels / total_pixels

        if detection.get("mode") == "blackbear":
            if white_ratio > detection.get(
                "white_min_ratio", 0.08
            ) and blue_ratio > detection.get("blue_min_ratio", 0.005):
                confidence = min(
                    1.0,
                    (white_ratio / detection.get("white_conf_ratio", 0.15)) * 0.7
                    + (blue_ratio / detection.get("blue_conf_ratio", 0.02)) * 0.3,
                )
                return True, float(confidence)
            return False, 0.0

        # Generic presence check: high-contrast horizontal/vertical edges or solid blocks
        gray = np.array(crop.convert("L"))
        grad_y = np.abs(np.diff(gray, axis=0))
        grad_x = np.abs(np.diff(gray, axis=1))
        high_edges = np.sum(grad_y > detection.get("edge_threshold", 80)) + np.sum(
            grad_x > detection.get("edge_threshold", 80)
        )
        edge_ratio = high_edges / total_pixels

        is_present = white_ratio > detection.get(
            "white_min_ratio", 0.10
        ) or edge_ratio > detection.get("edge_min_ratio", 0.05)
        conf = (
            min(
                1.0, max(white_ratio, edge_ratio * detection.get("edge_conf_scale", 10))
            )
            if is_present
            else 0.0
        )
        return is_present, float(conf)

    def check_presence(self, frame: Image.Image) -> Tuple[bool, float]:
        """
        Check whether the scoreboard is present, optionally scanning candidate ROIs.
        """
        candidates = [self.preset.full_roi]
        if self.scan_scoreboard:
            candidates.extend(
                roi for roi in self.preset.candidate_rois if roi != self.preset.full_roi
            )

        best_roi = self.preset.full_roi
        best_present = False
        best_confidence = 0.0
        for roi in candidates:
            present, confidence = self._presence_for_roi(frame, roi)
            if confidence > best_confidence:
                best_roi = roi
                best_present = present
                best_confidence = confidence

        self.active_full_roi = best_roi
        return best_present, best_confidence

    def analyze_frame(self, frame: Image.Image, timestamp: float) -> ScoreboardReading:
        """
        Extract scoreboard state from frame at given timestamp.
        """
        is_present, confidence = self.check_presence(frame)
        if not is_present:
            return ScoreboardReading(
                present=False,
                timestamp=timestamp,
                confidence=confidence,
            )

        w, h = frame.size
        px1, py1, px2, py2 = self._get_pixel_bbox(self.active_full_roi, w, h)
        full_crop = frame.crop((px1, py1, px2, py2))

        clock_crop = self.crop_subregion(full_crop, self.preset.clock_roi)
        period_crop = self.crop_subregion(full_crop, self.preset.period_roi)
        score_crop = self.crop_subregion(full_crop, self.preset.score_roi)

        clock_seconds = self.ocr.read_clock(clock_crop)
        period = self.ocr.read_period(period_crop)
        score = self.ocr.read_score(score_crop)

        return ScoreboardReading(
            present=True,
            timestamp=timestamp,
            period=period,
            clock_seconds=clock_seconds,
            score=score,
            confidence=confidence,
        )
