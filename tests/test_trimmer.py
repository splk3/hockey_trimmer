"""
Tests for trimmer utility functions and keyframe logic.
"""

import unittest
from unittest.mock import patch, MagicMock
from hockey_trimmer.trimmer import (
    VideoInfo,
    snap_to_keyframes,
    VideoTrimmer,
)


class TestVideoTrimmer(unittest.TestCase):

    def test_video_info_dataclass(self):
        info = VideoInfo(
            path="game.mp4",
            duration=3600.0,
            width=1280,
            height=720,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
        )
        self.assertEqual(info.duration, 3600.0)
        self.assertEqual(info.width, 1280)
        self.assertEqual(info.height, 720)

    def test_snap_to_keyframes(self):
        # Mock keyframes at t = 0, 10, 20, 30, 40, 50, 60
        fake_keyframes = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

        with patch(
            "hockey_trimmer.trimmer.get_keyframes_near", return_value=fake_keyframes
        ):
            # cut_start = 14.0 -> should snap to 10.0 (preceding)
            # cut_end = 45.0 -> should snap to 50.0 (subsequent)
            snapped_start, snapped_end = snap_to_keyframes("video.mp4", 14.0, 45.0)
            self.assertEqual(snapped_start, 10.0)
            self.assertEqual(snapped_end, 50.0)

    @patch("subprocess.run")
    def test_trimmer_stream_copy(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with patch("hockey_trimmer.trimmer.probe_video") as mock_probe:
            mock_probe.return_value = VideoInfo(
                "game.mp4", 3600.0, 1280, 720, 30.0, "h264", "aac"
            )
            with patch(
                "hockey_trimmer.trimmer.snap_to_keyframes", return_value=(10.0, 50.0)
            ):
                trimmer = VideoTrimmer("game.mp4")
                success = trimmer.trim(
                    "out.mp4", 14.0, 45.0, reencode=False, snap_keyframes=True
                )

                self.assertTrue(success)
                self.assertTrue(mock_run.called)
                cmd = mock_run.call_args[0][0]
                self.assertIn("ffmpeg", cmd)
                self.assertIn("-c", cmd)
                self.assertIn("copy", cmd)
                self.assertIn("10.000", cmd)
                self.assertIn("50.000", cmd)


if __name__ == "__main__":
    unittest.main()
