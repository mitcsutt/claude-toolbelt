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
- Cold (outsider): "Read the problem statement below first and write your cold take before opening <path>.">

## Problem statement   <!-- outsider only -->
<The problem as the user would describe it, with no solution in it.>

## Repo rules that apply
<Paste the hard rules from CLAUDE.md, CONTRIBUTING or linked docs that bear on this artifact, or list the files to read. Plugin agents don't load CLAUDE.md on their own.>

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
