#!/usr/bin/env python3
"""Tests for the program that makes the HTML report.

The tests make a small CSV file. Then they examine the report.
The tests need no connection to the internet.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import csv
import os
import tempfile
import time
import unittest
from io import StringIO
from unittest import mock

import make_report as mr
import read_data as rd

FIELDS = ["recv_ts_ns", "rtt_ms", "ticker", "event_ticker", "yes_bid", "yes_ask",
          "no_bid", "no_ask", "volume", "open_interest"]

ONE_SECOND = 1_000_000_000


def make_line(second, ticker, yes_bid, yes_ask, volume="2000.00"):
    """Make one line of a file of the collector."""
    return {
        "recv_ts_ns": int(time.time_ns() + second * ONE_SECOND),
        "rtt_ms": "150.000",
        "ticker": ticker,
        "event_ticker": "KXHIGHNY-26SEP01",
        "yes_bid": "%.4f" % yes_bid,
        "yes_ask": "%.4f" % yes_ask,
        "no_bid": "%.4f" % (1 - yes_ask),
        "no_ask": "%.4f" % (1 - yes_bid),
        "volume": volume,
        "open_interest": "1996.00",
    }


def as_text(lines):
    """Change each value into a text. A file gives only texts."""
    return [{k: str(v) for k, v in line.items()} for line in lines]


class TestStrike(unittest.TestCase):
    """Tests for the strike of a ticker."""

    def test_the_program_reads_the_strike(self):
        self.assertEqual(mr.strike_of("KXHIGHNY-26SEP01-T90"), ("T", 90.0))
        self.assertEqual(mr.strike_of("KXHIGHNY-26SEP01-B82.5"), ("B", 82.5))

    def test_a_ticker_without_a_strike_gives_none(self):
        self.assertIsNone(mr.strike_of("KXHIGHNY-26SEP01"))
        self.assertIsNone(mr.strike_of(""))
        self.assertIsNone(mr.strike_of(None))


class TestLadder(unittest.TestCase):
    """Tests for the ladder test. This test finds an arbitrage."""

    def test_a_correct_ladder_gives_no_window(self):
        lines = as_text([
            make_line(0, "E-T85", 0.40, 0.42),
            make_line(1, "E-T90", 0.20, 0.22),
            make_line(2, "E-T95", 0.05, 0.07),
        ])
        self.assertEqual(mr.ladder_violations(lines), [])

    def test_a_higher_strike_with_a_higher_price_gives_a_window(self):
        lines = as_text([
            make_line(0, "E-T85", 0.40, 0.42),
            make_line(1, "E-T90", 0.50, 0.52),
        ])
        found = mr.ladder_violations(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["low"], "E-T85")
        self.assertEqual(found[0]["high"], "E-T90")
        self.assertAlmostEqual(found[0]["edge"], 0.08)

    def test_the_window_ends_after_a_correction(self):
        lines = as_text([
            make_line(0, "E-T85", 0.40, 0.42),
            make_line(1, "E-T90", 0.50, 0.52),
            make_line(2, "E-T90", 0.51, 0.53),
            make_line(3, "E-T90", 0.20, 0.22),
        ])
        found = mr.ladder_violations(lines)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["edge"], 0.09)
        self.assertGreater(found[0]["end"], found[0]["start"])

    def test_two_events_stay_separate(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.42),
                         make_line(1, "E-T90", 0.50, 0.52)])
        for line in lines:
            if line["ticker"] == "E-T90":
                line["event_ticker"] = "OTHER-EVENT"
        self.assertEqual(mr.ladder_violations(lines), [])

    def test_a_market_without_a_strike_makes_no_error(self):
        lines = as_text([make_line(0, "NO-STRIKE-HERE", 0.40, 0.42)])
        self.assertEqual(mr.ladder_violations(lines), [])


class TestNumbers(unittest.TestCase):
    """Tests for the values of the page."""

    def test_the_program_makes_a_short_text_for_a_price(self):
        self.assertEqual(mr.price_label(0.0100), "0.01")
        self.assertEqual(mr.price_label(0.1250), "0.125")
        self.assertEqual(mr.price_label(0.0), "0")

    def test_few_different_spreads_give_one_group_for_each(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.41),
                         make_line(1, "E-T85", 0.40, 0.42),
                         make_line(2, "E-T85", 0.40, 0.41)])
        labels, counts = mr.spread_histogram(lines)
        self.assertEqual(labels, ["1.0", "2.0"])
        self.assertEqual(counts, [2, 1])

    def test_many_different_spreads_give_groups_of_an_equal_size(self):
        lines = as_text([make_line(i, "E-T85", 0.10, 0.10 + i / 100.0)
                         for i in range(1, 30)])
        labels, counts = mr.spread_histogram(lines)
        self.assertEqual(len(labels), 12)
        self.assertEqual(sum(counts), 29)

    def test_an_empty_price_is_not_in_the_series(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.42)])
        lines.append(dict(lines[0], yes_bid="", yes_ask=""))
        self.assertEqual(len(mr.quote_series(lines)), 1)

    def test_the_time_of_one_day_has_no_date(self):
        now = time.time_ns()
        first, last = mr.time_range([now, now + 60 * ONE_SECOND])
        self.assertEqual(len(first), len("00:00:00"))
        self.assertEqual(len(last), len("00:00:00"))

    def test_the_time_of_two_days_has_a_date(self):
        now = time.time_ns()
        first, _last = mr.time_range([now, now + 3 * 86400 * ONE_SECOND])
        self.assertIn("-", first)

    def test_the_program_counts_the_changes_for_each_hour(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.42)])
        counts = mr.hour_counts(lines)
        self.assertEqual(len(counts), 24)
        self.assertEqual(sum(counts), 1)


class TestDeskNumbers(unittest.TestCase):
    """Tests for the measurements of a trading desk."""

    def test_the_program_makes_a_text_in_cents(self):
        self.assertEqual(mr.cents(0.0100), "1.0")
        self.assertEqual(mr.cents(0.3450), "34.5")
        self.assertEqual(mr.cents(0.0125, 2), "1.25")
        self.assertEqual(mr.cents(None), "–")

    def test_the_program_makes_a_text_in_basis_points(self):
        # A spread of 2 cents on a price of 50 cents is 400 basis points.
        self.assertEqual(mr.basis_points(0.02, 0.50), "400")
        self.assertEqual(mr.basis_points(None, 0.50), "–")
        self.assertEqual(mr.basis_points(0.02, 0), "–")

    def test_the_program_makes_a_short_text_for_a_time(self):
        self.assertEqual(mr.duration(45), "45s")
        self.assertEqual(mr.duration(125), "2m 05s")
        self.assertEqual(mr.duration(7325), "2h 02m")
        self.assertIsNotNone(mr.duration(0))

    def test_the_program_calculates_a_percentile(self):
        values = list(range(1, 101))
        self.assertEqual(mr.percentile(values, 0.50), 51)
        self.assertEqual(mr.percentile(values, 0.95), 95)
        self.assertEqual(mr.percentile(values, 0.99), 99)
        self.assertEqual(mr.percentile([7], 0.95), 7)
        self.assertIsNone(mr.percentile([], 0.5))

    def test_the_spread_gets_a_weight_of_the_time(self):
        # The spread of 1 cent holds for 100 seconds. The spread of 5 cents
        # holds for 1 second. The mean with a weight of the time is near 1 cent.
        lines = as_text([make_line(0, "E-T85", 0.40, 0.41),
                         make_line(100, "E-T85", 0.40, 0.45)])
        end = int(lines[-1]["recv_ts_ns"]) + ONE_SECOND
        item = mr.market_metrics("E-T85", lines, end)
        self.assertAlmostEqual(item["mean_spread"], 0.03, places=4)
        self.assertLess(item["time_spread"], 0.011)

    def test_the_program_finds_the_longest_quiet_time(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.41),
                         make_line(5, "E-T85", 0.40, 0.42),
                         make_line(605, "E-T85", 0.40, 0.43)])
        longest, long_gaps = mr.quiet_times(lines)
        self.assertAlmostEqual(longest, 600, places=0)
        self.assertEqual(long_gaps, 1)

    def test_the_program_divides_the_session_into_parts(self):
        lines = as_text([make_line(i, "E-T85", 0.40, 0.41) for i in range(0, 100, 5)])
        labels, counts = mr.session_activity(lines, parts=12)
        self.assertEqual(len(counts), 12)
        self.assertEqual(sum(counts), 20)

    def test_the_interval_between_two_updates(self):
        lines = as_text([make_line(0, "E-T85", 0.40, 0.41),
                         make_line(10, "E-T85", 0.40, 0.42),
                         make_line(30, "E-T85", 0.40, 0.43)])
        item = mr.market_metrics("E-T85", lines, int(lines[-1]["recv_ts_ns"]))
        self.assertAlmostEqual(item["gap_p50"], 20, places=0)
        self.assertEqual(item["updates"], 3)


class TestPage(unittest.TestCase):
    """Tests for the file report.html."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data_dir)
        self.out = os.path.join(self.tmp.name, "report.html")

    def write_file(self, lines):
        path = os.path.join(self.data_dir, "KXHIGHNY_2026-09-01.csv")
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(lines)

    def run_main(self, extra=None):
        with mock.patch("sys.stdout", StringIO()), mock.patch("sys.stderr", StringIO()):
            return mr.main(["--data-dir", self.data_dir, "--out", self.out]
                           + (extra or []))

    def test_the_program_writes_the_file(self):
        self.write_file([make_line(0, "E-T85", 0.40, 0.42),
                         make_line(1, "E-T85", 0.41, 0.43)])
        self.assertEqual(self.run_main(), 0)
        page = open(self.out, encoding="utf-8").read()
        self.assertIn("Kalshi market efficiency", page)
        self.assertIn("E-T85", page)
        self.assertIn("<svg", page)

    def test_a_correct_ladder_gives_a_good_message(self):
        self.write_file([make_line(0, "E-T85", 0.40, 0.42),
                         make_line(1, "E-T90", 0.20, 0.22)])
        self.run_main()
        self.assertIn("No violation", open(self.out, encoding="utf-8").read())

    def test_an_arbitrage_is_in_the_page(self):
        self.write_file([make_line(0, "E-T85", 0.40, 0.42),
                         make_line(1, "E-T90", 0.50, 0.52)])
        self.run_main()
        page = open(self.out, encoding="utf-8").read()
        self.assertIn("arbitrage window(s)", page)
        self.assertIn("8.00", page)

    def test_an_empty_folder_gives_an_error(self):
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(os.path.exists(self.out))

    def test_the_page_counts_the_lines_of_the_past_data(self):
        self.write_file([make_line(0, "E-T85", 0.40, 0.42)])
        history = os.path.join(self.data_dir, "history")
        os.makedirs(history)
        with open(os.path.join(history, "candles_KXHIGHNY.csv"), "w",
                  encoding="utf-8") as handle:
            handle.write("ticker,end_period_ts\nA,1\nB,2\n")
        self.assertEqual(mr.count_history(self.data_dir), 2)
        self.run_main()
        self.assertIn("Rows of past data", open(self.out, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
