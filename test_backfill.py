#!/usr/bin/env python3
"""Tests for the program that collects the past data.

The tests use a local fake server. This server answers with the same shape as
the real Kalshi API. The shape comes from a real answer of 2026-09-01.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import csv
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

import backfill as bf

MARKET = {
    "ticker": "KXHIGHNY-26SEP01-T90",
    "event_ticker": "KXHIGHNY-26SEP01",
    "open_time": "2026-08-31T14:00:00Z",
    "close_time": "2026-09-02T05:00:00Z",
    "status": "active",
}

CANDLE = {
    "end_period_ts": 1788184860,
    "open_interest_fp": "0.00",
    "price": {},
    "volume_fp": "0.00",
    "yes_ask": {"close_dollars": "0.0300", "high_dollars": "1.0000",
                "low_dollars": "0.0300", "open_dollars": "1.0000"},
    "yes_bid": {"close_dollars": "0.0200", "high_dollars": "0.0200",
                "low_dollars": "0.0100", "open_dollars": "0.0100"},
}

TRADE = {
    "count_fp": "155.00",
    "created_time": "2026-09-01T01:46:15.690388Z",
    "is_block_trade": False,
    "no_price_dollars": "0.9900",
    "taker_book_side": "bid",
    "taker_outcome_side": "yes",
    "ticker": "KXHIGHNY-26SEP01-T90",
    "trade_id": "072132b7-bf49-830e-4134-143b779b2c81",
    "yes_price_dollars": "0.0100",
}


class _Handler(BaseHTTPRequestHandler):
    """Answer like the real API. The path decides the answer."""

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append((parsed.path, query))
        body = self.server.responder(parsed.path, query)
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """Stop the log of the server."""


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
        return f"http://{host}:{port}/trade-api/v2"

    @property
    def requests(self):
        return self.server.requests

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


def default_responder(path, _query):
    """Give one market, one candlestick and one trade."""
    if path.endswith("/markets/trades"):
        return {"trades": [TRADE], "cursor": ""}
    if path.endswith("/candlesticks"):
        return {"candlesticks": [CANDLE]}
    if path.endswith("/markets"):
        return {"markets": [MARKET], "cursor": ""}
    return {}


class BackfillTestCase(unittest.TestCase):
    """The common start of each test."""

    def setUp(self):
        self.api = None
        # The program writes to the screen. The tests stop this text.
        patcher = mock.patch.object(bf, "_log")
        patcher.start()
        self.addCleanup(patcher.stop)
        # The tests must be quick. They use no pause between two requests.
        pause = mock.patch.object(bf, "PAUSE_BETWEEN_REQUESTS", 0)
        pause.start()
        self.addCleanup(pause.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.addCleanup(self.stop_api)
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"

    def stop_api(self):
        if self.api is not None:
            self.api.stop()

    def start_api(self, responder=default_responder):
        self.api = FakeApi(responder)
        bf.BASE_URL = self.api.url
        return self.api

    def read_csv(self, name):
        path = os.path.join(self.data_dir, name)
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def run_main(self, extra=None):
        argv = ["--data-dir", self.data_dir, "--base-url", self.api.url]
        return bf.main(argv + (extra or []))


class TestTime(unittest.TestCase):
    """Tests for the calculation of the time."""

    def test_the_program_reads_a_time_of_the_api(self):
        self.assertEqual(bf.to_epoch_seconds("2026-08-31T14:00:00Z"), 1788184800)

    def test_a_bad_time_gives_none(self):
        self.assertIsNone(bf.to_epoch_seconds("not a time"))
        self.assertIsNone(bf.to_epoch_seconds(""))
        self.assertIsNone(bf.to_epoch_seconds(None))

    def test_the_program_writes_a_time_in_the_utc_zone(self):
        self.assertEqual(bf.to_utc_text(1788184860), "2026-08-31T14:01:00Z")

    def test_a_short_time_makes_one_part(self):
        parts = bf.time_parts(1000, 2000, 1)
        self.assertEqual(parts, [(1000, 2000)])

    def test_a_long_time_makes_more_parts(self):
        size = bf.MAX_PERIODS_IN_ONE_REQUEST * 60
        parts = bf.time_parts(0, size * 2 + 100, 1)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], (0, size))
        self.assertEqual(parts[-1][1], size * 2 + 100)


class TestRows(unittest.TestCase):
    """Tests for one line of a file."""

    def test_the_program_reads_the_two_legs_of_a_candlestick(self):
        row = bf.candle_row(MARKET, CANDLE)
        self.assertEqual(row["ticker"], "KXHIGHNY-26SEP01-T90")
        self.assertEqual(row["end_period_utc"], "2026-08-31T14:01:00Z")
        self.assertEqual(row["yes_bid_open"], "0.0100")
        self.assertEqual(row["yes_bid_close"], "0.0200")
        self.assertEqual(row["yes_ask_open"], "1.0000")
        self.assertEqual(row["yes_ask_close"], "0.0300")
        self.assertEqual(row["volume"], "0.00")

    def test_an_empty_price_becomes_an_empty_cell(self):
        row = bf.candle_row(MARKET, CANDLE)
        self.assertEqual(row["price_open"], "")
        self.assertEqual(row["price_close"], "")

    def test_the_program_reads_each_field_of_a_trade(self):
        row = bf.trade_row(TRADE)
        self.assertEqual(row["trade_id"], TRADE["trade_id"])
        self.assertEqual(row["yes_price_dollars"], "0.0100")
        self.assertEqual(row["count_fp"], "155.00")
        self.assertEqual(row["taker_outcome_side"], "yes")


class TestRequests(BackfillTestCase):
    """Tests for the requests to the API."""

    def test_the_program_reads_all_pages_of_the_markets(self):
        def responder(path, query):
            if path.endswith("/markets"):
                if query.get("cursor") == ["PAGE2"]:
                    other = dict(MARKET, ticker="KXHIGHNY-26SEP01-T85")
                    return {"markets": [other], "cursor": ""}
                return {"markets": [MARKET], "cursor": "PAGE2"}
            return {}

        self.start_api(responder)
        markets = bf.fetch_markets(bf.make_session(), "KXHIGHNY")
        self.assertEqual(len(markets), 2)

    def test_the_program_reads_all_pages_of_the_trades(self):
        def responder(path, query):
            if path.endswith("/markets/trades"):
                if query.get("cursor") == ["PAGE2"]:
                    return {"trades": [dict(TRADE, trade_id="second")], "cursor": ""}
                return {"trades": [TRADE], "cursor": "PAGE2"}
            return {}

        self.start_api(responder)
        rows = bf.fetch_trades(bf.make_session(), "KXHIGHNY-26SEP01-T90")
        self.assertEqual([row["trade_id"] for row in rows],
                         [TRADE["trade_id"], "second"])

    def test_the_program_divides_a_long_time_into_parts(self):
        self.start_api()
        size = bf.MAX_PERIODS_IN_ONE_REQUEST * 60
        bf.fetch_candles(bf.make_session(), "KXHIGHNY", MARKET, 0, size * 2, 1)
        candle_calls = [q for path, q in self.api.requests
                        if path.endswith("/candlesticks")]
        self.assertEqual(len(candle_calls), 2)
        self.assertEqual(candle_calls[0]["period_interval"], ["1"])

    def test_the_program_sends_the_ticker_of_the_market(self):
        self.start_api()
        bf.fetch_trades(bf.make_session(), "KXHIGHNY-26SEP01-T90", 100, 200)
        _path, query = self.api.requests[0]
        self.assertEqual(query["ticker"], ["KXHIGHNY-26SEP01-T90"])
        self.assertEqual(query["min_ts"], ["100"])
        self.assertEqual(query["max_ts"], ["200"])


class TestFiles(BackfillTestCase):
    """Tests for the two CSV files."""

    def test_the_program_writes_the_two_files(self):
        self.start_api()
        self.assertEqual(self.run_main(), 0)
        candles = self.read_csv("candles_KXHIGHNY.csv")
        trades = self.read_csv("trades_KXHIGHNY.csv")
        self.assertEqual(len(candles), 1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(candles[0]["yes_bid_close"], "0.0200")
        self.assertEqual(trades[0]["trade_id"], TRADE["trade_id"])

    def test_the_second_run_makes_no_copy_of_a_line(self):
        self.start_api()
        self.run_main()
        self.run_main()
        self.assertEqual(len(self.read_csv("candles_KXHIGHNY.csv")), 1)
        self.assertEqual(len(self.read_csv("trades_KXHIGHNY.csv")), 1)

    def test_the_second_run_adds_a_new_line(self):
        state = {"n": 0}

        def responder(path, query):
            if path.endswith("/markets/trades"):
                state["n"] += 1
                trade = dict(TRADE, trade_id="trade-%d" % state["n"])
                return {"trades": [trade], "cursor": ""}
            return default_responder(path, query)

        self.start_api(responder)
        self.run_main(["--what", "trades"])
        self.run_main(["--what", "trades"])
        rows = self.read_csv("trades_KXHIGHNY.csv")
        self.assertEqual([row["trade_id"] for row in rows], ["trade-1", "trade-2"])

    def test_the_program_collects_two_series(self):
        def responder(path, query):
            if path.endswith("/markets/trades"):
                series = (query.get("ticker") or ["X"])[0].split("-")[0]
                return {"trades": [dict(TRADE, trade_id="id-" + series)], "cursor": ""}
            if path.endswith("/markets"):
                series = (query.get("series_ticker") or ["KXHIGHNY"])[0]
                return {"markets": [dict(MARKET, ticker=series + "-26SEP01-T90")],
                        "cursor": ""}
            return default_responder(path, query)

        self.start_api(responder)
        self.run_main(["--series", "KXHIGHNY", "KXHIGHCHI", "--what", "trades"])
        names = sorted(os.listdir(self.data_dir))
        self.assertEqual(names, ["trades_KXHIGHCHI.csv", "trades_KXHIGHNY.csv"])

    def test_the_option_what_selects_the_data(self):
        self.start_api()
        self.run_main(["--what", "candles"])
        self.assertEqual(sorted(os.listdir(self.data_dir)), ["candles_KXHIGHNY.csv"])

    def test_no_market_gives_an_error(self):
        self.start_api(lambda path, query: {"markets": [], "cursor": ""})
        self.assertEqual(self.run_main(), 1)


class TestOptions(unittest.TestCase):
    """Tests for the options on the command line."""

    def test_the_default_series_is_kxhighny(self):
        args = bf.parse_args([])
        self.assertEqual(args.series, ["KXHIGHNY"])
        self.assertEqual(args.what, "both")
        self.assertEqual(args.interval, 1)

    def test_an_interval_of_zero_is_not_permitted(self):
        import contextlib
        from io import StringIO
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            bf.parse_args(["--interval", "0"])

    def test_a_bad_value_for_what_is_not_permitted(self):
        import contextlib
        from io import StringIO
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            bf.parse_args(["--what", "everything"])


if __name__ == "__main__":
    unittest.main()
