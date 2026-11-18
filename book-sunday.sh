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

# Preferences: tee box 1, any slot from 6:04 to 6:39 AM, latest first.
# shellcheck disable=SC2086
exec .venv/bin/python -u -W ignore -m teebot.book \
    --session Morning \
    --target "06:39 AM" --earliest "06:04 AM" --latest "06:39 AM" \
    --holes 1 \
    $MODE "$@"
