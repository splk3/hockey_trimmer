"""
Command-line interface and end-to-end video analysis orchestrator.
"""

import argparse
import os
import sys
import time
from typing import Optional

from .detector import ScoreboardDetector
from .presets import (
    PresetError,
    load_preset,
    parse_roi_string,
    save_preset,
)
from .timeline import GameTimelineTracker, GameBoundaries
from .trimmer import VideoTrimmer, probe_video, extract_frame_at_timestamp


def format_seconds(seconds: float) -> str:
    """Format seconds into HH:MM:SS string."""
    tot = int(max(0.0, seconds))
    h = tot // 3600
    m = (tot % 3600) // 60
    s = tot % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def analyze_video(
    video_path: str,
    buffer_before: float = 15.0,
    buffer_after: float = 15.0,
    sample_interval: float = 10.0,
    preset: str = "blackbear",
    preset_file: Optional[str] = None,
    custom_roi: Optional[tuple] = None,
    clock_roi: Optional[tuple] = None,
    period_roi: Optional[tuple] = None,
    score_roi: Optional[tuple] = None,
    scan_scoreboard: bool = True,
    verbose: bool = False,
    fine_refine: bool = True,
) -> GameBoundaries:
    """
    Scans video to identify the first complete hockey game and its cut boundaries.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video file not found: {video_path}")

    video_info = probe_video(video_path)
    duration = video_info.duration

    print(f"🎬 Analyzing video: {os.path.basename(video_path)}")
    print(
        "   Duration: "
        f"{format_seconds(duration)} ({duration:.1f}s) | "
        f"Resolution: {video_info.width}x{video_info.height}"
    )
    preset_label = preset_file or preset
    print(f"   Preset: '{preset_label}' | Sample interval: {sample_interval:.1f}s")
    print(
        f"   Buffers: {buffer_before:.1f}s before puck drop, {buffer_after:.1f}s after final horn"
    )

    detector = ScoreboardDetector(
        preset=preset,
        preset_file=preset_file,
        custom_roi=custom_roi,
        clock_roi=clock_roi,
        period_roi=period_roi,
        score_roi=score_roi,
        scan_scoreboard=scan_scoreboard,
    )
    timeline = GameTimelineTracker(
        buffer_before=buffer_before,
        buffer_after=buffer_after,
        video_duration=duration,
    )

    t_curr = 0.0
    sample_idx = 0
    start_time_proc = time.time()

    print("\n🔍 Scanning for scoreboard and game transitions...")

    last_reported_state = None

    while t_curr < duration:
        frame = extract_frame_at_timestamp(video_path, t_curr)
        if frame is not None:
            reading = detector.analyze_frame(frame, t_curr)
            is_complete = timeline.process_reading(reading)

            if verbose and reading.present:
                clock_str = reading.clock_formatted
                print(
                    f"   [{format_seconds(t_curr)}] "
                    f"P:{reading.period or '-'} | "
                    f"Clock: {clock_str} | Score: {reading.score or '-'}"
                )

            if timeline.state != last_reported_state:
                last_reported_state = timeline.state
                if timeline.events:
                    latest = timeline.events[-1]
                    print(f"   ⚡ [{latest.timestamp_formatted}] {latest.description}")

            if is_complete:
                print(
                    f"   🏁 First complete game detected! Stopping scan at {format_seconds(t_curr)}."
                )
                break

        t_curr += sample_interval
        sample_idx += 1
        if sample_idx % 20 == 0 and not verbose:
            pct = min(100.0, (t_curr / duration) * 100)
            print(
                f"   Progress: {pct:.1f}% ({format_seconds(t_curr)} / {format_seconds(duration)})...",
                end="\r",
                flush=True,
            )

    print(f"\n   Scan completed in {time.time() - start_time_proc:.1f}s.")

    boundaries = timeline.get_boundaries()

    # Fine refinement around start and end if found
    if fine_refine and boundaries.game_found:
        # Refine puck drop time
        if timeline.puck_drop_time is not None:
            refine_start = max(0.0, timeline.puck_drop_time - sample_interval)
            refine_end = timeline.puck_drop_time + sample_interval
            for ts in [refine_start + i for i in range(int(refine_end - refine_start))]:
                f = extract_frame_at_timestamp(video_path, ts)
                if f:
                    r = detector.analyze_frame(f, ts)
                    if r.present and r.period == 1 and r.clock_seconds is not None:
                        if (
                            timeline.p1_max_clock
                            and r.clock_seconds < timeline.p1_max_clock
                        ):
                            boundaries.puck_drop_time = ts
                            boundaries.cut_start_time = max(0.0, ts - buffer_before)
                            break

        # Refine final horn time
        if timeline.final_horn_time is not None:
            refine_start = max(0.0, timeline.final_horn_time - sample_interval)
            refine_end = min(duration, timeline.final_horn_time + sample_interval)
            for ts in [refine_start + i for i in range(int(refine_end - refine_start))]:
                f = extract_frame_at_timestamp(video_path, ts)
                if f:
                    r = detector.analyze_frame(f, ts)
                    if (
                        r.present
                        and (r.period == 3 or r.period == 4)
                        and r.clock_seconds is not None
                    ):
                        if r.clock_seconds <= 1.0:
                            boundaries.final_horn_time = ts
                            boundaries.cut_end_time = min(duration, ts + buffer_after)
                            break

    return boundaries


def parse_cli_roi(value: Optional[str], option_name: str) -> Optional[tuple]:
    """Parse a CLI ROI option, returning None when the option was omitted."""
    if value is None:
        return None
    return parse_roi_string(value, option_name)


def calibrate_preset(
    video_path: str,
    output_path: str,
    timestamp: float = 0.0,
    preset: str = "blackbear",
    preset_file: Optional[str] = None,
) -> None:
    """
    Interactively collect scoreboard ROIs from a sample frame and save a preset.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video file not found: {video_path}")

    frame = extract_frame_at_timestamp(video_path, timestamp)
    if frame is None:
        raise RuntimeError(f"Could not extract calibration frame at {timestamp:.1f}s.")

    preview_path = os.path.splitext(output_path)[0] + "-calibration-frame.jpg"
    frame.save(preview_path)
    print(f"Saved calibration frame: {preview_path}")
    print(
        "Enter normalized ROIs as x1,y1,x2,y2. The full scoreboard ROI is "
        "relative to the whole frame; clock/period/score ROIs are relative to "
        "the full scoreboard box."
    )

    base = load_preset(preset, preset_file)
    full_roi = parse_roi_string(
        input(f"Full scoreboard ROI [{base.full_roi}]: ")
        or ",".join(map(str, base.full_roi)),
        "full ROI",
    )
    clock_roi = parse_roi_string(
        input(f"Clock ROI [{base.clock_roi}]: ") or ",".join(map(str, base.clock_roi)),
        "clock ROI",
    )
    period_roi = parse_roi_string(
        input(f"Period ROI [{base.period_roi}]: ")
        or ",".join(map(str, base.period_roi)),
        "period ROI",
    )
    score_roi = parse_roi_string(
        input(f"Score ROI [{base.score_roi}]: ") or ",".join(map(str, base.score_roi)),
        "score ROI",
    )

    base.name = os.path.splitext(os.path.basename(output_path))[0]
    base.full_roi = full_roi
    base.clock_roi = clock_roi
    base.period_roi = period_roi
    base.score_roi = score_roi
    base.candidate_rois = [full_roi]
    save_preset(base, output_path)
    print(f"Saved calibrated preset: {output_path}")


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hockey_trimmer",
        description="Automated ice hockey video analyzer and trimmer.",
        epilog=(
            "Examples:\n"
            "  # Check video and print game analysis without cutting:\n"
            "  hockey_trimmer -i game_raw.mp4 --check\n\n"
            "  # Trim the first complete game using fast stream copy:\n"
            "  hockey_trimmer -i game_raw.mp4 -o game_trimmed.mp4\n\n"
            "  # Trim with 10s buffers and re-encoding:\n"
            "  hockey_trimmer -i game_raw.mp4 -o game_trimmed.mp4 --buffer-before 10 --buffer-after 10 --reencode\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to the input video file (e.g., .mp4, .mov, .mkv).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Path to the output trimmed video file. Required unless --check is set.",
    )
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="Analysis mode only: report detected game boundaries and events without modifying or cutting the file.",
    )
    parser.add_argument(
        "--buffer-before",
        type=float,
        default=15.0,
        help="Seconds of video to keep before the opening puck drop (default: 15.0).",
    )
    parser.add_argument(
        "--buffer-after",
        type=float,
        default=15.0,
        help="Seconds of video to keep after the final horn / period 3 0:00 (default: 15.0).",
    )
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=10.0,
        help="Coarse scan interval in seconds (default: 10.0).",
    )
    parser.add_argument(
        "--preset",
        default="blackbear",
        help="Scoreboard layout preset (default: blackbear).",
    )
    parser.add_argument(
        "--preset-file",
        default=None,
        help="Path to a custom JSON/YAML scoreboard preset file.",
    )
    parser.add_argument(
        "--roi",
        type=str,
        default=None,
        help="Custom scoreboard bounding box as 'x1,y1,x2,y2' normalized floats (e.g. '0.04,0.02,0.25,0.20').",
    )
    parser.add_argument(
        "--clock-roi",
        type=str,
        default=None,
        help="Custom clock subregion within the scoreboard ROI as 'x1,y1,x2,y2'.",
    )
    parser.add_argument(
        "--period-roi",
        type=str,
        default=None,
        help="Custom period subregion within the scoreboard ROI as 'x1,y1,x2,y2'.",
    )
    parser.add_argument(
        "--score-roi",
        type=str,
        default=None,
        help="Custom score subregion within the scoreboard ROI as 'x1,y1,x2,y2'.",
    )
    parser.add_argument(
        "--force-preset-roi",
        action="store_true",
        help="Disable candidate scanning and use only the configured scoreboard ROI.",
    )
    parser.add_argument(
        "--calibrate-preset",
        default=None,
        help="Interactively create a JSON/YAML preset file from a sample frame.",
    )
    parser.add_argument(
        "--calibrate-timestamp",
        type=float,
        default=0.0,
        help="Timestamp in seconds for the calibration frame (default: 0.0).",
    )
    parser.add_argument(
        "--reencode",
        action="store_true",
        help="Re-encode video instead of fast stream copy (-c copy).",
    )
    parser.add_argument(
        "--no-keyframe-snap",
        action="store_true",
        help="Disable snapping cut points to adjacent keyframes when using stream copy.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output showing per-sample detection details.",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)

    if args.calibrate_preset:
        try:
            calibrate_preset(
                video_path=args.input,
                output_path=args.calibrate_preset,
                timestamp=args.calibrate_timestamp,
                preset=args.preset,
                preset_file=args.preset_file,
            )
            return 0
        except Exception as exc:
            print(f"❌ Error calibrating preset: {exc}", file=sys.stderr)
            return 1

    if not args.check and not args.output:
        print(
            "❌ Error: -o / --output must be specified unless --check is used.",
            file=sys.stderr,
        )
        return 1

    try:
        custom_roi = parse_cli_roi(args.roi, "--roi")
        clock_roi = parse_cli_roi(args.clock_roi, "--clock-roi")
        period_roi = parse_cli_roi(args.period_roi, "--period-roi")
        score_roi = parse_cli_roi(args.score_roi, "--score-roi")
    except PresetError as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1

    try:
        boundaries = analyze_video(
            video_path=args.input,
            buffer_before=args.buffer_before,
            buffer_after=args.buffer_after,
            sample_interval=args.sample_interval,
            preset=args.preset,
            preset_file=args.preset_file,
            custom_roi=custom_roi,
            clock_roi=clock_roi,
            period_roi=period_roi,
            score_roi=score_roi,
            scan_scoreboard=not args.force_preset_roi,
            verbose=args.verbose,
        )
    except Exception as exc:
        print(f"❌ Error analyzing video: {exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print("📊 GAME ANALYSIS REPORT")
    print("=" * 60)

    if not boundaries.game_found:
        print("⚠️ Warning: No complete game sequence (P1 -> P3) was identified.")
        print(f"   Puck drop candidate: {format_seconds(boundaries.puck_drop_time)}")
        print(f"   Final horn candidate: {format_seconds(boundaries.final_horn_time)}")
        print(
            f"   Proposed Cut: {format_seconds(boundaries.cut_start_time)} -> {format_seconds(boundaries.cut_end_time)}"
        )
    else:
        print("✅ First Complete Game Identified:")
        print(
            f"   • Opening Puck Drop: {format_seconds(boundaries.puck_drop_time)} ({boundaries.puck_drop_time:.1f}s)"
        )
        print(
            f"   • Final Horn (P3/OT): {format_seconds(boundaries.final_horn_time)} ({boundaries.final_horn_time:.1f}s)"
        )
        print(
            "   • Cut Window:        "
            f"{format_seconds(boundaries.cut_start_time)} -> "
            f"{format_seconds(boundaries.cut_end_time)}"
        )
        print(
            f"   • Cut Duration:      {boundaries.duration_formatted} ({boundaries.duration_seconds:.1f}s)"
        )

    if boundaries.events:
        print("\n📜 Timeline Events:")
        for ev in boundaries.events:
            print(f"   [{ev.timestamp_formatted}] {ev.description}")

    if args.check:
        print("\n🔍 Check mode active: No output file was generated.")
        return 0

    # Trimming
    print("\n✂️ Trimming video...")
    trimmer = VideoTrimmer(args.input)
    t_trim_start = time.time()
    success = trimmer.trim(
        output_path=args.output,
        cut_start=boundaries.cut_start_time,
        cut_end=boundaries.cut_end_time,
        reencode=args.reencode,
        snap_keyframes=not args.no_keyframe_snap,
    )

    if success:
        trim_duration = time.time() - t_trim_start
        out_size_mb = os.path.getsize(args.output) / (1024 * 1024)
        print(f"🎉 Successfully trimmed video in {trim_duration:.1f}s!")
        print(f"   Output saved to: {args.output} ({out_size_mb:.1f} MB)")
        return 0
    else:
        print("❌ Error: FFmpeg failed to trim the video.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
