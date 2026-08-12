# claude-toolbelt repo hygiene and consistency — design

**Date:** 2026-08-12
**Status:** approved, pending implementation

## Problem

`claude-toolbelt` grew plugin-by-plugin without a defined structure. The
consequences are observable, not hypothetical:

- **No agent-facing context file.** There is no `CLAUDE.md`. Every agent that
  touches the repo reverse-engineers the conventions from existing plugins, and
  guesses wrong. A recent session produced a plugin with the wrong hook script
  extension in its own spec, caught only by luck at plan time.
- **Metadata drift is live.** `agent-loop` and `postmortem` have descriptions
  that differ between `plugin.json` and `.claude-plugin/marketplace.json` right
  now. Nothing detects it.
- **Documentation misses are structural.** A new plugin was added without its
  root-README row, install line, or detail section, because nothing enumerated
  those steps.
- **Coverage is uneven.** 3 of 6 plugins have a `README.md`. 3 of 6 have tests.
  `find-docs` and `session-timeline` have neither.
- **Gates hide inside plugins.** The repo's real shellcheck gate
  (`--severity=warning`) lives in `plugins/agent-loop/tests/lint.sh` and applies
  only to that plugin.
- **No repo-level test runner.** Each plugin has its own `tests/all.sh`; nothing
  runs them all, so a change can break a sibling plugin unnoticed.
- **`tests/assert.sh` is duplicated** byte-identically between `agent-loop` and
  `cutthroat`.
- **File modes are arbitrary.** `plugins/agent-loop/tests/` contains both 644
  and 755 `.sh` files.
- **No `LICENSE` file**, though the README claims MIT.
- **Design rationale is unrecoverable.** `docs/superpowers/` is gitignored, so
  the "why" behind merged code exists nowhere in history.

## Evidence gathered

All of the following was verified by executing the local `claude` CLI against
this repository, not recalled.

### Official tooling already exists

| Command | Verified behaviour |
|---|---|
| `claude plugin validate <path>` | Validates a plugin or marketplace manifest. |
| `claude plugin validate --strict` | Help text: *"Treat warnings as errors (exit 1). Use in CI to fail on unrecognized fields, missing metadata, and other issues that the runtime tolerates."* |
| `claude plugin tag [path]` | Creates a `{name}--v{version}` git tag, *"validating that plugin.json and any enclosing marketplace entry agree"*. |
| `claude plugin eval [target]` | Runs eval cases from `evals/**/case.yaml` or `evals/**/prompt.md` + `graders/*.md`. Supports `--threshold`, `--json`, `--ablation with-without`, `--max-cost-usd`. |

Current results against this repo:

- All 6 plugin manifests pass `validate --strict`.
- **`.claude-plugin/marketplace.json` FAILS `validate --strict`** — warning:
  *"No marketplace description provided."*
- `claude plugin tag --dry-run` passes for all 6 plugins (versions agree), and
  **did not catch the two drifted descriptions**.

### plugin.json is authoritative over the marketplace entry

A fixture whose marketplace entry disagreed with `plugin.json` produced:

> `plugins[0].version: Entry declares version "9.9.9" but
> plugins/demo/.claude-plugin/plugin.json says "1.0.0". At install time,
> plugin.json wins (calculatePluginVersion precedence) — the entry version is
> silently ignored. Update this entry to "1.0.0" to match.`

and from `tag`:

> `plugin.json wins at install time, so update the marketplace entry to "1.0.0"
> (or remove it) before tagging.`

Two further facts established by fixture:

- A marketplace entry of only `{ "name": ..., "source": ... }` passes
  `validate --strict` cleanly. Duplicated metadata is **optional**.
- Version drift **is** reported; description drift is **not** — a fixture with a
  deliberately different entry description produced no warning at all. This is
  precisely the hole the two real drifted descriptions fell through.

### Executable bits

Every hook is invoked as `node ${CLAUDE_PLUGIN_ROOT}/hooks/<file>.mjs`, so the
executable bit is inert for hooks — but `config/ccstatusline/settings.json`
invokes `ccsl-*.sh` by bare absolute path, where the bit **is** required. The
correct rule is therefore not "modes are meaningless" but "755 only where the
file is invoked directly".

### CI safety of the CLI

`claude plugin validate --strict` returned exit 0 with `HOME` and
`CLAUDE_CONFIG_DIR` redirected to an empty directory and `ANTHROPIC_API_KEY` /
`CLAUDE_CODE_OAUTH_TOKEN` unset — strong evidence it needs no authentication.
To be confirmed on the first real CI run.

### Observed conventions in public marketplace repos

Four public repos examined by fetching their trees and files directly
(`gh api repos/<r>/git/trees/HEAD?recursive=1`), selected by star count from
`gh search repos "claude-code plugin marketplace"`:

| Repo | ★ | plugins/ layout | per-plugin README | CLAUDE.md | docs/ | CI validation | tests |
|---|---|---|---|---|---|---|---|
| `trailofbits/skills-curated` | 482 | yes | yes (all) | yes | no | yes (3 workflows) | none |
| `ivan-magda/claude-code-plugin-template` | 63 | yes | yes | no | yes | yes | none |
| `obra/superpowers-marketplace` | 1206 | n/a — points at external repos | n/a | no | no | no | none |
| `LeeJuOh/claude-code-zero` | 51 | yes | yes | no | no | — | none |

Confirms R1 (`plugins/<name>/`), R4 (per-plugin README), the root `CLAUDE.md`,
the `docs/` directory, `LICENSE`, and CI-validated manifests as settled
convention.

Two findings that cut the other way, and are the reason this spec does not
simply copy them:

1. **None of the four has any automated test suite for its plugins, and none
   uses `evals/`.** R5 and D9 put this repo ahead of observed practice rather
   than behind it. There is no established convention to follow, so the
   testing design is derived from the official tooling instead.
2. **All of them hand-roll manifest validation** — `trailofbits` in Python
   (`python3 -m json.tool`, plus a ~30-line script checking each marketplace
   entry has a matching directory and `plugin.json`), `ivan-magda` in jq
   (per-field `jq -e ".name"` checks). **None invokes `claude plugin
   validate`.** Using the official CLI replaces all of that hand-rolled
   checking, and catches the version-precedence class of bug their scripts
   cannot see.

Worth adopting from `trailofbits/skills-curated@.github/workflows/validate.yml`
— credible CI hygiene from a security firm:

```yaml
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
steps:
  - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2
    with:
      persist-credentials: false
```

SHA-pinned actions, least-privilege token, no credential persistence,
cancel-in-progress concurrency.

Also worth noting from `trailofbits/skills-curated@CLAUDE.md`: its content is
resources → technical reference → structure diagram → naming conventions, and
it calls out the gotcha that component directories must sit at the plugin root,
**not** inside `.claude-plugin/`. That shape — agent-facing correctness rules
and gotchas, not prose — is the model for this repo's `CLAUDE.md`.

## Decisions

- **D1** — Mechanical enforcement via root scripts plus GitHub Actions CI.
- **D2** — Testing unifies on the existing stack: shared bash assertion helper +
  `node:test` for `.mjs`. No new dependency.
- **D3** — `docs/superpowers/specs/` becomes tracked; `plans/` and
  `.superpowers/` stay ignored.
- **D4** — Add CI workflow, `CONTRIBUTING.md`, issue/PR templates, `LICENSE`.
- **D5** — Fixed 8-section per-plugin README skeleton.
- **D6** — Per-plugin semver, bumped on any behaviour change.
- **D7** — `config/` stays as the home for tracked non-plugin config.
- **D8** — Marketplace entries slim to `{name, source, category}`. Metadata
  duplication is deleted rather than policed.
- **D9** — `claude plugin eval` is adopted for prompt-shaped plugins, with
  `evals/` suites committed. **It does not run in CI** — cost. Run manually.

## Target structure

```
claude-toolbelt/
├── .claude-plugin/marketplace.json
├── .github/
│   ├── workflows/ci.yml
│   ├── ISSUE_TEMPLATE/{bug_report.md,plugin_request.md}
│   └── pull_request_template.md
├── CLAUDE.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
├── shared-rules.md
├── config/
│   ├── README.md
│   └── ccstatusline/
├── docs/
│   ├── README.md
│   ├── repo-structure.md
│   ├── authoring-plugins.md
│   ├── testing.md
│   └── superpowers/specs/        (tracked)
├── plugins/<name>/
│   ├── .claude-plugin/plugin.json
│   ├── README.md
│   ├── evals/                    (prompt-shaped plugins)
│   ├── skills/ hooks/ agents/ commands/ lib/ templates/ references/
│   └── tests/all.sh
├── scripts/{validate.sh,test-all.sh,lint.sh}
└── tests/lib/assert.sh
```

### The anti-drift principle

`docs/` owns every convention. `README.md` and `CLAUDE.md` **link** to it and
never restate it. This is the direct fix for the class of bug where a README
sentence describing another file's contents silently became false.

## Ruleset

| # | Rule | Enforced by |
|---|---|---|
| R1 | Plugin lives at `plugins/<name>/`; dir name == `plugin.json` name == marketplace entry name | `scripts/validate.sh` |
| R2 | `plugin.json` is the sole source of plugin metadata; marketplace entries carry only `{name, source, category}` | `validate.sh` + `claude plugin validate --strict` |
| R3 | Marketplace manifest passes `claude plugin validate --strict` (requires a top-level `description`) | CI |
| R4 | Every plugin has a `README.md` on the 8-section skeleton | `validate.sh` |
| R5 | Every plugin has `tests/all.sh` with ≥1 behavioural assertion per product promise its README states | `scripts/test-all.sh` (presence), review (substance) |
| R6 | Bash tests source the shared `tests/lib/assert.sh`; no local copies | `validate.sh` |
| R7 | shellcheck `--severity=warning` passes repo-wide | `scripts/lint.sh`, CI |
| R8 | Per-plugin semver, bumped on any behaviour change; released via `claude plugin tag` | review + `tag` |
| R9 | Mode 644 by default; 755 only for files invoked directly (`run.sh`, `tests/all.sh`, `config/ccstatusline/ccsl-*.sh`) | `validate.sh` |
| R10 | `docs/` is the single source of truth; `README.md`/`CLAUDE.md` link, never restate | review |
| R11 | Adding or changing a plugin follows the `CLAUDE.md` checklist | `validate.sh` mirrors every mechanically checkable step |

### Per-plugin README skeleton

```
# <plugin-name>

One-line purpose.

## Why
## What it ships      table: component | kind | what
## Install
## Usage
## Configuration      env vars, settings.json keys
## Tests
## Related
```

Sections with nothing to say are omitted; they are never reordered or renamed.
`validate.sh` checks that the title, `## Install`, and `## Tests` sections exist
and that headings that do appear are drawn from this set in this order.

## Testing convention

Three layers, with no duplication between them:

1. **`scripts/validate.sh`** — repo-wide structural invariants, implemented once
   and looped over all plugins. Wraps `claude plugin validate --strict` and adds
   what it does not cover: dir/name agreement, README skeleton, absence of
   duplicated marketplace metadata, no local `assert.sh` copies, file modes,
   `SKILL.md` frontmatter name matching its directory.
2. **`plugins/<x>/tests/all.sh`** — behavioural assertions specific to that
   plugin. This is where "one assertion per product promise" lives. Sources
   `tests/lib/assert.sh` from the repo root.
3. **`scripts/test-all.sh`** — runs `validate.sh`, then every plugin's
   `tests/all.sh`, then `node --test` across `**/*.test.mjs`.

Deliberately *not* a per-plugin `manifest.test.sh` copied six times — those
checks belong in layer 1, implemented once.

**Evals** are a fourth, manual layer. Prompt-shaped plugins (`cutthroat`,
`postmortem`, `find-docs`) get an `evals/` suite run with
`claude plugin eval <path> --threshold <n>`. Committed to the repo, documented
in `docs/testing.md`, and **excluded from CI** per D9.

## CI design

`.github/workflows/ci.yml`, on push and pull request:

1. Install Claude Code CLI.
2. `claude plugin validate --strict` on the marketplace manifest and each plugin.
3. `scripts/lint.sh` — shellcheck `--severity=warning`.
4. `scripts/test-all.sh` — validate + all plugin suites + `node --test`.

No eval step. No secrets required.

## Work implied by adopting this

Ordered, and each item traces to a rule above:

1. Add top-level `description` to `marketplace.json` (**currently failing
   `--strict`**), and slim all 6 entries to `{name, source, category}`.
2. Promote `tests/assert.sh` to `tests/lib/assert.sh`; repoint `agent-loop` and
   `cutthroat`; delete both copies.
3. Promote `agent-loop/tests/lint.sh` to `scripts/lint.sh`, widened repo-wide.
4. Write `scripts/validate.sh` and `scripts/test-all.sh`.
5. Write `README.md` for `find-docs`, `session-timeline`, `postmortem`;
   restructure the existing 3 onto the skeleton.
6. Give `find-docs`, `session-timeline`, `postmortem` a `tests/all.sh`.
   `postmortem`'s current `tests/` holds only non-executable markdown baselines.
7. Normalise file modes per R9.
8. Write `docs/` (`repo-structure.md`, `authoring-plugins.md`, `testing.md`).
9. Write `CLAUDE.md` and rewrite `README.md` as an OSS front door.
10. Add `.github/`, `CONTRIBUTING.md`, `LICENSE`.
11. Update `.gitignore` — ignore `docs/superpowers/plans/` and `.superpowers/`,
    track `docs/superpowers/specs/`.
12. Author `evals/` suites for the prompt-shaped plugins.

## Statusline portability (done)

`config/ccstatusline/settings.json` hardcoded three
`/Users/mitchellsutton/...` absolute `commandPath` values, so the tracked config
only worked on one machine.

Established from the upstream source (`sirmalloc/ccstatusline`), not assumed:

- `src/widgets/CustomCommand.tsx:68` runs
  `execSync(item.commandPath, { …, env: process.env, … })` with no `shell:
  false` and no `cwd`. `execSync` goes through `/bin/sh -c`, so `commandPath` is
  a **shell command string**: `$VAR` and `~` expand, and the executable bit is
  genuinely required (confirming R9 for these files).
- `src/utils/config.ts:30` — `DEFAULT_SETTINGS_PATH = ~/.config/ccstatusline/settings.json`.
- `src/utils/config.ts:87-106` — writes resolve symlinks
  (`resolveAtomicWriteTarget` / `resolveSymlinkTarget`), so a symlinked config
  is written through to its real target. Temp files are created in the target's
  directory.

Fix: each `commandPath` became
`"${CCSL_HOME:-$HOME/.config/ccstatusline}/ccsl-<name>.sh"`, and the documented
install symlinks the **directory** rather than just `settings.json`, so the
default resolves with no env var for any clone location. `CCSL_HOME` remains as
an override for anyone keeping the scripts elsewhere.

Verified: all four expansion cases (var set, var unset, path containing spaces,
neither present → exit 127 rather than a silently empty widget), then
end-to-end with `CCSL_HOME` unset against the live
`~/.config/ccstatusline/settings.json` — all three widgets render and the
tracked config contains no `/Users/` paths.

Consequence: `.gitignore` now excludes `config/ccstatusline/*.bak` and `*.tmp`,
since that directory is now ccstatusline's config dir.

## Known risks

- ~~`config/ccstatusline/settings.json` hardcodes absolute paths.~~ **Fixed** —
  see "Statusline portability" below.
- **CI depends on the `claude` CLI being installable and auth-free on a
  runner.** Evidenced locally; unconfirmed on GitHub Actions until first run.
- **Eval suites cost money to run** and are excluded from CI, so nothing forces
  them to stay green. Mitigation: `docs/testing.md` states they are run before a
  version bump on a prompt-shaped plugin.
