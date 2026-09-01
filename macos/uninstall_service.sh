#!/bin/bash
# Remove the service of the collector from macOS.
#
# Use: bash macos/uninstall_service.sh
#
# The command stops the collector. It also removes the control file. It does not
# remove the data in the folder data/.

set -euo pipefail

LABEL="com.ethanmoseman.kalshi-collector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID/$LABEL" 2> /dev/null || true
rm -f "$PLIST"

echo "The service is stopped. The control file is removed."
echo "The data in the folder data/ is not changed."
