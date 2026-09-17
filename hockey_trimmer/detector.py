"""
Scoreboard overlay detection and region-of-interest analysis.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
from PIL import Image

from .ocr import ScoreboardOCR


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

    PRESETS = {
        "blackbear": {
            # Bounding box of full overlay relative to frame (width, height): (x1, y1, x2, y2)
            "full_roi": (0.045, 0.020, 0.255, 0.140),
            # Relative to full_roi: (x1, y1, x2, y2)
            "period_roi": (0.34, 0.02, 0.65, 0.22),
            "clock_roi": (0.29, 0.56, 0.70, 0.90),
            "score_roi": (0.30, 0.20, 0.70, 0.58),
        },
        "top_left": {
            "full_roi": (0.02, 0.02, 0.30, 0.22),
            "period_roi": (0.30, 0.0, 0.70, 0.25),
            "clock_roi": (0.25, 0.50, 0.75, 0.90),
            "score_roi": (0.25, 0.20, 0.75, 0.55),
        },
        "top_center": {
            "full_roi": (0.35, 0.02, 0.65, 0.18),
            "period_roi": (0.05, 0.10, 0.30, 0.90),
            "clock_roi": (0.35, 0.10, 0.65, 0.90),
            "score_roi": (0.70, 0.10, 0.95, 0.90),
        },
    }

    def __init__(
        self,
        preset: str = "blackbear",
        custom_roi: Optional[Tuple[float, float, float, float]] = None,
        ocr: Optional[ScoreboardOCR] = None,
    ):
        self.preset_name = preset.lower()
        self.config = self.PRESETS.get(
            self.preset_name, self.PRESETS["blackbear"]
        ).copy()
        if custom_roi is not None:
            self.config["full_roi"] = custom_roi

        self.ocr = ocr or ScoreboardOCR()

    def _get_pixel_bbox(
        self,
        norm_bbox: Tuple[float, float, float, float],
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
        norm_sub_bbox: Tuple[float, float, float, float],
    ) -> Image.Image:
        w, h = img.size
        px1, py1, px2, py2 = self._get_pixel_bbox(norm_sub_bbox, w, h)
        return img.crop((px1, py1, px2, py2))

    def check_presence(self, frame: Image.Image) -> Tuple[bool, float]:
        """
        Check whether the scoreboard is present in the frame.
        Uses high-contrast rectangular edge density and color profile.
        """
        w, h = frame.size
        px1, py1, px2, py2 = self._get_pixel_bbox(self.config["full_roi"], w, h)
        crop = frame.crop((px1, py1, px2, py2)).convert("RGB")
        arr = np.array(crop)

        if arr.size == 0:
            return False, 0.0

        # Presence check 1: Scoreboard white box pixels (team/clock boxes)
        white_pixels = np.sum(
            (arr[:, :, 0] > 220) & (arr[:, :, 1] > 220) & (arr[:, :, 2] > 220)
        )
        total_pixels = arr.shape[0] * arr.shape[1]
        white_ratio = white_pixels / total_pixels

        # Presence check 2: Blue header/period box (standard Black Bear TV)
        blue_pixels = np.sum(
            (arr[:, :, 2] > 160) & (arr[:, :, 0] < 70) & (arr[:, :, 1] < 160)
        )
        blue_ratio = blue_pixels / total_pixels

        if self.preset_name == "blackbear":
            if white_ratio > 0.08 and blue_ratio > 0.005:
                confidence = min(
                    1.0, (white_ratio / 0.15) * 0.7 + (blue_ratio / 0.02) * 0.3
                )
                return True, float(confidence)
            return False, 0.0

        # Generic presence check: high-contrast horizontal/vertical edges or solid blocks
        gray = np.array(crop.convert("L"))
        grad_y = np.abs(np.diff(gray, axis=0))
        grad_x = np.abs(np.diff(gray, axis=1))
        high_edges = np.sum(grad_y > 80) + np.sum(grad_x > 80)
        edge_ratio = high_edges / total_pixels

        is_present = white_ratio > 0.10 or edge_ratio > 0.05
        conf = min(1.0, edge_ratio * 10) if is_present else 0.0
        return is_present, float(conf)

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
        px1, py1, px2, py2 = self._get_pixel_bbox(self.config["full_roi"], w, h)
        full_crop = frame.crop((px1, py1, px2, py2))

        clock_crop = self.crop_subregion(full_crop, self.config["clock_roi"])
        period_crop = self.crop_subregion(full_crop, self.config["period_roi"])
        score_crop = self.crop_subregion(full_crop, self.config["score_roi"])

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
