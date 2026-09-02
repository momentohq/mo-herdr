#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/mo-herdr-startup.XXXXXX")
trap 'rm -rf "$TEST_ROOT"' EXIT HUP INT TERM

mkdir -p "$TEST_ROOT/bin" "$TEST_ROOT/mo/herdr-panes" "$TEST_ROOT/plugin-state"
CALLS="$TEST_ROOT/calls"
ATTEMPTS="$TEST_ROOT/attempts"
: > "$CALLS"

cat > "$TEST_ROOT/bin/herdr" <<'EOF'
#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$TEST_CALLS"

case "$*" in
  "pane process-info --pane retry-pane")
    attempts=0
    [ ! -f "$TEST_ATTEMPTS" ] || attempts=$(cat "$TEST_ATTEMPTS")
    attempts=$((attempts + 1))
    printf '%s\n' "$attempts" > "$TEST_ATTEMPTS"
    [ "$attempts" -gt 1 ] || exit 1
    printf '{"result":{"process_info":{"foreground_processes":[{"name":"sh","cwd":"%s"}]}}}\n' "$TEST_CWD"
    ;;
  "pane process-info --pane missing-pane")
    exit 1
    ;;
  "pane process-info --pane running-pane")
    printf '{"result":{"process_info":{"foreground_processes":[{"name":"mo","cwd":"%s"}]}}}\n' "$TEST_CWD"
    ;;
  "pane process-info --pane mismatch-pane")
    printf '{"result":{"process_info":{"foreground_processes":[{"name":"sh","cwd":"/somewhere/else"}]}}}\n'
    ;;
  "pane run retry-pane mo --resume session-1")
    ;;
  *)
    echo "unexpected herdr call: $*" >&2
    exit 1
    ;;
esac
EOF
chmod +x "$TEST_ROOT/bin/herdr"

cat > "$TEST_ROOT/bin/sleep" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$TEST_ROOT/bin/sleep"

now_ms=$(($(date +%s) * 1000))
printf '{"pane_id":"retry-pane","session_id":"session-1","cwd":"%s","updated_at_ms":%s}\n' \
  "$TEST_ROOT/work" "$now_ms" > "$TEST_ROOT/mo/herdr-panes/retry.json"
printf '{"pane_id":"missing-pane","session_id":"session-2","cwd":"%s","updated_at_ms":%s}\n' \
  "$TEST_ROOT/work" "$now_ms" > "$TEST_ROOT/mo/herdr-panes/missing.json"
printf '{"pane_id":"running-pane","session_id":"session-3","cwd":"%s","updated_at_ms":%s}\n' \
  "$TEST_ROOT/work" "$now_ms" > "$TEST_ROOT/mo/herdr-panes/running.json"
printf '{"pane_id":"mismatch-pane","session_id":"session-4","cwd":"%s","updated_at_ms":%s}\n' \
  "$TEST_ROOT/work" "$now_ms" > "$TEST_ROOT/mo/herdr-panes/mismatch.json"
printf '{"pane_id":"stale-pane","session_id":"session-5","cwd":"%s","updated_at_ms":0}\n' \
  "$TEST_ROOT/work" > "$TEST_ROOT/mo/herdr-panes/stale.json"
printf '%s\n' '{not valid json' > "$TEST_ROOT/mo/herdr-panes/invalid.json"

export TEST_CALLS="$CALLS"
export TEST_ATTEMPTS="$ATTEMPTS"
export TEST_CWD="$TEST_ROOT/work"
PATH="$TEST_ROOT/bin:$PATH" \
MO_HOME="$TEST_ROOT/mo" \
HERDR_BIN_PATH="$TEST_ROOT/bin/herdr" \
HERDR_PLUGIN_ROOT="$REPO_ROOT" \
HERDR_PLUGIN_STATE_DIR="$TEST_ROOT/plugin-state" \
HERDR_SOCKET_PATH="$TEST_ROOT/missing.sock" \
  /bin/sh "$REPO_ROOT/startup.sh"

[ "$(grep -c '^pane process-info --pane retry-pane$' "$CALLS")" -eq 2 ] || {
  echo "expected retry-pane discovery to succeed on the second attempt" >&2
  exit 1
}
[ "$(grep -c '^pane run retry-pane mo --resume session-1$' "$CALLS")" -eq 1 ] || {
  echo "expected retry-pane to resume exactly once" >&2
  exit 1
}
[ "$(grep -c '^pane process-info --pane missing-pane$' "$CALLS")" -eq 5 ] || {
  echo "expected missing-pane discovery to stop after five attempts" >&2
  exit 1
}
[ "$(grep -c '^pane run ' "$CALLS")" -eq 1 ] || {
  echo "expected running, stale, mismatched, and invalid records to remain skipped" >&2
  exit 1
}
! grep -q 'stale-pane\|invalid-pane' "$CALLS" || {
  echo "expected stale and invalid records to be skipped before pane discovery" >&2
  exit 1
}
grep -q 'process-info retry exhausted for pane missing-pane' "$TEST_ROOT/plugin-state/plugin.log" || {
  echo "expected retry exhaustion to be logged with the pane id" >&2
  exit 1
}

echo "startup retry test: ok"
