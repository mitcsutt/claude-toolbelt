# fact-checker sources

Verified on 2026-11-20 (Claude Code 2.1.329, Ubuntu 24.04.4). Re-check live before a finding depends on a volatile fact.

For each domain: how to find the installed version, where the versioned truth lives, and the trap. Evidence IDs resolve in `evidence.md` and `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. The commands here are the ones `probes.md` allows.

## Order of authority

A matching-version probe beats source at the matching tag, which beats the versioned official doc, then the changelog or NEWS, then the latest doc. For a distro build, the distro changelog decides (FC8). A platform verdict from docs alone is labelled `docs-only` (FC11).

## git

- Installed: `git --version`.
- Docs per version: `https://git-scm.com/docs/<cmd>/<version>` (for example `git-switch/2.43.0`).
- Release notes: `https://raw.githubusercontent.com/git/git/v<ver>/Documentation/RelNotes/<ver>.txt`.
- Latest tag: `git ls-remote --tags https://github.com/git/git 'v2.*'` (v2.56.1 on 2026-11-20, volatile).
- Trap: Git 3.0 is unreleased. `Documentation/BreakingChanges.adoc` describes its changes as future ("will be").
- UNVERIFIED: the Apple Git build string on macOS. Run `git --version` there.

## coreutils

- Installed: probe the tool the claim is about, for example `cp --version 2>&1 | head -1`. A `(GNU coreutils) X` line means GNU. `(uutils coreutils)` means the Rust rewrite Ubuntu is moving to, where GNU docs are a lead only. An error means BSD (macOS), and GNU docs don't apply.
- NEWS: `https://raw.githubusercontent.com/coreutils/coreutils/master/NEWS`, with one section per release headed `* Noteworthy changes in release X (date)`.
- Manual: `https://www.gnu.org/software/coreutils/manual/coreutils.html`. It tracks the latest release, so use NEWS for version deltas.
- Distro build: `dpkg-query -W -f='${Version}\n' coreutils`, then `zcat /usr/share/doc/coreutils/changelog.Debian.gz | grep -n '<topic>'`.
- Ubuntu man pages: `https://manpages.ubuntu.com/manpages/<codename>/man1/<cmd>.1.html` (`noble` is 24.04).
- Worked trap: `cp -n`. Upstream 9.2–9.4 exit nonzero when it skips a file, 9.5 reverted that, and Ubuntu 24.04's 9.4 build already exits 0 because of a Debian patch it inherits (FC8).

## bash

- Installed: `bash --version`.
- NEWS: `https://tiswww.case.edu/php/chet/bash/NEWS`, one section per feature release (not per patch level), newest first (`bash-5.3`, `bash-5.2`, …).
- Manual: `https://www.gnu.org/software/bash/manual/bash.html` (latest release only).
- Ubuntu 24.04 ships 5.2.21. macOS `/bin/bash` is 3.2.
- Trap: a 5.3 feature fails on a 5.2 host. That is "observed on 5.2", not WRONG for a claim about 5.3.

## Node

- Installed: `node --version`, `node -p 'process.versions'`.
- Docs per version: `https://nodejs.org/docs/v<ver>/api/<module>.html`.
- Raw doc source at a tag: `https://raw.githubusercontent.com/nodejs/node/v<ver>/doc/api/<module>.md`. Its YAML `changes:` blocks give the release of each behaviour change, for example `typescript.md`: "Type stripping is enabled by default." (v23.6.0, v22.18.0).
- Releases with dates and LTS codenames: `https://nodejs.org/dist/index.json` (`jq` it).
- Pins: `.nvmrc`, `.node-version`, `.tool-versions`, `package.json` `engines` or `volta`.

## Python

- Installed: `python3 --version`.
- Docs per minor version: `https://docs.python.org/<X.Y>/library/<mod>.html`.
- What's new: `https://docs.python.org/<X.Y>/whatsnew/<X.Y>.html`. The 3.15 page says "Python 3.15 was released on 9 October 2026."
- Pins: `.python-version`, `pyproject.toml` `requires-python`, `uv.lock`, `poetry.lock`.

## Docker

- Installed: `docker --version`. `docker version` also queries the daemon, read-only.
- Engine release notes per major: `https://docs.docker.com/engine/release-notes/<major>/` (for example `29`).
- CLI reference: `https://docs.docker.com/reference/cli/docker/<group>/<cmd>/`.
- Compose file reference: `https://docs.docker.com/reference/compose-file/`.
- The docs site tracks the current release, so for an older engine use the release notes.
- Image tag exists: `https://hub.docker.com/v2/repositories/library/<image>/tags/<tag>` (for example `node/24-alpine`). Never `pull`.

## GitHub Actions

- Workflow syntax: `https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax`.
- Hosted runners: `https://docs.github.com/en/actions/reference/runners/github-hosted-runners`.
- Label to image: `https://raw.githubusercontent.com/actions/runner-images/main/README.md` (volatile, FC9).
- Action inputs at the pinned ref: `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/action.yml` (for example `actions/checkout/v4`).
- Latest release: `gh api repos/<owner>/<repo>/releases/latest --jq .tag_name` (actions/checkout was v7.0.1 on 2026-11-20, volatile).

## Claude Code

- Installed: `claude --version`.
- Docs: `https://code.claude.com/docs/en/<page>.md` (latest only, no per-version docs).
- Changelog: `https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md` or `https://code.claude.com/docs/en/changelog.md`.
- Release date: `gh release view v<ver> -R anthropics/claude-code --json publishedAt`.
- Rule (FC11): docs and behaviour can diverge. Label a docs-only verdict `docs-only`. The live test (`claude -p …` in a scratch directory) goes under Coverage and is never run.

## Registries (GET only)

- npm: `https://registry.npmjs.org/<name>` and `https://registry.npmjs.org/<name>/<version>`. Scoped names work as `@scope/name` or `@scope%2fname` (for example `@types/node`). An unknown name returns 404.
- PyPI: `https://pypi.org/pypi/<name>/json` and `https://pypi.org/pypi/<name>/<version>/json`. An unknown name returns 404.
- Docker Hub: as under Docker.
- GitHub repo or action: `gh api repos/<owner>/<repo>`.
- Use `curl`, not `npm view`, which writes the npm cache.
- **The one 404 that is evidence.** A 404 for the exact name in the right ecosystem makes an "X exists" or "install X" claim WRONG, because the registry is the authority on existence (FC6, E34). It applies only when no private registry is configured, so check first: `.npmrc` (`registry=`, `@scope:registry=`), `pip.conf`, `PIP_INDEX_URL` and `[[tool.uv.index]]` in `pyproject.toml`. A web-search miss stays UNVERIFIED. This rule is doctrine built on FC6, not measured.

## Moving pointers: verdicts get `as of <date>`

These change under a review (FC9). The list is doctrine, apart from the runner-labels example:

- runner labels `ubuntu-latest`, `macos-latest` and `windows-latest` (on 2026-11-20 the runner-images README mapped `macos-latest` to macOS 26 Arm64; actions/runner-images#14748 is migrating `ubuntu-latest` to Ubuntu 26.04, planned to finish 2026-11-19, but the README still listed it under 24.04 on 2026-11-20);
- action refs `@v4`, `@main` and `@master`, and image tags `:latest`, `:lts` and `node:24`;
- "current LTS" and LTS codenames;
- unpinned `npx`, `pip install` and `uvx`;
- anything on code.claude.com, model defaults, and prices.
