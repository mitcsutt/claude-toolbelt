# noise-filter accounting

Verified on 2026-11-19; re-check live before a decision depends on a volatile fact (platform behaviour, tool versions).

The bookkeeping runs in code, so it doesn't depend on you (NF11). `${CLAUDE_PLUGIN_ROOT}` is substituted in agent content, so the commands below get the installed path (NF15).

## Allowed commands

**T1:**
- `node "${CLAUDE_PLUGIN_ROOT}/lib/report-parse.mjs" findings|merges|check-triage …`
- `git show <ref>:<path>`, `git diff`, `git log`, `git grep`
- `grep`, `sed -n`, `wc`, `ls`, `head`, `cat`
- `<tool> --version` and `<tool> --help`
- `node -e '<pure expression>'` with no `require` or `import` and no file or network access, for example `node -e 'console.log(parseInt("08"))'`

**T2 adds:** `mktemp -d`, copying cited files into that directory, and running a command a report quotes, from inside it, only if it touches no network, installs nothing and starts no service. At most 5 re-runs.

Never anything else.

## Commands and output shapes

```
node "${CLAUDE_PLUGIN_ROOT}/lib/report-parse.mjs" findings --shuffle --compact <report.md>...
node "${CLAUDE_PLUGIN_ROOT}/lib/report-parse.mjs" merges <report.md>...
node "${CLAUDE_PLUGIN_ROOT}/lib/report-parse.mjs" check-triage <triage.md> <report.md>...
```

- `findings` prints a first line `{"order":[…],"seed":N}`, then one compact JSON object per finding (`key, sev, conf, where, title`), in that order. `--seed N` reproduces an earlier order. A report that doesn't parse is named on stderr.
- `merges` prints one object per candidate group (`keys, path, lines, reviewers, sevMax, sevSpread`): findings at the same path within 5 lines of another member (groups chain, so the ends can be further apart). Every group is only a candidate.
- `check-triage` prints `accounted <a>/<n> · fix … / decide … / worth … / pre-existing … / dropped rows … (findings …)`, then one line per problem (`missing:`, `duplicated:`, `unknown:`, `tag:`, `cause:`, `format:`) and `warn:` lines for each item over budget, an At a glance block over 9 lines, and a Fix section out of severity order. It exits 1 while any problem line remains; `warn:` lines don't change the exit code.

## Sources rules

- Every input finding appears exactly once: on one `- Sources:` line, or in the first cell of one Dropped row.
- Written out in full as `<reviewer> F<n>`, comma-separated. No ranges (`F2–F4`) and no bare `F3`.
- A finding exposed by your own spot-check: `new (spot-check of <reviewer> F<n>)`. It counts toward no reviewer.
- A same-cause clause, `(same cause: <clause>)`, on every Sources line with two or more keys.
- Mentions in prose, At a glance or Coverage don't count.

## No-shell fallback (T0)

1. Build the index by hand: list `<reviewer> F<n>` for each report, in the order given, with its severity, and note "order not shuffled" on the `Tier:` line.
2. Before writing, tick each key off against your Sources lines and Dropped cells.
3. Under Coverage, list `node <plugin>/lib/report-parse.mjs check-triage <triage> <reports>` as not run, with the real paths.
