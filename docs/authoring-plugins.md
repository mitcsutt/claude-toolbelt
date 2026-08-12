# Authoring a plugin

The reference for building something that lives under `plugins/<name>/`.

## Canonical plugin layout

```
plugins/<name>/
├── .claude-plugin/
│   └── plugin.json           # the only file under .claude-plugin/
├── README.md                 # 8-section skeleton — see below
├── skills/<skill-name>/SKILL.md
├── hooks/                    # hooks.json + the scripts it invokes
├── agents/
├── commands/
├── lib/
├── templates/
├── references/
├── evals/                    # prompt-shaped plugins only, see docs/testing.md
└── tests/
    └── all.sh                # required — the plugin's test entrypoint (R5)
```

**Gotcha:** `skills/`, `hooks/`, `agents/`, and `commands/` live at the
**plugin root**, sibling to `.claude-plugin/` — not nested inside it.
`.claude-plugin/` holds exactly one file, `plugin.json`.

## `plugin.json`

Fields in use across this repo's six plugins:

```json
{
  "name": "cutthroat",
  "version": "1.0.0",
  "description": "...",
  "author": { "name": "..." }
}
```

`name` must match both the plugin's directory name and its marketplace entry
name (R1, enforced by `scripts/validate.sh`).

**`plugin.json` is authoritative over the marketplace entry.** Verified
against a fixture whose marketplace entry disagreed with its `plugin.json`
version — `claude plugin validate --strict` reported:

> `plugins[0].version: Entry declares version "9.9.9" but
> plugins/demo/.claude-plugin/plugin.json says "1.0.0". At install time,
> plugin.json wins (calculatePluginVersion precedence) — the entry version is
> silently ignored. Update this entry to "1.0.0" to match.`

Practical consequence: never duplicate `version` or `description` into the
marketplace entry to "keep it in sync" — there is nothing to sync. Version
drift between the two is caught by `claude plugin validate --strict`;
description drift is not caught by anything, which is why the marketplace
entry doesn't carry a description at all (see below).

## Marketplace entries

`.claude-plugin/marketplace.json` at the repo root lists every plugin, and
each entry carries exactly three keys:

```json
{ "name": "cutthroat", "source": "./plugins/cutthroat", "category": "productivity" }
```

`name`, `source`, and `category` — nothing else. `scripts/validate.sh` fails
any entry with a fourth key (R2). This isn't a policy of keeping duplicated
metadata in sync; it's the deletion of the duplication. A fixture entry with
only these three keys passes `claude plugin validate --strict` cleanly, so
there is no metadata this format is missing.

## `${CLAUDE_PLUGIN_ROOT}`

Hooks reference their own scripts through this variable rather than a
relative or absolute path, since a plugin can be installed anywhere. Example,
from `plugins/cutthroat/hooks/hooks.json`:

```json
"command": "node ${CLAUDE_PLUGIN_ROOT}/hooks/subagent-brief.mjs"
```

## The README skeleton

Every plugin's `README.md` follows this fixed 8-section skeleton:

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

Rules, enforced by `scripts/validate.sh` (R4):

- The first line must be exactly `# <plugin-name>`.
- `## Install` and `## Tests` are mandatory; every other section may be
  omitted if there's nothing to say.
- Whichever sections **are** present must come from the set above, in that
  order. A section is never renamed or reordered — if a plugin has nothing
  for `## Configuration`, it's dropped, not moved.

## Versioning

Semver, bumped on any behaviour change (R8):

- **patch** — bug fixes, doc-only changes.
- **minor** — a new skill, hook, or capability.
- **major** — a breaking change to invocation or configuration.

Release with `claude plugin tag [path]`, which creates a `{name}--v{version}`
git tag after validating that `plugin.json` and the marketplace entry agree.
`claude plugin tag --dry-run` is a good pre-check before actually tagging.
Version bumping and tagging are review-gated, not machine-enforced.

## File modes

Tracked files default to mode `644`. `755` is reserved for files that are
**invoked directly** rather than through an interpreter — the allowlist in
`scripts/validate.sh` (R9). Everything else executable-looking should still
be `644`, because it's invoked as `node ${CLAUDE_PLUGIN_ROOT}/hooks/foo.mjs`
or `bash tests/all.sh`, where the interpreter does the work and the
executable bit is inert.

The one case where the bit is load-bearing rather than cosmetic:
`config/ccstatusline/settings.json` invokes its `ccsl-*.sh` scripts by bare
absolute path. Traced to the upstream source
(`sirmalloc/ccstatusline`, `src/widgets/CustomCommand.tsx`): it calls
`execSync(item.commandPath, …)` with no explicit interpreter, which shells
out via `/bin/sh -c` and requires the target to be directly executable. That
is why `config/ccstatusline/ccsl-*.sh` sit on the R9 allowlist alongside
`run.sh`, which a shell also invokes directly (e.g. `./run.sh`).

## Related

- [`docs/repo-structure.md`](repo-structure.md) — where a plugin sits
  relative to the rest of the repo.
- [`docs/testing.md`](testing.md) — the three enforcement layers, and where a
  plugin's `tests/all.sh` fits among them.
