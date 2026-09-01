#!/usr/bin/env python3
"""Make an HTML report from the collected data. Open the report in a browser.

The report has four parts:

1. The numbers of the collection: markets, changes, spread and round-trip time.
2. The ladder test. This test finds an arbitrage between two strikes of the same
   event. The market with the higher strike must have the lower price. A market
   that breaks this rule gives a profit without a risk.
3. One line for each market, with a small graph of the middle price.
4. Two graphs: the distribution of the spread, and the activity for each hour.

The program writes one file: report.html. The file contains all data and all
graphs. It needs no library and no connection to the internet.

Examples:
    python3 make_report.py                 # Make report.html.
    python3 make_report.py --open          # Also open it in the browser.
    python3 make_report.py --out page.html # Use a different name.
"""

import argparse
import collections
import html
import os
import re
import statistics
import subprocess
import sys
import time

import read_data as rd

DEFAULT_OUT = "report.html"
REFRESH_SECONDS = 60

# The strike is the number at the end of the ticker. The letter before the
# number gives the type of the market. An example is KXHIGHNY-26SEP01-T90.
STRIKE_PATTERN = re.compile(r"-([A-Z])(\d+(?:\.\d+)?)$")

# The colors come from the validated palette of the data-viz guidance.
COLORS = {
    "series": ("#2a78d6", "#3987e5"),
    "good": ("#0ca30c", "#0ca30c"),
    "critical": ("#d03b3b", "#d03b3b"),
}


# ----------------------------------------------------------------- the numbers

def strike_of(ticker):
    """Get the type and the strike of a ticker. Return None if it has none."""
    found = STRIKE_PATTERN.search(ticker or "")
    if not found:
        return None
    return found.group(1), float(found.group(2))


def quote_series(lines):
    """Make the series of the time, the middle price and the spread."""
    points = []
    for line in lines:
        bid = rd.to_float(line["yes_bid"])
        ask = rd.to_float(line["yes_ask"])
        if bid is None or ask is None:
            continue
        points.append((int(line["recv_ts_ns"]), (bid + ask) / 2.0, ask - bid))
    return points


def ladder_violations(lines):
    """Find each moment with an arbitrage between two strikes.

    The market with the higher strike must not have the higher price. If the bid
    of the higher strike is above the ask of the lower strike, a trader buys the
    lower strike and sells the higher strike. The profit has no risk.

    The program walks through the lines in the order of the time. It keeps the
    last quote of each market. After each line it examines the pairs.
    """
    state = {}
    windows = {}
    results = []
    for line in sorted(lines, key=lambda item: int(line_time(item))):
        ticker = line["ticker"]
        strike = strike_of(ticker)
        if strike is None:
            continue
        bid = rd.to_float(line["yes_bid"])
        ask = rd.to_float(line["yes_ask"])
        if bid is None or ask is None:
            continue
        state[ticker] = (line["event_ticker"], strike[0], strike[1], bid, ask)

        moment = int(line_time(line))
        by_event = collections.defaultdict(list)
        for name, (event, kind, value, market_bid, market_ask) in state.items():
            by_event[(event, kind)].append((value, name, market_bid, market_ask))

        live = set()
        for markets in by_event.values():
            markets.sort()
            for i in range(len(markets) - 1):
                low_strike, low_name, _low_bid, low_ask = markets[i]
                high_strike, high_name, high_bid, _high_ask = markets[i + 1]
                edge = high_bid - low_ask
                if edge <= 0:
                    continue
                key = (low_name, high_name)
                live.add(key)
                window = windows.get(key)
                if window is None:
                    windows[key] = {"low": low_name, "high": high_name,
                                    "low_strike": low_strike,
                                    "high_strike": high_strike,
                                    "start": moment, "end": moment, "edge": edge}
                else:
                    window["end"] = moment
                    window["edge"] = max(window["edge"], edge)
        for key in list(windows):
            if key not in live:
                results.append(windows.pop(key))
    results.extend(windows.values())
    results.sort(key=lambda item: item["edge"], reverse=True)
    return results


def line_time(line):
    """Get the time of one line."""
    return line["recv_ts_ns"]


def hour_counts(lines):
    """Count the changes for each hour of the day."""
    counts = collections.Counter()
    for line in lines:
        moment = time.localtime(int(line["recv_ts_ns"]) / rd.NS_IN_ONE_SECOND)
        counts[moment.tm_hour] += 1
    return [counts.get(hour, 0) for hour in range(24)]


def price_label(value):
    """Make a short text for a price. The text has no zero at the end."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def spread_histogram(lines, parts=12, max_groups=14):
    """Count the spreads. Return the labels and the counts.

    A market with a tick of one cent has few different spreads. Then each
    different spread gets its own group. A market with many different spreads
    gets groups of an equal size.
    """
    spreads = []
    for line in lines:
        bid = rd.to_float(line["yes_bid"])
        ask = rd.to_float(line["yes_ask"])
        if bid is not None and ask is not None:
            spreads.append(round(ask - bid, 4))
    if not spreads:
        return [], []

    different = sorted(set(spreads))
    if len(different) <= max_groups:
        counts = collections.Counter(spreads)
        return [price_label(value) for value in different], \
               [counts[value] for value in different]

    largest = max(spreads) or 0.01
    step = largest / parts
    counts = [0] * parts
    for value in spreads:
        index = min(int(value / step), parts - 1) if step else 0
        counts[index] += 1
    return [price_label(index * step) for index in range(parts)], counts


# ------------------------------------------------------------------ the graphs

def svg_open(width, height, extra=""):
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none" role="img" {extra}>')


def sparkline(points, width=180, height=32):
    """Make a small graph of the middle price of one market."""
    values = [value for _ts, value, _spread in points]
    if len(values) < 2:
        return '<span class="muted">-</span>'
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = width / (len(values) - 1)
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{i * step:.1f},"
        f"{height - 2 - (value - low) / span * (height - 4):.1f}"
        for i, value in enumerate(values))
    return (svg_open(width, height, 'class="spark"') +
            f'<title>{len(values)} changes, from {low:.4f} to {high:.4f} dollars</title>'
            f'<path d="{path}" fill="none" stroke="var(--series-1)" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def bar_chart(labels, values, unit="", height=180):
    """Make a chart with one bar for each value."""
    if not values or not any(values):
        return '<p class="muted">There is no data for this chart.</p>'
    width = 720
    largest = max(values)
    gap = 2
    slot = width / len(values)
    bars = []
    for index, value in enumerate(values):
        tall = (value / largest) * (height - 24) if largest else 0
        x = index * slot
        bars.append(
            f'<g><title>{html.escape(str(labels[index]))}{unit}: {value}</title>'
            f'<rect x="{x + gap / 2:.1f}" y="{height - 20 - tall:.1f}" '
            f'width="{max(slot - gap, 1):.1f}" height="{max(tall, 0):.1f}" rx="4" '
            'fill="var(--series-1)"/></g>')
    ticks = []
    for index, label in enumerate(labels):
        if len(labels) > 14 and index % 2:
            continue
        ticks.append(f'<text x="{index * slot + slot / 2:.1f}" y="{height - 6}" '
                     f'text-anchor="middle" class="tick">{html.escape(str(label))}</text>')
    return (svg_open(width, height) +
            f'<line x1="0" y1="{height - 20}" x2="{width}" y2="{height - 20}" '
            'stroke="var(--axis)" stroke-width="1"/>' +
            "".join(bars) + "".join(ticks) + "</svg>")


# ------------------------------------------------------------------- the page

def time_range(times):
    """Make the text of the first time and the last time.

    The text contains the date if the data covers more than one day.
    """
    if not times:
        return "-", "-"
    start, end = min(times), max(times)
    same_day = (time.localtime(start / rd.NS_IN_ONE_SECOND).tm_yday ==
                time.localtime(end / rd.NS_IN_ONE_SECOND).tm_yday)
    if same_day:
        return rd.clock(start), rd.clock(end)
    shape = "%m-%d %H:%M:%S"
    return (time.strftime(shape, time.localtime(start / rd.NS_IN_ONE_SECOND)),
            time.strftime(shape, time.localtime(end / rd.NS_IN_ONE_SECOND)))


def stat_tile(value, label, note=""):
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="tile"><div class="value">{html.escape(str(value))}</div>'
            f'<div class="label">{html.escape(label)}</div>{note_html}</div>')


def show(value, digits=4):
    """Make a text for one number. Show a dash if the number is absent."""
    return "-" if value is None else f"{value:.{digits}f}"


STYLE = """
:root {
  color-scheme: light;
  --plane: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7;
  --series-1: #2a78d6; --good: #0ca30c; --critical: #d03b3b;
  --ring: rgba(11,11,11,0.10);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plane: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835;
    --series-1: #3987e5; --ring: rgba(255,255,255,0.10);
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--plane); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1000px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 36px 0 12px; letter-spacing: .02em; }
.sub { color: var(--ink-2); margin: 0 0 24px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; }
.card, .tile { background: var(--surface); border: 1px solid var(--ring);
  border-radius: 10px; padding: 14px 16px; }
.tile .value { font-size: 26px; font-weight: 600; }
.tile .label { color: var(--ink-2); font-size: 12px; margin-top: 2px; }
.tile .note { color: var(--muted); font-size: 11px; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 8px 10px; border-bottom: 1px solid var(--grid);
  white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink-2); font-weight: 500; font-size: 12px; }
tbody tr:last-child td { border-bottom: none; }
.wrap { overflow-x: auto; }
.spark { display: block; width: 180px; }
.muted { color: var(--muted); }
.good { color: var(--good); font-weight: 600; }
.critical { color: var(--critical); font-weight: 600; }
.tick { fill: var(--muted); font-size: 10px; }
footer { color: var(--muted); font-size: 12px; margin-top: 40px; }
code { background: var(--surface); border: 1px solid var(--ring); border-radius: 4px;
  padding: 1px 5px; }
"""


def build_page(context):
    """Make the full text of the HTML page."""
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
        "<title>Kalshi market efficiency</title>",
        f"<style>{STYLE}</style></head><body><main>",
        "<h1>Kalshi market efficiency</h1>",
        f'<p class="sub">{html.escape(context["subtitle"])}</p>',
        '<div class="tiles">', "".join(context["tiles"]), "</div>",
        "<h2>The ladder test</h2>", context["ladder"],
        "<h2>Each market</h2>",
        '<div class="card wrap">', context["market_table"], "</div>",
        "<h2>The distribution of the spread</h2>",
        '<div class="card">', context["spread_chart"],
        '<p class="muted">The group of the spread, in dollars. The height is the '
        "number of changes.</p></div>",
        "<h2>The activity for each hour</h2>",
        '<div class="card">', context["hour_chart"],
        '<p class="muted">The local hour of the day. The height is the number of '
        "changes of a quote.</p></div>",
        f'<footer>{html.escape(context["footer"])}</footer>',
        "</main></body></html>",
    ]
    return "".join(parts)


def ladder_section(violations, market_count):
    """Make the part of the page for the ladder test."""
    if market_count < 2:
        return ('<div class="card"><p class="muted">The test needs two markets of '
                "one event or more.</p></div>")
    if not violations:
        return ('<div class="card"><p><span class="good">&#10003; No arbitrage.</span> '
                "Each market with a higher strike has a lower price. The ladder of "
                "the prices is correct in all data.</p></div>")
    rows = ["<table><thead><tr><th>Buy this market</th><th>Sell this market</th>"
            "<th>Edge</th><th>Start</th><th>End</th><th>Seconds</th>"
            "</tr></thead><tbody>"]
    for item in violations[:25]:
        seconds = (item["end"] - item["start"]) / rd.NS_IN_ONE_SECOND
        rows.append(
            f'<tr><td>{html.escape(item["low"])}</td>'
            f'<td>{html.escape(item["high"])}</td>'
            f'<td class="critical">{item["edge"]:.4f}</td>'
            f'<td>{rd.clock(item["start"])}</td><td>{rd.clock(item["end"])}</td>'
            f"<td>{seconds:.0f}</td></tr>")
    rows.append("</tbody></table>")
    return ('<div class="card wrap"><p><span class="critical">&#9888; '
            f"{len(violations)} arbitrage window(s).</span> A market with a higher "
            "strike had a higher price. A trader buys the first market at the ask. "
            "The same trader sells the second market at the bid. The edge is the "
            "profit for one contract, in dollars.</p>" + "".join(rows) + "</div>")


def market_table(summaries, points_by_ticker):
    """Make the table with one line for each market."""
    head = ("<table><thead><tr><th>Market</th><th>Middle price</th><th>Changes</th>"
            "<th>For each hour</th><th>Spread</th><th>Spread %</th>"
            "<th>Last middle</th><th>Volume</th><th>RTT ms</th>"
            "</tr></thead><tbody>")
    rows = []
    for item in summaries:
        points = points_by_ticker.get(item["ticker"], [])
        last_middle = points[-1][1] if points else None
        relative = None
        if item["mean_spread"] is not None and item["mean_middle"]:
            relative = item["mean_spread"] / item["mean_middle"]
        rows.append(
            f'<tr><td>{html.escape(item["ticker"])}</td>'
            f"<td>{sparkline(points)}</td>"
            f'<td>{item["lines"]}</td>'
            f'<td>{show(item["changes_per_hour"], 1)}</td>'
            f'<td>{show(item["mean_spread"])}</td>'
            f"<td>{f'{relative * 100:.1f}%' if relative is not None else '-'}</td>"
            f"<td>{show(last_middle)}</td>"
            f'<td>{show(item["last_volume"], 0)}</td>'
            f'<td>{show(item["median_rtt_ms"], 1)}</td></tr>')
    return head + "".join(rows) + "</tbody></table>"


def build_context(lines, paths, history_lines):
    """Calculate every value of the page."""
    groups = rd.group_by_ticker(lines)
    summaries = [rd.summarize(ticker, group) for ticker, group in groups.items()]
    summaries.sort(key=lambda item: item["lines"], reverse=True)
    points = {ticker: quote_series(group) for ticker, group in groups.items()}

    times = [int(line["recv_ts_ns"]) for line in lines]
    rtts = [value for value in (rd.to_float(line["rtt_ms"]) for line in lines)
            if value is not None]
    spreads = [item["mean_spread"] for item in summaries
               if item["mean_spread"] is not None]
    relatives = [item["mean_spread"] / item["mean_middle"] for item in summaries
                 if item["mean_spread"] is not None and item["mean_middle"]]
    violations = ladder_violations(lines)

    tiles = [
        stat_tile(len(summaries), "markets"),
        stat_tile(f"{len(lines):,}", "changes of a quote"),
        stat_tile(show(statistics.fmean(spreads)) if spreads else "-",
                  "mean spread", "dollars"),
        stat_tile(f"{statistics.fmean(relatives) * 100:.1f}%" if relatives else "-",
                  "spread of the middle price"),
        stat_tile(f"{statistics.median(rtts):.0f}" if rtts else "-",
                  "median round-trip time", "milliseconds"),
        stat_tile(len(violations), "arbitrage windows", "the ladder test"),
    ]
    if history_lines:
        tiles.append(stat_tile(f"{history_lines:,}", "lines of past data",
                               "candlesticks and trades"))

    labels, counts = spread_histogram(lines)
    first, last = time_range(times)
    return {
        "subtitle": f"{len(paths)} file(s). From {first} to {last}, local time.",
        "tiles": tiles,
        "ladder": ladder_section(violations, len(summaries)),
        "market_table": market_table(summaries, points),
        "spread_chart": bar_chart(labels, counts, " dollars"),
        "hour_chart": bar_chart([f"{hour:02d}" for hour in range(24)],
                                hour_counts(lines)),
        "footer": "Made by make_report.py at " +
                  time.strftime("%Y-%m-%d %H:%M:%S") +
                  ". The page reads itself again each minute.",
    }


def count_history(data_dir):
    """Count the lines of the past data. Return 0 if the folder is absent."""
    folder = os.path.join(data_dir, "history")
    if not os.path.isdir(folder):
        return 0
    total = 0
    for name in os.listdir(folder):
        if not name.endswith(".csv"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            total += max(sum(1 for _ in handle) - 1, 0)
    return total


def parse_args(argv=None):
    """Read the options from the command line."""
    parser = argparse.ArgumentParser(
        description="Make an HTML report from the collected data.")
    parser.add_argument("--data-dir", default=rd.DEFAULT_DATA_DIR,
                        help="The folder of the CSV files. Default: %(default)s")
    parser.add_argument("--series", help="Use the files of one series only.")
    parser.add_argument("--date", help="Use the files of one day only.")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="The name of the HTML file. Default: %(default)s")
    parser.add_argument("--open", action="store_true", dest="open_it",
                        help="Open the report in the browser after the program "
                             "writes it.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    paths = rd.find_files(args.data_dir, args.series, args.date)
    lines = rd.read_lines(paths) if paths else []
    if not lines:
        print(f"The folder {args.data_dir} contains no line of data.",
              file=sys.stderr)
        print("Run the collector first: python3 kalshi_collector.py",
              file=sys.stderr)
        return 1

    context = build_context(lines, paths, count_history(args.data_dir))
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(build_page(context))
    print(f"The program wrote {args.out} from {len(lines)} line(s).")

    if args.open_it:
        try:
            subprocess.run(["open", args.out], check=False)
        except OSError as exc:
            print(f"The program cannot open the file: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
