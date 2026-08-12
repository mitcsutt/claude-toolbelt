# session-timeline

Generates a self-contained HTML timeline visualization from a Claude Code session transcript (JSONL) — dark-themed, responsive, with stats, tool usage, subagent cards, and a chronological event timeline.

## Why

A session transcript is a flat JSONL log of turns and tool calls — hard to scan for what actually happened, especially once subagents are involved. This renders it as a single HTML file you can open in a browser or share, with subagent transcripts correlated back into the main timeline.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/session-timeline` | skill | Runs `generate-timeline.mjs` against a session transcript and reports the output HTML path. |

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install session-timeline@claude-toolbelt
```

Node is the only runtime requirement.

## Usage

```bash
node ${CLAUDE_PLUGIN_ROOT}/skills/session-timeline/scripts/generate-timeline.mjs [flags]
```

| Flag | Description | Default |
| --- | --- | --- |
| `--file <path>` | Path to a specific JSONL file | Auto-discover |
| `--project <dir>` | Project directory for session discovery | `cwd` |
| `--output <path>` | Output HTML file path | `$TMPDIR/claude/session-timeline-<id>.html` |
| `--list` | List recent sessions and exit | — |

If `--file` is omitted, the script auto-discovers the most recent session under `~/.claude/projects/<slug>/` for `--project` (or `cwd`). Subagent transcripts under that session's `subagents/` directory are parsed and correlated with `Agent` tool calls by timestamp proximity.

## Tests

```bash
bash plugins/session-timeline/tests/all.sh
```
