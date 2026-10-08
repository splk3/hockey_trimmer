"""
Tests for GameTimelineTracker state machine.
"""

import unittest
from hockey_trimmer.detector import ScoreboardReading
from hockey_trimmer.timeline import GameTimelineTracker, GameState


class TestGameTimelineTracker(unittest.TestCase):

    def test_standard_three_period_game(self):
        tracker = GameTimelineTracker(
            buffer_before=15.0, buffer_after=15.0, video_duration=5000.0
        )

        # 1. Warmups / Pre-game: No scoreboard
        self.assertFalse(
            tracker.process_reading(ScoreboardReading(present=False, timestamp=100.0))
        )
        self.assertEqual(tracker.state, GameState.SEARCHING_START)

        # 2. Scoreboard appears in P1, clock at 15:00 (not ticking yet)
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=300.0,
                    period=1,
                    clock_seconds=900.0,
                    score=(0, 0),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.SEARCHING_START)

        # 3. Puck drop: clock ticks down to 14:58
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=315.0,
                    period=1,
                    clock_seconds=898.0,
                    score=(0, 0),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.puck_drop_time, 315.0)

        # 4. End of P1
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=1200.0,
                    period=1,
                    clock_seconds=0.0,
                    score=(1, 0),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.INTERMISSION_1)

        # 5. Period 2 (a period must be read twice in a row to be trusted)
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=1490.0,
                period=2,
                clock_seconds=860.0,
                score=(1, 1),
            )
        )
        self.assertEqual(tracker.state, GameState.INTERMISSION_1)
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=1500.0,
                    period=2,
                    clock_seconds=850.0,
                    score=(1, 1),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P2_RUNNING)

        # 6. Period 3
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=2690.0,
                period=3,
                clock_seconds=900.0,
                score=(2, 1),
            )
        )
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=2700.0,
                    period=3,
                    clock_seconds=900.0,
                    score=(2, 1),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P3_RUNNING)

        # 7. Candidate final horn: Period 3 clock reaches 0:00 (Score 3-1, not tied)
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=3600.0,
                period=3,
                clock_seconds=0.0,
                score=(3, 1),
            )
        )
        self.assertFalse(is_done)
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        # 8. Clock stays at 0:00 through the confirmation window
        for ts in range(3610, 3760, 10):
            is_done = tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=float(ts),
                    period=3,
                    clock_seconds=0.0,
                    score=(3, 1),
                )
            )
        self.assertTrue(is_done)
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 3600.0)

        # Check cut boundaries
        boundaries = tracker.get_boundaries()
        self.assertTrue(boundaries.game_found)
        self.assertEqual(boundaries.cut_start_time, 300.0)  # 315 - 15
        self.assertEqual(boundaries.cut_end_time, 3615.0)  # 3600 + 15
        self.assertEqual(boundaries.duration_seconds, 3315.0)

    def test_skip_midgame_recording(self):
        """If video starts mid-game during Period 2, it should be skipped until Period 1 of the complete game."""
        tracker = GameTimelineTracker(
            buffer_before=15.0, buffer_after=15.0, video_duration=6000.0
        )

        # Video starts in Period 2 of prior game
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=50.0,
                period=2,
                clock_seconds=400.0,
                score=(2, 2),
            )
        )
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        self.assertFalse(tracker.p1_observed)
        self.assertIsNone(tracker.final_horn_time)

        # Prior game's final horn cannot end the target game before Period 1 is seen.
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=60.0,
                    period=3,
                    clock_seconds=0.0,
                    score=(3, 2),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        self.assertIsNone(tracker.final_horn_time)

        # Prior game ends, scoreboard disappears
        tracker.process_reading(ScoreboardReading(present=False, timestamp=500.0))
        self.assertEqual(tracker.state, GameState.SEARCHING_START)

        # Next game starts: Period 1 appears
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=800.0,
                period=1,
                clock_seconds=900.0,
                score=(0, 0),
            )
        )
        self.assertTrue(tracker.p1_observed)

        # Puck drops
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=815.0,
                period=1,
                clock_seconds=895.0,
                score=(0, 0),
            )
        )
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.puck_drop_time, 815.0)

    def test_overtime_when_tied(self):
        """If score is tied at end of Period 3, game continues into Overtime."""
        tracker = GameTimelineTracker(
            buffer_before=10.0, buffer_after=10.0, video_duration=5000.0
        )

        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=100.0,
                period=1,
                clock_seconds=900.0,
                score=(0, 0),
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=110.0,
                period=1,
                clock_seconds=895.0,
                score=(0, 0),
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=990.0,
                period=2,
                clock_seconds=510.0,
                score=(1, 1),
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=1000.0,
                period=2,
                clock_seconds=500.0,
                score=(1, 1),
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=1990.0,
                period=3,
                clock_seconds=510.0,
                score=(2, 2),
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=2000.0,
                period=3,
                clock_seconds=500.0,
                score=(2, 2),
            )
        )

        # Period 3 ends tied 2-2
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=2900.0,
                    period=3,
                    clock_seconds=0.0,
                    score=(2, 2),
                )
            )
        )
        self.assertEqual(tracker.state, GameState.OT_RUNNING)
        self.assertTrue(tracker.tied_at_p3_end)

        # Overtime ends at 0:00 (Score 3-2), then confirmed
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=3300.0,
                period=4,
                clock_seconds=0.0,
                score=(3, 2),
            )
        )
        self.assertFalse(is_done)
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        for ts in range(3310, 3460, 10):
            is_done = tracker.process_reading(
                ScoreboardReading(
                    present=True,
                    timestamp=float(ts),
                    period=4,
                    clock_seconds=0.0,
                    score=(3, 2),
                )
            )
        self.assertTrue(is_done)
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 3300.0)

    def test_scoreboard_disappearance_after_p3(self):
        """If scoreboard disappears after Period 3 (low clock), game concludes."""
        tracker = GameTimelineTracker(
            buffer_before=15.0, buffer_after=15.0, video_duration=4000.0
        )

        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=100.0, period=1, clock_seconds=900.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=115.0, period=1, clock_seconds=895.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=1490.0, period=2, clock_seconds=310.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=1500.0, period=2, clock_seconds=300.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=2490.0, period=3, clock_seconds=55.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=2500.0,
                period=3,
                clock_seconds=45.0,
                score=(4, 1),
            )
        )

        # Scoreboard turns off at 2550s -> candidate end, pending confirmation
        is_done = tracker.process_reading(
            ScoreboardReading(present=False, timestamp=2550.0)
        )
        self.assertFalse(is_done)
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        # It stays off for the confirmation window
        for ts in range(2560, 2710, 10):
            is_done = tracker.process_reading(
                ScoreboardReading(present=False, timestamp=float(ts))
            )
        self.assertTrue(is_done)
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 2500.0)


class TestEndOfGameConfirmation(unittest.TestCase):
    """Covers the final-horn confirmation window and clock plausibility filter."""

    def _advance_to_p3(self, tracker, p3_clock=120.0, p3_time=3000.0):
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=100.0, period=1, clock_seconds=900.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=115.0, period=1, clock_seconds=895.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=1490.0, period=2, clock_seconds=610.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=1500.0, period=2, clock_seconds=600.0
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=p3_time - 10.0,
                period=3,
                clock_seconds=p3_clock + 10.0,
            )
        )
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=p3_time, period=3, clock_seconds=p3_clock
            )
        )
        return tracker

    def test_unknown_terminal_tab_requires_confirmation_and_detects_resumption(self):
        for terminal_period in (3, 4):
            with self.subTest(period=terminal_period):
                tracker = GameTimelineTracker()
                self._advance_to_p3(tracker, p3_clock=60)
                if terminal_period == 4:
                    for ts in (3010, 3020):
                        tracker.process_reading(ScoreboardReading(True, ts, 4, 60))
                    self.assertTrue(tracker.ot_observed)
                tracker.process_reading(ScoreboardReading(True, 3080, None, 0))
                self.assertEqual(tracker.state, GameState.PENDING_GAME_END)
                self.assertIsNone(tracker.final_horn_time)
                # An operator resets the clock; this was not the final horn.
                for ts in (3090, 3100):
                    tracker.process_reading(ScoreboardReading(True, ts, None, 60))
                self.assertEqual(tracker.false_end_count, 1)
                self.assertEqual(
                    tracker.state,
                    (
                        GameState.P3_RUNNING
                        if terminal_period == 3
                        else GameState.OT_RUNNING
                    ),
                )
                tracker.process_reading(ScoreboardReading(True, 3170, None, 0))
                self.assertEqual(tracker.state, GameState.PENDING_GAME_END)
                tracker.process_reading(ScoreboardReading(True, 3310, None, 0))
                self.assertIsNone(tracker.final_horn_time)
                self.assertTrue(
                    tracker.process_reading(ScoreboardReading(True, 3320, None, 0))
                )
                self.assertEqual(tracker.final_horn_time, 3170)

    def test_unknown_period_cannot_establish_terminal_context(self):
        tracker = GameTimelineTracker()
        for ts in (0, 10):
            tracker.process_reading(ScoreboardReading(True, ts, 1, 900))
        tracker.process_reading(ScoreboardReading(True, 20, 1, 890))
        for ts in (1000, 1010, 1200):
            tracker.process_reading(ScoreboardReading(True, ts, None, 0))
        self.assertEqual(tracker.state, GameState.INTERMISSION_1)
        self.assertIsNone(tracker.pending_end_time)
        self.assertFalse(tracker.get_boundaries().game_found)

    def test_tied_regulation_zero_with_unknown_tab_is_not_overtime_horn(self):
        tracker = GameTimelineTracker()
        self._advance_to_p3(tracker, p3_clock=10)
        tracker.process_reading(ScoreboardReading(True, 3010, 3, 0, (1, 1)))
        self.assertEqual(tracker.state, GameState.OT_RUNNING)
        self.assertFalse(tracker.ot_observed)
        for ts in (3020, 3030, 3200):
            tracker.process_reading(ScoreboardReading(True, ts, None, 0))
        self.assertIsNone(tracker.pending_end_time)
        self.assertIsNone(tracker.final_horn_time)

    def test_known_regulation_tab_cannot_end_established_overtime(self):
        tracker = GameTimelineTracker()
        self._advance_to_p3(tracker, p3_clock=60)
        for ts in (3010, 3020):
            tracker.process_reading(ScoreboardReading(True, ts, 4, 60))
        tracker.process_reading(ScoreboardReading(True, 3080, 3, 0))
        self.assertEqual(tracker.state, GameState.OT_RUNNING)
        self.assertIsNone(tracker.pending_end_time)

    def test_false_end_during_timeout_is_rejected(self):
        """A stoppage misread as 0:00 must not end the game; the real horn is used."""
        tracker = GameTimelineTracker(
            buffer_before=15.0,
            buffer_after=15.0,
            video_duration=6000.0,
            end_confirm_window=150.0,
        )
        self._advance_to_p3(tracker, p3_clock=60.0, p3_time=3000.0)
        self.assertEqual(tracker.state, GameState.P3_RUNNING)

        # Timeout: clock frozen at 0:51, then a single-frame OCR misread of 0:01.
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3010.0, period=3, clock_seconds=51.0
            )
        )
        # Implausible (51s -> 1s in 10s) so the clock value is rejected outright.
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3020.0, period=3, clock_seconds=1.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P3_RUNNING)
        self.assertEqual(tracker.rejected_clock_count, 1)

        # A plausible 0:00 (slow, sustained countdown) opens a pending end...
        readings = [(3030.0, 45.0), (3060.0, 20.0), (3090.0, 0.0)]
        for ts, clock in readings:
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=ts, period=3, clock_seconds=clock
                )
            )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)
        self.assertEqual(tracker.pending_end_time, 3090.0)

        # ...but the clock resumes shortly after -> false end, keep scanning.
        # Two consecutive above-zero readings are required.
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3130.0, period=3, clock_seconds=35.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3140.0, period=3, clock_seconds=30.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P3_RUNNING)
        self.assertIsNone(tracker.pending_end_time)
        self.assertEqual(tracker.false_end_count, 1)
        self.assertIsNone(tracker.final_horn_time)

        # Real final horn, sustained through the confirmation window.
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3160.0, period=3, clock_seconds=10.0
            )
        )
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3170.0, period=3, clock_seconds=0.0
            )
        )
        self.assertFalse(is_done)
        for ts in range(3180, 3330, 10):
            is_done = tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=float(ts), period=3, clock_seconds=0.0
                )
            )
        self.assertTrue(is_done)
        self.assertEqual(tracker.final_horn_time, 3170.0)
        self.assertEqual(tracker.get_boundaries().cut_end_time, 3185.0)

    def test_pending_end_resolved_when_video_ends(self):
        """If the footage runs out mid-confirmation, the pending horn is used."""
        tracker = GameTimelineTracker(
            buffer_before=15.0, buffer_after=15.0, video_duration=3200.0
        )
        self._advance_to_p3(tracker, p3_clock=30.0, p3_time=3000.0)
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3030.0, period=3, clock_seconds=0.0
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        for ts in (3040.0, 3050.0, 3060.0):
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=ts, period=3, clock_seconds=0.0
                )
            )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        self.assertTrue(tracker.finalize())
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 3030.0)
        boundaries = tracker.get_boundaries()
        self.assertTrue(boundaries.game_found)
        self.assertEqual(boundaries.cut_end_time, 3045.0)

    def test_next_game_clock_confirms_end(self):
        """A full period clock after 0:00 means the next game, not a resumption."""
        tracker = GameTimelineTracker(video_duration=6000.0)
        self._advance_to_p3(tracker, p3_clock=30.0, p3_time=3000.0)
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3030.0, period=3, clock_seconds=0.0
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        # A single full-period reading is not enough (it may be a misread).
        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3040.0, period=1, clock_seconds=900.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3050.0, period=1, clock_seconds=900.0
            )
        )
        self.assertTrue(is_done)
        self.assertEqual(tracker.final_horn_time, 3030.0)

    def test_high_clock_in_period3_cancels_pending_end(self):
        """High resumed clocks outside Period 1 should cancel pending end."""
        tracker = GameTimelineTracker(video_duration=6000.0)
        self._advance_to_p3(tracker, p3_clock=30.0, p3_time=3000.0)
        tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3030.0, period=3, clock_seconds=0.0
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3040.0, period=3, clock_seconds=600.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.PENDING_GAME_END)

        self.assertFalse(
            tracker.process_reading(
                ScoreboardReading(
                    present=True, timestamp=3050.0, period=3, clock_seconds=600.0
                )
            )
        )
        self.assertEqual(tracker.state, GameState.P3_RUNNING)
        self.assertIsNone(tracker.final_horn_time)
        self.assertEqual(tracker.false_end_count, 1)

    def test_implausible_clock_drop_is_rejected_then_resyncs(self):
        """Isolated misreads are dropped, but a sustained new value re-syncs."""
        tracker = GameTimelineTracker(video_duration=6000.0)
        self._advance_to_p3(tracker, p3_clock=300.0, p3_time=3000.0)

        # 5:00 -> 0:05 in 10s is impossible.
        self.assertIsNone(
            tracker._accept_clock(
                ScoreboardReading(
                    present=True, timestamp=3010.0, period=3, clock_seconds=5.0
                )
            )
        )
        # Plausible countdown is accepted.
        self.assertEqual(
            tracker._accept_clock(
                ScoreboardReading(
                    present=True, timestamp=3020.0, period=3, clock_seconds=280.0
                )
            ),
            280.0,
        )
        # Repeated implausible values eventually re-sync (clock correction).
        for ts in (3030.0, 3040.0):
            self.assertIsNone(
                tracker._accept_clock(
                    ScoreboardReading(
                        present=True, timestamp=ts, period=3, clock_seconds=600.0
                    )
                )
            )
        self.assertEqual(
            tracker._accept_clock(
                ScoreboardReading(
                    present=True, timestamp=3050.0, period=3, clock_seconds=600.0
                )
            ),
            600.0,
        )

    def test_end_confirmation_can_be_disabled(self):
        """--no-end-confirm restores the legacy immediate-stop behavior."""
        tracker = GameTimelineTracker(
            buffer_before=15.0,
            buffer_after=15.0,
            video_duration=6000.0,
            enable_end_confirm=False,
        )
        self._advance_to_p3(tracker, p3_clock=30.0, p3_time=3000.0)
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True, timestamp=3030.0, period=3, clock_seconds=0.0
            )
        )
        self.assertTrue(is_done)
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 3030.0)


def _r(ts, period=None, clock=None, present=True, score=None):
    return ScoreboardReading(
        present=present,
        timestamp=float(ts),
        period=period,
        clock_seconds=None if clock is None else float(clock),
        score=score,
    )


class TestGameStartDetection(unittest.TestCase):
    """Guards that keep a previous game's late periods from faking a start."""

    def test_previous_game_p3_with_p1_misreads_is_skipped(self):
        """
        Replays 20260927-ducks12aa-at-pbk12aa-raw.mp4 as the old OCR saw it:
        the feed opens ~5:48 into a previous game's Period 3 and about half
        the tabs were misread as "1". The real puck drop is at ~28:18.
        """
        tracker = GameTimelineTracker(video_duration=8143.0)
        prev_game = [
            (0, 3, 348),
            (10, 1, None),
            (20, 1, None),
            (30, 3, 319),
            (40, 1, 311),
            (50, 1, 309),
            (60, 1, 309),
            (70, 1, 305),
            (80, 1, 295),
            (90, 3, 285),
            (100, 3, None),
            (110, 1, None),
            (120, 1, None),
            (170, 1, 252),
            (180, 1, 242),
        ]
        for ts, period, clock in prev_game:
            self.assertFalse(tracker.process_reading(_r(ts, period, clock)))
            self.assertEqual(tracker.state, GameState.SEARCHING_START)
        self.assertIsNone(tracker.puck_drop_time)

        # Break between games: no overlay for ~16 minutes.
        for ts in range(650, 1600, 10):
            tracker.process_reading(_r(ts, present=False))
        self.assertEqual(tracker.state, GameState.SEARCHING_START)

        # New game: Period 1 parked at 15:00, then the clock starts.
        for ts in range(1600, 1700, 10):
            tracker.process_reading(_r(ts, 1, 900))
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        tracker.process_reading(_r(1700, 1, 898))
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.puck_drop_time, 1700.0)
        self.assertTrue(
            any("already in progress" in e.description for e in tracker.events)
        )

    def test_low_period1_clock_is_not_a_game_start(self):
        """A consistent Period 1 at a low clock is not a full-period start."""
        tracker = GameTimelineTracker(video_duration=6000.0)
        for ts, clock in ((0, 311), (10, 309), (20, 300), (30, 290)):
            tracker.process_reading(_r(ts, 1, clock))
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        self.assertFalse(tracker.p1_observed)
        self.assertFalse(tracker.get_boundaries().game_found)

    def test_min_period_start_clock_is_configurable(self):
        tracker = GameTimelineTracker(min_period_start_clock=240.0)
        tracker.process_reading(_r(0, 1, 300))
        tracker.process_reading(_r(10, 1, 295))
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.puck_drop_time, 10.0)

    def test_confirmed_later_period_discards_p1_candidate(self):
        tracker = GameTimelineTracker()
        tracker.process_reading(_r(0, 1, 900))
        self.assertTrue(tracker.p1_observed)
        tracker.process_reading(_r(10, 3, 600))
        tracker.process_reading(_r(20, 3, 590))
        self.assertFalse(tracker.p1_observed)
        self.assertIsNone(tracker.p1_candidate_start)
        self.assertIsNone(tracker.p1_max_clock)

    def test_single_period_misread_does_not_change_state(self):
        tracker = GameTimelineTracker()
        tracker.process_reading(_r(0, 1, 900))
        tracker.process_reading(_r(10, 1, 890))
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        # One stray "3" (or "2") must not advance the game.
        tracker.process_reading(_r(20, 3, 880))
        tracker.process_reading(_r(30, 1, 870))
        tracker.process_reading(_r(40, 2, 860))
        tracker.process_reading(_r(50, 1, 850))
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.confirmed_period, 1)

    def test_single_period_error_cannot_rebaseline_clock_or_end_game(self):
        tracker = GameTimelineTracker()
        for ts, period, clock in (
            (0, 1, 900),
            (10, 1, 890),
            (20, 2, 870),
            (30, 2, 860),
            (40, 3, 600),
            (50, 3, 590),
        ):
            tracker.process_reading(_r(ts, period, clock))
        self.assertEqual(tracker.state, GameState.P3_RUNNING)

        tracker.process_reading(_r(60, 1, 0))
        self.assertEqual(tracker.state, GameState.P3_RUNNING)
        self.assertEqual(tracker.last_accepted_clock, 590)
        self.assertIsNone(tracker.pending_end_time)
        tracker.process_reading(_r(70, 3, 570))
        self.assertEqual(tracker.state, GameState.P3_RUNNING)

    def test_period3_before_period2_discards_false_start(self):
        tracker = GameTimelineTracker()
        tracker.process_reading(_r(0, 1, 900))
        tracker.process_reading(_r(10, 1, 890))
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        tracker.process_reading(_r(20, 3, 500))
        tracker.process_reading(_r(30, 3, 490))
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        self.assertIsNone(tracker.puck_drop_time)
        self.assertFalse(tracker.p1_observed)

    def test_long_overlay_gap_resets_start_candidate(self):
        tracker = GameTimelineTracker(start_gap_reset=300.0)
        tracker.process_reading(_r(0, 1, 900))
        tracker.process_reading(_r(10, 1, 900))
        self.assertEqual(tracker.p1_candidate_start, 0.0)
        for ts in range(20, 400, 10):
            tracker.process_reading(_r(ts, present=False))
        tracker.process_reading(_r(400, 1, 900))
        self.assertEqual(tracker.p1_candidate_start, 400.0)
        self.assertEqual(tracker.state, GameState.SEARCHING_START)
        # Period votes were reset too, so one reading is not yet confirmed.
        self.assertIsNone(tracker.confirmed_period)

    def test_short_overlay_gap_keeps_start_candidate(self):
        tracker = GameTimelineTracker(start_gap_reset=300.0)
        tracker.process_reading(_r(0, 1, 900))
        tracker.process_reading(_r(10, 1, 900))
        for ts in range(20, 100, 10):
            tracker.process_reading(_r(ts, present=False))
        tracker.process_reading(_r(100, 1, 895))
        self.assertEqual(tracker.p1_candidate_start, 0.0)
        self.assertEqual(tracker.state, GameState.P1_RUNNING)
        self.assertEqual(tracker.puck_drop_time, 100.0)


if __name__ == "__main__":
    unittest.main()
