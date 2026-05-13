---
name: session-timeline
description: >
  This skill should be used when the user asks to "generate a session timeline",
  "visualize a session", "session presentation", "transcript timeline",
  "session view", "show me what happened", "create session HTML",
  "session recap", "review my session", "what did I do last session",
  or wants a visual HTML timeline of a Claude Code session transcript.
user-invocable: true
---

# Session Timeline

Generate a self-contained HTML timeline visualization from a Claude Code session transcript (JSONL). The output is a dark-themed, responsive HTML page showing:

- **Stats grid**: session duration, turn count, subagent count, token usage, models used
- **Tools table**: aggregated tool call counts sorted by frequency
- **Subagent cards**: type, description, duration, token usage, tools, and summary for each subagent
- **Timeline**: chronological events — user messages, assistant text, tool calls, subagent execution blocks, turn markers, and time gaps

## Usage

The base command for all operations:

```
node ${CLAUDE_PLUGIN_ROOT}/skills/session-timeline/scripts/generate-timeline.mjs [flags]
```

The script prints the output file path to stdout and diagnostics to stderr.

**Common invocations:**

```bash
# Most recent session in the current project (default)
<base-command> --project "$(pwd)"

# Specific JSONL file
<base-command> --file /path/to/session.jsonl

# List recent sessions to choose from
<base-command> --project "$(pwd)" --list

# Custom output path
<base-command> --project "$(pwd)" --output /tmp/my-timeline.html
```

## CLI Arguments

| Flag | Description | Default |
|------|-------------|---------|
| `--file <path>` | Path to a specific JSONL file | Auto-discover |
| `--project <dir>` | Project directory for session discovery | `cwd` |
| `--output <path>` | Output HTML file path | `$TMPDIR/claude/session-timeline-<id>.html` |
| `--list` | List recent sessions and exit | — |

## Session Discovery

JSONL transcripts live under `~/.claude/projects/<slug>/<session-id>.jsonl`, where `<slug>` is the project directory path with `/` replaced by `-`. The script auto-discovers the correct directory from `--project` and picks the most recent session by modification time.

Subagent transcripts, when present, live under `~/.claude/projects/<slug>/<session-id>/subagents/`. The script parses these automatically and correlates them with Agent tool calls from the main transcript using timestamp proximity matching.

## After Generation

1. Capture the output path from stdout
2. Open the file in a browser — use `open <path>` on macOS or browser automation tools
3. Report the file path to the user so they can share it
