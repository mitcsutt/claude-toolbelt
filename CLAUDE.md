# claude-toolbelt

A marketplace repo for a small kit of narrow, composable Claude Code
plugins, plus tracked repo-wide config and scripts. See
[`docs/repo-structure.md`](docs/repo-structure.md) for the full annotated
directory tree and what belongs where.

## Before you touch a plugin

Read [`docs/authoring-plugins.md`](docs/authoring-plugins.md) first — it's
the reference for layout, `plugin.json`, marketplace entries,
`${CLAUDE_PLUGIN_ROOT}`, the README skeleton, versioning, and file modes.
Don't restate any of it here; link to it.

## Adding a plugin

- [ ] Create `plugins/<name>/` with `.claude-plugin/plugin.json`
- [ ] Add the marketplace entry — `name`, `source`, `category` only
- [ ] Write `plugins/<name>/README.md` on the skeleton
- [ ] Write `plugins/<name>/tests/all.sh` with ≥1 assertion per product promise
- [ ] Add the row to the root README Plugins table
- [ ] Add the install line to the root README install block
- [ ] Add the Plugin-details section to the root README
- [ ] Run `bash scripts/test-all.sh`

## Changing a plugin

- [ ] Bump the version in `plugin.json` (semver — see `docs/authoring-plugins.md`)
- [ ] Update the plugin's `README.md`
- [ ] Re-run `bash scripts/test-all.sh`
- [ ] For prompt-shaped plugins (`cutthroat`, `postmortem`, `find-docs`), run
      the eval suite — see [`docs/testing.md`](docs/testing.md). Manual only,
      never CI: evals cost money per run.

## Gotchas

- Hooks are `.mjs`, invoked as `node ${CLAUDE_PLUGIN_ROOT}/hooks/<file>.mjs`
  — not `.py`, not bare-executable.
- `plugin.json` beats the marketplace entry at install time; never duplicate
  metadata into the entry.
- `shared-rules.md` reaches subagents via `CLAUDE.md` imports and output
  styles — output styles alone do **not** reach subagents.
- `docs/superpowers/plans/` and `.superpowers/` are gitignored;
  `docs/superpowers/specs/` is tracked.
- macOS ships bash 3.2 (`/bin/bash --version`): no `mapfile`, no
  `declare -A`, no `${var,,}`. Under `set -u`, expanding `"${arr[@]}"` on an
  **empty** array is fatal; `${#arr[@]}` is safe.
- A shell comment starting with `# shellcheck ` followed by prose, rather
  than an actual directive, is parsed as malformed (SC1072/SC1073) and fails
  the file's own lint.
- `scripts/lint.sh` exits 2 when `shellcheck` isn't on PATH — that's a SKIP,
  not a pass.
- `scripts/validate.sh` silently skips the `claude plugin validate --strict`
  check when the `claude` CLI isn't on PATH.
- Never restate a rule that lives in `docs/` — link it. A rule stated twice
  is a rule that will disagree with itself.

## Verification

Never call work done without pasting `bash scripts/test-all.sh` output. It
runs `scripts/validate.sh`, `scripts/lint.sh`, every plugin's
`tests/all.sh`, and `node --test` — see [`docs/testing.md`](docs/testing.md).
