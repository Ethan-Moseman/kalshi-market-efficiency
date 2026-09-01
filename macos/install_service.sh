#!/bin/bash
# Install the collector as a service on macOS.
#
# The service starts at each login of the user. It starts again after an error.
# It writes its messages to the file collector.log.
#
# Use: bash macos/install_service.sh [SERIES]
# The default series is KXHIGHNY.

set -euo pipefail

SERIES="${1:-KXHIGHNY}"
LABEL="com.ethanmoseman.kalshi-collector"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python3"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

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

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$REPO/data"

# Step 2: write the control file for launchd.
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$REPO/kalshi_collector.py</string>
        <string>--series</string>
        <string>$SERIES</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$REPO/collector.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO/collector.log</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLISTEOF

# Step 3: start the service. The command bootout stops an older version.
launchctl bootout "gui/$UID/$LABEL" 2> /dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

echo "The service is installed and started."
echo "  series      : $SERIES"
echo "  control file: $PLIST"
echo "  log file    : $REPO/collector.log"
echo
echo "To see the messages:  tail -f $REPO/collector.log"
echo "To see the state   :  launchctl print gui/$UID/$LABEL | head -20"
echo "To stop the service:  bash macos/uninstall_service.sh"
