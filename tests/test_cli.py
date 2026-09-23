"""
Tests for CLI argument parsing and validation.
"""

import io
import unittest
from unittest.mock import patch
from hockey_trimmer.cli import parse_args, format_seconds, main


class TestCLI(unittest.TestCase):

    def test_format_seconds(self):
        self.assertEqual(format_seconds(0), "00:00")
        self.assertEqual(format_seconds(65), "01:05")
        self.assertEqual(format_seconds(3665), "01:01:05")

    def test_parse_args_valid(self):
        args = parse_args(["-i", "game.mp4", "-o", "trimmed.mp4"])
        self.assertEqual(args.input, "game.mp4")
        self.assertEqual(args.output, "trimmed.mp4")
        self.assertFalse(args.check)
        self.assertEqual(args.buffer_before, 15.0)
        self.assertEqual(args.buffer_after, 15.0)
        self.assertEqual(args.sample_interval, 10.0)
        self.assertEqual(args.preset, "blackbear")

    def test_parse_args_check_mode(self):
        args = parse_args(
            [
                "-i",
                "game.mp4",
                "--check",
                "--buffer-before",
                "10",
                "--buffer-after",
                "20",
            ]
        )
        self.assertEqual(args.input, "game.mp4")
        self.assertTrue(args.check)
        self.assertIsNone(args.output)
        self.assertEqual(args.buffer_before, 10.0)
        self.assertEqual(args.buffer_after, 20.0)

    def test_parse_args_custom_roi_and_reencode(self):
        args = parse_args(
            [
                "-i",
                "game.mp4",
                "-o",
                "out.mp4",
                "--roi",
                "0.05,0.05,0.30,0.25",
                "--clock-roi",
                "0.20,0.50,0.80,0.80",
                "--preset-file",
                "custom.yaml",
                "--force-preset-roi",
                "--reencode",
            ]
        )
        self.assertEqual(args.roi, "0.05,0.05,0.30,0.25")
        self.assertEqual(args.clock_roi, "0.20,0.50,0.80,0.80")
        self.assertEqual(args.preset_file, "custom.yaml")
        self.assertTrue(args.force_preset_roi)
        self.assertTrue(args.reencode)

    def test_parse_args_calibration(self):
        args = parse_args(
            [
                "-i",
                "game.mp4",
                "--calibrate-preset",
                "preset.json",
                "--calibrate-timestamp",
                "12.5",
            ]
        )
        self.assertEqual(args.calibrate_preset, "preset.json")
        self.assertEqual(args.calibrate_timestamp, 12.5)

    def test_main_missing_output_without_check(self):
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ret = main(["-i", "game.mp4"])
        self.assertEqual(ret, 1)
        self.assertIn("output must be specified unless --check is used", buf.getvalue())

    def test_main_invalid_roi(self):
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ret = main(["-i", "game.mp4", "-o", "out.mp4", "--roi", "invalid,roi"])
        self.assertEqual(ret, 1)
        self.assertIn("--roi must be 4 comma-separated numbers", buf.getvalue())

    def test_main_invalid_sub_roi(self):
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            ret = main(
                [
                    "-i",
                    "game.mp4",
                    "-o",
                    "out.mp4",
                    "--clock-roi",
                    "0.3,0.3,0.1,0.2",
                ]
            )
        self.assertEqual(ret, 1)
        self.assertIn("--clock-roi", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
