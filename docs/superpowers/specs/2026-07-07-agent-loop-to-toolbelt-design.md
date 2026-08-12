# Design: Move agent-loop (+ deps) into claude-toolbelt

**Date:** 2026-07-07
**Status:** Approved (design), pending plan

## Goal

Land the `agent-loop` plugin in `claude-toolbelt` as a clean, self-contained,
de-personalised, freshly-versioned public plugin — **without** deleting it from
`mitcsutt-claude-plugins` yet (this is step 1: copy + prepare only).

## Constraints / decisions

- **Copy, don't move.** mitcsutt-claude-plugins keeps its copies untouched. No
  deletes this pass.
- **Fresh versioning.** All three plugins start at **`1.0.0`** in toolbelt,
  independent of their mitcsutt versions (agent-loop `0.14.0`, postmortem/
  permission-advisor `0.1.0`).
- **De-personalised.** agent-loop is already free of Moxi/Rise/ActivePipe/Mitch
  references (only `author.name: "Mitchell Sutton"`, which is correct and stays).
- **Deps travel with it, but generic.** agent-loop hard-depends on `postmortem`
  and softly on `permission-advisor`. Both are copied into toolbelt, but any
  agent-loop-specific coupling is **stripped out of them** — they become generic,
  and the loop-specific context is injected *by the agent-loop skills themselves*.
- **superpowers is an external hard dependency** and must be documented as such.
- **Design doc is not committed** (shared-rules: never commit superpowers specs/plans).

## What ships

Three plugins copied into `claude-toolbelt/plugins/`:

| Plugin | Role | toolbelt version |
| --- | --- | --- |
| `agent-loop` | the loop harness + 3 skills | 1.0.0 |
| `postmortem` | generic retrospective generator (agent-loop hard dep) | 1.0.0 |
| `permission-advisor` | generic pre-dispatch permission gate (agent-loop soft dep) | 1.0.0 |

Runtime cruft excluded from the copy: `agent-loop/.claude/loop/` (test-run
artefacts), `web/__pycache__/`, any `*.pyc`.

## Dependency graph (post-decouple)

```
agent-loop ──hard──▶ superpowers        (external; brainstorming, writing-plans, TDD)
           ──hard──▶ postmortem         (bundled in toolbelt; GENERIC)
           ──soft──▶ permission-advisor  (bundled in toolbelt; GENERIC)
```

- `agent-loop-postmortem` skill builds the dense structured context and injects it
  into `/postmortem`. The loop-field knowledge (LOOP_LOG.jsonl, tick counts, etc.)
  lives in the caller, not in postmortem.
- `agent-loop-setup` skill drives the `permission-advisor` call. permission-advisor
  itself knows nothing about loops.

## Decoupling changes

### postmortem/skills/postmortem/SKILL.md (generic)

- L14: `Called by /agent-loop-postmortem with structured log data pre-injected`
  → "Called by another skill/command with structured data pre-injected."
- L41: `Pre-injected input from /agent-loop-postmortem is always sufficient…`
  → generic: "When a caller pre-injects dense structured context, it is sufficient
  — skip the thin-context questions."
- L115–124 `## Caller integration (/agent-loop-postmortem)` section
  → `## Caller integration` — describe the generic contract: a caller may
  pre-inject dense fields; when it does, skip the sufficiency check and write
  directly. Drop the enumerated LOOP_* field list (that knowledge belongs to the
  caller). Keep the "skip thin-context check when caller supplies dense data" rule.
- plugin.json description already generic ("not loop-specific") — verify, keep.

### permission-advisor/skills/permission-advisor/SKILL.md (generic)

- Description: `…or as the final gate of /agent-loop-setup.`
  → "…or as a pre-dispatch gate invoked by another skill/command."
- L18 `/agent-loop-setup is in step 4 (final gate)` → generic caller phrasing.
- L106 `If invoked inside /agent-loop-setup…` → "If invoked by a caller as a gate,
  the report is shown to the user; the user may bypass and proceed."
- L127 `Callers (e.g. /agent-loop-setup) should:` → "Callers should:".
- Test fixtures referencing `refactor-tick`/loop cadence
  (`tests/baseline-scenario-b-red.md`): these are scenario fixtures, not runtime.
  Leave the copied fixtures as-is (they read as generic "an existing loop skill"
  examples and don't create a code dependency) — flagged, not changed, to keep the
  diff minimal.

### agent-loop (consumer side)

- Re-read `agent-loop-postmortem/SKILL.md` and `agent-loop-setup/SKILL.md` to
  confirm they carry every loop-specific field the now-generic deps used to name.
  They already do (postmortem context built in Step 5; permission-advisor driven in
  Step 4). No functional change expected — verification step only.
- plugin.json description: drop "Requires the permission-advisor and postmortem
  plugins." (now bundled) and keep/clarify the superpowers requirement.

## Documentation

### agent-loop/README.md — add a Requirements section

New section near the top:

- **superpowers** (required) — `brainstorming`, `writing-plans`,
  `test-driven-development`. Install: `/plugin marketplace add …` / the canonical
  superpowers install line.
- **postmortem** and **permission-advisor** — bundled in claude-toolbelt; install
  them from the same marketplace. `agent-loop-postmortem` hard-requires
  `/postmortem`; `permission-advisor` is advisory (setup degrades gracefully
  without it).

### claude-toolbelt/README.md — plugins table + Install block

Add three rows to the plugins table and three `/plugin install …@claude-toolbelt`
lines. Keep the existing terse table voice.

### claude-toolbelt/.claude-plugin/marketplace.json

Three new plugin entries (name, description, version `1.0.0`, author, source,
category). Categories: agent-loop → `developer-tools`; postmortem → `productivity`;
permission-advisor → `productivity`.

## Testing / verification

- `bash claude-toolbelt/plugins/agent-loop/tests/all.sh` passes (or documents the
  known `mktemp` sandbox caveat).
- `jq . claude-toolbelt/.claude-plugin/marketplace.json` parses.
- `jq .` on all three copied `plugin.json` files parses; versions all `1.0.0`.
- grep the copied agent-loop tree for personal tokens → only `author.name`.
- grep the copied postmortem + permission-advisor for `agent-loop`/`/agent-loop-`
  → zero (except the flagged, left-as-is permission-advisor test fixtures).

## Out of scope (this pass)

- Deleting anything from mitcsutt-claude-plugins.
- Generalising the mitcsutt copies of postmortem/permission-advisor (toolbelt gets
  the generic versions; mitcsutt divergence is acceptable and temporary).
- Publishing / pushing.
```
