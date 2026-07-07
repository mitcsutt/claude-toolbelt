# Baseline RED Evidence — postmortem

**Dispatched:** 2026-05-21
**Subagent:** general-purpose (no postmortem skill loaded)

## Scenario A — Thin context, no questions asked

Subagent refused to write a file and offered narrative structure. It DID try to ground in commits/plan files first, which is good behaviour.

> "Save location: I would **not** auto-save. Two reasons. (1) Global rule: 'NEVER proactively create documentation files (*.md)...' (2) Subagent rule in this prompt: 'Do NOT Write report/summary/findings/analysis .md files. Return findings directly as your final assistant message.'"

**RED partial:** The fluff failure mode was suppressed (it grounded in real signal). However, the file-write failure mode was confirmed — no agent will write to `docs/postmortems/` without a skill explicitly contracting that as the deliverable. The general-purpose system prompt actively forbids it.

## Scenario B — Skipping the file write

Subagent declined to write file (matching "no need to save" framing + its system rule) and also flagged it had no concrete context.

> "I don't save it — emit inline as plain markdown bullets... However, I can't actually give you the postmortem content. I have no context on a 'test refactor' in this thread."

**RED confirmed:** The file-write contract must come FROM the skill — the user-said-no-need-to-save framing successfully bypassed the persistent file requirement. The skill must establish "the file IS the deliverable" as non-negotiable.

## Scenario C — Fluff over specifics

Subagent refused to fabricate. Asked 6 specific follow-up questions, named the 3-sentence input as "not a postmortem input", flagged the writeup as "theater" if generated cold.

> "Three sentences is not a postmortem input. Writing a full doc from this would be fabrication dressed as analysis."

**Already GREEN-ish:** baseline behaviour here is strong. The skill needs to reinforce the "ask questions when context is thin" instinct + define what "thin" means concretely.

## Verdict

The interesting RED is **file-write avoidance** (Scenarios A + B). The interesting GREEN reinforcement target is **structured 8-section output** + **commit-to-disk contract**. The skill will be written around these.

The fluff RED (Scenario C target) is partially defended by baseline. The skill will codify the procedure but not over-engineer the anti-fluff guards.
