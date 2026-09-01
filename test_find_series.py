#!/usr/bin/env python3
"""Tests for the program that finds the series of Kalshi.

The tests use a local fake server. They need no connection to the internet.

To run the tests, use this command:
    python3 -m unittest discover -v
"""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

import find_series as fs


def make_event(event_ticker, category="Climate and Weather", title="High temperature"):
    """Make one event. The fields are the same as the fields of the API."""
    return {"event_ticker": event_ticker, "category": category, "title": title}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append(query)
        status, body = self.server.responder(query)
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        """Stop the log of the server."""


class FakeApi:
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


WEATHER = [make_event("KXHIGHNY-26SEP01"), make_event("KXHIGHNY-26SEP02"),
           make_event("KXHIGHCHI-26SEP01")]
OTHER = [make_event("KXFED-26SEP", category="Economics", title="Rate")]


class FindSeriesTestCase(unittest.TestCase):
    def setUp(self):
        self.api = None
        patcher = mock.patch.object(fs, "_log")
        patcher.start()
        self.addCleanup(patcher.stop)
        pause = mock.patch.object(fs, "PAUSE_BETWEEN_REQUESTS", 0)
        pause.start()
        self.addCleanup(pause.stop)
        self.addCleanup(self.stop_api)
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"

    def stop_api(self):
        if self.api is not None:
            self.api.stop()

    def start_api(self, responder=None):
        if responder is None:
            responder = lambda q: (200, {"events": WEATHER + OTHER, "cursor": ""})
        self.api = FakeApi(responder)
        fs.BASE_URL = self.api.url
        return self.api

    def run_main(self, extra=None):
        out = StringIO()
        with mock.patch("sys.stdout", out):
            code = fs.main(["--base-url", self.api.url] + (extra or []))
        return code, out.getvalue()


class TestGrouping(unittest.TestCase):
    """Tests for the group of each series."""

    def test_the_program_reads_the_series_of_an_event(self):
        self.assertEqual(fs.series_of("KXHIGHNY-26SEP01"), "KXHIGHNY")
        self.assertEqual(fs.series_of("KXHIGHNY"), "KXHIGHNY")
        self.assertIsNone(fs.series_of(""))
        self.assertIsNone(fs.series_of(None))

    def test_the_program_counts_the_events_of_each_series(self):
        rows = fs.group_events(WEATHER + OTHER)
        counts = {row["series"]: row["events"] for row in rows}
        self.assertEqual(counts, {"KXHIGHNY": 2, "KXHIGHCHI": 1, "KXFED": 1})

    def test_the_program_keeps_one_category(self):
        rows = fs.keep_category(fs.group_events(WEATHER + OTHER), "weather")
        self.assertEqual(sorted(row["series"] for row in rows),
                         ["KXHIGHCHI", "KXHIGHNY"])

    def test_an_empty_category_keeps_each_series(self):
        rows = fs.group_events(WEATHER + OTHER)
        self.assertEqual(len(fs.keep_category(rows, None)), 3)


class TestRequests(FindSeriesTestCase):
    """Tests for the requests to the API."""

    def test_the_program_reads_all_pages(self):
        def responder(query):
            if query.get("cursor") == ["PAGE2"]:
                return 200, {"events": OTHER, "cursor": ""}
            return 200, {"events": WEATHER, "cursor": "PAGE2"}

        self.start_api(responder)
        events = fs.fetch_events(fs.requests.Session())
        self.assertEqual(len(events), 4)

    def test_the_program_continues_after_a_refused_parameter(self):
        def responder(query):
            if "status" in query:
                return 400, {"error": "the parameter status is not known"}
            return 200, {"events": WEATHER, "cursor": ""}

        self.start_api(responder)
        events = fs.fetch_events(fs.requests.Session())
        self.assertEqual(len(events), 3)


class TestOutput(FindSeriesTestCase):
    """Tests for the text on the screen."""

    def test_the_table_contains_each_series(self):
        self.start_api()
        code, text = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("KXHIGHNY", text)
        self.assertIn("KXFED", text)
        self.assertIn("Climate and Weather", text)

    def test_the_option_category_keeps_the_weather(self):
        self.start_api()
        code, text = self.run_main(["--category", "weather"])
        self.assertEqual(code, 0)
        self.assertIn("KXHIGHNY", text)
        self.assertNotIn("KXFED", text)

    def test_the_option_tickers_prints_one_line(self):
        self.start_api()
        code, text = self.run_main(["--category", "weather", "--tickers"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(text.split()), ["KXHIGHCHI", "KXHIGHNY"])

    def test_an_unknown_category_gives_an_error(self):
        self.start_api()
        code, _text = self.run_main(["--category", "not-a-category"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
