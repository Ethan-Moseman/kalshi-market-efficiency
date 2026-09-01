#!/usr/bin/env python3
"""Tests for the program that reads the CSV files.

The tests make a small CSV file. Then they examine the result of the program.
The tests need no connection to the internet.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import csv
import os
import tempfile
import unittest
from io import StringIO
from unittest import mock

import read_data as rd

FIELDS = ["recv_ts_ns", "rtt_ms", "ticker", "event_ticker", "yes_bid", "yes_ask",
          "no_bid", "no_ask", "volume", "open_interest"]

ONE_SECOND = 1_000_000_000
START = 1_788_000_000 * ONE_SECOND


def make_line(second, ticker="T90", yes_bid="0.0100", yes_ask="0.0300",
              volume="2000.00", rtt_ms="150.000"):
    """Make one line of a CSV file."""
    return {
        "recv_ts_ns": START + second * ONE_SECOND,
        "rtt_ms": rtt_ms,
        "ticker": ticker,
        "event_ticker": "KXHIGHNY-26SEP01",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": "0.9700",
        "no_ask": "0.9900",
        "volume": volume,
        "open_interest": "1996.00",
    }


class ReaderTestCase(unittest.TestCase):
    """The common start of each test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data_dir)

    def write_file(self, lines, name="KXHIGHNY_2026-09-01.csv"):
        """Write the lines to one CSV file. Return the path."""
        path = os.path.join(self.data_dir, name)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(lines)
        return path

    def run_main(self, argv):
        """Run the program. Return the code and the text of the screen."""
        out = StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", StringIO()):
            code = rd.main(argv)
        return code, out.getvalue()


class TestFiles(ReaderTestCase):
    """Tests for the search of the files."""

    def test_the_program_finds_all_files(self):
        self.write_file([make_line(0)], "KXHIGHNY_2026-09-01.csv")
        self.write_file([make_line(0)], "KXHIGHCHI_2026-09-02.csv")
        self.assertEqual(len(rd.find_files(self.data_dir)), 2)

    def test_the_option_series_selects_one_series(self):
        self.write_file([make_line(0)], "KXHIGHNY_2026-09-01.csv")
        self.write_file([make_line(0)], "KXHIGHCHI_2026-09-02.csv")
        paths = rd.find_files(self.data_dir, series="KXHIGHNY")
        self.assertEqual(len(paths), 1)
        self.assertIn("KXHIGHNY", paths[0])

    def test_the_option_date_selects_one_day(self):
        self.write_file([make_line(0)], "KXHIGHNY_2026-09-01.csv")
        self.write_file([make_line(0)], "KXHIGHNY_2026-09-02.csv")
        paths = rd.find_files(self.data_dir, date="2026-09-02")
        self.assertEqual(len(paths), 1)
        self.assertIn("2026-09-02", paths[0])

    def test_an_empty_folder_gives_an_error(self):
        code, _text = self.run_main(["--data-dir", self.data_dir])
        self.assertEqual(code, 1)


class TestNumbers(ReaderTestCase):
    """Tests for the calculation of the values."""

    def test_an_empty_cell_becomes_none(self):
        self.assertIsNone(rd.to_float(""))
        self.assertIsNone(rd.to_float(None))
        self.assertIsNone(rd.to_float("not a number"))
        self.assertEqual(rd.to_float("0.0100"), 0.01)

    def test_the_program_calculates_the_mean_spread(self):
        lines = [make_line(0, yes_bid="0.0000", yes_ask="0.0200"),
                 make_line(60, yes_bid="0.0000", yes_ask="0.0400")]
        summary = rd.summarize("T90", lines)
        self.assertAlmostEqual(summary["mean_spread"], 0.03)
        self.assertAlmostEqual(summary["mean_middle"], 0.015)

    def test_the_program_calculates_the_changes_for_each_hour(self):
        lines = [make_line(second) for second in (0, 1800, 3600)]
        summary = rd.summarize("T90", lines)
        self.assertEqual(summary["lines"], 3)
        self.assertAlmostEqual(summary["changes_per_hour"], 2.0)

    def test_one_line_alone_gives_no_value_for_each_hour(self):
        summary = rd.summarize("T90", [make_line(0)])
        self.assertIsNone(summary["changes_per_hour"])

    def test_an_empty_price_does_not_stop_the_program(self):
        lines = [make_line(0, yes_bid="", yes_ask=""),
                 make_line(60, yes_bid="0.0000", yes_ask="0.0200")]
        summary = rd.summarize("T90", lines)
        self.assertAlmostEqual(summary["mean_spread"], 0.02)

    def test_the_program_uses_the_median_of_the_round_trip_time(self):
        lines = [make_line(0, rtt_ms="100.000"), make_line(1, rtt_ms="200.000"),
                 make_line(2, rtt_ms="300.000")]
        self.assertAlmostEqual(rd.summarize("T90", lines)["median_rtt_ms"], 200.0)


class TestGroups(ReaderTestCase):
    """Tests for the group of each market."""

    def test_the_program_puts_each_market_in_one_group(self):
        lines = [make_line(0, ticker="T90"), make_line(1, ticker="T85"),
                 make_line(2, ticker="T90")]
        groups = rd.group_by_ticker([{k: str(v) for k, v in line.items()}
                                     for line in lines])
        self.assertEqual(sorted(groups), ["T85", "T90"])
        self.assertEqual(len(groups["T90"]), 2)

    def test_the_program_sorts_each_group_by_the_time(self):
        lines = [make_line(9, ticker="T90"), make_line(1, ticker="T90")]
        groups = rd.group_by_ticker([{k: str(v) for k, v in line.items()}
                                     for line in lines])
        times = [int(line["recv_ts_ns"]) for line in groups["T90"]]
        self.assertEqual(times, sorted(times))


class TestOutput(ReaderTestCase):
    """Tests for the text on the screen."""

    def test_the_table_contains_one_line_for_each_market(self):
        self.write_file([make_line(0, ticker="T90"), make_line(60, ticker="T90"),
                         make_line(0, ticker="T85")])
        code, text = self.run_main(["--data-dir", self.data_dir])
        self.assertEqual(code, 0)
        self.assertIn("T90", text)
        self.assertIn("T85", text)
        self.assertIn("markets  : 2", text)
        self.assertIn("lines    : 3", text)

    def test_the_option_ticker_prints_each_line(self):
        self.write_file([make_line(0, ticker="T90"), make_line(60, ticker="T90"),
                         make_line(0, ticker="T85")])
        code, text = self.run_main(["--data-dir", self.data_dir, "--ticker", "T90"])
        self.assertEqual(code, 0)
        self.assertIn("lines: 2", text)
        self.assertNotIn("T85", text)

    def test_an_unknown_ticker_gives_an_error(self):
        self.write_file([make_line(0, ticker="T90")])
        code, _text = self.run_main(["--data-dir", self.data_dir, "--ticker", "NO"])
        self.assertEqual(code, 1)

    def test_a_file_with_a_header_only_gives_an_error(self):
        self.write_file([])
        code, _text = self.run_main(["--data-dir", self.data_dir])
        self.assertEqual(code, 1)

    def test_an_absent_value_becomes_a_dash(self):
        self.assertEqual(rd.show(None, 4, 6).strip(), "-")
        self.assertEqual(rd.show(0.015, 4, 6).strip(), "0.0150")


if __name__ == "__main__":
    unittest.main()
