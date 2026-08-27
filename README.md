# mo × herdr

Run [`mo`](https://github.com/momentohq/mfunc-llm-gw) inside [herdr](https://herdr.dev) panes.

mo reports its own lifecycle to herdr natively — install nothing and the sidebar shows live
`working` / `blocked` / `idle`, with the row released on every exit short of `kill -9`. This
plugin adds the parts that cannot live inside the mo process:

- **Restore** — after a herdr restart, panes that were running mo relaunch `mo --resume <id>`
  with their conversation intact.
- **Launch** — a pane action (bindable) that starts mo, since `herdr agent start --kind mo`
  does not know custom agents.
- **Swarm** — stand up a whole labeled multi-pane mo swarm (an orchestrator plus a balanced
  column of workers) from one spec file, so a repo carries a small `.mo-swarm.json` instead of
  a bespoke pane-plumbing script.
- **Reaper** — clears a pane row if mo is SIGKILLed (the one death it cannot report itself).

## Install

```sh
herdr plugin install momentohq/mo-herdr
```

## Swarm

One command builds the workspace, splits a balanced worker column, labels every pane, launches
each mo with its own flags and opening prompt, and prints the resulting ids as JSON:

```sh
sh swarm.sh path/to/spec.json          # scripted
# or the "Launch mo swarm" workspace action, which reads <workspace cwd>/.mo-swarm.json
```

The spec (paths resolve relative to the spec file; full shape in `swarm.py`):

```json
{
  "workspace_label": "review-factory",
  "panes": [
    {"label": "rf-orch", "flags": "--intercom=rf-orch --intercom-trigger always --intercom-allow rf-adv"},
    {"label": "rf-adv",
     "flags": "--intercom=rf-adv --intercom-accept {root_session_id} --intercom-trigger always --intercom-allow {root_session_id}",
     "prompt_file": "prompts/rf-adv.md"}
  ]
}
```

The first pane is the root; the rest stack right in equal splits. `{root_session_id}` in a
worker's flags substitutes the root's mo session id (resolved from the pane records), so workers
can launch already trusting their orchestrator. What the swarm does **not** do: intercom consent
beyond launch flags — the reverse grants stay with the caller (they cannot be pre-given; see the
mo repo's intercom docs), until mo grows a real spawner (mfunc-llm-gw#3006).

## Configuration warning

Do **not** add `mo` to `ui.sidebar.agents.rows_by_agent` in your herdr `config.toml`. herdr
validates that table against its built-in agent list, and an unknown id makes herdr discard your
**entire** config (keybindings, theme, everything) and run on defaults, with only a log warning.
The same applies to `ui.sound.agents` — there is no `mo` key. mo controls its row's content via
herdr's metadata API instead.

## How it fits

State reporting comes from mo itself (see `docs/agent-lifecycle.md` in the mo repo) — this plugin
never infers state and never reads terminal contents. It reads only mo's per-pane state files
(`$MO_HOME (default ~/.mo)/herdr-panes/*.json`) and calls the herdr CLI. When herdr grows native mo
support, the restore and reaper stand down; the launch action remains a convenience.
