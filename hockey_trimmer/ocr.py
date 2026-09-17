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
        'O': '0', 'o': '0', 'D': '0', 'Q': '0',
        'I': '1', 'l': '1', '|': '1', '!': '1', 'i': '1',
        'Z': '2', 'z': '2',
        'S': '5', 's': '5', '$': '5',
        'B': '8',
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
    cleaned = re.sub(r'^[^\d]+|[^\d]+$', '', cleaned)
    
    match_mmss = re.search(r'(\d{1,2})[\s:;.-](\d{2})', cleaned)
    if match_mmss:
        mins = int(match_mmss.group(1))
        secs = int(match_mmss.group(2))
        if 0 <= mins <= 30 and 0 <= secs <= 59:
            return float(mins * 60 + secs)

    match_sec_dec = re.search(r'(\d{1,2})[.,](\d{1,2})', cleaned)
    if match_sec_dec:
        secs = int(match_sec_dec.group(1))
        dec = float(f"0.{match_sec_dec.group(2)}")
        if 0 <= secs < 60:
            return secs + dec

    match_zero = re.match(r'^0{1,2}$', cleaned)
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
    if 'OT' in cleaned or 'OVERTIME' in cleaned:
        return 4

    match = re.search(r'[1-4]', clean_digit_string(cleaned))
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
    match = re.search(r'(\d{1,2})\s*[-:—–]\s*(\d{1,2})', cleaned)
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
            for d in '0123456789':
                bbox = font.getbbox(d)
                w = max(1, bbox[2] - bbox[0])
                h = max(1, bbox[3] - bbox[1])
                img = Image.new('L', (w + 4, h + 4), color=0)
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
                img = Image.new('L', (w + 4, h + 4), color=0)
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
        gray = np.array(crop_resized.convert('L'))
        _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = [
            cv2.boundingRect(c) for c in contours
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
            patch = thresh[y:y + h, x:x + w]
            norm = cv2.resize(patch, (8, 10), interpolation=cv2.INTER_AREA).astype(float) / 255.0

            best_d = None
            best_sim = -1.0
            for d, tmpl in self.clock_templates.items():
                sim = np.sum(norm * tmpl) / (np.sqrt(np.sum(norm ** 2) * np.sum(tmpl ** 2)) + 1e-6)
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

        rgb = np.array(period_crop.convert('RGB'))
        # White text has high R and G on blue background
        white_mask = ((rgb[:, :, 0] > 160) & (rgb[:, :, 1] > 160)).astype(np.uint8) * 255

        contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = [
            cv2.boundingRect(c) for c in contours
            if 5 <= cv2.boundingRect(c)[3] <= 20 and 2 <= cv2.boundingRect(c)[2] <= 20
        ]
        if not boxes:
            return None

        # Pick box closest to horizontal center
        center_x = period_crop.width / 2.0
        best_b = min(boxes, key=lambda b: abs((b[0] + b[2] / 2.0) - center_x))
        x, y, w, h = best_b
        patch = cv2.resize(white_mask[y:y + h, x:x + w], (6, 8), interpolation=cv2.INTER_AREA)
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

        return 1


class ScoreboardOCR:
    """
    Performs image preprocessing and text/digit extraction for scoreboard elements.
    Supports both Tesseract OCR and BuiltinDigitMatcher fallback.
    """

    def __init__(self, tesseract_cmd: Optional[str] = None):
        self.has_tesseract = HAS_PYTESSERACT
        self.matcher = BuiltinDigitMatcher() if HAS_CV2 else None

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

    def preprocess_image(self, img: Image.Image, invert_if_dark: bool = False, scale: int = 2) -> Image.Image:
        """
        Preprocess crop for OCR: grayscale, resize, and contrast stretch.
        """
        gray = img.convert('L')
        arr = np.array(gray)

        mean_val = np.mean(arr)
        if invert_if_dark and mean_val < 128:
            arr = 255 - arr

        p_low, p_high = np.percentile(arr, (5, 95))
        if p_high > p_low:
            stretched = np.clip((arr - p_low) * (255.0 / (p_high - p_low)), 0, 255).astype(np.uint8)
        else:
            stretched = arr

        processed_img = Image.fromarray(stretched)
        if scale > 1:
            w, h = processed_img.size
            processed_img = processed_img.resize((w * scale, h * scale), Image.Resampling.BICUBIC)

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
        # Try built-in fast template matcher first
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
        raw_text_inv = self.read_text(Image.fromarray(arr), whitelist="0123456789:.", psm=7)
        return parse_clock_string(raw_text_inv)

    def read_period(self, period_crop: Image.Image) -> Optional[int]:
        """Read period digit from an image crop."""
        if self.matcher is not None:
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
