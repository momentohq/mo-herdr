#!/bin/sh
# Clear herdr rows whose mo died a death mo cannot report (SIGKILL, abort). Every other exit
# releases from inside mo; this is the backstop. Liveness is the record's REFRESH STAMP, not the
# pid: mo re-stamps updated_at_ms every ~60s while alive, and a pid is reusable across reboots
# while a fresh stamp is not. Self-terminates when the herdr socket disappears.
set -u
STATE_DIR="${MO_HOME:-$HOME/.mo}/herdr-panes"
STALE_AFTER_MS=$((3 * 60 * 1000))   # 3 missed refresh intervals
command -v python3 >/dev/null 2>&1 || exit 0
while :; do
  [ -S "$HERDR_SOCKET_PATH" ] || exit 0
  if [ -d "$STATE_DIR" ]; then
    now_ms=$(($(date +%s) * 1000))
    for state_file in "$STATE_DIR"/*.json; do
      [ -e "$state_file" ] || break
      record=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(str(d.get("pane_id", "")) + "\t" + str(d.get("updated_at_ms", 0)))
except Exception:
    pass' "$state_file"); [ -n "$record" ] || continue
      pane_id=$(printf '%s' "$record" | cut -f1)
      updated_ms=$(printf '%s' "$record" | cut -f2)
      [ -n "$pane_id" ] && [ -n "$updated_ms" ] || continue
      [ $((now_ms - updated_ms)) -gt "$STALE_AFTER_MS" ] || continue
      # Stale: mo stopped refreshing without releasing — clear the row (epoch-ms seq out-runs
      # mo's own clock seqs) and retire the record so restore never resurrects it by surprise.
      "$HERDR_BIN_PATH" pane release-agent "$pane_id" --source custom:mo --agent mo --seq "$now_ms" 2>/dev/null || true
      rm -f "$state_file"
    done
  fi
  sleep 30
done
