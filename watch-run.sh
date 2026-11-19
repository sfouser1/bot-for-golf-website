#!/bin/bash
# Print the status lines of the booking run started by ./book-sunday.sh as they
# appear, and exit when that run ends. Each line is one status update.
#   ./watch-run.sh        from the start of the run's output
#   ./watch-run.sh new    only lines that appear from now on
cd "$(dirname "$0")" || exit 1
LOG=debug/last-run.log
PID=$(cat debug/last-run.pid 2>/dev/null)

lines() { [ -f "$LOG" ] && wc -l < "$LOG" | tr -d ' ' || echo 0; }
emit() {
    total=$(lines)
    if [ "$total" -gt "$seen" ]; then
        # "not open yet" answers repeat several times a second; leave them out.
        sed -n "$((seen + 1)),${total}p" "$LOG" | grep -v ': not_open'
        seen=$total
    fi
}

seen=0
[ "$1" = "new" ] && seen=$(lines)
while [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; do
    emit
    sleep 0.5
done
sleep 1
emit
echo "RUN ENDED"
