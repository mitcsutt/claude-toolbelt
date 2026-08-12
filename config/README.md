# config

Tracked, non-plugin configuration. See
[`docs/repo-structure.md`](../docs/repo-structure.md) for why this directory
exists at all — in short, Claude Code's `statusLine` setting is global
config, not something a plugin manifest can declare, so a statusline can
only be shipped as tracked config plus install instructions rather than a
plugin.

## `ccstatusline/`

The layout and widget scripts for the [ccstatusline](https://github.com/sirmalloc/ccstatusline)
statusline this repo uses:

- `settings.json` — the ccstatusline layout (widgets, colors, separators).
  Tracked so the layout is shared and editable through ccstatusline's own
  TUI, which writes back to this file through a symlink once installed.
- `ccsl-model.sh`, `ccsl-dir.sh`, `ccsl-sandbox.sh` — the three custom-command
  widget scripts the layout references by path. These are the only tracked
  files on the repo's mode-755 allowlist (R9 in
  [`docs/authoring-plugins.md`](../docs/authoring-plugins.md)) because
  ccstatusline invokes `commandPath` directly via `execSync`, with no
  interpreter in front of it — the executable bit is load-bearing here, not
  cosmetic.

For how to install and wire this up — the symlink step, pointing Claude
Code's `statusLine` config at the ccstatusline binary, and the env var
overrides (`CCSL_HOME`, `CCSL_EDITOR`, `CLAUDE_CODE_USE_BEDROCK`) — see the
root [`README.md`'s "Install (statusline)" section](../README.md#install-statusline)
rather than this file; the steps aren't repeated here.
