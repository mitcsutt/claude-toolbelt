# Repo structure

The annotated tree below is the target layout for `claude-toolbelt`. If a file
or directory isn't in this tree, ask whether it belongs before adding it.

```
claude-toolbelt/
├── .claude-plugin/marketplace.json    # marketplace manifest — thin, see below
├── .github/
│   ├── workflows/ci.yml               # validate + lint + test-all, on push/PR
│   ├── ISSUE_TEMPLATE/                # bug_report.md, plugin_request.md
│   └── pull_request_template.md
├── CLAUDE.md                          # agent-facing rules and checklists
├── CONTRIBUTING.md                    # human-facing contribution guide
├── LICENSE
├── README.md                          # OSS front door: what this is, install, plugin list
├── shared-rules.md                    # this author's personal Claude Code rules (not repo policy)
├── config/                            # tracked non-plugin configuration
│   ├── README.md
│   └── ccstatusline/                  # statusline config + scripts (see below)
├── docs/                              # this directory — single source of truth for conventions
│   ├── README.md
│   ├── repo-structure.md
│   ├── authoring-plugins.md
│   ├── testing.md
│   └── superpowers/specs/             # tracked design specs (rationale, decisions)
├── plugins/<name>/                    # one directory per plugin — see docs/authoring-plugins.md
├── scripts/                           # repo-wide, not plugin-scoped
│   ├── validate.sh                    # structural invariants (R1, R2, R4, R5, R6, R9)
│   ├── test-all.sh                    # validate + every plugin's tests/all.sh + node --test
│   └── lint.sh                        # repo-wide shell lint — see docs/testing.md
└── tests/lib/assert.sh                # shared bash assertion helper — the only copy (R6)
```

## What belongs where, and what doesn't

- **`plugins/<name>/`** — the unit of distribution. Anything a user installs
  through `/plugin install` lives here. See `docs/authoring-plugins.md` for
  the required internal layout.
- **`config/`** — tracked configuration that is *not* a plugin because
  Claude Code has nowhere to load it from as one. The current occupant is
  `config/ccstatusline/`: Claude Code's `statusLine` setting is **global
  config** (`~/.claude/settings.json`'s `statusLine` key), not something a
  plugin manifest can declare or install into. A statusline can't be shipped
  as a plugin — it can only be shipped as tracked config plus install
  instructions, which is what `config/ccstatusline/` and `config/README.md`
  do.
- **`docs/`** — every repo convention, written once. `README.md` and
  `CLAUDE.md` link into this directory rather than restating what's here
  (R10). `docs/superpowers/specs/` holds tracked design rationale for past
  changes; `docs/superpowers/plans/` and `.superpowers/` are execution
  scaffolding and stay gitignored.
- **`scripts/`** — checks that apply across all plugins, implemented once.
  A check that only makes sense for one plugin belongs in that plugin's
  `tests/`, not here.
- **`tests/lib/assert.sh`** — the one shared assertion helper every plugin's
  `tests/all.sh` sources. Plugins never carry their own copy (R6, enforced by
  `scripts/validate.sh`).

## Related

- [`docs/authoring-plugins.md`](authoring-plugins.md) — how to build something
  that goes in `plugins/`.
- [`docs/testing.md`](testing.md) — how the three enforcement layers relate to
  this structure.
