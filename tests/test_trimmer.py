"""
Tests for trimmer utility functions and keyframe logic.
"""

import io
import unittest
from unittest.mock import patch, MagicMock
from hockey_trimmer.trimmer import (
    VideoInfo,
    snap_to_keyframes,
    VideoTrimmer,
)


class TestVideoTrimmer(unittest.TestCase):

    @patch("hockey_trimmer.trimmer.snap_to_keyframes")
    @patch("hockey_trimmer.trimmer.subprocess.run")
    @patch("hockey_trimmer.trimmer.probe_video")
    def test_encoding_modes(self, mock_probe, mock_run, mock_snap):
        mock_run.return_value = MagicMock(returncode=0)
        trimmer = VideoTrimmer("game.mp4")
        for hevc, codec, crf, audio in (
            (True, "libx265", "21", "copy"),
            (False, "libx264", "18", "aac"),
        ):
            with self.subTest(hevc=hevc):
                self.assertTrue(
                    trimmer.trim("out.mp4", 14, 45, reencode=not hevc, hevc=hevc)
                )
                cmd = mock_run.call_args.args[0]
                for flag, value in (
                    ("-c:v", codec),
                    ("-preset", "fast"),
                    ("-crf", crf),
                    ("-c:a", audio),
                    ("-ss", "14.000"),
                    ("-to", "45.000"),
                ):
                    self.assertEqual(cmd[cmd.index(flag) + 1], value)
                if hevc:
                    for flag, value in (
                        ("-tag:v", "hvc1"),
                        ("-fps_mode", "passthrough"),
                        ("-x265-params", "log-level=error"),
                        ("-map_metadata", "0"),
                        ("-map_chapters", "0"),
                    ):
                        self.assertEqual(cmd[cmd.index(flag) + 1], value)
                    self.assertIn("0:v:0", cmd)
                    self.assertIn("0:a?", cmd)
                    self.assertNotIn("-b:a", cmd)
                else:
                    self.assertEqual(cmd[cmd.index("-b:a") + 1], "192k")
        mock_snap.assert_not_called()

    @patch("hockey_trimmer.trimmer.subprocess.run")
    @patch("hockey_trimmer.trimmer.probe_video")
    def test_conflicting_modes_are_rejected(self, mock_probe, mock_run):
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            VideoTrimmer("game.mp4").trim("out.mp4", 0, 10, reencode=True, hevc=True)
        mock_run.assert_not_called()

    @patch("hockey_trimmer.trimmer.subprocess.run")
    @patch("hockey_trimmer.trimmer.probe_video")
    def test_hevc_failure_reports_ffmpeg_stderr(self, mock_probe, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Unknown encoder 'libx265'"
        )
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            success = VideoTrimmer("game.mp4").trim("out.mp4", 0, 10, hevc=True)
        self.assertFalse(success)
        self.assertIn("Unknown encoder 'libx265'", stderr.getvalue())
        mock_run.assert_called_once()

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
