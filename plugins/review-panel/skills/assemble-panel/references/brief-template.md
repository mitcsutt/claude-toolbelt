# Reviewer brief template

Fill one of these per reviewer. The agent file already holds the persona, lens, output contract and boundaries, so don't restate them. The brief carries only what changes per run.

```markdown
## The goal (understand this first; don't argue against the goal itself)
<One or two sentences: what the work is for and what "done" looks like.
Example: "Let a developer run two stacks side by side on one laptop without port clashes, with no change to the single-stack workflow.">

## What you're reviewing
<Artifact type, and the exact paths, PR numbers, refs or URLs to read.
For a branch state: "Read with `git show <ref>:<path>` or `git diff <base>...<ref>`; don't check out.">

## Context you get
<Choose this on purpose; say what is withheld and why, if anything.
- Full: the artifact, the repo at <path>, the ticket or thread at <link>.
- Artifact only: "Read only <path>. Don't explore the repo; judge the document as written."
- Cold (outsider): "Read only the problem statement below and the paths it lists. Write your cold take to your report file before opening <path> or exploring the repo.">

## Problem statement   <!-- outsider only -->
<The problem as the user would put it before any design existed. Four parts:
- Goal: the outcome, in the user's words.
- Hard constraints: each quoted with its source (`path:line`, or "user, this conversation"). Mark anything you inferred as "inferred".
- Who uses it, and how often.
- Paths the outsider may read before its cold take: problem docs only, never the artifact.
Leave out: the artifact's name and its component, script or tool names; the chosen approach; alternatives already rejected; and who decided ("we decided", "the team agreed"). If the goal can't be stated without naming the solution, state the problem one level up and say so.>

## Author's rationale   <!-- outsider only, optional -->
<Why the author chose this route and what they rejected, quoted from the artifact or thread. The outsider reads this only after its cold take and treats every line as a claim.>

## Repo rules that apply
<Rules that bear on this artifact and live outside the session's CLAUDE.md chain, which reviewers already load: CONTRIBUTING, linked docs, another repo's CLAUDE.md. Paste them or list the files to read.>

## Lens boundaries (this panel)
<Panel: <agent>, <agent>, …
Claim checks belong to <claim owner>. Don't re-verify technical claims yourself. Raise a claim only if its truth would change a finding in your own lens, then name that finding and mark the claim "for <claim owner> to verify".
For the claim owner itself: "You own claim checks for this panel.">

## Tier   <!-- any reviewer whose agent file defines tiers, optional -->
<Override its own tier choice: "static only", or a follower budget such as "at most 2 scenarios × 3 runs".>

## Focus for this run   <!-- optional -->
<Anything the user specifically asked this reviewer to look at, or a known-hot area.>

## Report
Write to: <scratchpad>/panel-<slug>/<agent>.md
Then return one line, as your agent file describes.
```

## Brief for noise-filter

```markdown
## The goal (fixed)
<same goal statement>

## Reports
- <agent>: <path>
- ...

## The author's own known decisions   <!-- optional -->
<Choices already made on purpose, so findings that argue against them land under Decisions, not Fix.>

## Triage
Write to: <scratchpad>/panel-<slug>/triage.md
Then return one line, as your agent file describes.
```
