"""
FFmpeg video probing, frame extraction, keyframe snapping, and trimming utilities.
"""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence
import io
from PIL import Image


@dataclass
class VideoInfo:
    """Metadata for a video file."""

    path: str
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str


def probe_video(video_path: str) -> VideoInfo:
    """
    Extract video metadata using ffprobe.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate:format=duration",
        "-of",
        "json",
        video_path,
    ]
    res = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
    )
    data = json.loads(res.stdout)

    format_data = data.get("format", {})
    duration = float(format_data.get("duration", 0.0))

    width = 1280
    height = 720
    fps = 30.0
    v_codec = "h264"
    a_codec = "aac"

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 1280))
            height = int(stream.get("height", 720))
            v_codec = stream.get("codec_name", "h264")
            rate_str = stream.get("r_frame_rate", "30/1")
            if "/" in rate_str:
                num, den = rate_str.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            else:
                fps = float(rate_str)
        elif stream.get("codec_type") == "audio":
            a_codec = stream.get("codec_name", "aac")

    return VideoInfo(
        path=video_path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        video_codec=v_codec,
        audio_codec=a_codec,
    )


def extract_frame_at_timestamp(
    video_path: str, timestamp_sec: float
) -> Optional[Image.Image]:
    """
    Extract a single frame from video as a PIL Image using ffmpeg pipe.
    """
    cmd = [
        "ffmpeg",
        "-ss",
        f"{timestamp_sec:.3f}",
        "-i",
        video_path,
        "-vframes",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "2",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True
        )
        if proc.stdout:
            return Image.open(io.BytesIO(proc.stdout)).convert("RGB")
    except Exception:
        pass
    return None


def get_keyframes_near(
    video_path: str, center_time: float, window_sec: float = 15.0
) -> List[float]:
    """
    Find keyframe (I-frame) timestamps around a center time.
    """
    start_search = max(0.0, center_time - window_sec)
    duration_search = window_sec * 2

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-read_intervals",
        f"{start_search:.2f}%+{duration_search:.2f}",
        "-select_streams",
        "v",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "csv=p=0",
        video_path,
    ]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
        keyframes = []
        for line in res.stdout.strip().splitlines():
            parts = line.split(",")
            if parts and parts[0]:
                try:
                    keyframes.append(float(parts[0]))
                except ValueError:
                    pass
        return sorted(keyframes)
    except Exception:
        return []


def snap_to_keyframes(
    video_path: str,
    cut_start: float,
    cut_end: float,
    window_sec: float = 15.0,
) -> tuple[float, float]:
    """
    Snap cut_start to the nearest prior keyframe, and cut_end to the nearest subsequent keyframe.
    Ensures complete content is included and cuts land cleanly on I-frames.
    """
    # Start keyframe: find keyframe <= cut_start
    start_kfs = get_keyframes_near(video_path, cut_start, window_sec=window_sec)
    prior_kfs = [kf for kf in start_kfs if kf <= cut_start]
    snapped_start = prior_kfs[-1] if prior_kfs else cut_start

    # End keyframe: find keyframe >= cut_end
    end_kfs = get_keyframes_near(video_path, cut_end, window_sec=window_sec)
    after_kfs = [kf for kf in end_kfs if kf >= cut_end]
    snapped_end = after_kfs[0] if after_kfs else cut_end

    return snapped_start, snapped_end


def _ffconcat_quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def concat_videos(input_paths: Sequence[str], output_path: str) -> bool:
    """
    Losslessly concatenate videos, in the given order, into output_path.

    Uses the FFmpeg concat demuxer with stream copy, so inputs must share
    codec parameters (as sequential LiveBarn downloads do). Only the first
    video stream and any audio streams are kept; LiveBarn files also carry
    an AMF0 data track that the MP4 muxer cannot stream-copy.
    """
    if not input_paths:
        raise ValueError("At least one input is required for concatenation.")

    fd, list_path = tempfile.mkstemp(prefix="hockey_trimmer_concat_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("ffconcat version 1.0\n")
            for path in input_paths:
                handle.write(f"file {_ffconcat_quote(os.path.abspath(path))}\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            output_path,
        ]
        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        except OSError as exc:
            print(f"Video concatenation failed:\n{exc}", file=sys.stderr)
            return False
    finally:
        os.remove(list_path)

    if res.returncode != 0:
        print(f"Video concatenation failed:\n{res.stderr}", file=sys.stderr)
    return res.returncode == 0


class VideoTrimmer:
    """
    Executes the video trimming via FFmpeg.
    """

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.info = probe_video(video_path)

    def trim(
        self,
        output_path: str,
        cut_start: float,
        cut_end: float,
        reencode: bool = False,
        snap_keyframes: bool = True,
        hevc: bool = False,
    ) -> bool:
        """
        Cut video from cut_start to cut_end.
        """
        if reencode and hevc:
            raise ValueError("H.264 and HEVC modes are mutually exclusive.")
        actual_start = cut_start
        actual_end = cut_end

        if snap_keyframes and not (reencode or hevc):
            actual_start, actual_end = snap_to_keyframes(
                self.video_path, cut_start, cut_end, window_sec=15.0
            )

        if hevc:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{actual_start:.3f}",
                "-to",
                f"{actual_end:.3f}",
                "-i",
                self.video_path,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map_metadata",
                "0",
                "-map_chapters",
                "0",
                "-c:v",
                "libx265",
                "-preset",
                "fast",
                "-crf",
                "21",
                "-x265-params",
                "log-level=error",
                "-tag:v",
                "hvc1",
                "-c:a",
                "copy",
                "-fps_mode",
                "passthrough",
                output_path,
            ]
        elif reencode:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{actual_start:.3f}",
                "-to",
                f"{actual_end:.3f}",
                "-i",
                self.video_path,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                output_path,
            ]
        else:
            # Fast lossless stream copy
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{actual_start:.3f}",
                "-to",
                f"{actual_end:.3f}",
                "-i",
                self.video_path,
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                output_path,
            ]

        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if hevc and res.returncode != 0:
            print(f"HEVC encoding failed:\n{res.stderr}", file=sys.stderr)
        return res.returncode == 0
