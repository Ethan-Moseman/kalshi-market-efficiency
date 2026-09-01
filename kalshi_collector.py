#!/usr/bin/env python3
"""Collect prices from the public Kalshi API. Write each change to a CSV file.

The endpoint is public. The program uses no password and no key.

In each cycle the program asks for all open markets of each series. The program
writes a line only when the quote of a market changes. The quote is the group of
four prices: yes_bid, yes_ask, no_bid and no_ask. Each line contains the local
time of the answer in nanoseconds. Each line also contains the round-trip time
of the request in milliseconds.

This program needs the library "requests". Refer to the file requirements.txt.

Examples:
    python3 kalshi_collector.py                     # Poll KXHIGHNY each 2 seconds.
    python3 kalshi_collector.py --series KXHIGHNY KXHIGHCHI
    python3 kalshi_collector.py --inspect           # Print one market. Then stop.
"""

import argparse
import csv
import json
import os
import signal
import sys
import time

import requests

API_URL = "https://external-api.kalshi.com/trade-api/v2/markets"

DEFAULT_SERIES = ["KXHIGHNY"]
DEFAULT_INTERVAL = 2.0
DEFAULT_DATA_DIR = "data"

PAGE_LIMIT = 200
REQUEST_TIMEOUT = 10.0
MAX_PAGES = 50  # This limit stops an endless loop if the cursor does not end.

# These four fields make the quote. A change of the quote makes a new line.
# The fields volume and open_interest change with each trade. A change of these
# two fields alone makes no new line.
QUOTE_FIELDS = ("yes_bid", "yes_ask", "no_bid", "no_ask")

# The program takes these fields from each market.
MARKET_FIELDS = ("ticker", "event_ticker") + QUOTE_FIELDS + ("volume", "open_interest")

# The API sends the price as a text in dollars. The name of the field has the
# ending "_dollars". An example is yes_bid_dollars = "0.0100". This value is
# equal to 1 cent. The counters have the ending "_fp".
#
# Older documents of Kalshi use short names such as yes_bid and volume. The
# program accepts the two groups of names. It uses the first name that the
# answer contains.
FIELD_SOURCES = {
    "ticker": ("ticker",),
    "event_ticker": ("event_ticker",),
    "yes_bid": ("yes_bid_dollars", "yes_bid"),
    "yes_ask": ("yes_ask_dollars", "yes_ask"),
    "no_bid": ("no_bid_dollars", "no_bid"),
    "no_ask": ("no_ask_dollars", "no_ask"),
    "volume": ("volume_fp", "volume"),
    "open_interest": ("open_interest_fp", "open_interest"),
}

CSV_FIELDS = [
    "recv_ts_ns",
    "rtt_ms",
    "ticker",
    "event_ticker",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "volume",
    "open_interest",
]

_stop = False


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    _log(f"The program received signal {signum}. The program stops after this cycle.")


def make_session():
    """Make one HTTP session. The program uses this session for all requests."""
    session = requests.Session()
    # The program keeps one connection open. A new connection needs more time
    # than the request itself. One connection keeps the measurement of the
    # round-trip time correct.
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "kalshi-market-collector/1.0",
    })
    return session


def fetch_page(session, series_ticker, cursor=None):
    """Get one page of open markets. Return (payload, recv_ts_ns, rtt_ms)."""
    params = {"series_ticker": series_ticker, "status": "open", "limit": PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor
    started = time.perf_counter_ns()
    response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    # The program measures the time with a monotonic clock. A change of the
    # system clock does not corrupt this measurement.
    rtt_ms = (time.perf_counter_ns() - started) / 1e6
    recv_ts_ns = time.time_ns()
    response.raise_for_status()
    return response.json(), recv_ts_ns, rtt_ms


def fetch_markets(session, series_ticker):
    """Get all open markets of one series. Follow the cursor to the last page.

    Return a list of (market, recv_ts_ns, rtt_ms). Each market keeps the time of
    the request that carried it.
    """
    snapshots = []
    cursor = None
    for _ in range(MAX_PAGES):
        payload, recv_ts_ns, rtt_ms = fetch_page(session, series_ticker, cursor)
        markets = payload.get("markets") or []
        for market in markets:
            snapshots.append((market, recv_ts_ns, rtt_ms))
        cursor = payload.get("cursor")
        if not cursor or not markets:
            break
    return snapshots


def field_value(market, field):
    """Get the value of one field. Accept the two groups of names."""
    for name in FIELD_SOURCES[field]:
        value = market.get(name)
        if value is not None:
            return value
    return None


def quote_key(market):
    """Return the four prices of one market as a tuple."""
    return tuple(field_value(market, field) for field in QUOTE_FIELDS)


def market_has_no_price(market):
    """Return True if the market contains no price with a known name."""
    return all(field_value(market, field) is None for field in QUOTE_FIELDS)


def build_row(market, recv_ts_ns, rtt_ms):
    """Make one line for the CSV file."""
    row = {"recv_ts_ns": recv_ts_ns, "rtt_ms": f"{rtt_ms:.3f}"}
    for field in MARKET_FIELDS:
        row[field] = field_value(market, field)
    # The csv module writes the value None as an empty cell. The next line makes
    # this behavior clear to the reader.
    return {k: ("" if v is None else v) for k, v in row.items()}


class DailyCsvWriter:
    """Add lines to data/<series>_<date>.csv. Open a new file at midnight."""

    def __init__(self, data_dir, series_ticker):
        self.data_dir = data_dir
        self.series_ticker = series_ticker
        self.day = None
        self.handle = None
        self.writer = None

    @property
    def path(self):
        return os.path.join(self.data_dir, f"{self.series_ticker}_{self.day}.csv")

    def _roll(self, day):
        """Close the old file. Then open the file of the given day."""
        self.close()
        self.day = day
        os.makedirs(self.data_dir, exist_ok=True)
        path = self.path
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.handle = open(path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=CSV_FIELDS)
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
        """Send the lines in memory to the disk."""
        if self.handle:
            self.handle.flush()

    def close(self):
        """Write the last lines to the disk. Then close the file."""
        if self.handle:
            self.handle.flush()
            self.handle.close()
        self.handle = None
        self.writer = None


class SeriesCollector:
    """Poll one series. Write each change of a quote to the daily CSV file."""

    def __init__(self, series_ticker, data_dir):
        self.series_ticker = series_ticker
        self.writer = DailyCsvWriter(data_dir, series_ticker)
        self.last_quotes = {}
        self.lines_written = 0
        self.checked_names = False

    def poll(self, session):
        """Do one poll. Return the number of markets and the number of lines."""
        snapshots = fetch_markets(session, self.series_ticker)
        # The API can change the names of its fields. Then the program finds no
        # price. It gives a caution one time. A silent collection of empty
        # lines is worse than a message.
        if snapshots and not self.checked_names:
            self.checked_names = True
            if market_has_no_price(snapshots[0][0]):
                _log(f"{self.series_ticker}: CAUTION. The program found no price "
                     "in the answer. The API possibly changed the names of its "
                     "fields. Use the option --inspect. Then look at the names.")
        written = 0
        for market, recv_ts_ns, rtt_ms in snapshots:
            ticker = field_value(market, "ticker")
            if not ticker:
                continue
            key = quote_key(market)
            # The program sees this market for the first time. It writes a start
            # value. Because of this, the file starts with a known quote.
            if self.last_quotes.get(ticker) == key:
                continue
            rolled = self.writer.write(build_row(market, recv_ts_ns, rtt_ms))
            if rolled:
                # This is a new daily file. The program forgets the last quotes.
                # Because of this, the new file also gets a full set of start
                # values.
                self.last_quotes.clear()
            self.last_quotes[ticker] = key
            written += 1
        self.writer.flush()
        self.lines_written += written
        return len(snapshots), written

    def close(self):
        """Close the CSV file."""
        self.writer.close()


def run_inspect(session, series_ticker, ticker=None):
    """Send one request. Print the raw JSON of one market. Then stop."""
    payload, _recv_ts_ns, rtt_ms = fetch_page(session, series_ticker)
    markets = payload.get("markets") or []
    _log(f"{series_ticker}: {len(markets)} open market(s). The RTT was {rtt_ms:.1f} ms.")
    if not markets:
        _log("There are no open markets. The program stops.")
        return 1
    if ticker:
        match = next((m for m in markets if m.get("ticker") == ticker), None)
        if match is None:
            _log(f"The ticker {ticker} is not on the first page. These tickers are "
                 "available: " + ", ".join(m.get("ticker", "?") for m in markets))
            return 1
    else:
        match = markets[0]
        _log(f"The program shows the raw JSON of {match.get('ticker')}. "
             "To select a different market, use the option --ticker.")
    print(json.dumps(match, indent=2, sort_keys=True))
    _log("The program writes these values to the CSV file:")
    for field in MARKET_FIELDS:
        value = field_value(match, field)
        source = next((n for n in FIELD_SOURCES[field] if match.get(n) is not None),
                      "NOT FOUND")
        _log(f"    {field} = {value!r}   (from the field {source})")
    if market_has_no_price(match):
        _log("CAUTION. The program found no price. Send these names to the author.")
        return 1
    return 0


def run_collect(session, collectors, interval):
    """Poll all series until the user or the system stops the program."""
    names = ", ".join(c.series_ticker for c in collectors)
    _log(f"The program polls {names} each {interval:.1f} seconds. To stop, push Ctrl-C.")
    cycle = 0
    errors = 0
    start = time.monotonic()
    while not _stop:
        for collector in collectors:
            try:
                seen, written = collector.poll(session)
            except requests.RequestException as exc:
                errors += 1
                _log(f"{collector.series_ticker}: the request failed: {exc}")
            except ValueError as exc:
                # The body of the answer is not correct JSON.
                errors += 1
                _log(f"{collector.series_ticker}: the answer is not correct JSON: {exc}")
            else:
                if written:
                    _log(f"{collector.series_ticker}: {written} change(s) "
                         f"in {seen} market(s).")
        cycle += 1
        # The program calculates the time of the next cycle from the start time.
        # Because of this, the duration of a request does not move the later
        # cycles.
        deadline = start + cycle * interval
        while not _stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # The program sleeps in short steps. Because of this, it answers a
            # signal quickly.
            time.sleep(min(remaining, 0.25))

    total = sum(c.lines_written for c in collectors)
    _log(f"The program stopped after {cycle} cycle(s). It wrote {total} line(s). "
         f"There were {errors} error(s).")
    return 0


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Poll the open Kalshi markets. Write each change of a quote "
                    "to a daily CSV file.")
    parser.add_argument("--series", nargs="+", default=DEFAULT_SERIES, metavar="TICKER",
                        help="The series to poll. Give one name or more. "
                             "Default: %s" % " ".join(DEFAULT_SERIES))
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="The time between two cycles, in seconds. "
                             "Default: %(default)s")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder for the daily CSV files. Default: %(default)s")
    parser.add_argument("--inspect", action="store_true",
                        help="Send one request. Print the JSON of one market. Then stop.")
    parser.add_argument("--ticker",
                        help="With --inspect, the market to print.")
    parser.add_argument("--api-url", default=API_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be more than 0")
    return args


def main(argv=None):
    global API_URL
    args = parse_args(argv)
    API_URL = args.api_url
    session = make_session()

    if args.inspect:
        return run_inspect(session, args.series[0], args.ticker)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    collectors = [SeriesCollector(s, args.data_dir) for s in args.series]
    try:
        return run_collect(session, collectors, args.interval)
    finally:
        for collector in collectors:
            collector.close()


if __name__ == "__main__":
    sys.exit(main())
