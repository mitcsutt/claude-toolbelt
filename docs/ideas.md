# Ideas

Ideas are GitHub issues carrying the `idea` label, filed with the form at
`.github/ISSUE_TEMPLATE/idea.yml` or by the repo-local `/idea` skill
(`.claude/skills/idea/`, available only when working in this repo - it is not a
marketplace plugin). Ideas may be solution-open: only **Problem** is required.

## Lifecycle

1. **Logged** - issue open, `Research status: Logged only`.
2. **Researched** - a `### Research` section was added (by `/idea` or by hand);
   edit **Research status** to `Researched`.
3. **Closed** - a PR closes it (`Closes #n`), or it is closed as not planned
   with a comment saying why.

## Related

- [`docs/repo-structure.md`](repo-structure.md) - where the skill and issue templates sit.
- `tests/ideas.sh` keeps the skill and the form from drifting apart.
