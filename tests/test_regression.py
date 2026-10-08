"""Offline visual and exact-observation replay regressions."""

import io
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from hockey_trimmer.cli import analyze_video, format_seconds, main
from hockey_trimmer.detector import ScoreboardDetector
from hockey_trimmer.ocr import ScoreboardOCR
from hockey_trimmer.trimmer import VideoInfo
from scripts.regression_fixtures import (
    BOUNDARY_FIELDS,
    MAX_CORPUS_BYTES,
    fixture_path,
    load_json,
    reading_from_dict,
    replay_scan,
    validate_manifest,
)

ROOT = Path(__file__).parent / "fixtures" / "regression"


def assert_boundaries(test, actual, expected, tolerances):
    for field in BOUNDARY_FIELDS:
        with test.subTest(boundary=field):
            if field == "game_found":
                test.assertEqual(actual.game_found, expected[field])
            else:
                test.assertLessEqual(
                    abs(getattr(actual, field) - expected[field]), tolerances[field]
                )


def replay_analyzer(trace):
    """Require every production extraction/phase to match the recorded sequence."""
    observations = iter(trace["observations"])
    current = None

    def extract(path, timestamp):
        nonlocal current
        current = next(observations, None)
        if current is None or current["timestamp"] != timestamp:
            raise AssertionError(f"Unexpected extraction timestamp: {timestamp}")
        return object() if current["reading"] is not None else None

    class Detector:
        def __init__(self, **kwargs):
            pass

        def analyze_frame(self, frame, timestamp):
            return reading_from_dict(current["reading"])

    def observe(event):
        if (event.phase, event.timestamp) != (
            current["phase"],
            current["timestamp"],
        ):
            raise AssertionError(f"Observation phase mismatch: {event}")
        expected = (
            reading_from_dict(current["reading"])
            if current["reading"] is not None
            else None
        )
        if event.reading != expected:
            raise AssertionError("Reading changed before observation")

    source = trace["source"]
    info = VideoInfo(
        source["name"],
        source["duration"],
        source["width"],
        source["height"],
        source["fps"],
        "h264",
        "aac",
    )
    with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
        "hockey_trimmer.cli.probe_video", return_value=info
    ), patch(
        "hockey_trimmer.cli.extract_frame_at_timestamp", side_effect=extract
    ), patch(
        "hockey_trimmer.cli.ScoreboardDetector", Detector
    ), patch(
        "sys.stdout", new_callable=io.StringIO
    ) as stdout:
        boundaries = analyze_video(
            source["name"], **trace["settings"], observer=observe
        )
    if next(observations, None) is not None:
        raise AssertionError("Analyzer stopped before consuming recorded observations")
    return boundaries, stdout.getvalue()


class TestRegressionCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = validate_manifest(load_json(ROOT / "manifest.json"), ROOT)

    def test_corpus_budget_and_review_status(self):
        total = sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file())
        self.assertLessEqual(total, MAX_CORPUS_BYTES)
        self.assertTrue(self.manifest["samples"])
        self.assertTrue(
            all(s["review_status"] == "reviewed" for s in self.manifest["samples"])
        )
        for path in self.manifest["traces"]:
            self.assertEqual(load_json(ROOT / path)["review_status"], "reviewed")

    def test_held_out_visual_samples_and_resolutions(self):
        self.assertTrue(
            any(not s["template_training_overlap"] for s in self.manifest["samples"])
        )
        sources = {s["name"]: s for s in self.manifest["sources"]}
        self.assertEqual(
            {sources[s["source"]]["height"] for s in self.manifest["samples"]},
            {540, 720},
        )
        self.assertEqual({s["source"] for s in self.manifest["samples"]}, set(sources))
        self.assertEqual(
            {load_json(ROOT / p)["source"]["name"] for p in self.manifest["traces"]},
            set(sources),
        )

    def test_operator_error_digits_abstain_without_generic_fallback(self):
        ocr = ScoreboardOCR()
        ocr.has_tesseract_bin = True
        detector = ScoreboardDetector()
        samples = [
            s for s in self.manifest["samples"] if s.get("reviewed_visible_period") == 5
        ]
        self.assertTrue(samples)
        with patch.object(ocr.matcher, "match_period") as generic, patch.object(
            ocr, "read_text"
        ) as tesseract:
            for sample in samples:
                with self.subTest(sample=sample["id"]), Image.open(
                    fixture_path(ROOT, sample["path"])
                ) as image:
                    self.assertIsNone(
                        ocr.read_period(
                            detector.crop_subregion(
                                image, sample["config"]["period_roi"]
                            )
                        )
                    )
            generic.assert_not_called()
            tesseract.assert_not_called()

    def test_period_and_clock_on_reviewed_crops(self):
        for sample in self.manifest["samples"]:
            with self.subTest(sample=sample["id"]):
                detector = ScoreboardDetector(preset=sample["preset"])
                detector.config = sample["config"]
                ocr = ScoreboardOCR(preset=sample["preset"])
                with Image.open(fixture_path(ROOT, sample["path"])) as image:
                    overlay = image.convert("RGB")
                for field, roi, method in (
                    ("period", "period_roi", ocr.read_period),
                    ("clock_seconds", "clock_roi", ocr.read_clock),
                ):
                    if field in sample["expected"]:
                        self.assertEqual(
                            method(
                                detector.crop_subregion(overlay, sample["config"][roi])
                            ),
                            sample["expected"][field],
                        )

    def test_detector_on_reconstructed_native_canvas(self):
        sources = {s["name"]: s for s in self.manifest["sources"]}
        for sample in self.manifest["samples"]:
            with self.subTest(sample=sample["id"]):
                source = sources[sample["source"]]
                frame = Image.new(
                    "RGB", (source["width"], source["height"]), (80, 80, 80)
                )
                with Image.open(fixture_path(ROOT, sample["path"])) as image:
                    frame.paste(image.convert("RGB"), tuple(sample["bbox"][:2]))
                detector = ScoreboardDetector(preset=sample["preset"])
                detector.config = sample["config"]
                reading = detector.analyze_frame(frame, sample["timestamp"])
                for field, expected in sample["expected"].items():
                    actual = getattr(reading, field)
                    self.assertEqual(
                        (
                            list(actual)
                            if field == "score" and actual is not None
                            else actual
                        ),
                        expected,
                        field,
                    )

    def test_reviewed_trace_replays(self):
        self.assertEqual(len(self.manifest["traces"]), 7)
        for relative in self.manifest["traces"]:
            trace = load_json(fixture_path(ROOT, relative))
            with self.subTest(trace=relative):
                coarse, _ = replay_scan(trace)
                assert_boundaries(
                    self,
                    coarse,
                    trace["expected"]["coarse"],
                    trace["tolerances"]["coarse"],
                )
                refined, output = replay_analyzer(trace)
                assert_boundaries(
                    self,
                    refined,
                    trace["expected"]["refined"],
                    trace["tolerances"]["refined"],
                )
                self.assertIn("Duration:", output)
                self.assertIn("Scan completed", output)

    def test_check_reports_reviewed_boundaries_without_trimming(self):
        for relative in self.manifest["traces"]:
            trace = load_json(fixture_path(ROOT, relative))
            with self.subTest(trace=relative):
                boundaries, _ = replay_analyzer(trace)
                with patch(
                    "hockey_trimmer.cli.analyze_video", return_value=boundaries
                ), patch("hockey_trimmer.cli.VideoTrimmer") as trimmer, patch(
                    "sys.stdout", new_callable=io.StringIO
                ) as output:
                    self.assertEqual(
                        main(["--input", trace["source"]["name"], "--check"]), 0
                    )
                trimmer.assert_not_called()
                report = output.getvalue()
                self.assertIn(
                    f"Final Horn (P3/OT): {format_seconds(boundaries.final_horn_time)} "
                    f"({boundaries.final_horn_time:.1f}s)",
                    report,
                )
                self.assertIn(
                    f"Opening Puck Drop: {format_seconds(boundaries.puck_drop_time)}",
                    report,
                )
                self.assertIn("No output file was generated", report)
