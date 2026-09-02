#!/bin/sh
# Runs once after herdr restores a session (and again after a live handoff).
# 1. Restore: relaunch `mo --resume <id>` in panes whose state record matches a restored pane.
# 2. Start the reaper (background poller; self-terminates when the herdr socket disappears —
#    herdr documents startup hooks as one-shot, so nothing restarts it but us).
set -eu
STATE_DIR="${MO_HOME:-$HOME/.mo}/herdr-panes"
LOG="$HERDR_PLUGIN_STATE_DIR/plugin.log"
echo "startup $(date -u +%FT%TZ)" >> "$LOG"
command -v python3 >/dev/null 2>&1 || { echo "python3 missing; restore/reaper disabled" >> "$LOG"; exit 0; }

# Records are JSON written atomically by mo; parse with a real JSON parser (values may contain
# quotes/backslashes). Emits: pane_id \t session_id \t cwd \t updated_at_ms — tab-separated.
read_record() {
  python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print("\t".join([str(d.get(k, "")) for k in ("pane_id", "session_id", "cwd", "updated_at_ms")]))
except Exception:
    pass' "$1"
}

# Restore only records younger than this: a record that stopped refreshing days ago is a machine
# reboot or long-dead session, not a herdr restart to recover from. mo refreshes every ~60s.
MAX_RESTORE_AGE_MS=$((48 * 3600 * 1000))
PROCESS_INFO_ATTEMPTS=5
PROCESS_INFO_RETRY_DELAY_SECONDS=1

# Restored panes can briefly reject process inspection while their shell processes settle. Retry
# within a bounded window so one transient API failure does not discard an otherwise valid record.
read_process_info() {
  process_info_pane=$1
  process_info_attempt=1
  while [ "$process_info_attempt" -le "$PROCESS_INFO_ATTEMPTS" ]; do
    if process_info=$("$HERDR_BIN_PATH" pane process-info --pane "$process_info_pane" 2>/dev/null); then
      printf '%s' "$process_info"
      return 0
    fi
    [ "$process_info_attempt" -eq "$PROCESS_INFO_ATTEMPTS" ] || sleep "$PROCESS_INFO_RETRY_DELAY_SECONDS"
    process_info_attempt=$((process_info_attempt + 1))
  done
  echo "process-info retry exhausted for pane $process_info_pane" >> "$LOG"
  return 1
}

if [ -d "$STATE_DIR" ]; then
  now_ms=$(($(date +%s) * 1000))
  for state_file in "$STATE_DIR"/*.json; do
    [ -e "$state_file" ] || break
    record=$(read_record "$state_file"); [ -n "$record" ] || continue
    pane_id=$(printf '%s' "$record" | cut -f1)
    session_id=$(printf '%s' "$record" | cut -f2)
    pane_cwd=$(printf '%s' "$record" | cut -f3)
    updated_ms=$(printf '%s' "$record" | cut -f4)
    [ -n "$pane_id" ] && [ -n "$session_id" ] && [ -n "$pane_cwd" ] || continue  # empty cwd = unusable
    [ -n "$updated_ms" ] && [ $((now_ms - updated_ms)) -lt "$MAX_RESTORE_AGE_MS" ] || continue
    # Pane ids survive a herdr restart (verified on 0.8.0); confirm the pane exists and is at a
    # bare shell before typing into it. cwd is the guard when panes were renumbered.
    info=$(read_process_info "$pane_id") || continue
    printf '%s' "$info" | grep -q '"name":"mo"' && continue    # already running (live handoff)
    current_cwd=$(printf '%s' "$info" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)["result"]["process_info"]
    print(d["foreground_processes"][0].get("cwd", "") if d.get("foreground_processes") else "")
except Exception:
    pass')
    [ "$current_cwd" = "$pane_cwd" ] || continue
    "$HERDR_BIN_PATH" pane run "$pane_id" "mo --resume $session_id" >> "$LOG" 2>&1 || true
    echo "restored $pane_id -> $session_id" >> "$LOG"
  done
fi

# Exactly one reaper: a previous server generation's reaper survives a restart (the new server
# recreates the same socket path its liveness check watches), so retire it before spawning ours.
REAPER_PID_FILE="$HERDR_PLUGIN_STATE_DIR/reaper.pid"
if [ -f "$REAPER_PID_FILE" ]; then
  kill "$(cat "$REAPER_PID_FILE")" 2>/dev/null || true
fi
nohup /bin/sh "$HERDR_PLUGIN_ROOT/reaper.sh" >> "$LOG" 2>&1 &
echo $! > "$REAPER_PID_FILE"
