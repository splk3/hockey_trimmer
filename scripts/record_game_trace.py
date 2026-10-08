#!/usr/bin/env python3
"""Capture normal scan/refinement observations as a local, unreviewed trace."""

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hockey_trimmer.cli import analyze_video  # noqa: E402
from hockey_trimmer.trimmer import probe_video  # noqa: E402
from scripts.regression_fixtures import (  # noqa: E402
    ANALYSIS_DEFAULTS,
    SCHEMA_VERSION,
    boundaries_dict,
    load_json,
    replay_scan,
    sha256,
    validate_trace,
    write_json,
)


def source_metadata(path):
    info = probe_video(str(path))
    return {
        "name": Path(path).name,
        "sha256": sha256(path),
        "duration": info.duration,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
    }


def record(path, output, settings=None, source=None, replace=False, review_from=None):
    output = Path(output)
    if output.resolve() == Path(path).resolve():
        raise ValueError("Trace output must not overwrite the source video")
    if output.exists() and not replace:
        raise FileExistsError(f"Refusing to replace {output}; use --replace")
    options = dict(ANALYSIS_DEFAULTS)
    if settings:
        options.update(settings)
    if options["custom_roi"] is not None:
        options["custom_roi"] = list(options["custom_roi"])
    metadata = source if source is not None else source_metadata(path)
    reviewed = load_json(review_from) if review_from is not None else None
    if reviewed is not None:
        # Historical observations need not replay under changed production logic.
        validate_trace(reviewed, check_replay=False)
        if (
            reviewed["review_status"] != "reviewed"
            or reviewed["source"] != metadata
            or reviewed["settings"] != options
        ):
            raise ValueError("Review source, settings, and reviewed status must match")
    observations = []
    boundaries = analyze_video(
        str(path),
        **options,
        observer=lambda event: observations.append(asdict(event)),
    )
    trace = {
        "schema_version": SCHEMA_VERSION,
        "source": metadata,
        "settings": options,
        "observations": observations,
        "review_status": "draft",
        "expected": {},
        "tolerances": {},
        "review_note": "",
        "baseline": {"refined": boundaries_dict(boundaries)},
    }
    coarse, reason = replay_scan(trace)
    trace["baseline"]["coarse"] = boundaries_dict(coarse)
    trace["stop_reason"] = reason
    if reviewed is not None:
        for key in ("review_status", "expected", "tolerances", "review_note"):
            trace[key] = reviewed[key]
        if "reviewed_intervals" in reviewed:
            trace["reviewed_intervals"] = reviewed["reviewed_intervals"]
        trace["original_baseline"] = reviewed.get(
            "original_baseline", reviewed["baseline"]
        )
    validate_trace(trace)
    write_json(output, trace)
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--review-from",
        type=Path,
        help="Preserve independent review and original baseline from a matching trace",
    )
    args = parser.parse_args()
    record(
        args.input,
        args.output,
        {"sample_interval": args.sample_interval},
        replace=args.replace,
        review_from=args.review_from,
    )


if __name__ == "__main__":
    main()
