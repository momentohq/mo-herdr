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
      {"label": "rf-orch", "flags": ["--intercom=rf-orch", "--intercom-trigger", "always"]},
      {"label": "rf-adv",
       "flags": ["--intercom=rf-adv", "--intercom-accept", "{root_session_id}",
                 "--intercom-trigger", "always", "--intercom-allow", "{root_session_id}"],
       "prompt_file": "scripts/review-factory-prompts/rf-adv.md"}
    ]
  }

`flags` as a LIST is the safe form (each element shell-quoted whole). A plain STRING is
accepted as trusted shell syntax verbatim — a spec is code either way; see the README.

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
import shlex
import sys
import time

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
# `or` (not a default arg) so MO_HOME="" means unset, matching the shell scripts' ${MO_HOME:-...}.
PANE_RECORDS_DIR = os.path.join(os.environ.get("MO_HOME") or os.path.expanduser("~/.mo"), "herdr-panes")
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


def equal_column(parent, count):
    """Split `parent`'s region into `count` panes of equal height, returned top-to-bottom.
    Each split's --ratio is the EXISTING pane's share (probed live on 0.8.2): carving 1/k of
    the remaining region off the top each round is exact for any count, where plain halving
    is equal only for powers of two."""
    panes = [parent]
    current, remaining = parent, count
    while remaining > 1:
        current = herdr(
            "pane", "split", current, "--direction", "down",
            "--ratio", f"{1 / remaining:.4f}", "--no-focus",
        )["pane"]["pane_id"]
        panes.append(current)
        remaining -= 1
    return panes


def session_id_for_pane(pane_id, timeout_seconds, not_before_ms):
    """The pane's mo session id, from the pane record mo maintains for this plugin's
    restore/reaper tooling. Appears once mo has reported its session; bounded wait.
    Only a record stamped at/after `not_before_ms` counts: pane ids repeat across herdr
    server generations and a SIGKILLed mo's record lingers until the reaper sweeps it, so
    an unfiltered match could hand a worker an unrelated old session id."""
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
            if (
                record.get("pane_id") == pane_id
                and record.get("session_id")
                and record.get("updated_at_ms", 0) >= not_before_ms
            ):
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
        try:
            detected = {agent["pane_id"] for agent in herdr("agent", "list")["agents"]}
        except (SystemExit, Exception):  # display names must never fail a launched swarm
            return
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
    tried = []
    for pane in herdr("pane", "list")["panes"]:
        if pane.get("workspace_id") == workspace_id and pane.get("cwd"):
            candidate = os.path.join(pane["cwd"], ".mo-swarm.json")
            if os.path.exists(candidate):
                return candidate
            tried.append(pane["cwd"])
    raise SystemExit(
        f"no .mo-swarm.json found in workspace {workspace_id} (looked in: {', '.join(tried) or 'no pane cwds'})"
    )


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

    def rendered_flags(pane, root_session_id):
        """The pane's mo flags as shell text. A LIST is the safe form — each element is
        substituted then shell-quoted whole. A STRING is passed verbatim: it is trusted
        shell syntax by contract, because `pane run` takes one shell command line (see the
        README's trust note — a spec is code)."""
        flags = pane.get("flags", [])
        substitute = (lambda text: text.replace(ROOT_SESSION_PLACEHOLDER, root_session_id)) \
            if root_session_id is not None else (lambda text: text)
        if root_session_id is None:
            probe = " ".join(flags) if isinstance(flags, list) else flags
            if ROOT_SESSION_PLACEHOLDER in probe:
                raise SystemExit(f"{pane['label']}: {ROOT_SESSION_PLACEHOLDER} is only valid in non-root panes")
        if isinstance(flags, list):
            return " ".join(shlex.quote(substitute(flag)) for flag in flags)
        return substitute(flags)

    def launch(pane_id, pane, root_session_id=None):
        """Rename the pane and start its mo; returns the epoch-ms launch floor its pane
        record must be stamped at/after to count as this launch's."""
        command = f"{shlex.quote(mo_bin)} {rendered_flags(pane, root_session_id)}".strip()
        if pane.get("prompt_file"):
            command += f" --prompt-file {shlex.quote(os.path.join(spec_dir, pane['prompt_file']))}"
        herdr("pane", "rename", pane_id, pane["label"])
        not_before_ms = int(time.time() * 1000) - 2000  # slack for a sub-second stamp race
        herdr("pane", "run", pane_id, command)
        return not_before_ms

    created = herdr(
        "workspace", "create", "--cwd", cwd,
        "--label", spec.get("workspace_label", "mo-swarm"), "--no-focus",
    )
    root_pane = created["root_pane"]["pane_id"]
    workspace_id = created["root_pane"]["workspace_id"]

    # Past this point a failure leaves live panes behind; point the caller at them rather
    # than exiting with nothing actionable. Deliberately no auto-close: a half-launched
    # swarm is exactly what someone debugging a spec wants to look at.
    try:
        root_launched_ms = launch(root_pane, panes_spec[0])
        root_session_id = session_id_for_pane(root_pane, RECORD_TIMEOUT_SECONDS, root_launched_ms)

        worker_panes = []
        worker_launched_ms = []
        if len(panes_spec) > 1:
            column_parent = split_pane(root_pane, "right")
            worker_panes = equal_column(column_parent, len(panes_spec) - 1)
            for pane_id, pane in zip(worker_panes, panes_spec[1:]):
                worker_launched_ms.append(launch(pane_id, pane, root_session_id))

        result_panes = [
            {"label": panes_spec[0]["label"], "pane_id": root_pane, "session_id": root_session_id}
        ]
        for pane_id, pane, launched_ms in zip(worker_panes, panes_spec[1:], worker_launched_ms):
            result_panes.append({
                "label": pane["label"],
                "pane_id": pane_id,
                "session_id": session_id_for_pane(pane_id, RECORD_TIMEOUT_SECONDS, launched_ms),
            })
    except BaseException:
        print(
            f"swarm bring-up failed part-way; workspace {workspace_id} is left running for "
            f"inspection — close it with: herdr workspace close {workspace_id}",
            file=sys.stderr,
        )
        raise

    rename_agents_when_detected({entry["pane_id"]: entry["label"] for entry in result_panes})
    print(json.dumps({"workspace_id": workspace_id, "panes": result_panes}))


if __name__ == "__main__":
    main()
