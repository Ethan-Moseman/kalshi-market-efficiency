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
3. Read the field names in the JSON.
4. Compare these names with the columns in section 6.
5. If a name is different, tell the author. The program needs a correction.

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
| `yes_bid` | The best price to buy YES, in cents. |
| `yes_ask` | The best price to sell YES, in cents. |
| `no_bid` | The best price to buy NO, in cents. |
| `no_ask` | The best price to sell NO, in cents. |
| `volume` | The number of contracts in trades until now. |
| `open_interest` | The number of open contracts. |

An empty cell means that the API sent no value for this field.

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

## 8. Status of the tests

The author could not test this program against the real Kalshi API. The network
gate of the test computer denied the connection. This is a rule of that
computer. It is not a fault in the program.

The author tested the program against a local test server. These tests passed:

1. The program writes a line only when the quote changes.
2. The program ignores a change of `volume` alone.
3. The program reads all pages of a long answer.
4. The program makes a new file after midnight.
5. The program continues after a network error.
6. The program stops in a clean manner after Ctrl-C.

**CAUTION: Do the test in section 4 before your first long collection.** This
test shows the true field names from Kalshi. It also shows that your network
permits the connection.
