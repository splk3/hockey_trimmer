# Hockey Trimmer 🏒✂️

[![CI Tests](https://github.com/your-username/hockey-trimmer/actions/workflows/test.yml/badge.svg)](https://github.com/your-username/hockey-trimmer/actions/workflows/test.yml)
[![Super-Linter](https://github.com/your-username/hockey-trimmer/actions/workflows/linter.yml/badge.svg)](https://github.com/your-username/hockey-trimmer/actions/workflows/linter.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An automated Python command-line utility for analyzing ice hockey game videos,
detecting on-screen scoreboard overlays (period, clock, scores), and cleanly
trimming the beginning and end of the recording to save space and watch time.

Tuned for broadcasts and arena streaming feeds (such as **Black Bear TV**,
LiveBarn, Pixellot, and standard broadcast scorebugs).

---

## Key Features

- **Automated Game Lifecycle Detection**: Tracks scoreboard state transitions
  from Period 1 opening puck drop through Period 3 regulation finish (and
  Overtime if tied).
- **First Complete Game Selection**: Intelligently ignores warmups and mid-game
  recordings from previous matches, locking onto the _first complete match_
  (Period 1 -> Period 2 -> Period 3 -> End).
- **Self-Contained Digit & Period Recognition**: Reads digital game clocks
  (`MM:SS`) and periods (`1`, `2`, `3`, `OT`) without requiring external
  Tesseract binaries. Black Bear TV overlays are matched against grayscale
  templates cut from real broadcast frames (`hockey_trimmer/assets/blackbear/`), which
  reliably separates the tiny period tab digits (e.g. `3` vs `1`) and clock
  digits (`3`/`8`, `4`/`0`) even after JPEG compression. Ambiguous template
  matches are not guessed; generic OCR and optional Tesseract provide fallback.
- **Fast Lossless Stream Copy**: Defaults to `ffmpeg -c copy` snapped to
  adjacent keyframes—cuts multi-gigabyte video files in seconds with zero CPU
  re-encoding overhead and zero quality loss.
- **Keyframe-Accurate or Frame-Accurate**: Snaps start cuts to preceding
  keyframes and end cuts to subsequent keyframes to guarantee no game action is
  missed. Supports frame-accurate re-encoding (`--reencode`).
- **Confirmed Final Horn**: A single 0:00 clock reading never ends the game. The
  scanner keeps sampling for a confirmation window (default 150s, every 10s) to
  make sure the clock does not go back above 0:00 after a late timeout or
  stoppage, and it rejects OCR clock values that count down faster than real
  time. Tunable via `--end-confirm-window` / `--end-confirm-interval`, or
  disabled with `--no-end-confirm`.
- **Configurable Buffers**: Adds customizable pre-game padding (default: 15s
  before opening puck drop) and post-game padding (default: 15s after final horn
  / Period 3 0:00).
- **Dry-Run Analysis (`--check`)**: Scans video and reports timeline events,
  period transitions, and cut timestamps without modifying or creating files.
- **Scoreboard Presets & Custom ROI**: Pre-configured for Black Bear TV
  (`blackbear`), generic corner layouts (`top_left`, `top_center`), and custom
  bounding box overrides via `--roi`.

---

## Game Completion Logic

1. **Start of Game**:
   - The scanner searches for the appearance of the Period 1 scoreboard.
   - A period is only trusted after two consecutive samples agree, so a single
     misread tab digit cannot advance the game or fake a new Period 1.
   - A recording that opens mid-way through a previous game is skipped: a
     confirmed Period 2/3/OT while searching discards any Period 1 candidate,
     and a Period 1 clock is only a start candidate once it has been seen at a
     full-period value (at least `8:00`, e.g. `15:00`).
   - A long scoreboard absence while searching (5+ minutes, e.g. the break
     between two games) resets any earlier start candidate.
   - Opening puck drop is locked when the Period 1 clock begins counting down
     from starting duration (e.g. `15:00` -> `14:59`). If Period 3/OT shows up
     before Period 2, that puck drop is discarded as a false start.
   - A 15-second pre-buffer is added so player introductions and faceoff lineups
     are preserved.
2. **Regulation & Overtime**:
   - The state machine tracks progression through Period 1, Period 2, and
     Period 3.
   - **Regulation Finish**: If the score is **not tied** when the Period 3 clock
     reaches `00:00`, the game concludes.
   - **Overtime**: If the score **is tied** at Period 3 `00:00`, the game
     continues into Overtime and ends when the OT period clock reaches `00:00`
     (regardless of score).
   - **Overlay Disappearance**: If the scoreboard overlay turns off after Period
     3 has been in progress, the disappearance marks a candidate end of the game.
3. **Final Horn Confirmation**:
   - A candidate end (Period 3 / OT clock at `00:00`, or the overlay turning
     off) is **not** treated as final immediately: a single OCR misread of the
     clock during a late timeout would otherwise cut the game short.
   - The scanner keeps sampling every `--end-confirm-interval` seconds
     (default 10s) for `--end-confirm-window` seconds (default 150s). If two
     consecutive readings show the clock back above `00:00`, the candidate is
     discarded as a false end and scanning continues toward the real horn.
   - Clock readings are also sanity-checked: a game clock can never count down
     faster than real elapsed time, so impossible drops (e.g. `0:51` -> `0:05`
     five seconds later) are discarded as OCR misreads.
   - If the footage ends while a candidate is still being confirmed, that
     candidate becomes the final horn.
4. **End of Game**:
   - A 15-second post-buffer is added after the final horn so celebrations and
     post-game handshakes are included.

---

## Prerequisites

- **Python**: 3.10 or higher
- **FFmpeg & FFprobe**: Required for video probing, frame extraction, and video
  cutting.
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

- _(Optional)_ **Tesseract OCR**: Only needed if you want external OCR fallback
  on non-standard custom scoreboard fonts
  (`sudo apt-get install tesseract-ocr`).

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

Scan the video and display timeline events and proposed cut timestamps without
writing a file:

```bash
python hockey_trimmer.py -i game_raw.mp4 --check
```

### 2. Fast Lossless Trim (Default)

Trim the first complete game in seconds using stream copy (scans at the default
10s interval):

```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4
```

### 3. Custom Pre- and Post-Buffers

Keep 10 seconds before opening puck drop and 20 seconds after final horn:

```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4 \
  --buffer-before 10 --buffer-after 20
```

### 4. Frame-Accurate Re-encode

Re-encode with H.264 video and AAC audio for frame-exact cuts at arbitrary
timestamps:

```bash
python hockey_trimmer.py -i game_raw.mp4 -o game_trimmed.mp4 --reencode
```

### 5. Custom Scoreboard Region of Interest

If your video uses a unique scoreboard position, specify `--roi x1,y1,x2,y2` in
normalized coordinates (0.0 to 1.0):

```bash

`--roi` changes the overlay position, not its layout or OCR strategy. Select
`--preset blackbear` for Black Bear TV layouts; `top_left` and `top_center`
use the generic digit matcher and optional Tesseract OCR. For Black Bear TV,
generic OCR is also tried when its specialized template matcher cannot read a
glyph confidently.bash
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
                      [--end-confirm-window SEC] [--end-confirm-interval SEC]
                      [--no-end-confirm]
                      [--reencode] [--no-keyframe-snap] [-v]

Automated ice hockey video analyzer and trimmer.

options:
  -h, --help            show this help message and exit
  -i, --input INPUT     Path to input video file (e.g., .mp4, .mov, .mkv).
  -o, --output OUTPUT   Path to output trimmed video file. Required unless
                        --check is set.
  -c, --check           Analysis mode only: report detected game boundaries
                        and events without modifying or cutting the file.
  --buffer-before SEC   Seconds of video to keep before the opening puck
                        drop (default: 15.0).
  --buffer-after SEC    Seconds of video to keep after the final horn /
                        period 3 0:00 (default: 15.0).
  --sample-interval SEC Coarse scan interval in seconds (default: 10.0).
  --preset PRESET       Scoreboard layout preset (default: blackbear).
                        Choices: blackbear, top_left, top_center.
  --roi ROI             Custom scoreboard bounding box as
                        'x1,y1,x2,y2' normalized floats
                        (e.g. '0.045,0.02,0.255,0.14').
  --end-confirm-window SEC
                        Seconds to keep watching after a 0:00 clock is first
                        seen in P3/OT, to make sure the clock does not go back
                        above 0:00 (default: 150.0).
  --end-confirm-interval SEC
                        Sample interval used while confirming the final horn
                        (default: 10.0).
  --no-end-confirm      Disable final-horn confirmation and end the game at
                        the first 0:00 clock reading.
  --reencode            Re-encode video instead of fast stream copy (-c copy).
  --no-keyframe-snap    Disable snapping cut points to adjacent keyframes
                        when using stream copy.
  -v, --verbose         Enable verbose output showing per-sample detection details.
```

---

## Running Tests

After installing the project and development dependencies, run the suite with
standard-library `unittest`:

```bash
python3 -m unittest discover -s tests -v
```

Or with `pytest`:

```bash
pytest -v
```

Tests cover CLI parsing, scoreboard presence, OCR, game lifecycle transitions,
overtime, observation capture/replay, and actual FFmpeg extraction and trimming.
Normal tests use committed fixtures and need no original recordings or network.
Media integration requires FFmpeg/FFprobe with libx264; missing tools skip that
class locally and fail in CI.

### Real-footage regression fixtures

**NOTE:** The fixtures information below relies on source data that is not available in this repository - it is meant as reference material for testing on an as-is basis.
Future improvements may use publicly available video files to assist in recreating and checking images.

`tests/fixtures/regression/manifest.json` records original source metadata and
SHA256, native-resolution overlay geometry, timestamps, expected labels, review
notes, and template-training overlap. The committed corpus currently contains
396 human-reviewed crops and seven reviewed complete-game traces covering
Igloo, Hawks, McGinty, both MYHA games, PBK, and Ruina at two source resolutions.
Both held-out and template-training timestamps are identified. Real-game
coarse/refined replay assertions run offline without a review-gate skip.
Scores are not labelled in this corpus. Hawks' 31 visible digit5 cases retain
that literal operator-error annotation; period OCR must abstain rather than
reinterpret it as Period3 or overtime. Established terminal-period context,
plausible clocks, and end confirmation still allow tracking the final horn.

Committed PNGs, manifests, and traces have a combined **15 MiB** limit. No source
or generated video is committed. Overlay crops reconstruct only the detector ROI
on a native-size test canvas, not the original surrounding footage. Trace replay
checks timeline/orchestration behavior, not OCR; visual tests check OCR separately.
Training samples are tagged and must not be mistaken for held-out benchmarks.

Prepare candidate crops and contact sheets from the seven `*-raw.mp4` sources in
the ignored `temp_videos/` directory:

```bash
python scripts/extract_regression_fixtures.py prepare \
  --output temp_frames/regression/candidates.json
python scripts/extract_regression_fixtures.py extract \
  --manifest temp_frames/regression/candidates.json \
  --output temp_frames/regression/draft
```

Capture a source's actual adaptive scan and one-second refinement observations:

```bash
python scripts/record_game_trace.py \
  --input temp_videos/20260906-ducks12aa-njavalanche12aa-igloo-raw.mp4 \
  --output temp_frames/regression/traces/20260906-ducks12aa-njavalanche12aa-igloo-raw.json
```

Repeat for each raw source. To add transition/refinement candidates and copy
traces into a new draft, run `prepare` and `extract` with
`--trace-dir temp_frames/regression/traces`. The trace filenames must match the
raw video stems. Keep predictions and `baseline` results separate from labels:

1. Inspect contact sheets and native crops; inspect source footage around opening
   countdown, period transitions, and final-horn confirmation.
2. Set each verified sample's `expected` fields, `review_status: "reviewed"`,
   descriptive `category`, and `review_note`. An omitted expected field means
   unlabelled; explicit `null` means the OCR must abstain.
3. For each verified trace, supply `expected.coarse` and `expected.refined` with
   `game_found`, `puck_drop_time`, `final_horn_time`, `cut_start_time`, and
   `cut_end_time`. Supply nonnegative numeric tolerances for the four time fields
   in both `tolerances.coarse` and `tolerances.refined`, then set its review status
   and note. Coarse replay excludes refinement observations; refined replay
   requires every recorded phase/timestamp exactly.
4. Compare reviewed boundaries with a normal source `--check` run. Do not copy
   current predictions into expectations without independent review.

After an OCR or sampling change, refresh observations from the original footage
before promotion. Frozen replay readings cannot reflect an OCR fix. Add
`--review-from path/to/reviewed-trace.json` when recording to preserve independent
expectations, tolerances, reviewed intervals, and the original observed baseline.
Source identity and analysis settings must match; recording never replaces
reviewed truth with current predictions. Use a separate output or explicit
`--replace`, then compare both coarse and refined results with the saved review.

Promote only reviewed assets (drafts are intentionally filtered out):

```bash
python scripts/extract_regression_fixtures.py promote \
  --manifest temp_frames/regression/draft/manifest.json \
  --output tests/fixtures/regression --replace
python -m unittest tests.test_regression tests.test_regression_tools \
  tests.test_media_integration -v
```

Commands refuse existing outputs unless `--replace` is explicit. Extraction and
promotion stage and validate outputs before replacing a corpus; invalid hashes,
geometry, labels, missing frames, or an exceeded budget leave it unchanged.
Promotion replaces the whole destination, so preserve existing reviewed entries
in the draft before promoting.

For optional **original-footage** smoke clips, edit the manifest's `clips`
recipes (`id`, `source`, `start`, `duration`, and purpose) and run:

```bash
python scripts/extract_regression_fixtures.py clips \
  --manifest temp_frames/regression/draft/manifest.json \
  --output temp_frames/regression/source_clips
```

These clips preserve source resolution and audio when present and use exact-start
re-encoding. They are local review assets, not complete-game fixtures. CI instead
generates an eight-second **crop-derived** clip in a temporary directory, asserts
real keyframes and stream-copy/no-snap/re-encode trimming with audio, and cleans
it up. It never compresses a full game's clock into a short clip or weakens
tracker defaults. Full games retain original timestamps in trace replay.

---

## Repository Structure

```text
hockey-trimmer/
├── hockey_trimmer/
│   ├── __init__.py         # Package entrypoint and exports
│   ├── cli.py              # CLI argument parser and scan orchestrator
│   ├── detector.py         # Scoreboard presence detector and ROI cropper
│   ├── ocr.py              # Overlay template & digit matchers, OCR parsing
│   ├── assets/blackbear/   # Black Bear TV period and clock digit templates
│   ├── timeline.py         # GameTimelineTracker state machine
│   └── trimmer.py          # FFmpeg video probing, keyframe snapping, and cutter
├── tests/
│   ├── __init__.py
│   ├── test_cli.py         # Tests for argument parsing and validation
│   ├── test_detector.py    # Tests for scoreboard presence detection
│   ├── test_ocr.py         # Tests for clock, period, and score parsing
│   ├── test_timeline.py    # Tests for regulation, OT, and multi-game logic
│   ├── test_trimmer.py     # Tests for video probing and keyframe snapping
│   ├── fixtures/           # Synthetic frames and real overlay crops
│   └── generate_fixtures.py
│                           # Synthetic test frame generator for CI
├── scripts/
│   └── build_ocr_templates.py
│                           # Rebuilds assets/blackbear/ from labelled footage
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

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file
for details.
