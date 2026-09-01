#!/bin/bash
# Install the two services of this project on macOS.
#
# Service 1: the collector. It runs always. It writes each change of a quote.
# Service 2: the backfill. It runs each hour. It gets the past minutes from
#            Kalshi. It fills each gap of the collector. A gap occurs when the
#            Mac sleeps, or after an error.
# Service 3: the report. It runs each 5 minutes. It makes the file report.html.
#
# The two services start at each login of the user.
#
# Use: bash macos/install_service.sh [SERIES ...]
# Give one series or more. The default series is KXHIGHNY.
# An example with four series:
#     bash macos/install_service.sh KXHIGHNY KXHIGHCHI KXHIGHMIA KXHIGHAUS

set -euo pipefail

SERIES=("$@")
if [ ${#SERIES[@]} -eq 0 ]; then
    SERIES=("KXHIGHNY")
fi
COLLECTOR_LABEL="com.ethanmoseman.kalshi-collector"
BACKFILL_LABEL="com.ethanmoseman.kalshi-backfill"
REPORT_LABEL="com.ethanmoseman.kalshi-report"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python3"
AGENTS="$HOME/Library/LaunchAgents"

# Step 1: examine the necessary parts.
if ! command -v launchctl > /dev/null; then
    echo "ERROR: launchctl is absent. This program is only for macOS."
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: the file $PYTHON is absent."
    echo "Make the virtual environment first. Use these three commands:"
    echo "    python3 -m venv .venv"
    echo "    source .venv/bin/activate"
    echo "    pip install -r requirements.txt"
    exit 1
fi

mkdir -p "$AGENTS" "$REPO/data" "$REPO/data/history"

# write_plist LABEL LOGFILE KEY VALUE PROGRAM_ARGUMENTS...
# The key is KeepAlive for a program that runs always. The key is StartInterval
# for a program that runs again after a number of seconds.
write_plist() {
    local label="$1" logfile="$2" key="$3" value="$4"
    shift 4
    local arguments=""
    for argument in "$@"; do
        arguments="$arguments        <string>$argument</string>"$'\n'
    done
    if [ "$key" = "KeepAlive" ]; then
        local schedule="    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>"
    else
        local schedule="    <key>StartInterval</key>
    <integer>$value</integer>"
    fi
    cat > "$AGENTS/$label.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
$arguments    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
$schedule
    <key>StandardOutPath</key>
    <string>$REPO/$logfile</string>
    <key>StandardErrorPath</key>
    <string>$REPO/$logfile</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLISTEOF
}

start_service() {
    local label="$1"
    launchctl bootout "gui/$UID/$label" 2> /dev/null || true
    launchctl bootstrap "gui/$UID" "$AGENTS/$label.plist"
    launchctl enable "gui/$UID/$label"
}

# Step 2: write the three control files.
write_plist "$COLLECTOR_LABEL" "collector.log" KeepAlive 0 \
    "$PYTHON" "$REPO/kalshi_collector.py" --series "${SERIES[@]}"

write_plist "$BACKFILL_LABEL" "backfill.log" StartInterval 3600 \
    "$PYTHON" "$REPO/backfill.py" --days 1 --series "${SERIES[@]}"

write_plist "$REPORT_LABEL" "report.log" StartInterval 300 \
    "$PYTHON" "$REPO/make_report.py"

# Step 3: start the three services.
start_service "$COLLECTOR_LABEL"
start_service "$BACKFILL_LABEL"
start_service "$REPORT_LABEL"

echo "The three services are installed and started."
echo "  series   : ${SERIES[*]}"
echo "  collector: it runs always. Log: $REPO/collector.log"
echo "  backfill : it runs each hour. Log: $REPO/backfill.log"
echo "  report   : it runs each 5 minutes. Log: $REPO/report.log"
echo
echo "To see your dashboard:  open $REPO/report.html"
echo "To see the collector :  tail -f $REPO/collector.log"
echo "To see your data     :  cd $REPO && python3 read_data.py"
echo "To stop the services :  bash macos/uninstall_service.sh"
