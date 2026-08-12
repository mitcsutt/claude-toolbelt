# Design: migrate statusline from custom script to ccstatusline

**Date:** 2026-07-13
**Status:** approved

## Goal

Replace the hand-rolled `scripts/statusline.sh` with
[ccstatusline](https://github.com/sirmalloc/ccstatusline), keeping the
statusline's config tracked inside this repo and reproducing the current
visual style as closely as the tool's built-in widgets allow.

## Current statusline (baseline to match)

`scripts/statusline.sh` renders two lines:

- **Line 1:** `[Model] folder │ branch │ sandbox`
- **Line 2:** `▪▫bar ctx% │ ↑session% plan%·timeleft │ duration ↻cache%`

Where:

- `folder` = repo name (or basename when inside a subdir)
- `branch` = current git branch
- `sandbox` = `sandbox` / `sandbox:auto` label, derived by reading
  `.claude/settings.local.json` (project) then `~/.claude/settings.json` (user)
  for `sandbox.enabled` / `sandbox.autoAllowBashIfSandboxed`
- `bar` = 6-cell context-usage bar, green <50 / yellow <80 / red ≥80
- `ctx%` = context-window used percentage
- `↑session%` = slice of the Claude.ai 5-hour budget consumed since the last
  `/clear`, tracked via a persisted baseline in `~/.claude/.statusline_session_baseline`
- `plan%·timeleft` = 5-hour rolling limit used percentage + time until reset
- `duration` = session wall-clock time
- `cache%` = cache-read hit rate

The script also has a **side effect**: it writes `~/.claude/.context-window.json`
(context %, timestamp, session id) as a fast-path cache consumed by the
`guardrails` plugin's `context-warn.sh` UserPromptSubmit hook (which lives in
the separate `mitcsutt-claude-plugins` repo). That hook already has a
transcript-parsing fallback, so it degrades gracefully if the cache is absent.

## Target layout (ccstatusline widgets)

Two status lines:

- **Line 1:** `Model` · `Git Root Dir` (fall back to CWD) · `Git Branch` ·
  sandbox (**Custom Command** widget — no built-in equivalent)
- **Line 2:** `Context %` (progress-bar render style + per-threshold colors) ·
  `Session Usage` + `Block Reset Timer` (replaces `plan%·timeleft`) ·
  `Session Duration` · `Cache Hit Rate`

**Dropped:** `↑session%`. It has no built-in widget and its baseline-persistence
logic is too heavy to justify porting as a custom command. `Session Usage`
already conveys the 5-hour budget consumption.

## Install

- **Pinned global install** of a fixed ccstatusline version (chosen at install
  time), so renders are fast, version-locked, and reproducible.
- `~/.claude/settings.json` `statusLine`:
  - `type: "command"`
  - `command`: **absolute path** to the pinned binary — not bare `ccstatusline`
    — because the user runs Node via nvm and the bin dir changes with node
    version switches; an absolute path survives that.
  - `refreshInterval: 10` (only valid on Claude Code ≥ 2.1.97; confirm version
    before adding, else omit).

## Config tracking

- Canonical config committed to this repo at `config/ccstatusline/settings.json`
  (new directory).
- `~/.config/ccstatusline/settings.json` is a **symlink** to the repo file, so
  edits made through the TUI write straight through to the tracked copy.
- Setup must create the parent dir and replace any existing regular file with
  the symlink (back it up first if present).

## Authoring the config

The widget/settings schema is undocumented and normally TUI-generated. Rather
than guess:

1. Install the pinned version.
2. Run the TUI once to emit a valid baseline `settings.json`.
3. Hand-edit that JSON into the two-line layout above, consulting the installed
   package's own config types/defaults if the TUI cannot express a detail.

No reverse-engineering of minified bundles; reading the package's published
types is fine.

## context-warn.sh fast path

Attempt to preserve the `~/.claude/.context-window.json` cache write via the
sandbox Custom Command widget — **only if** that widget receives the full Claude
Code stdin JSON (the docs imply `terminal_width` is *added* to the CC payload,
which would mean the context fields are present; verify at implementation). If
the full JSON is not available to custom commands, drop the cache write and rely
on `context-warn.sh`'s transcript fallback. Either way, no change is made to the
`mitcsutt-claude-plugins` repo.

## Old script

Keep `scripts/statusline.sh` in place during the transition — it provides
rollback and keeps the context cache warm until ccstatusline is verified. Remove
it in a follow-up once ccstatusline is confirmed rendering correctly.

## Verification

- Pipe a representative Claude Code stdin JSON payload into the pinned binary and
  confirm two-line output renders, with colors and the sandbox label, matching
  the current style.
- Confirm the symlink resolves and TUI edits land in the repo file.
- Confirm `~/.claude/settings.json` points at the pinned binary via absolute path.

## Out of scope

- Porting `↑session%`.
- Any change to the `guardrails` / `context-warn.sh` hook.
- Removing the old script (deferred to a follow-up).
