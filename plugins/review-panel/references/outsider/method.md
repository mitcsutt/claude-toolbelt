# outsider method

Verified on 2026-11-20 (Claude Code 2.1.329 docs); re-check live before a finding depends on a volatile fact.

How to run steps 2 to 7 of the agent file. `OU` IDs resolve in `evidence.md` (this directory); `E` IDs in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. The platform checklist is `built-ins.md` (this directory).

## Sealing (OU1, OU2, OU9)

- The first Write creates the report with `## Cold take`. No Read, Grep or Glob of the artifact before it.
- If you opened the artifact by mistake, write `Sealed: no (read <path> first)`. Don't pretend.
- When the artifact is the problem statement (no separate problem), write `Sealed: no (no separate problem statement)` and go on.
- Leak check on the brief: does the problem statement name a component, tool or rejected alternative, or say "we decided"? Record what leaked in the `anchored:` field. A leak is a defect of the brief, not a finding against the artifact.

## Constraint ledger (OU17)

- Format: `C<n> · constraint (quoted) · source · hard | inferred`.
- Only hard constraints can exclude an option.
- An `inferred` constraint can lower confidence, never exclude.

## Source order for prior art (OU10, OU11, E35)

1. Platform docs (`llms.txt`, e.g. `https://code.claude.com/docs/llms.txt`) and the changelog (`https://code.claude.com/docs/en/changelog.md`).
2. Each candidate's official docs.
3. The registry page (npm, PyPI, Docker Hub) for the last release date.
4. The GitHub repo or topic.
5. Curated lists, HN, Lobsters: discovery only, never evidence.

## Inclusion criteria (OU10–OU13)

- It exists (fetched).
- A quote shows the specific job.
- It meets every hard constraint.
- Its last release or commit is within 12 months. That threshold is an inference from OU12–OU13. A younger or older tool that fits becomes `candidate, immature` or `candidate, stale`, not excluded.

## Build line (OU12–OU15)

- Count the files and config you'd own, and the dependencies added, direct and transitive where cheap (`npm view <pkg> dependencies` is a network read; allowed at T2 only).
- Abandonment: 16% for popular projects. Say "higher for small projects, by inference".
- No dollar TCO.
- Default preference: first-party built-in, then third-party, then custom, other things equal.

## Compare (OU6, OU7, E31)

- Mark each difference between the cold take and the artifact `match | differs`.
- For each `differs`: scenario A (the artifact's route fails), scenario B (the cold take fails), and the external evidence that decides between them, or "undecided".
- Never revise the cold take to agree with the artifact or other reviewers (OU9).

## Premortem (OU8)

"A year on, this route was abandoned; why?" is a generation prompt only, capped at 5 candidates. Report only the candidates that gain evidence.

## Refutation (E32)

Drop each BLOCK or SHOULD that says "use X instead" or "don't build" unless all three hold: a fetched quote shows X does the job; X meets every hard constraint; X exists at the version in use (installed `--version`, a pin, or the brief). Name the line you read that refutes it, or drop it. Refute only with what was read.

## Overlap rules

The line with grumpy-senior: the unit of the replacement decides.

- Outsider owns any finding whose fix replaces or removes a whole component of the design (a script, hook, skill, service, harness, or the system itself) with a built-in, a third-party tool, an existing internal system, or nothing. That covers route, build vs buy, and prior art.
- Grumpy-senior owns reinvention *inside* a component: a function, helper, loop or option that duplicates the stdlib, a language or platform API, or an in-repo module.
- Platform built-ins listed in `built-ins.md` are outsider's even when grumpy-senior notices them.
- To avoid double-reporting, title each built-in finding `Built-in: <feature name> replaces <component>`. Noise-filter then merges on the feature name rather than the location, since location alone is unsafe (E40).

Rows 8, 11, 12 and 20 of `built-ins.md` overlap this marketplace's own plugins. When reviewing those plugins, name the overlap *and* what the plugin adds. Don't say "delete it" without checking what the built-in lacks (e.g. rules-lawyer's README states what `/doctor prompt-audit` doesn't cover).

## Checks not to make

1. "Rewrite it in another language or stack", unless the goal states a constraint the current stack can't meet (e.g. this repo's bash 3.2 gotcha). (E32)
2. "Just use SaaS X" without the constraint ledger. LAN-only or no-cloud constraints routinely exclude it, and that includes `/schedule`. (OU17)
3. A tool that fails a stated hard constraint, as anything but `excluded: <constraint>`. (OU17)
4. "Big company Y does it differently" without a transfer argument naming the shared constraint.
5. Popularity (stars, downloads) as evidence. (OU11)
6. Any tool or feature cited from recall or a snippet at BLOCK or SHOULD. It is UNVERIFIED. (OU10, OU16, E35)
7. "Use a deny rule instead" when the goal needs a security boundary or a condition on state. (OU17)
8. Revising the cold take to agree with the artifact or other reviewers. (OU9)
9. Restating other lenses (failure modes, security, cost), or reinvention inside a component, which belongs to grumpy-senior. (E32)
10. Treating the author's "we evaluated X and it doesn't fit" as settled. It is a claim to check, not a reason to skip X. (OU4, OU5)
