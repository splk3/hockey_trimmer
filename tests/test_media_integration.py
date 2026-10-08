"""Real FFmpeg smoke coverage without original footage or committed videos."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hockey_trimmer.cli import analyze_video, main
from hockey_trimmer.detector import ScoreboardDetector
from hockey_trimmer.trimmer import (
    VideoTrimmer,
    extract_frame_at_timestamp,
    get_keyframes_near,
    probe_video,
    snap_to_keyframes,
)
from tests.generate_fixtures import create_regression_clip


class TestMediaIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
        if not missing:
            encoders = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            available = {
                fields[1]
                for line in encoders.splitlines()
                if len(fields := line.split()) >= 2
            }
            missing.extend(
                f"{encoder} encoder"
                for encoder in ("libx264", "aac")
                if encoder not in available
            )
        if missing:
            message = "Media integration requires: " + ", ".join(missing)
            if os.environ.get("CI"):
                raise RuntimeError(message)
            raise unittest.SkipTest(message)
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.video = Path(cls.directory.name) / "crop_smoke.mp4"
        cls.sample, cls.source = create_regression_clip(cls.video)

    def test_probe_extract_and_detector(self):
        info = probe_video(str(self.video))
        self.assertEqual(
            (info.width, info.height), (self.source["width"], self.source["height"])
        )
        self.assertAlmostEqual(info.duration, 8, delta=0.1)
        self.assertEqual(info.fps, 10)
        self.assertEqual(info.video_codec, "h264")
        detector = ScoreboardDetector()
        frame = extract_frame_at_timestamp(str(self.video), 0)
        self.assertIsNotNone(frame)
        self.assertFalse(detector.analyze_frame(frame, 0).present)
        frame = extract_frame_at_timestamp(str(self.video), 3)
        self.assertIsNotNone(frame)
        reading = detector.analyze_frame(frame, 3)
        for field, expected in self.sample["expected"].items():
            self.assertEqual(getattr(reading, field), expected, field)

    def test_keyframes_are_real_and_snapping_is_not_a_noop(self):
        response = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=key_frame,best_effort_timestamp_time",
                "-of",
                "json",
                str(self.video),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        independent = [
            float(frame["best_effort_timestamp_time"])
            for frame in json.loads(response.stdout)["frames"]
            if frame["key_frame"] == 1
        ]
        self.assertEqual(independent, [0, 2, 4, 6])
        self.assertEqual(get_keyframes_near(str(self.video), 4), independent)
        self.assertEqual(snap_to_keyframes(str(self.video), 2.4, 5.4), (2, 6))

    def test_trim_modes_preserve_decodable_video_and_audio(self):
        trimmer = VideoTrimmer(str(self.video))
        for name, reencode, snap, duration, tolerance in (
            ("copy_snap", False, True, 4, 0.3),
            ("copy_no_snap", False, False, 3, 2.1),
            ("reencode", True, True, 3, 0.1),
        ):
            with self.subTest(mode=name):
                output = Path(self.directory.name) / f"{name}.mp4"
                self.assertTrue(
                    trimmer.trim(
                        str(output), 2.4, 5.4, reencode=reencode, snap_keyframes=snap
                    )
                )
                info = probe_video(str(output))
                self.assertEqual(
                    (info.width, info.height),
                    (self.source["width"], self.source["height"]),
                )
                self.assertEqual(info.video_codec, "h264")
                self.assertAlmostEqual(info.duration, duration, delta=tolerance)
                self.assertGreaterEqual(info.duration, 3)
                metadata = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=codec_type,codec_name",
                        "-of",
                        "json",
                        str(output),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertTrue(
                    any(
                        s["codec_type"] == "audio" and s["codec_name"] == "aac"
                        for s in json.loads(metadata.stdout)["streams"]
                    )
                )
                frame = extract_frame_at_timestamp(str(output), 0.1)
                self.assertIsNotNone(frame)
                red, green, blue = frame.getpixel((info.width // 2, info.height // 2))
                self.assertAlmostEqual(red, 70, delta=5)
                self.assertAlmostEqual(green, 60, delta=5)
                self.assertAlmostEqual(blue, 90, delta=5)
                end = extract_frame_at_timestamp(
                    str(output), min(info.duration - 0.4, 2.8)
                )
                self.assertIsNotNone(end)
                self.assertGreater(
                    end.getpixel((info.width // 2, info.height // 2))[0], red + 20
                )

    def test_short_clip_is_not_a_complete_game_or_trimmed_by_check_mode(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            boundaries = analyze_video(
                str(self.video), sample_interval=1, fine_refine=False
            )
        self.assertFalse(boundaries.game_found)
        output = Path(self.directory.name) / "check_must_not_write.mp4"
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = main(
                [
                    "-i",
                    str(self.video),
                    "--check",
                    "-o",
                    str(output),
                    "--sample-interval",
                    "1",
                ]
            )
        self.assertFalse(output.exists())
        self.assertIn("No complete game", stdout.getvalue())
        self.assertEqual(result, 0)
