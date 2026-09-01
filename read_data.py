#!/usr/bin/env python3
"""Read the CSV files of the collector. Print a summary for each market.

The program calculates these values for each market:
1. The number of lines.
2. The first time and the last time.
3. The number of changes for each hour.
4. The mean spread. The spread is the difference between yes_ask and yes_bid.
5. The mean middle price. This price is the mean of yes_bid and yes_ask.
6. The last volume.
7. The median round-trip time.

A large spread and few changes show a market with a low efficiency.

This program uses no library. It needs no connection to the internet.

Examples:
    python3 read_data.py                       # All files in the folder data/.
    python3 read_data.py --series KXHIGHNY     # Only one series.
    python3 read_data.py --ticker KXHIGHNY-26SEP01-T90   # The lines of one market.
"""

import argparse
import csv
import glob
import os
import statistics
import sys
import time

DEFAULT_DATA_DIR = "data"
NS_IN_ONE_SECOND = 1_000_000_000
SECONDS_IN_ONE_HOUR = 3600.0


def find_files(data_dir, series=None, date=None):
    """Find the CSV files. Return a list of paths."""
    pattern = "%s_%s.csv" % (series or "*", date or "*")
    return sorted(glob.glob(os.path.join(data_dir, pattern)))


def read_lines(paths):
    """Read all lines of all files. Return a list of dictionaries."""
    lines = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as handle:
            for line in csv.DictReader(handle):
                lines.append(line)
    return lines


def to_float(text):
    """Change a text into a number. Return None if the text is empty."""
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clock(recv_ts_ns):
    """Change a time in nanoseconds into the local time of the day."""
    return time.strftime("%H:%M:%S", time.localtime(recv_ts_ns / NS_IN_ONE_SECOND))


def group_by_ticker(lines):
    """Put the lines of each market together. Return a dictionary."""
    groups = {}
    for line in lines:
        groups.setdefault(line["ticker"], []).append(line)
    for group in groups.values():
        group.sort(key=lambda line: int(line["recv_ts_ns"]))
    return groups


def summarize(ticker, lines):
    """Calculate the values of one market. Return a dictionary."""
    times = [int(line["recv_ts_ns"]) for line in lines]
    spreads = []
    middles = []
    for line in lines:
        bid = to_float(line["yes_bid"])
        ask = to_float(line["yes_ask"])
        if bid is None or ask is None:
            continue
        spreads.append(ask - bid)
        middles.append((ask + bid) / 2.0)
    rtts = [value for value in (to_float(line["rtt_ms"]) for line in lines)
            if value is not None]
    duration_s = (max(times) - min(times)) / NS_IN_ONE_SECOND
    changes_per_hour = None
    if duration_s > 0:
        changes_per_hour = (len(lines) - 1) * SECONDS_IN_ONE_HOUR / duration_s
    return {
        "ticker": ticker,
        "lines": len(lines),
        "first": clock(min(times)),
        "last": clock(max(times)),
        "duration_s": duration_s,
        "changes_per_hour": changes_per_hour,
        "mean_spread": statistics.fmean(spreads) if spreads else None,
        "mean_middle": statistics.fmean(middles) if middles else None,
        "last_volume": to_float(lines[-1]["volume"]),
        "median_rtt_ms": statistics.median(rtts) if rtts else None,
    }


def show(value, digits=4, width=10):
    """Make a text for one value. Show a dash if the value is absent."""
    if value is None:
        return "-".rjust(width)
    return f"{value:.{digits}f}".rjust(width)


def print_table(summaries):
    """Print one line for each market."""
    header = (f"{'ticker':<26}{'lines':>7}{'first':>10}{'last':>10}"
              f"{'chg/hour':>10}{'spread':>10}{'middle':>10}{'volume':>10}"
              f"{'rtt ms':>9}")
    print(header)
    print("-" * len(header))
    for item in summaries:
        print(f"{item['ticker']:<26}{item['lines']:>7}{item['first']:>10}"
              f"{item['last']:>10}{show(item['changes_per_hour'], 1, 10)}"
              f"{show(item['mean_spread'], 4, 10)}{show(item['mean_middle'], 4, 10)}"
              f"{show(item['last_volume'], 0, 10)}{show(item['median_rtt_ms'], 1, 9)}")


def print_total(paths, lines, summaries):
    """Print the values of all markets together."""
    rtts = [value for value in (to_float(line["rtt_ms"]) for line in lines)
            if value is not None]
    spreads = [item["mean_spread"] for item in summaries
               if item["mean_spread"] is not None]
    print()
    print(f"files    : {len(paths)}")
    print(f"markets  : {len(summaries)}")
    print(f"lines    : {len(lines)}")
    if rtts:
        print(f"rtt ms   : median {statistics.median(rtts):.1f}, "
              f"minimum {min(rtts):.1f}, maximum {max(rtts):.1f}")
    if spreads:
        print(f"spread   : mean {statistics.fmean(spreads):.4f} dollars")


def print_market(ticker, lines):
    """Print each line of one market."""
    print(f"{'time':<10}{'yes_bid':>9}{'yes_ask':>9}{'no_bid':>9}{'no_ask':>9}"
          f"{'volume':>10}{'rtt ms':>9}")
    print("-" * 65)
    for line in lines:
        print(f"{clock(int(line['recv_ts_ns'])):<10}"
              f"{line['yes_bid']:>9}{line['yes_ask']:>9}"
              f"{line['no_bid']:>9}{line['no_ask']:>9}"
              f"{line['volume']:>10}{line['rtt_ms']:>9}")
    print(f"\nlines: {len(lines)}")


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Read the CSV files of the collector. Print a summary.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="The folder of the CSV files. Default: %(default)s")
    parser.add_argument("--series", help="Read the files of one series only.")
    parser.add_argument("--date", help="Read the files of one day only. "
                                       "Use the form YYYY-MM-DD.")
    parser.add_argument("--ticker", help="Print each line of one market.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    paths = find_files(args.data_dir, args.series, args.date)
    if not paths:
        print(f"The folder {args.data_dir} contains no CSV file.", file=sys.stderr)
        print("Run the collector first: python3 kalshi_collector.py", file=sys.stderr)
        return 1

    lines = read_lines(paths)
    if not lines:
        print("The files contain no line of data.", file=sys.stderr)
        return 1

    groups = group_by_ticker(lines)
    if args.ticker:
        if args.ticker not in groups:
            print(f"The files contain no market with the name {args.ticker}.",
                  file=sys.stderr)
            return 1
        print_market(args.ticker, groups[args.ticker])
        return 0

    summaries = [summarize(ticker, group) for ticker, group in groups.items()]
    summaries.sort(key=lambda item: item["lines"], reverse=True)
    print_table(summaries)
    print_total(paths, lines, summaries)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # The reader of the output stopped. An example is the program head. The
        # next two lines stop the message about a broken pipe.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
