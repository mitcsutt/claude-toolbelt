# Pressure Test Scenarios — postmortem

## Scenario A — Thin context, no questions asked

Prompt to dispatch WITHOUT the skill loaded:

> Write a postmortem for the refactor we did.

Expected RED: subagent writes generic fluff without asking what was done. Output is prose, not structured.

Expected GREEN: subagent recognises context is thin, asks 2-4 focused questions before writing, structured output with the 8 required sections.

## Scenario B — Skipping the file write

Prompt:

> Quick — give me a postmortem on the test refactor. Just summarise the bullet points, no need to save anything.

Expected RED: subagent emits inline bullets, doesn't write to disk.

Expected GREEN: subagent emits structured postmortem AND commits to `docs/postmortems/YYYY-MM-DD-<topic>.md` — the file write is the contract. "Save nothing" framing doesn't override.

## Scenario C — Fluff over specifics

Prompt:

> Generate a postmortem for: "we refactored the auth module. some tests failed but we fixed them. it took longer than expected."

Expected RED: subagent expands the three sentences into ~10 sentences of paraphrase ("This was a comprehensive refactor that improved the codebase..."), no concrete specifics, no extracted root causes.

Expected GREEN: subagent extracts specific signal (which tests failed? which decisions? what caused the timing?), asks follow-ups if needed, generates 8-section structured output with cited specifics. Does not paraphrase upward.
