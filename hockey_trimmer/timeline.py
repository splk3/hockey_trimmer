"""
Game timeline analyzer and state machine for detecting the first complete hockey game.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple
from .detector import ScoreboardReading


class GameState(Enum):
    SEARCHING_START = auto()  # Searching for Period 1 start
    P1_RUNNING = auto()  # Period 1 actively in progress
    INTERMISSION_1 = auto()  # Between P1 and P2
    P2_RUNNING = auto()  # Period 2 actively in progress
    INTERMISSION_2 = auto()  # Between P2 and P3
    P3_RUNNING = auto()  # Period 3 actively in progress
    OT_RUNNING = auto()  # Overtime in progress (if tied)
    GAME_COMPLETE = auto()  # First complete game fully concluded


@dataclass
class TimelineEvent:
    """An event detected along the game timeline."""

    timestamp: float
    description: str
    reading: Optional[ScoreboardReading] = None

    @property
    def timestamp_formatted(self) -> str:
        total_sec = int(self.timestamp)
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


@dataclass
class GameBoundaries:
    """Resulting cut boundaries for the detected game."""

    puck_drop_time: float
    final_horn_time: float
    cut_start_time: float
    cut_end_time: float
    game_found: bool = True
    summary: str = ""
    events: List[TimelineEvent] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.cut_end_time - self.cut_start_time)

    @property
    def duration_formatted(self) -> str:
        total_sec = int(self.duration_seconds)
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"


class GameTimelineTracker:
    """
    Tracks scoreboard readings across frames and identifies the first complete game.
    """

    def __init__(
        self,
        buffer_before: float = 15.0,
        buffer_after: float = 15.0,
        video_duration: float = float("inf"),
    ):
        self.buffer_before = buffer_before
        self.buffer_after = buffer_after
        self.video_duration = video_duration

        self.state = GameState.SEARCHING_START
        self.events: List[TimelineEvent] = []

        # State tracking variables
        self.p1_candidate_start: Optional[float] = None
        self.puck_drop_time: Optional[float] = None
        self.p1_max_clock: Optional[float] = None
        self.last_clock: Optional[float] = None
        self.last_period: Optional[int] = None
        self.last_score: Optional[Tuple[int, int]] = None
        self.last_seen_scoreboard_time: Optional[float] = None
        self.final_horn_time: Optional[float] = None

        # Flags for complete game requirements
        self.p1_observed = False
        self.p2_observed = False
        self.p3_observed = False
        self.ot_observed = False
        self.tied_at_p3_end = False

    def add_event(
        self,
        timestamp: float,
        description: str,
        reading: Optional[ScoreboardReading] = None,
    ):
        event = TimelineEvent(
            timestamp=timestamp, description=description, reading=reading
        )
        self.events.append(event)

    def process_reading(self, reading: ScoreboardReading) -> bool:
        """
        Process a single scoreboard reading.
        Returns True if the first complete game has finished (no need to scan further).
        """
        if self.state == GameState.GAME_COMPLETE:
            return True

        ts = reading.timestamp

        # Check if overlay disappeared after Period 3 (or OT)
        if not reading.present:
            if self.p3_observed and self.final_horn_time is None:
                # If scoreboard disappears after Period 3 has been running
                # and clock was low or already at/near end
                if self.last_clock is not None and self.last_clock <= 120.0:
                    self.final_horn_time = self.last_seen_scoreboard_time or ts
                    self.add_event(
                        ts,
                        f"Scoreboard disappeared after Period 3 (final clock ~{int(self.last_clock)}s). Game end assumed.",
                        reading,
                    )
                    self.state = GameState.GAME_COMPLETE
                    return True
            return False

        # Scoreboard is present
        self.last_seen_scoreboard_time = ts
        period = reading.period
        clock = reading.clock_seconds
        score = reading.score or self.last_score

        if reading.score:
            self.last_score = reading.score

        # -------------------------------------------------------------
        # STATE 1: SEARCHING_START
        # Must locate Period 1 start. If scoreboard shows Period 2/3
        # without P1, this is an incomplete previous game -> skip it!
        # -------------------------------------------------------------
        if self.state == GameState.SEARCHING_START:
            if period is not None and period > 1 and not self.p1_observed:
                # Mid-game from previous match -> ignore
                return False

            if period == 1:
                self.p1_observed = True
                if clock is not None:
                    if self.p1_max_clock is None or clock > self.p1_max_clock:
                        self.p1_max_clock = clock

                    # Check if clock is at the initial period time (e.g. 15:00 or 12:00 or 20:00)
                    if self.p1_candidate_start is None:
                        self.p1_candidate_start = ts
                        self.add_event(
                            ts,
                            f"Period 1 scoreboard detected (Clock: {reading.clock_formatted})",
                            reading,
                        )

                    # Clock begins countdown when it is lower than the initial observed period clock
                    # or drops below standard threshold (e.g., < 15:00, or < max_clock)
                    if self.p1_max_clock is not None and clock < self.p1_max_clock:
                        self.puck_drop_time = ts
                        self.state = GameState.P1_RUNNING
                        self.add_event(
                            ts,
                            f"Opening puck drop detected! Clock started counting down ({reading.clock_formatted})",
                            reading,
                        )
                else:
                    # Scoreboard detected in P1 without readable clock yet
                    if self.p1_candidate_start is None:
                        self.p1_candidate_start = ts
                        self.add_event(ts, "Period 1 scoreboard detected", reading)

        # -------------------------------------------------------------
        # STATE 2: P1_RUNNING
        # -------------------------------------------------------------
        elif self.state == GameState.P1_RUNNING:
            if period == 2:
                self.p2_observed = True
                self.state = GameState.P2_RUNNING
                self.add_event(
                    ts, f"Period 2 began (Score: {score or 'unknown'})", reading
                )
            elif period == 1 and clock is not None and clock <= 2.0:
                self.add_event(ts, "Period 1 ended (Clock reached 0:00)", reading)
                self.state = GameState.INTERMISSION_1

        # -------------------------------------------------------------
        # STATE 3: INTERMISSION_1
        # -------------------------------------------------------------
        elif self.state == GameState.INTERMISSION_1:
            if period == 2:
                self.p2_observed = True
                self.state = GameState.P2_RUNNING
                self.add_event(
                    ts, f"Period 2 began (Score: {score or 'unknown'})", reading
                )

        # -------------------------------------------------------------
        # STATE 4: P2_RUNNING
        # -------------------------------------------------------------
        elif self.state == GameState.P2_RUNNING:
            if period == 3:
                self.p3_observed = True
                self.state = GameState.P3_RUNNING
                self.add_event(
                    ts, f"Period 3 began (Score: {score or 'unknown'})", reading
                )
            elif period == 2 and clock is not None and clock <= 2.0:
                self.add_event(ts, "Period 2 ended (Clock reached 0:00)", reading)
                self.state = GameState.INTERMISSION_2

        # -------------------------------------------------------------
        # STATE 5: INTERMISSION_2
        # -------------------------------------------------------------
        elif self.state == GameState.INTERMISSION_2:
            if period == 3:
                self.p3_observed = True
                self.state = GameState.P3_RUNNING
                self.add_event(
                    ts, f"Period 3 began (Score: {score or 'unknown'})", reading
                )

        # -------------------------------------------------------------
        # STATE 6: P3_RUNNING
        # -------------------------------------------------------------
        elif self.state == GameState.P3_RUNNING:
            # Check for clock hitting 0:00 in Period 3
            if clock is not None and clock <= 2.0:
                # Check score tie condition
                is_tied = False
                if score is not None and score[0] == score[1]:
                    is_tied = True

                if is_tied:
                    self.tied_at_p3_end = True
                    self.add_event(
                        ts,
                        f"Period 3 ended tied {score[0]}-{score[1]}! Awaiting Overtime.",
                        reading,
                    )
                    self.state = GameState.OT_RUNNING
                else:
                    self.final_horn_time = ts
                    score_str = f"{score[0]}-{score[1]}" if score else "regulation"
                    self.add_event(
                        ts,
                        f"Period 3 ended (0:00 on clock, Score: {score_str}). Game complete!",
                        reading,
                    )
                    self.state = GameState.GAME_COMPLETE
                    return True

            elif period == 4:
                # Direct transition to OT observed
                self.ot_observed = True
                self.state = GameState.OT_RUNNING
                self.add_event(ts, "Overtime period began", reading)

        # -------------------------------------------------------------
        # STATE 7: OT_RUNNING
        # -------------------------------------------------------------
        elif self.state == GameState.OT_RUNNING:
            if clock is not None and clock <= 2.0:
                self.final_horn_time = ts
                self.add_event(
                    ts, "Overtime ended (0:00 on clock). Game complete!", reading
                )
                self.state = GameState.GAME_COMPLETE
                return True

        if clock is not None:
            self.last_clock = clock
        if period is not None:
            self.last_period = period

        return False

    def get_boundaries(self) -> GameBoundaries:
        """
        Compute the final cut boundaries with applied padding/buffers.
        """
        # Fallback if puck drop wasn't explicitly caught but P1 candidate was
        puck_drop = self.puck_drop_time
        if puck_drop is None:
            puck_drop = self.p1_candidate_start or 0.0

        # Fallback if final horn wasn't caught at 0:00
        final_horn = self.final_horn_time
        if final_horn is None:
            if self.last_seen_scoreboard_time and self.p3_observed:
                final_horn = self.last_seen_scoreboard_time
            else:
                final_horn = self.video_duration

        cut_start = max(0.0, puck_drop - self.buffer_before)
        cut_end = min(self.video_duration, final_horn + self.buffer_after)

        game_found = self.p1_observed and (
            self.p3_observed or self.state == GameState.GAME_COMPLETE
        )
        summary = (
            f"Puck drop: {puck_drop:.1f}s | Final horn: {final_horn:.1f}s | "
            f"Cut: {cut_start:.1f}s -> {cut_end:.1f}s (Duration: {cut_end - cut_start:.1f}s)"
        )

        return GameBoundaries(
            puck_drop_time=puck_drop,
            final_horn_time=final_horn,
            cut_start_time=cut_start,
            cut_end_time=cut_end,
            game_found=game_found,
            summary=summary,
            events=self.events,
        )
