#!/usr/bin/env python3
"""Tests for the collector program. The tests use a local fake API.

The tests need no connection to the internet. They start a small HTTP server on
the local computer. This server answers like the Kalshi API.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import contextlib
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests

import kalshi_collector as kc

# The fake server runs on the local computer. The program must not use a proxy
# for this address.
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"


def make_live_market(ticker="KXHIGHNY-26SEP01-T90", yes_bid="0.0000",
                     yes_ask="0.0100", no_bid="0.9900", no_ask="1.0000",
                     volume="2375.00", open_interest="1996.00"):
    """Make one market with the names of the live API.

    The live API sends the price as a text in dollars. The name of the field has
    the ending "_dollars". The counters have the ending "_fp".
    """
    return {
        "ticker": ticker,
        "event_ticker": "KXHIGHNY-26SEP01",
        "yes_bid_dollars": yes_bid,
        "yes_ask_dollars": yes_ask,
        "no_bid_dollars": no_bid,
        "no_ask_dollars": no_ask,
        "volume_fp": volume,
        "open_interest_fp": open_interest,
        "status": "active",
        "market_type": "binary",
    }


def make_market(ticker="KXHIGHNY-26SEP01-B80", yes_bid=40, yes_ask=44, no_bid=56,
                no_ask=60, volume=100, open_interest=50):
    """Make one market with the short names of the older documents."""
    return {
        "ticker": ticker,
        "event_ticker": "KXHIGHNY-26SEP01",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "volume": volume,
        "open_interest": open_interest,
    }


class _Handler(BaseHTTPRequestHandler):
    """Answer one request. The server gets the answer from its responder."""

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        self.server.requests.append(query)
        status, body = self.server.responder(query)
        if status is None:
            # The server closes the connection. It sends no answer. The client
            # then sees the error "Connection reset by peer".
            self.close_connection = True
            return
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """Stop the log of the server. The log makes the test output difficult."""


class FakeApi:
    """A small HTTP server. It answers like the Kalshi API."""

    def __init__(self, responder):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.responder = responder
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}/trade-api/v2/markets"

    @property
    def requests(self):
        return self.server.requests

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class CollectorTestCase(unittest.TestCase):
    """The common start and end of each test."""

    def setUp(self):
        self.api = None
        kc._stop = False
        # The log of the collector makes the output of the tests difficult to
        # read. Each test stops this log.
        patcher = mock.patch.object(kc, "_log")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.stop_api)

    def stop_api(self):
        if self.api is not None:
            self.api.stop()

    def start_api(self, responder):
        """Start the fake API. Then tell the program to use this address."""
        self.api = FakeApi(responder)
        kc.API_URL = self.api.url
        return self.api

    def read_csv(self, name=None):
        """Read the lines of one CSV file. Return a list of lists."""
        if name is None:
            name = sorted(os.listdir(self.data_dir))[0]
        path = os.path.join(self.data_dir, name)
        with open(path, encoding="utf-8") as handle:
            return [line.rstrip("\n").split(",") for line in handle if line.strip()]


class TestRequest(CollectorTestCase):
    """Tests for the request to the API."""

    def test_the_program_sends_the_correct_parameters(self):
        self.start_api(lambda q: (200, {"markets": [make_market()], "cursor": ""}))
        kc.fetch_markets(kc.make_session(), "KXHIGHNY")
        query = self.api.requests[0]
        self.assertEqual(query["series_ticker"], ["KXHIGHNY"])
        self.assertEqual(query["status"], ["open"])
        self.assertEqual(query["limit"], ["200"])

    def test_the_program_reads_all_pages(self):
        def responder(query):
            if query.get("cursor") == ["PAGE2"]:
                return 200, {"markets": [make_market(ticker="B82")], "cursor": ""}
            return 200, {"markets": [make_market(ticker="B80")], "cursor": "PAGE2"}

        self.start_api(responder)
        snapshots = kc.fetch_markets(kc.make_session(), "KXHIGHNY")
        tickers = [market["ticker"] for market, _ts, _rtt in snapshots]
        self.assertEqual(tickers, ["B80", "B82"])

    def test_each_page_keeps_its_own_time(self):
        def responder(query):
            if query.get("cursor") == ["PAGE2"]:
                return 200, {"markets": [make_market(ticker="B82")], "cursor": ""}
            return 200, {"markets": [make_market(ticker="B80")], "cursor": "PAGE2"}

        self.start_api(responder)
        snapshots = kc.fetch_markets(kc.make_session(), "KXHIGHNY")
        first_time = snapshots[0][1]
        second_time = snapshots[1][1]
        self.assertNotEqual(first_time, second_time)
        for _market, recv_ts_ns, rtt_ms in snapshots:
            self.assertGreater(recv_ts_ns, 1_600_000_000_000_000_000)
            self.assertGreater(rtt_ms, 0.0)

    def test_the_program_makes_a_broken_request_again(self):
        state = {"n": 0}

        def responder(_query):
            state["n"] += 1
            if state["n"] == 1:
                return None, None
            return 200, {"markets": [make_live_market()], "cursor": ""}

        self.start_api(responder)
        snapshots = kc.fetch_markets(kc.make_session(), "KXHIGHNY")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(state["n"], 2)

    def test_the_program_stops_after_the_last_attempt(self):
        self.start_api(lambda q: (None, None))
        with self.assertRaises(requests.ConnectionError):
            kc.fetch_markets(kc.make_session(), "KXHIGHNY")

    def test_an_error_code_makes_an_exception(self):
        self.start_api(lambda q: (500, {"error": "server error"}))
        with self.assertRaises(requests.HTTPError):
            kc.fetch_markets(kc.make_session(), "KXHIGHNY")


class TestChangeDetection(CollectorTestCase):
    """Tests for the rule that decides when the program writes a line."""

    def collect(self, answers):
        """Poll one time for each answer in the list. Return the CSV lines."""
        state = {"n": 0}

        def responder(_query):
            markets = answers[min(state["n"], len(answers) - 1)]
            state["n"] += 1
            return 200, {"markets": markets, "cursor": ""}

        self.start_api(responder)
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        session = kc.make_session()
        for _ in answers:
            collector.poll(session)
        collector.close()
        return self.read_csv()

    def test_the_first_sight_of_a_market_makes_one_line(self):
        lines = self.collect([[make_market()]])
        self.assertEqual(lines[0], kc.CSV_FIELDS)
        self.assertEqual(len(lines), 2)

    def test_the_same_quote_makes_no_new_line(self):
        market = make_market()
        lines = self.collect([[market], [market], [market]])
        self.assertEqual(len(lines), 2)

    def test_a_change_of_the_quote_makes_a_new_line(self):
        lines = self.collect([
            [make_market(yes_bid=40)],
            [make_market(yes_bid=41)],
            [make_market(yes_bid=41)],
            [make_market(yes_bid=42)],
        ])
        self.assertEqual(len(lines), 4)
        column = kc.CSV_FIELDS.index("yes_bid")
        self.assertEqual([line[column] for line in lines[1:]], ["40", "41", "42"])

    def test_a_change_of_the_volume_alone_makes_no_new_line(self):
        lines = self.collect([
            [make_market(volume=100)],
            [make_market(volume=200)],
            [make_market(volume=300, open_interest=999)],
        ])
        self.assertEqual(len(lines), 2)

    def test_each_of_the_four_prices_makes_a_new_line(self):
        for field in kc.QUOTE_FIELDS:
            with self.subTest(field=field):
                first = make_market()
                second = make_market(**{field: first[field] + 1})
                state = {"n": 0}

                def responder(_query, first=first, second=second, state=state):
                    market = first if state["n"] == 0 else second
                    state["n"] += 1
                    return 200, {"markets": [market], "cursor": ""}

                self.stop_api()
                self.start_api(responder)
                data_dir = os.path.join(self.tmp.name, "data_" + field)
                collector = kc.SeriesCollector("KXHIGHNY", data_dir)
                session = kc.make_session()
                collector.poll(session)
                _seen, written = collector.poll(session)
                collector.close()
                self.assertEqual(written, 1)

    def test_a_market_without_a_ticker_makes_no_line(self):
        market = make_market()
        del market["ticker"]
        state = {"n": 0}

        def responder(_query):
            state["n"] += 1
            return 200, {"markets": [market], "cursor": ""}

        self.start_api(responder)
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        _seen, written = collector.poll(kc.make_session())
        collector.close()
        self.assertEqual(written, 0)


class TestCsvFile(CollectorTestCase):
    """Tests for the CSV file."""

    def test_an_empty_value_becomes_an_empty_cell(self):
        market = make_market(yes_bid=None, no_ask=None)
        self.start_api(lambda q: (200, {"markets": [market], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        collector.poll(kc.make_session())
        collector.close()
        lines = self.read_csv()
        self.assertEqual(lines[1][kc.CSV_FIELDS.index("yes_bid")], "")
        self.assertEqual(lines[1][kc.CSV_FIELDS.index("no_ask")], "")

    def test_the_values_in_the_line_come_from_the_api(self):
        market = make_market(yes_bid=1, yes_ask=2, no_bid=3, no_ask=4,
                             volume=5, open_interest=6)
        self.start_api(lambda q: (200, {"markets": [market], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        collector.poll(kc.make_session())
        collector.close()
        line = dict(zip(kc.CSV_FIELDS, self.read_csv()[1]))
        self.assertEqual(line["ticker"], market["ticker"])
        self.assertEqual(line["event_ticker"], market["event_ticker"])
        self.assertEqual(line["yes_bid"], "1")
        self.assertEqual(line["yes_ask"], "2")
        self.assertEqual(line["no_bid"], "3")
        self.assertEqual(line["no_ask"], "4")
        self.assertEqual(line["volume"], "5")
        self.assertEqual(line["open_interest"], "6")
        self.assertGreater(int(line["recv_ts_ns"]), 1_600_000_000_000_000_000)
        self.assertGreater(float(line["rtt_ms"]), 0.0)

    def test_the_name_of_the_file_contains_the_series_and_the_date(self):
        self.start_api(lambda q: (200, {"markets": [make_market()], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        collector.poll(kc.make_session())
        collector.close()
        name = os.listdir(self.data_dir)[0]
        self.assertEqual(name, "KXHIGHNY_%s.csv" % time.strftime("%Y-%m-%d"))

    def test_the_program_writes_the_header_one_time(self):
        row = kc.build_row(make_market(), 1, 2.0)
        for _ in range(2):
            writer = kc.DailyCsvWriter(self.data_dir, "KXHIGHNY")
            writer.write(row)
            writer.close()
        lines = self.read_csv()
        self.assertEqual(lines[0], kc.CSV_FIELDS)
        self.assertEqual(len(lines), 3)

    def test_the_program_opens_a_new_file_after_midnight(self):
        days = ["2026-09-01", "2026-09-01", "2026-09-02"]
        state = {"n": 0}
        real_strftime = time.strftime

        def fake_strftime(fmt, *args):
            if fmt == "%Y-%m-%d":
                day = days[min(state["n"], len(days) - 1)]
                state["n"] += 1
                return day
            return real_strftime(fmt, *args)

        row = kc.build_row(make_market(), 1, 2.0)
        writer = kc.DailyCsvWriter(self.data_dir, "KXHIGHNY")
        with mock.patch.object(kc.time, "strftime", fake_strftime):
            self.assertTrue(writer.write(row))
            self.assertFalse(writer.write(row))
            self.assertTrue(writer.write(row))
        writer.close()
        self.assertEqual(sorted(os.listdir(self.data_dir)),
                         ["KXHIGHNY_2026-09-01.csv", "KXHIGHNY_2026-09-02.csv"])
        self.assertEqual(len(self.read_csv("KXHIGHNY_2026-09-01.csv")), 3)
        self.assertEqual(len(self.read_csv("KXHIGHNY_2026-09-02.csv")), 2)


class TestLoop(CollectorTestCase):
    """Tests for the loop that polls the API."""

    def test_the_loop_continues_after_an_error(self):
        class FailingCollector:
            series_ticker = "KXHIGHNY"
            lines_written = 0
            calls = 0

            def poll(self, _session):
                FailingCollector.calls += 1
                if FailingCollector.calls >= 3:
                    kc._stop = True
                raise requests.ConnectionError("the network is not available")

        result = kc.run_collect(None, [FailingCollector()], 0.01)
        self.assertEqual(result, 0)
        self.assertEqual(FailingCollector.calls, 3)

    def test_the_loop_stops_after_a_signal(self):
        kc._handle_signal(15, None)
        result = kc.run_collect(None, [], 0.01)
        self.assertEqual(result, 0)


class TestOptions(CollectorTestCase):
    """Tests for the options on the command line."""

    def test_the_default_series_is_kxhighny(self):
        args = kc.parse_args([])
        self.assertEqual(args.series, ["KXHIGHNY"])
        self.assertEqual(args.interval, 2.0)
        self.assertEqual(args.data_dir, "data")

    def test_the_option_series_accepts_more_names(self):
        args = kc.parse_args(["--series", "KXHIGHNY", "KXHIGHCHI"])
        self.assertEqual(args.series, ["KXHIGHNY", "KXHIGHCHI"])

    def test_an_interval_of_zero_is_not_permitted(self):
        # argparse writes the usage text to stderr. The test hides this text.
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            kc.parse_args(["--interval", "0"])

    def test_the_option_inspect_prints_one_market(self):
        markets = [make_market(ticker="B80"), make_market(ticker="B82")]
        self.start_api(lambda q: (200, {"markets": markets, "cursor": ""}))
        out = StringIO()
        with mock.patch("sys.stdout", out):
            result = kc.main(["--inspect", "--api-url", self.api.url])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out.getvalue())["ticker"], "B80")

    def test_the_option_ticker_selects_one_market(self):
        markets = [make_market(ticker="B80"), make_market(ticker="B82")]
        self.start_api(lambda q: (200, {"markets": markets, "cursor": ""}))
        out = StringIO()
        with mock.patch("sys.stdout", out):
            result = kc.main(["--inspect", "--ticker", "B82", "--api-url", self.api.url])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out.getvalue())["ticker"], "B82")

    def test_an_unknown_ticker_gives_an_error(self):
        self.start_api(lambda q: (200, {"markets": [make_market()], "cursor": ""}))
        result = kc.main(["--inspect", "--ticker", "NOT-A-TICKER",
                          "--api-url", self.api.url])
        self.assertEqual(result, 1)

    def test_no_open_market_gives_an_error(self):
        self.start_api(lambda q: (200, {"markets": [], "cursor": ""}))
        result = kc.main(["--inspect", "--api-url", self.api.url])
        self.assertEqual(result, 1)


class TestLiveFieldNames(CollectorTestCase):
    """Tests for the names of the fields of the live Kalshi API."""

    def poll_once(self, market, data_dir=None):
        """Poll one time with one market. Return the collector."""
        self.start_api(lambda q: (200, {"markets": [market], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", data_dir or self.data_dir)
        collector.poll(kc.make_session())
        return collector

    def test_the_program_reads_the_price_from_the_field_dollars(self):
        collector = self.poll_once(make_live_market())
        collector.close()
        line = dict(zip(kc.CSV_FIELDS, self.read_csv()[1]))
        self.assertEqual(line["yes_bid"], "0.0000")
        self.assertEqual(line["yes_ask"], "0.0100")
        self.assertEqual(line["no_bid"], "0.9900")
        self.assertEqual(line["no_ask"], "1.0000")

    def test_the_program_reads_the_counters_from_the_field_fp(self):
        collector = self.poll_once(make_live_market())
        collector.close()
        line = dict(zip(kc.CSV_FIELDS, self.read_csv()[1]))
        self.assertEqual(line["volume"], "2375.00")
        self.assertEqual(line["open_interest"], "1996.00")
        self.assertEqual(line["ticker"], "KXHIGHNY-26SEP01-T90")
        self.assertEqual(line["event_ticker"], "KXHIGHNY-26SEP01")

    def test_a_change_of_the_price_makes_a_new_line(self):
        answers = [make_live_market(yes_bid="0.0000"),
                   make_live_market(yes_bid="0.0100"),
                   make_live_market(yes_bid="0.0100")]
        state = {"n": 0}

        def responder(_query):
            market = answers[min(state["n"], len(answers) - 1)]
            state["n"] += 1
            return 200, {"markets": [market], "cursor": ""}

        self.start_api(responder)
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        session = kc.make_session()
        counts = [collector.poll(session)[1] for _ in answers]
        collector.close()
        self.assertEqual(counts, [1, 1, 0])

    def test_a_change_of_the_volume_alone_makes_no_new_line(self):
        answers = [make_live_market(volume="2375.00"),
                   make_live_market(volume="2400.00")]
        state = {"n": 0}

        def responder(_query):
            market = answers[min(state["n"], len(answers) - 1)]
            state["n"] += 1
            return 200, {"markets": [market], "cursor": ""}

        self.start_api(responder)
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        session = kc.make_session()
        counts = [collector.poll(session)[1] for _ in answers]
        collector.close()
        self.assertEqual(counts, [1, 0])

    def test_the_long_name_has_the_first_position(self):
        market = make_live_market()
        market["yes_bid"] = 99
        self.assertEqual(kc.field_value(market, "yes_bid"), "0.0000")

    def test_the_short_name_is_still_correct(self):
        self.assertEqual(kc.field_value(make_market(yes_bid=40), "yes_bid"), 40)


class TestUnknownFieldNames(CollectorTestCase):
    """Tests for the caution about an unknown name of a field."""

    def test_a_market_without_a_price_gives_a_caution(self):
        market = {"ticker": "B80", "event_ticker": "E", "yes_bid_cents": 40}
        self.assertTrue(kc.market_has_no_price(market))
        self.start_api(lambda q: (200, {"markets": [market], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        collector.poll(kc.make_session())
        collector.close()
        messages = [call.args[0] for call in kc._log.call_args_list]
        self.assertTrue(any("CAUTION" in message for message in messages))

    def test_a_market_with_a_price_gives_no_caution(self):
        self.start_api(lambda q: (200, {"markets": [make_live_market()], "cursor": ""}))
        collector = kc.SeriesCollector("KXHIGHNY", self.data_dir)
        collector.poll(kc.make_session())
        collector.close()
        messages = [call.args[0] for call in kc._log.call_args_list]
        self.assertFalse(any("CAUTION" in message for message in messages))

    def test_the_option_inspect_gives_an_error_without_a_price(self):
        market = {"ticker": "B80", "event_ticker": "E"}
        self.start_api(lambda q: (200, {"markets": [market], "cursor": ""}))
        with mock.patch("sys.stdout", StringIO()):
            result = kc.main(["--inspect", "--api-url", self.api.url])
        self.assertEqual(result, 1)

    def test_the_option_inspect_shows_the_name_of_each_source(self):
        self.start_api(lambda q: (200, {"markets": [make_live_market()], "cursor": ""}))
        with mock.patch("sys.stdout", StringIO()):
            result = kc.main(["--inspect", "--api-url", self.api.url])
        self.assertEqual(result, 0)
        messages = " ".join(call.args[0] for call in kc._log.call_args_list)
        self.assertIn("yes_bid_dollars", messages)
        self.assertIn("open_interest_fp", messages)


if __name__ == "__main__":
    unittest.main()
