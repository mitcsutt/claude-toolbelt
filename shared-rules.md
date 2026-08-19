# Shared Claude Code Rules

## Agent voice

Owned by the `cutthroat` plugin (`plugins/cutthroat/`), which ships it as an
output style. Enable with `"outputStyle": "cutthroat:cutthroat"` in
`~/.claude/settings.json`.

## Tooling

- Use Skills from `~/.claude/skills/` when task matches (e.g. `/systematic-debugging` for bugs, `/go-testing` for tests)
- Makefile exists → prefer targets (`make help`) over direct calls (`make test` not `go test ./...`)
- Edit tool over `sed`. Search tool over `grep`/`rg`

## MCPs and external blockers

- MCP fails (Unauthenticated/error) → stop and surface. Critical for auth MCPs (CI/CD, issue trackers, observability, analytics)
- Never substitute curl, browser automation, web search, or cached research for a requested-but-unavailable MCP
- Tool/MCP errors twice in same session → STOP and surface
- Docs source inaccessible, MCP unauthenticated, reverse-engineering minified bundles → STOP and surface
- "I'll just curl this" / "I'll just guess from training data" / "I'll just reverse-engineer" without explicit user approval = forbidden

## Verification before declaring done

- Never claim "complete"/"done"/"fixed"/"passing"/"working" without citing verification output — test counts, CI status, screenshot evidence, diff numbers, command output. Evidence visible in response, not asserted
- Can't verify → say "unverified" explicitly. List verification still needed. No confident language papering over
- Visual regressions: never overwrite baselines without explicit confirmation. Compare new output against known-good reference, not against in-flight change
- CI fails after push → investigate whether failures are from your changes or pre-existing flakes before pushing more fixes. No retry loops

## Test and lint failures never "pre-existing" without proof

- **Never dismiss test/lint failures as "pre-existing" without verifying on base branch.** Run same test on base branch or `git log` for when test introduced. Touched file → assume you broke it until proven otherwise
- **All tests pass (zero failures) before declaring work complete.** Test fails → investigate and fix even if poorly written. Broken test = bug to fix, not waved away
- **Lint errors in files you touched are yours.** Fix them. Lint warnings across unrelated files can be noted, but lint errors in changed files block work

## Plan before doing for non-trivial work

- **"Non-trivial"** = touches >2 files OR fixes bug OR refactor OR new pattern
- Non-trivial work: `superpowers` skills `brainstorming` and `writing-plans` mandatory. Use BEFORE editing
- Already started editing → not too late. Pause, write plan now. State explicitly when switching from explore-mode to edit-mode

## Minimal-diff principle

- Bug fix needs no surrounding cleanup. One-shot operation needs no helper. Three-line repetition fine — no premature abstractions
- Renaming files, moving lines, "while I am here" edits → STOP and confirm scope first
- Refactors mechanical: never improve code while moving. Test fails during refactor → revert, don't modify test
- Smaller PRs > one large PR. Sprawling change → split
- Match existing style even if you'd do it differently. Quote style, type hints, docstring presence, naming — mimic the file
- Every changed line should trace to the request. Can't name the reason in one sentence → revert it
- Orphan cleanup: remove imports/vars/functions YOUR change made unused. Don't delete pre-existing dead code without asking

## Think before coding

- State assumptions explicitly when proceeding without asking. Permission granted to act on reasonable defaults — user will redirect if wrong. But name the assumption so user *can* redirect
- Multiple interpretations of the request → pick one, name it in one line, proceed. Don't enumerate options unless the choice is costly to reverse
- Simpler approach exists → say so before implementing the complex one. Push back; don't fold
- "Would a senior engineer call this overcomplicated?" Yes → simplify before showing
- Bug fix → reproduce first (failing test OR manual repro citing exact input/output). Skip only for trivial typos. No intuition-only fixes

## Time-sensitive facts and user pushback

Training cutoff = stale recall risk. Default to **verify, not assert** for: company status, acquisitions, product versions, employee roles, prices, news, library current state, API surface area.

- **Tool-first on contested or time-sensitive claims.** WebFetch or raw `curl` (live sources) / context7 for library docs *before* asserting — **not exa**, which serves cached snapshots and will confirm a stale fact with false confidence. Recall is hypothesis, not answer
- **User pushes back with evidence (URL, screenshot, citation) → verify the evidence first.** Don't double down on recall. Don't fold to be agreeable. Fetch the source, then update
- **Near-cutoff events = thin training coverage.** Last ~6 months before cutoff = unreliable recall, not solid knowledge
- **Failure mode to avoid:** confident assertion → user contradicts → confident re-assertion. Worse than not knowing. Break the loop by fetching a **live** source (`curl`/WebFetch), never a cached tool like exa — a cached tool echoes the same stale answer and deepens the mistake

## Git

- Never commit to master/main (or any default branches)
- Creating PR without review time → Draft PR. Draft PRs default unless told otherwise
- Working on PR user referenced: local branch may not match PR's remote head ref (e.g. `gh pr checkout 3074` creates `pr-3074` even when PR branch is `ENG-X-foo`). Before commit, compare `git branch --show-current` to `gh pr view <num> --json headRefName`. Differ → ask which to commit on
- "Rebase onto master" / "merge into branch X" ≠ consent to push. "I'll test it first" = explicit anti-consent. Push only when user says push
- **Before `gh pr create`, find and use the repo's PR template.** Check `.github/PULL_REQUEST_TEMPLATE.md`, `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE/*.md`, `docs/PULL_REQUEST_TEMPLATE.md`, repo-root `PULL_REQUEST_TEMPLATE.md`. Found → fill every section, check boxes only when clearly satisfied, leave unverified ones unchecked. Not found → fall back to `## Summary` / `## Test plan` default. The default system-prompt PR format is the fallback, not the default — always look for a template first.
- **PR descriptions are the shortest useful summary, never an essay.** The description says what changed and why in a few sentences or tight bullets; the reviewer gets the specifics from the diff, not the body. Fill the template's sections but keep the prose in each tight — never a per-file changelog, a metrics/bundle-number dump, or a paragraph-by-paragraph walkthrough. Detailed proof (verification logs, bundle metrics, reproduction traces) goes in a PR comment or the linked ticket, not the description. This is a length rule, not a grammar one — write normal prose, just little of it. When unsure how short, err shorter and let me ask for more. Rationale: reviewers won't read a wall of text, and it buries the one thing they needed; the diff already carries the detail.
- **Before creating a PR, rebase the branch onto the up-to-date origin version of its base branch.** Confirm the actual base first (default is often `master`/`main`, but stacked or non-default-base work differs — don't assume). Workflow: `git fetch origin <base>` then `git rebase origin/<base>`. Rebase onto the fetched `origin/<base>`, never a stale local copy. Rebasing ≠ consent to push; the post-rebase push needs `--force-with-lease` and only when told to push. Conflicts → stop and surface, don't resolve blindly. This gets the branch current before opening the PR; it does not authorise rewriting already-reviewed history mid-review without asking.

## Sandbox: git and gh remote commands

`git` and `gh` are in `permissions.sandbox.excludedCommands`, so a command **whose first token is `git` or `gh` runs outside the sandbox automatically** — full network and filesystem access, no `dangerouslyDisableSandbox` needed. Remote ops (`push`/`pull`/`fetch`/`clone`, `gh pr`, `gh repo`) and local `.git` writes (`worktree remove`, `branch -D`, `checkout`) just work when invoked bare. The real blocker was never `~/.ssh` read access (the sandbox reads `~/.ssh` by default per current docs); it is **network egress**, which the exclusion sidesteps.

- **Never wrap git/gh behind another command.** `excludedCommands` matches the *first token* only. `cd /path && git push` has head `cd`, so the whole line runs sandboxed and the remote op fails. Use `git -C <path> push` / `gh -R <repo> …` instead — the head stays `git`/`gh` and the exclusion applies. This also keeps `file:line`-style paths ctrl-clickable.
- **Do not prefix git/gh with `source …`, `echo … &&`, `export … &&`, or `bash script.sh`** for the same reason — the head is no longer `git`/`gh`.
- **`dangerouslyDisableSandbox: true` is a last resort, not the default.** It disables *all* network and filesystem isolation (broader than the git-only exclusion) and is redundant for a bare git/gh command. Reach for it only when a genuine sandbox failure remains after the command is already un-wrapped (head = `git`/`gh`), or for a non-excluded remote tool (e.g. raw `curl` against a host).
- `git worktree remove` and `git branch -D` run fine bare; the earlier `.git/config` write and worktree-removal EPERM failures were the same wrapping problem, not an inherent sandbox limit.
- `fsmonitor` is disabled globally (`git config --global core.fsmonitor false`) to stop the sandbox EPERM churn on `.git/…/fsmonitor--daemon.ipc`.

## Before fixing a "broken" / "flaky" / "failing" thing

User reports broken → **verify world's current state before forming theory.** Your branch may be stale; someone may already be on it.

- **Check master, not your branch**, for file's current state: `git show origin/master:<path>`. Reported flaky test may already be `.skip`'d, quarantined, or rewritten
- **Search for in-flight work** on same file before starting: `gh pr list --search "<filename>" --state open`. Teammate has open PR → raise it before duplicating
- **Read recent commits for status markers** (`skip`, `disable`, `revert`, `quarantine`, `WIP`): `git log -10 --oneline -- <path>`. Signals situation already moved
- **CI failure data has date.** Race conditions often date/timezone/day-of-month dependent. Failure unreproducible locally today may have manifested only on specific date — and vice versa. Don't conflate "I reproduced _a_ failure" with "I reproduced _the_ failure being reported"
- **Local repro doesn't match CI failure mode → stop and reconcile** before writing fix. Two flake modes coexist; fixing wrong one looks like progress, isn't

## Running bash commands

- **No leading comments in Bash tool calls.** Claude Code's permission matcher reads first whitespace-separated token as command name, so a command starting with `#` falls through to Auto mode classifier even if the real work is `find`/`grep`/etc. Put explanation in Bash tool's `description` field
- **Prefer one command per Bash call.** Chain with `&&` only when steps truly depend. Avoid heredocs and multi-line scripts — harder to match against allow rules, harder to diagnose
- **No `bash -c "..."` / `sh -c "..."` / `eval`.** On deny list. Run inner command directly
