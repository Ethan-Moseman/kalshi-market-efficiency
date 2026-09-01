# Kalshi Market Collector

This program collects price data from Kalshi. It writes the data to a CSV file.
This document uses Simplified Technical English (ASD-STE100).

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

All 34 automatic tests passed. One of these tests uses a copy of the real
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

## 11. The collector as a service on macOS

A service starts at each login. It also starts again after an error. Use a
service for a collection of many days.

To install the service, use this command:

```bash
bash macos/install_service.sh KXHIGHNY
```

The script does these actions:

1. It examines the virtual environment.
2. It writes a control file for launchd to `~/Library/LaunchAgents/`.
3. It starts the collector.
4. It sends all messages to the file `collector.log`.

To see the messages, use this command:

```bash
tail -f collector.log
```

To stop the service, use this command:

```bash
bash macos/uninstall_service.sh
```

The command stops the collector. It does not remove your data.

**CAUTION: A Mac in sleep does not collect data.** The service stops with the
sleep of the computer. It starts again after the wake.

There are three solutions:

1. Keep the Mac awake with the command `caffeinate -i -s`.
2. Change the energy settings of the Mac. Then the Mac does not sleep.
3. Put the collector on a small computer in the cloud. This solution is the
   best solution for a collection of many weeks.
