#!/usr/bin/env python3
"""
Build the real-overlay OCR templates shipped in hockey_trimmer/assets/blackbear/.

Frames are extracted with the same ffmpeg MJPEG path the analyzer uses, so the
templates include realistic compression artifacts. Every sample below was
labelled by hand from the source footage (period tab digit and clock value).

Usage:
    python scripts/build_ocr_templates.py --videos-dir temp_videos
"""

import argparse
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hockey_trimmer.detector import ScoreboardDetector  # noqa: E402
from hockey_trimmer.ocr import (  # noqa: E402
    BLACKBEAR_ASSETS_DIR,
    OverlayTemplateMatcher,
)
from hockey_trimmer.trimmer import extract_frame_at_timestamp  # noqa: E402

PBK = "20260927-ducks12aa-at-pbk12aa-raw.mp4"
HAWKS = "20260912-ducks12aa-vs-haverford-hawks-raw.mp4"

# (video, timestamp_seconds, period, clock "MM:SS")
SAMPLES = [
    (PBK, 0, 3, "05:48"),
    (PBK, 40, 3, "05:11"),
    (PBK, 90, 3, "04:45"),
    (PBK, 150, 3, "04:16"),
    (PBK, 190, 3, "03:57"),
    (PBK, 210, 3, "03:47"),
    (PBK, 290, 3, "02:51"),
    (PBK, 300, 3, "02:47"),
    (PBK, 410, 3, "01:44"),
    (PBK, 460, 3, "01:25"),
    (PBK, 1700, 1, "14:58"),
    (PBK, 1710, 1, "14:48"),
    (PBK, 1730, 1, "14:28"),
    (PBK, 1760, 1, "13:58"),
    (PBK, 1800, 1, "13:18"),
    (PBK, 1820, 1, "12:58"),
    (PBK, 1890, 1, "11:48"),
    (PBK, 1950, 1, "10:48"),
    (PBK, 1980, 1, "10:22"),
    (PBK, 2090, 1, "09:49"),
    (PBK, 2300, 1, "06:53"),
    (PBK, 2500, 1, "04:34"),
    (PBK, 2700, 1, "02:48"),
    (PBK, 3000, 2, "15:00"),
    (PBK, 3100, 2, "13:35"),
    (PBK, 3300, 2, "11:54"),
    (PBK, 3500, 2, "09:11"),
    (PBK, 3700, 2, "06:29"),
    (PBK, 3900, 2, "04:16"),
    (PBK, 4100, 2, "02:06"),
    (PBK, 4400, 3, "14:41"),
    (PBK, 4600, 3, "13:02"),
    (PBK, 4800, 3, "00:14"),
    (PBK, 5000, 3, "11:15"),
    (PBK, 5200, 3, "08:47"),
    (PBK, 5400, 3, "07:23"),
    (PBK, 5600, 3, "07:23"),
    (PBK, 5800, 3, "02:15"),
    (PBK, 6000, 3, "00:56"),
    (PBK, 6090, 3, "00:00"),
    (HAWKS, 2400, 4, "12:58"),
]


def build(videos_dir: str, out_dir: str) -> None:
    detector = ScoreboardDetector(preset="blackbear")
    periods = defaultdict(list)
    digits = defaultdict(list)

    for video, ts, period, clock in SAMPLES:
        path = os.path.join(videos_dir, video)
        frame = extract_frame_at_timestamp(path, ts)
        if frame is None:
            raise ValueError(f"Could not extract {video} @ {ts}s from {path}")
        w, h = frame.size
        box = detector._get_pixel_bbox(detector.config["full_roi"], w, h)
        full = frame.crop(box)
        period_crop = detector.crop_subregion(full, detector.config["period_roi"])
        clock_crop = detector.crop_subregion(full, detector.config["clock_roi"])

        periods[period].append(OverlayTemplateMatcher.period_glyph(period_crop))

        text = clock.replace(":", "")
        cells = OverlayTemplateMatcher.clock_digit_cells(clock_crop)
        if cells is None or len(cells) != len(text):
            found = None if cells is None else len(cells)
            raise ValueError(
                f"Clock segmentation mismatch {video} @ {ts}s ({clock}): {found}"
            )
        for d, cell in zip(text, cells):
            digits[d].append(cell)

    missing_periods = sorted(set(range(1, 5)) - set(periods))
    missing_digits = sorted(set("0123456789") - set(digits))
    if missing_periods or missing_digits:
        raise ValueError(
            f"Incomplete templates: missing periods {missing_periods}, "
            f"digits {missing_digits}"
        )

    os.makedirs(out_dir, exist_ok=True)
    for p, glyphs in sorted(periods.items()):
        avg = np.mean(glyphs, axis=0).clip(0, 255).astype(np.uint8)
        Image.fromarray(avg).save(os.path.join(out_dir, f"period_{p}.png"))
        print(f"period {p}: {len(glyphs)} samples")
    for d, cells in sorted(digits.items()):
        avg = np.mean(cells, axis=0).clip(0, 255).astype(np.uint8)
        Image.fromarray(avg).save(os.path.join(out_dir, f"clock_digit_{d}.png"))
        print(f"digit {d}: {len(cells)} samples")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos-dir", default="temp_videos")
    parser.add_argument("--out-dir", default=BLACKBEAR_ASSETS_DIR)
    args = parser.parse_args()
    build(args.videos_dir, args.out_dir)


if __name__ == "__main__":
    main()
