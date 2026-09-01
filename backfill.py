#!/usr/bin/env python3
"""Collect the past data of Kalshi. Write the data to CSV files.

The collector kalshi_collector.py gets only the present. This program gets the
past. Kalshi keeps two groups of past data:

1. The candlesticks. For each minute they give the open, the high, the low and
   the close of yes_bid and yes_ask. They also give the volume.
2. The trades. For each trade they give the price, the number of contracts, the
   time and the side of the taker.

The endpoints are public. The program uses no password and no key.

The program writes to two files for each series:
    data/history/candles_<series>.csv
    data/history/trades_<series>.csv

The folder is not the folder of the collector. The two programs write different
columns. Each program must read only its own files.

The program can run more than one time. It reads the file first. Then it adds
only the new lines. It does not make a copy of a line.

Requires: requests (see requirements.txt)

Examples:
    python3 backfill.py                                  # The series KXHIGHNY.
    python3 backfill.py --series KXHIGHNY --days 3       # The last three days.
    python3 backfill.py --ticker KXHIGHNY-26SEP01-T90    # Only one market.
    python3 backfill.py --what trades                    # Only the trades.
"""

import argparse
import csv
import datetime as dt
import os
import sys
import time

import requests

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

DEFAULT_SERIES = "KXHIGHNY"
DEFAULT_DATA_DIR = os.path.join("data", "history")
DEFAULT_INTERVAL_MINUTES = 1

REQUEST_TIMEOUT = 20.0
RETRY_COUNT = 2
RETRY_PAUSE = 1.0
PAUSE_BETWEEN_REQUESTS = 0.15
MARKET_PAGE_LIMIT = 200
TRADE_PAGE_LIMIT = 1000
MAX_PAGES = 200

# The endpoint of the candlesticks gives a limited number of periods for each
# request. The program divides a long time into parts of this size.
MAX_PERIODS_IN_ONE_REQUEST = 5000

SECONDS_IN_ONE_MINUTE = 60

CANDLE_FIELDS = [
    "ticker", "event_ticker", "end_period_ts", "end_period_utc",
    "yes_bid_open", "yes_bid_high", "yes_bid_low", "yes_bid_close",
    "yes_ask_open", "yes_ask_high", "yes_ask_low", "yes_ask_close",
    "price_open", "price_high", "price_low", "price_close",
    "volume", "open_interest",
]

TRADE_FIELDS = [
    "trade_id", "ticker", "created_time", "yes_price_dollars",
    "no_price_dollars", "count_fp", "taker_book_side", "taker_outcome_side",
    "is_block_trade",
]


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def make_session():
    """Make one HTTP session. The program uses this session for all requests."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "kalshi-market-collector/1.0 (backfill)",
    })
    return session


def get_json(session, url, params=None):
    """Get one answer. Make the request again after a broken connection."""
    for attempt in range(RETRY_COUNT + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= RETRY_COUNT:
                raise
            _log(f"the connection failed ({type(exc).__name__}). "
                 "The program makes the request again.")
            time.sleep(RETRY_PAUSE)
            continue
        response.raise_for_status()
        time.sleep(PAUSE_BETWEEN_REQUESTS)
        return response.json()
    raise RuntimeError("the program made all attempts")


def to_epoch_seconds(text):
    """Change a time of the API into seconds. Return None after a bad value."""
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(moment.timestamp())


def to_utc_text(epoch_seconds):
    """Change seconds into a time in the UTC zone."""
    moment = dt.datetime.fromtimestamp(int(epoch_seconds), dt.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_markets(session, series_ticker, status=None, ticker=None):
    """Get the list of the markets of one series."""
    if ticker:
        params = {"tickers": ticker, "limit": MARKET_PAGE_LIMIT}
    else:
        params = {"series_ticker": series_ticker, "limit": MARKET_PAGE_LIMIT}
    if status and status != "all":
        params["status"] = status

    markets = []
    cursor = None
    for _ in range(MAX_PAGES):
        if cursor:
            params["cursor"] = cursor
        payload = get_json(session, f"{BASE_URL}/markets", params)
        page = payload.get("markets") or []
        markets.extend(page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
    return markets


def time_parts(start_ts, end_ts, interval_minutes):
    """Divide a long time into parts. Return a list of (start, end)."""
    size = MAX_PERIODS_IN_ONE_REQUEST * interval_minutes * SECONDS_IN_ONE_MINUTE
    parts = []
    part_start = start_ts
    while part_start < end_ts:
        part_end = min(part_start + size, end_ts)
        parts.append((part_start, part_end))
        part_start = part_end
    return parts


def leg_values(candle, name):
    """Get the four prices of one leg. A leg is yes_bid, yes_ask or price."""
    part = candle.get(name) or {}
    return [part.get("open_dollars"), part.get("high_dollars"),
            part.get("low_dollars"), part.get("close_dollars")]


def candle_row(market, candle):
    """Make one line for the file of the candlesticks."""
    end_ts = candle.get("end_period_ts")
    values = [market.get("ticker"), market.get("event_ticker"), end_ts,
              to_utc_text(end_ts) if end_ts else ""]
    values += leg_values(candle, "yes_bid")
    values += leg_values(candle, "yes_ask")
    values += leg_values(candle, "price")
    values += [candle.get("volume_fp"), candle.get("open_interest_fp")]
    row = dict(zip(CANDLE_FIELDS, values))
    return {k: ("" if v is None else v) for k, v in row.items()}


def fetch_candles(session, series_ticker, market, start_ts, end_ts, interval_minutes):
    """Get the candlesticks of one market. Return a list of lines."""
    ticker = market.get("ticker")
    url = f"{BASE_URL}/series/{series_ticker}/markets/{ticker}/candlesticks"
    rows = []
    for part_start, part_end in time_parts(start_ts, end_ts, interval_minutes):
        params = {"start_ts": part_start, "end_ts": part_end,
                  "period_interval": interval_minutes}
        try:
            payload = get_json(session, url, params)
        except requests.HTTPError as exc:
            _log(f"{ticker}: the request for the candlesticks failed: {exc}")
            continue
        for candle in payload.get("candlesticks") or []:
            rows.append(candle_row(market, candle))
    return rows


def trade_row(trade):
    """Make one line for the file of the trades."""
    row = {field: trade.get(field) for field in TRADE_FIELDS}
    return {k: ("" if v is None else v) for k, v in row.items()}


def fetch_trades(session, ticker, start_ts=None, end_ts=None):
    """Get the trades of one market. Follow the cursor to the last page."""
    params = {"ticker": ticker, "limit": TRADE_PAGE_LIMIT}
    if start_ts:
        params["min_ts"] = start_ts
    if end_ts:
        params["max_ts"] = end_ts

    rows = []
    cursor = None
    for _ in range(MAX_PAGES):
        if cursor:
            params["cursor"] = cursor
        try:
            payload = get_json(session, f"{BASE_URL}/markets/trades", params)
        except requests.HTTPError as exc:
            _log(f"{ticker}: the request for the trades failed: {exc}")
            break
        page = payload.get("trades") or []
        rows.extend(trade_row(trade) for trade in page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
    return rows


def read_keys(path, fields, key_fields):
    """Read the keys of the lines that the file contains already."""
    if not os.path.exists(path):
        return set()
    keys = set()
    with open(path, newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            keys.add(tuple(str(line.get(field, "")) for field in key_fields))
    return keys


def append_new_rows(path, fields, rows, key_fields):
    """Add the new lines to a file. Return the number of new lines."""
    known = read_keys(path, fields, key_fields)
    new_rows = []
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in key_fields)
        if key in known:
            continue
        known.add(key)
        new_rows.append(row)
    if not new_rows:
        return 0

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Collect the past data of Kalshi. Write it to CSV files.")
    parser.add_argument("--series", default=DEFAULT_SERIES,
                        help="The series. Default: %(default)s")
    parser.add_argument("--ticker",
                        help="Collect the data of one market only.")
    parser.add_argument("--what", choices=["candles", "trades", "both"],
                        default="both", help="The data to collect. "
                                             "Default: %(default)s")
    parser.add_argument("--days", type=float,
                        help="Collect the last days only. The default is the "
                             "full life of each market.")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MINUTES,
                        help="The length of one candlestick, in minutes. "
                             "Default: %(default)s")
    parser.add_argument("--status", default="open",
                        help="The status of the markets: open, closed, settled "
                             "or all. Default: %(default)s")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder for the CSV files. Default: %(default)s")
    parser.add_argument("--base-url", default=BASE_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be more than 0")
    if args.days is not None and args.days <= 0:
        parser.error("--days must be more than 0")
    return args


def main(argv=None):
    global BASE_URL
    args = parse_args(argv)
    BASE_URL = args.base_url
    session = make_session()

    markets = fetch_markets(session, args.series, args.status, args.ticker)
    if not markets:
        _log(f"{args.series}: the API gave no market. Try --status all.")
        return 1
    _log(f"{args.series}: {len(markets)} market(s).")

    now_ts = int(time.time())
    candle_rows = []
    trade_rows = []
    for market in markets:
        ticker = market.get("ticker")
        if not ticker:
            continue
        if args.days:
            start_ts = now_ts - int(args.days * 24 * 3600)
        else:
            start_ts = to_epoch_seconds(market.get("open_time")) or (now_ts - 86400)
        end_ts = min(to_epoch_seconds(market.get("close_time")) or now_ts, now_ts)
        if end_ts <= start_ts:
            _log(f"{ticker}: the time is empty. The program continues.")
            continue

        if args.what in ("candles", "both"):
            rows = fetch_candles(session, args.series, market, start_ts, end_ts,
                                 args.interval)
            candle_rows.extend(rows)
            _log(f"{ticker}: {len(rows)} candlestick(s).")
        if args.what in ("trades", "both"):
            rows = fetch_trades(session, ticker, start_ts, end_ts)
            trade_rows.extend(rows)
            _log(f"{ticker}: {len(rows)} trade(s).")

    written = 0
    if candle_rows:
        path = os.path.join(args.data_dir, f"candles_{args.series}.csv")
        new = append_new_rows(path, CANDLE_FIELDS, candle_rows,
                              ("ticker", "end_period_ts"))
        _log(f"{path}: {new} new line(s) of {len(candle_rows)}.")
        written += new
    if trade_rows:
        path = os.path.join(args.data_dir, f"trades_{args.series}.csv")
        new = append_new_rows(path, TRADE_FIELDS, trade_rows, ("trade_id",))
        _log(f"{path}: {new} new line(s) of {len(trade_rows)}.")
        written += new

    _log(f"The program wrote {written} new line(s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
