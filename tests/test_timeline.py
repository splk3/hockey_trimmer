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

        # 5. Period 2
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

        # 7. Final Horn: Period 3 clock reaches 0:00 (Score 3-1, not tied)
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=3600.0,
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
                timestamp=1000.0,
                period=2,
                clock_seconds=500.0,
                score=(1, 1),
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

        # Overtime ends at 0:00 (Score 3-2 or tied)
        is_done = tracker.process_reading(
            ScoreboardReading(
                present=True,
                timestamp=3300.0,
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
                present=True, timestamp=1500.0, period=2, clock_seconds=300.0
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

        # Scoreboard turns off at 2550s
        is_done = tracker.process_reading(
            ScoreboardReading(present=False, timestamp=2550.0)
        )
        self.assertTrue(is_done)
        self.assertEqual(tracker.state, GameState.GAME_COMPLETE)
        self.assertEqual(tracker.final_horn_time, 2500.0)


if __name__ == "__main__":
    unittest.main()
