#!/usr/bin/env python3
"""Stand up a labeled mo swarm from a spec file: one herdr workspace, a root pane plus a
balanced column of worker panes, each launching mo with its own flags and opening prompt.

Usage:
  python3 swarm.py <spec.json>     # scripted entry (also what swarm.sh wraps)
  python3 swarm.py                 # action entry: reads .mo-swarm.json from the invoking
                                   # workspace's root (HERDR_WORKSPACE_ID)

Spec shape (paths resolve relative to the spec file's directory):

  {
    "workspace_label": "review-factory",
    "cwd": ".",                            // workspace cwd; default: the spec's directory
    "mo_bin": "mo",                        // overridden by $MO_BIN when set
    "panes": [
      {"label": "rf-orch", "flags": "--intercom=rf-orch --intercom-trigger always"},
      {"label": "rf-adv",
       "flags": "--intercom=rf-adv --intercom-accept {root_session_id} --intercom-trigger always --intercom-allow {root_session_id}",
       "prompt_file": "scripts/review-factory-prompts/rf-adv.md"}
    ]
  }

The first pane is the root (left, full height); the rest stack right in a balanced column.
`{root_session_id}` in a non-root pane's flags substitutes the root pane's mo session id,
resolved from mo's own pane records — which is why the root launches first and the workers
after its record appears. Consent handshakes beyond launch flags stay with the caller.

Emits one JSON object on stdout: {"workspace_id": ..., "panes": [{"label", "pane_id",
"session_id"}, ...]} in spec order, so callers script against it.
"""

import json
import os
import subprocess
import sys
import time

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PANE_RECORDS_DIR = os.path.join(os.environ.get("MO_HOME", os.path.expanduser("~/.mo")), "herdr-panes")
RECORD_TIMEOUT_SECONDS = 90
AGENT_DETECT_TIMEOUT_SECONDS = 60
ROOT_SESSION_PLACEHOLDER = "{root_session_id}"


def herdr(*args):
    """Run one herdr CLI call and return its parsed JSON result object (None for the
    commands, like `pane run`, that print nothing on success)."""
    completed = subprocess.run(
        [HERDR, *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"herdr {' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    if not completed.stdout.strip():
        return None
    body = json.loads(completed.stdout)
    if "error" in body:
        raise SystemExit(f"herdr {' '.join(args)}: {body['error']}")
    return body["result"]


def split_pane(parent, direction):
    return herdr("pane", "split", parent, "--direction", direction, "--no-focus")["pane"]["pane_id"]


def balanced_column(parent, count):
    """Split `parent`'s region into `count` panes of equal height, returned top-to-bottom.
    Recursive halving — sequential downward splits would yield 50/25/12.5/..."""
    if count == 1:
        return [parent]
    top_count = (count + 1) // 2
    bottom = split_pane(parent, "down")
    return balanced_column(parent, top_count) + balanced_column(bottom, count - top_count)


def session_id_for_pane(pane_id, timeout_seconds):
    """The pane's mo session id, from the pane record mo maintains for this plugin's
    restore/reaper tooling. Appears once mo has reported its session; bounded wait."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            names = os.listdir(PANE_RECORDS_DIR)
        except FileNotFoundError:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(PANE_RECORDS_DIR, name)) as record_file:
                    record = json.load(record_file)
            except (OSError, ValueError):
                continue
            if record.get("pane_id") == pane_id and record.get("session_id"):
                return record["session_id"]
        time.sleep(2)
    raise SystemExit(f"no mo pane record for {pane_id} after {timeout_seconds}s (looked in {PANE_RECORDS_DIR})")


def rename_agents_when_detected(pane_ids_to_labels):
    """Set each pane's agent display name once herdr detects the running mo. Best-effort:
    mo (>= the intercom-label build) reports its own label; this keeps older binaries and
    the addressing name (`herdr agent send-keys <name>`) useful either way."""
    pending = dict(pane_ids_to_labels)
    deadline = time.monotonic() + AGENT_DETECT_TIMEOUT_SECONDS
    while pending and time.monotonic() < deadline:
        detected = {agent["pane_id"] for agent in herdr("agent", "list")["agents"]}
        for pane_id in [p for p in pending if p in detected]:
            subprocess.run(
                [HERDR, "agent", "rename", pane_id, pending.pop(pane_id)],
                capture_output=True, text=True, check=False,
            )
        if pending:
            time.sleep(2)


def spec_path_from_action_context():
    """Action entry: resolve <workspace cwd>/.mo-swarm.json for the invoking workspace."""
    workspace_id = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace_id:
        raise SystemExit("usage: swarm.py <spec.json> (or run as a workspace action)")
    for pane in herdr("pane", "list")["panes"]:
        if pane.get("workspace_id") == workspace_id and pane.get("cwd"):
            candidate = os.path.join(pane["cwd"], ".mo-swarm.json")
            if os.path.exists(candidate):
                return candidate
            raise SystemExit(f"no .mo-swarm.json in {pane['cwd']}")
    raise SystemExit(f"no pane with a cwd found in workspace {workspace_id}")


def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else spec_path_from_action_context()
    spec_dir = os.path.dirname(os.path.abspath(spec_path))
    with open(spec_path) as spec_file:
        spec = json.load(spec_file)

    panes_spec = spec.get("panes") or []
    if not panes_spec:
        raise SystemExit("spec has no panes")
    for pane in panes_spec:
        if not pane.get("label"):
            raise SystemExit("every pane needs a label")
    mo_bin = os.environ.get("MO_BIN") or spec.get("mo_bin") or "mo"
    cwd = os.path.join(spec_dir, spec.get("cwd", "."))

    def launch(pane_id, pane, root_session_id=None):
        flags = pane.get("flags", "")
        if root_session_id is not None:
            flags = flags.replace(ROOT_SESSION_PLACEHOLDER, root_session_id)
        elif ROOT_SESSION_PLACEHOLDER in flags:
            raise SystemExit(f"{pane['label']}: {ROOT_SESSION_PLACEHOLDER} is only valid in non-root panes")
        command = f"{mo_bin} {flags}".strip()
        if pane.get("prompt_file"):
            command += f" --prompt-file {os.path.join(spec_dir, pane['prompt_file'])}"
        herdr("pane", "rename", pane_id, pane["label"])
        herdr("pane", "run", pane_id, command)

    created = herdr(
        "workspace", "create", "--cwd", cwd,
        "--label", spec.get("workspace_label", "mo-swarm"), "--no-focus",
    )
    root_pane = created["root_pane"]["pane_id"]
    workspace_id = created["root_pane"]["workspace_id"]

    launch(root_pane, panes_spec[0])
    root_session_id = session_id_for_pane(root_pane, RECORD_TIMEOUT_SECONDS)

    worker_panes = []
    if len(panes_spec) > 1:
        column_parent = split_pane(root_pane, "right")
        worker_panes = balanced_column(column_parent, len(panes_spec) - 1)
        for pane_id, pane in zip(worker_panes, panes_spec[1:]):
            launch(pane_id, pane, root_session_id)

    result_panes = [
        {"label": panes_spec[0]["label"], "pane_id": root_pane, "session_id": root_session_id}
    ]
    for pane_id, pane in zip(worker_panes, panes_spec[1:]):
        result_panes.append({
            "label": pane["label"],
            "pane_id": pane_id,
            "session_id": session_id_for_pane(pane_id, RECORD_TIMEOUT_SECONDS),
        })

    rename_agents_when_detected({entry["pane_id"]: entry["label"] for entry in result_panes})
    print(json.dumps({"workspace_id": workspace_id, "panes": result_panes}))


if __name__ == "__main__":
    main()
