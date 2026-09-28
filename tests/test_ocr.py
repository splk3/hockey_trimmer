"""
Tests for OCR parsing functions and ScoreboardOCR.
"""

import os
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

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
# ffmpeg MJPEG path as the analyzer: (fixture, period, clock seconds).
REAL_OVERLAYS = [
    # Previous game's Period 3; the old matcher misread this tab as "1".
    ("overlay_pbk_p3_0010.png", 3, 5 * 60 + 38),
    ("overlay_pbk_p3_0050.png", 3, 5 * 60 + 9),
    ("overlay_pbk_p1_1805.png", 1, 13 * 60 + 13),
    ("overlay_pbk_p2_3205.png", 2, 12 * 60 + 48),
    # 960x540 feed: the "00" digits touch and must be split.
    ("overlay_igloo_p1_1960.png", 1, 51),
    ("overlay_igloo_p3_4800.png", 3, 0),
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
        for name, period, _ in REAL_OVERLAYS:
            with self.subTest(fixture=name):
                period_crop, _ = self._crops(name)
                self.assertEqual(self.ocr.read_period(period_crop), period)

    def test_read_clock_on_real_overlays(self):
        for name, _, clock in REAL_OVERLAYS:
            with self.subTest(fixture=name):
                _, clock_crop = self._crops(name)
                self.assertEqual(self.ocr.read_clock(clock_crop), float(clock))

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
