# mo × herdr

Run [`mo`](https://github.com/momentohq/mfunc-llm-gw) inside [herdr](https://herdr.dev) panes.

mo reports its own lifecycle to herdr natively — install nothing and the sidebar shows live
`working` / `blocked` / `idle`, with the row released on every exit short of `kill -9`. This
plugin adds the parts that cannot live inside the mo process:

- **Restore** — after a herdr restart, panes that were running mo relaunch `mo --resume <id>`
  with their conversation intact.
- **Launch** — a pane action (bindable) that starts mo, since `herdr agent start --kind mo`
  does not know custom agents.
- **Reaper** — clears a pane row if mo is SIGKILLed (the one death it cannot report itself).

## Install

```sh
herdr plugin install momentohq/mo-herdr
```

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
