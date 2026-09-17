# Hockey Trimmer 🏒✂️

[![CI Tests](https://github.com/your-username/hockey-trimmer/actions/workflows/test.yml/badge.svg)](https://github.com/your-username/hockey-trimmer/actions/workflows/test.yml)
[![Super-Linter](https://github.com/your-username/hockey-trimmer/actions/workflows/linter.yml/badge.svg)](https://github.com/your-username/hockey-trimmer/actions/workflows/linter.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An automated Python command-line utility for analyzing ice hockey game videos, detecting on-screen scoreboard overlays (period, clock, scores), and cleanly trimming the beginning and end of the recording to save space and watch time.

Tuned for broadcasts and arena streaming feeds (such as **Black Bear TV**, LiveBarn, Pixellot, and standard broadcast scorebugs).

---

## Key Features

- **Automated Game Lifecycle Detection**: Tracks scoreboard state transitions from Period 1 opening puck drop through Period 3 regulation finish (and Overtime if tied).
- **First Complete Game Selection**: Intelligently ignores warmups and mid-game recordings from previous matches, locking onto the *first complete match* (Period 1 -> Period 2 -> Period 3 -> End).
- **Self-Contained Digit & Period Recognition**: Includes a built-in OpenCV contour slot matcher that reads digital game clocks (`MM:SS`) and periods (`1`, `2`, `3`, `OT`) in microseconds without requiring external Tesseract binaries.
- **Fast Lossless Stream Copy**: Defaults to `ffmpeg -c copy` snapped to adjacent keyframes—cuts multi-gigabyte video files in seconds with zero CPU re-encoding overhead and zero quality loss.
- **Keyframe-Accurate or Frame-Accurate**: Snaps start cuts to preceding keyframes and end cuts to subsequent keyframes to guarantee no game action is missed. Supports frame-accurate re-encoding (`--reencode`).
- **Configurable Buffers**: Adds customizable pre-game padding (default: 15s before opening puck drop) and post-game padding (default: 15s after final horn / Period 3 0:00).
- **Dry-Run Analysis (`--check`)**: Scans video and reports timeline events, period transitions, and cut timestamps without modifying or creating files.
- **Scoreboard Presets & Custom ROI**: Pre-configured for Black Bear TV (`blackbear`), generic corner layouts (`top_left`, `top_center`), and custom bounding box overrides via `--roi`.

---

## Game Completion Logic

1. **Start of Game**:
   - The scanner searches for the appearance of the Period 1 scoreboard.
   - Opening puck drop is locked when the Period 1 clock begins counting down from starting duration (e.g. `15:00` -> `14:59`).
   - A 15-second pre-buffer is added so player introductions and faceoff lineups are preserved.
2. **Regulation & Overtime**:
   - The state machine tracks progression through Period 1, Period 2, and Period 3.
   - **Regulation Finish**: If the score is **not tied** when the Period 3 clock reaches `00:00`, the game concludes.
   - **Overtime**: If the score **is tied** at Period 3 `00:00`, the game continues into Overtime and ends when the OT period clock reaches `00:00` (regardless of score).
   - **Overlay Disappearance**: If the scoreboard overlay turns off after Period 3 has been in progress, the disappearance marks the end of the game.
3. **End of Game**:
   - A 15-second post-buffer is added after the final horn so celebrations and post-game handshakes are included.

---

## Prerequisites

- **Python**: 3.10 or higher
- **FFmpeg & FFprobe**: Required for video probing, frame extraction, and video cutting.
  - **Ubuntu / Debian**:
    ```bash
    sudo apt-get update && sudo apt-get install -y ffmpeg
    ```
  - **macOS**:
    ```bash
    brew install ffmpeg
    ```
  - **Windows**:
    ```powershell
    winget install Gyan.FFmpeg
    ```
- *(Optional)* **Tesseract OCR**: Only needed if you want external OCR fallback on non-standard custom scoreboard fonts (`sudo apt-get install tesseract-ocr`).

---

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/hockey-trimmer.git
   cd hockey-trimmer
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

   For development and running tests:
   ```bash
   pip install -r requirements-dev.txt
   ```

---

## Usage Examples

### 1. Analyze / Check Video (Dry-Run)
Scan the video and display timeline events and proposed cut timestamps without writing a file:
```bash
python hockey_trimmer.py -i game_raw.mp4 --check
```

### 2. Fast Lossless Trim (Default)
Trim the first complete game in seconds using stream copy (scans at the default 10s interval):
```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4
```

### 3. Custom Pre- and Post-Buffers
Keep 10 seconds before opening puck drop and 20 seconds after final horn:
```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4 --buffer-before 10 --buffer-after 20
```

### 4. Frame-Accurate Re-encode
Re-encode with H.264 video and AAC audio for frame-exact cuts at arbitrary timestamps:
```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4 --reencode
```

### 5. Custom Scoreboard Region of Interest
If your video uses a unique scoreboard position, specify `--roi x1,y1,x2,y2` in normalized coordinates (0.0 to 1.0):
```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4 --roi 0.05,0.02,0.25,0.14
```

---

## Real-World Benchmark

Tested on raw arena footage (`20260913-ducks12aa-vs-genesis-mcginty-raw.mp4`):
- **Raw Video**: 2 hours 7 minutes (7,661s), 3.15 GB, 1280x720 @ 30fps.
- **Detected Puck Drop**: `15:59` (clock begins countdown from 15:00).
- **Detected Final Horn**: `01:22:52` (Period 3 reaches 0:00, score 2-6).
- **Cut Window**: `15:44` -> `01:23:07` (Duration: `01:07:23`).
- **Trim Execution Time**: **5.3 seconds** via stream copy (`-c copy`).
- **Output File**: 1.58 GB (saved **1.57 GB** / **50% storage savings**).
- **Accuracy**: Within 37 seconds of manual reference edit.

---

## CLI Reference

```text
usage: hockey_trimmer [-h] -i INPUT [-o OUTPUT] [-c]
                      [--buffer-before BUFFER_BEFORE]
                      [--buffer-after BUFFER_AFTER]
                      [--sample-interval SAMPLE_INTERVAL]
                      [--preset {blackbear,top_left,top_center}] [--roi ROI]
                      [--reencode] [--no-keyframe-snap] [-v]

Automated ice hockey video analyzer and trimmer.

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Path to input video file (e.g., .mp4, .mov, .mkv).
  -o, --output OUTPUT   Path to output trimmed video file. Required unless --check is set.
  -c, --check           Analysis mode only: report detected game boundaries and events without modifying or cutting the file.
  --buffer-before SEC   Seconds of video to keep before the opening puck drop (default: 15.0).
  --buffer-after SEC    Seconds of video to keep after the final horn / period 3 0:00 (default: 15.0).
  --sample-interval SEC Coarse scan interval in seconds (default: 10.0).
  --preset PRESET       Scoreboard layout preset (default: blackbear). Choices: blackbear, top_left, top_center.
  --roi ROI             Custom scoreboard bounding box as 'x1,y1,x2,y2' normalized floats (e.g. '0.045,0.02,0.255,0.14').
  --reencode            Re-encode video instead of fast stream copy (-c copy).
  --no-keyframe-snap    Disable snapping cut points to adjacent keyframes when using stream copy.
  -v, --verbose         Enable verbose output showing per-sample detection details.
```

---

## Running Tests

Run the test suite with standard library `unittest` (zero dependencies):
```bash
python3 -m unittest discover -s tests -v
```

Or with `pytest`:
```bash
pytest -v
```

All 23 unit tests cover CLI parsing, scoreboard presence detection, digit/clock parsing, period classification, game timeline state transitions, overtime logic, and keyframe snapping.

---

## Repository Structure

```
hockey-trimmer/
├── hockey_trimmer/
│   ├── __init__.py         # Package entrypoint and exports
│   ├── cli.py              # CLI argument parser and scan orchestrator
│   ├── detector.py         # Scoreboard presence detector and ROI cropper
│   ├── ocr.py              # BuiltinDigitMatcher & OCR parsing utilities
│   ├── timeline.py         # GameTimelineTracker state machine
│   └── trimmer.py          # FFmpeg video probing, keyframe snapping, and cutter
├── tests/
│   ├── __init__.py
│   ├── test_cli.py         # Tests for argument parsing and validation
│   ├── test_detector.py    # Tests for scoreboard presence detection
│   ├── test_ocr.py         # Tests for clock, period, and score parsing
│   ├── test_timeline.py    # Tests for regulation, OT, and multi-game logic
│   ├── test_trimmer.py     # Tests for video probing and keyframe snapping
│   └── generate_fixtures.py# Synthetic test frame generator for CI
├── .github/
│   ├── dependabot.yml      # Dependabot configuration for pip & actions
│   └── workflows/
│       ├── test.yml        # CI test matrix across Python 3.10–3.13
│       └── linter.yml      # GitHub Super-Linter workflow
├── hockey_trimmer.py       # Top-level executable script
├── requirements.txt        # Production dependencies
├── requirements-dev.txt    # Development and test dependencies
├── .gitignore
├── LICENSE                 # MIT License
└── README.md
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
