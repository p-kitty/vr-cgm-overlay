"""When a low announces itself, and when it stays quiet.

These are the rules for the alert on a glucose reading, so they are
worth more than a manual try. Each one below exists because of a
specific way the alert goes wrong:

  - firing throughout a low rather than on the way in trains people to
    mute it, and a muted alert is worse than none because it is trusted
  - firing on every crossing of a threshold the reading is hovering on
    does the same thing faster
  - firing on no reading at all treats "we do not know" as "you are fine
    now", which is the one reading this whole app exists to distrust

`LowAlert` is arithmetic over a value and a clock, so all of it runs
here. The sound itself is one winsound call and is not exercised: what
it does is the machine's business, and there is nothing to assert about
it that would not just be asserting the mock.
"""

from __future__ import annotations

import unittest

from cgm.core.alert import LowAlert

LOW = 70.0
MINUTE = 60.0


def feed(alert: LowAlert, readings, *, start: float = 0.0, step: float = MINUTE):
    """Push readings a minute apart; return which ones fired."""
    fired = []
    for i, mgdl in enumerate(readings):
        if alert.update(mgdl, start + i * step):
            fired.append((i, mgdl))
    return fired


class Entry(unittest.TestCase):
    def test_it_fires_on_the_way_in(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [100.0, 90.0, 65.0]), [(2, 65.0)])

    def test_it_fires_once_not_throughout(self):
        # The reason the original comment said "a repeating alert is
        # intolerable mid-game", kept.
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [65.0, 64.0, 63.0, 62.0, 61.0]), [(0, 65.0)])

    def test_a_first_reading_that_is_low_still_fires(self):
        # Starting the app already low is the case where being told
        # matters most, so it starts armed.
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [55.0]), [(0, 55.0)])

    def test_the_threshold_itself_is_not_low(self):
        # Same boundary the face uses: low is strictly below low_mgdl,
        # so the colour and the sound cannot disagree about one reading.
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [LOW]), [])

    def test_just_under_the_threshold_is_low(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [LOW - 0.5]), [(0, LOW - 0.5)])


class Hysteresis(unittest.TestCase):
    def test_crossing_back_over_the_threshold_does_not_re_arm(self):
        # The sensor's own noise is a couple of mg/dL, the same size as
        # the moves here. A bare threshold test would announce every
        # wobble as a fresh low.
        alert = LowAlert(LOW, rearm_mgdl=5.0)
        self.assertEqual(
            feed(alert, [65.0, 71.0, 65.0, 72.0, 64.0]), [(0, 65.0)]
        )

    def test_clearing_the_margin_re_arms(self):
        alert = LowAlert(LOW, rearm_mgdl=5.0)
        self.assertEqual(
            feed(alert, [65.0, 80.0, 64.0]), [(0, 65.0), (2, 64.0)]
        )

    def test_the_margin_boundary_re_arms(self):
        # At exactly low + margin the reading has recovered, so the next
        # dip is news. The band that stays quiet is below it.
        alert = LowAlert(LOW, rearm_mgdl=5.0)
        self.assertEqual(feed(alert, [65.0, 75.0, 64.0]), [(0, 65.0), (2, 64.0)])

    def test_a_hair_under_the_margin_does_not_re_arm(self):
        alert = LowAlert(LOW, rearm_mgdl=5.0)
        self.assertEqual(feed(alert, [65.0, 74.9, 64.0]), [(0, 65.0)])

    def test_a_zero_margin_is_the_bare_threshold_test(self):
        # What the inline version did. Still reachable for anyone who
        # would rather have it.
        alert = LowAlert(LOW, rearm_mgdl=0.0)
        self.assertEqual(
            feed(alert, [65.0, 71.0, 65.0]), [(0, 65.0), (2, 65.0)]
        )


class Repeat(unittest.TestCase):
    def test_repeating_is_off_by_default(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [60.0] * 30), [(0, 60.0)])

    def test_it_repeats_on_the_interval_while_low(self):
        alert = LowAlert(LOW, repeat_min=5.0)
        fired = feed(alert, [60.0] * 12)  # twelve minutes, one a minute
        self.assertEqual([i for i, _ in fired], [0, 5, 10])

    def test_a_repeat_does_not_survive_recovery(self):
        # Climbing clear re-arms, which resets the clock: the next low
        # starts its own cycle rather than inheriting the old one.
        alert = LowAlert(LOW, rearm_mgdl=5.0, repeat_min=5.0)
        fired = feed(alert, [60.0, 60.0, 90.0, 60.0, 60.0, 60.0])
        self.assertEqual([i for i, _ in fired], [0, 3])

    def test_a_repeat_does_not_fire_above_the_threshold(self):
        alert = LowAlert(LOW, rearm_mgdl=5.0, repeat_min=1.0)
        self.assertEqual(feed(alert, [60.0, 72.0, 72.0, 72.0]), [(0, 60.0)])


class NoReading(unittest.TestCase):
    def test_no_reading_says_nothing(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [None, None, None]), [])

    def test_no_reading_does_not_re_arm(self):
        # A failed fetch is not a recovery. The last value stays on the
        # face; a gap in fetching must not turn into a second alert for
        # the same low once it comes back.
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [65.0, None, None, 64.0]), [(0, 65.0)])

    def test_no_reading_before_the_first_low_leaves_it_armed(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [None, None, 65.0]), [(2, 65.0)])


class Retuning(unittest.TestCase):
    def test_an_edited_threshold_takes_effect(self):
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [75.0]), [])
        alert.set_tuning(80.0, 5.0, 0.0)
        self.assertEqual(alert.update(75.0, 10 * MINUTE), True)

    def test_retuning_does_not_re_announce_a_low_already_told(self):
        # Placement and thresholds get nudged with the headset on. A
        # reload must not sound the alert again for the reading already
        # on the face.
        alert = LowAlert(LOW)
        self.assertEqual(feed(alert, [60.0]), [(0, 60.0)])
        alert.set_tuning(LOW, 5.0, 0.0)
        self.assertFalse(alert.update(60.0, 5 * MINUTE))


if __name__ == "__main__":
    unittest.main()
