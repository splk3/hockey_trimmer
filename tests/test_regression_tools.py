"""Fixture tooling rejects invalid input without replacing existing artifacts."""

from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import extract_regression_fixtures as extractor
from scripts.record_game_trace import record
from scripts.regression_fixtures import (
    ANALYSIS_DEFAULTS,
    boundaries_dict,
    fixture_path,
    load_json,
    replay_scan,
    sha256,
    validate_manifest,
    validate_trace,
    write_json,
)
from hockey_trimmer.trimmer import VideoInfo
from tests.test_cli import _FakeDetector
from tests.test_regression import ROOT, replay_analyzer


class TestRegressionTools(unittest.TestCase):
    def setUp(self):
        self.manifest = load_json(ROOT / "manifest.json")
        self.manifest["traces"] = []

    def test_invalid_paths_and_symlinks(self):
        for path in ("../escape.png", "/absolute.png", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                fixture_path(ROOT, path)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            (root / "escape").symlink_to(Path(directory), target_is_directory=True)
            with self.assertRaises(ValueError):
                fixture_path(root, "escape/file.png")

    def test_manifest_rejects_duplicates_dimensions_and_unreviewed_expectations(self):
        for field, value in (
            ("bbox", [0, 0, 99999, 20]),
            ("timestamp", float("nan")),
            ("expected", {"period": 8}),
            ("review_note", ""),
        ):
            manifest = deepcopy(self.manifest)
            manifest["samples"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_manifest(manifest)
        manifest = deepcopy(self.manifest)
        manifest["samples"].append(manifest["samples"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_manifest(manifest)

    def test_json_write_failure_preserves_existing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text("existing")
            with self.assertRaises(ValueError):
                write_json(path, {"invalid": float("nan")})
            self.assertEqual(path.read_text(), "existing")

    def test_hash_mismatch_prevents_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft"
            draft.mkdir()
            manifest = deepcopy(self.manifest)
            manifest["samples"] = manifest["samples"][:1]
            sample = manifest["samples"][0]
            path = draft / sample["path"]
            path.parent.mkdir(parents=True)
            path.write_bytes((ROOT / sample["path"]).read_bytes())
            sample["sha256"] = "0" * 64
            write_json(draft / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                extractor.promote(draft / "manifest.json", Path(directory) / "out")
            self.assertFalse((Path(directory) / "out").exists())
            self.assertNotEqual(sha256(path), sample["sha256"])

    def test_failed_directory_build_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus"
            output.mkdir()
            (output / "sentinel").write_text("existing")
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with extractor.staged_directory(output, replace=True) as stage:
                    (stage / "partial").write_text("partial")
                    raise RuntimeError("failed")
            self.assertEqual([p.name for p in output.iterdir()], ["sentinel"])
            with self.assertRaises(FileExistsError):
                with extractor.staged_directory(output):
                    pass

    def test_extraction_failure_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "manifest.json"
            output = Path(directory) / "out"
            output.mkdir()
            (output / "sentinel").write_text("existing")
            manifest = deepcopy(self.manifest)
            manifest["sources"] = [manifest["sources"][0]]
            manifest["clips"] = []
            manifest["samples"] = [
                s
                for s in manifest["samples"]
                if s["source"] == manifest["sources"][0]["name"]
            ][:1]
            write_json(draft, manifest)
            with patch.object(
                extractor, "source_metadata", return_value=manifest["sources"][0]
            ), patch.object(
                extractor, "extract_frame_at_timestamp", return_value=None
            ), self.assertRaisesRegex(
                ValueError, "Could not extract"
            ):
                extractor.extract(draft, directory, output, replace=True)
            self.assertEqual((output / "sentinel").read_text(), "existing")

    def test_output_cannot_replace_working_directory_or_source_video(self):
        with self.assertRaisesRegex(ValueError, "working directory"):
            with extractor.staged_directory(Path.cwd(), replace=True):
                pass
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic-raw.mp4"
            source.write_bytes(b"source footage")
            with self.assertRaisesRegex(ValueError, "source video"):
                record(source, source, replace=True)
            self.assertEqual(source.read_bytes(), b"source footage")

    def test_promotion_filters_drafts_and_preserves_output_on_budget_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            draft_root = Path(directory) / "draft"
            draft_root.mkdir()
            manifest = deepcopy(self.manifest)
            for sample in manifest["samples"]:
                path = draft_root / sample["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / sample["path"]).read_bytes())
            manifest["samples"][0]["review_status"] = "draft"
            write_json(draft_root / "manifest.json", manifest)
            output = Path(directory) / "corpus"
            extractor.promote(draft_root / "manifest.json", output)
            self.assertEqual(
                len(load_json(output / "manifest.json")["samples"]),
                len(manifest["samples"]) - 1,
            )
            before = (output / "manifest.json").read_bytes()
            with patch.object(extractor, "MAX_CORPUS_BYTES", 1), self.assertRaisesRegex(
                ValueError, "budget"
            ):
                extractor.promote(draft_root / "manifest.json", output, replace=True)
            self.assertEqual((output / "manifest.json").read_bytes(), before)


class TestRecordedTrace(unittest.TestCase):
    def _record(
        self, output, duration=3100, fine_refine=True, review_from=None, custom_roi=None
    ):
        info = VideoInfo("synthetic-raw.mp4", duration, 1280, 720, 30, "h264", "aac")
        source = {
            "name": info.path,
            "duration": duration,
            "width": 1280,
            "height": 720,
            "fps": 30,
            "sha256": "0" * 64,
        }
        with patch("hockey_trimmer.cli.os.path.exists", return_value=True), patch(
            "hockey_trimmer.cli.probe_video", return_value=info
        ), patch(
            "hockey_trimmer.cli.extract_frame_at_timestamp",
            side_effect=lambda path, ts: None if ts == 50 else object(),
        ), patch(
            "hockey_trimmer.cli.ScoreboardDetector", _FakeDetector
        ), patch(
            "sys.stdout", io.StringIO()
        ):
            return record(
                info.path,
                output,
                {
                    "sample_interval": 50.0,
                    "fine_refine": fine_refine,
                    "custom_roi": custom_roi,
                },
                source=source,
                review_from=review_from,
            )

    def test_refresh_preserves_review_and_original_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roi = (0.02, 0.02, 0.3, 0.22)
            reviewed = self._record(root / "old.json", custom_roi=roi)
            reviewed["review_status"] = "reviewed"
            reviewed["review_note"] = "Independent review"
            reviewed["expected"] = deepcopy(reviewed["baseline"])
            reviewed["expected"]["refined"]["final_horn_time"] += 0.5
            reviewed["tolerances"] = {
                phase: {field: 1 for field in values if field != "game_found"}
                for phase, values in reviewed["expected"].items()
            }
            reviewed["reviewed_intervals"] = {"final_horn_time": [3000, 3001]}
            write_json(root / "old.json", reviewed)
            fresh = self._record(
                root / "new.json", review_from=root / "old.json", custom_roi=roi
            )
            for key in (
                "review_status",
                "review_note",
                "expected",
                "tolerances",
                "reviewed_intervals",
            ):
                self.assertEqual(fresh[key], reviewed[key])
            self.assertEqual(fresh["original_baseline"], reviewed["baseline"])
            second = self._record(
                root / "second.json", review_from=root / "new.json", custom_roi=roi
            )
            self.assertEqual(second["original_baseline"], reviewed["baseline"])
            with self.assertRaisesRegex(ValueError, "must match"):
                self._record(
                    root / "invalid.json", duration=3400, review_from=root / "old.json"
                )
            self.assertFalse((root / "invalid.json").exists())

    def test_historical_review_can_be_structurally_validated_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = self._record(Path(directory) / "old.json")
            trace["stop_reason"] = "game_complete"
            with self.assertRaisesRegex(ValueError, "stop reason"):
                validate_trace(trace)
            self.assertIsNone(validate_trace(trace, check_replay=False))
            trace["observations"][0]["reading"]["timestamp"] = 1
            with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
                validate_trace(trace, check_replay=False)

    def test_record_and_exact_analyzer_replay_with_missing_frame_and_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.json"
            trace = self._record(output)
            self.assertEqual(load_json(output)["review_status"], "draft")
            self.assertIsNone(trace["observations"][1]["reading"])
            self.assertEqual(trace["stop_reason"], "eof")
            self.assertEqual(
                boundaries_dict(validate_trace(trace)), trace["baseline"]["coarse"]
            )
            coarse, _ = replay_scan(trace)
            refined, _ = replay_analyzer(trace)
            self.assertEqual(boundaries_dict(coarse), trace["baseline"]["coarse"])
            self.assertEqual(boundaries_dict(refined), trace["baseline"]["refined"])
            with self.assertRaises(FileExistsError):
                self._record(output)

    def test_early_completion_and_no_refinement(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = self._record(Path(directory) / "trace.json", 3400, False)
            self.assertEqual(trace["stop_reason"], "game_complete")
            self.assertTrue(all(o["phase"] == "scan" for o in trace["observations"]))
            self.assertEqual(
                boundaries_dict(replay_analyzer(trace)[0]), trace["baseline"]["refined"]
            )

    def test_prepare_uses_raw_sources_and_trace_boundary_clip_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            videos = root / "videos"
            videos.mkdir()
            (videos / "synthetic-raw.mp4").write_bytes(b"source")
            (videos / "synthetic.mp4").write_bytes(b"ignore trimmed video")
            traces = root / "traces"
            traces.mkdir()
            trace = self._record(traces / "synthetic-raw.json")
            with patch.object(
                extractor, "source_metadata", return_value=trace["source"]
            ):
                manifest = extractor.prepare(videos, root / "candidates.json", traces)
            self.assertEqual(manifest["sources"], [trace["source"]])
            self.assertEqual(manifest["traces"], ["traces/synthetic-raw.json"])
            self.assertEqual(len(manifest["clips"]), 3)
            self.assertTrue(
                all(s["review_status"] == "draft" for s in manifest["samples"])
            )
            self.assertTrue(
                all("unreviewed" in c["purpose"] for c in manifest["clips"])
            )
            expected_start = trace["baseline"]["refined"]["puck_drop_time"] - 3
            opening = next(c for c in manifest["clips"] if c["id"].endswith(".opening"))
            self.assertEqual(opening["start"], expected_start)

    def test_invalid_traces_and_unexpected_extraction_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = self._record(Path(directory) / "trace.json")
            invalid = deepcopy(trace)
            invalid["observations"][0]["reading"]["timestamp"] = 1
            with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
                validate_trace(invalid)
            invalid = deepcopy(trace)
            invalid["settings"] = dict(ANALYSIS_DEFAULTS)
            with self.assertRaisesRegex(AssertionError, "Unexpected extraction"):
                replay_analyzer(invalid)

    def test_reviewed_trace_requires_complete_expectations_and_tolerances(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = self._record(Path(directory) / "trace.json")
            trace["review_status"] = "reviewed"
            trace["review_note"] = "Synthetic scripted game; exact known input sequence"
            trace["expected"] = deepcopy(trace["baseline"])
            trace["tolerances"] = {
                phase: {field: 0 for field in values if field != "game_found"}
                for phase, values in trace["expected"].items()
            }
            validate_trace(trace)
            invalid = deepcopy(trace)
            invalid["tolerances"]["refined"]["final_horn_time"] = float("nan")
            with self.assertRaises(ValueError):
                validate_trace(invalid)
            invalid = deepcopy(trace)
            del invalid["expected"]["coarse"]["cut_start_time"]
            with self.assertRaisesRegex(ValueError, "Incomplete expected boundaries"):
                validate_trace(invalid)
