#!/bin/sh
# Book next Sunday's tee time with the saved preferences.
#   ./book-sunday.sh             log in, wait for the booking window, book for real
#   ./book-sunday.sh --rehearse  log in, show the plan, book nothing
cd "$(dirname "$0")" || exit 1

MODE="--live --wait"
if [ "$1" = "--rehearse" ]; then
    MODE=""
    shift
fi

# Preferences: tee box 1, these times only, tried in this order.
# shellcheck disable=SC2086
exec .venv/bin/python -u -W ignore -m teebot.book \
    --session Morning \
    --order "06:18 AM,06:25 AM,06:39 AM,06:32 AM,06:11 AM,06:04 AM" \
    --holes 1 \
    $MODE "$@"
