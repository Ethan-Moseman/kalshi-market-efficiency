#!/usr/bin/env python3
"""Measure the calibration of the Kalshi markets. Use no library.

A price is a forecast. A price of 70 cents says "this event comes 70 percent of
the time". This program tests that forecast against the answer.

The program needs two groups of files:
    data/history/settlements_<series>.csv   (from settlements.py)
    data/history/candles_<series>.csv       (from backfill.py)

The first file gives the answer of each market. The second file gives the price
of each minute. The program joins the two files with the ticker.

CAUTION: The program takes the price at a fixed time BEFORE the close. It does
not take the last price. At the close, the price is almost 0 or almost 100. A
calibration of the last price looks perfect and measures nothing. The option
--hours sets that time. The default is 6 hours before the close.

THE SIZE OF THE SAMPLE

The markets of one event are not independent. The event KXHIGHNY-26SEP01 has the
markets T85, T88 and T90. One temperature decides all three. So three lines are
one measurement, and not three measurements.

Because of this, the program counts two numbers:
    contracts: the number of markets.
    events:    the number of independent measurements.

The second number decides the precision. The program uses it for each error.

Examples:
    python3 calibration.py                              # The series KXHIGHNY.
    python3 calibration.py --series KXHIGHNY KXHIGHCHI  # Two series together.
    python3 calibration.py --hours 12                   # A longer horizon.
    python3 calibration.py --bin-width 20               # Five wide bins.
"""

import argparse
import csv
import math
import os
import sys

DEFAULT_DATA_DIR = os.path.join("data", "history")
DEFAULT_SERIES = ["KXHIGHNY"]
DEFAULT_HOURS = 6.0
DEFAULT_BIN_WIDTH = 10

SECONDS_IN_ONE_HOUR = 3600

# A bin needs this number of events before the program calls the result solid.
# Below this number the error is too large to separate a good market from a bad
# market. See the function print_verdict.
EVENTS_FOR_A_SOLID_BIN = 100


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def to_float(text):
    """Change a text into a number. Return None after an empty or bad value."""
    if text is None or str(text).strip() == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_settlements(path):
    """Read the answers. Return a dict of ticker to the data of the market."""
    answers = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            ticker = (line.get("ticker") or "").strip()
            outcome = to_float(line.get("result_binary"))
            if not ticker or outcome is None:
                continue
            close_ts = to_float(line.get("close_ts"))
            if close_ts is None:
                continue
            answers[ticker] = {
                "event": (line.get("event_ticker") or ticker).strip(),
                "outcome": outcome,
                "close_ts": close_ts,
            }
    return answers


def candle_mid(line):
    """Get the middle price of one candlestick. Return None without a price."""
    bid = to_float(line.get("yes_bid_close"))
    ask = to_float(line.get("yes_ask_close"))
    if bid is None and ask is None:
        return None
    if bid is None:
        return ask
    if ask is None:
        return bid
    return (bid + ask) / 2.0


def read_prices_at_horizon(path, answers, horizon_seconds):
    """Read the candlesticks. Give the last price before the horizon.

    The horizon is a time before the close of each market. The program keeps
    the newest candlestick that is not after that time.
    """
    best = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            ticker = (line.get("ticker") or "").strip()
            answer = answers.get(ticker)
            if answer is None:
                continue
            end_ts = to_float(line.get("end_period_ts"))
            if end_ts is None:
                continue
            if end_ts > answer["close_ts"] - horizon_seconds:
                continue
            mid = candle_mid(line)
            if mid is None:
                continue
            if ticker not in best or end_ts > best[ticker][0]:
                best[ticker] = (end_ts, mid)
    return {ticker: mid for ticker, (_ts, mid) in best.items()}


def build_observations(data_dir, series_list, horizon_seconds):
    """Join the answers and the prices. Return a list of observations."""
    observations = []
    missing = 0
    for series_ticker in series_list:
        answer_path = os.path.join(data_dir, f"settlements_{series_ticker}.csv")
        candle_path = os.path.join(data_dir, f"candles_{series_ticker}.csv")
        if not os.path.exists(answer_path):
            _log(f"{series_ticker}: the file {answer_path} is not present. "
                 "Run settlements.py first.")
            continue
        if not os.path.exists(candle_path):
            _log(f"{series_ticker}: the file {candle_path} is not present. "
                 "Run backfill.py first.")
            continue

        answers = read_settlements(answer_path)
        prices = read_prices_at_horizon(candle_path, answers, horizon_seconds)
        for ticker, answer in answers.items():
            price = prices.get(ticker)
            if price is None:
                missing += 1
                continue
            observations.append({
                "ticker": ticker,
                "event": answer["event"],
                "series": series_ticker,
                "price": price,
                "outcome": answer["outcome"],
            })
    return observations, missing


def clustered_error(rows, mean_value, key):
    """Give the error of a mean. Group the lines of one event together.

    The markets of one event move together. A normal error makes the sample
    look larger than it is. This error uses the sum of each group.
    """
    if not rows:
        return None
    groups = {}
    for row in rows:
        groups.setdefault(row["event"], []).append(row[key] - mean_value)
    total = sum(sum(part) ** 2 for part in groups.values())
    count = len(rows)
    if count == 0 or total <= 0:
        return 0.0
    return math.sqrt(total) / count


def smallest_error(realized, events):
    """Give the smallest error that a bin can have.

    A bin where each market gave the same answer is a special case. The error
    of the group above is then 0. An error of 0 is not true: a bin with 6
    events that all gave YES does not prove that the price is wrong.

    This function adds two YES and two NO to the count. Then it makes the
    error from the number of EVENTS. The result is never 0. Statisticians call
    this correction the interval of Agresti and Coull.
    """
    if not events:
        return None
    adjusted = (realized * events + 2.0) / (events + 4.0)
    return math.sqrt(adjusted * (1.0 - adjusted) / events)


def bin_error(rows, realized, events):
    """Give the error of one bin. Use the larger of the two errors."""
    grouped = clustered_error(rows, realized, "outcome")
    floor = smallest_error(realized, events)
    if grouped is None:
        return floor
    if floor is None:
        return grouped
    return max(grouped, floor)


def make_bins(observations, bin_width):
    """Put each observation into a bin of the price. Return a list of bins."""
    bins = {}
    for row in observations:
        cents = row["price"] * 100.0
        index = min(int(cents // bin_width), (100 // bin_width) - 1)
        bins.setdefault(index, []).append(row)

    result = []
    for index in sorted(bins):
        rows = bins[index]
        count = len(rows)
        mean_price = sum(r["price"] for r in rows) / count
        realized = sum(r["outcome"] for r in rows) / count
        events = len({r["event"] for r in rows})
        result.append({
            "low": index * bin_width,
            "high": (index + 1) * bin_width,
            "contracts": count,
            "events": events,
            "mean_price": mean_price,
            "realized": realized,
            "error": bin_error(rows, realized, events),
        })
    return result


def brier_score(observations):
    """Give the Brier score. A small score is a good forecast."""
    if not observations:
        return None
    return sum((r["price"] - r["outcome"]) ** 2 for r in observations) / len(observations)


def print_table(bins, bin_width):
    """Print one line for each bin of the price."""
    print()
    print(f"{'price bin':>12}  {'contracts':>9}  {'events':>6}  "
          f"{'forecast':>8}  {'realized':>8}  {'error':>7}  {'gap':>7}")
    print("-" * 74)
    for row in bins:
        error = row["error"]
        error_text = f"{error * 100:6.1f}" if error is not None else "     -"
        gap = (row["realized"] - row["mean_price"]) * 100
        mark = " " if error is None or abs(gap) <= 2 * error * 100 else "*"
        print(f"{row['low']:5d}-{row['high']:<3d}¢  "
              f"{row['contracts']:9d}  {row['events']:6d}  "
              f"{row['mean_price'] * 100:7.1f}¢  "
              f"{row['realized'] * 100:7.1f}%  "
              f"{error_text}  {gap:+6.1f}{mark}")
    print()
    print("forecast : the mean price of the bin. This is what the market said.")
    print("realized : the part of the markets of the bin that gave YES.")
    print("error    : the error of `realized`, with a group for each event.")
    print("gap      : realized - forecast. A star marks a gap of more than 2 errors.")


def print_verdict(observations, bins):
    """Say if the data is large enough. Answer the question 'is it too early'."""
    events = len({r["event"] for r in observations})
    contracts = len(observations)
    solid = [b for b in bins if b["events"] >= EVENTS_FOR_A_SOLID_BIN]

    print()
    print("=" * 74)
    print("IS THE DATA LARGE ENOUGH?")
    print("=" * 74)
    print(f"contracts (markets)            : {contracts}")
    print(f"events (independent measurements): {events}")
    print(f"bins with {EVENTS_FOR_A_SOLID_BIN}+ events         : "
          f"{len(solid)} of {len(bins)}")

    if events == 0:
        print()
        print("There is no data. Run settlements.py and backfill.py first.")
        return

    typical = 0.5 / math.sqrt(events / max(len(bins), 1))
    print(f"typical error of one bin       : near {typical * 100:.1f} points")
    print()

    if len(solid) >= max(len(bins) - 2, 1):
        print("The data is large enough. Each bin has a small error.")
        print("You can compare the forecast with the answer.")
    elif events >= EVENTS_FOR_A_SOLID_BIN:
        print("The data is in the middle. The wide bins give a result. The")
        print("narrow bins do not. Use the option --bin-width 25 for a first")
        print("result. Then collect more events.")
    else:
        print("The data is TOO SMALL. The error of each bin is larger than the")
        print("effect that you want to measure.")
        print()
        print("Do not wait for the collector. The collector gives a few events")
        print("each day. Use settlements.py with the option --days. Kalshi keeps")
        print("the past markets. One command gives more events than one month")
        print("of the collector:")
        print()
        print("    python3 settlements.py --series <ALL YOUR SERIES> --days 90")
        print("    python3 backfill.py --status settled --days 90 "
              "--series <ALL YOUR SERIES>")


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Measure the calibration of the Kalshi markets.")
    parser.add_argument("--series", nargs="+", default=DEFAULT_SERIES,
                        metavar="TICKER",
                        help="The series. Give one name or more. Default: %s"
                             % " ".join(DEFAULT_SERIES))
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                        help="Take the price this number of hours before the "
                             "close. Default: %(default)s")
    parser.add_argument("--bin-width", type=int, default=DEFAULT_BIN_WIDTH,
                        help="The width of one bin, in cents. Default: %(default)s")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder of the CSV files. Default: %(default)s")
    args = parser.parse_args(argv)
    if args.hours < 0:
        parser.error("--hours must not be less than 0")
    if args.bin_width <= 0 or 100 % args.bin_width != 0:
        parser.error("--bin-width must divide 100. Use 5, 10, 20 or 25.")
    return args


def main(argv=None):
    args = parse_args(argv)
    horizon_seconds = args.hours * SECONDS_IN_ONE_HOUR

    observations, missing = build_observations(args.data_dir, args.series,
                                               horizon_seconds)
    if not observations:
        _log("The program found no observation. It needs the two files of "
             "settlements.py and backfill.py.")
        return 1

    if missing:
        _log(f"NOTE: {missing} market(s) have an answer but no price at "
             f"{args.hours} hours before the close. The program did not use them.")

    bins = make_bins(observations, args.bin_width)
    score = brier_score(observations)

    print()
    print(f"series   : {' '.join(args.series)}")
    print(f"horizon  : {args.hours} hour(s) before the close")
    print(f"Brier    : {score:.4f}   (0 is perfect. 0.25 is a coin.)")

    print_table(bins, args.bin_width)
    print_verdict(observations, bins)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
