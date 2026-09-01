#!/usr/bin/env python3
"""Collect the weather forecast and the observed temperature. Write each change.

The market collector records the opinion of the market. This program records the
information that moves that opinion. The two files together give the delay
between new information and a new price.

The program uses the public API of the National Weather Service. This API needs
no password and no key. It needs a User-Agent header with a contact.

The program writes to two groups of files:
    data/weather/forecast_<series>_<date>.csv
    data/weather/observation_<series>_<date>.csv

The program writes a line only when a value changes. A new publication with the
same temperature makes no line. This rule is the same rule as the rule of the
market collector.

**IMPORTANT: You must confirm the station of each market.** Each market of
Kalshi names its station in the field rules_primary. Use this command to read
that text:
    python3 kalshi_collector.py --inspect | grep rules_primary
If the station is different, correct the table CITIES below. You can also make
the file weather_cities.json. That file replaces the table.

Requires: requests (see requirements.txt)

Examples:
    python3 weather_collector.py                    # Each known city.
    python3 weather_collector.py --series KXHIGHNY  # One city.
    python3 weather_collector.py --list             # The table of the cities.
    python3 weather_collector.py --inspect          # One fetch, raw values.
"""

import argparse
import csv
import datetime as dt
import json
import os
import signal
import sys
import time

import requests

BASE_URL = "https://api.weather.gov"

# The National Weather Service asks each program for a contact in this header.
USER_AGENT = ("kalshi-market-efficiency "
              "(https://github.com/Ethan-Moseman/kalshi-market-efficiency)")

DEFAULT_DATA_DIR = os.path.join("data", "weather")
DEFAULT_INTERVAL = 60.0
DEFAULT_DAYS = 2

REQUEST_TIMEOUT = 20.0
RETRY_COUNT = 2
RETRY_PAUSE = 2.0
PAUSE_BETWEEN_REQUESTS = 0.2

# The series of Kalshi and the station of each series.
# CAUTION: Confirm each station against the field rules_primary of the market.
# The file weather_cities.json replaces this table.
CITIES = {
    "KXHIGHNY":   {"city": "New York",     "station": "KNYC", "lat": 40.7789, "lon": -73.9692},
    "KXHIGHCHI":  {"city": "Chicago",      "station": "KMDW", "lat": 41.7842, "lon": -87.7553},
    "KXHIGHMIA":  {"city": "Miami",        "station": "KMIA", "lat": 25.7906, "lon": -80.3164},
    "KXHIGHAUS":  {"city": "Austin",       "station": "KAUS", "lat": 30.1975, "lon": -97.6664},
    "KXHIGHLAX":  {"city": "Los Angeles",  "station": "KLAX", "lat": 33.9381, "lon": -118.3889},
    "KXHIGHDEN":  {"city": "Denver",       "station": "KDEN", "lat": 39.8467, "lon": -104.6564},
    "KXHIGHPHIL": {"city": "Philadelphia", "station": "KPHL", "lat": 39.8683, "lon": -75.2311},
}

FORECAST_FIELDS = [
    "recv_ts_ns", "rtt_ms", "series", "city", "target_date", "period_name",
    "is_daytime", "high_f", "short_forecast", "nws_update_time",
]

OBSERVATION_FIELDS = [
    "recv_ts_ns", "rtt_ms", "series", "city", "station", "obs_time",
    "temp_c", "temp_f", "description",
]

_stop = False


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    _log(f"The program received signal {signum}. The program stops after this cycle.")


def load_cities(path="weather_cities.json"):
    """Read the table of the cities. Use the file if the file exists."""
    if not os.path.exists(path):
        return dict(CITIES)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def make_session():
    """Make one HTTP session. The API needs a User-Agent with a contact."""
    session = requests.Session()
    session.headers.update({"Accept": "application/geo+json",
                            "User-Agent": USER_AGENT})
    return session


def get_json(session, url, params=None):
    """Get one answer. Make the request again after a broken connection."""
    for attempt in range(RETRY_COUNT + 1):
        try:
            started = time.perf_counter_ns()
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            rtt_ms = (time.perf_counter_ns() - started) / 1e6
            recv_ts_ns = time.time_ns()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= RETRY_COUNT:
                raise
            _log(f"the connection failed ({type(exc).__name__}). "
                 "The program makes the request again.")
            time.sleep(RETRY_PAUSE)
            continue
        response.raise_for_status()
        time.sleep(PAUSE_BETWEEN_REQUESTS)
        return response.json(), recv_ts_ns, rtt_ms
    raise RuntimeError("the program made all attempts")


def to_fahrenheit(celsius):
    """Change a temperature in Celsius into a temperature in Fahrenheit."""
    if celsius is None:
        return None
    return celsius * 9.0 / 5.0 + 32.0


def local_date_of(text):
    """Get the date from a time of the API. Return an empty text after an error."""
    if not text:
        return ""
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return moment.strftime("%Y-%m-%d")


def find_forecast_url(session, city):
    """Get the address of the forecast of one city.

    The API gives this address from the position of the city. The address does
    not change. The program asks for it one time.
    """
    url = f"{BASE_URL}/points/{city['lat']},{city['lon']}"
    payload, _ts, _rtt = get_json(session, url)
    properties = payload.get("properties") or {}
    forecast = properties.get("forecast")
    if not forecast:
        raise RuntimeError(f"the API gave no forecast address for {city['city']}")
    return forecast


def fetch_forecast(session, forecast_url, days):
    """Get the forecast. Return the periods of the day and the time of update."""
    payload, recv_ts_ns, rtt_ms = get_json(session, forecast_url)
    properties = payload.get("properties") or {}
    periods = []
    for period in properties.get("periods") or []:
        if not period.get("isDaytime"):
            # The high temperature is the temperature of the day period.
            continue
        periods.append(period)
        if len(periods) >= days:
            break
    return periods, properties.get("updateTime"), recv_ts_ns, rtt_ms


def forecast_row(series, city, period, update_time, recv_ts_ns, rtt_ms):
    """Make one line for the file of the forecast."""
    row = {
        "recv_ts_ns": recv_ts_ns,
        "rtt_ms": f"{rtt_ms:.3f}",
        "series": series,
        "city": city["city"],
        "target_date": local_date_of(period.get("startTime")),
        "period_name": period.get("name"),
        "is_daytime": period.get("isDaytime"),
        "high_f": period.get("temperature"),
        "short_forecast": period.get("shortForecast"),
        "nws_update_time": update_time,
    }
    return {k: ("" if v is None else v) for k, v in row.items()}


def fetch_observations(session, station, start=None):
    """Get the observations of one station.

    Without a start, the program gets the last observation only. With a start,
    it gets each observation after that time.
    """
    if start:
        url = f"{BASE_URL}/stations/{station}/observations"
        payload, recv_ts_ns, rtt_ms = get_json(session, url, {"start": start})
        features = payload.get("features") or []
        return [item.get("properties") or {} for item in features], recv_ts_ns, rtt_ms
    url = f"{BASE_URL}/stations/{station}/observations/latest"
    payload, recv_ts_ns, rtt_ms = get_json(session, url)
    return [payload.get("properties") or {}], recv_ts_ns, rtt_ms


def observation_row(series, city, observation, recv_ts_ns, rtt_ms):
    """Make one line for the file of the observations."""
    temperature = (observation.get("temperature") or {}).get("value")
    fahrenheit = to_fahrenheit(temperature)
    row = {
        "recv_ts_ns": recv_ts_ns,
        "rtt_ms": f"{rtt_ms:.3f}",
        "series": series,
        "city": city["city"],
        "station": city["station"],
        "obs_time": observation.get("timestamp"),
        "temp_c": f"{temperature:.1f}" if temperature is not None else None,
        "temp_f": f"{fahrenheit:.1f}" if fahrenheit is not None else None,
        "description": observation.get("textDescription"),
    }
    return {k: ("" if v is None else v) for k, v in row.items()}


class DailyCsvWriter:
    """Add lines to a file of one day. Open a new file at midnight."""

    def __init__(self, data_dir, prefix, series_ticker, fields):
        self.data_dir = data_dir
        self.prefix = prefix
        self.series_ticker = series_ticker
        self.fields = fields
        self.day = None
        self.handle = None
        self.writer = None

    @property
    def path(self):
        return os.path.join(
            self.data_dir, f"{self.prefix}_{self.series_ticker}_{self.day}.csv")

    def _roll(self, day):
        self.close()
        self.day = day
        os.makedirs(self.data_dir, exist_ok=True)
        path = self.path
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.handle = open(path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=self.fields)
        if is_new:
            self.writer.writeheader()
        _log(f"{self.series_ticker}: the program writes to {path}")

    def write(self, row):
        """Write one line. Return True if the program opened a new daily file."""
        today = time.strftime("%Y-%m-%d")
        rolled = today != self.day
        if rolled:
            self._roll(today)
        self.writer.writerow(row)
        return rolled

    def flush(self):
        if self.handle:
            self.handle.flush()

    def close(self):
        if self.handle:
            self.handle.flush()
            self.handle.close()
        self.handle = None
        self.writer = None


class CityCollector:
    """Collect the forecast and the observations of one city."""

    def __init__(self, series_ticker, city, data_dir, days):
        self.series_ticker = series_ticker
        self.city = city
        self.days = days
        self.forecast_writer = DailyCsvWriter(data_dir, "forecast", series_ticker,
                                              FORECAST_FIELDS)
        self.observation_writer = DailyCsvWriter(data_dir, "observation",
                                                 series_ticker, OBSERVATION_FIELDS)
        self.forecast_url = None
        self.last_forecast = {}
        self.last_obs_time = None
        self.filled_today = None
        self.lines_written = 0

    def poll(self, session):
        """Do one cycle. Return the number of new lines."""
        if self.forecast_url is None:
            self.forecast_url = find_forecast_url(session, self.city)
            _log(f"{self.series_ticker}: the forecast address is {self.forecast_url}")

        written = self.poll_forecast(session)
        written += self.poll_observations(session)
        self.forecast_writer.flush()
        self.observation_writer.flush()
        self.lines_written += written
        return written

    def poll_forecast(self, session):
        """Get the forecast. Write a line for each new temperature."""
        periods, update_time, recv_ts_ns, rtt_ms = fetch_forecast(
            session, self.forecast_url, self.days)
        written = 0
        for period in periods:
            key = (local_date_of(period.get("startTime")), period.get("name"))
            value = (period.get("temperature"), period.get("shortForecast"))
            if self.last_forecast.get(key) == value:
                continue
            row = forecast_row(self.series_ticker, self.city, period, update_time,
                               recv_ts_ns, rtt_ms)
            if self.forecast_writer.write(row):
                self.last_forecast.clear()
            self.last_forecast[key] = value
            written += 1
            _log(f"{self.series_ticker}: {period.get('name')} = "
                 f"{period.get('temperature')} F ({period.get('shortForecast')})")
        return written

    def poll_observations(self, session):
        """Get the observations. Write a line for each new observation."""
        today = time.strftime("%Y-%m-%d")
        start = None
        if self.filled_today != today:
            # The program starts, or the day changed. It gets each observation
            # of this day. Because of this, the file has the full day.
            self.filled_today = today
            start = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00Z")

        observations, recv_ts_ns, rtt_ms = fetch_observations(
            session, self.city["station"], start)
        written = 0
        for observation in sorted(observations,
                                  key=lambda item: item.get("timestamp") or ""):
            obs_time = observation.get("timestamp")
            if not obs_time or (self.last_obs_time and obs_time <= self.last_obs_time):
                continue
            row = observation_row(self.series_ticker, self.city, observation,
                                  recv_ts_ns, rtt_ms)
            self.observation_writer.write(row)
            self.last_obs_time = obs_time
            written += 1
        if written:
            _log(f"{self.series_ticker}: {written} observation(s).")
        return written

    def close(self):
        self.forecast_writer.close()
        self.observation_writer.close()


def run_inspect(session, series_ticker, city, days):
    """Get the forecast and the last observation one time. Print the values."""
    forecast_url = find_forecast_url(session, city)
    _log(f"{series_ticker}: the forecast address is {forecast_url}")
    periods, update_time, _ts, rtt_ms = fetch_forecast(session, forecast_url, days)
    _log(f"{series_ticker}: {len(periods)} day period(s). The RTT was {rtt_ms:.0f} ms.")
    _log(f"{series_ticker}: the NWS published this forecast at {update_time}.")
    for period in periods:
        print(f"  {period.get('name'):<12} {period.get('temperature')} "
              f"{period.get('temperatureUnit')}   {period.get('shortForecast')}")
    observations, _ts, _rtt = fetch_observations(session, city["station"])
    if not observations:
        _log("The station gave no observation.")
        return 1
    latest = observations[0]
    celsius = (latest.get("temperature") or {}).get("value")
    print(f"  station {city['station']}: {to_fahrenheit(celsius):.1f} F at "
          f"{latest.get('timestamp')}" if celsius is not None
          else f"  station {city['station']}: no temperature")
    return 0


def run_collect(session, collectors, interval):
    """Poll each city until the user or the system stops the program."""
    names = ", ".join(item.series_ticker for item in collectors)
    _log(f"The program polls {names} each {interval:.0f} seconds. To stop, push Ctrl-C.")
    cycle = 0
    errors = 0
    start = time.monotonic()
    while not _stop:
        for collector in collectors:
            try:
                collector.poll(session)
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                errors += 1
                _log(f"{collector.series_ticker}: the request failed: {exc}")
        cycle += 1
        deadline = start + cycle * interval
        while not _stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.25))

    total = sum(item.lines_written for item in collectors)
    _log(f"The program stopped after {cycle} cycle(s). It wrote {total} line(s). "
         f"There were {errors} error(s).")
    return 0


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Collect the weather forecast and the observed temperature.")
    parser.add_argument("--series", nargs="+", metavar="TICKER",
                        help="The series to collect. The default is each city "
                             "of the table.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="The time between two cycles, in seconds. "
                             "Default: %(default)s")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="The number of day periods to record. "
                             "Default: %(default)s")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder for the CSV files. Default: %(default)s")
    parser.add_argument("--list", action="store_true", dest="list_it",
                        help="Print the table of the cities. Then stop.")
    parser.add_argument("--inspect", action="store_true",
                        help="Get the values one time. Print them. Then stop.")
    parser.add_argument("--api-url", default=BASE_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be more than 0")
    if args.days <= 0:
        parser.error("--days must be more than 0")
    return args


def main(argv=None):
    global BASE_URL
    args = parse_args(argv)
    BASE_URL = args.api_url

    cities = load_cities()
    if args.series:
        unknown = [name for name in args.series if name not in cities]
        known = [name for name in args.series if name in cities]
        if unknown:
            _log(f"These series are not in the table: {', '.join(unknown)}")
            _log("Use --list to see the table. Make weather_cities.json to add "
                 "a city.")
        if not known:
            return 1
        # The program continues with the known cities. A service does not stop
        # because of one wrong name.
        cities = {name: cities[name] for name in known}

    if args.list_it:
        for name, city in sorted(cities.items()):
            print(f"  {name:<12} {city['city']:<14} station {city['station']:<6} "
                  f"{city['lat']}, {city['lon']}")
        print("\nCAUTION: Confirm each station against the rules of the market.")
        return 0

    session = make_session()

    if args.inspect:
        name = next(iter(cities))
        return run_inspect(session, name, cities[name], args.days)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    collectors = [CityCollector(name, city, args.data_dir, args.days)
                  for name, city in sorted(cities.items())]
    try:
        return run_collect(session, collectors, args.interval)
    finally:
        for collector in collectors:
            collector.close()


if __name__ == "__main__":
    sys.exit(main())
