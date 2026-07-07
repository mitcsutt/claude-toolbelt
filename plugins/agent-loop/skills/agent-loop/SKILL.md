---
name: agent-loop
description: Launch the agent-loop live dashboard for the current repo's loop. Discovers the loop dir and background-launches the localhost observer server. User-invoked only (never auto-run).
disable-model-invocation: true
---

# Launch the agent-loop dashboard

Discover the active (or paused/finished) agent-loop in this repo and open its live dashboard.
The dashboard **observes** — it never auto-starts the loop; use its ▶ Start / ⟳ Resume buttons.

## Step 1: Find the loop dir
Run: `ls -dt .claude/loop/*/ 2>/dev/null | head -1` from the repo (or worktree) root.
- If none, tell the user to run `/agent-loop-setup` first and stop.
- If several, list them (`ls -dt .claude/loop/*/`) and ask which to open.

## Step 2: Resolve the plugin's serve.py (source, not cache)
Use `${CLAUDE_PLUGIN_ROOT}/web/serve.py`. Confirm the file exists. If `${CLAUDE_PLUGIN_ROOT}`
is unset or points somewhere without `web/serve.py`, fall back to locating the plugin source
under the marketplace repo. Never hand-type a version-keyed cache path.

## Step 3: Background-launch (zero ongoing tokens)
Launch the server detached so it keeps running and does not consume tokens while alive:
`cd <worktree-from-LOOP_CONFIG> && LOOP_DIR=<loop-dir> python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"`
Run it with the Bash tool's background mode (`run_in_background: true`). It prints a JSON line
`{"type":"dashboard-started","url":"http://127.0.0.1:PORT", ...}` — surface the URL to the user.

## Step 4: Report
Tell the user the dashboard URL and that the loop is observed, not started. Your interactive
session stays free — they can keep asking you things while the loop runs.

Notes: requires python3 (stdlib only). The plain `bash run.sh` / `python3 web/serve.py` paths
still work for headless/CI use. `--no-spawn` makes the server a pure read-only observer.
