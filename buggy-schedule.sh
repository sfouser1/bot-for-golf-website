#!/bin/bash
# Scheduled buggy bookings, run by macOS (launchd) so they survive a closed terminal or a restart.
#
#   ./buggy-schedule.sh friday                     book a Friday-afternoon buggy every week
#   ./buggy-schedule.sh watch 20-11-2026 [Session] look about hourly for a free buggy and book it
#   ./buggy-schedule.sh status                     what is scheduled, and the last log lines
#   ./buggy-schedule.sh remove friday|watch        stop one of them
#   ./buggy-schedule.sh sync                       copy code changes to the scheduled copy
#   ./buggy-schedule.sh test-message               send a test iMessage from a scheduled job
#   ./buggy-schedule.sh relay                      from a terminal: send results the jobs could not
#
# macOS does not let scheduled jobs read the Documents folder, so the jobs run from a
# copy of the bot in ~/.tngf-teebot (code, .env, its own Python environment and logs).
# Run `sync` (or any install command) after changing the code or .env.
#
# Friday buggies open 7 days ahead at 06:00 AM, so the weekly job starts on Fridays at
# 05:57 and books the Friday one week later. Preferences are in the two "run-" lines below.
#
# Each job ends by sending its result as an iMessage to NOTIFY_IMESSAGE_TO in .env.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
RUN="$HOME/.tngf-teebot"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
FRIDAY=in.tngf.amy-buggy-friday
WATCH=in.tngf.amy-buggy-watch

sync_copy() {
    [ "$SRC" = "$RUN" ] && return 0
    mkdir -p "$RUN/debug"
    rsync -a --delete --exclude __pycache__ "$SRC/teebot/" "$RUN/teebot/"
    cp "$SRC/.env" "$RUN/.env" && chmod 600 "$RUN/.env"
    cp "$SRC/requirements.txt" "$RUN/"
    # Replace, never overwrite: a job that is mid-run keeps reading its own old copy.
    cp "$SRC/buggy-schedule.sh" "$RUN/.buggy-schedule.new" && mv "$RUN/.buggy-schedule.new" "$RUN/buggy-schedule.sh"
    if [ ! -x "$RUN/.venv/bin/python" ]; then
        /usr/bin/python3 -m venv "$RUN/.venv"
        "$RUN/.venv/bin/pip" install --quiet -r "$RUN/requirements.txt"
    fi
}

# notify TEXT: iMessage the result to the address in .env. Never fails the job.
notify() {
    local to
    to=$(sed -n 's/^NOTIFY_IMESSAGE_TO=//p' "$RUN/.env" | tail -n 1)
    [ -n "$to" ] || { echo "No NOTIFY_IMESSAGE_TO in .env: no message sent."; return 0; }
    if osascript -e 'on run argv' \
        -e 'tell application "Messages" to send (item 1 of argv) to participant (item 2 of argv) of (1st account whose service type = iMessage)' \
        -e 'end run' "TNGF buggy: $1" "$to" 2>&1; then
        echo "Message sent."
    else
        echo "Message NOT sent (see the error above)."
    fi
}

# result LOGFILE: the last status line of a job's log, without its timestamp.
result() { grep -E '\] ' "$RUN/debug/$1" | tail -n 1 | sed 's/^\[[^]]*\] //'; }

# plist LABEL LOGFILE EXTRA_XML ARG...   (the job runs: buggy-schedule.sh ARG...)
plist() {
    local label=$1 logfile=$2 extra=$3; shift 3
    local args=""
    for a in "$@"; do args="$args<string>$a</string>"; done
    cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$label</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$RUN/buggy-schedule.sh</string>$args</array>
<key>WorkingDirectory</key><string>$RUN</string>
<key>StandardOutPath</key><string>$RUN/debug/$logfile</string>
<key>StandardErrorPath</key><string>$RUN/debug/$logfile</string>
$extra
</dict></plist>
EOF
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist"
}

case "${1:-}" in
sync)
    sync_copy; echo "Scheduled copy updated in $RUN" ;;
friday)
    sync_copy
    plist "$FRIDAY" buggy-friday.log \
        "<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>5</integer><key>Minute</key><integer>57</integer></dict>" \
        run-friday
    echo "Scheduled: every Friday 05:57 AM, book a buggy for the Friday after (Afternoon, hole 1)." ;;
watch)
    [ -n "${2:-}" ] || { echo "Give the date: ./buggy-schedule.sh watch DD-MM-YYYY [Session]"; exit 2; }
    sync_copy
    : > "$RUN/debug/buggy-watch.log"
    plist "$WATCH" buggy-watch.log "<key>RunAtLoad</key><true/>" run-watch "$2" "${3:-Afternoon}"
    echo "Watching started for $2 ${3:-Afternoon}. Log: $RUN/debug/buggy-watch.log" ;;
remove)
    case "${2:-}" in friday) label=$FRIDAY ;; watch) label=$WATCH ;; *) echo "remove friday|watch"; exit 2 ;; esac
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$AGENTS/$label.plist"
    echo "Removed $label." ;;
status)
    for label in "$FRIDAY" "$WATCH"; do
        if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
            echo "$label: scheduled ($(launchctl print "$DOMAIN/$label" | grep -m1 'state =' | xargs))"
        else
            echo "$label: not scheduled"
        fi
    done
    for f in buggy-friday.log buggy-watch.log; do
        [ -s "$RUN/debug/$f" ] && { echo "--- $f (last lines)"; tail -n 8 "$RUN/debug/$f"; }
    done
    exit 0 ;;

# ---- what the scheduled jobs run (from the copy in ~/.tngf-teebot) ----
run-friday)
    cd "$RUN"
    caffeinate -i -w $$ &
    .venv/bin/python -u -W ignore -m teebot.buggy book --date friday --session Afternoon --hole 1 --live --wait || true
    notify "$(result buggy-friday.log)" ;;
run-watch)
    cd "$RUN"
    caffeinate -i -w $$ &
    .venv/bin/python -u -W ignore -m teebot.buggy watch --date "$2" --session "$3" --hole 1 --live || true
    rm -f "$AGENTS/$WATCH.plist"   # one-off: do not start again at the next login
    notify "$(result buggy-watch.log)" ;;
test-message)
    sync_copy
    : > "$RUN/debug/buggy-test.log"
    plist in.tngf.amy-buggy-test buggy-test.log "<key>RunAtLoad</key><true/>" run-test
    echo "Waiting for the result (up to 2.5 minutes if macOS is asking for permission on screen)..."
    for _ in $(seq 1 30); do [ -s "$RUN/debug/buggy-test.log" ] && break; sleep 5; done
    cat "$RUN/debug/buggy-test.log"
    launchctl bootout "$DOMAIN/in.tngf.amy-buggy-test" 2>/dev/null || true
    rm -f "$AGENTS/in.tngf.amy-buggy-test.plist" ;;
relay)
    # Until macOS lets scheduled jobs use Messages (a one-time Allow on the Mac's screen),
    # their sends time out. Run this from a terminal, which is allowed: it sends any result
    # a scheduled job failed to send. Stops when the terminal app quits or the Mac restarts.
    seen() { cat "$RUN"/debug/buggy-friday.log "$RUN"/debug/buggy-watch.log 2>/dev/null | grep -c 'Message NOT sent' || true; }
    count=$(seen)
    while sleep 60; do
        for f in buggy-friday.log buggy-watch.log; do
            [ -f "$RUN/debug/$f" ] || continue
            if [ "$(tail -n 1 "$RUN/debug/$f")" = "Message NOT sent (see the error above)." ] && [ "$(seen)" -gt "$count" ]; then
                notify "$(result "$f")"
            fi
        done
        count=$(seen)
    done ;;
run-test)
    notify "test message sent by a scheduled job. Booking results will arrive like this." ;;
*)
    sed -n '2,11p' "$0"; exit 2 ;;
esac
