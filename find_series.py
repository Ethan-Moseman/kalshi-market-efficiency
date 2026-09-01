#!/usr/bin/env python3
"""Find the series of Kalshi. Print the ticker of each series with its category.

The collector needs the ticker of a series. This program finds each ticker from
the public endpoint /events. It groups the tickers by category.

The name of an event starts with the name of its series. An example is the event
KXHIGHNY-26SEP01. Its series is KXHIGHNY. The program uses this rule.

The endpoint is public. The program uses no password and no key.

Examples:
    python3 find_series.py                       # All categories.
    python3 find_series.py --category weather    # The weather only.
    python3 find_series.py --category weather --tickers   # Only the tickers.
"""

import argparse
import collections
import sys
import time

import requests

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"

PAGE_LIMIT = 200
MAX_PAGES = 100
REQUEST_TIMEOUT = 20.0
PAUSE_BETWEEN_REQUESTS = 0.15


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def fetch_events(session, status="open"):
    """Get the events of Kalshi. Follow the cursor to the last page."""
    events = []
    cursor = None
    params = {"limit": PAGE_LIMIT}
    if status and status != "all":
        params["status"] = status
    for _ in range(MAX_PAGES):
        if cursor:
            params["cursor"] = cursor
        response = session.get(f"{BASE_URL}/events", params=params,
                               timeout=REQUEST_TIMEOUT)
        if response.status_code == 400 and "status" in params:
            # This version of the API does not accept the parameter status.
            _log("The API refused the parameter status. The program continues "
                 "without it.")
            del params["status"]
            continue
        response.raise_for_status()
        payload = response.json()
        page = payload.get("events") or []
        events.extend(page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
        time.sleep(PAUSE_BETWEEN_REQUESTS)
    return events


def series_of(event_ticker):
    """Get the name of the series from the name of an event."""
    if not event_ticker:
        return None
    return event_ticker.split("-")[0]


def group_events(events):
    """Count the events of each series. Return a list of dictionaries."""
    counts = collections.Counter()
    categories = {}
    titles = {}
    for event in events:
        series = series_of(event.get("event_ticker"))
        if not series:
            continue
        counts[series] += 1
        category = event.get("category") or "(no category)"
        categories.setdefault(series, category)
        titles.setdefault(series, event.get("title") or "")
    rows = [{"series": series, "events": counts[series],
             "category": categories[series], "title": titles[series]}
            for series in counts]
    rows.sort(key=lambda item: (item["category"], -item["events"], item["series"]))
    return rows


def keep_category(rows, part):
    """Keep the series of one category. The part is a piece of the name."""
    if not part:
        return rows
    needle = part.lower()
    return [row for row in rows if needle in row["category"].lower()]


def print_table(rows):
    """Print one line for each series."""
    if not rows:
        print("The program found no series.", file=sys.stderr)
        return
    width = max(len(row["series"]) for row in rows)
    category = None
    for row in rows:
        if row["category"] != category:
            category = row["category"]
            print(f"\n{category}")
            print("-" * (width + 30))
        print(f"  {row['series']:<{width}}  {row['events']:>4} event(s)  "
              f"{row['title'][:50]}")


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Find the series of Kalshi and print their tickers.")
    parser.add_argument("--category",
                        help="Keep the categories that contain this text. An "
                             "example is weather.")
    parser.add_argument("--status", default="open",
                        help="The status of the events. Use all for each event. "
                             "Default: %(default)s")
    parser.add_argument("--tickers", action="store_true",
                        help="Print only the tickers, in one line. Use this "
                             "line after the option --series.")
    parser.add_argument("--base-url", default=BASE_URL, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    global BASE_URL
    args = parse_args(argv)
    BASE_URL = args.base_url

    session = requests.Session()
    session.headers.update({"Accept": "application/json",
                            "User-Agent": "kalshi-market-collector/1.0 (series)"})
    try:
        events = fetch_events(session, args.status)
    except requests.RequestException as exc:
        _log(f"The request failed: {exc}")
        return 1

    rows = keep_category(group_events(events), args.category)
    if not rows:
        _log(f"The program found no series for the category {args.category!r}.")
        _log("Run the program again without the option --category.")
        return 1

    if args.tickers:
        print(" ".join(row["series"] for row in rows))
    else:
        print_table(rows)
        print(f"\n{len(rows)} series of {len(events)} event(s).")
        print("To collect all of them, use this command:")
        print("  python3 kalshi_collector.py --series "
              + " ".join(row["series"] for row in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
