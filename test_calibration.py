#!/usr/bin/env python3
"""Tests for the program that measures the calibration.

The tests write small CSV files. They need no connection to the internet.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import csv
import math
import os
import tempfile
import unittest

import calibration as cal

CLOSE_TS = 1788411600
HOUR = 3600


def write_csv(path, fields, rows):
    """Write one small CSV file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class TestNumbers(unittest.TestCase):
    """The program reads a number from a text."""

    def test_an_empty_text_gives_none(self):
        self.assertIsNone(cal.to_float(""))
        self.assertIsNone(cal.to_float(None))

    def test_a_bad_text_gives_none(self):
        self.assertIsNone(cal.to_float("abc"))

    def test_a_good_text_gives_a_number(self):
        self.assertEqual(cal.to_float("0.7000"), 0.7)


class TestMiddlePrice(unittest.TestCase):
    """The program makes the middle price from the two sides."""

    def test_the_program_uses_the_two_sides(self):
        self.assertAlmostEqual(
            cal.candle_mid({"yes_bid_close": "0.40", "yes_ask_close": "0.44"}), 0.42)

    def test_one_side_alone_gives_that_side(self):
        self.assertAlmostEqual(
            cal.candle_mid({"yes_bid_close": "0.40", "yes_ask_close": ""}), 0.40)

    def test_no_price_gives_none(self):
        self.assertIsNone(cal.candle_mid({"yes_bid_close": "", "yes_ask_close": ""}))


class TestClusteredError(unittest.TestCase):
    """The error groups the markets of one event together."""

    def test_the_markets_of_one_event_do_not_count_as_separate(self):
        # Two events. Each event has three markets with the same answer. The
        # three markets are one measurement, and not three measurements.
        rows = ([{"event": "A", "outcome": 1.0}] * 3 +
                [{"event": "B", "outcome": 0.0}] * 3)
        clustered = cal.clustered_error(rows, 0.5, "outcome")
        independent = math.sqrt(0.25 / 6)
        self.assertGreater(clustered, independent)
        self.assertAlmostEqual(clustered, math.sqrt(4.5) / 6)

    def test_one_market_for_each_event_gives_the_normal_error(self):
        rows = [{"event": "A", "outcome": 1.0}, {"event": "B", "outcome": 0.0}]
        clustered = cal.clustered_error(rows, 0.5, "outcome")
        self.assertAlmostEqual(clustered, math.sqrt(0.5) / 2)

    def test_no_line_gives_none(self):
        self.assertIsNone(cal.clustered_error([], 0.5, "outcome"))


class TestSmallestError(unittest.TestCase):
    """A bin where each market gave the same answer has no error of 0."""

    def test_an_answer_of_always_yes_does_not_give_an_error_of_zero(self):
        self.assertGreater(cal.smallest_error(1.0, 6), 0.10)

    def test_an_answer_of_always_no_does_not_give_an_error_of_zero(self):
        self.assertGreater(cal.smallest_error(0.0, 6), 0.10)

    def test_more_events_give_a_smaller_error(self):
        self.assertLess(cal.smallest_error(1.0, 200), cal.smallest_error(1.0, 6))

    def test_the_error_of_a_bin_is_never_zero(self):
        rows = [{"event": f"E{i}", "outcome": 1.0} for i in range(6)]
        self.assertGreater(cal.bin_error(rows, 1.0, 6), 0.0)

    def test_the_error_of_a_bin_uses_the_larger_of_the_two(self):
        # Two events, three markets each, with opposite answers. The grouped
        # error is large. It must win against the floor.
        rows = ([{"event": "A", "outcome": 1.0}] * 3 +
                [{"event": "B", "outcome": 0.0}] * 3)
        self.assertAlmostEqual(cal.bin_error(rows, 0.5, 2), math.sqrt(4.5) / 6)


class TestBins(unittest.TestCase):
    """The program puts each market into a bin of the price."""

    def make_rows(self, prices):
        return [{"event": f"E{i}", "price": p, "outcome": 1.0}
                for i, p in enumerate(prices)]

    def test_the_program_uses_the_correct_bin(self):
        bins = cal.make_bins(self.make_rows([0.05, 0.35, 0.95]), 10)
        self.assertEqual([(b["low"], b["high"]) for b in bins],
                         [(0, 10), (30, 40), (90, 100)])

    def test_a_price_of_one_goes_into_the_last_bin(self):
        bins = cal.make_bins(self.make_rows([1.0]), 10)
        self.assertEqual((bins[0]["low"], bins[0]["high"]), (90, 100))

    def test_the_program_counts_the_events_and_the_contracts(self):
        rows = [{"event": "A", "price": 0.55, "outcome": 1.0},
                {"event": "A", "price": 0.56, "outcome": 1.0},
                {"event": "B", "price": 0.57, "outcome": 0.0}]
        bins = cal.make_bins(rows, 10)
        self.assertEqual(bins[0]["contracts"], 3)
        self.assertEqual(bins[0]["events"], 2)

    def test_the_program_gives_the_part_of_the_answers(self):
        rows = [{"event": "A", "price": 0.7, "outcome": 1.0},
                {"event": "B", "price": 0.7, "outcome": 0.0}]
        bins = cal.make_bins(rows, 10)
        self.assertAlmostEqual(bins[0]["realized"], 0.5)
        self.assertAlmostEqual(bins[0]["mean_price"], 0.7)


class TestBrier(unittest.TestCase):
    """The Brier score measures the quality of the forecast."""

    def test_a_perfect_forecast_gives_zero(self):
        rows = [{"price": 1.0, "outcome": 1.0}, {"price": 0.0, "outcome": 0.0}]
        self.assertAlmostEqual(cal.brier_score(rows), 0.0)

    def test_a_coin_gives_one_quarter(self):
        rows = [{"price": 0.5, "outcome": 1.0}, {"price": 0.5, "outcome": 0.0}]
        self.assertAlmostEqual(cal.brier_score(rows), 0.25)


class TestHorizon(unittest.TestCase):
    """The program takes the price before the close, and not at the close."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write_pair(self, candles):
        write_csv(os.path.join(self.folder.name, "settlements_S.csv"),
                  ["ticker", "event_ticker", "result_binary", "close_ts"],
                  [{"ticker": "M1", "event_ticker": "E1",
                    "result_binary": "1", "close_ts": CLOSE_TS}])
        write_csv(os.path.join(self.folder.name, "candles_S.csv"),
                  ["ticker", "end_period_ts", "yes_bid_close", "yes_ask_close"],
                  candles)

    def test_the_program_takes_the_last_price_before_the_horizon(self):
        self.write_pair([
            {"ticker": "M1", "end_period_ts": CLOSE_TS - 10 * HOUR,
             "yes_bid_close": "0.30", "yes_ask_close": "0.30"},
            {"ticker": "M1", "end_period_ts": CLOSE_TS - 7 * HOUR,
             "yes_bid_close": "0.40", "yes_ask_close": "0.40"},
            {"ticker": "M1", "end_period_ts": CLOSE_TS - 1 * HOUR,
             "yes_bid_close": "0.99", "yes_ask_close": "0.99"},
        ])
        rows, missing = cal.build_observations(self.folder.name, ["S"], 6 * HOUR)
        self.assertEqual(missing, 0)
        self.assertEqual(len(rows), 1)
        # The price of 7 hours before the close, and not the price of 1 hour.
        self.assertAlmostEqual(rows[0]["price"], 0.40)

    def test_a_market_without_a_price_at_the_horizon_is_not_used(self):
        self.write_pair([
            {"ticker": "M1", "end_period_ts": CLOSE_TS - 1 * HOUR,
             "yes_bid_close": "0.99", "yes_ask_close": "0.99"},
        ])
        rows, missing = cal.build_observations(self.folder.name, ["S"], 6 * HOUR)
        self.assertEqual(rows, [])
        self.assertEqual(missing, 1)

    def test_a_market_without_an_answer_is_not_used(self):
        write_csv(os.path.join(self.folder.name, "settlements_S.csv"),
                  ["ticker", "event_ticker", "result_binary", "close_ts"],
                  [{"ticker": "M1", "event_ticker": "E1",
                    "result_binary": "", "close_ts": CLOSE_TS}])
        write_csv(os.path.join(self.folder.name, "candles_S.csv"),
                  ["ticker", "end_period_ts", "yes_bid_close", "yes_ask_close"],
                  [{"ticker": "M1", "end_period_ts": CLOSE_TS - 7 * HOUR,
                    "yes_bid_close": "0.40", "yes_ask_close": "0.40"}])
        rows, _missing = cal.build_observations(self.folder.name, ["S"], 6 * HOUR)
        self.assertEqual(rows, [])

    def test_a_folder_without_a_file_gives_no_observation(self):
        rows, _missing = cal.build_observations(self.folder.name, ["NONE"], 6 * HOUR)
        self.assertEqual(rows, [])


class TestOptions(unittest.TestCase):
    """The options of the command line have the correct effect."""

    def test_the_default_horizon_is_six_hours(self):
        self.assertEqual(cal.parse_args([]).hours, 6.0)

    def test_a_bin_that_does_not_divide_one_hundred_is_not_permitted(self):
        with self.assertRaises(SystemExit):
            cal.parse_args(["--bin-width", "30"])

    def test_a_good_bin_is_permitted(self):
        self.assertEqual(cal.parse_args(["--bin-width", "25"]).bin_width, 25)

    def test_a_negative_horizon_is_not_permitted(self):
        with self.assertRaises(SystemExit):
            cal.parse_args(["--hours", "-1"])


class TestMain(unittest.TestCase):
    """The program runs from the start to the end."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_an_empty_folder_gives_an_error(self):
        self.assertEqual(cal.main(["--data-dir", self.folder.name,
                                   "--series", "S"]), 1)

    def test_the_program_gives_zero_with_data(self):
        settlements = []
        candles = []
        for i in range(20):
            settlements.append({"ticker": f"M{i}", "event_ticker": f"E{i}",
                                "result_binary": str(i % 2), "close_ts": CLOSE_TS})
            candles.append({"ticker": f"M{i}", "end_period_ts": CLOSE_TS - 7 * HOUR,
                            "yes_bid_close": "0.50", "yes_ask_close": "0.50"})
        write_csv(os.path.join(self.folder.name, "settlements_S.csv"),
                  ["ticker", "event_ticker", "result_binary", "close_ts"],
                  settlements)
        write_csv(os.path.join(self.folder.name, "candles_S.csv"),
                  ["ticker", "end_period_ts", "yes_bid_close", "yes_ask_close"],
                  candles)
        self.assertEqual(cal.main(["--data-dir", self.folder.name,
                                   "--series", "S"]), 0)


if __name__ == "__main__":
    unittest.main()
