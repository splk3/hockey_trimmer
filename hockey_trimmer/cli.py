"""
Command-line interface and end-to-end video analysis orchestrator.
"""

import argparse
import os
import sys
import time
from typing import Optional

from .detector import ScoreboardDetector
from .ocr import ScoreboardOCR
from .timeline import GameTimelineTracker, GameBoundaries, GameState
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
    custom_roi: Optional[tuple] = None,
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
    print(f"   Duration: {format_seconds(duration)} ({duration:.1f}s) | Resolution: {video_info.width}x{video_info.height}")
    print(f"   Preset: '{preset}' | Sample interval: {sample_interval:.1f}s")
    print(f"   Buffers: {buffer_before:.1f}s before puck drop, {buffer_after:.1f}s after final horn")

    detector = ScoreboardDetector(preset=preset, custom_roi=custom_roi)
    timeline = GameTimelineTracker(
        buffer_before=buffer_before,
        buffer_after=buffer_after,
        video_duration=duration,
    )

    t_curr = 0.0
    total_samples = int(duration / sample_interval) + 1
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
                print(f"   [{format_seconds(t_curr)}] P:{reading.period or '-'} | Clock: {clock_str} | Score: {reading.score or '-'}")

            if timeline.state != last_reported_state:
                last_reported_state = timeline.state
                if timeline.events:
                    latest = timeline.events[-1]
                    print(f"   ⚡ [{latest.timestamp_formatted}] {latest.description}")

            if is_complete:
                print(f"   🏁 First complete game detected! Stopping scan at {format_seconds(t_curr)}.")
                break

        t_curr += sample_interval
        sample_idx += 1
        if sample_idx % 20 == 0 and not verbose:
            pct = min(100.0, (t_curr / duration) * 100)
            print(f"   Progress: {pct:.1f}% ({format_seconds(t_curr)} / {format_seconds(duration)})...", end="\r", flush=True)

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
                        if timeline.p1_max_clock and r.clock_seconds < timeline.p1_max_clock:
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
                    if r.present and (r.period == 3 or r.period == 4) and r.clock_seconds is not None:
                        if r.clock_seconds <= 1.0:
                            boundaries.final_horn_time = ts
                            boundaries.cut_end_time = min(duration, ts + buffer_after)
                            break

    return boundaries


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
        "-i", "--input",
        required=True,
        help="Path to the input video file (e.g., .mp4, .mov, .mkv).",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Path to the output trimmed video file. Required unless --check is set.",
    )
    parser.add_argument(
        "-c", "--check",
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
        choices=["blackbear", "top_left", "top_center"],
        default="blackbear",
        help="Scoreboard layout preset (default: blackbear).",
    )
    parser.add_argument(
        "--roi",
        type=str,
        default=None,
        help="Custom scoreboard bounding box as 'x1,y1,x2,y2' normalized floats (e.g. '0.04,0.02,0.25,0.20').",
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
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output showing per-sample detection details.",
    )

    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)

    if not args.check and not args.output:
        print("❌ Error: -o / --output must be specified unless --check is used.", file=sys.stderr)
        return 1

    custom_roi = None
    if args.roi:
        try:
            parts = [float(p.strip()) for p in args.roi.split(",")]
            if len(parts) == 4:
                custom_roi = tuple(parts)
            else:
                raise ValueError()
        except ValueError:
            print("❌ Error: --roi must be 4 comma-separated numbers: 'x1,y1,x2,y2'", file=sys.stderr)
            return 1

    try:
        boundaries = analyze_video(
            video_path=args.input,
            buffer_before=args.buffer_before,
            buffer_after=args.buffer_after,
            sample_interval=args.sample_interval,
            preset=args.preset,
            custom_roi=custom_roi,
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
        print(f"   Proposed Cut: {format_seconds(boundaries.cut_start_time)} -> {format_seconds(boundaries.cut_end_time)}")
    else:
        print(f"✅ First Complete Game Identified:")
        print(f"   • Opening Puck Drop: {format_seconds(boundaries.puck_drop_time)} ({boundaries.puck_drop_time:.1f}s)")
        print(f"   • Final Horn (P3/OT): {format_seconds(boundaries.final_horn_time)} ({boundaries.final_horn_time:.1f}s)")
        print(f"   • Cut Window:        {format_seconds(boundaries.cut_start_time)} -> {format_seconds(boundaries.cut_end_time)}")
        print(f"   • Cut Duration:      {boundaries.duration_formatted} ({boundaries.duration_seconds:.1f}s)")

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
