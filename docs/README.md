# docs

The single source of truth for `claude-toolbelt`'s repo conventions.
`README.md` and `CLAUDE.md` at the repo root link into this directory rather
than restating what's here (R10) — if you find the same rule written out in
two places, one of them is wrong.

- [`repo-structure.md`](repo-structure.md) — the annotated directory tree:
  what belongs at the top level and why.
- [`authoring-plugins.md`](authoring-plugins.md) — the reference for
  building a plugin: layout, `plugin.json`, marketplace entries,
  `${CLAUDE_PLUGIN_ROOT}`, the README skeleton, versioning, file modes.
- [`testing.md`](testing.md) — the three enforcement layers (structural,
  behavioural, repo-wide runner) plus the manual eval layer.

`superpowers/specs/` holds tracked design specs — the rationale and
decisions behind past changes to this repo. It documents *why*, not *what's
currently true*; for current convention, use the three files above.
