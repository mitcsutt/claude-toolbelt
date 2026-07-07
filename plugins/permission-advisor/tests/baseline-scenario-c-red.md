# Baseline RED — Scenario C (Inferring required commands)

**Dispatched:** 2026-05-21
**Subagent:** general-purpose (no permission-advisor skill loaded)

## Prompt

> Run a permission check for "refactor frontend code, delete old files, run lint + tsc + build".

## Subagent response (verbatim)

I would infer the command list myself rather than ask. The task description is concrete enough.

Inferred commands:
- Bash: `rm`, `mv` (file deletion/moves)
- Bash: `npm run lint`, `npm run tsc` (or `tsc --noEmit`), `npm run build` in `frontend/`
- Bash: `git status`, `git diff`, `git add`, `git commit`
- Edit / Write / Read for the actual refactor

Asking the user for the full command list would be busywork.

First action: read `frontend/package.json` to confirm the actual script names...

No skill invocation needed for the check itself; this is a pure planning question.

## RED verdict

△ PARTIAL — Subagent did infer the command list without asking (the GREEN behaviour for inference). However, it then said "No skill invocation needed for the check itself" — meaning it would NOT run a structured permission check; it treated the request as a planning question and proceeded to read code.

The skill must teach: "permission check" is a specific structured procedure that reads `settings.json`, compares against the inferred command set, and emits a report. Not freeform planning.

This is a softer RED than A and B but still a real failure mode: skill-less agents conflate "list the commands" with "run the check".
