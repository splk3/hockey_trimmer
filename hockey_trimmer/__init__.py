"""
hockey_trimmer
~~~~~~~~~~~~~~
An automated tool for analyzing ice hockey game videos, detecting scoreboard
overlays, tracking game lifecycles, and cleanly trimming start/end boundaries.
"""

from .detector import ScoreboardDetector, ScoreboardReading
from .ocr import ScoreboardOCR, parse_clock_string
from .timeline import GameTimelineTracker, GameBoundaries
from .trimmer import VideoTrimmer, probe_video

__version__ = "1.0.0"
__all__ = [
    "ScoreboardDetector",
    "ScoreboardReading",
    "ScoreboardOCR",
    "parse_clock_string",
    "GameTimelineTracker",
    "GameBoundaries",
    "VideoTrimmer",
    "probe_video",
]
