"""
Tests for CLI argument parsing and validation.
"""

import io
import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from hockey_trimmer.cli import (
    analyze_video,
    concat_temp_path,
    format_seconds,
    main,
    parse_args,
)
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
        self.assertEqual(args.input, ["game.mp4"])
        self.assertEqual(args.output, "trimmed.mp4")
        self.assertFalse(args.check)
        self.assertEqual(args.buffer_before, 15.0)
        self.assertEqual(args.buffer_after, 15.0)
        self.assertEqual(args.sample_interval, 10.0)
        self.assertEqual(args.preset, "blackbear")
        self.assertEqual(args.end_confirm_window, 150.0)
        self.assertEqual(args.end_confirm_interval, 10.0)
        self.assertFalse(args.no_end_confirm)
        self.assertFalse(args.hevc)
        self.assertFalse(args.reencode)

    def test_hevc_arguments(self):
        args = parse_args(["-i", "game.mp4", "-o", "out.mp4", "--hevc"])
        self.assertTrue(args.hevc)
        self.assertFalse(args.reencode)
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["-i", "game.mp4", "--hevc", "--reencode"])

    @patch("hockey_trimmer.cli.os.path.getsize", return_value=1000)
    @patch("hockey_trimmer.cli.VideoTrimmer")
    @patch("hockey_trimmer.cli.analyze_video")
    def test_main_hevc(self, mock_analyze, mock_trimmer, mock_size):
        boundaries = GameBoundaries(
            game_found=True,
            puck_drop_time=10,
            final_horn_time=100,
            cut_start_time=0,
            cut_end_time=110,
        )
        mock_analyze.return_value = boundaries
        with patch("sys.stdout", io.StringIO()):
            result = main(["-i", "game.mp4", "-o", "out.mp4", "--hevc"])
        self.assertEqual(result, 0)
        mock_trimmer.return_value.trim.assert_called_once_with(
            output_path="out.mp4",
            cut_start=0,
            cut_end=110,
            reencode=False,
            snap_keyframes=True,
            hevc=True,
        )
        mock_trimmer.reset_mock()
        with patch("sys.stdout", io.StringIO()):
            self.assertEqual(main(["-i", "game.mp4", "--hevc", "--check"]), 0)
        mock_trimmer.assert_not_called()

        mock_trimmer.return_value.trim.return_value = False
        with patch("sys.stdout", io.StringIO()), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            result = main(["-i", "game.mp4", "-o", "out.mp4", "--hevc"])
        self.assertEqual(result, 1)
        self.assertIn("FFmpeg failed", stderr.getvalue())

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
        self.assertEqual(args.input, ["game.mp4"])
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

    def test_parse_args_multiple_inputs_keep_order(self):
        args = parse_args(["-i", "b.mp4", "-i", "a.mp4", "-i", "c.mp4", "--check"])
        self.assertEqual(args.input, ["b.mp4", "a.mp4", "c.mp4"])

    def test_concat_temp_path_is_random_sibling_of_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "game.mp4")
            self.assertRegex(
                concat_temp_path(output),
                rf"^{re.escape(os.path.join(tmp, 'game'))}\.concat\.[A-Za-z0-9]{{6}}\.tmp\.mkv$",
            )
            open(os.path.join(tmp, "game.concat.AAAAAA.tmp.mkv"), "w").close()
            with patch(
                "hockey_trimmer.cli.secrets.choice", side_effect=["A"] * 6 + ["Z"] * 6
            ):
                self.assertEqual(
                    concat_temp_path(output),
                    os.path.join(tmp, "game.concat.ZZZZZZ.tmp.mkv"),
                )

    def _multi_input_main(self, argv, concat_ok=True, trim_ok=True):
        """Run main() with real temp inputs and mocked ffmpeg-backed helpers."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        inputs = []
        for name in ("p1.mp4", "p2.mp4", "p3.mp4"):
            path = os.path.join(tmp.name, name)
            open(path, "w").close()
            inputs += ["-i", path]
        output = os.path.join(tmp.name, "game.mp4")
        argv = [a.replace("OUT", output).replace("P1", inputs[1]) for a in argv]
        seen = {}

        def fake_concat(paths, out):
            seen["concat"] = (list(paths), out)
            open(out, "w").close()
            return concat_ok

        def fake_analyze(video_path, **kwargs):
            seen["analyzed"] = video_path
            self.assertTrue(os.path.exists(video_path))
            return GameBoundaries(
                game_found=True,
                puck_drop_time=20,
                final_horn_time=35,
                cut_start_time=5,
                cut_end_time=50,
            )

        def fake_trimmer(video_path):
            seen["trimmed_from"] = video_path
            trimmer = MagicMock()
            trimmer.trim.side_effect = lambda **kw: (
                open(kw["output_path"], "w").close() or trim_ok
            )
            return trimmer

        with patch("hockey_trimmer.cli.concat_videos", side_effect=fake_concat), patch(
            "hockey_trimmer.cli.analyze_video", side_effect=fake_analyze
        ), patch("hockey_trimmer.cli.VideoTrimmer", side_effect=fake_trimmer), patch(
            "sys.stdout", io.StringIO()
        ), patch(
            "sys.stderr", io.StringIO()
        ):
            result = main(inputs + argv)
        return result, seen, [inputs[i] for i in range(1, 6, 2)], output

    def test_main_multiple_inputs_concat_then_trim_and_cleanup(self):
        result, seen, inputs, output = self._multi_input_main(["-o", "OUT"])
        self.assertEqual(result, 0)
        concat_inputs, concat_out = seen["concat"]
        self.assertEqual(concat_inputs, inputs)
        self.assertRegex(
            os.path.basename(concat_out), r"^game\.concat\.[A-Za-z0-9]{6}\.tmp\.mkv$"
        )
        self.assertEqual(os.path.dirname(concat_out), os.path.dirname(output))
        self.assertEqual(seen["analyzed"], concat_out)
        self.assertEqual(seen["trimmed_from"], concat_out)
        self.assertTrue(os.path.exists(output))
        self.assertFalse(os.path.exists(concat_out))

    def test_main_multiple_inputs_cleans_temp_on_failure(self):
        result, seen, _, _ = self._multi_input_main(["-o", "OUT"], trim_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(os.path.exists(seen["concat"][1]))

        result, seen, _, _ = self._multi_input_main(["-o", "OUT"], concat_ok=False)
        self.assertEqual(result, 1)
        self.assertNotIn("analyzed", seen)
        self.assertFalse(os.path.exists(seen["concat"][1]))

    def test_main_multiple_inputs_check_mode_uses_system_temp(self):
        result, seen, _, output = self._multi_input_main(["--check"])
        self.assertEqual(result, 0)
        concat_out = seen["concat"][1]
        self.assertNotEqual(os.path.dirname(concat_out), os.path.dirname(output))
        self.assertNotIn("trimmed_from", seen)
        self.assertFalse(os.path.exists(os.path.dirname(concat_out)))

    def test_main_multiple_inputs_rejects_output_matching_input(self):
        result, seen, _, _ = self._multi_input_main(["-o", "P1"])
        self.assertEqual(result, 1)
        self.assertNotIn("concat", seen)

    def test_main_multiple_inputs_rejects_output_hard_link_to_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_a = os.path.join(tmp, "part1.mp4")
            input_b = os.path.join(tmp, "part2.mp4")
            output = os.path.join(tmp, "output.mp4")
            open(input_a, "w").close()
            open(input_b, "w").close()
            os.link(input_a, output)

            with patch("hockey_trimmer.cli.concat_videos") as mock_concat, patch(
                "sys.stderr", io.StringIO()
            ):
                result = main(["-i", input_a, "-i", input_b, "-o", output])

        self.assertEqual(result, 1)
        mock_concat.assert_not_called()

    def test_main_multiple_inputs_rejects_missing_input(self):
        buf = io.StringIO()
        with patch("sys.stderr", buf), patch(
            "hockey_trimmer.cli.concat_videos"
        ) as mock_concat:
            ret = main(["-i", "missing1.mp4", "-i", "missing2.mp4", "--check"])
        self.assertEqual(ret, 1)
        self.assertIn("not found", buf.getvalue())
        mock_concat.assert_not_called()


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
        self.ocr = SimpleNamespace(tesseract_warning=None)

    def analyze_frame(self, frame, timestamp):
        return _scripted_reading(timestamp)


class TestAnalysisStatus(unittest.TestCase):
    def _run(
        self,
        read,
        duration=240.0,
        verbose=False,
        warning=None,
        sample_interval=10.0,
        fine_refine=False,
    ):
        info = VideoInfo("game.mp4", duration, 1280, 720, 30, "h264", "aac")
        observations = []

        class Detector:
            def __init__(self, **kwargs):
                self.ocr = SimpleNamespace(tesseract_warning=warning)

            def analyze_frame(self, frame, timestamp):
                return read(timestamp)

        with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
            "hockey_trimmer.cli.probe_video", return_value=info
        ), patch(
            "hockey_trimmer.cli.extract_frame_at_timestamp",
            side_effect=lambda path, ts: None if ts == 60 else object(),
        ), patch(
            "hockey_trimmer.cli.ScoreboardDetector", Detector
        ), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout, patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            boundaries = analyze_video(
                "game.mp4",
                verbose=verbose,
                fine_refine=fine_refine,
                sample_interval=sample_interval,
                observer=observations.append,
            )
        return stdout.getvalue(), stderr.getvalue(), observations, boundaries

    def test_current_and_last_seen_scores_in_verbose_and_progress_output(self):
        def read(ts):
            return ScoreboardReading(
                present=ts != 20,
                timestamp=ts,
                period=1,
                clock_seconds=900 - ts,
                score={0: (0, 0), 30: (1, 0)}.get(ts),
            )

        for verbose in (False, True):
            with self.subTest(verbose=verbose):
                output, _, observations, _ = self._run(read, verbose=verbose)
                self.assertIn("Score: 0-0\n", output)
                self.assertIn("Score: 0-0 (last seen at 00:00)", output)
                self.assertIn("Score: 1-0 (last seen at 00:30)", output)
                self.assertEqual(output.count("Opening puck drop detected"), 1)
                self.assertNotIn("Score: (", output)
                self.assertIsNone(observations[1].reading.score)
                self.assertIsNone(
                    next(o for o in observations if o.timestamp == 60).reading
                )
                if verbose:
                    self.assertIn("[00:30] P:1 | Clock: 14:30 | Score: 1-0\n", output)
                    self.assertNotIn("Progress:", output)
                else:
                    self.assertEqual(output.count("Progress:"), 1)

    def test_progress_timestamp_matches_current_score_observation(self):
        output, _, _, _ = self._run(
            lambda ts: ScoreboardReading(True, ts, 1, 900 - ts, (1, 0))
        )
        progress = next(line for line in output.splitlines() if "Progress:" in line)
        self.assertIn("(03:10 / 04:00) | Score: 1-0", progress)
        self.assertNotIn("last seen", progress)

    def test_unknown_score_is_not_inferred_at_game_start(self):
        output, _, _, _ = self._run(
            lambda ts: ScoreboardReading(
                present=True, timestamp=ts, period=1, clock_seconds=900 - ts
            ),
            duration=30,
            verbose=True,
        )
        self.assertIn("Opening puck drop detected", output)
        self.assertIn("Score: unknown", output)
        self.assertNotIn("Score: 0-0", output)

    def test_unknown_period_readings_preserve_last_seen_score(self):
        def read(ts):
            return ScoreboardReading(
                present=True,
                timestamp=ts,
                period=None,
                clock_seconds=None,
                score=(4, 1) if ts == 0 else None,
            )

        output, _, _, _ = self._run(read, duration=30, verbose=True)
        self.assertIn(
            "[00:10] P:- | Clock: --:-- | " "Score: 4-1 (last seen at 00:00)",
            output,
        )

    def test_p1_candidate_confirmation_preserves_score_from_same_run(self):
        def read(ts):
            return ScoreboardReading(
                present=True,
                timestamp=ts,
                period=1,
                clock_seconds=400 if ts == 0 else 900 - (ts - 10),
                score=(4, 1) if ts == 0 else None,
            )

        output, _, _, _ = self._run(read, duration=30, verbose=True)
        detected = next(
            line
            for line in output.splitlines()
            if "Period 1 scoreboard detected" in line
        )
        self.assertIn("Score: 4-1 (last seen at 00:00)", detected)

    def test_previous_game_score_does_not_leak_into_new_game_status(self):
        def read(ts):
            if ts < 20:
                return ScoreboardReading(
                    present=True,
                    timestamp=ts,
                    period=3,
                    clock_seconds=100,
                    score=(8, 9),
                )
            if ts < 360:
                return ScoreboardReading(present=False, timestamp=ts)
            return ScoreboardReading(
                present=True,
                timestamp=ts,
                period=1,
                clock_seconds=900 - (ts - 360),
            )

        output, _, _, _ = self._run(read, duration=400, verbose=True)
        new_game_output = output[output.index("[06:00] P:1") :]
        self.assertIn("Opening puck drop detected", new_game_output)
        self.assertIn("Score: unknown", new_game_output)
        self.assertNotIn("8-9", new_game_output)

    def test_refinement_scores_do_not_rewrite_scan_status(self):
        def read(ts):
            reading = _scripted_reading(ts)
            if ts % 10:
                reading.score = (9, 9)
            return reading

        output, _, observations, boundaries = self._run(
            read, duration=3100, sample_interval=50, fine_refine=True
        )
        self.assertTrue(
            any(o.phase != "scan" and o.reading.score == (9, 9) for o in observations)
        )
        self.assertNotIn("9-9", output)
        self.assertTrue(all("9-9" not in e.description for e in boundaries.events))
        self.assertIn("Final horn confirmed", output)
        self.assertIn("Score: 3-1", output)

    def test_backdated_period_event_identifies_score_observation_time(self):
        def read(ts):
            reading = _scripted_reading(ts)
            if ts in (1100, 1150):
                reading.score = (2 if ts == 1100 else 3, 0)
            return reading

        output, _, _, _ = self._run(read, duration=1200, sample_interval=50)
        event = next(line for line in output.splitlines() if "Period 2 began" in line)
        self.assertIn("[18:20] Period 2 began | Score: 3-0 (last seen at 19:10)", event)

    def test_new_game_score_does_not_replace_final_score(self):
        def read(ts):
            if ts == 3010:
                return ScoreboardReading(True, ts, 3, 0, (4, 1))
            if ts == 3020:
                return ScoreboardReading(True, ts, None, 900, (0, 0))
            if ts >= 3030:
                return ScoreboardReading(True, ts, 1, 900, (0, 0))
            return _scripted_reading(ts)

        output, _, observations, boundaries = self._run(
            read, duration=3100, sample_interval=50
        )
        final = next(
            line for line in output.splitlines() if "Final horn confirmed" in line
        )
        self.assertTrue(boundaries.game_found)
        self.assertIn("Score: 4-1 (last seen at 50:10)", final)
        self.assertEqual(observations[-1].reading.score, (0, 0))

    def test_resumed_game_updates_score_after_false_end(self):
        def read(ts):
            if ts == 3010:
                return ScoreboardReading(True, ts, 3, 120, (5, 1))
            if ts == 3020:
                return ScoreboardReading(True, ts, 3, 110, None)
            if ts >= 3030:
                return ScoreboardReading(True, ts, 3, 100, None)
            return _scripted_reading(ts)

        output, _, _, _ = self._run(
            read, duration=3040, sample_interval=10, verbose=True
        )
        false_end = "False game end"
        event = next(line for line in output.splitlines() if false_end in line)
        self.assertTrue(event.endswith("Score: 5-1 (last seen at 50:10)"), event)
        self.assertIn(
            "[50:30] P:3 | Clock: 01:40 | Score: 5-1 (last seen at 50:10)",
            output,
        )

    def test_final_horn_at_end_of_footage_uses_last_sample_score(self):
        def read(ts):
            reading = _scripted_reading(ts)
            if reading.period == 3:
                reading.score = (3, 1)
            return reading

        output, _, _, _ = self._run(read, duration=3050, sample_interval=50)
        final = next(
            line for line in output.splitlines() if "Final horn confirmed" in line
        )
        self.assertTrue(final.endswith("| Score: 3-1"), final)

    def test_startup_warning_is_once_on_stderr_and_analysis_continues(self):
        for verbose in (False, True):
            for warning in (None, "Warning: external OCR fallback unavailable"):
                with self.subTest(verbose=verbose, warning=warning):
                    output, stderr, _, boundaries = self._run(
                        _scripted_reading,
                        duration=3100,
                        sample_interval=50,
                        verbose=verbose,
                        warning=warning,
                    )
                    self.assertTrue(boundaries.game_found)
                    self.assertIn("Scan completed", output)
                    self.assertEqual(stderr, f"{warning}\n" if warning else "")
                    self.assertNotIn("Warning:", output)

    def test_multi_input_check_warns_once_and_never_trims(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs = [os.path.join(directory, name) for name in ("a.mp4", "b.mp4")]
            for path in inputs:
                open(path, "w").close()

            def concat(paths, output):
                open(output, "w").close()
                return True

            info = VideoInfo("concat.mkv", 3100, 1280, 720, 30, "h264", "aac")
            with patch("hockey_trimmer.cli.concat_videos", side_effect=concat), patch(
                "hockey_trimmer.cli.probe_video", return_value=info
            ), patch(
                "hockey_trimmer.cli.extract_frame_at_timestamp",
                return_value=object(),
            ), patch(
                "hockey_trimmer.cli.ScoreboardDetector.analyze_frame",
                side_effect=lambda frame, ts: _scripted_reading(ts),
            ), patch(
                "hockey_trimmer.ocr.pytesseract.get_tesseract_version",
                side_effect=FileNotFoundError("missing engine"),
            ) as probe, patch(
                "hockey_trimmer.cli.VideoTrimmer"
            ) as trimmer, patch(
                "sys.stdout", new_callable=io.StringIO
            ) as stdout, patch(
                "sys.stderr", new_callable=io.StringIO
            ) as stderr:
                result = main(
                    [
                        "-i",
                        inputs[0],
                        "-i",
                        inputs[1],
                        "--check",
                        "--sample-interval",
                        "50",
                    ]
                )
            self.assertEqual(result, 0)
            probe.assert_called_once()
            trimmer.assert_not_called()
            self.assertEqual(stderr.getvalue().count("Warning:"), 1)
            self.assertIn("apt-get install tesseract-ocr", stderr.getvalue())
            self.assertIn("No output file was generated", stdout.getvalue())


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
                self.p1_candidate_start = None
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
                self.ocr = SimpleNamespace(tesseract_warning=None)

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
                self.p1_candidate_start = None
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
                self.ocr = SimpleNamespace(tesseract_warning=None)

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
