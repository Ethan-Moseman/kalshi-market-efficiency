#!/usr/bin/env python3
"""Poll Kalshi's public markets endpoint and log top-of-book changes to a daily CSV.

The endpoint is public, so no authentication is used. Every poll cycle the
collector fetches all open markets for each requested series and appends a row
only for markets whose quote (yes_bid / yes_ask / no_bid / no_ask) changed since
the previous observation. Each row carries the local receive timestamp in
nanoseconds and the round-trip time of the request that produced it.

Requires: requests (see requirements.txt)

Examples:
    python3 kalshi_collector.py                       # poll KXHIGHNY every 2s
    python3 kalshi_collector.py --series KXHIGHNY KXHIGHCHI
    python3 kalshi_collector.py --inspect             # dump one raw market
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
MAX_PAGES = 50  # guard against a cursor that never terminates

# Fields that decide whether a market's state is "new". Volume and open interest
# move on every trade; the task is to record quote changes only.
QUOTE_FIELDS = ("yes_bid", "yes_ask", "no_bid", "no_ask")

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
    _log(f"received signal {signum}, shutting down after this cycle")


def make_session():
    session = requests.Session()
    # Keep-alive matters: at a 2s cadence a fresh TLS handshake per poll would
    # dominate the round-trip time we are trying to measure.
    session.headers.update({"Accept": "application/json", "User-Agent": "kalshi-market-collector/1.0"})
    return session


def fetch_page(session, series_ticker, cursor=None):
    """Fetch one page of open markets. Returns (payload, recv_ts_ns, rtt_ms)."""
    params = {"series_ticker": series_ticker, "status": "open", "limit": PAGE_LIMIT}
    if cursor:
        params["cursor"] = cursor
    started = time.perf_counter_ns()
    response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    rtt_ms = (time.perf_counter_ns() - started) / 1e6
    recv_ts_ns = time.time_ns()
    response.raise_for_status()
    return response.json(), recv_ts_ns, rtt_ms


def fetch_markets(session, series_ticker):
    """Fetch every open market for a series, following the cursor.

    Returns a list of (market, recv_ts_ns, rtt_ms) so each market keeps the
    timing of the request that actually carried it.
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


def quote_key(market):
    return tuple(market.get(field) for field in QUOTE_FIELDS)


def build_row(market, recv_ts_ns, rtt_ms):
    row = {
        "recv_ts_ns": recv_ts_ns,
        "rtt_ms": f"{rtt_ms:.3f}",
        "ticker": market.get("ticker"),
        "event_ticker": market.get("event_ticker"),
        "volume": market.get("volume"),
        "open_interest": market.get("open_interest"),
    }
    for field in QUOTE_FIELDS:
        row[field] = market.get(field)
    # csv writes None as an empty string already; this keeps that explicit.
    return {k: ("" if v is None else v) for k, v in row.items()}


class DailyCsvWriter:
    """Append rows to data/<series>_<YYYY-MM-DD>.csv, rolling over at midnight."""

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
        self.close()
        self.day = day
        os.makedirs(self.data_dir, exist_ok=True)
        path = self.path
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.handle = open(path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=CSV_FIELDS)
        if is_new:
            self.writer.writeheader()
        _log(f"{self.series_ticker}: writing to {path}")

    def write(self, row):
        """Write a row, returning True if the write opened a new daily file."""
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


class SeriesCollector:
    def __init__(self, series_ticker, data_dir):
        self.series_ticker = series_ticker
        self.writer = DailyCsvWriter(data_dir, series_ticker)
        self.last_quotes = {}
        self.rows_written = 0

    def poll(self, session):
        snapshots = fetch_markets(session, self.series_ticker)
        written = 0
        for market, recv_ts_ns, rtt_ms in snapshots:
            ticker = market.get("ticker")
            if not ticker:
                continue
            key = quote_key(market)
            # A market seen for the first time is written as a baseline, so the
            # file always starts from a known book rather than a bare delta.
            if self.last_quotes.get(ticker) == key:
                continue
            rolled = self.writer.write(build_row(market, recv_ts_ns, rtt_ms))
            if rolled:
                # New daily file: drop the cache so this file also gets a full
                # baseline snapshot instead of only post-midnight changes.
                self.last_quotes.clear()
            self.last_quotes[ticker] = key
            written += 1
        self.writer.flush()
        self.rows_written += written
        return len(snapshots), written

    def close(self):
        self.writer.close()


def run_inspect(session, series_ticker, ticker=None):
    payload, _recv_ts_ns, rtt_ms = fetch_page(session, series_ticker)
    markets = payload.get("markets") or []
    _log(f"{series_ticker}: {len(markets)} open market(s) in {rtt_ms:.1f} ms")
    if not markets:
        _log("no open markets returned; nothing to inspect")
        return 1
    if ticker:
        match = next((m for m in markets if m.get("ticker") == ticker), None)
        if match is None:
            _log(f"ticker {ticker} not in the first page; available: "
                 + ", ".join(m.get("ticker", "?") for m in markets))
            return 1
    else:
        match = markets[0]
        _log(f"showing raw JSON for {match.get('ticker')} (use --ticker to pick another)")
    print(json.dumps(match, indent=2, sort_keys=True))
    return 0


def run_collect(session, collectors, interval):
    _log("polling %s every %.1fs (Ctrl-C to stop)"
         % (", ".join(c.series_ticker for c in collectors), interval))
    cycle = 0
    errors = 0
    start = time.monotonic()
    while not _stop:
        for collector in collectors:
            try:
                seen, written = collector.poll(session)
            except requests.RequestException as exc:
                errors += 1
                _log(f"{collector.series_ticker}: request failed: {exc}")
            except ValueError as exc:  # malformed JSON body
                errors += 1
                _log(f"{collector.series_ticker}: bad response body: {exc}")
            else:
                if written:
                    _log(f"{collector.series_ticker}: {written} change(s) of {seen} market(s)")
        cycle += 1
        # Absolute deadlines keep the cadence from drifting with request latency.
        deadline = start + cycle * interval
        while not _stop:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.25))

    total = sum(c.rows_written for c in collectors)
    _log(f"stopped after {cycle} cycle(s): {total} row(s) written, {errors} error(s)")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Poll Kalshi open markets and log quote changes to a daily CSV.")
    parser.add_argument("--series", nargs="+", default=DEFAULT_SERIES, metavar="TICKER",
                        help="series ticker(s) to poll (default: %s)" % " ".join(DEFAULT_SERIES))
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="seconds between polls (default: %(default)s)")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="directory for the daily CSV files (default: %(default)s)")
    parser.add_argument("--inspect", action="store_true",
                        help="fetch once, print the raw JSON of a single market, and exit")
    parser.add_argument("--ticker", help="with --inspect, the specific market ticker to print")
    parser.add_argument("--api-url", default=API_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")
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
