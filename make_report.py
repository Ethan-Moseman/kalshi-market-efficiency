#!/usr/bin/env python3
"""Make an HTML report from the collected data. Open the report in a browser.

The page shows the measurements of a trading desk:

1. The session: the first tick, the last tick, the age of the last tick and the
   longest quiet time.
2. The spread in cents and in basis points. The mean has a weight of the time.
   A quote that stays for 10 minutes has more weight than a quote that stays for
   1 second.
3. The interval between two changes of a quote, in seconds.
4. The round-trip time at the percentile 50, 95 and 99.
5. The ladder test. It finds an arbitrage between two strikes of one event.

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

# A quiet time longer than this value is a possible stop of the collector.
QUIET_LIMIT_SECONDS = 120

# The strike is the number at the end of the ticker. The letter before the
# number gives the type of the market. An example is KXHIGHNY-26SEP01-T90.
STRIKE_PATTERN = re.compile(r"-([A-Z])(\d+(?:\.\d+)?)$")


# ------------------------------------------------------------- the text of a value

def cents(dollars, digits=1):
    """Make a text in cents. The API gives the price in dollars."""
    if dollars is None:
        return "–"
    return f"{dollars * 100:.{digits}f}"


def basis_points(part, whole):
    """Make a text in basis points. 100 basis points are 1 percent."""
    if part is None or not whole:
        return "–"
    return f"{part / whole * 10000:.0f}"


def duration(seconds):
    """Make a short text for a number of seconds."""
    if seconds is None:
        return "–"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def number(value, digits=0):
    """Make a text for a number with a separator for the thousands."""
    if value is None:
        return "–"
    return f"{value:,.{digits}f}"


def price_label(value):
    """Make a short text for a price. The text has no zero at the end."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def percentile(values, part):
    """Get one percentile of a list. The part is a number between 0 and 1."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(part * (len(ordered) - 1) + 0.5), len(ordered) - 1)
    return ordered[index]


def clock(ns):
    """Make the local time of the day from a time in nanoseconds."""
    return time.strftime("%H:%M:%S", time.localtime(ns / rd.NS_IN_ONE_SECOND))


def time_range(times):
    """Make the text of the first time and the last time.

    The text contains the date if the data covers more than one day.
    """
    if not times:
        return "–", "–"
    start, end = min(times), max(times)
    same_day = (time.localtime(start / rd.NS_IN_ONE_SECOND).tm_yday ==
                time.localtime(end / rd.NS_IN_ONE_SECOND).tm_yday)
    if same_day:
        return clock(start), clock(end)
    shape = "%m-%d %H:%M:%S"
    return (time.strftime(shape, time.localtime(start / rd.NS_IN_ONE_SECOND)),
            time.strftime(shape, time.localtime(end / rd.NS_IN_ONE_SECOND)))


# ------------------------------------------------------------------ the numbers

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


def market_metrics(ticker, lines, session_end_ns):
    """Calculate the measurements of one market."""
    points = quote_series(lines)
    times = [int(line["recv_ts_ns"]) for line in lines]
    rtts = [value for value in (rd.to_float(line["rtt_ms"]) for line in lines)
            if value is not None]

    # The mean spread gets a weight of the time. A quote holds until the next
    # change. Because of this, a long quote has more weight than a short quote.
    weighted_sum = 0.0
    total_time = 0.0
    for index, (moment, _middle, spread) in enumerate(points):
        if index + 1 < len(points):
            hold = points[index + 1][0] - moment
        else:
            hold = max(session_end_ns - moment, 0)
        hold /= rd.NS_IN_ONE_SECOND
        weighted_sum += spread * hold
        total_time += hold
    time_spread = weighted_sum / total_time if total_time else None

    gaps = [(times[i + 1] - times[i]) / rd.NS_IN_ONE_SECOND
            for i in range(len(times) - 1)] if len(times) > 1 else []
    span = (max(times) - min(times)) / rd.NS_IN_ONE_SECOND if len(times) > 1 else 0

    return {
        "ticker": ticker,
        "points": points,
        "updates": len(lines),
        "updates_per_hour": (len(lines) - 1) * 3600 / span if span > 0 else None,
        "mean_spread": statistics.fmean([p[2] for p in points]) if points else None,
        "time_spread": time_spread,
        "last_middle": points[-1][1] if points else None,
        "last_spread": points[-1][2] if points else None,
        "gap_p50": percentile(gaps, 0.50),
        "gap_p95": percentile(gaps, 0.95),
        "last_age_s": (session_end_ns - max(times)) / rd.NS_IN_ONE_SECOND if times else None,
        "volume": rd.to_float(lines[-1]["volume"]),
        "open_interest": rd.to_float(lines[-1]["open_interest"]),
        "rtt_p50": percentile(rtts, 0.50),
    }


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
    for line in sorted(lines, key=lambda item: int(item["recv_ts_ns"])):
        ticker = line["ticker"]
        strike = strike_of(ticker)
        if strike is None:
            continue
        bid = rd.to_float(line["yes_bid"])
        ask = rd.to_float(line["yes_ask"])
        if bid is None or ask is None:
            continue
        state[ticker] = (line["event_ticker"], strike[0], strike[1], bid, ask)

        moment = int(line["recv_ts_ns"])
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


def hour_counts(lines):
    """Count the changes for each hour of the day."""
    counts = collections.Counter()
    for line in lines:
        moment = time.localtime(int(line["recv_ts_ns"]) / rd.NS_IN_ONE_SECOND)
        counts[moment.tm_hour] += 1
    return [counts.get(hour, 0) for hour in range(24)]


def session_activity(lines, parts=96):
    """Count the changes in equal parts of the session."""
    times = sorted(int(line["recv_ts_ns"]) for line in lines)
    if len(times) < 2:
        return [], []
    start, end = times[0], times[-1]
    step = (end - start) / parts or 1
    counts = [0] * parts
    for moment in times:
        counts[min(int((moment - start) / step), parts - 1)] += 1
    labels = []
    for index in range(parts):
        if index % (parts // 6) == 0:
            labels.append(clock(start + index * step))
        else:
            labels.append("")
    return labels, counts


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
        return [cents(value) for value in different], \
               [counts[value] for value in different]

    largest = max(spreads) or 0.01
    step = largest / parts
    counts = [0] * parts
    for value in spreads:
        index = min(int(value / step), parts - 1) if step else 0
        counts[index] += 1
    return [cents(index * step) for index in range(parts)], counts


def quiet_times(lines):
    """Find the longest time without a change of a quote."""
    times = sorted(int(line["recv_ts_ns"]) for line in lines)
    if len(times) < 2:
        return None, 0
    gaps = [(times[i + 1] - times[i]) / rd.NS_IN_ONE_SECOND
            for i in range(len(times) - 1)]
    long_gaps = [gap for gap in gaps if gap > QUIET_LIMIT_SECONDS]
    return max(gaps), len(long_gaps)


# ------------------------------------------------------------------- the graphs

def svg_open(width, height, extra=""):
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none" role="img" {extra}>')


def sparkline(points, width=140, height=26):
    """Make a small graph of the middle price of one market."""
    values = [value for _ts, value, _spread in points]
    if len(values) < 2:
        return '<span class="dim">–</span>'
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = width / (len(values) - 1)
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{i * step:.1f},"
        f"{height - 2 - (value - low) / span * (height - 4):.1f}"
        for i, value in enumerate(values))
    return (svg_open(width, height, 'class="spark"') +
            f"<title>{len(values)} updates · {cents(low)} to {cents(high)} cents</title>"
            f'<path d="{path}" fill="none" stroke="var(--series-1)" stroke-width="1.5" '
            'stroke-linejoin="round" stroke-linecap="round"/></svg>')


MAX_BAR_WIDTH = 56


def bar_chart(labels, values, unit="", height=150, radius=2):
    """Make a chart with one bar for each value.

    A chart with few values keeps a limit on the width of a bar. Then two bars
    do not become two large blocks. The group of the bars stays in the middle.
    """
    if not values or not any(values):
        return '<p class="dim">There is no data for this chart.</p>'
    width = 640
    largest = max(values)
    gap = 2 if len(values) < 40 else 1
    slot = min(width / len(values), MAX_BAR_WIDTH + gap)
    left = (width - slot * len(values)) / 2
    plot = height - 18
    bars = []
    for index, value in enumerate(values):
        tall = (value / largest) * (plot - 4) if largest else 0
        label = labels[index] if index < len(labels) else str(index)
        bars.append(
            f'<g><title>{html.escape(str(label) or str(index))}{unit}: {value}</title>'
            f'<rect x="{left + index * slot + gap / 2:.1f}" y="{plot - tall:.1f}" '
            f'width="{max(slot - gap, 0.8):.1f}" height="{max(tall, 0.8):.1f}" '
            f'rx="{radius}" fill="var(--series-1)"/></g>')
    ticks = []
    every = max(1, len(labels) // 12)
    for index, label in enumerate(labels):
        if not label or index % every:
            continue
        # A label at the edge of the chart moves into the chart. If it does not
        # move, the browser cuts the text.
        x = left + index * slot + slot / 2
        anchor = "middle"
        if x < 18:
            x, anchor = 1, "start"
        elif x > width - 18:
            x, anchor = width - 1, "end"
        ticks.append(f'<text x="{x:.1f}" y="{height - 4}" text-anchor="{anchor}" '
                     f'class="tick">{html.escape(str(label))}</text>')
    return (svg_open(width, height) +
            f'<line x1="0" y1="{plot}" x2="{width}" y2="{plot}" '
            'stroke="var(--axis)" stroke-width="1"/>' +
            "".join(bars) + "".join(ticks) + "</svg>")


# -------------------------------------------------------------------- the page

STYLE = """
:root {
  color-scheme: light;
  --plane:#f4f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --dim:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --rule:rgba(11,11,11,.14);
  --series-1:#2a78d6; --good:#0ca30c; --critical:#d03b3b;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --dim:#898781;
    --grid:#2c2c2a; --axis:#383835; --rule:rgba(255,255,255,.16);
    --series-1:#3987e5; --good:#0ca30c; --critical:#d03b3b;
  }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--plane); color:var(--ink);
  font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased; }
main { max-width:1180px; margin:0 auto; padding:0 20px 56px; }
.num, td.n, th.n { font-family:var(--mono); font-variant-numeric:tabular-nums; }

header { display:flex; flex-wrap:wrap; gap:16px 28px; align-items:baseline;
  padding:16px 0 12px; border-bottom:2px solid var(--ink); margin-bottom:0; }
.brand { font-weight:700; font-size:15px; letter-spacing:.10em; text-transform:uppercase; }
.brand span { color:var(--dim); font-weight:400; }
.hdr-item { font-size:11px; color:var(--dim); text-transform:uppercase;
  letter-spacing:.08em; }
.hdr-item b { display:block; font:13px var(--mono); color:var(--ink);
  letter-spacing:0; font-weight:500; margin-top:2px; text-transform:none; }
.hdr-item b.ok { color:var(--good); } .hdr-item b.bad { color:var(--critical); }

.kpi { display:grid; grid-template-columns:repeat(auto-fit,minmax(118px,1fr));
  border-bottom:1px solid var(--rule); }
.kpi > div { padding:14px 13px 13px; border-right:1px solid var(--rule); }
.kpi > div:last-child { border-right:none; }
.kpi .v { font:21px/1.1 var(--mono); font-variant-numeric:tabular-nums;
  letter-spacing:-.01em; }
.kpi .v small { font-size:12px; color:var(--ink-2); margin-left:3px; }
.kpi .k { font-size:10.5px; color:var(--dim); text-transform:uppercase;
  letter-spacing:.09em; margin-top:6px; }

h2 { font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-2);
  margin:30px 0 0; padding-bottom:6px; border-bottom:1px solid var(--ink); font-weight:600; }
h2 span { float:right; color:var(--dim); font-weight:400; letter-spacing:.04em;
  text-transform:none; }
.panel { border-bottom:1px solid var(--rule); padding:0; overflow-x:auto; }
.pad { padding:12px 2px 16px; }

table { width:100%; border-collapse:collapse; }
th, td { text-align:right; padding:5px 10px; white-space:nowrap;
  border-bottom:1px solid var(--grid); }
th { font-size:10.5px; color:var(--dim); text-transform:uppercase; letter-spacing:.07em;
  font-weight:500; padding-top:10px; border-bottom:1px solid var(--rule); }
th:first-child, td:first-child { text-align:left; }
td { font-size:12.5px; }
td.n { font-size:12.5px; }
tbody tr:hover { background:var(--surface); }
tbody tr:last-child td { border-bottom:none; }
.tick { fill:var(--dim); font-size:9.5px; font-family:var(--mono); }
.spark { display:block; width:140px; }
.dim { color:var(--dim); }
.good { color:var(--good); } .bad { color:var(--critical); }
.note { font-size:11.5px; color:var(--ink-2); margin:10px 2px 12px; max-width:76ch; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:0 24px; }
@media (max-width:760px) { .grid2 { grid-template-columns:1fr; } }
footer { font:11px var(--mono); color:var(--dim); padding-top:16px; }
"""


def kpi(value, unit, key):
    unit_html = f"<small>{html.escape(unit)}</small>" if unit else ""
    return (f'<div><div class="v">{html.escape(str(value))}{unit_html}</div>'
            f'<div class="k">{html.escape(key)}</div></div>')


def header_bar(series, first, last, age, quiet, long_gaps, history):
    state = "ok" if age is not None and age < 120 else "bad"
    age_text = duration(age) + " ago" if age is not None else "–"
    return (
        "<header>"
        f'<div class="brand">Kalshi <span>/</span> {html.escape(series)}</div>'
        f'<div class="hdr-item">Session<b>{html.escape(first)} → {html.escape(last)}</b></div>'
        f'<div class="hdr-item">Last update<b class="{state}">{html.escape(age_text)}</b></div>'
        f'<div class="hdr-item">Longest quiet time<b>{duration(quiet)}</b></div>'
        f'<div class="hdr-item">Quiet times &gt; 2 min<b>{long_gaps}</b></div>'
        f'<div class="hdr-item">Rows of past data<b>{number(history)}</b></div>'
        "</header>")


def ladder_section(violations, market_count):
    """Make the part of the page for the ladder test."""
    if market_count < 2:
        return ('<div class="panel"><p class="note">The test needs two markets of '
                "one event or more.</p></div>")
    note = ("<p class=\"note\">A market with a higher strike must not have a higher "
            "price. The event is the same, so the outcome of the higher strike is "
            "inside the outcome of the lower strike. A pair that breaks this rule "
            "gives a profit without a risk: buy the low strike at the ask, sell the "
            "high strike at the bid. The edge is the profit for one contract.</p>")
    if not violations:
        return ('<div class="panel">' + note +
                '<p class="note"><b class="good">No violation.</b> The ladder of the '
                "prices is correct in all data. The program found no arbitrage.</p>"
                "</div>")

    total_seconds = sum((item["end"] - item["start"]) / rd.NS_IN_ONE_SECOND
                        for item in violations)
    rows = ["<table><thead><tr><th>Buy (low strike)</th><th>Sell (high strike)</th>"
            '<th class="n">Edge ¢</th><th class="n">Start</th><th class="n">End</th>'
            '<th class="n">Held</th></tr></thead><tbody>']
    for item in violations[:30]:
        seconds = (item["end"] - item["start"]) / rd.NS_IN_ONE_SECOND
        rows.append(
            f'<tr><td>{html.escape(item["low"])}</td>'
            f'<td>{html.escape(item["high"])}</td>'
            f'<td class="n bad">{cents(item["edge"], 2)}</td>'
            f'<td class="n">{clock(item["start"])}</td>'
            f'<td class="n">{clock(item["end"])}</td>'
            f'<td class="n">{duration(seconds)}</td></tr>')
    rows.append("</tbody></table>")
    return ('<div class="panel">' + note +
            f'<p class="note"><b class="bad">{len(violations)} arbitrage window(s)</b> '
            f"· {duration(total_seconds)} in violation · largest edge "
            f'{cents(violations[0]["edge"], 2)} cents.</p>' + "".join(rows) + "</div>")


def market_table(metrics):
    """Make the table with one line for each market."""
    head = ('<table><thead><tr><th>Market</th><th>Middle price</th>'
            '<th class="n">Mid ¢</th><th class="n">Spread ¢</th>'
            '<th class="n">Time spread ¢</th><th class="n">Spread bp</th>'
            '<th class="n">Updates</th><th class="n">Per hour</th>'
            '<th class="n">Gap p50</th><th class="n">Gap p95</th>'
            '<th class="n">Volume</th><th class="n">OI</th>'
            '<th class="n">RTT ms</th></tr></thead><tbody>')
    rows = []
    for item in metrics:
        rows.append(
            f'<tr><td>{html.escape(item["ticker"])}</td>'
            f'<td>{sparkline(item["points"])}</td>'
            f'<td class="n">{cents(item["last_middle"])}</td>'
            f'<td class="n">{cents(item["last_spread"])}</td>'
            f'<td class="n">{cents(item["time_spread"], 2)}</td>'
            f'<td class="n">{basis_points(item["time_spread"], item["last_middle"])}</td>'
            f'<td class="n">{number(item["updates"])}</td>'
            f'<td class="n">{number(item["updates_per_hour"], 1)}</td>'
            f'<td class="n">{duration(item["gap_p50"])}</td>'
            f'<td class="n">{duration(item["gap_p95"])}</td>'
            f'<td class="n">{number(item["volume"])}</td>'
            f'<td class="n">{number(item["open_interest"])}</td>'
            f'<td class="n">{number(item["rtt_p50"])}</td></tr>')
    return head + "".join(rows) + "</tbody></table>"


def build_page(context):
    """Make the full text of the HTML page."""
    return "".join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
        "<title>Kalshi market efficiency</title>",
        f"<style>{STYLE}</style></head><body><main>",
        context["header"],
        f'<div class="kpi">{"".join(context["kpis"])}</div>',
        "<h2>Ladder test<span>arbitrage between two strikes of one event</span></h2>",
        context["ladder"],
        "<h2>Markets<span>the spread with a weight of the time; the interval between "
        "two updates</span></h2>",
        f'<div class="panel">{context["market_table"]}</div>',
        '<div class="grid2">',
        "<div><h2>Spread<span>cents</span></h2>"
        f'<div class="panel pad">{context["spread_chart"]}</div></div>',
        "<div><h2>Updates by hour<span>local time</span></h2>"
        f'<div class="panel pad">{context["hour_chart"]}</div></div>',
        "</div>",
        "<h2>Session activity<span>updates of a quote over the session</span></h2>",
        f'<div class="panel pad">{context["session_chart"]}</div>',
        f'<footer>{html.escape(context["footer"])}</footer>',
        "</main></body></html>",
    ])


def build_context(lines, paths, history_lines, series_name):
    """Calculate every value of the page."""
    groups = rd.group_by_ticker(lines)
    times = [int(line["recv_ts_ns"]) for line in lines]
    session_end = max(times) if times else time.time_ns()
    metrics = [market_metrics(ticker, group, session_end)
               for ticker, group in groups.items()]
    metrics.sort(key=lambda item: item["updates"], reverse=True)

    rtts = [value for value in (rd.to_float(line["rtt_ms"]) for line in lines)
            if value is not None]
    time_spreads = [item["time_spread"] for item in metrics
                    if item["time_spread"] is not None]
    middles = [item["last_middle"] for item in metrics
               if item["last_middle"] is not None]
    gaps = [item["gap_p50"] for item in metrics if item["gap_p50"] is not None]
    violations = ladder_violations(lines)
    longest_quiet, long_gaps = quiet_times(lines)
    first, last = time_range(times)
    age = (time.time_ns() - max(times)) / rd.NS_IN_ONE_SECOND if times else None

    mean_spread = statistics.fmean(time_spreads) if time_spreads else None
    mean_middle = statistics.fmean(middles) if middles else None

    kpis = [
        kpi(len(metrics), "", "markets"),
        kpi(number(len(lines)), "", "quote updates"),
        kpi(cents(mean_spread, 2), "¢", "spread, time weight"),
        kpi(basis_points(mean_spread, mean_middle), "bp", "spread of the price"),
        kpi(duration(statistics.median(gaps)) if gaps else "–", "",
            "interval, median"),
        kpi(number(percentile(rtts, 0.50)), "ms", "round trip p50"),
        kpi(number(percentile(rtts, 0.95)), "ms", "round trip p95"),
        kpi(len(violations), "", "ladder violations"),
    ]

    spread_labels, spread_counts = spread_histogram(lines)
    session_labels, session_counts = session_activity(lines)
    return {
        "header": header_bar(series_name, first, last, age, longest_quiet,
                             long_gaps, history_lines),
        "kpis": kpis,
        "ladder": ladder_section(violations, len(metrics)),
        "market_table": market_table(metrics),
        "spread_chart": bar_chart(spread_labels, spread_counts, " cents"),
        "hour_chart": bar_chart([f"{hour:02d}" for hour in range(24)],
                                hour_counts(lines)),
        "session_chart": bar_chart(session_labels, session_counts, "", 130, 1),
        "footer": (f"{len(paths)} file(s) · made by make_report.py at "
                   f"{time.strftime('%Y-%m-%d %H:%M:%S')} · the page reads itself "
                   "again each minute"),
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


def series_from_paths(paths, given):
    """Get the name of the series for the header."""
    if given:
        return given
    names = {os.path.basename(path).split("_")[0] for path in paths}
    return ", ".join(sorted(names)) if names else "–"


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

    context = build_context(lines, paths, count_history(args.data_dir),
                            series_from_paths(paths, args.series))
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
