#!/usr/bin/env python3
"""Prepare, extract, review, and promote compact real-footage fixtures.

Draft predictions are never ground truth. Edit expected labels/review notes in
the extracted manifest and traces after inspecting the contact sheets/footage.
"""

import argparse
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402
from hockey_trimmer.detector import ScoreboardDetector  # noqa: E402
from hockey_trimmer.trimmer import (  # noqa: E402
    extract_frame_at_timestamp,
    probe_video,
)
from scripts.build_ocr_templates import SAMPLES  # noqa: E402
from scripts.record_game_trace import source_metadata  # noqa: E402
from scripts.regression_fixtures import (  # noqa: E402
    MAX_CORPUS_BYTES,
    SCHEMA_VERSION,
    fixture_path,
    load_json,
    sha256,
    validate_manifest,
    validate_trace,
    write_json,
)

KNOWN_OVERLAYS = [
    ("20260927-ducks12aa-at-pbk12aa-raw.mp4", 10, 3, 338),
    ("20260927-ducks12aa-at-pbk12aa-raw.mp4", 50, 3, 309),
    ("20260927-ducks12aa-at-pbk12aa-raw.mp4", 1805, 1, 793),
    ("20260927-ducks12aa-at-pbk12aa-raw.mp4", 3205, 2, 768),
    ("20260906-ducks12aa-njavalanche12aa-igloo-raw.mp4", 1960, 1, 51),
    ("20260906-ducks12aa-njavalanche12aa-igloo-raw.mp4", 4800, 3, 0),
]


@contextmanager
def staged_directory(output, replace=False):
    output = Path(output).resolve()
    if Path.cwd().resolve().is_relative_to(output):
        raise ValueError(
            "Output must not replace the working directory or its ancestors"
        )
    if output.exists() and not replace:
        raise FileExistsError(f"Refusing to replace {output}; use --replace")
    if output.exists() and not output.is_dir():
        raise ValueError("Output must be a directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".regression-", dir=output.parent) as temp:
        stage = Path(temp) / "contents"
        stage.mkdir()
        yield stage
        if output.exists() and not replace:
            raise FileExistsError(f"Output appeared while preparing fixtures: {output}")
        previous = Path(temp) / "previous"
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(stage, output)
        except OSError:
            if previous.exists():
                os.replace(previous, output)
            raise


def prepare(videos_dir, output, trace_dir=None, replace=False):
    if Path(output).exists() and not replace:
        raise FileExistsError(f"Refusing to replace {output}; use --replace")
    paths = sorted(Path(videos_dir).glob("*-raw.mp4"))
    if not paths:
        raise ValueError(f"No raw videos in {videos_dir}")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "sources": [source_metadata(path) for path in paths],
        "samples": [],
        "traces": [],
        "clips": [],
        "pending_review_categories": [
            "previous_game",
            "pregame",
            "period_transition",
            "false_horn",
            "final_horn",
            "scoreboard_absent",
            "touching_digits",
        ],
    }
    known = {(name, ts): (period, clock) for name, ts, period, clock in KNOWN_OVERLAYS}
    training = {}
    for name, ts, period, text in SAMPLES:
        minutes, seconds = text.split(":")
        training[name, ts] = (period, 60 * int(minutes) + int(seconds))
    for source in manifest["sources"]:
        name = source["name"]
        trace = None
        candidates = {0.0}
        candidates.update(float(int(source["duration"] * i / 16)) for i in range(1, 16))
        candidates.update(ts for n, ts in set(known) | set(training) if n == name)
        if trace_dir:
            trace_path = Path(trace_dir) / f"{Path(name).stem}.json"
            trace = load_json(trace_path)
            validate_trace(trace)
            if trace["source"] != source:
                raise ValueError(f"Trace source metadata mismatch: {name}")
            last_period = None
            last_present = None
            for event in trace["observations"]:
                reading = event["reading"]
                if reading is None:
                    continue
                if event["phase"] != "scan":
                    candidates.add(event["timestamp"])
                elif (
                    reading["period"] != last_period
                    or reading["present"] != last_present
                    or reading["clock_seconds"] in (0, 1, 2)
                ):
                    candidates.add(event["timestamp"])
                last_period, last_present = reading["period"], reading["present"]
            manifest["traces"].append(f"traces/{trace_path.name}")
        for ts in sorted(candidates):
            detector = ScoreboardDetector()
            bbox = detector._get_pixel_bbox(
                detector.config["full_roi"], source["width"], source["height"]
            )
            label = known.get((name, ts), training.get((name, ts)))
            identifier = f"{Path(name).stem.removesuffix('-raw')}_{ts:010.3f}"
            sample = {
                "id": identifier,
                "source": name,
                "timestamp": ts,
                "preset": "blackbear",
                "config": detector.config,
                "bbox": bbox,
                "path": f"overlays/{identifier}.png",
                "category": "review_candidate",
                "template_training_overlap": (name, ts) in training,
                "review_status": "draft",
                "review_note": "",
                "expected": {},
            }
            if label is not None:
                sample.update(
                    review_status="reviewed",
                    review_note=(
                        "Existing hand labels in scripts/build_ocr_templates.py"
                        if (name, ts) in training
                        else "Existing real-overlay labels in tests/test_ocr.py"
                    ),
                    category=f"period_{label[0]}",
                    expected={
                        "present": True,
                        "period": label[0],
                        "clock_seconds": label[1],
                    },
                )
            manifest["samples"].append(sample)
        targets = [("initial", 0.0, "initial footage smoke clip")]
        if trace is not None and trace["baseline"]["refined"]["game_found"]:
            baseline = trace["baseline"]["refined"]
            targets.extend(
                (
                    (
                        "opening",
                        baseline["puck_drop_time"],
                        "opening boundary candidate",
                    ),
                    ("ending", baseline["final_horn_time"], "final boundary candidate"),
                )
            )
        for label, timestamp, purpose in targets:
            manifest["clips"].append(
                {
                    "id": f"{Path(name).stem}.{label}",
                    "source": name,
                    "start": min(
                        max(0.0, timestamp - 3), max(0.0, source["duration"] - 10)
                    ),
                    "duration": 6.0,
                    "purpose": purpose + " (unreviewed)",
                    "encoding": "libx264 crf=18 preset=fast; audio=aac",
                }
            )
    validate_manifest(manifest)
    write_json(output, manifest)
    return manifest


def contact_sheets(samples, root):
    for offset in range(0, len(samples), 12):
        batch = samples[offset : offset + 12]
        sheet = Image.new("RGB", (800, 170 * len(batch)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, sample in enumerate(batch):
            y = i * 170
            with Image.open(fixture_path(root, sample["path"])) as image:
                sheet.paste(image, (5, y + 45))
            draw.text(
                (5, y + 5), f"{sample['source']} @ {sample['timestamp']}s", fill="black"
            )
            draw.text(
                (5, y + 22),
                f"{sample['review_status']} prediction={sample['prediction']}",
                fill="black",
            )
        sheet.save(root / f"contact_sheet_{offset // 12:03d}.png")


def extract(manifest_path, videos_dir, output, trace_dir=None, replace=False):
    manifest = validate_manifest(load_json(manifest_path))
    if Path(manifest_path).resolve().parent.is_relative_to(Path(output).resolve()):
        raise ValueError(
            "Extraction output must not replace the input manifest directory"
        )
    if Path(videos_dir).resolve().is_relative_to(Path(output).resolve()):
        raise ValueError("Output must not contain the source directory")
    for source in manifest["sources"]:
        if source_metadata(Path(videos_dir) / source["name"]) != source:
            raise ValueError(f"Source identity/metadata mismatch: {source['name']}")
    with staged_directory(output, replace) as stage:
        for sample in manifest["samples"]:
            source = Path(videos_dir) / sample["source"]
            frame = extract_frame_at_timestamp(str(source), sample["timestamp"])
            if frame is None:
                raise ValueError(f"Could not extract {source} @ {sample['timestamp']}")
            detector = ScoreboardDetector(preset=sample["preset"])
            detector.config = sample["config"]
            from dataclasses import asdict

            sample["prediction"] = asdict(
                detector.analyze_frame(frame, sample["timestamp"])
            )
            path = fixture_path(stage, sample["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.crop(tuple(sample["bbox"])).convert("RGB").save(path)
            sample["sha256"] = sha256(path)
        for relative in manifest["traces"]:
            if trace_dir is None:
                raise ValueError("--trace-dir is required to copy traces")
            destination = fixture_path(stage, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(trace_dir) / Path(relative).name, destination)
        validate_manifest(manifest, stage)
        contact_sheets(manifest["samples"], stage)
        write_json(stage / "manifest.json", manifest)


def promote(manifest_path, output, replace=False):
    root = Path(manifest_path).resolve().parent
    output = Path(output).resolve()
    if output == root or root.is_relative_to(output) or output.is_relative_to(root):
        raise ValueError("Promotion output must be separate from the draft directory")
    manifest = validate_manifest(load_json(manifest_path), root)
    manifest["samples"] = [
        sample
        for sample in manifest["samples"]
        if sample["review_status"] == "reviewed"
    ]
    if not manifest["samples"]:
        raise ValueError("No reviewed image fixtures to promote")
    reviewed_traces = []
    for relative in manifest["traces"]:
        trace = load_json(fixture_path(root, relative))
        if trace["review_status"] == "reviewed":
            reviewed_traces.append(relative)
    manifest["traces"] = reviewed_traces
    with staged_directory(output, replace) as stage:
        for relative in [s["path"] for s in manifest["samples"]] + reviewed_traces:
            destination = fixture_path(stage, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixture_path(root, relative), destination)
        write_json(stage / "manifest.json", manifest)
        validate_manifest(manifest, stage)
        size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        if size > MAX_CORPUS_BYTES:
            raise ValueError(
                f"Corpus exceeds {MAX_CORPUS_BYTES} byte budget: {size} bytes"
            )


def extract_clips(manifest_path, videos_dir, output, replace=False):
    manifest = validate_manifest(load_json(manifest_path))
    sources = {s["name"]: s for s in manifest["sources"]}
    if Path(manifest_path).resolve().parent.is_relative_to(Path(output).resolve()):
        raise ValueError("Clip output must not replace the input manifest directory")
    if Path(videos_dir).resolve().is_relative_to(Path(output).resolve()):
        raise ValueError("Output must not contain the source directory")
    for source in manifest["sources"]:
        if source_metadata(Path(videos_dir) / source["name"]) != source:
            raise ValueError(f"Source identity/metadata mismatch: {source['name']}")
    with staged_directory(output, replace) as stage:
        for clip in manifest["clips"]:
            source = sources[clip["source"]]
            start, duration = clip["start"], clip["duration"]
            from scripts.regression_fixtures import finite_number

            finite_number(start, "clip start")
            finite_number(duration, "clip duration", 1e-9)
            if start + duration > source["duration"]:
                raise ValueError("Clip range outside source")
            destination = fixture_path(stage, f"{clip['id']}.mp4")
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-ss",
                    str(start),
                    "-i",
                    str(Path(videos_dir) / source["name"]),
                    "-t",
                    str(duration),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-c:a",
                    "aac",
                    str(destination),
                ],
                check=True,
                capture_output=True,
            )
            info = probe_video(str(destination))
            if (
                (info.width, info.height) != (source["width"], source["height"])
                or abs(info.duration - duration) > max(0.1, 2 / source["fps"])
                or extract_frame_at_timestamp(str(destination), 0) is None
            ):
                raise ValueError(f"Clip validation failed: {clip['id']}")
        write_json(stage / "recipes.json", manifest["clips"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "extract", "clips", "promote"))
    parser.add_argument("--videos-dir", type=Path, default=Path("temp_videos"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--trace-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.videos_dir, args.output, args.trace_dir, args.replace)
    else:
        if args.manifest is None:
            parser.error("--manifest is required")
        if args.mode == "extract":
            extract(
                args.manifest,
                args.videos_dir,
                args.output,
                args.trace_dir,
                args.replace,
            )
        elif args.mode == "clips":
            extract_clips(args.manifest, args.videos_dir, args.output, args.replace)
        else:
            promote(args.manifest, args.output, args.replace)


if __name__ == "__main__":
    main()
