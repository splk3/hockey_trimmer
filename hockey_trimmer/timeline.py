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
    PENDING_GAME_END = auto()  # Candidate final horn seen, awaiting confirmation
    GAME_COMPLETE = auto()  # First complete game fully concluded


def format_clock(seconds: Optional[float]) -> str:
    """Format a clock value in seconds as M:SS ('--:--' when unknown)."""
    if seconds is None:
        return "--:--"
    total_sec = int(seconds)
    return f"{total_sec // 60}:{total_sec % 60:02d}"


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

    # A clock at or below this value is considered "expired" (0:00).
    CLOCK_END_THRESHOLD = 2.0
    # Slack allowed when validating how fast the clock may drop between samples.
    CLOCK_DELTA_TOLERANCE = 3.0
    # Extra proportional slack for sparse samples (timestamp/rounding jitter).
    CLOCK_DELTA_TOLERANCE_RATIO = 0.05
    # After this many consecutive implausible readings, re-sync on the newest value.
    MAX_IMPLAUSIBLE_STREAK = 3
    # Consecutive readings required to treat a pending end as false / as a new game.
    RESUME_CONFIRM_COUNT = 2

    def __init__(
        self,
        buffer_before: float = 15.0,
        buffer_after: float = 15.0,
        video_duration: float = float("inf"),
        end_confirm_window: float = 150.0,
        end_confirm_interval: float = 10.0,
        enable_end_confirm: bool = True,
        max_resume_clock: float = 300.0,
    ):
        self.buffer_before = buffer_before
        self.buffer_after = buffer_after
        self.video_duration = video_duration
        self.end_confirm_window = end_confirm_window
        self.end_confirm_interval = end_confirm_interval
        self.enable_end_confirm = enable_end_confirm
        self.max_resume_clock = max_resume_clock

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

        # Clock plausibility tracking
        self.last_accepted_clock: Optional[float] = None
        self.last_accepted_clock_time: Optional[float] = None
        self.last_accepted_period: Optional[int] = None
        self.implausible_streak = 0
        self.rejected_clock_count = 0

        # Pending (unconfirmed) end-of-game tracking
        self.pending_end_time: Optional[float] = None
        self.pending_end_started_at: Optional[float] = None
        self.pending_end_prev_state: Optional[GameState] = None
        self.pending_resume_streak = 0
        self.pending_newgame_streak = 0
        self.false_end_count = 0

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

    # -----------------------------------------------------------------
    # Clock plausibility filtering
    # -----------------------------------------------------------------
    def _accept_clock(self, reading: ScoreboardReading) -> Optional[float]:
        """
        Validate a raw OCR clock value against the previously accepted one.

        A game clock can never count down faster than real elapsed time, and it
        never counts up inside a period. Readings violating that are treated as
        OCR misreads and their clock value is dropped. After several consecutive
        rejections the tracker re-syncs on the newest value so legitimate resets
        (new period, new game, clock corrections) are not ignored forever.
        """
        clock = reading.clock_seconds
        if clock is None:
            return None

        ts = reading.timestamp
        period = reading.period

        baseline_known = (
            self.last_accepted_clock is not None
            and self.last_accepted_clock_time is not None
        )
        period_changed = (
            period is not None
            and self.last_accepted_period is not None
            and period != self.last_accepted_period
        )

        if baseline_known and not period_changed:
            elapsed = ts - self.last_accepted_clock_time
            delta = self.last_accepted_clock - clock
            tolerance = max(
                self.CLOCK_DELTA_TOLERANCE, elapsed * self.CLOCK_DELTA_TOLERANCE_RATIO
            )
            too_fast = delta > elapsed + tolerance
            counted_up = -delta > tolerance
            if elapsed >= 0 and (too_fast or counted_up):
                self.implausible_streak += 1
                if self.implausible_streak < self.MAX_IMPLAUSIBLE_STREAK:
                    self.rejected_clock_count += 1
                    return None

        self.implausible_streak = 0
        self.last_accepted_clock = clock
        self.last_accepted_clock_time = ts
        if period is not None:
            self.last_accepted_period = period
        return clock

    # -----------------------------------------------------------------
    # Pending end-of-game handling
    # -----------------------------------------------------------------
    def _begin_pending_end(
        self,
        ts: float,
        horn_time: float,
        description: str,
        reading: Optional[ScoreboardReading] = None,
    ) -> bool:
        """
        Register a candidate final horn. Unless confirmation is disabled, the
        game is not considered over until the clock has been observed not to
        resume for `end_confirm_window` seconds.
        """
        if not self.enable_end_confirm:
            self.final_horn_time = horn_time
            self.add_event(ts, f"{description} Game complete!", reading)
            self.state = GameState.GAME_COMPLETE
            return True

        self.pending_end_time = horn_time
        self.pending_end_started_at = ts
        self.pending_end_prev_state = self.state
        self.pending_resume_streak = 0
        self.pending_newgame_streak = 0
        self.state = GameState.PENDING_GAME_END
        self.add_event(
            ts,
            f"{description} Confirming the clock stays at 0:00 for the next "
            f"{int(self.end_confirm_window)}s before ending the game...",
            reading,
        )
        return False

    def _confirm_pending_end(
        self,
        ts: float,
        why: str,
        reading: Optional[ScoreboardReading] = None,
    ) -> bool:
        horn = self.pending_end_time if self.pending_end_time is not None else ts
        self.final_horn_time = horn
        self.state = GameState.GAME_COMPLETE
        event = TimelineEvent(timestamp=horn, description="", reading=reading)
        self.add_event(
            ts,
            f"Final horn confirmed at {event.timestamp_formatted} ({why}). "
            "Game complete!",
            reading,
        )
        return True

    def _cancel_pending_end(
        self,
        ts: float,
        clock: Optional[float],
        reading: Optional[ScoreboardReading] = None,
    ) -> None:
        pending = self.pending_end_time
        prev_state = self.pending_end_prev_state or GameState.P3_RUNNING
        pending_event = TimelineEvent(timestamp=pending or ts, description="")
        self.false_end_count += 1
        self.add_event(
            ts,
            f"False game end at {pending_event.timestamp_formatted}: clock resumed "
            f"at {format_clock(clock)}. Continuing to scan for the real final horn.",
            reading,
        )
        self.state = prev_state
        self.pending_end_time = None
        self.pending_end_started_at = None
        self.pending_end_prev_state = None
        self.pending_resume_streak = 0
        self.pending_newgame_streak = 0

        # Re-baseline the plausibility filter on the resumed clock, otherwise
        # every later reading looks like an upward jump from 0:00.
        if clock is not None:
            self.last_accepted_clock = clock
            self.last_accepted_clock_time = ts
            self.last_clock = clock
            self.implausible_streak = 0
            if reading is not None and reading.period is not None:
                self.last_accepted_period = reading.period

    def _process_pending_end(self, reading: ScoreboardReading) -> bool:
        """
        Handle a reading while a candidate final horn awaits confirmation.

        Raw OCR clock values are used here on purpose: once the pending clock is
        0:00 every resumed clock looks like an implausible upward jump, so the
        plausibility filter cannot help. Instead, several consecutive readings
        are required before a pending end is cancelled or reassigned.
        """
        ts = reading.timestamp
        raw_clock = reading.clock_seconds if reading.present else None

        if raw_clock is not None:
            if raw_clock > self.max_resume_clock and reading.period == 1:
                # A full period clock means the feed moved on to another game.
                self.pending_resume_streak = 0
                self.pending_newgame_streak += 1
                if self.pending_newgame_streak >= self.RESUME_CONFIRM_COUNT:
                    return self._confirm_pending_end(
                        ts, "a new game's clock appeared", reading
                    )
            elif raw_clock > self.CLOCK_END_THRESHOLD:
                self.pending_newgame_streak = 0
                self.pending_resume_streak += 1
                if self.pending_resume_streak >= self.RESUME_CONFIRM_COUNT:
                    self._cancel_pending_end(ts, raw_clock, reading)
                    return False
            else:
                self.pending_resume_streak = 0
                self.pending_newgame_streak = 0

        started = (
            self.pending_end_started_at
            if self.pending_end_started_at is not None
            else ts
        )
        if ts - started >= self.end_confirm_window:
            return self._confirm_pending_end(
                ts,
                f"clock stayed at 0:00 for {int(ts - started)}s",
                reading,
            )
        return False

    def finalize(self) -> bool:
        """
        Resolve any unconfirmed end of game once no further readings are
        available (e.g. the video ended shortly after the final horn).
        Returns True if a complete game has been identified.
        """
        if self.state == GameState.PENDING_GAME_END:
            ts = self.last_seen_scoreboard_time or self.pending_end_time or 0.0
            self._confirm_pending_end(ts, "no further footage to check")
        return self.state == GameState.GAME_COMPLETE

    def process_reading(self, reading: ScoreboardReading) -> bool:
        """
        Process a single scoreboard reading.
        Returns True if the first complete game has finished (no need to scan further).
        """
        if self.state == GameState.GAME_COMPLETE:
            return True

        ts = reading.timestamp
        clock = self._accept_clock(reading) if reading.present else None

        if self.state == GameState.PENDING_GAME_END:
            if reading.present:
                self.last_seen_scoreboard_time = ts
                if reading.score:
                    self.last_score = reading.score
                if clock is not None:
                    self.last_clock = clock
                if reading.period is not None:
                    self.last_period = reading.period
            return self._process_pending_end(reading)

        # Check if overlay disappeared after Period 3 (or OT)
        if not reading.present:
            if self.p3_observed and self.final_horn_time is None:
                # If scoreboard disappears after Period 3 has been running
                # and clock was low or already at/near end
                if self.last_clock is not None and self.last_clock <= 120.0:
                    horn = self.last_seen_scoreboard_time or ts
                    return self._begin_pending_end(
                        ts,
                        horn,
                        "Scoreboard disappeared after Period 3 "
                        f"(final clock ~{int(self.last_clock)}s).",
                        reading,
                    )
            return False

        # Scoreboard is present
        self.last_seen_scoreboard_time = ts
        period = reading.period
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
            elif (
                period == 1 and clock is not None and clock <= self.CLOCK_END_THRESHOLD
            ):
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
            elif (
                period == 2 and clock is not None and clock <= self.CLOCK_END_THRESHOLD
            ):
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
            if clock is not None and clock <= self.CLOCK_END_THRESHOLD:
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
                    score_str = f"{score[0]}-{score[1]}" if score else "regulation"
                    return self._begin_pending_end(
                        ts,
                        ts,
                        f"Period 3 clock reached 0:00 (Score: {score_str}).",
                        reading,
                    )

            elif period == 4:
                # Direct transition to OT observed
                self.ot_observed = True
                self.state = GameState.OT_RUNNING
                self.add_event(ts, "Overtime period began", reading)

        # -------------------------------------------------------------
        # STATE 7: OT_RUNNING
        # -------------------------------------------------------------
        elif self.state == GameState.OT_RUNNING:
            if clock is not None and clock <= self.CLOCK_END_THRESHOLD:
                return self._begin_pending_end(
                    ts, ts, "Overtime clock reached 0:00.", reading
                )

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
            if self.pending_end_time is not None:
                final_horn = self.pending_end_time
            elif self.last_seen_scoreboard_time and self.p3_observed:
                final_horn = self.last_seen_scoreboard_time
            else:
                final_horn = self.video_duration

        cut_start = max(0.0, puck_drop - self.buffer_before)
        cut_end = min(self.video_duration, final_horn + self.buffer_after)

        game_found = self.p1_observed and (
            self.p3_observed
            or self.state in (GameState.GAME_COMPLETE, GameState.PENDING_GAME_END)
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
