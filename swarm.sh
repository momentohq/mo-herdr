#!/bin/sh
# Stand up a labeled mo swarm from a spec file — see swarm.py for the spec shape.
# Scripted: sh swarm.sh path/to/spec.json   Action: reads .mo-swarm.json from the workspace.
set -eu
command -v python3 >/dev/null 2>&1 || { echo "python3 required for the swarm action" >&2; exit 1; }
exec python3 "$(dirname "$0")/swarm.py" "$@"
