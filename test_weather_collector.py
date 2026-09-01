#!/usr/bin/env python3
"""Tests for the collector of the weather.

The tests use a local fake server. This server answers like the API of the
National Weather Service. The tests need no connection to the internet.

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
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests

import weather_collector as wc

CITY = {"city": "New York", "station": "KNYC", "lat": 40.7789, "lon": -73.9692}


def period(name="Today", temperature=88, short="Sunny", daytime=True,
           start="2026-09-01T06:00:00-04:00"):
    """Make one period of the forecast."""
    return {"name": name, "temperature": temperature, "temperatureUnit": "F",
            "shortForecast": short, "isDaytime": daytime, "startTime": start}


def observation(timestamp="2026-09-01T04:51:00+00:00", celsius=21.1,
                text="Clear"):
    """Make one observation of a station."""
    return {"timestamp": timestamp, "temperature": {"value": celsius,
                                                    "unitCode": "wmoUnit:degC"},
            "textDescription": text}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append((parsed.path, query))
        status, body = self.server.responder(parsed.path, query)
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/geo+json")
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
        return f"http://{host}:{port}"

    @property
    def requests(self):
        return self.server.requests

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class WeatherTestCase(unittest.TestCase):
    """The common start of each test."""

    def setUp(self):
        self.api = None
        wc._stop = False
        patcher = mock.patch.object(wc, "_log")
        patcher.start()
        self.addCleanup(patcher.stop)
        pause = mock.patch.object(wc, "PAUSE_BETWEEN_REQUESTS", 0)
        pause.start()
        self.addCleanup(pause.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, "weather")
        self.addCleanup(self.stop_api)
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"

    def stop_api(self):
        if self.api is not None:
            self.api.stop()

    def start_api(self, periods=None, observations=None, update="2026-09-01T10:00:00Z"):
        """Start a fake API with a fixed forecast and fixed observations."""
        state = {"periods": periods if periods is not None else [period()],
                 "observations": observations if observations is not None
                 else [observation()],
                 "update": update}
        self.state = state

        def responder(path, query):
            if path.startswith("/points/"):
                return 200, {"properties": {
                    "forecast": f"{self.api.url}/gridpoints/OKX/33,37/forecast"}}
            if path.endswith("/forecast"):
                return 200, {"properties": {"updateTime": state["update"],
                                            "periods": state["periods"]}}
            if path.endswith("/observations/latest"):
                return 200, {"properties": state["observations"][-1]}
            if path.endswith("/observations"):
                return 200, {"features": [{"properties": item}
                                          for item in state["observations"]]}
            return 404, {}

        self.api = FakeApi(responder)
        wc.BASE_URL = self.api.url
        return self.api

    def collector(self, days=1):
        return wc.CityCollector("KXHIGHNY", CITY, self.data_dir, days)

    def read_csv(self, prefix):
        names = [n for n in os.listdir(self.data_dir) if n.startswith(prefix)]
        path = os.path.join(self.data_dir, sorted(names)[0])
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))


class TestValues(unittest.TestCase):
    """Tests for the change of a value."""

    def test_the_program_changes_celsius_into_fahrenheit(self):
        self.assertAlmostEqual(wc.to_fahrenheit(0), 32.0)
        self.assertAlmostEqual(wc.to_fahrenheit(100), 212.0)
        self.assertAlmostEqual(wc.to_fahrenheit(21.1), 69.98)
        self.assertIsNone(wc.to_fahrenheit(None))

    def test_the_program_reads_the_date_of_a_time(self):
        self.assertEqual(wc.local_date_of("2026-09-01T06:00:00-04:00"), "2026-09-01")
        self.assertEqual(wc.local_date_of("2026-09-01T10:00:00Z"), "2026-09-01")
        self.assertEqual(wc.local_date_of("not a time"), "")
        self.assertEqual(wc.local_date_of(None), "")

    def test_the_table_of_the_cities_has_each_necessary_field(self):
        for name, city in wc.CITIES.items():
            self.assertIn("city", city, name)
            self.assertIn("station", city, name)
            self.assertIsInstance(city["lat"], float, name)
            self.assertIsInstance(city["lon"], float, name)


class TestForecast(WeatherTestCase):
    """Tests for the forecast."""

    def test_the_program_finds_the_address_of_the_forecast(self):
        self.start_api()
        url = wc.find_forecast_url(wc.make_session(), CITY)
        self.assertTrue(url.endswith("/forecast"))

    def test_the_program_keeps_the_day_periods_only(self):
        self.start_api(periods=[period(name="Today"),
                                period(name="Tonight", daytime=False),
                                period(name="Tuesday", temperature=91)])
        collector = self.collector(days=2)
        collector.poll(wc.make_session())
        collector.close()
        rows = self.read_csv("forecast")
        self.assertEqual([row["period_name"] for row in rows], ["Today", "Tuesday"])

    def test_the_same_forecast_makes_no_new_line(self):
        self.start_api()
        collector = self.collector()
        session = wc.make_session()
        counts = [collector.poll(session) for _ in range(3)]
        collector.close()
        self.assertEqual(counts[0], 2)   # One forecast and one observation.
        self.assertEqual(counts[1:], [0, 0])

    def test_a_new_temperature_makes_a_new_line(self):
        self.start_api()
        collector = self.collector()
        session = wc.make_session()
        collector.poll(session)
        self.state["periods"] = [period(temperature=91)]
        collector.poll(session)
        collector.close()
        rows = self.read_csv("forecast")
        self.assertEqual([row["high_f"] for row in rows], ["88", "91"])

    def test_a_new_description_makes_a_new_line(self):
        self.start_api()
        collector = self.collector()
        session = wc.make_session()
        collector.poll(session)
        self.state["periods"] = [period(short="Rain")]
        collector.poll(session)
        collector.close()
        rows = self.read_csv("forecast")
        self.assertEqual([row["short_forecast"] for row in rows], ["Sunny", "Rain"])

    def test_the_line_contains_the_time_of_the_publication(self):
        self.start_api()
        collector = self.collector()
        collector.poll(wc.make_session())
        collector.close()
        row = self.read_csv("forecast")[0]
        self.assertEqual(row["nws_update_time"], "2026-09-01T10:00:00Z")
        self.assertEqual(row["series"], "KXHIGHNY")
        self.assertEqual(row["city"], "New York")
        self.assertEqual(row["target_date"], "2026-09-01")
        self.assertGreater(int(row["recv_ts_ns"]), 1_600_000_000_000_000_000)
        self.assertGreater(float(row["rtt_ms"]), 0.0)


class TestObservations(WeatherTestCase):
    """Tests for the observations of the station."""

    def test_the_first_cycle_gets_each_observation_of_the_day(self):
        self.start_api(observations=[observation("2026-09-01T01:51:00+00:00", 18.0),
                                     observation("2026-09-01T02:51:00+00:00", 19.0),
                                     observation("2026-09-01T03:51:00+00:00", 20.0)])
        collector = self.collector()
        collector.poll(wc.make_session())
        collector.close()
        rows = self.read_csv("observation")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["temp_c"], "18.0")
        self.assertEqual(rows[0]["temp_f"], "64.4")
        self.assertEqual(rows[0]["station"], "KNYC")

    def test_an_old_observation_makes_no_new_line(self):
        self.start_api()
        collector = self.collector()
        session = wc.make_session()
        collector.poll(session)
        collector.poll(session)
        collector.close()
        self.assertEqual(len(self.read_csv("observation")), 1)

    def test_a_new_observation_makes_a_new_line(self):
        self.start_api()
        collector = self.collector()
        session = wc.make_session()
        collector.poll(session)
        self.state["observations"] = [observation("2026-09-01T05:51:00+00:00", 23.0)]
        collector.poll(session)
        collector.close()
        rows = self.read_csv("observation")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["temp_f"], "73.4")

    def test_an_observation_without_a_temperature_makes_an_empty_cell(self):
        self.start_api(observations=[observation(celsius=None)])
        collector = self.collector()
        collector.poll(wc.make_session())
        collector.close()
        row = self.read_csv("observation")[0]
        self.assertEqual(row["temp_c"], "")
        self.assertEqual(row["temp_f"], "")


class TestLoop(WeatherTestCase):
    """Tests for the loop and for the options."""

    def test_the_loop_continues_after_an_error(self):
        class FailingCollector:
            series_ticker = "KXHIGHNY"
            lines_written = 0
            calls = 0

            def poll(self, _session):
                FailingCollector.calls += 1
                if FailingCollector.calls >= 3:
                    wc._stop = True
                raise requests.ConnectionError("the network is not available")

        result = wc.run_collect(None, [FailingCollector()], 0.01)
        self.assertEqual(result, 0)
        self.assertEqual(FailingCollector.calls, 3)

    def test_the_option_list_prints_the_table(self):
        out = StringIO()
        with mock.patch("sys.stdout", out):
            result = wc.main(["--list"])
        self.assertEqual(result, 0)
        self.assertIn("KXHIGHNY", out.getvalue())
        self.assertIn("KNYC", out.getvalue())

    def test_an_unknown_series_gives_an_error(self):
        self.assertEqual(wc.main(["--list", "--series", "NOT-A-SERIES"]), 1)

    def test_the_option_inspect_prints_the_forecast(self):
        self.start_api()
        out = StringIO()
        with mock.patch("sys.stdout", out):
            result = wc.main(["--inspect", "--series", "KXHIGHNY",
                              "--api-url", self.api.url])
        self.assertEqual(result, 0)
        self.assertIn("Today", out.getvalue())
        self.assertIn("88", out.getvalue())

    def test_an_interval_of_zero_is_not_permitted(self):
        import contextlib
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            wc.parse_args(["--interval", "0"])

    def test_the_default_options(self):
        args = wc.parse_args([])
        self.assertEqual(args.interval, 60.0)
        self.assertEqual(args.days, 2)
        self.assertIn("weather", args.data_dir)


if __name__ == "__main__":
    unittest.main()
