# postmortem

Structured retrospective generator. Writes a searchable 8-section postmortem to `docs/postmortems/` after any significant task.

## Why

Retrospectives that live in chat scrollback are lost. This writes them to disk in a consistent shape, so past incidents and their causes stay greppable.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/postmortem` | skill | Interviews you about the task, then writes an 8-section document to `docs/postmortems/`. |

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install postmortem@claude-toolbelt
```

## Usage

Invoke `/postmortem` after any task worth recording. It reads the session for
context, asks what it cannot infer, and writes
`docs/postmortems/YYYY-MM-DD-<topic>.md`.

## Tests

```bash
bash plugins/postmortem/tests/all.sh
```

## Related

- [`agent-loop`](../agent-loop/) — calls this plugin via `/agent-loop-postmortem` to close out a loop.
