"""Versioned regression fixture contracts shared by local tools and tests."""

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from hockey_trimmer.detector import ScoreboardDetector, ScoreboardReading
from hockey_trimmer.timeline import GameTimelineTracker

SCHEMA_VERSION = 1
MAX_CORPUS_BYTES = 15 * 1024 * 1024
PHASES = ("scan", "refine_start", "refine_end")
BOUNDARY_FIELDS = (
    "game_found",
    "puck_drop_time",
    "final_horn_time",
    "cut_start_time",
    "cut_end_time",
)
ANALYSIS_DEFAULTS = {
    "buffer_before": 15.0,
    "buffer_after": 15.0,
    "sample_interval": 10.0,
    "preset": "blackbear",
    "custom_roi": None,
    "fine_refine": True,
    "end_confirm_window": 150.0,
    "end_confirm_interval": 10.0,
    "enable_end_confirm": True,
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Invalid relative fixture path: {relative}")
    resolved = (Path(root) / path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"Fixture escapes root: {relative}")
    return resolved


def write_json(path, data):
    """Replace one JSON file only after serialization and writing succeed."""
    encoded = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(encoded)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def finite_number(value, label, minimum=0):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"Invalid {label}: {value}")


def validate_expected(expected):
    allowed = {"present", "period", "clock_seconds", "score"}
    if not isinstance(expected, dict) or not expected or set(expected) - allowed:
        raise ValueError("Expected labels must be a nonempty reading-field mapping")
    if "present" in expected and not isinstance(expected["present"], bool):
        raise ValueError("Invalid expected presence")
    if "period" in expected and expected["period"] is not None:
        if type(expected["period"]) is not int or expected["period"] not in (
            1,
            2,
            3,
            4,
        ):
            raise ValueError("Invalid expected period")
    if expected.get("clock_seconds") is not None:
        finite_number(expected["clock_seconds"], "expected clock")
    if expected.get("score") is not None:
        score = expected["score"]
        if (
            not isinstance(score, (list, tuple))
            or len(score) != 2
            or any(type(n) is not int or n < 0 for n in score)
        ):
            raise ValueError("Invalid expected score")


def validate_source(source):
    name = source["name"]
    if Path(name).name != name or not name.endswith("-raw.mp4"):
        raise ValueError(f"Source must be a raw video basename: {name}")
    for key in ("duration", "width", "height", "fps"):
        finite_number(source[key], f"source {key}", minimum=1e-9)
    if any(type(source[k]) is not int for k in ("width", "height")):
        raise ValueError("Source dimensions must be integers")
    digest = source["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid source SHA256")


def validate_manifest(manifest, root=None):
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported manifest schema")
    sources = {s["name"]: s for s in manifest["sources"]}
    if len(sources) != len(manifest["sources"]):
        raise ValueError("Duplicate sources")
    for source in sources.values():
        validate_source(source)
    ids = set()
    paths = set()
    for sample in manifest["samples"]:
        if sample["id"] in ids or sample["path"] in paths:
            raise ValueError("Duplicate sample ID or path")
        ids.add(sample["id"])
        paths.add(sample["path"])
        source = sources[sample["source"]]
        finite_number(sample["timestamp"], "sample timestamp")
        if sample["timestamp"] >= source["duration"]:
            raise ValueError("Sample timestamp outside source")
        bbox = sample["bbox"]
        if (
            len(bbox) != 4
            or any(type(n) is not int for n in bbox)
            or not 0 <= bbox[0] < bbox[2] <= source["width"]
            or not 0 <= bbox[1] < bbox[3] <= source["height"]
        ):
            raise ValueError("Invalid sample bbox")
        for roi in sample["config"].values():
            if (
                len(roi) != 4
                or any(
                    isinstance(n, bool)
                    or not isinstance(n, (int, float))
                    or not math.isfinite(n)
                    for n in roi
                )
                or not 0 <= roi[0] < roi[2] <= 1
                or not 0 <= roi[1] < roi[3] <= 1
            ):
                raise ValueError("Invalid normalized ROI")
        if set(sample["config"]) != set(ScoreboardDetector.PRESETS["blackbear"]):
            raise ValueError("Incomplete ROI configuration")
        if sample["preset"] not in ScoreboardDetector.PRESETS:
            raise ValueError("Unknown scoreboard preset")
        detector = ScoreboardDetector(preset=sample["preset"])
        actual_bbox = detector._get_pixel_bbox(
            sample["config"]["full_roi"], source["width"], source["height"]
        )
        if tuple(bbox) != actual_bbox:
            raise ValueError("Sample bbox does not match source ROI geometry")
        if sample["review_status"] not in ("draft", "reviewed"):
            raise ValueError("Invalid review status")
        if sample["review_status"] == "reviewed":
            validate_expected(sample["expected"])
            if not sample.get("review_note"):
                raise ValueError("Reviewed labels require a review note")
        path = fixture_path(root or ".", sample["path"])
        if root is not None:
            from PIL import Image

            if sha256(path) != sample["sha256"]:
                raise ValueError(f"Fixture hash mismatch: {path}")
            with Image.open(path) as image:
                if image.size != (bbox[2] - bbox[0], bbox[3] - bbox[1]):
                    raise ValueError(f"Fixture dimensions mismatch: {path}")
    traces = manifest.get("traces", [])
    if len(traces) != len(set(traces)):
        raise ValueError("Duplicate trace paths")
    for relative in traces:
        path = fixture_path(root or ".", relative)
        if root is not None:
            trace = load_json(path)
            validate_trace(trace)
            if trace["source"] != sources[trace["source"]["name"]]:
                raise ValueError("Trace source metadata mismatch")
    clip_ids = set()
    for clip in manifest.get("clips", []):
        if clip["id"] in clip_ids:
            raise ValueError("Duplicate clip IDs")
        clip_ids.add(clip["id"])
        fixture_path(root or ".", f"{clip['id']}.mp4")
        source = sources[clip["source"]]
        finite_number(clip["start"], "clip start")
        finite_number(clip["duration"], "clip duration", 1e-9)
        if clip["start"] + clip["duration"] > source["duration"]:
            raise ValueError("Clip range outside source")
    return manifest


def reading_from_dict(data):
    values = dict(data)
    if values.get("score") is not None:
        values["score"] = tuple(values["score"])
    return ScoreboardReading(**values)


def boundaries_dict(boundaries):
    return {key: getattr(boundaries, key) for key in BOUNDARY_FIELDS}


def replay_scan(trace):
    settings = trace["settings"]
    tracker = GameTimelineTracker(
        video_duration=trace["source"]["duration"],
        **{
            key: settings[key]
            for key in (
                "buffer_before",
                "buffer_after",
                "end_confirm_window",
                "end_confirm_interval",
                "enable_end_confirm",
            )
        },
    )
    completed = False
    for observation in trace["observations"]:
        if observation["phase"] != "scan":
            continue
        if completed:
            raise ValueError("Scan observations continue after game completion")
        if observation["reading"] is not None:
            completed = tracker.process_reading(
                reading_from_dict(observation["reading"])
            )
    tracker.finalize()
    return tracker.get_boundaries(), "game_complete" if completed else "eof"


def validate_trace(trace, check_replay=True):
    """Validate structure; optionally check observations against today's tracker."""
    if trace["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported trace schema")
    validate_source(trace["source"])
    if set(trace["settings"]) != set(ANALYSIS_DEFAULTS):
        raise ValueError("Incomplete analysis settings")
    if trace["settings"]["preset"] not in ScoreboardDetector.PRESETS:
        raise ValueError("Unknown analysis preset")
    for key in ("fine_refine", "enable_end_confirm"):
        if not isinstance(trace["settings"][key], bool):
            raise ValueError(f"Invalid boolean setting: {key}")
    custom_roi = trace["settings"]["custom_roi"]
    if custom_roi is not None:
        if (
            len(custom_roi) != 4
            or any(
                isinstance(n, bool)
                or not isinstance(n, (int, float))
                or not math.isfinite(n)
                for n in custom_roi
            )
            or not 0 <= custom_roi[0] < custom_roi[2] <= 1
            or not 0 <= custom_roi[1] < custom_roi[3] <= 1
        ):
            raise ValueError("Invalid custom ROI")
    for key in (
        "buffer_before",
        "buffer_after",
        "sample_interval",
        "end_confirm_window",
        "end_confirm_interval",
    ):
        finite_number(trace["settings"][key], key, 1e-9 if "interval" in key else 0)
    previous_phase = -1
    previous_ts = -1.0
    if not trace["observations"]:
        raise ValueError("Empty trace")
    for observation in trace["observations"]:
        phase = PHASES.index(observation["phase"])
        ts = observation["timestamp"]
        finite_number(ts, "observation timestamp")
        if phase < previous_phase or (phase == previous_phase and ts <= previous_ts):
            raise ValueError("Observations must be ordered within each phase")
        if phase == 0 and ts >= trace["source"]["duration"]:
            raise ValueError("Scan timestamp outside source")
        previous_phase, previous_ts = phase, ts
        if observation["reading"] is not None:
            reading = reading_from_dict(observation["reading"])
            if reading.timestamp != ts:
                raise ValueError("Reading timestamp mismatch")
            validate_expected(
                {
                    k: v
                    for k, v in asdict(reading).items()
                    if k in ("present", "period", "clock_seconds", "score")
                }
            )
            finite_number(reading.confidence, "reading confidence")
            if reading.confidence > 1:
                raise ValueError("Reading confidence outside 0-1")
    if trace["review_status"] not in ("draft", "reviewed"):
        raise ValueError("Invalid trace review status")
    if trace["review_status"] == "reviewed":
        if not trace.get("review_note"):
            raise ValueError("Reviewed trace requires a review note")
        for phase in ("coarse", "refined"):
            expected = trace["expected"][phase]
            if set(expected) != set(BOUNDARY_FIELDS):
                raise ValueError("Incomplete expected boundaries")
            if not isinstance(expected["game_found"], bool):
                raise ValueError("Invalid game_found expectation")
            for key in BOUNDARY_FIELDS[1:]:
                finite_number(expected[key], key)
                finite_number(trace["tolerances"][phase][key], f"{key} tolerance")
            if not (
                expected["cut_start_time"]
                <= expected["cut_end_time"]
                <= trace["source"]["duration"]
            ):
                raise ValueError("Expected cut range outside source")
    if trace["stop_reason"] not in ("game_complete", "eof"):
        raise ValueError("Invalid trace stop reason")
    if not check_replay:
        return None
    boundaries, stop_reason = replay_scan(trace)
    if stop_reason != trace["stop_reason"]:
        raise ValueError("Trace stop reason does not match observations")
    return boundaries
