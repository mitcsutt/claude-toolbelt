# fact-checker probes

Verified on 2026-11-20 (Claude Code 2.1.329, Ubuntu 24.04.4). Re-check live before a finding depends on a volatile fact.

The only commands you may run. Anything not listed here goes under `## Coverage` as an exact command. Where each tool's versioned truth lives is in `sources.md`.

## Allowed (read-only)

- **Versions:** `<tool> --version` / `-V`, and only these `version` subcommands: `docker version`, `go version`, `kubectl version --client` (a bare `version` subcommand can write: `yarn version` bumps the package and tags), `node -p 'process.versions'`, `python3 -I -c 'import sys; print(sys.version)'`, `uname -srm`, `cat /etc/os-release`, `sw_vers`, `command -v <tool>`, `dpkg-query -W -f='${Version}\n' <pkg>`, `rpm -q <pkg>`, `brew list --versions <formula>`, `zcat /usr/share/doc/<pkg>/changelog.Debian.gz | grep -n <topic>`.
- **Help:** `<tool> [<sub>] --help`, `git help -a`, `man -P cat <page>`, and `python3 -I -m pydoc <name>` only for a standard-library module name with no `/` (pydoc imports its target, so a path or a third-party package runs that code). Diff the flags a text claims against `--help` output instead of reading prose (FC14).
- **Repo:** `git log`, `git show <ref>:<path>`, `git diff`, `git grep`, `git ls-files`, `git tag --list`, `git describe`, `git rev-parse`.
- **Network GET:** `curl -sSfL --max-time 20 <https-url>` piped only into `grep -n`, `sed -n`, `head` or `jq`. Also `gh api <path>` with no `-X`/`--method`, `-f`/`--raw-field`, `-F`/`--field` or `--input` (any field switches the request to POST), `gh release view … --json …`, and `git ls-remote --tags <url>`.
- **Expressions:** `node -e` / `node -p`, `python3 -I -c` and `bash --norc --noprofile -c`, on snippets that read nothing outside a temp dir and make no network calls.

## Temp-dir protocol

Run `d=$(mktemp -d)` and keep every write inside `$d` (`git -C "$d" init -q` is allowed there). Print the tool version in the same command as the probe. Finish with `rm -rf "$d"` on that path only. Example:

```bash
d=$(mktemp -d); cd "$d" && : > a && : > b && cp --version | head -1; cp -n a b; echo "exit=$?"; cd / && rm -rf "$d"
```

## Version gate

Compare the installed version with the target version before trusting a probe. When they differ, record the result as "observed on vX": the target version's docs or source decide the claim, never the probe. A distro build counts as its own version, so read its changelog (FC8).

## Never run: list under Coverage instead

- Installs: `npm i|install|ci`, `npx`, `pnpm dlx`, `pip install`, `uv pip install`, `uvx`, `brew install`, `apt`, `cargo install`.
- Containers and services: `docker run|pull|build|exec|compose`, `systemctl`, `service`, `kill`.
- Network writes: `curl` with `-X`, `-d`, `--data*`, `-F`, `-T`, `-u` or any auth header or token, and mutating `gh` (`create`, `edit`, `merge`, `close`, `api` with `-X`/`--method` other than `GET`, or with any field).
- Repo state: `git fetch|pull|push|checkout|switch|stash|reset|commit` in the repo.
- `claude -p` or anything else that starts a model session.
- The artifact's own scripts.

## No shell

When Bash is withheld, use WebFetch for sources: ask it for the exact sentences verbatim and quote only those, never its summary (E35). Write every probe you would have run under Coverage, as an exact command.

## T2

At most 3 candidates, at most 4 calls each. A candidate is a WRONG resting on docs-only or version-mismatched evidence, or a load-bearing UNVERIFIED whose settling action is allowed above. Use one method per candidate and name it in the ledger's source cell:

1. **Source at the matching tag:** the raw file at `v<ver>`, then `grep -n` for the behaviour.
2. **Changelog bisect:** the first NEWS or CHANGELOG section that mentions the change.
3. **Temp-dir behaviour probe:** only when the installed version equals the target.
4. **A second independent primary source.**

T2 must not re-verify a CONFIRMED row, hunt a second source for a WRONG already backed by a quoted, version-matched contradiction, re-probe a claim a matching-version probe already settled, or touch a claim whose only open question is a moving pointer: its `as of <date>` covers that.

## Checks not to make

The shared ones live in `${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md`. These are this lens's own:

- Opinions, design judgements and forward-looking estimates. Check their checkable premise instead (FC2).
- Trivially true or textbook claims, unless a version qualifier makes them non-trivial (FC4).
- WRONG for incompleteness ("the doc is less detailed than the code") (E42).
- WRONG from a failed search, a 404 page, an unrendered page, a snippet or a WebFetch summary (E34, E35). The one exception is an exact-name registry 404 (`sources.md`, Registries).
- WRONG from a probe on a different version than the target (FC8).
- Lists labelled illustrative ("e.g.", "such as") read as exhaustive (FC10).
- Re-checking an instruction artifact's claims inside a panel where rules-lawyer is present (FC17).
- Design, cost, security or compatibility judgements: the other lenses own them.
