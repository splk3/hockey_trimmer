"""
Tests for OCR parsing functions and ScoreboardOCR.
"""

import os
import subprocess
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from hockey_trimmer.detector import ScoreboardDetector
from hockey_trimmer.ocr import (
    HAS_CV2,
    OverlayTemplateMatcher,
    parse_clock_string,
    parse_period_string,
    parse_score_string,
    clean_digit_string,
    ScoreboardOCR,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# Real Black Bear TV overlay crops (full_roi) extracted through the same
# ffmpeg MJPEG path as the analyzer: (fixture, period, clock seconds, visible score).
REAL_OVERLAYS = [
    # Previous game's Period 3; the old matcher misread this tab as "1".
    ("overlay_pbk_p3_0010.png", 3, 5 * 60 + 38, (2, 6)),
    ("overlay_pbk_p3_0050.png", 3, 5 * 60 + 9, (2, 6)),
    ("overlay_pbk_p1_1805.png", 1, 13 * 60 + 13, (0, 0)),
    ("overlay_pbk_p2_3205.png", 2, 12 * 60 + 48, (0, 1)),
    # 960x540 feed: the "00" digits touch and must be split.
    ("overlay_igloo_p1_1960.png", 1, 51, (2, 0)),
    ("overlay_igloo_p3_4800.png", 3, 0, (7, 1)),
]


class TestOCR(unittest.TestCase):

    def test_clean_digit_string(self):
        self.assertEqual(clean_digit_string("O4:lO"), "04:10")
        self.assertEqual(clean_digit_string("S-B"), "5-8")
        self.assertEqual(clean_digit_string("12:34"), "12:34")

    def test_parse_clock_string_standard(self):
        self.assertEqual(parse_clock_string("15:00"), 900.0)
        self.assertEqual(parse_clock_string("04:10"), 250.0)
        self.assertEqual(parse_clock_string("0:06"), 6.0)
        self.assertEqual(parse_clock_string("00:00"), 0.0)
        self.assertEqual(parse_clock_string("0:00"), 0.0)
        self.assertEqual(parse_clock_string("20:00"), 1200.0)

    def test_parse_clock_string_ocr_errors(self):
        self.assertEqual(parse_clock_string("O4:1O"), 250.0)
        self.assertEqual(parse_clock_string("l5:00"), 900.0)
        self.assertEqual(parse_clock_string("15.00"), 900.0)
        self.assertEqual(parse_clock_string(" 04:10 "), 250.0)
        self.assertEqual(parse_clock_string("04 10"), 250.0)

    def test_parse_clock_string_fractional(self):
        self.assertEqual(parse_clock_string("14.5"), 14.5)
        self.assertEqual(parse_clock_string("05.2"), 5.2)

    def test_parse_clock_string_invalid(self):
        self.assertIsNone(parse_clock_string(""))
        self.assertIsNone(parse_clock_string("hello"))
        self.assertIsNone(parse_clock_string(None))
        self.assertIsNone(parse_clock_string("65:00"))  # Invalid hockey minute (>30)
        self.assertIsNone(parse_clock_string("12:80"))  # Invalid second (>59)

    def test_parse_period_string(self):
        self.assertEqual(parse_period_string("1"), 1)
        self.assertEqual(parse_period_string("2"), 2)
        self.assertEqual(parse_period_string("3"), 3)
        self.assertEqual(parse_period_string("4"), 4)
        self.assertEqual(parse_period_string("OT"), 4)
        self.assertEqual(parse_period_string("OVERTIME"), 4)
        self.assertEqual(parse_period_string(" 1 "), 1)
        self.assertEqual(parse_period_string("I"), 1)
        self.assertIsNone(parse_period_string(""))
        self.assertIsNone(parse_period_string(None))

    def test_parse_score_string(self):
        self.assertEqual(parse_score_string("1-0"), (1, 0))
        self.assertEqual(parse_score_string("2 - 6"), (2, 6))
        self.assertEqual(parse_score_string("0:0"), (0, 0))
        self.assertEqual(parse_score_string("10 - 2"), (10, 2))
        self.assertIsNone(parse_score_string(""))
        self.assertIsNone(parse_score_string("None"))

    def test_scoreboard_ocr_instance(self):
        ocr = ScoreboardOCR()
        self.assertTrue(hasattr(ocr, "has_tesseract"))

    def test_tesseract_availability_diagnostics(self):
        with patch("hockey_trimmer.ocr.HAS_PYTESSERACT", False):
            ocr = ScoreboardOCR()
        self.assertFalse(ocr.has_tesseract_bin)
        self.assertIn("Python package pytesseract", ocr.tesseract_warning)
        self.assertIn("pip install -r requirements.txt", ocr.tesseract_warning)

        with patch("hockey_trimmer.ocr.HAS_PYTESSERACT", True), patch(
            "hockey_trimmer.ocr.pytesseract.get_tesseract_version"
        ) as probe:
            for error in (
                FileNotFoundError("missing engine"),
                subprocess.CalledProcessError(1, ["tesseract", "--version"]),
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid version output"),
                SystemExit("Invalid tesseract version"),
            ):
                with self.subTest(error=type(error).__name__):
                    probe.side_effect = error
                    ocr = ScoreboardOCR()
                    self.assertFalse(ocr.has_tesseract_bin)
                    self.assertIn("unavailable or unusable", ocr.tesseract_warning)
                    self.assertIn(
                        "apt-get install tesseract-ocr", ocr.tesseract_warning
                    )
                    self.assertIn("brew install tesseract", ocr.tesseract_warning)
                    self.assertIn("tessdoc/Installation.html", ocr.tesseract_warning)
                    self.assertNotIn("pip install", ocr.tesseract_warning)
            probe.side_effect = None
            self.assertIsNone(ScoreboardOCR().tesseract_warning)

            probe.side_effect = RuntimeError("unexpected bug")
            with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                ScoreboardOCR()


@unittest.skipUnless(HAS_CV2, "OpenCV is required for template matching")
class TestRealOverlayOCR(unittest.TestCase):
    """Period and clock recognition on real broadcast overlay crops."""

    def setUp(self):
        self.detector = ScoreboardDetector(preset="blackbear")
        self.ocr = ScoreboardOCR()

    def _crops(self, name):
        full = Image.open(os.path.join(FIXTURES_DIR, name)).convert("RGB")
        cfg = self.detector.config
        return (
            self.detector.crop_subregion(full, cfg["period_roi"]),
            self.detector.crop_subregion(full, cfg["clock_roi"]),
        )

    def test_templates_are_bundled(self):
        matcher = OverlayTemplateMatcher()
        self.assertEqual(sorted(matcher.period_templates), [1, 2, 3, 4])
        self.assertTrue(matcher.has_digit_templates)

    def test_read_period_on_real_overlays(self):
        for name, period, _, _ in REAL_OVERLAYS:
            with self.subTest(fixture=name):
                period_crop, _ = self._crops(name)
                self.assertEqual(self.ocr.read_period(period_crop), period)

    def test_read_clock_on_real_overlays(self):
        for name, _, clock, _ in REAL_OVERLAYS:
            with self.subTest(fixture=name):
                _, clock_crop = self._crops(name)
                self.assertEqual(self.ocr.read_clock(clock_crop), float(clock))

    def test_read_score_on_real_overlays_without_tesseract(self):
        self.ocr.has_tesseract_bin = False
        for name, _, _, score in REAL_OVERLAYS:
            with self.subTest(fixture=name):
                full = Image.open(os.path.join(FIXTURES_DIR, name)).convert("RGB")
                crop = self.detector.crop_subregion(
                    full, self.detector.config["score_roi"]
                )
                self.assertEqual(self.ocr.read_score(crop), score)

    def _render_score(self, left, right, separator=True):
        matcher = self.ocr.overlay_matcher
        image = Image.new("L", matcher.SCORE_CANVAS)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 3, 13, 32), fill=255)
        draw.rectangle((95, 3, 107, 32), fill=255)
        width = 11 * (len(left) + len(right)) + 7
        x = (image.width - width) // 2
        for index, team in enumerate((left, right)):
            for digit in team:
                cell = Image.fromarray(
                    255 - matcher.digit_templates[digit].astype(np.uint8)
                ).resize((10, 14), Image.Resampling.BICUBIC)
                image.paste(cell, (x, 12))
                x += 11
            if index == 0:
                if separator:
                    draw.rectangle((x, 20, x + 4, 22), fill=255)
                x += 7
        return image

    def test_score_segmentation_supports_two_digit_scores(self):
        self.ocr.has_tesseract_bin = False
        for left, right in (("0", "0"), ("10", "12"), ("21", "9"), ("99", "8")):
            with self.subTest(score=(left, right)):
                self.assertEqual(
                    self.ocr.read_score(self._render_score(left, right)),
                    (int(left), int(right)),
                )

    def test_unreadable_scores_abstain(self):
        matcher = self.ocr.overlay_matcher
        score = self._render_score("6", "2")
        extra_separator = score.copy()
        ImageDraw.Draw(extra_separator).rectangle((50, 7, 54, 9), fill=255)
        for crop in (
            Image.new("L", matcher.SCORE_CANVAS, 0),
            Image.new("L", matcher.SCORE_CANVAS, 255),
            Image.new("L", (0, 0)),
            self._render_score("6", "2", separator=False),
            extra_separator,
            Image.fromarray(255 - np.array(score)),
            score.crop((0, 0, 56, score.height)),
        ):
            with self.subTest(size=crop.size):
                self.assertIsNone(matcher.match_score(crop))
        with patch.object(matcher, "_zncc", return_value=0.8):
            self.assertIsNone(matcher.match_score(score))
        with patch.object(matcher, "_zncc", return_value=0.5):
            self.assertIsNone(matcher.match_score(score))

    def test_non_blackbear_score_preserves_tesseract_path(self):
        other = ScoreboardOCR(preset="top_left")
        other.has_tesseract_bin = True
        with patch.object(other, "read_text", return_value="10-2") as text:
            self.assertEqual(other.read_score(self._render_score("1", "0")), (10, 2))
            text.assert_called_once()

    def test_blackbear_score_can_fall_back_to_tesseract(self):
        self.ocr.has_tesseract_bin = True
        with patch.object(
            self.ocr.overlay_matcher, "match_score", return_value=None
        ), patch.object(self.ocr, "read_text", return_value="4-1"):
            self.assertEqual(self.ocr.read_score(self._render_score("4", "1")), (4, 1))

    def test_period_three_is_not_confused_with_one(self):
        matcher = OverlayTemplateMatcher()
        period_crop, _ = self._crops("overlay_pbk_p3_0010.png")
        scores = matcher.period_scores(period_crop)
        self.assertGreater(
            scores[3] - scores[1], OverlayTemplateMatcher.PERIOD_MIN_MARGIN
        )

    def test_uncertain_period_returns_none(self):
        matcher = OverlayTemplateMatcher()
        blank = Image.fromarray(np.full((17, 83, 3), 30, dtype=np.uint8))
        self.assertIsNone(matcher.match_period(blank))
        self.assertIsNone(matcher.match_clock(blank))

    def test_period_confidence_margin_is_not_relaxed(self):
        matcher = OverlayTemplateMatcher()
        crop = Image.new("RGB", (83, 17))
        with patch.object(matcher, "period_scores", return_value={1: 0.7, 3: 0.69}):
            self.assertIsNone(matcher.match_period(crop))
        with patch.object(matcher, "period_scores", return_value={1: 0.5, 3: 0.1}):
            self.assertIsNone(matcher.match_period(crop))

    def test_ambiguous_blackbear_period_does_not_become_a_generic_guess(self):
        crop = Image.new("RGB", (83, 17))
        self.ocr.has_tesseract_bin = False
        with patch.object(
            self.ocr.overlay_matcher, "match_period", return_value=None
        ), patch.object(
            self.ocr.overlay_matcher, "period_scores", return_value={1: 0.7, 3: 0.69}
        ), patch.object(
            self.ocr.matcher, "match_period", return_value=1
        ) as generic:
            self.assertIsNone(self.ocr.read_period(crop))
            generic.assert_not_called()

    def test_ambiguous_blackbear_period_can_use_tesseract(self):
        crop = Image.new("RGB", (83, 17))
        self.ocr.has_tesseract_bin = True
        with patch.object(
            self.ocr.overlay_matcher, "match_period", return_value=None
        ), patch.object(
            self.ocr.overlay_matcher, "period_scores", return_value={1: 0.7, 3: 0.69}
        ), patch.object(
            self.ocr.matcher, "match_period", return_value=1
        ) as generic, patch.object(
            self.ocr, "read_text", return_value="3"
        ):
            self.assertEqual(self.ocr.read_period(crop), 3)
            generic.assert_not_called()

    def test_weak_blackbear_template_falls_back_to_generic_period(self):
        crop = Image.new("RGB", (83, 17))
        with patch.object(
            self.ocr.overlay_matcher, "match_period", return_value=None
        ), patch.object(
            self.ocr.overlay_matcher, "period_scores", return_value={1: 0.1}
        ), patch.object(
            self.ocr.matcher, "match_period", return_value=2
        ):
            self.assertEqual(self.ocr.read_period(crop), 2)

    def test_non_blackbear_clock_uses_generic_matcher(self):
        other = ScoreboardOCR(preset="top_left")
        self.assertIsNone(other.overlay_matcher)
        with patch.object(other.matcher, "match_clock", return_value=75.0):
            self.assertEqual(other.read_clock(Image.new("RGB", (110, 29))), 75.0)


if __name__ == "__main__":
    unittest.main()
