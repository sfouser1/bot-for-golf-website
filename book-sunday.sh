#!/bin/bash
# Book next Sunday's tee time with the saved preferences.
#   ./book-sunday.sh             log in, wait for the booking window, book for real
#   ./book-sunday.sh --rehearse  log in, show the plan, book nothing
# Everything it prints also goes to debug/last-run.log, which ./watch-run.sh follows.
cd "$(dirname "$0")" || exit 1
set -o pipefail

MODE="--live --wait"
if [ "$1" = "--rehearse" ]; then
    MODE=""
    shift
fi

mkdir -p debug
echo $$ > debug/last-run.pid
# A live run may wait for hours: keep the Mac awake until this script ends.
[ -n "$MODE" ] && caffeinate -i -s -w $$ &

# Preferences: tee box 1, these times only, tried in this order.
# shellcheck disable=SC2086
.venv/bin/python -u -W ignore -m teebot.book \
    --session Morning \
    --order "06:18 AM,06:25 AM,06:39 AM,06:32 AM,06:11 AM,06:04 AM" \
    --holes 1 \
    $MODE "$@" 2>&1 | tee debug/last-run.log
