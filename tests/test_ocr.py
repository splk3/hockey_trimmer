"""
Tests for OCR parsing functions and ScoreboardOCR.
"""

import unittest
from hockey_trimmer.ocr import (
    parse_clock_string,
    parse_period_string,
    parse_score_string,
    clean_digit_string,
    ScoreboardOCR,
)


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


if __name__ == "__main__":
    unittest.main()
