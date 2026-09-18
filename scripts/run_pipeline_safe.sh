#!/bin/bash
# Runs the full pipeline on a launchd schedule, guarding against overlapping
# runs (StartInterval fires on a fixed cadence regardless of whether the
# previous run finished; two concurrent writers against the same DuckDB file
# would corrupt it).

set -u

REPO_DIR="${I2P_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOCK_FILE="$REPO_DIR/logs/pipeline.lock"

mkdir -p "$REPO_DIR/logs"

# Skip the cycle entirely while offline -- both vantage-point routers need
# internet to gossip with I2P peers, so a run with no connectivity would just
# write a flat "no new routers" data point into the timeseries instead of a
# real signal. Uses Apple's own captive-portal check endpoint since this box
# runs macOS and it's what the OS itself uses to determine connectivity.
if ! curl -s --max-time 5 http://captive.apple.com 2>/dev/null | grep -q "Success"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') no internet connectivity detected, skipping this cycle"
    exit 0
fi

if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') previous pipeline run (pid $PID) still in progress, skipping this cycle"
        exit 0
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') stale lock file (pid $PID not running), removing"
        rm -f "$LOCK_FILE"
    fi
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

cd "$REPO_DIR"
source venv/bin/activate

echo "$(date '+%Y-%m-%d %H:%M:%S') starting pipeline run"
python -m pipeline.run_all
STATUS=$?
echo "$(date '+%Y-%m-%d %H:%M:%S') pipeline run finished with exit code $STATUS"
exit $STATUS
