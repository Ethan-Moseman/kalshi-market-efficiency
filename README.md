# Kalshi Market Efficiency

This project measures the efficiency of a prediction market. It collects data
from Kalshi and from the National Weather Service. Then it tests three
questions.

This document uses Simplified Technical English (ASD-STE100).

## The three questions

**1. Is the price correct?**
A price of 70 cents is a forecast. It says "this event comes 70 percent of the
time". The program `calibration.py` tests that forecast against the answer of
each settled market. A market with a good calibration has a small gap between
the price and the answer.

**2. Does the market break a law of logic?**
The markets of one event make a ladder. A temperature above 90 degrees is also
above 85 degrees. So the market for 90 degrees must not cost more than the
market for 85 degrees. This rule is a law of logic. It is not an opinion. The
dashboard finds each window where the market breaks the rule. In that window a
trader makes a profit without a risk.

**3. How fast does the market read the news?**
The National Weather Service publishes a new forecast. The program
`weather_collector.py` records the second of that publication. The market
collector records the second of the new price. The difference between the two
times is the reaction time of the market.

## The result

Each measurement is reproducible. The quick start below gives the commands. The
programs print the numbers from your own data.

### What the calibration looks like

The program `calibration.py` prints this table:

       price bin  contracts  events  forecast  realized    error      gap
    --------------------------------------------------------------------------
        0-10 ¢       1662     587      2.5¢      1.7%     0.6    -0.7 
       10-20 ¢        332     332     14.4¢     13.3%     1.9    -1.2 
       20-30 ¢        278     278     25.2¢     28.4%     2.7    +3.2 
       30-40 ¢        241     241     34.4¢     29.9%     3.0    -4.5 
       40-50 ¢        185     185     44.8¢     46.5%     3.7    +1.7 
       50-60 ¢        197     197     55.0¢     60.9%     3.5    +5.9 
       60-70 ¢        208     208     65.0¢     67.3%     3.3    +2.3 
       70-80 ¢        194     194     74.6¢     73.7%     3.2    -0.9 
       80-90 ¢        265     265     85.5¢     85.7%     2.2    +0.2 
       90-100¢        758     370     97.1¢     97.5%     0.9    +0.4 

**CAUTION: The numbers above come from synthetic data. They are not a finding.**
The program `make_demo_data.py` makes a market that is correct by construction.
Each event has one temperature and six strikes. The price of each market is the
true probability of its strike. To make the same table again, use these two
commands:

    python3 make_demo_data.py
    python3 calibration.py --series demo

The table is a test of the program. A correct program finds a small gap in each
bin of this data. To get a real result, run the commands of the quick start
against the live API.

### Why the program counts the events

The table has two counts. The count of the contracts is large. The count of the
events is small. The second count decides the precision.

The markets of one event are not independent. The event `KXHIGHNY-26SEP01` has
the markets T85, T88 and T90. One temperature decides all three. So three lines
are one measurement, and not three measurements.

A program that counts the contracts makes the sample look 6 times larger than
it is. The error of each bin is then too small. The program can show an effect
that is only noise. This project counts the events and groups the markets of
one event together.

### The three measurements

| Question | The measurement | The program |
| --- | --- | --- |
| Calibration | The gap between the price and the answer, for each bin | `calibration.py` |
| Arbitrage | The number and the size of the windows in the ladder | `make_report.py` |
| Reaction time | The seconds from the new forecast to the new price | `weather_collector.py` |

## The programs

| Program | Function |
| --- | --- |
| `kalshi_collector.py` | It records each change of a quote, to the second. |
| `weather_collector.py` | It records each forecast and each measurement of the NWS. |
| `backfill.py` | It gets the candlesticks and the trades of the past. |
| `settlements.py` | It gets the answer of each settled market. |
| `calibration.py` | It tests the forecast against the answer. |
| `make_report.py` | It makes the dashboard `report.html`. |
| `read_data.py` | It prints a summary of the data of the collector. |
| `find_series.py` | It finds the ticker of each series. |
| `make_demo_data.py` | It makes synthetic data for a test of the calibration. |

The project has 181 automatic tests. They use a small fake server. They need no
connection to the internet. To run them, use this command:

    python3 -m unittest discover -v

## Quick start

Install the program:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Test the names of the fields against the live API. Do this first:

    python3 settlements.py --inspect

Then collect the past data and measure the calibration:

    SERIES=$(python3 find_series.py --category weather --tickers)
    python3 settlements.py --series $SERIES --days 90
    python3 backfill.py --status settled --days 90 --series $SERIES
    python3 calibration.py --series $SERIES

To record the present, start the services of section 11. The collector and the
weather program record the seconds. You cannot get those seconds later.

## The manual

The sections below give the manual of each program.

## 1. Technical terms

| Term | Meaning |
| --- | --- |
| API | The Kalshi web service. It sends market data. |
| JSON | The data format of the API. |
| CSV | A text file. Each line is one record. Commas divide the columns. |
| Series | A group of related markets. An example is `KXHIGHNY`. |
| Ticker | The short name of one market. |
| Bid | The price to buy. |
| Ask | The price to sell. |
| Quote | The four prices together: `yes_bid`, `yes_ask`, `no_bid`, `no_ask`. |
| RTT | Round-trip time. It is the time from the start of the request to the end of the answer. |
| Poll | One request to the API. |
| Cycle | One poll of each series. |

## 2. What the program does

1. The program sends a request to the Kalshi API.
2. The API sends back all open markets of one series.
3. The program waits 2 seconds. Then it sends the next request.
4. The program compares the quote of each market with the last quote.
5. If the quote is the same, the program writes nothing.
6. If the quote is different, the program writes one line in the CSV file.
7. The program also writes a line for the first sight of a market. This line is
   the start value.
8. The program makes one new CSV file each day.

A change of `volume` alone does not make a new line. Only a change of the quote
makes a new line.

The API needs no password and no key. The endpoint is public.

## 3. Installation

1. Install Python 3.9 or a later version.
2. Open a terminal in the folder of this repository.
3. Install the library: `pip install -r requirements.txt`

## 4. First test

Do this test before you start a long collection.

1. Run this command: `python3 kalshi_collector.py --inspect`
2. The program sends one request. Then it prints the JSON of one market.
3. After the JSON, the program prints the value of each column.
4. Each line shows the column, the value and the field of the API.
5. Each price must show a value. An example is `yes_bid = '0.0000'`.

The last part of a good result looks like this:

    The program writes these values to the CSV file:
        ticker = 'KXHIGHNY-26SEP01-T90'   (from the field ticker)
        event_ticker = 'KXHIGHNY-26SEP01'   (from the field event_ticker)
        yes_bid = '0.0000'   (from the field yes_bid_dollars)
        yes_ask = '0.0100'   (from the field yes_ask_dollars)
        no_bid = '0.9900'   (from the field no_bid_dollars)
        no_ask = '1.0000'   (from the field no_ask_dollars)
        volume = '2375.00'   (from the field volume_fp)
        open_interest = '1996.00'   (from the field open_interest_fp)

**CAUTION: The program prints a caution if it finds no price.** Then the API
changed the names of its fields. Do not start a collection. Tell the author.
The program needs a correction.

To see a different market, use the option `--ticker`:

    python3 kalshi_collector.py --inspect --ticker KXHIGHNY-26SEP01-B80

## 5. Collection of data

1. Run this command: `python3 kalshi_collector.py`
2. The program prints its status to the screen.
3. The program writes the data to the folder `data/`.
4. To stop the program, push Ctrl-C.
5. The program closes the file. Then it prints the number of lines.

### Options

| Option | Function | Default |
| --- | --- | --- |
| `--series` | The series to poll. You may give more than one. | `KXHIGHNY` |
| `--interval` | The time between two cycles, in seconds. | `2.0` |
| `--data-dir` | The folder for the CSV files. | `data` |
| `--inspect` | Send one request. Print one market. Then stop. | off |
| `--ticker` | With `--inspect`, the market to print. | first market |

Example with two series and a shorter interval:

    python3 kalshi_collector.py --series KXHIGHNY KXHIGHCHI --interval 1

**NOTE: Do not use a very short interval.** A short interval makes many
requests. Kalshi can refuse the requests of a client that sends too many.

## 6. The CSV file

The name of the file contains the series and the date. An example is
`data/KXHIGHNY_2026-09-01.csv`.

| Column | Meaning |
| --- | --- |
| `recv_ts_ns` | The local time of the answer, in nanoseconds after 1970-01-01. |
| `rtt_ms` | The round-trip time of that request, in milliseconds. |
| `ticker` | The name of the market. |
| `event_ticker` | The name of the event that contains the market. |
| `yes_bid` | The best price to buy YES, in dollars. |
| `yes_ask` | The best price to sell YES, in dollars. |
| `no_bid` | The best price to buy NO, in dollars. |
| `no_ask` | The best price to sell NO, in dollars. |
| `volume` | The number of contracts in trades until now. |
| `open_interest` | The number of open contracts. |

An empty cell means that the API sent no value for this field.

### The unit of a price

The API sends each price as a text in dollars. The program writes this text
without a change. The value `0.0100` is equal to 1 cent. The value `0.9900` is
equal to 99 cents. A price is always between `0.0000` and `1.0000`.

### The names of the fields

The name of a column is not equal to the name of the field in the API. This
table shows the two names:

| The column in the CSV file | The field in the API |
| --- | --- |
| `yes_bid` | `yes_bid_dollars` |
| `yes_ask` | `yes_ask_dollars` |
| `no_bid` | `no_bid_dollars` |
| `no_ask` | `no_ask_dollars` |
| `volume` | `volume_fp` |
| `open_interest` | `open_interest_fp` |
| `ticker` | `ticker` |
| `event_ticker` | `event_ticker` |

Older documents of Kalshi use short names such as `yes_bid` and `volume`. The
program accepts the two groups of names. It uses the first name that the answer
contains. The long name has the first position.

To change `recv_ts_ns` into a normal time, divide the value by 1000000000. The
result is the time in seconds after 1970-01-01.

The folder `data/` is in the file `.gitignore`. Git does not save the collected
data.

## 7. Design decisions

1. The program uses one HTTP connection for all requests. A new connection
   needs approximately 100 milliseconds. This time is larger than the RTT.
   One connection keeps the measurement of the RTT correct.
2. The program measures the RTT with a monotonic clock. A change of the system
   clock does not corrupt the measurement.
3. The API sends a maximum of 200 markets in one answer. If there are more
   markets, the answer contains a cursor. The program uses the cursor and asks
   for the next page. Each market keeps the time of its own page.
4. The program calculates the time of the next cycle from the start time. The
   duration of a request does not move the times of the later cycles.
5. If a request fails, the program prints the error. Then it continues with the
   next cycle. The program does not stop.
6. At midnight, the program opens a new file. It also forgets the last quotes.
   Because of this, each daily file starts with a full set of start values.
7. The server closes an old connection sometimes. Then the request fails with
   the message "Connection reset by peer". The program makes the same request
   again one time. An error code from the server is different. The program does
   not make that request again.

## 8. The automatic tests

The file `test_kalshi_collector.py` contains 24 automatic tests. The tests use a
small fake server on your computer. They need no connection to the internet.

To run the tests, use this command:

    python3 -m unittest discover -v

The tests examine these rules:

1. The request contains the correct parameters.
2. The program reads all pages of a long answer.
3. Each page keeps the time of its own request.
4. An error code from the API makes an exception.
5. The program writes one line for the first sight of a market.
6. The program writes no line when the quote stays the same.
7. A change of each of the four prices makes a new line.
8. A change of the volume alone makes no new line.
9. A market without a ticker makes no line.
10. An empty value from the API becomes an empty cell.
11. The values in the line are equal to the values from the API.
12. The name of the file contains the series and the date.
13. The program writes the header one time only.
14. The program opens a new file after midnight.
15. The loop continues after a network error.
16. The loop stops after a signal.
17. The options on the command line have the correct effect.

Run the tests after each change to the program.

## 9. The status against the real API

A user did the test of section 4 against the real Kalshi API on 2026-09-01. The
API answered. The names of the fields were different from the first version of
this program. The program now uses the correct names.

All 181 automatic tests passed. One of these tests uses a copy of the real
answer from the API.

**CAUTION: Do the test in section 4 before your first long collection.** The
API can change again. This test finds the change in 5 seconds.

## 10. The program that reads the data

The file `read_data.py` reads the CSV files. It prints one line for each market.
This program uses no library.

```bash
python3 read_data.py                    # All files in the folder data/.
python3 read_data.py --series KXHIGHNY  # The files of one series.
python3 read_data.py --date 2026-09-01  # The files of one day.
python3 read_data.py --ticker KXHIGHNY-26SEP01-T90   # Each line of one market.
```

The result looks like this:

    ticker                      lines     first      last  chg/hour    spread    middle    volume   rtt ms
    ------------------------------------------------------------------------------------------------------
    KXHIGHNY-26SEP01-T85          120  02:10:48  05:40:41      34.0    0.0147    0.4082      4473    178.8
    KXHIGHNY-26SEP01-T90           40  02:09:55  03:21:48      32.6    0.0155    0.0192      2823    177.5

    files    : 1
    markets  : 2
    lines    : 160
    rtt ms   : median 178.4, minimum 120.2, maximum 259.0
    spread   : mean 0.0151 dollars

| Column | Meaning |
| --- | --- |
| `lines` | The number of changes of the quote in the data. |
| `first` and `last` | The local time of the first line and the last line. |
| `chg/hour` | The number of changes for each hour. |
| `spread` | The mean difference between `yes_ask` and `yes_bid`, in dollars. |
| `middle` | The mean of `yes_bid` and `yes_ask`, in dollars. |
| `volume` | The volume in the last line. |
| `rtt ms` | The median round-trip time, in milliseconds. |

A dash in a column means that the program found no value.

A large spread and a small number of changes show a market with a low
efficiency. A small spread and many changes show a market with a high
efficiency.

## 11. The three services on macOS

A service starts at each login. It also starts again after an error. Use the
services for a collection of many days.

To install the three services, use this command:

```bash
bash macos/install_service.sh KXHIGHNY
```

Give more than one series to collect more markets:

```bash
bash macos/install_service.sh KXHIGHNY KXHIGHCHI KXHIGHMIA KXHIGHAUS
```

The command installs three services:

| Service | Function |
| --- | --- |
| collector | It runs always. It writes each change of a quote. |
| backfill | It runs each hour. It gets the past minutes from Kalshi. |
| report | It runs each 5 minutes. It makes the dashboard `report.html`. |
| weather | It runs always. It records the forecast and the temperature. |

The backfill fills each gap of the collector. A gap occurs when the Mac sleeps.
A gap also occurs after an error. Because of this, the two services together
give a full set of data.

To see the messages, use these commands:

```bash
tail -f collector.log
tail -f backfill.log
tail -f weather.log
```

Push Ctrl-C to leave a log. The service continues.

To stop the three services, use this command:

```bash
bash macos/uninstall_service.sh
```

The command stops the three services. It does not remove your data.

**NOTE: A Mac in sleep collects no data with the collector.** The collector
stops with the sleep. It starts again after the wake. The backfill then gets
the minutes of the sleep from Kalshi. The candlesticks give one value for each
minute. They do not give each change.

For a full set of changes for 24 hours, put the collector on a small computer
in the cloud. That computer does not sleep.

## 12. The past data

The collector gets only the present. The program `backfill.py` gets the past.
Kalshi keeps two groups of past data.

1. The candlesticks give the open, the high, the low and the close of `yes_bid`
   and `yes_ask` for each minute.
2. The trades give the price, the number of contracts, the time and the side of
   the taker for each trade.

The endpoints are public. They need no password.

```bash
python3 backfill.py                                  # The series KXHIGHNY.
python3 backfill.py --series KXHIGHNY KXHIGHCHI      # Two series.
python3 backfill.py --series KXHIGHNY --days 3       # The last three days.
python3 backfill.py --ticker KXHIGHNY-26SEP01-T90    # One market only.
python3 backfill.py --what trades                    # The trades only.
python3 backfill.py --status all                     # Also the closed markets.
```

The program writes to these two files:

    data/history/candles_KXHIGHNY.csv
    data/history/trades_KXHIGHNY.csv

You may run the program more than one time. It reads the file first. Then it
adds only the new lines. It makes no copy of a line. A candlestick has the key
`ticker` and `end_period_ts`. A trade has the key `trade_id`.

**CAUTION: The default of `--status` is `open`. It gets NO settled market.**
For the calibration you need the settled markets. Use this command:

    python3 backfill.py --status settled --days 90 --series KXHIGHNY

Kalshi keeps the recent markets and the old markets in two different places.
The program tries the endpoint of the recent markets first. After an error, it
tries the endpoint of the old markets. Read the log after a long backfill. If
the log shows an error for each old market, the path changed. Tell the author.

**NOTE: The past data is in the folder `data/history/`.** The data of the
collector is in the folder `data/`. The two groups have different columns. The
program `read_data.py` reads only the files of the collector.

### The columns of the candlesticks

| Column | Meaning |
| --- | --- |
| `ticker` and `event_ticker` | The names of the market and the event. |
| `end_period_ts` | The end of the minute, in seconds after 1970-01-01. |
| `end_period_utc` | The same time in the UTC zone. |
| `yes_bid_open` to `yes_bid_close` | The four values of `yes_bid` in the minute. |
| `yes_ask_open` to `yes_ask_close` | The four values of `yes_ask` in the minute. |
| `price_open` to `price_close` | The four values of the trade price. |
| `volume` and `open_interest` | The counters at the end of the minute. |

### The columns of the trades

| Column | Meaning |
| --- | --- |
| `trade_id` | The name of the trade. It is unique. |
| `ticker` | The name of the market. |
| `created_time` | The time of the trade. |
| `yes_price_dollars` | The price of YES, in dollars. |
| `no_price_dollars` | The price of NO, in dollars. |
| `count_fp` | The number of contracts. |
| `taker_book_side` | The side of the book: `bid` or `ask`. |
| `taker_outcome_side` | The side of the taker: `yes` or `no`. |
| `is_block_trade` | True for a large trade outside the book. |


## 13. The dashboard

The program `make_report.py` makes one HTML file. Open this file in a browser.
The file contains all data and all graphs. It needs no server and no library.

```bash
python3 make_report.py --open
```

The page reads itself again each minute. The service of section 11 writes the
page again each 5 minutes. So the page is always new.

### The header

| Item | Meaning |
| --- | --- |
| Session | The time of the first change and the time of the last change. |
| Last update | The age of the last change. Red shows an age of more than 2 minutes. |
| Longest quiet time | The longest time between two changes. |
| Quiet times > 2 min | The number of long quiet times. A large number shows a stop of the collector. |
| Rows of past data | The lines in the folder `data/history/`. |

### The measurements at the top

| Measurement | Meaning |
| --- | --- |
| Markets | The number of markets in the data. |
| Quote updates | The number of changes of a quote. |
| Spread, time weight | The mean spread in cents. A quote keeps its weight until the next change. |
| Spread of the price | The same spread in basis points of the middle price. 100 basis points are 1 percent. |
| Interval, median | The median time between two changes of a quote. |
| Round trip p50 and p95 | The round-trip time of the requests at the percentile 50 and 95. |
| Ladder violations | The number of arbitrage windows. |

The mean spread has a weight of the time. A quote of 1 cent that stays for 10
minutes has more weight than a quote of 5 cents that stays for 1 second. A mean
without this weight gives a wrong picture of the market.

### The ladder test

The markets of one event make a ladder. An example is the event KXHIGHNY-26SEP01:

| Market | Question |
| --- | --- |
| `T85` | Is the maximum temperature more than 85 degrees? |
| `T88` | Is the maximum temperature more than 88 degrees? |
| `T90` | Is the maximum temperature more than 90 degrees? |

A temperature above 90 degrees is also above 85 degrees. So the market `T90`
must not have a higher price than the market `T85`. This rule is a law of logic.
It is not an opinion.

Sometimes the market breaks this rule for some seconds. Then a trader has a
profit without a risk:

1. Buy `T85` at the ask.
2. Sell `T90` at the bid.
3. The profit is the difference between the two prices.

The page shows each window with this error. It shows the two markets, the edge
in cents, the start, the end and the time of the window. A market with many
windows and large windows has a low efficiency.

**NOTE: The page shows the past, not an offer.** A window of the past is not
open now. The size and the number of the windows measure the efficiency of the
market.

### An arbitrage inside one market is not possible

Kalshi makes `no_bid` from `yes_ask` with a subtraction. It makes `no_ask` from
`yes_bid`. The two sides of one market are always a mirror. So the sum of the
two asks is always more than one dollar. The ladder between the strikes is the
only test with a result.

### The table of the markets

| Column | Meaning |
| --- | --- |
| Middle price | A small graph of the middle price in the session. |
| Mid ¢ | The last middle price, in cents. |
| Spread ¢ | The last spread, in cents. |
| Time spread ¢ | The mean spread with a weight of the time. |
| Spread bp | The same spread in basis points of the middle price. |
| Updates | The number of changes of a quote. |
| Per hour | The number of changes for each hour. |
| Gap p50 and p95 | The time between two changes, at the percentile 50 and 95. |
| Volume and OI | The volume and the open interest in the last line. |
| RTT ms | The median round-trip time of the requests of this market. |

### The graphs

1. Spread: the number of changes for each value of the spread.
2. Updates by hour: the activity for each hour of the day.
3. Session activity: the activity from the start of the session to the end.

Put the pointer on a bar or on a small graph. Then the browser shows the exact
values.

### The colors

The graphs use one color for the data. The colors come from a palette with a
test for color blindness. The numbers use a font with an equal width for each
digit. Because of this, the columns of the tables stay in a line. The page has a
light mode and a dark mode. It follows the setting of your computer.


## 14. The list of the series

The collector needs the ticker of a series. The program `find_series.py` finds
each ticker from the public endpoint `/events`.

```bash
python3 find_series.py                     # All categories.
python3 find_series.py --category weather  # The weather markets only.
python3 find_series.py --category weather --tickers
```

The result looks like this:

    Climate and Weather
    ------------------------------------------
      KXHIGHNY     2 event(s)  Highest temperature in NYC
      KXHIGHCHI    2 event(s)  Highest temperature in Chicago

    2 series of 48 event(s).
    To collect all of them, use this command:
      python3 kalshi_collector.py --series KXHIGHNY KXHIGHCHI

The option `--tickers` prints only the names, in one line. Put that line after
the option `--series` of the collector.

The name of an event starts with the name of its series. An example is the event
`KXHIGHNY-26SEP01`. Its series is `KXHIGHNY`. The program uses this rule.

**NOTE: Kalshi can change the names of its series.** Run this program again if a
series gives no market.

### Why more than one series

A study of one city is weak. A study of many cities can compare a busy market
with a quiet market. The ladder test then gives a result for each city.

Collect the weather series together:

```bash
python3 kalshi_collector.py --series $(python3 find_series.py --category weather --tickers)
```

Each series gets its own CSV file. The dashboard shows all series together.


## 15. The weather data

The market collector records the opinion of the market. The program
`weather_collector.py` records the information that moves that opinion.

The two groups of files together give one number: **the delay between new
information and a new price.**

```bash
python3 weather_collector.py --list       # The table of the cities.
python3 weather_collector.py --inspect    # One fetch. Print the values.
python3 weather_collector.py              # Collect each city.
python3 weather_collector.py --series KXHIGHNY --interval 30
```

The program uses the public API of the National Weather Service at
`api.weather.gov`. This API needs no password and no key. It needs a User-Agent
header with a contact. The program sends the address of this repository.

### The two files

The program writes to the folder `data/weather/`.

| File | One line is |
| --- | --- |
| `forecast_<series>_<date>.csv` | A new forecast of the high temperature. |
| `observation_<series>_<date>.csv` | A new measurement of the station. |

The program writes a forecast line only when the temperature or the description
changes. A new publication with the same values makes no line. This rule is the
same rule as the rule of the market collector.

### The columns of the forecast

| Column | Meaning |
| --- | --- |
| `recv_ts_ns` | The local time of the answer, in nanoseconds. |
| `rtt_ms` | The round-trip time of the request, in milliseconds. |
| `series` and `city` | The market and the city. |
| `target_date` | The day of the forecast. |
| `period_name` | The name of the period. An example is `Today`. |
| `high_f` | The high temperature of that day, in Fahrenheit. |
| `short_forecast` | The short text. An example is `Sunny`. |
| `nws_update_time` | The time of the publication, from the NWS. |

The column `recv_ts_ns` is the important column. It is **your** time, from the
same clock as the market file. Because of this, you can subtract the two times.

### The columns of the observation

| Column | Meaning |
| --- | --- |
| `obs_time` | The time of the measurement, from the station. |
| `temp_c` and `temp_f` | The temperature in Celsius and in Fahrenheit. |
| `description` | The short text of the station. |

At the start of each day the program gets each observation of that day. Because
of this, the file has the full day, and not only the part after the start.

### The station of each market

**CAUTION: You must confirm the station of each market.** Each market names its
station in the field `rules_primary`. Use this command to read that text:

```bash
python3 kalshi_collector.py --inspect | grep rules_primary
```

If the station is different, correct the table `CITIES` in the program. You can
also make the file `weather_cities.json`. That file replaces the table. It has
this shape:

```json
{
  "KXHIGHNY": {"city": "New York", "station": "KNYC",
               "lat": 40.7789, "lon": -73.9692}
}
```

### The two questions that this data answers

1. **The reaction time.** The NWS publishes a new forecast. How many seconds
   later does the price move? Each publication gives one measurement. Many
   publications give a distribution.
2. **The anticipation.** Does the price move **before** the new forecast? If it
   does, the market has the information before the NWS publishes it.

The second question is more interesting. The ladder test finds an error of
logic. This test finds the speed of the information.

**NOTE: You cannot get this data later.** The NWS gives the forecast of now. It
does not give the time when you first saw an older forecast. Each hour without
this collector is an hour that is lost.


## 16. The answer of each market

The collector records the opinion of the market. It does not record the answer.
A price of 70 cents says "this event comes 70 percent of the time". To test that
forecast, you must know what came. The program `settlements.py` gets that.

For each market that settled, Kalshi gives the field `result`. The value is
`yes` or `no`. This value is the truth.

```bash
python3 settlements.py --inspect                 # Test the names of the fields.
python3 settlements.py                           # The series KXHIGHNY.
python3 settlements.py --series KXHIGHNY KXHIGHCHI
python3 settlements.py --days 90                 # The markets of the last 90 days.
```

**CAUTION: Do the test with `--inspect` before your first collection.** The
program prints the JSON of one settled market. Then it prints the value of each
column. If the field `result` is not `yes` and not `no`, the program prints a
caution and stops. The name of the field changed. Tell the author.

The program writes to this file:

    data/history/settlements_<series>.csv

| Column | Meaning |
| --- | --- |
| `ticker` and `event_ticker` | The names of the market and the event. |
| `series` | The name of the series. |
| `status` | The status from the API. An example is `settled`. |
| `result` | The answer from the API: `yes` or `no`. |
| `result_binary` | The same answer as 1, 0 or an empty cell. |
| `close_time` and `close_ts` | The time of the close, as text and as seconds. |
| `strike_type` | The type of the strike. An example is `greater`. |
| `floor_strike` and `cap_strike` | The limits of the market. |
| `expiration_value` | The value that decided the answer. An example is `86`. |
| `last_price` | The last price of a trade, in dollars. |
| `volume` and `open_interest` | The counters at the close. |

An empty cell in `result_binary` shows a market with no answer. An example is a
market that Kalshi cancelled. The program `calibration.py` does not use that
market.

The key of a line is the ticker. A market settles one time only. Because of
this, you can run the program each day. It adds only the new markets.

## 17. The calibration

The program `calibration.py` joins the answers and the prices. Then it tests the
forecast of the market. This program uses no library.

```bash
python3 calibration.py                              # The series KXHIGHNY.
python3 calibration.py --series KXHIGHNY KXHIGHCHI  # Two series together.
python3 calibration.py --hours 12                   # A longer horizon.
python3 calibration.py --bin-width 25               # Four wide bins.
```

The program needs the two files of section 16 and section 12:

    data/history/settlements_<series>.csv
    data/history/candles_<series>.csv

### The horizon

**CAUTION: The program takes the price BEFORE the close. It does not take the
last price.** At the close, the price is almost 0 or almost 100. The market then
knows the answer. A calibration of the last price looks perfect and measures
nothing.

The option `--hours` sets that time. The default is 6 hours before the close.
The program takes the last candlestick that is not after that time.

### The size of the sample

**The markets of one event are not independent.** The event `KXHIGHNY-26SEP01`
has the markets T85, T88 and T90. One temperature decides all three. So three
lines are one measurement, and not three measurements.

Because of this, the program counts two numbers:

| Number | Meaning |
| --- | --- |
| contracts | The number of markets. |
| events | The number of independent measurements. |

The second number decides the precision. A large number of contracts with a
small number of events gives a weak result. The program makes each error from
the events.

### The table

| Column | Meaning |
| --- | --- |
| `price bin` | The group of the price, in cents. |
| `contracts` | The number of markets in the bin. |
| `events` | The number of independent measurements in the bin. |
| `forecast` | The mean price of the bin. This is what the market said. |
| `realized` | The part of the markets of the bin that gave YES. |
| `error` | The error of `realized`. |
| `gap` | `realized` minus `forecast`. A star marks a gap of more than 2 errors. |

A market with a good calibration has a small gap in each bin. A market with a
bad calibration has a large gap with the same sign in many bins.

The Brier score gives one number for all bins. A score of 0 is a perfect
forecast. A score of 0.25 is a coin.

### The two errors

The program makes two errors and uses the larger one.

1. The error of the group. The markets of one event make one group. This error
   is correct when the answers of a bin are different.
2. The smallest error. A bin where each market gave the same answer has an
   error of 0 from the first method. An error of 0 is not true: a bin with 6
   events that all gave YES does not prove that the price is correct. This
   method adds two YES and two NO. Then it makes the error from the number of
   events. The result is never 0. Statisticians call this correction the
   interval of Agresti and Coull.

### Is the data large enough?

At the end, the program prints an answer to this question. It counts the bins
with 100 events or more. Then it says one of three things:

1. The data is large enough.
2. The data is in the middle. Use wide bins.
3. The data is too small.

**NOTE: Do not wait for the collector to make the sample large.** The collector
gives a few events each day. Kalshi keeps the past markets. One backfill gives
more events than one month of the collector:

```bash
python3 settlements.py --series KXHIGHNY KXHIGHCHI --days 90
python3 backfill.py --status settled --days 90 --series KXHIGHNY KXHIGHCHI
python3 calibration.py --series KXHIGHNY KXHIGHCHI
```

The collector and the weather program are still necessary. They record the
seconds. The candlesticks record only the minutes. And the forecast of the NWS
is not in the API of Kalshi. Section 15 gives that rule.
