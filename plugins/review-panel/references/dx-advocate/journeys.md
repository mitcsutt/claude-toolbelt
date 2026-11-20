# dx-advocate journeys

Verified on 2026-11-19. Re-check live before a finding depends on a volatile fact (marked volatile).

How to walk the journeys in step 4, after `doc-checks.md` has filled the resolution table. Evidence IDs resolve in `evidence.md` (this directory). Cite resolution rows in every step you mark; a step you couldn't settle is UNVERIFIED, never `ok`.

## Journey order

1. **Setup**, deepest (DX1, DX2, DX11): from a fresh clone to a working local copy, prerequisites included.
2. **First run**: the first command after setup, and how the user sees it worked.
3. **The everyday loop**: edit, test, lint, commit, the commands the docs and rules tell people to run.
4. **Upgrade from the previous version**: an existing install, with its old config, env and habits.
5. **The unattended run**: anything the repo documents running headless, scheduled or in a pipeline.
6. **Recovery**: what the user does when a step fails.
7. **Removal**: uninstall, and what is left behind.

Walk only the journeys the change touches, and name the ones you skipped and why.

## Per-step questions

Cognitive-walkthrough questions (doctrine, R14 F14), asked for each step, as each user:

- Does the user know what to do here?
- Can they do it with what is written? Every prerequisite named, every command and flag resolved.
- Will they see that it worked? An observable done-state: output, a file, an exit code.
- If it fails, does the message say what failed, where, and what to do next (DX14)?

Mark the step `ok`, `stuck: <why>` or `misled: <by what>`, for the engineer and the unattended agent separately.

## Unattended pass

For each step an agent would follow:

- The done-state is observable from output or the exit code.
- The failure branch doesn't need a human.
- No "ask", "confirm" or "wait for" where the repo documents `-p`, cron, CI or `dontAsk` runs. `dontAsk` denies `AskUserQuestion` even when it is allowed (DX13, volatile: re-check the permission-modes docs).
- A documented check that exits 0 doing nothing is a false green for an agent's done-check (DX15).

A dead end here is your finding, as a journey consequence. The rewording of agent-loaded text is rules-lawyer's: route it there.

## Upgrade pass

For each name check 1 or check 8 found renamed or removed: does the old value fail loudly, get migrated, or get silently ignored or defaulted? Silent counts as "silently wrong" severity. CHANGELOG wording that calls a user-visible rename "internal" is part of the finding.

## Severity in this lens

The definitions are in the shared review method. In this lens:

- **BLOCK:** every user following the docs is blocked at setup or upgrade, or is silently wrong where harm follows (runs against the wrong database, a false-green check gates merges).
- **SHOULD:** a step fails loudly with a message that doesn't say what to do; a documented flag or target doesn't exist; an unattended run dead-ends.
- **COULD:** a pointer or wording fix with an obvious workaround.

Grade by the user's consequence (blocked > silently wrong > slowed > cosmetic), not by how many you found (DX9, DX18).

## Follower scenario format

At most 2, under Coverage, only when a BLOCK or SHOULD candidate turns on how an agent would read a step and no check settles it:

```
journey · line under test (path:line, quoted) · instructions as the agent receives them · task · environment facts · expected behaviour, written before anyone runs it · why no check settles it
```

Add one line: "Run with review-panel:instruction-follower per `references/rules-lawyer/followers.md`, 3 runs." It is a pointer for the reader; you don't load that file and you don't run the scenarios.
