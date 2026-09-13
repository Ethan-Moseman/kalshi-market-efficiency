#!/usr/bin/env python3
"""Collect the result of each settled market of Kalshi.

The other programs collect prices. This program collects the answer. For each
market that is closed, Kalshi gives the field `result`. The value is `yes` or
`no`. This value is the truth. Without it you cannot measure the calibration.

A price of 70 cents is a forecast. It says "this event comes 70 percent of the
time". To test that forecast, you must know what came. This program writes that.

The program writes to one file for each series:
    data/history/settlements_<series>.csv

The key of a line is the ticker. A market settles one time only. Because of
this, the program can run more than one time. It adds only the new markets.

The endpoint is public. The program uses no password and no key.

Requires: requests (see requirements.txt)

Examples:
    python3 settlements.py --inspect                 # Test the field names.
    python3 settlements.py                           # The series KXHIGHNY.
    python3 settlements.py --series KXHIGHNY KXHIGHCHI
    python3 settlements.py --days 90                 # The last 90 days.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

import requests

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

DEFAULT_SERIES = ["KXHIGHNY"]
DEFAULT_DATA_DIR = os.path.join("data", "history")
DEFAULT_STATUS = "settled"

REQUEST_TIMEOUT = 20.0
RETRY_COUNT = 2
RETRY_PAUSE = 1.0
PAUSE_BETWEEN_REQUESTS = 0.15
MARKET_PAGE_LIMIT = 200
MAX_PAGES = 500

# The API can use more than one name for the same value. The program uses the
# first name that the answer contains. Kalshi changed these names in the past.
# Because of this, the program accepts a group of names for each column.
FIELD_SOURCES = {
    "ticker": ("ticker",),
    "event_ticker": ("event_ticker",),
    "status": ("status",),
    "result": ("result", "settlement_result"),
    "close_time": ("close_time",),
    "strike_type": ("strike_type",),
    "floor_strike": ("floor_strike",),
    "cap_strike": ("cap_strike",),
    "expiration_value": ("expiration_value",),
    "last_price": ("last_price_dollars", "last_price"),
    "volume": ("volume_fp", "volume"),
    "open_interest": ("open_interest_fp", "open_interest"),
}

MARKET_FIELDS = tuple(FIELD_SOURCES)

CSV_FIELDS = [
    "ticker", "event_ticker", "series", "status", "result", "result_binary",
    "close_time", "close_ts", "strike_type", "floor_strike", "cap_strike",
    "expiration_value", "last_price", "volume", "open_interest",
]

# These values of the field `result` give a market with an answer. A market
# with a different value has no answer. An example is a market that Kalshi
# cancelled. That market must not go into the measurement of the calibration.
RESULT_YES = "yes"
RESULT_NO = "no"


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def make_session():
    """Make one HTTP session. The program uses this session for all requests."""
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "kalshi-market-collector/1.0 (settlements)",
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


def field_value(market, field):
    """Get one value of a market. Try each name of the group."""
    for name in FIELD_SOURCES[field]:
        value = market.get(name)
        if value is not None:
            return value
    return None


def to_epoch_seconds(text):
    """Change a time of the API into seconds. Return None after a bad value."""
    if not text:
        return None
    try:
        moment = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(moment.timestamp())


def result_binary(result):
    """Change the result into 1, 0 or an empty text.

    A market without an answer gives an empty text. The analysis must not use
    that market.
    """
    text = str(result or "").strip().lower()
    if text == RESULT_YES:
        return 1
    if text == RESULT_NO:
        return 0
    return ""


def settlement_row(market, series_ticker):
    """Make one line for the file of the settlements."""
    values = {field: field_value(market, field) for field in MARKET_FIELDS}
    close_ts = to_epoch_seconds(values.get("close_time"))
    row = {
        "ticker": values.get("ticker"),
        "event_ticker": values.get("event_ticker"),
        "series": series_ticker,
        "status": values.get("status"),
        "result": values.get("result"),
        "result_binary": result_binary(values.get("result")),
        "close_time": values.get("close_time"),
        "close_ts": close_ts,
        "strike_type": values.get("strike_type"),
        "floor_strike": values.get("floor_strike"),
        "cap_strike": values.get("cap_strike"),
        "expiration_value": values.get("expiration_value"),
        "last_price": values.get("last_price"),
        "volume": values.get("volume"),
        "open_interest": values.get("open_interest"),
    }
    return {k: ("" if v is None else v) for k, v in row.items()}


def fetch_settled_markets(session, series_ticker, status=DEFAULT_STATUS,
                          min_close_ts=None, max_close_ts=None):
    """Get the settled markets of one series. Follow the cursor to the last page."""
    params = {"series_ticker": series_ticker, "limit": MARKET_PAGE_LIMIT}
    if status and status != "all":
        params["status"] = status
    if min_close_ts:
        params["min_close_ts"] = int(min_close_ts)
    if max_close_ts:
        params["max_close_ts"] = int(max_close_ts)

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


def read_keys(path, key_field):
    """Read the keys of the lines that the file contains already."""
    if not os.path.exists(path):
        return set()
    keys = set()
    with open(path, newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            keys.add(str(line.get(key_field, "")))
    return keys


def append_new_rows(path, rows, key_field="ticker"):
    """Add the new lines to a file. Return the number of new lines."""
    known = read_keys(path, key_field)
    new_rows = []
    for row in rows:
        key = str(row.get(key_field, ""))
        if not key or key in known:
            continue
        known.add(key)
        new_rows.append(row)
    if not new_rows:
        return 0

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def run_inspect(session, series_ticker, status):
    """Get one settled market. Print its JSON and the value of each column."""
    markets = fetch_settled_markets(session, series_ticker, status)
    _log(f"{series_ticker}: {len(markets)} market(s) with the status {status!r}.")
    if not markets:
        _log("The API gave no market. Try the option --status all.")
        return 1

    match = markets[0]
    _log(f"The program shows the raw JSON of {match.get('ticker')}.")
    print(json.dumps(match, indent=2, sort_keys=True))

    _log("The program writes these values to the CSV file:")
    for field in MARKET_FIELDS:
        value = field_value(match, field)
        source = next((n for n in FIELD_SOURCES[field] if match.get(n) is not None),
                      "NOT FOUND")
        _log(f"    {field} = {value!r}   (from the field {source})")

    answer = field_value(match, "result")
    if result_binary(answer) == "":
        _log(f"CAUTION. The field `result` is {answer!r}. This value is not "
             "`yes` and it is not `no`.")
        _log("The name of the field changed, or this market has no answer.")
        _log("Look in the JSON above. Find the field with the answer. Then tell "
             "the author. Do not start a long collection.")
        return 1

    _log("The field `result` is good. The program can measure the calibration.")
    return 0


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Collect the result of each settled market of Kalshi.")
    parser.add_argument("--series", nargs="+", default=DEFAULT_SERIES,
                        metavar="TICKER",
                        help="The series. Give one name or more. Default: %s"
                             % " ".join(DEFAULT_SERIES))
    parser.add_argument("--status", default=DEFAULT_STATUS,
                        help="The status of the markets: settled, finalized, "
                             "closed or all. Default: %(default)s")
    parser.add_argument("--days", type=float,
                        help="Collect the markets that closed in the last days. "
                             "The default is each market that the API gives.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder for the CSV files. Default: %(default)s")
    parser.add_argument("--inspect", action="store_true",
                        help="Get one market. Print its JSON. Then stop.")
    parser.add_argument("--base-url", default=BASE_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.days is not None and args.days <= 0:
        parser.error("--days must be more than 0")
    return args


def collect_series(session, args, series_ticker, now_ts):
    """Collect the settled markets of one series.

    Return the number of new lines. Return None if the API gave no market.
    """
    min_close_ts = None
    if args.days:
        min_close_ts = now_ts - int(args.days * 24 * 3600)

    markets = fetch_settled_markets(session, series_ticker, args.status,
                                    min_close_ts, now_ts if args.days else None)
    if not markets:
        _log(f"{series_ticker}: the API gave no market. Try --status all.")
        return None

    rows = [settlement_row(market, series_ticker) for market in markets]
    with_answer = sum(1 for row in rows if row["result_binary"] != "")
    _log(f"{series_ticker}: {len(rows)} market(s). {with_answer} have an answer.")

    if with_answer == 0:
        _log(f"CAUTION. No market of {series_ticker} has an answer. Run the "
             "program again with the option --inspect.")

    path = os.path.join(args.data_dir, f"settlements_{series_ticker}.csv")
    new = append_new_rows(path, rows)
    _log(f"{path}: {new} new line(s) of {len(rows)}.")
    return new


def main(argv=None):
    global BASE_URL
    args = parse_args(argv)
    BASE_URL = args.base_url
    session = make_session()

    if args.inspect:
        return run_inspect(session, args.series[0], args.status)

    now_ts = int(time.time())
    written = 0
    found = 0
    for series_ticker in args.series:
        try:
            new = collect_series(session, args, series_ticker, now_ts)
        except requests.RequestException as exc:
            _log(f"{series_ticker}: the request failed: {exc}")
            continue
        if new is None:
            continue
        found += 1
        written += new

    _log(f"The program wrote {written} new line(s).")
    return 0 if found else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
