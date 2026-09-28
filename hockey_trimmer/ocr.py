"""
OCR, template matching, and text parsing utilities for scoreboard clocks, periods, and scores.
"""

import os
import re
from typing import Optional, Tuple, Dict
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import pytesseract

    HAS_PYTESSERACT = True
except ImportError:
    HAS_PYTESSERACT = False

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


def clean_digit_string(raw: str) -> str:
    """Normalize common OCR character substitutions for numbers."""
    substitutions = {
        "O": "0",
        "o": "0",
        "D": "0",
        "Q": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "!": "1",
        "i": "1",
        "Z": "2",
        "z": "2",
        "S": "5",
        "s": "5",
        "$": "5",
        "B": "8",
    }
    result = []
    for ch in raw:
        result.append(substitutions.get(ch, ch))
    return "".join(result)


def parse_clock_string(clock_str: str) -> Optional[float]:
    """
    Parse a clock string (e.g. '15:00', '04:10', '0:06', '12.5') into total seconds.
    Returns None if the string cannot be parsed into a valid hockey period clock.
    """
    if not clock_str or not isinstance(clock_str, str):
        return None

    cleaned = clean_digit_string(clock_str.strip())
    cleaned = re.sub(r"^[^\d]+|[^\d]+$", "", cleaned)

    match_mmss = re.search(r"(\d{1,2})[\s:;.-](\d{2})", cleaned)
    if match_mmss:
        mins = int(match_mmss.group(1))
        secs = int(match_mmss.group(2))
        if 0 <= mins <= 30 and 0 <= secs <= 59:
            return float(mins * 60 + secs)

    match_sec_dec = re.search(r"(\d{1,2})[.,](\d{1,2})", cleaned)
    if match_sec_dec:
        secs = int(match_sec_dec.group(1))
        dec = float(f"0.{match_sec_dec.group(2)}")
        if 0 <= secs < 60:
            return secs + dec

    match_zero = re.match(r"^0{1,2}$", cleaned)
    if match_zero:
        return 0.0

    return None


def parse_period_string(period_str: str) -> Optional[int]:
    """
    Parse period text into period integer (1, 2, 3, or 4 for OT).
    """
    if not period_str or not isinstance(period_str, str):
        return None

    cleaned = period_str.strip().upper()
    if "OT" in cleaned or "OVERTIME" in cleaned:
        return 4

    match = re.search(r"[1-4]", clean_digit_string(cleaned))
    if match:
        return int(match.group(0))

    return None


def parse_score_string(score_str: str) -> Optional[Tuple[int, int]]:
    """
    Parse score text like '1-0', '2 - 6', '0:0' into (team1, team2) tuple.
    """
    if not score_str or not isinstance(score_str, str):
        return None

    cleaned = clean_digit_string(score_str.strip())
    match = re.search(r"(\d{1,2})\s*[-:—–]\s*(\d{1,2})", cleaned)
    if match:
        return int(match.group(1)), int(match.group(2))

    return None


class BuiltinDigitMatcher:
    """
    Fast, self-contained template-matching digit recognizer for scoreboard overlays.
    Uses contour-based slot isolation and normalized template correlation.
    """

    FONT_PATHS = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]

    def __init__(self):
        self.clock_templates: Dict[str, np.ndarray] = {}
        self.period_templates: Dict[int, np.ndarray] = {}
        self._init_templates()

    def _init_templates(self):
        # Try loading system font first
        font = None
        font_period = None
        for p in self.FONT_PATHS:
            if os.path.exists(p):
                try:
                    font = ImageFont.truetype(p, 24)
                    font_period = ImageFont.truetype(p, 18)
                    break
                except Exception:
                    pass

        if font is None:
            font = ImageFont.load_default()
            font_period = font

        if HAS_CV2:
            # Generate 8x10 templates for 0-9
            for d in "0123456789":
                bbox = font.getbbox(d)
                w = max(1, bbox[2] - bbox[0])
                h = max(1, bbox[3] - bbox[1])
                img = Image.new("L", (w + 4, h + 4), color=0)
                draw = ImageDraw.Draw(img)
                draw.text((2 - bbox[0], 2 - bbox[1]), d, fill=255, font=font)
                arr = cv2.resize(np.array(img), (8, 10), interpolation=cv2.INTER_AREA)
                self.clock_templates[d] = arr.astype(float) / 255.0

            # Period templates for 1-4 using period-sized font
            for p in range(1, 5):
                d = str(p)
                bbox = font_period.getbbox(d)
                w = max(1, bbox[2] - bbox[0])
                h = max(1, bbox[3] - bbox[1])
                img = Image.new("L", (w + 4, h + 4), color=0)
                draw = ImageDraw.Draw(img)
                draw.text((2 - bbox[0], 2 - bbox[1]), d, fill=255, font=font_period)
                arr = cv2.resize(np.array(img), (8, 10), interpolation=cv2.INTER_AREA)
                self.period_templates[p] = arr.astype(float) / 255.0

    def match_clock(self, clock_crop: Image.Image) -> Optional[float]:
        """
        Recognize MM:SS from clock box crop using contour isolation and template correlation.
        """
        if not HAS_CV2 or not self.clock_templates:
            return None

        # Ensure consistent size (approx 100x25)
        w_orig, h_orig = clock_crop.size
        if w_orig == 0 or h_orig == 0:
            return None

        crop_resized = clock_crop.resize((100, 25), Image.Resampling.BICUBIC)
        gray = np.array(crop_resized.convert("L"))
        _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes = [
            cv2.boundingRect(c)
            for c in contours
            if 6 <= cv2.boundingRect(c)[3] <= 22 and 2 <= cv2.boundingRect(c)[2] <= 20
        ]

        if len(boxes) < 4:
            return None

        # Standard clock slot target centers: D1 (~35), D2 (~45), D3 (~61), D4 (~71)
        chosen_boxes = []
        for target_x in [35, 45, 61, 71]:
            best_b = min(boxes, key=lambda b: abs((b[0] + b[2] / 2) - target_x))
            # Validate box is within reasonable distance of slot (max 8px deviation)
            if abs((best_b[0] + best_b[2] / 2) - target_x) <= 8:
                chosen_boxes.append(best_b)
            else:
                return None

        if len(chosen_boxes) != 4:
            return None

        digits = []
        for b in chosen_boxes:
            x, y, w, h = b
            patch = thresh[y : y + h, x : x + w]
            norm = (
                cv2.resize(patch, (8, 10), interpolation=cv2.INTER_AREA).astype(float)
                / 255.0
            )

            best_d = None
            best_sim = -1.0
            for d, tmpl in self.clock_templates.items():
                sim = np.sum(norm * tmpl) / (
                    np.sqrt(np.sum(norm**2) * np.sum(tmpl**2)) + 1e-6
                )
                if sim > best_sim:
                    best_sim = sim
                    best_d = d

            if best_sim >= 0.30 and best_d is not None:
                digits.append(best_d)
            else:
                return None

        clock_str = f"{digits[0]}{digits[1]}:{digits[2]}{digits[3]}"
        return parse_clock_string(clock_str)

    def match_period(self, period_crop: Image.Image) -> Optional[int]:
        """
        Recognize period number (1, 2, 3, 4) from period tab crop.
        """
        if not HAS_CV2:
            return None

        rgb = np.array(period_crop.convert("RGB"))
        # White text has high R and G on blue background
        white_mask = ((rgb[:, :, 0] > 160) & (rgb[:, :, 1] > 160)).astype(
            np.uint8
        ) * 255

        contours, _ = cv2.findContours(
            white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes = [
            cv2.boundingRect(c)
            for c in contours
            if 5 <= cv2.boundingRect(c)[3] <= 20 and 2 <= cv2.boundingRect(c)[2] <= 20
        ]
        if not boxes:
            return None

        # Pick box closest to horizontal center
        center_x = period_crop.width / 2.0
        best_b = min(boxes, key=lambda b: abs((b[0] + b[2] / 2.0) - center_x))
        x, y, w, h = best_b
        patch = cv2.resize(
            white_mask[y : y + h, x : x + w], (6, 8), interpolation=cv2.INTER_AREA
        )
        binary = (patch > 100).astype(int)

        # Bulletproof Broadcast Font Pattern Rules:
        # Period 1 has a solid vertical stem in rows 2..5, cols 1..3 (density > 0.6)
        stem_density = np.mean(binary[2:5, 1:3])
        if stem_density > 0.60:
            return 1

        # Period 2 has bottom-left ink at row 6, col 1, but no bottom-right loop
        if binary[6, 1] == 1 and binary[5, 5] == 0:
            return 2

        # Period 3 has bottom-right loop at row 5 or 6, col 5
        if binary[5, 5] == 1 or binary[6, 5] == 1:
            return 3

        # No rule matched: report "unknown" rather than guessing Period 1,
        # which would let a previous game's P3 masquerade as a new game.
        return None


BLACKBEAR_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "blackbear"
)


class OverlayTemplateMatcher:
    """
    Grayscale template matcher using glyphs cut from Blackbear broadcast overlays.

    The Black Bear TV period digit is only ~4x8 px at 720p and is heavily
    affected by JPEG compression, so binarized pixel rules cannot tell a "1"
    from a "3". Instead crops are normalized to a canonical size, upscaled,
    and compared with averaged real-overlay templates using normalized
    cross-correlation over a small shift range. A label is returned only when
    it wins by a clear margin.
    """

    # Canonical size of the period ROI crop (native pixels at 1280x720).
    PERIOD_CANVAS = (84, 18)
    # Glyph window inside the canonical period canvas: (x1, y1, x2, y2).
    PERIOD_GLYPH_BOX = (36, 3, 52, 17)
    PERIOD_SCALE = 4
    PERIOD_MIN_SCORE = 0.55
    PERIOD_MIN_MARGIN = 0.06

    # Canonical size of the clock ROI crop and normalized digit cell size.
    CLOCK_CANVAS = (110, 29)
    CLOCK_SCALE = 4
    DIGIT_CELL = (26, 32)
    DIGIT_MIN_SCORE = 0.60
    DIGIT_MIN_MARGIN = 0.02

    def __init__(self, assets_dir: Optional[str] = None):
        self.assets_dir = assets_dir or BLACKBEAR_ASSETS_DIR
        self.period_templates: Dict[int, np.ndarray] = {}
        self.digit_templates: Dict[str, np.ndarray] = {}
        if HAS_CV2:
            self._load_templates()

    @property
    def has_period_templates(self) -> bool:
        return bool(self.period_templates)

    @property
    def has_digit_templates(self) -> bool:
        return len(self.digit_templates) == 10

    def _load_templates(self) -> None:
        for p in range(1, 5):
            path = os.path.join(self.assets_dir, f"period_{p}.png")
            if os.path.exists(path):
                self.period_templates[p] = np.array(
                    Image.open(path).convert("L"), dtype=np.float32
                )
        for d in "0123456789":
            path = os.path.join(self.assets_dir, f"clock_digit_{d}.png")
            if os.path.exists(path):
                self.digit_templates[d] = np.array(
                    Image.open(path).convert("L"), dtype=np.float32
                )

    # ------------------------------------------------------------------
    # Shared preprocessing (also used by scripts/build_ocr_templates.py)
    # ------------------------------------------------------------------
    @classmethod
    def period_canvas(cls, period_crop: Image.Image) -> np.ndarray:
        """Normalize a period crop to the upscaled canonical grayscale canvas."""
        s = cls.PERIOD_SCALE
        w, h = cls.PERIOD_CANVAS
        gray = period_crop.convert("L").resize((w, h), Image.Resampling.BICUBIC)
        up = gray.resize((w * s, h * s), Image.Resampling.BICUBIC)
        return np.array(up, dtype=np.float32)

    @classmethod
    def period_glyph(cls, period_crop: Image.Image) -> np.ndarray:
        """Return the canonical glyph window used to build period templates."""
        s = cls.PERIOD_SCALE
        x1, y1, x2, y2 = cls.PERIOD_GLYPH_BOX
        return cls.period_canvas(period_crop)[y1 * s : y2 * s, x1 * s : x2 * s]

    @classmethod
    def clock_digit_cells(cls, clock_crop: Image.Image) -> Optional[list]:
        """
        Segment the dark clock digits into normalized cells, left to right.
        Returns None if the crop does not look like an M:SS / MM:SS clock.
        """
        if not HAS_CV2:
            return None
        s = cls.CLOCK_SCALE
        w, h = cls.CLOCK_CANVAS
        if clock_crop.width == 0 or clock_crop.height == 0:
            return None
        gray = clock_crop.convert("L").resize((w, h), Image.Resampling.BICUBIC)
        gray = gray.resize((w * s, h * s), Image.Resampling.BICUBIC)
        arr = np.array(gray)

        # Digits are dark ink on a white box; Otsu separates them robustly.
        _, ink = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        H = arr.shape[0]
        boxes = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            # Digit glyphs span ~35-50% of the clock crop height; the colon
            # dots, box borders and background are rejected here.
            if (
                0.25 * H <= bh <= 0.75 * H
                and bw <= 2.2 * bh
                and area >= 0.03 * bh * bh
                and y > 0
                and y + bh < H
            ):
                boxes.extend(cls._split_merged_digits(ink, x, y, bw, bh))
        if len(boxes) not in (3, 4):
            return None
        boxes.sort(key=lambda b: b[0])
        heights = [b[3] for b in boxes]
        if max(heights) > 1.25 * min(heights):
            return None

        cells = []
        for x, y, bw, bh in boxes:
            patch = arr[y : y + bh, x : x + bw]
            cells.append(cls._normalize_digit(patch))
        return cells

    @staticmethod
    def _split_merged_digits(ink: np.ndarray, x: int, y: int, bw: int, bh: int) -> list:
        """
        Split a component holding two touching digits (common at low
        resolutions, e.g. "00") at the weakest ink column near its middle.
        """
        if bw <= 1.35 * bh:
            return [(x, y, bw, bh)]
        region = ink[y : y + bh, x : x + bw] > 0
        cols = region.sum(axis=0)
        lo, hi = int(bw * 0.35), int(bw * 0.65)
        cut = lo + int(np.argmin(cols[lo:hi]))
        pieces = []
        for x0, x1 in ((0, cut), (cut + 1, bw)):
            sub = region[:, x0:x1]
            xs = np.where(sub.any(axis=0))[0]
            if xs.size == 0:
                continue
            pieces.append((x + x0 + int(xs[0]), y, int(xs[-1] - xs[0] + 1), bh))
        return pieces

    @classmethod
    def _normalize_digit(cls, patch: np.ndarray) -> np.ndarray:
        """Place a digit patch into a fixed cell keeping its aspect ratio."""
        cw, ch = cls.DIGIT_CELL
        bh, bw = patch.shape
        scale = ch / float(bh)
        nw = max(1, min(cw, int(round(bw * scale))))
        resized = cv2.resize(patch, (nw, ch), interpolation=cv2.INTER_AREA)
        cell = np.full((ch, cw), 255, dtype=np.uint8)
        x0 = (cw - nw) // 2
        cell[:, x0 : x0 + nw] = resized
        return cell.astype(np.float32)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    @staticmethod
    def _zncc(a: np.ndarray, b: np.ndarray) -> float:
        a = a - a.mean()
        b = b - b.mean()
        denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
        if denom < 1e-6:
            return 0.0
        return float(np.sum(a * b) / denom)

    def period_scores(self, period_crop: Image.Image) -> Dict[int, float]:
        """Best normalized correlation for each period template."""
        if not HAS_CV2 or not self.period_templates:
            return {}
        canvas = self.period_canvas(period_crop)
        scores = {}
        for label, tmpl in self.period_templates.items():
            if canvas.shape[0] < tmpl.shape[0] or canvas.shape[1] < tmpl.shape[1]:
                continue
            res = cv2.matchTemplate(canvas, tmpl, cv2.TM_CCOEFF_NORMED)
            scores[label] = float(res.max())
        return scores

    def match_period(self, period_crop: Image.Image) -> Optional[int]:
        scores = self.period_scores(period_crop)
        if not scores:
            return None
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_label, best = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
        if best < self.PERIOD_MIN_SCORE or best - runner_up < self.PERIOD_MIN_MARGIN:
            return None
        return best_label

    def match_digit(self, cell: np.ndarray) -> Optional[str]:
        scores = {d: self._zncc(cell, t) for d, t in self.digit_templates.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_d, best = ranked[0]
        if best < self.DIGIT_MIN_SCORE or best - ranked[1][1] < self.DIGIT_MIN_MARGIN:
            return None
        return best_d

    def match_clock(self, clock_crop: Image.Image) -> Optional[float]:
        if not self.has_digit_templates:
            return None
        cells = self.clock_digit_cells(clock_crop)
        if cells is None:
            return None
        digits = []
        for cell in cells:
            d = self.match_digit(cell)
            if d is None:
                return None
            digits.append(d)
        text = "".join(digits)
        return parse_clock_string(f"{text[:-2]}:{text[-2:]}")


class ScoreboardOCR:
    """
    Performs image preprocessing and text/digit extraction for scoreboard elements.
    Supports both Tesseract OCR and BuiltinDigitMatcher fallback.
    """

    def __init__(self, tesseract_cmd: Optional[str] = None, preset: str = "blackbear"):
        self.has_tesseract = HAS_PYTESSERACT
        self.matcher = BuiltinDigitMatcher() if HAS_CV2 else None
        self.overlay_matcher = (
            OverlayTemplateMatcher() if HAS_CV2 and preset == "blackbear" else None
        )

        if tesseract_cmd and HAS_PYTESSERACT:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        if HAS_PYTESSERACT:
            try:
                pytesseract.get_tesseract_version()
                self.has_tesseract_bin = True
            except Exception:
                self.has_tesseract_bin = False
        else:
            self.has_tesseract_bin = False

    def preprocess_image(
        self, img: Image.Image, invert_if_dark: bool = False, scale: int = 2
    ) -> Image.Image:
        """
        Preprocess crop for OCR: grayscale, resize, and contrast stretch.
        """
        gray = img.convert("L")
        arr = np.array(gray)

        mean_val = np.mean(arr)
        if invert_if_dark and mean_val < 128:
            arr = 255 - arr

        p_low, p_high = np.percentile(arr, (5, 95))
        if p_high > p_low:
            stretched = np.clip(
                (arr - p_low) * (255.0 / (p_high - p_low)), 0, 255
            ).astype(np.uint8)
        else:
            stretched = arr

        processed_img = Image.fromarray(stretched)
        if scale > 1:
            w, h = processed_img.size
            processed_img = processed_img.resize(
                (w * scale, h * scale), Image.Resampling.BICUBIC
            )

        return processed_img

    def read_text(self, img: Image.Image, whitelist: str = "", psm: int = 7) -> str:
        """
        Extract text using Tesseract if available, otherwise return empty string.
        """
        if not self.has_tesseract_bin:
            return ""

        config = f"--psm {psm}"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"

        try:
            text = pytesseract.image_to_string(img, config=config)
            return text.strip()
        except Exception:
            return ""

    def read_clock(self, clock_crop: Image.Image) -> Optional[float]:
        """Read the game clock from an image crop."""
        # Real-overlay digit templates are the most reliable source.
        if self.overlay_matcher is not None:
            val = self.overlay_matcher.match_clock(clock_crop)
            if val is not None:
                return val

        # Fall back to the synthetic-font template matcher
        if self.matcher is not None:
            val = self.matcher.match_clock(clock_crop)
            if val is not None:
                return val

        if not self.has_tesseract_bin:
            return None

        prep = self.preprocess_image(clock_crop, invert_if_dark=False, scale=3)
        raw_text = self.read_text(prep, whitelist="0123456789:.", psm=7)
        clock_sec = parse_clock_string(raw_text)
        if clock_sec is not None:
            return clock_sec

        arr = 255 - np.array(prep)
        raw_text_inv = self.read_text(
            Image.fromarray(arr), whitelist="0123456789:.", psm=7
        )
        return parse_clock_string(raw_text_inv)

    def read_period(self, period_crop: Image.Image) -> Optional[int]:
        """Read period digit from an image crop."""
        ambiguous_overlay = False
        if self.overlay_matcher is not None:
            val = self.overlay_matcher.match_period(period_crop)
            if val is not None:
                return val
            scores = self.overlay_matcher.period_scores(period_crop)
            ambiguous_overlay = (
                bool(scores)
                and max(scores.values()) >= self.overlay_matcher.PERIOD_MIN_SCORE
            )

        if self.matcher is not None and not ambiguous_overlay:
            val = self.matcher.match_period(period_crop)
            if val is not None:
                return val

        if not self.has_tesseract_bin:
            return None

        prep = self.preprocess_image(period_crop, invert_if_dark=True, scale=3)
        raw_text = self.read_text(prep, whitelist="1234OT", psm=10)
        period = parse_period_string(raw_text)
        if period is not None:
            return period

        raw_text = self.read_text(prep, whitelist="1234OT", psm=7)
        return parse_period_string(raw_text)

    def read_score(self, score_crop: Image.Image) -> Optional[Tuple[int, int]]:
        """Read score (home, away) from an image crop."""
        if not self.has_tesseract_bin:
            return None
        prep = self.preprocess_image(score_crop, invert_if_dark=True, scale=3)
        raw_text = self.read_text(prep, whitelist="0123456789-:", psm=7)
        return parse_score_string(raw_text)
