#!/bin/bash
# Launches the I2P router via the vendor-provided runplain.sh, then blocks
# until the actual (backgrounded) Java process exits.
#
# Why: runplain.sh forks the real router process via `nohup ... &` and exits
# almost immediately itself -- if launchd supervises runplain.sh directly,
# it only sees that near-instant successful exit, decoupled from the real
# router's lifetime, so KeepAlive could never detect an actual crash. This
# wrapper waits on the real PID (written by runplain.sh to $TMPDIR/router.pid)
# so launchd's supervised process lifetime matches the router's real lifetime.

set -u

I2P_RUNPLAIN="/opt/homebrew/Cellar/i2p/2.12.0/libexec/runplain.sh"
PIDFILE="${TMPDIR:-/tmp}/router.pid"

echo "$(date '+%Y-%m-%d %H:%M:%S') starting I2P router via runplain.sh"
"$I2P_RUNPLAIN"

if [ ! -f "$PIDFILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: $PIDFILE not found after runplain.sh ran -- router likely failed to start"
    exit 1
fi

ROUTER_PID=$(cat "$PIDFILE")
echo "$(date '+%Y-%m-%d %H:%M:%S') supervising router pid $ROUTER_PID"

while kill -0 "$ROUTER_PID" 2>/dev/null; do
    sleep 5
done

echo "$(date '+%Y-%m-%d %H:%M:%S') router pid $ROUTER_PID has exited"
exit 1
