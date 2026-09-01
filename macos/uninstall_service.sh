#!/bin/bash
# Remove the four services of this project from macOS.
#
# Use: bash macos/uninstall_service.sh
#
# The command stops each service. It also removes the four control files. It does not remove your data.

set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"

for LABEL in com.ethanmoseman.kalshi-collector \
             com.ethanmoseman.kalshi-backfill \
             com.ethanmoseman.kalshi-report \
             com.ethanmoseman.kalshi-weather; do
    launchctl bootout "gui/$UID/$LABEL" 2> /dev/null || true
    rm -f "$AGENTS/$LABEL.plist"
    echo "The service $LABEL is stopped and removed."
done

echo "Your data in the folders data/ and data/history/ is not changed."
