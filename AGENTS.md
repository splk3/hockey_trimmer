# AGENTS.md

This repository keeps all agent-facing project instructions in this file.

- This file is the single source of truth for coding and collaboration guidance.
- The file at .github/copilot-instructions.md exists only as a pointer to this file.
- Do not add new project-specific agent instructions to .github/copilot-instructions.md.
- If instructions change, update AGENTS.md first and keep the Copilot instructions file minimal.

## Project overview

This repository is a Python CLI for analyzing ice hockey game footage, detecting scoreboard overlays, tracking the lifecycle of a complete game, and trimming raw recordings to the first complete game. The top-level entrypoint is `hockey_trimmer.py`; the main library code lives under `hockey_trimmer/`.

The workflow is intentionally split by responsibility:
- CLI and end-to-end orchestration live in `hockey_trimmer/cli.py`
- Scoreboard detection and ROI-based OCR live in `hockey_trimmer/detector.py`
- Game progression and completion logic live in `hockey_trimmer/timeline.py`
- FFmpeg/ffprobe integration and trimming logic live in `hockey_trimmer/trimmer.py`

## Build, test, and lint commands

Prerequisites from the project docs:
- Python 3.10+
- FFmpeg and FFprobe installed on the host
- Optional: Tesseract OCR for custom OCR fallback scenarios

Install dependencies in a repo-local virtual environment that is not tracked by git:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

- Keep the virtual environment under the repo root as `.venv`.
- Do not commit `.venv` or other local environment artifacts to git.
- If needed, ensure `.venv/` is ignored by your local git config or `.git/info/exclude`.

Run the full test suite:

```bash
python -m unittest discover -s tests -v
```

Run a single test module or class:

```bash
python -m unittest tests.test_cli -v
python -m unittest tests.test_detector -v
python -m unittest tests.test_timeline.TestGameTimelineTracker -v
```

Run a specific test method:

```bash
python -m unittest tests.test_cli.TestCLI.test_parse_args_valid -v
```

Local linting equivalents for the repo's dev dependencies:

```bash
python -m flake8 hockey_trimmer tests
python -m black --check hockey_trimmer tests
```

CI behavior in this repo:
- `.github/workflows/test.yml` runs `python -m unittest discover -s tests -v` on Python 3.10, 3.11, 3.12, and 3.13, after installing FFmpeg and Tesseract.
- `.github/workflows/linter.yml` validates the codebase with Super-Linter, including Python Black and Flake8.

## High-level architecture

The codebase is built around a video-analysis pipeline that samples frames over time, recognizes scoreboard state, and decides when the first complete game starts and ends.

1. `cli.py`
   - Parses CLI flags such as `-i/--input`, `-o/--output`, `--check`, `--preset`, `--roi`, and `--reencode`.
   - Calls `analyze_video()` to scan the file and infer cut boundaries.
   - Uses `VideoTrimmer` only after a valid game is found.

2. `detector.py`
   - Maintains preset ROI layouts for `blackbear`, `top_left`, and `top_center` scoreboard positions.
   - Crops the frame into subregions for period, clock, and score detection.
   - Uses `ScoreboardOCR` to parse game state from each sampled frame.

3. `timeline.py`
   - Implements the game lifecycle as a finite state machine: searching for start, period transitions, intermissions, overtime, completion.
   - Ignores incomplete earlier games and locks onto the first valid Period 1 -> Period 3 / OT sequence.
   - Produces `GameBoundaries` and timeline event logs used by the CLI report.

4. `trimmer.py`
   - Uses FFprobe for video metadata and FFmpeg for frame extraction.
   - Optionally snaps trims to nearby keyframes for fast stream-copy cuts.
   - Supports either lossless `-c copy` trimming or re-encoding when `--reencode` is used.

The test suite mirrors this split with `test_cli.py`, `test_detector.py`, `test_ocr.py`, `test_timeline.py`, and `test_trimmer.py`.

## Key conventions

- Keep the pipeline responsibilities separated: CLI orchestrates, detector inspects frames, timeline decides lifecycle state, trimmer handles ffmpeg operations.
- Prefer the `--check` mode when validating detection logic; it is the lowest-risk way to inspect boundaries without writing output files.
- ROI values are normalized to 0.0-1.0 coordinates when passed as `--roi`; detector presets and custom ROIs are interpreted relative to the frame size.
- The project favors lossless stream-copy trimming by default; keyframe snapping is part of the normal workflow unless `--no-keyframe-snap` or `--reencode` is chosen.
- Time values are treated as seconds throughout the codebase, and CLI output uses `HH:MM:SS` formatting only for reporting.
- Tests use standard-library `unittest` rather than a pytest-specific convention, even though pytest is available in dev dependencies.

## Repository-specific expectations

- New functionality should generally be added in the module that owns the concern rather than in a catch-all utility.
- When editing detection behavior, validate both the timeline state transitions and the CLI output path; this project is tuned around end-to-end game detection rather than isolated helper logic.
- If a change affects trimming semantics, check whether it is correct for both the default stream-copy path and the `--reencode` path.
