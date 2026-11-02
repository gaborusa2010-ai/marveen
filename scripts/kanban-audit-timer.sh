#!/bin/bash
# Card #698 part 3: the mechanical kanban audit runs on a systemd timer (06/12/18:00),
# outside the scheduler's quota gate, so archiving and the dampeners never starve.
# Output contract of `kanban_motor.py audit`: exactly "SKIP" = nothing for the model.
# Anything else is written to PENDING; the kanban-audit task's preCheck (2 minutes
# later) hands it to the model once. stderr ("Archivalva #N") goes to the journal.
set -u
STORE=/home/gabor/marveen/store
PENDING=$STORE/kanban-audit-pending.txt
STAMP=$STORE/.kanban-audit-timer-last
# 2026-11-01: the Persistent catch-up after a boot fires before the dashboard
# listens (19:58:53, "Connection refused"), and the day's archiving was lost.
# Wait up to 3 minutes for the port; if it never comes up, the motor reports it.
for _ in $(seq 36); do
    curl -s -o /dev/null --max-time 2 http://localhost:3420/ && break
    sleep 5
done
out=$(timeout 9 python3 -I /home/gabor/marveen/scripts/kanban-motor/kanban_motor.py audit)
rc=$?
date +%s > "$STAMP"
if [ "$rc" -eq 0 ] && [ "$out" = "SKIP" ]; then
    rm -f "$PENDING"
    echo "kanban-audit: SKIP"
    exit 0
fi
if [ -z "$out" ]; then
    out="Kanban-audit timer: a motor nem adott kimenetet (rc=$rc, 124 = 9 s-os timeout); a kanban-auditot nezd meg magad, a naplo: journalctl --user -u marveen-kanban-audit."
fi
printf '%s\n' "$out" > "$PENDING.tmp" && mv "$PENDING.tmp" "$PENDING"
echo "kanban-audit: pending written (rc=$rc, $(wc -l < "$PENDING") lines)"
