#!/bin/sh
# The launch action: start mo in the invoking pane (herdr's `agent start --kind mo` rejects
# unrecognized kinds, so this is the scripted-launch stand-in).
set -eu
pane="${HERDR_PANE_ID:?run from a pane context}"
exec "$HERDR_BIN_PATH" pane run "$pane" "mo"
