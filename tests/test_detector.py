"""
Tests for ScoreboardDetector presence check and ROI configuration.
"""

import unittest
from PIL import Image, ImageDraw
from hockey_trimmer.detector import ScoreboardDetector


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

    def test_custom_subregion_overrides(self):
        detector = ScoreboardDetector(
            preset="blackbear",
            custom_roi=(0.0, 0.0, 0.5, 0.5),
            clock_roi=(0.1, 0.2, 0.3, 0.4),
            period_roi=(0.2, 0.3, 0.4, 0.5),
            score_roi=(0.3, 0.4, 0.5, 0.6),
            scan_scoreboard=False,
        )
        self.assertEqual(detector.preset.full_roi, (0.0, 0.0, 0.5, 0.5))
        self.assertEqual(detector.preset.clock_roi, (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(detector.preset.period_roi, (0.2, 0.3, 0.4, 0.5))
        self.assertEqual(detector.preset.score_roi, (0.3, 0.4, 0.5, 0.6))

    def test_candidate_scanning_finds_moved_scoreboard(self):
        frame = Image.new("RGB", (1280, 720), color=(180, 180, 180))
        draw = ImageDraw.Draw(frame)
        draw.rectangle([896, 14, 1254, 158], fill=(10, 10, 10))
        draw.rectangle([930, 35, 1120, 90], fill=(255, 255, 255))
        draw.rectangle([1120, 35, 1230, 90], fill=(255, 255, 255))

        detector = ScoreboardDetector(preset="top_left")
        is_present, conf = detector.check_presence(frame)
        self.assertTrue(is_present)
        self.assertGreater(conf, 0.0)
        self.assertEqual(detector.active_full_roi, (0.70, 0.02, 0.98, 0.22))


if __name__ == "__main__":
    unittest.main()
