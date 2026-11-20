# dx-advocate doc checks

Verified on 2026-11-20 (GNU grep 3.11, GNU Make 4.3, git 2.43.0, Node 24.14.1, Linux). Re-check live before a finding depends on a volatile fact (marked volatile).

The read-only commands for step 3 of your method. Run each block from the repo root, after the setup block, and read what it prints: every line is a lead, never a finding on its own (DX6). Evidence IDs resolve in `evidence.md` (this directory). The commands come from R14 §3; where they differ, the check says so. `lib/doc-checks-commands.test.mjs` runs every block against the eval fixture and holds them to the rules below.

## Rules

- Allowed: `git diff|show|log|grep|ls-files|merge-base|rev-parse`, `grep`, `sed -n`, `awk` (print only), `sort`, `uniq`, `comm`, `head`, `tail`, `wc`, `cat`, `ls`, `find` (no `-delete`, no `-exec`), `test`, `[`, `tr`, `cut`, `basename`, `dirname`, `mktemp -d`, `echo`, `read`, `bash -n <script>` (parses, runs nothing), `node -e` only to read a JSON file, and `<runtime> --version` for an installed runtime.
- Redirect only into `"$T/…"` where `T=$(mktemp -d)`, or into `/dev/null`.
- Never run `make` in any form: `-n`, `-p` and `-q` still expand `$(shell …)`. Never run a repo script, `npm|pnpm|yarn` run or install, `docker`, or anything that touches the network.
- Pipe long outputs through `head -50`; say when you truncated.
- In a Claude Code Bash session `grep` can be a shell function that runs an embedded ugrep, which skips gitignored files in `-r` searches. The patterns here give the same hits under it and under GNU grep on the test fixture; when a file you expected is missing from a `-r` result, check whether it is gitignored.
- `$BASE` is the diff base from the brief. When there is none, the setup block picks the merge base with `origin/main`, else `HEAD~1`. For a plan with no diff, take the names it says it renames or removes, and run checks 1 and 8 by hand on those names.

## Setup

```bash
T=$(mktemp -d)
BASE=${BASE:-$(git merge-base HEAD origin/main 2>/dev/null || git rev-parse HEAD~1)}
```

## 1. Stale names after the diff

DX6, DX17. Names that leave the diff and still appear in docs or agent-loaded files. Two changes from R14: the temp dir replaces `/tmp/old`, and an identifier filter keeps env vars, flags, kebab, snake and camel names and drops plain words. Removed lines are taken with `^-` minus the `---` file header, so a removed Markdown bullet (`-- …`) still counts.

```bash
git diff "$BASE" -U0 | grep -E '^-' | grep -vE '^--- (a/|/dev/null)' | grep -oE '[A-Z][A-Z0-9_]{3,}|--[a-z][a-z-]+|[a-z]+[_-][a-z0-9_-]+|[a-z]+[A-Z][A-Za-z0-9]*' | sort -u > "$T/old"
git diff "$BASE" -U0 | grep -E '^\+' | grep -vE '^\+\+\+ (b/|/dev/null)' | grep -oE '[A-Z][A-Z0-9_]{3,}|--[a-z][a-z-]+|[a-z]+[_-][a-z0-9_-]+|[a-z]+[A-Z][A-Za-z0-9]*' | sort -u > "$T/new"
comm -23 "$T/old" "$T/new" > "$T/gone"
while read -r n; do git grep -nF -e "$n" -- '*.md' 'docs/' '.claude/' ':!CHANGELOG*' | head -5; done < "$T/gone"
```

Trap: CHANGELOG and history hits are correct, not stale. A name can leave one file and stay right in another. Read every hit.

## 2. Paths named in docs resolve

R14 §3's path command, made to test each path against the doc's own directory as well as the repo root.

```bash
grep -rnoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '`[.]?[A-Za-z0-9_][A-Za-z0-9_./-]*\.(sh|md|js|mjs|cjs|ts|py|json|toml|ya?ml|example)`' . | while IFS=: read -r f l m; do
  p=$(echo "$m" | tr -d '`')
  test -e "$p" || test -e "$(dirname "$f")/$p" || echo "missing $f:$l $p"
done
```

Trap: a file the docs tell the user to create (`config.json` from `cp config.example.json config.json`) is fine. Generated paths (`dist/`) are UNVERIFIED, not broken. Home and absolute paths aren't matched: list them as UNVERIFIED if a step depends on them.

## 3. Relative markdown links resolve

Each relative link is tested against `dirname` of its file. The pattern needs the target to end at `)` or `#`, so `https:` links don't match; the second command lists them.

```bash
grep -rnoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '\]\([^)#: ]+[)#]' . | while IFS=: read -r f l m; do
  p=${m#??}
  p=${p%?}
  test -e "$(dirname "$f")/$p" || echo "broken $f:$l $p"
done
grep -rnoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '\]\(https?://[^)]+' . | head -20
```

Trap: external URLs are UNVERIFIED (no network here). A link that resolves can still point at the wrong section: read the anchor.

## 4. Make targets named in docs exist

Targets the docs name (in backticks or at the start of a code line) against the Makefile's rules, then `.PHONY` names with no rule. A phony target with no rule prints "Nothing to be done" and exits 0, so a documented check passes while running nothing (DX15).

```bash
test -f Makefile || echo "no Makefile"
grep -hoE '^[A-Za-z0-9_.-]+:' Makefile 2>/dev/null | tr -d : | sort -u > "$T/rules"
grep -hE '^\.PHONY:' Makefile 2>/dev/null | sed -n 's/^\.PHONY:[[:space:]]*//p' | tr ' ' '\n' | grep . | sort -u > "$T/phony"
grep -rhoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '(`|^[[:space:]]*)make [A-Za-z0-9_.-]+' . | sed -n 's/^[`[:space:]]*make //p' | sort -u > "$T/named"
comm -23 "$T/named" "$T/rules" | sed -n 's/^/documented target with no rule: /p'
comm -23 "$T/phony" "$T/rules" | sed -n 's/^/phony with no rule (exits 0, runs nothing): /p'
grep -nE '^-?include' Makefile 2>/dev/null
```

Trap: read every `include`d makefile and pattern rule (`%:`) before calling a target missing. A `.PHONY` list continued with `\` is only half read here.

## 5. Package scripts named in docs exist

```bash
test -f package.json && node -e 'console.log(Object.keys(require("./package.json").scripts||{}).join("\n"))' | sort -u > "$T/scripts"
grep -rhoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '(npm run|pnpm run|yarn run|pnpm|yarn) [A-Za-z0-9:_-]+' . | awk '{print $NF}' | grep -vxE 'run|install|i|add|remove|exec|dlx|ci|test|start|why' | sort -u > "$T/used"
comm -23 "$T/used" "$T/scripts" | sed -n 's/^/documented script not in package.json: /p'
```

Trap: `npm test` and `npm start` aren't matched here, so check them by hand: `npm test` fails with no `test` script, and `npm start` needs a `start` script unless `server.js` exists at the root. `pnpm <script>` works without `run`. A workspace's scripts live in its own `package.json`.

## 6. Script flags named in docs are accepted

Changed from R14: R14's pattern only finds a flag written right after the script path. This one takes every flag on a doc line that names a script, then looks for it as a `case` arm in that script.

```bash
grep -rnE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '(scripts|bin)/[A-Za-z0-9_.-]+' . | while IFS= read -r hit; do
  s=$(echo "$hit" | grep -oE '(scripts|bin)/[A-Za-z0-9_.-]+' | head -1)
  test -f "$s" || continue
  for fl in $(echo "$hit" | grep -oE '(^|[ `(])--?[a-z][a-z0-9-]*' | tr -d ' `('); do
    grep -qE -- "(^|[[:space:]|(])$fl[|)]" "$s" || echo "flag not in a case arm of $s: $fl ($(echo "$hit" | cut -d: -f1-2))"
  done
done
grep -n getopts scripts/* bin/* 2>/dev/null
```

Trap: short flags parsed by `getopts` appear without the dash (`n)`), so read the `getopts` line before reporting one. Never run `--help` to find out: a script without a help arm may run its setup.

## 7. Env vars read by code vs documented

R14 §3's env-var command, compared with `.env.example` and the docs' backticked names. Prints names only.

```bash
grep -rhoE --exclude-dir=node_modules --exclude-dir=.git --include='*.js' --include='*.mjs' --include='*.cjs' --include='*.ts' --include='*.py' 'process\.env\.[A-Z][A-Z0-9_]*|process\.env\[.[A-Z][A-Z0-9_]*|os\.environ(\.get)?[[(].[A-Z][A-Z0-9_]*|getenv\(.[A-Z][A-Z0-9_]*' . | grep -oE '[A-Z][A-Z0-9_]*$' | sort -u > "$T/read"
grep -hoE '^[A-Z][A-Z0-9_]*=' .env.example .env.sample .env.template 2>/dev/null | tr -d = > "$T/doc1"
grep -rhoE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '`[A-Z][A-Z0-9_]{2,}`' . | tr -d '`' > "$T/doc2"
cat "$T/doc1" "$T/doc2" | sort -u > "$T/documented"
comm -23 "$T/read" "$T/documented" | sed -n 's/^/read by code, not documented: /p'
comm -13 "$T/read" "$T/documented" | sed -n 's/^/documented, read by no code: /p'
grep -rnoE --exclude-dir=node_modules --exclude-dir=.git --include='*.js' --include='*.mjs' --include='*.cjs' --include='*.ts' 'env(\.|\[.)[A-Z][A-Z0-9_]*.?\]?[[:space:]]*(\?\?|\|\|)' . | head -20
```

The last command lists reads with a default. Trap: a `??`, `||` or `getenv(…, default)` fallback makes a rename **silent**: the old variable is ignored and the default wins, with no error. Platform vars (`PORT`, `NODE_ENV`, `CI`) need no entry. Never print values.

## 8. Config keys renamed with no migration

Changed from R14: the old keys are extracted (JSON keys and `.key ??`/`.key ||` reads in config and settings files) and each is looked up in the upgrade docs.

```bash
git diff "$BASE" -U0 -- '*config*' '*settings*' | grep -E '^-' | grep -vE '^--- ' | grep -oE '"[a-z][a-z0-9_]*"[[:space:]]*:|\.[a-z][a-z0-9_]*[[:space:]]*(\?\?|\|\|)' | grep -oE '[a-z][a-z0-9_]*' | sort -u > "$T/oldkeys"
git diff "$BASE" -U0 -- '*config*' '*settings*' | grep -E '^\+' | grep -vE '^\+\+\+ ' | grep -oE '"[a-z][a-z0-9_]*"[[:space:]]*:|\.[a-z][a-z0-9_]*[[:space:]]*(\?\?|\|\|)' | grep -oE '[a-z][a-z0-9_]*' | sort -u > "$T/newkeys"
comm -23 "$T/oldkeys" "$T/newkeys" | while read -r k; do
  grep -rqw -- "$k" CHANGELOG* UPGRADING* MIGRATING* docs/ 2>/dev/null || echo "no migration note for removed key: $k"
done
```

Trap: if the loader rejects unknown keys, the rename fails loudly, so read the loader first. A CHANGELOG line that mentions the key but calls the change "internal" is still no migration note.

## 9. Version claims agree

Prints every version source and every package script that passes a runtime flag, so you can compare them with the docs.

```bash
for f in .nvmrc .node-version .tool-versions .python-version; do test -f "$f" && echo "$f: $(cat "$f")"; done
test -f package.json && node -e 'const p=require("./package.json");console.log("engines: "+JSON.stringify(p.engines||{}));for(const [k,v] of Object.entries(p.scripts||{}))if(/ --?[a-z]/.test(v))console.log("script "+k+": "+v)'
grep -rnE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '(Node|Python|Go|Ruby|bash)( version)?[^0-9]{0,4}[0-9]+(\.[0-9]+)*' . | head -20
```

Trap: a feature's minimum runtime version (a flag, an API) needs a primary source. Without one it is UNVERIFIED, with the URL to fetch: fact-checker's lens. A harmless `node --version` tells you what is installed here, not what users run.

## 10. Platform claims vs bashisms

Run when the docs claim macOS (the first command shows whether they do). Changed from R14: the `sed -i` pattern now matches only the GNU form, so BSD's `sed -i ''` isn't flagged.

```bash
grep -rniE --include='*.md' --exclude-dir=node_modules --exclude-dir=.git 'mac ?os|os ?x|darwin' . | head -5
for s in scripts/* bin/*; do test -f "$s" && echo "$s: $(head -1 "$s")"; done
grep -nE 'mapfile|readarray|declare -A|\$\{[a-zA-Z_]+,,\}|readlink -f|date -d|grep -P|stat -c' scripts/* bin/* 2>/dev/null
grep -nE "sed -i[[:space:]]+['\"][^'\"]" scripts/* bin/* 2>/dev/null
```

Trap: `#!/usr/bin/env bash` may find a Homebrew bash 5; `#!/bin/bash` never does on macOS (DX16). A bashism in a script the claimed platform never runs isn't a finding.

## 11. Exit paths say what to do

```bash
grep -nE 'exit [1-9]|>&2' scripts/* bin/* 2>/dev/null | head -50
```

Read each message before its `exit` (DX14): does it say what failed, where, and what to do next? Trap: don't demand a fix hint where the cause isn't knowable; a wrapped tool's own message may be enough. Compare a changed message with the old one (`git show "$BASE":<path>`).

## 12. Human-only steps where runs are unattended

R14 §3's two greps. It's a hit only if both print something.

```bash
grep -rniE 'ask (the )?(user|maintainer|owner|team)|check with|confirm with|wait for (approval|review)' CLAUDE.md AGENTS.md .claude/ docs/ README* CONTRIBUTING* 2>/dev/null | head -20 > "$T/ask"
grep -rnE -- '-p |--print|dontAsk|headless|cron|nightly' .github/ docs/ CLAUDE.md AGENTS.md README* 2>/dev/null | head -20 > "$T/unattended"
if test -s "$T/ask" && test -s "$T/unattended"; then cat "$T/ask" "$T/unattended"; fi
```

Trap: fine when the doc gives an unattended branch ("…; unattended: stop and record the failure"). Whether a permission mode can ask at all is volatile (DX13): re-check the permission-modes docs.

## 13. Removal covers what setup writes

```bash
grep -nE '(>>?[^&]|ln -s|cp |install |mkdir -p|crontab|systemctl|launchctl|git config|\.git/hooks|\$HOME|~/)' scripts/setup* scripts/install* bin/setup* bin/install* 2>/dev/null
grep -rniE -A4 --include='*.md' --exclude-dir=node_modules --exclude-dir=.git '^#+ .*(uninstall|remov)' . | head -20
```

Compare the writes with the uninstall section. Trap: writes inside the clone go with the clone, but hooks under `.git/hooks` stay active until removed, and anything under `$HOME`, `~/`, a crontab or a service manager stays behind.

## 14. Written rules the change could breach

Changed from R14: case-insensitive, and POSIX classes instead of `\s` and `\b`, which POSIX ERE doesn't define. The `( |.*[^a-z])` part also catches a rule whose first word is the keyword ("- Never …").

```bash
grep -niE '^[[:space:]]*([-*]|[0-9]+\.)( |.*[^a-z])(must|always|never|every|don.t|do not)([^a-z]|$)' CLAUDE.md AGENTS.md CONTRIBUTING* docs/*.md 2>/dev/null | head -50
```

Quote each rule in your rule ledger and mark it held or breached against the diff. Agent-behaviour rules go to rules-lawyer.

## Without Bash

Use the Grep tool with `output_mode: content` and the same pattern for checks 2, 3, 4, 6, 7, 10, 11, 12, 13 and 14, and Glob for whether a path exists. Read the Makefile, the scripts' `case` arms and `package.json` directly. Checks 1, 5, 8 and 9 need Bash, or a manual read of a supplied diff (`CHANGE.diff`, PR text) and of `package.json`. If neither is possible, list them under Coverage with the command.

## Other ecosystems

pyproject `[project.scripts]`, the `go.mod` `go` line, and `Cargo.toml` `rust-version` are the equivalents of checks 5 and 9. They are named only: their checks are UNVERIFIED and not researched (R14 is Node, Make and bash centred).
