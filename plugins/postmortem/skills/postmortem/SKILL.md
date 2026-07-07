---
name: postmortem
description: Use after any significant completed task to generate a structured retrospective written to docs/postmortems/. Triggers on phrases like "write a postmortem", "retrospective on X", "what did we learn from Y", "do a postmortem". Always writes to disk and commits — the file IS the deliverable, not the inline summary.
---

# Postmortem

Generates a structured retrospective document. The file write is the contract — never emit inline-only.

## When to invoke

- User says "postmortem", "retrospective", "what did we learn"
- After completing a long task, refactor, incident, or loop run
- Called by another skill or command with structured data pre-injected

## Hard contract

These are non-negotiable:

1. **The deliverable is a file at `docs/postmortems/YYYY-MM-DD-<topic>.md`**, committed to git. Inline-only output is a violation — even when the user says "just summarise" or "no need to save".
2. **The output has the 8 named sections** (below). All 8. Not 7, not 12. Even when a section ends up short.
3. **If context is thin, ask focused questions before writing.** Do not paraphrase 3 sentences upward into 10 paragraphs.
4. **Cite specifics, not vibes.** "Auth refactor failed CI on `auth.test.ts:42` (timeout)" beats "we had some test issues."
5. **State behavioural-verification status explicitly.** Whatever the topic, the postmortem must answer in one line: *was the running result actually exercised?* — did anyone boot the app, click the path, hit the real API? `yes (how)` / `no (only structural: lint/tsc/test/build)`. Never let "tests pass" stand in for "it works." When the answer is `no`, that fact belongs in the Limitations note (or §7) so the gap between *verified* and *predicted-working* is on the record — a postmortem that predicts a failure but never ran the thing to confirm should say so plainly.

## When to ask vs. when to write

Context is **thin** if you cannot answer at least 4 of these from the input + working directory state:

- What was the goal of the task?
- What was completed?
- What was attempted and failed?
- What blocked, if anything?
- What decisions were made and why?
- What is the user's intended follow-up?

If thin: ask 2-4 focused questions (not all 6, not 10 — focused). Wait for answers.

If sufficient: write immediately.

When a caller pre-injects dense structured context, treat it as sufficient — skip the thin-context questions.

## Required sections (in this order)

1. **Summary** — 2-3 sentences. What was attempted, what happened, where it landed.
2. **What worked well** — concrete observations with specifics. Numbers, file names, commit hashes if known.
3. **What failed or blocked** — concrete failures. Include exact error messages or task IDs when known.
4. **Root causes** — for each failure, the cause. Not a paraphrase of the failure.
5. **Decisions made and why** — non-obvious calls that future-you should be able to recover.
6. **Manual follow-up tasks** — items requiring a human action. Use checkbox `- [ ]` syntax.
7. **Recommendations for next time** — actionable changes to process, tooling, or scope.
8. **Key learnings** — durable insights worth surfacing in future planning.

If a section is genuinely empty (e.g. zero failures), write a single line: "_No items._" Do not invent filler.

## Output filename

Format: `docs/postmortems/YYYY-MM-DD-<topic>.md`

- `YYYY-MM-DD` — today's date
- `<topic>` — short kebab-case identifier (e.g. `auth-refactor`, `overnight-loop-aug15`, `t085-blocker`)
- If the file already exists for today's topic: append `-2`, `-3`, etc.

**Topic derivation when not explicitly named** (in order of preference):
1. The first noun phrase in the user's request
2. The current branch name (minus ticket prefix, e.g. `overnight-refactor` → `overnight-refactor`)
3. The latest commit subject line, slugged
4. Fallback: `untitled`

## Process

1. **Inspect repo state before deciding sufficiency.** Run `git log --oneline -20`, `git status`, `git branch --show-current`. Much of "what was done / what's staged / what's the topic" is recoverable here without asking the user.
2. **Check input sufficiency** (against the 6-question list above). Thin after step 1 → ask. Sufficient → continue.
3. **Determine the file path.** `mkdir -p docs/postmortems` first (create the directory even if `docs/` didn't exist).
4. **Write the file** with all 8 sections.
5. **Commit:** `git add docs/postmortems/<file>` (NOT `git add -A` — never stage unrelated changes). Then `git commit -m "docs: postmortem — <topic>"`.
6. **If `git rev-parse` fails (not a git repo):** write the file anyway, surface "not committed: not a git repo" in your final message.
7. **Surface the path** to the user in your final message. Don't paste the full content inline — point them at the file.

## What "advisory only" does NOT apply to

Unlike `/permission-advisor`, this skill DOES write files. The whole point is to build institutional memory on disk. "Don't save" framing in the user request does not override the file-write contract — it just means the user underestimates the value.

If the user genuinely doesn't want a file, they can delete it after. The skill always writes first.

## Addenda and superseding (post-close findings)

A postmortem is a point-in-time record. When later work invalidates a claim — most commonly a "what worked well" item that was only *structurally* verified and then failed on first real use — do NOT silently rewrite the original prose. Rewriting destroys the signal that the original assessment was wrong.

Instead:

1. **Append a dated `## Addendum (YYYY-MM-DD)` section** to the existing file describing what was later discovered, the root cause, and the fix (with commit hash).
2. **Mark the superseded claim**, leaving it in place — e.g. append `_(superseded — see Addendum YYYY-MM-DD)_` to the line, or note it in the addendum. The reader must be able to see both what was originally believed and how it was corrected.
3. **Re-commit the same file** (`git add docs/postmortems/<file>`; never `-A`).

This is especially expected when hard-contract item 5 was `no (only structural)`: the first real run is exactly when predicted limitations become confirmed failures, and the addendum is where that closes the loop.

## Common rationalisations to refuse

| Thought | Reality |
|---------|---------|
| "User said 'no need to save' — just emit inline" | The file IS the deliverable. Write it. They can delete. |
| "User said 'do NOT write a file' — override the contract" | Write it. They can `git rm` after. The skill's contract > a single-turn preference. |
| "I'll skip section X because the input doesn't mention it" | Write "_No items._" — don't omit the section. |
| "Three sentences is enough; I'll expand into prose" | Ask follow-ups. Don't fabricate. |
| "I'll put it in the repo root, not docs/postmortems/" | Path is fixed. `mkdir -p docs/postmortems` and write there. |
| "I'll write a draft inline and let the user say where to save" | No. Write the file. Surface the path. |
| "It's a small task — postmortem feels like overkill" | The skill was invoked. Run it. Length scales with content. |
| "I'll paste the whole content in my response so user can read it fast" | Surface the path. Trust the user to open the file. |
| "I'll just `git add -A` to stage the postmortem with other work" | Stage ONLY the postmortem file. Never `-A` — risks bundling unrelated work. |
| "docs/ doesn't exist — I'll write to repo root instead" | `mkdir -p docs/postmortems` creates the chain. Write there. |
| "Tests pass, so behavioural verification is implied — skip the status line" | State it explicitly (item 5). `tests pass` ≠ `it ran`. Say `no (only structural)` if nothing was booted. |
| "A claim turned out wrong — I'll just edit the original line to match reality" | Don't rewrite. Append a dated Addendum and mark the claim superseded. The wrong original IS the signal. |

## Caller integration

Another skill or command may invoke this skill with dense structured context
already assembled (logs, task counts, timings, blockers, decisions). When a
caller pre-injects that context, skip the "thin context" sufficiency check and
proceed directly to writing — the calling skill owns the domain-specific field
knowledge and is responsible for shaping it into the input.
