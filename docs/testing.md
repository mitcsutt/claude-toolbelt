# Testing

Three enforcement layers, each with a distinct job and no overlap between
them, plus a fourth manual layer for prompt-shaped plugins.

## The three layers

1. **`scripts/validate.sh`** — repo-wide structural invariants, implemented
   once and looped over every plugin. Wraps `claude plugin validate --strict`
   (skipped with a note if the `claude` CLI isn't on `PATH`) and adds what
   that command doesn't cover: plugin directory name matches `plugin.json`
   name matches the marketplace entry name (R1); marketplace entries carry
   only `{name, source, category}` (R2); the README skeleton and heading
   order (R4); presence of `tests/all.sh` (R5); no plugin-local copy of
   `assert.sh` (R6); file modes against the R9 allowlist; and that any
   `SKILL.md`'s frontmatter `name` matches the directory it lives in.

2. **`plugins/<name>/tests/all.sh`** — behavioural assertions specific to
   that plugin. This is where "one behavioural assertion per product
   promise the README states" lives: if the README's `## What it ships`
   table claims a hook does X, `tests/all.sh` should have an assertion that
   X actually happens. Sources the shared helper at `tests/lib/assert.sh`
   (never a local copy — R6). `scripts/validate.sh` only checks that the
   file exists; the substance of what it asserts is a review concern (R5),
   not something a script can judge.

   A plugin whose implementation is Python puts its unit tests in
   `plugins/<name>/tests/runner/test_*.py` and has `tests/all.sh` run them with
   `python3 -m unittest discover -s tests/runner -p 'test_*.py'`, skipping with
   a printed note when `python3` is absent — the same treatment `serve.test.py`
   already gets. `agent-loop` is the only plugin in that shape: its harness is
   `runner/run.py` and `run.sh` is a four-line shim, so `tests/run.e2e.test.sh`
   skips too when there is no `python3` to run.

3. **`scripts/test-all.sh`** — runs `scripts/validate.sh`, then every
   plugin's `tests/all.sh` in turn, then `node --test` over every tracked
   `*.test.mjs` file. This is the single command that must pass before any
   change lands, and the one CI runs.

Deliberately *not* present: a per-plugin manifest-checking script copied into
every plugin. Those checks are structural, so they live once in layer 1.

## Running everything

```bash
bash scripts/test-all.sh
```

Runs validate, lint (shellcheck `--severity=warning`, skipped with a note if
shellcheck isn't installed), every plugin's `tests/all.sh`, and
`node --test` over tracked `*.test.mjs` files. This is what
`.github/workflows/ci.yml` runs on every push and pull request.

To run a single layer in isolation:

```bash
bash scripts/validate.sh   # structural invariants only
bash scripts/lint.sh       # shellcheck only
bash plugins/postmortem/tests/all.sh  # one plugin's behavioural tests
```

## Evals (manual, fourth layer)

Prompt-shaped plugins — `postmortem`, `find-docs`, `review-panel` — are best
verified by actually running the prompt and grading the output, which is
what `claude plugin eval` does. An eval suite lives at
`plugins/<name>/evals/` as `case.yaml` files (or `prompt.md` + graders), and
is committed to the repo like any other plugin file.

```bash
claude plugin eval plugins/postmortem --threshold 0.8
```

**Evals do not run in CI.** They cost money per run — each case invokes a
real agent turn, typically several times, plus grading — and nothing here
should draw on the same budget as ordinary push/PR checks. `.github/workflows/ci.yml`
runs `scripts/test-all.sh` only. Evals are a manual, human-triggered step:
run them before bumping the version of a prompt-shaped plugin, so a change to
the output style, an interview flow, or a routing prompt gets graded before
it ships. Nothing mechanically forces this today — it's a discipline this
document states, not a gate a script enforces.

### What's covered

- **`postmortem`** (2 cases) — `writes-file-despite-no-save-request` checks
  hard-contract #1 (the file is the deliverable even when told to skip
  saving) via a `regex` grader on the created-files list, no LLM judging
  needed for that signal. `all-eight-sections-present` checks hard-contract
  #2 (exactly 8 named sections, in order) against the file content visible
  in the tool-call trace.
- **`find-docs`** (2 cases) — `routes-popular-library-to-context7` and
  `routes-vendor-saas-to-exa-not-context7` check the routing table sends
  each question shape to the right backend and doesn't default to
  "Context7 first, Exa fallback." These cases call real MCP tools, which
  `claude plugin eval` gates by default — grant them explicitly:
  `--allow-tools "mcp__context7__*" "mcp__exa__*"`.
- **`review-panel`** (14 cases) — all seed a repo with `fixture.sh`, so pass
  `--scaffold`. In the reviewer cases, one grader per planted defect reads the written report
  (`AUDIT.md`, `TRIAGE.md` or `REVIEW.md`), and the no-plugin arm is the same model
  working unaided.
  - `rules-lawyer-planted-defects` is a Terraform repo with familiar
    defects: wrong CLI claims, a guard hook that exits 1, a lowercase
    matcher, a Stop-hook loop and contradictions.
  - `rules-lawyer-platform-traps` is a Node repo whose instructions depend
    on Claude Code behaviour that isn't true. Its ground truth was checked
    against the 2.1.328 docs, so re-check it when the platform changes.
  - `noise-filter-planted-triage` triages five pre-written reports on a
    small rate limiter into `TRIAGE.md`; it needs Bash for the parser path
    (`--allow-tools Bash Write`), and without it grades the no-shell T0
    fallback (`--allow-tools Write --disallow-tools Bash PowerShell`).
  - `goal-auditor-fix-claim` audits a branch that claims to fix two issues
    in a small Node repo, into `REVIEW.md`. Its three planted defects (a
    test that passes on the pre-fix code, a post-URL path behind a legacy
    helper, trimming fitted to the issue's example) can all be decided by
    reading, so it runs with `--allow-tools Write --disallow-tools Bash
    PowerShell` and measures T1, not the revert check. The sound #14 fix is
    the control. `tests/all.sh` runs the shipped revert-check recipe itself
    against two throwaway repos.
  - `dx-advocate-planted-defects` reviews a Node service's 1.4.0 change,
    into `REVIEW.md`, with doc, upgrade, unattended-run and removal defects
    and one control (an empty `SESSION_SECRET` that setup generates); run
    with `--allow-tools Write --disallow-tools Bash PowerShell`.
    `lib/doc-checks-commands.test.mjs` runs the shipped doc checks against
    the same fixture.
  - `assemble-panel-*` (6 cases) run with `Agent` withheld and grade the
    roster, claim owner and status line the skill prints before it would
    dispatch, with `regex` graders on the final message. The trace also
    holds SKILL.md's example roster, so don't grade it. Fixture hooks can't
    stage a refused dispatch: plugin eval loads no `.claude/` directory, even
    one a scaffold wrote. They run with `--ablation none` (the no-plugin arm
    has no skill to follow); the comparison is the same cases run from a
    scratch worktree of `main`:
    `claude plugin eval plugins/review-panel --case 'assemble-panel-*' --scaffold --runs 3 --ablation none --disallow-tools Bash PowerShell`.
    No subagent runs, so a run is one orchestrator session (estimated
    $0.10–0.40): about $2–7 for 6 cases × 3 runs, double with `main`.
    Bar: `control_no_panel` 3/3, `no_report_reads` 3/3, and each routing
    grader 3/3 or no worse than on `main`. `status_line` and `claim_owner*`
    are new behaviour that `main` should fail. Known gap: the
    blocked-dispatch fallback (`Panel blocked:`) is untested by evals.
    `tests/all.sh` grades golden final messages in
    `lib/fixtures/assemble-panel/` to show every roster grader passes on a
    right answer and fails on wrong ones.
  - `fact-checker-planted-claims` checks a build plan's version-qualified
    claims into `REVIEW.md`: six planted WRONG claims and two controls, with
    regex verdict graders keyed to `PLAN.md:<line>`. Run it with
    `--allow-tools Write WebFetch WebSearch --disallow-tools Bash PowerShell`,
    and re-check its ground truth (the URLs in the case description) first:
    a platform change can make a planted claim true.
  - `threat-modeller-homelab-agent` reviews a homelab-agent plan and first
    cut into `REVIEW.md`: ten planted attack paths, a restraint grader, a
    fixture-key control and a `not_contains` grader on the committed ops
    token. Run it with `--runs 2 --allow-tools Write --disallow-tools Bash
    PowerShell`, so its checks go through the Grep tool.
  - `contract-keeper-orders-v2` seeds an orders service (HEAD vs tag
    `base`, plus `CHANGE.diff` and `PR.md`) with eight planted interface
    breaks and two controls, graded on `REVIEW.md`. Run it with Bash
    withheld (`--allow-tools Write --disallow-tools Bash PowerShell`), so
    both arms census consumers with Grep/Glob. Before its first scored run,
    pilot the baseline (`--runs 3`, read the no-plugin column) and drop any
    planted item the unaided model already scores >= 0.8 on. Its scored run
    is recorded under `review-panel` scores below.
  - For the rules-lawyer cases, grant `--allow-tools Bash Write WebFetch
    WebSearch`. Granting Bash needs a working sandbox backend. Where there
    isn't one (for example Ubuntu with AppArmor's
    unprivileged-user-namespace restriction), run with
    `--disallow-tools Bash` instead, which skips the hook fixture runs in
    both arms.
  - `plugins/review-panel/lib/report-parse.test.mjs` (run by `node --test`)
    parses every agent's output template against the shared report
    contract. New eval cases should prefer `regex` graders over the report
    file wherever the ground truth is a token, and keep `llm` graders for
    "states the right reason".

**Status:** authored and loaded (`claude plugin eval <path> --case
<glob-that-matches-nothing>` parses every `case.yaml` in the suite at zero
cost — a YAML syntax error fails before the "no cases match" message, but
unknown grader types, `match` modes and regex flags are not rejected, so
`review-panel`'s `tests/all.sh` checks those itself). No baseline scores are recorded yet for `postmortem` or `find-docs`. Run
those suites in a normally authenticated terminal to get real scores before
relying on them.

`review-panel` scores, 2026-11-19. Claude Code 2.1.328 on its default model;
`--runs 2`; Bash withheld (no sandbox backend on the host), so both arms skip
hook fixture runs. Scores are weighted grader means, with plugin vs no plugin:

| Case | With plugin | No plugin | What separated them |
| --- | --- | --- | --- |
| `rules-lawyer-planted-defects` | 0.94 | 0.90 | Kept the sound scope rule 2/2 (baseline 0/2). Missed one grader each in two runs |
| `rules-lawyer-platform-traps` | 0.90 | 0.82 | Mean of two 2-run sessions, scored with a since-removed control grader that both arms always failed. Caught "a failing inline command aborts the skill" 3/4 (baseline 0/4) |

An audit cost about $1.0–1.5 and 5–6 minutes with the plugin, against about
$0.40 and 2.5 minutes for the baseline. With n=2 and haiku judges on 13–30k
character reports, a single grader flip is noise; read the per-grader table
in the HTML report, not just the mean.

Regression check for review-panel 0.3.0 (shared method references), same
setup with `--runs 1`, $3.59 total: `rules-lawyer-planted-defects` 1.00 vs
0.88 (control kept; baseline failed the control and the block-message
grader), `rules-lawyer-platform-traps` 0.91 vs 0.83 (missed
`ticket_prompt_stalls_unattended_runs`; baseline missed the inline-command
and `if`-on-Stop traps). Both within one grader flip of the table, so
unchanged.

`noise-filter-planted-triage`, review-panel 0.4.0, 2026-11-19: `--runs 3`,
Bash withheld (so both arms graded the no-shell T0 path), $2.06. With
plugin 0.97, without 0.89, delta +0.08. The control (hedged BLOCK kept in
Fix before merge) passed 6/6 across both arms. That is between the plan's
bars: below "worth shipping" (delta >= 0.10) and above "no measurable gain"
(delta < 0.05). `check-triage` was not run on the eval triages (no shell in
the run, sandboxes not kept). The baseline's misses were accounting ones the
parser catches: a misstated duplicate counted as confirmation, nits not
clustered, a tradeoff neither decided nor dropped.

`goal-auditor-fix-claim`, review-panel 0.5.0, 2026-11-20: `--runs 2`, Bash
withheld (T2 revert check unmeasured), $1.27. With plugin 1.00, without
1.00: no measurable gain. The baseline caught all three planted defects and
kept the control in both runs, so the case doesn't discriminate. The plan's
next step is to swap in defects the baseline misses (from R10 §4), keeping
only defects the baseline misses in at least 1 of 3 runs. With the plugin a
run cost about 2.4x the baseline ($0.45 vs $0.19).
`assemble-panel-*`, 2026-11-20, `--runs 3`, Agent and Bash withheld, graded
from the printed roster; the `main` arm ran the same cases against the 0.4.0
skill. A first run with the plan's new Step 0 sizing (normal plan = 3
reviewers) routed the poller plan worse than `main` (0.60 vs 0.56, but it
dropped cost-hawk and added threat-modeller 3/3, which `main` got right), so
per the plan's bar the sizing change was not shipped and Step 0 keeps the
0.4.0 bands. With that, branch vs `main` ($1.83 vs $1.53 for the five
routing cases): agent-instructions 0.97 vs 0.69, build-vs-buy 1.00 vs 0.83,
oauth-migration 1.00 vs 0.83, poller-plan 0.80 vs 0.56, typo-control
(the control) 1.00 vs 1.00. `blocked-add-persona` scored 1.00 in both. Both
arms missed contract-keeper on the poller plan 3/3. Most of the gain is the
new claim-owner and status lines, which `main` doesn't print.
`fact-checker-planted-claims`, 2026-11-20: `--runs 2`, Bash withheld (so
probes, the version gate and the shell-only trap are unexercised), $2.16.
With plugin 1.00, without 0.97, delta +0.03: no measurable gain at n=2
against the plan's bar of +0.15. The baseline missed one weight-1 grader
(`p6_cites_9_5_reversal`) in one run. Both controls passed in both arms, and
no P1-P3 line was CONFIRMED in the plugin arm. With the plugin a run cost
about 2.6x the baseline ($0.78 vs $0.30).
`threat-modeller-homelab-agent`, 2026-11-20: `--runs 2`, Bash withheld,
$2.84. With plugin 1.00, without 0.85, delta +0.15, which clears the plan's
+0.08 bar. The baseline printed the planted secret value in both runs
(`secret_value_never_printed` 0/2); the plugin never did (2/2). The baseline
also missed `bypass_mode_not_overstated` twice and the ops-API fallback
token once. Cost is over the plan's cap: about $1.09 per run with the plugin
against $0.34 for the baseline (+$0.75, cap +$0.35).
`contract-keeper-orders-v2`, 2026-11-20: `--runs 2`, Bash withheld (T2
unmeasured), $2.01. With plugin 0.95, without 0.91, delta +0.05: no
measurable gain against the plan's +0.10 bar (one weight-2 grader flip).
The only grader that moved was the control `control_optional_field_not_flagged`:
the plugin passed it 1/2 and the baseline 0/2, so neither arm meets the
plan's "both controls in at least 3/4 runs". Every planted break was caught
in both arms. With the plugin a run cost about $0.62, against $0.38 for the
baseline. No baseline pilot ran before this scored run, so the drop rule
wasn't applied; by that rule every planted break would now be dropped, so
the case needs harder breaks before it can show a gain.

`dx-advocate-planted-defects`, 2026-11-20: `--runs 2`, Bash withheld, $1.97.
With plugin 1.00, without 0.87, delta +0.13, which clears the plan's +0.09
bar, and the control passed in both arms. Only one of the four target
defects separated the arms: the baseline missed the phony-target false green
(D6) in both runs; D2, D3 and D7 were caught in both arms. So it's a partial
pass of the plan's bar (it asks for two). With the plugin a run cost about
2.4x the baseline ($0.70 vs $0.30).

## Related

- [`docs/authoring-plugins.md`](authoring-plugins.md) — the README skeleton
  that layer 2's assertions are checked against, and where `evals/` sits in
  a plugin's layout.
- [`docs/repo-structure.md`](repo-structure.md) — where `scripts/` and
  `tests/lib/` sit relative to `plugins/`.
