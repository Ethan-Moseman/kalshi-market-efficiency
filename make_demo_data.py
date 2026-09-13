#!/usr/bin/env python3
"""Make synthetic data for a test of the programs. Use no library.

The README shows a table of the calibration. The numbers of that table come
from this program. They are not a finding. They are a test.

The program makes a market that is correct by construction:

1. Each event has one temperature. The program takes that temperature from a
   normal distribution.
2. Each event has six markets. Each market has a different strike.
3. The price of each market is the true probability of its strike.

A correct program must find a small gap in each bin of this data. A program
that counts the contracts and not the events finds an error that is too small.
Because of this, the data is also a test of the error.

Examples:
    python3 make_demo_data.py                    # 720 events.
    python3 make_demo_data.py --events 24        # A small sample.
    python3 make_demo_data.py --series demo_big  # A different name.

After the program, measure the calibration of the synthetic data:

    python3 calibration.py --series demo
"""

import argparse
import csv
import math
import os
import random

DEFAULT_EVENTS = 720
DEFAULT_SERIES = "demo"
DEFAULT_DATA_DIR = os.path.join("data", "history")
DEFAULT_SEED = 7

# The strikes of one event, in degrees Fahrenheit.
STRIKES = [80, 83, 85, 88, 90, 93]

# The mean temperature of an event comes from this range. The spread of the
# forecast is SIGMA degrees.
LOW_MEAN = 78.0
HIGH_MEAN = 92.0
SIGMA = 3.0

FIRST_CLOSE_TS = 1788325200
SECONDS_IN_ONE_DAY = 86400
HORIZON_SECONDS = 7 * 3600

SETTLEMENT_FIELDS = ["ticker", "event_ticker", "result_binary", "close_ts"]
CANDLE_FIELDS = ["ticker", "end_period_ts", "yes_bid_close", "yes_ask_close"]


def normal_cdf(x):
    """Give the probability of a value below x, for a normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def make_rows(events, seed=DEFAULT_SEED):
    """Make the two groups of lines. Return (settlements, candles)."""
    random.seed(seed)
    settlements = []
    candles = []
    for index in range(events):
        event_ticker = f"DEMO-{index:05d}"
        close_ts = FIRST_CLOSE_TS - index * SECONDS_IN_ONE_DAY

        # One temperature decides each market of this event. This is the
        # group that the calibration must not break apart.
        mean = random.uniform(LOW_MEAN, HIGH_MEAN)
        actual = random.gauss(mean, SIGMA)

        for strike in STRIKES:
            ticker = f"{event_ticker}-T{strike}"
            probability = 1.0 - normal_cdf((strike - mean) / SIGMA)
            probability = min(max(probability, 0.01), 0.99)
            settlements.append({
                "ticker": ticker,
                "event_ticker": event_ticker,
                "result_binary": 1 if actual > strike else 0,
                "close_ts": close_ts,
            })
            candles.append({
                "ticker": ticker,
                "end_period_ts": close_ts - HORIZON_SECONDS,
                "yes_bid_close": f"{probability - 0.005:.4f}",
                "yes_ask_close": f"{probability + 0.005:.4f}",
            })
    return settlements, candles


def write_csv(path, fields, rows):
    """Write one CSV file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Make synthetic data for a test of the programs.")
    parser.add_argument("--events", type=int, default=DEFAULT_EVENTS,
                        help="The number of events. Default: %(default)s")
    parser.add_argument("--series", default=DEFAULT_SERIES,
                        help="The name of the series. Default: %(default)s")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="The seed of the random numbers. Default: %(default)s")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder for the CSV files. Default: %(default)s")
    args = parser.parse_args(argv)
    if args.events <= 0:
        parser.error("--events must be more than 0")
    return args


def main(argv=None):
    args = parse_args(argv)
    settlements, candles = make_rows(args.events, args.seed)

    settlement_path = os.path.join(args.data_dir, f"settlements_{args.series}.csv")
    candle_path = os.path.join(args.data_dir, f"candles_{args.series}.csv")
    write_csv(settlement_path, SETTLEMENT_FIELDS, settlements)
    write_csv(candle_path, CANDLE_FIELDS, candles)

    print(f"{settlement_path}: {len(settlements)} line(s).")
    print(f"{candle_path}: {len(candles)} line(s).")
    print(f"events: {args.events}. contracts: {len(settlements)}.")
    print()
    print("CAUTION: This data is synthetic. It is a test and not a finding.")
    print("To measure it, use this command:")
    print(f"    python3 calibration.py --series {args.series}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
