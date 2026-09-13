#!/usr/bin/env python3
"""Tests for the program that collects the result of each settled market.

The tests use a local fake server. This server answers with the same shape as
the real Kalshi API.

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
from urllib.parse import parse_qs, urlparse

import settlements as st

SETTLED_MARKET = {
    "ticker": "KXHIGHNY-26SEP01-T90",
    "event_ticker": "KXHIGHNY-26SEP01",
    "status": "settled",
    "result": "no",
    "close_time": "2026-09-02T05:00:00Z",
    "strike_type": "greater",
    "floor_strike": 90,
    "expiration_value": "86",
    "last_price_dollars": "0.0100",
    "volume_fp": "2375.00",
    "open_interest_fp": "1996.00",
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
        return f"http://{host}:{port}"

    @property
    def requests(self):
        return self.server.requests

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class SettlementsTestCase(unittest.TestCase):
    """A test with a fake server and a folder for the files."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.api = None

    def start_api(self, responder):
        self.api = FakeApi(responder)
        self.addCleanup(self.api.stop)
        return self.api

    def run_main(self, *extra):
        argv = ["--series", "KXHIGHNY", "--data-dir", self.folder.name,
                "--base-url", self.api.url] + list(extra)
        return st.main(argv)

    def read_file(self, name="settlements_KXHIGHNY.csv"):
        path = os.path.join(self.folder.name, name)
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


class TestResult(unittest.TestCase):
    """The program changes the result into a number."""

    def test_yes_becomes_one(self):
        self.assertEqual(st.result_binary("yes"), 1)

    def test_no_becomes_zero(self):
        self.assertEqual(st.result_binary("no"), 0)

    def test_the_program_accepts_a_large_letter(self):
        self.assertEqual(st.result_binary("YES"), 1)

    def test_a_market_without_an_answer_gives_an_empty_text(self):
        for value in ("", None, "void", "cancelled", "all_no_yes"):
            self.assertEqual(st.result_binary(value), "",
                             f"the value {value!r} must give an empty text")


class TestRows(unittest.TestCase):
    """The program makes one line from one market."""

    def test_the_program_reads_each_field(self):
        row = st.settlement_row(SETTLED_MARKET, "KXHIGHNY")
        self.assertEqual(row["ticker"], "KXHIGHNY-26SEP01-T90")
        self.assertEqual(row["event_ticker"], "KXHIGHNY-26SEP01")
        self.assertEqual(row["series"], "KXHIGHNY")
        self.assertEqual(row["result"], "no")
        self.assertEqual(row["result_binary"], 0)
        self.assertEqual(row["floor_strike"], 90)
        self.assertEqual(row["last_price"], "0.0100")

    def test_the_program_changes_the_close_time_into_seconds(self):
        row = st.settlement_row(SETTLED_MARKET, "KXHIGHNY")
        self.assertEqual(row["close_ts"], 1788325200)  # 2026-09-02T05:00:00Z

    def test_the_program_accepts_the_second_name_of_the_result(self):
        market = dict(SETTLED_MARKET)
        del market["result"]
        market["settlement_result"] = "yes"
        row = st.settlement_row(market, "KXHIGHNY")
        self.assertEqual(row["result_binary"], 1)

    def test_an_empty_value_becomes_an_empty_cell(self):
        row = st.settlement_row({"ticker": "T1", "close_time": "2026-09-02T05:00:00Z"},
                                "KXHIGHNY")
        self.assertEqual(row["cap_strike"], "")
        self.assertEqual(row["volume"], "")


class TestRequests(SettlementsTestCase):
    """The program sends the correct request."""

    def test_the_program_reads_all_pages(self):
        pages = [
            {"markets": [dict(SETTLED_MARKET, ticker="T1")], "cursor": "next"},
            {"markets": [dict(SETTLED_MARKET, ticker="T2")], "cursor": ""},
        ]
        state = {"index": 0}

        def responder(path, query):
            page = pages[state["index"]]
            state["index"] = min(state["index"] + 1, len(pages) - 1)
            return page

        self.start_api(responder)
        self.run_main()
        rows = self.read_file()
        self.assertEqual([r["ticker"] for r in rows], ["T1", "T2"])

    def test_the_program_asks_for_the_settled_markets(self):
        self.start_api(lambda path, query: {"markets": [SETTLED_MARKET]})
        self.run_main()
        _path, query = self.api.requests[0]
        self.assertEqual(query["status"], ["settled"])
        self.assertEqual(query["series_ticker"], ["KXHIGHNY"])

    def test_the_option_days_sets_the_time_of_the_close(self):
        self.start_api(lambda path, query: {"markets": [SETTLED_MARKET]})
        self.run_main("--days", "90")
        _path, query = self.api.requests[0]
        self.assertIn("min_close_ts", query)
        self.assertIn("max_close_ts", query)
        span = int(query["max_close_ts"][0]) - int(query["min_close_ts"][0])
        self.assertEqual(span, 90 * 24 * 3600)


class TestFiles(SettlementsTestCase):
    """The program writes the file one time for each market."""

    def test_the_program_writes_the_file(self):
        self.start_api(lambda path, query: {"markets": [SETTLED_MARKET]})
        self.assertEqual(self.run_main(), 0)
        rows = self.read_file()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result_binary"], "0")

    def test_the_second_run_makes_no_copy_of_a_line(self):
        self.start_api(lambda path, query: {"markets": [SETTLED_MARKET]})
        self.run_main()
        self.run_main()
        self.assertEqual(len(self.read_file()), 1)

    def test_the_second_run_adds_a_new_market(self):
        state = {"tickers": ["T1"]}

        def responder(path, query):
            return {"markets": [dict(SETTLED_MARKET, ticker=t)
                                for t in state["tickers"]]}

        self.start_api(responder)
        self.run_main()
        state["tickers"] = ["T1", "T2"]
        self.run_main()
        self.assertEqual([r["ticker"] for r in self.read_file()], ["T1", "T2"])

    def test_no_market_gives_an_error(self):
        self.start_api(lambda path, query: {"markets": []})
        self.assertEqual(self.run_main(), 1)


class TestInspect(SettlementsTestCase):
    """The option --inspect tests the names of the fields."""

    def test_a_good_result_gives_zero(self):
        self.start_api(lambda path, query: {"markets": [SETTLED_MARKET]})
        self.assertEqual(self.run_main("--inspect"), 0)

    def test_a_market_without_a_result_gives_an_error(self):
        market = dict(SETTLED_MARKET)
        del market["result"]
        self.start_api(lambda path, query: {"markets": [market]})
        self.assertEqual(self.run_main("--inspect"), 1)


class TestOptions(unittest.TestCase):
    """The options of the command line have the correct effect."""

    def test_the_default_status_is_settled(self):
        self.assertEqual(st.parse_args([]).status, "settled")

    def test_the_default_series_is_kxhighny(self):
        self.assertEqual(st.parse_args([]).series, ["KXHIGHNY"])

    def test_a_day_of_zero_is_not_permitted(self):
        with self.assertRaises(SystemExit):
            st.parse_args(["--days", "0"])


if __name__ == "__main__":
    unittest.main()
