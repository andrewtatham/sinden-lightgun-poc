#!/bin/sh
# Start the Sinden driver, run the game, and stop the driver again afterwards, so the idle guns
# don't push the mouse pointer around the desktop.
HERE="$(cd "$(dirname "$0")" && pwd)"
DRIVER_DIR="$HOME/sinden-software/SindenLightgunSoftwareReleaseV2.08b/SindenLightgunLinuxSoftwareV2.05/PCversion/Standard/Lightgun"
LOG="$HOME/sinden-software/driver.log"
DRIVER_PID=""

stop_driver() {
    if [ -n "$DRIVER_PID" ]; then
        kill "$DRIVER_PID" 2>/dev/null
        wait "$DRIVER_PID" 2>/dev/null
        DRIVER_PID=""
    fi
}
trap stop_driver EXIT
trap 'exit 130' INT TERM HUP

if [ "$1" != "--no-driver" ] && [ -d "$DRIVER_DIR" ]; then
    if pgrep -x mono >/dev/null; then
        echo "A Sinden driver is already running - using it (it will be left running)."
    else
        echo "Starting Sinden driver ..."
        (cd "$DRIVER_DIR" && exec mono LightgunMono.exe) >"$LOG" 2>&1 &
        DRIVER_PID=$!
        # wait until the guns have been set up (both cameras report an exposure), max 20 s
        for i in $(seq 1 40); do
            sleep 0.5
            kill -0 "$DRIVER_PID" 2>/dev/null || { echo "Driver exited early - see $LOG"; break; }
            [ "$(grep -c 'Exposure AutoSet' "$LOG")" -ge "$(grep -c 'Found Lightgun at' "$LOG")" ] \
                && grep -q 'Found Lightgun at' "$LOG" && break
        done
        sleep 1
        grep -E "Number of Sinden|Found Lightgun at" "$LOG"
    fi
fi
[ "$1" = "--no-driver" ] && shift

"$HERE/.venv/bin/python" "$HERE/lightgun.py" "$@"
