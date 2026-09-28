"""
Tests for ScoreboardDetector presence check and ROI configuration.
"""

import unittest
from PIL import Image, ImageDraw
from hockey_trimmer.detector import ScoreboardDetector
from hockey_trimmer.ocr import HAS_CV2


class TestScoreboardDetector(unittest.TestCase):

    def setUp(self):
        # Create a synthetic 1280x720 frame with a synthetic Black Bear TV scoreboard
        self.sb_frame = Image.new("RGB", (1280, 720), color=(180, 180, 180))
        draw = ImageDraw.Draw(self.sb_frame)
        # Blue tab at top center
        draw.rectangle([140, 10, 230, 35], fill=(0, 102, 255))
        # Away team white box
        draw.rectangle([40, 35, 140, 110], fill=(255, 255, 255))
        # Score black box
        draw.rectangle([140, 35, 230, 110], fill=(10, 10, 10))
        # Home team white box
        draw.rectangle([230, 35, 330, 110], fill=(255, 255, 255))
        # Clock white box
        draw.rectangle([140, 110, 230, 155], fill=(255, 255, 255))

        # Empty frame
        self.empty_frame = Image.new("RGB", (1280, 720), color=(200, 200, 205))

    def test_detector_presence(self):
        detector = ScoreboardDetector(preset="blackbear")

        is_present, conf = detector.check_presence(self.sb_frame)
        self.assertTrue(is_present)
        self.assertGreater(conf, 0.0)

        is_present_empty, conf_empty = detector.check_presence(self.empty_frame)
        self.assertFalse(is_present_empty)
        self.assertEqual(conf_empty, 0.0)

    def test_detector_analyze_frame(self):
        detector = ScoreboardDetector(preset="blackbear")
        reading = detector.analyze_frame(self.sb_frame, timestamp=42.0)
        self.assertTrue(reading.present)
        self.assertEqual(reading.timestamp, 42.0)

    @unittest.skipUnless(HAS_CV2, "OpenCV is required for template matching")
    def test_template_ocr_is_selected_only_for_blackbear(self):
        self.assertIsNotNone(ScoreboardDetector("blackbear").ocr.overlay_matcher)
        for preset in ("top_left", "top_center"):
            with self.subTest(preset=preset):
                detector = ScoreboardDetector(preset)
                self.assertIsNone(detector.ocr.overlay_matcher)
                self.assertIsNotNone(detector.ocr.matcher)

        custom = ScoreboardDetector("blackbear", custom_roi=(0.1, 0.1, 0.3, 0.3))
        self.assertIsNotNone(custom.ocr.overlay_matcher)


if __name__ == "__main__":
    unittest.main()
