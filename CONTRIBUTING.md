# Contributing

Thanks for considering a contribution to `claude-toolbelt`. This document
covers the contribution process — propose, build, test, submit. For the
rules a plugin must follow (layout, `plugin.json`, the README skeleton,
`${CLAUDE_PLUGIN_ROOT}`, file modes), see
[`docs/authoring-plugins.md`](docs/authoring-plugins.md); don't expect that
material restated here.

## Proposing a plugin

Open an issue first using the "Plugin request" template before writing code.
It should say what problem the plugin solves and why it belongs in this repo
rather than as its own standalone plugin — this repo is a curated kit, not a
catch-all. A short discussion up front is cheaper than a PR that turns out to
be out of scope.

For a change to an existing plugin, a PR without a preceding issue is fine.

## Building it

Follow [`docs/authoring-plugins.md`](docs/authoring-plugins.md) for the
directory layout, `plugin.json`, the marketplace entry, and the README
skeleton. Follow [`docs/repo-structure.md`](docs/repo-structure.md) if you're
unsure whether something belongs in `plugins/`, `config/`, or `scripts/`.

## The testing bar

Every plugin ships `tests/all.sh` meeting this repo's testing bar: **at least
one behavioural assertion per product promise the plugin's README makes**.
See [`docs/testing.md`](docs/testing.md) for how this fits alongside the
repo-wide structural checks in `scripts/validate.sh`.

Before opening a PR, run everything:

```bash
bash scripts/test-all.sh
```

This runs the structural validator, shell lint, every plugin's `tests/all.sh`,
and the Node test files. It must exit 0. This is also what CI runs on every
push and pull request — nothing else.

If your plugin is prompt-shaped (an output style, a routing skill, anything
graded by how it reads rather than by return codes — `cutthroat`,
`postmortem`, and `find-docs` are the current examples), also run its eval
suite before bumping its version:

```bash
claude plugin eval plugins/<name> --threshold 0.8
```

**This is a manual step you run yourself, never something CI does.** Each
eval case invokes a real agent turn — usually several — plus grading, so it
costs money per run. Don't add it to a CI workflow, and budget for the cost
before running it.

## Versioning

See [`docs/authoring-plugins.md`](docs/authoring-plugins.md#versioning) for
the semver policy (patch/minor/major) and how `claude plugin tag` works. Bump
`plugin.json`'s version for any behaviour change to a plugin you touch.

## Submitting

- Update the plugin's own `README.md` to match what changed.
- If you added a plugin, add its row to the root `README.md` table, its
  install line, and its "Plugin details" section.
- Run `bash scripts/test-all.sh` and paste the output (or confirm it's green)
  in the PR description — the pull request template asks for this.
- Keep the diff scoped to the change described; this repo prefers several
  small PRs over one that touches several plugins at once.
