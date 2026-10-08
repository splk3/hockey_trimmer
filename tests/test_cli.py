"""
Tests for CLI argument parsing and validation.
"""

import io
import unittest
from unittest.mock import patch
from hockey_trimmer.cli import parse_args, format_seconds, main, analyze_video
from hockey_trimmer.detector import ScoreboardReading
from hockey_trimmer.timeline import GameBoundaries, GameState
from hockey_trimmer.trimmer import VideoInfo


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
        self.assertEqual(args.end_confirm_window, 150.0)
        self.assertEqual(args.end_confirm_interval, 10.0)
        self.assertFalse(args.no_end_confirm)

    def test_parse_args_end_confirmation_options(self):
        args = parse_args(
            [
                "-i",
                "game.mp4",
                "--check",
                "--end-confirm-window",
                "240",
                "--end-confirm-interval",
                "15",
            ]
        )
        self.assertEqual(args.end_confirm_window, 240.0)
        self.assertEqual(args.end_confirm_interval, 15.0)
        self.assertFalse(args.no_end_confirm)

        args = parse_args(["-i", "game.mp4", "--check", "--no-end-confirm"])
        self.assertTrue(args.no_end_confirm)

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
                "--reencode",
            ]
        )
        self.assertEqual(args.roi, "0.05,0.05,0.30,0.25")
        self.assertTrue(args.reencode)

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


def _scripted_reading(timestamp: float) -> ScoreboardReading:
    """
    Synthetic scoreboard feed: P1 100-1000s, P2 1100-2000s, P3 2100-3000s with a
    single-frame OCR misread of 0:01 at 2500s and the real final horn at 3000s.
    """
    ts = timestamp
    if ts < 100:
        return ScoreboardReading(present=False, timestamp=ts)
    if ts == 2500:
        return ScoreboardReading(
            present=True, timestamp=ts, period=3, clock_seconds=1.0, score=(3, 1)
        )
    if ts < 1000:
        return ScoreboardReading(
            present=True, timestamp=ts, period=1, clock_seconds=900.0 - (ts - 100)
        )
    if ts < 1100:
        return ScoreboardReading(
            present=True, timestamp=ts, period=1, clock_seconds=0.0
        )
    if ts < 2000:
        return ScoreboardReading(
            present=True, timestamp=ts, period=2, clock_seconds=900.0 - (ts - 1100)
        )
    if ts < 2100:
        return ScoreboardReading(
            present=True, timestamp=ts, period=2, clock_seconds=0.0
        )
    if ts < 3000:
        return ScoreboardReading(
            present=True,
            timestamp=ts,
            period=3,
            clock_seconds=max(0.0, 900.0 - (ts - 2100)),
            score=(3, 1),
        )
    return ScoreboardReading(
        present=True, timestamp=ts, period=3, clock_seconds=0.0, score=(3, 1)
    )


class _FakeDetector:
    def __init__(self, *args, **kwargs):
        pass

    def analyze_frame(self, frame, timestamp):
        return _scripted_reading(timestamp)


class TestAnalyzeVideoEndConfirmation(unittest.TestCase):
    """End-to-end analyze_video behavior around the final-horn confirmation."""

    def _run(
        self, duration: float, observer=None, fine_refine=False, detector=_FakeDetector
    ):
        info = VideoInfo(
            path="game.mp4",
            duration=duration,
            width=1280,
            height=720,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
        )
        with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
            "hockey_trimmer.cli.probe_video", return_value=info
        ), patch(
            "hockey_trimmer.cli.extract_frame_at_timestamp", return_value=object()
        ), patch(
            "hockey_trimmer.cli.ScoreboardDetector", detector
        ), patch(
            "sys.stdout", io.StringIO()
        ):
            return analyze_video(
                "game.mp4",
                sample_interval=50.0,
                fine_refine=fine_refine,
                observer=observer,
            )

    def test_observer_records_all_phases_without_changing_result(self):
        observations = []
        expected = self._run(3400, fine_refine=True)
        actual = self._run(3400, observations.append, fine_refine=True)
        self.assertEqual(actual, expected)
        phases = [event.phase for event in observations]
        self.assertIn("scan", phases)
        self.assertIn("refine_start", phases)
        self.assertIn("refine_end", phases)
        self.assertEqual(
            phases, sorted(phases, key=("scan", "refine_start", "refine_end").index)
        )
        self.assertTrue(all(e.reading.timestamp == e.timestamp for e in observations))
        self.assertEqual(
            [e.timestamp for e in observations if e.phase == "scan"][-4:],
            [3120, 3130, 3140, 3150],
        )

    def test_observer_readings_are_isolated(self):
        expected = self._run(3100)
        observations = []

        def mutate(event):
            observations.append(event)
            event.reading.period = 4
            event.reading.clock_seconds = 0

        self.assertEqual(self._run(3100, mutate), expected)
        self.assertTrue(observations)

    def test_observer_failure_propagates(self):
        def fail(event):
            raise RuntimeError("observer failed")

        with self.assertRaisesRegex(RuntimeError, "observer failed"):
            self._run(3400, fail)

    def test_misread_zero_does_not_end_game_early(self):
        boundaries = self._run(duration=3400.0)
        self.assertTrue(boundaries.game_found)
        self.assertEqual(boundaries.final_horn_time, 3000.0)
        self.assertEqual(boundaries.cut_end_time, 3015.0)

    def test_pending_end_finalized_when_video_ends(self):
        boundaries = self._run(duration=3100.0)
        self.assertTrue(boundaries.game_found)
        self.assertEqual(boundaries.final_horn_time, 3000.0)
        self.assertEqual(boundaries.cut_end_time, 3015.0)

    def test_pending_confirmation_keeps_fine_step_on_frame_miss(self):
        info = VideoInfo(
            path="game.mp4",
            duration=130.0,
            width=1280,
            height=720,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
        )
        sampled_timestamps = []
        observations = []

        class _PendingOnFirstReadingTimeline:
            def __init__(self, *args, **kwargs):
                self.state = GameState.SEARCHING_START
                self.events = []
                self._seen = 0

            def process_reading(self, reading):
                self._seen += 1
                if self._seen == 1:
                    self.state = GameState.PENDING_GAME_END
                return False

            def finalize(self):
                return False

            def get_boundaries(self):
                return GameBoundaries(
                    puck_drop_time=0.0,
                    final_horn_time=0.0,
                    cut_start_time=0.0,
                    cut_end_time=0.0,
                    game_found=False,
                )

        class _MinimalDetector:
            def __init__(self, *args, **kwargs):
                pass

            def analyze_frame(self, frame, timestamp):
                return ScoreboardReading(present=False, timestamp=timestamp)

        def _extract(path, timestamp):
            sampled_timestamps.append(timestamp)
            return object() if timestamp == 0.0 else None

        with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
            "hockey_trimmer.cli.probe_video", return_value=info
        ), patch(
            "hockey_trimmer.cli.extract_frame_at_timestamp", side_effect=_extract
        ), patch(
            "hockey_trimmer.cli.ScoreboardDetector", _MinimalDetector
        ), patch(
            "hockey_trimmer.cli.GameTimelineTracker", _PendingOnFirstReadingTimeline
        ), patch(
            "sys.stdout", io.StringIO()
        ):
            analyze_video(
                "game.mp4",
                sample_interval=50.0,
                end_confirm_interval=10.0,
                fine_refine=False,
                observer=observations.append,
            )

        self.assertGreaterEqual(len(sampled_timestamps), 3)
        self.assertEqual(sampled_timestamps[:3], [0.0, 10.0, 20.0])
        self.assertEqual([o.timestamp for o in observations], sampled_timestamps)
        self.assertIsNotNone(observations[0].reading)
        self.assertTrue(all(o.reading is None for o in observations[1:]))

    def test_final_horn_refine_ignores_low_clock_from_non_p3_periods(self):
        info = VideoInfo(
            path="game.mp4",
            duration=4000.0,
            width=1280,
            height=720,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
        )

        class _StaticCompletedTimeline:
            def __init__(self, *args, **kwargs):
                self.state = GameState.SEARCHING_START
                self.events = []
                self.puck_drop_time = None
                self.p1_max_clock = None
                self.final_horn_time = 3000.0

            def process_reading(self, reading):
                self.state = GameState.GAME_COMPLETE
                return True

            def finalize(self):
                return True

            def get_boundaries(self):
                return GameBoundaries(
                    puck_drop_time=100.0,
                    final_horn_time=3000.0,
                    cut_start_time=85.0,
                    cut_end_time=3015.0,
                    game_found=True,
                )

        class _RefineDetector:
            def __init__(self, *args, **kwargs):
                pass

            def analyze_frame(self, frame, timestamp):
                if 2990.0 <= timestamp <= 2991.0:
                    return ScoreboardReading(
                        present=True,
                        timestamp=timestamp,
                        period=2,
                        clock_seconds=0.0,
                        score=(2, 1),
                    )
                if 3000.0 <= timestamp <= 3001.0:
                    return ScoreboardReading(
                        present=True,
                        timestamp=timestamp,
                        period=3,
                        clock_seconds=0.0,
                        score=(3, 1),
                    )
                return ScoreboardReading(
                    present=True,
                    timestamp=timestamp,
                    period=3,
                    clock_seconds=10.0,
                    score=(3, 1),
                )

        def _extract(path, timestamp):
            if timestamp == 0.0 or 2990.0 <= timestamp < 3010.0:
                return object()
            return None

        with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
            "hockey_trimmer.cli.probe_video", return_value=info
        ), patch(
            "hockey_trimmer.cli.extract_frame_at_timestamp", side_effect=_extract
        ), patch(
            "hockey_trimmer.cli.ScoreboardDetector", _RefineDetector
        ), patch(
            "hockey_trimmer.cli.GameTimelineTracker", _StaticCompletedTimeline
        ), patch(
            "sys.stdout", io.StringIO()
        ):
            boundaries = analyze_video(
                "game.mp4", sample_interval=10.0, fine_refine=True
            )

        self.assertEqual(boundaries.final_horn_time, 3000.0)
        self.assertEqual(boundaries.cut_end_time, 3015.0)

    def test_refinement_accepts_unknown_tab_only_after_confirmed_scan_horn(self):
        class UnknownRefinementDetector(_FakeDetector):
            def analyze_frame(self, frame, timestamp):
                reading = super().analyze_frame(frame, timestamp)
                if timestamp >= 2950:
                    reading.period = None
                return reading

        observations = []
        boundaries = self._run(
            3400,
            observations.append,
            fine_refine=True,
            detector=UnknownRefinementDetector,
        )
        self.assertEqual(boundaries.final_horn_time, 2999)
        self.assertEqual(boundaries.cut_end_time, 3014)
        self.assertEqual(
            [
                (e.timestamp, e.reading.period)
                for e in observations
                if e.phase == "refine_end"
            ][-2:],
            [(2999, None), (3000, None)],
        )


if __name__ == "__main__":
    unittest.main()
