# Matcher-Syntax Pitfalls

Common shapes of broken or redundant permission rules, with detection recipes. The matcher itself (see `lib/rule-matcher.mjs` and `references/rule-syntax.md`) behaves correctly per its documented semantics — these pitfalls are configuration mistakes that pass the matcher's contract but fail the user's intent.

`/permissions-lint` consumes this file. Each pitfall lists its symptom, the detection recipe in terms of rules + log entries, the fix, and any caveats that affect auto-removal safety.

## Pitfall 1: `Bash(cmd)` too narrow

**Symptom.** A rule exists for `Bash(cmd)` but the log shows `cmd args` calls that bypass it — those calls still fall through to Auto mode's classifier. The user likely intended the rule to cover the command with any arguments and did not realise `Bash(cmd)` matches only the bare command with no args.

Per `rule-syntax.md`: `Bash(cmd)` matches `cmd` exactly (zero arguments). `Bash(cmd:*)` matches `cmd` with any arguments (including no arguments). The two are not interchangeable.

**Detection.** For every `permissions.allow` rule of the literal form `Bash(X)` where `X` contains no `*` and no `:`:

1. Find all log entries with `tool: "Bash"` and `detail` whose first token (after stripping env-var assignments) equals `X`.
2. Of those, count how many have `detail !== X` — i.e. `cmd args` rather than bare `cmd`.
3. If that count is greater than zero, the rule is too narrow.

**Fix.** Rewrite the rule from `Bash(X)` to `Bash(X:*)`. Optionally keep both if the user has a deliberate reason to distinguish the bare invocation, but the common case is a single replacement.

## Pitfall 2: Subsumed rules

**Symptom.** Rule X exists, rule Y exists, both in the same bucket (`allow`/`deny`/`ask`), and every log entry that X matches is also matched by Y. X is dead — its presence has no effect on permission decisions.

**Detection.** For each rule X in a given bucket:

1. Compute the set `S_X` of log entries that X matches.
2. For each other rule Y in the same bucket, check whether Y matches every entry in `S_X`.
3. If any such Y exists, X is subsumed by Y.

`S_X` should be computed over the same log window the rest of the lint uses (last 30 days by default). A rule that matches no log entries cannot be ruled subsumed from the log alone — see pitfall 4.

**Fix.** Remove the narrower rule. Settings entries in the same bucket are unordered for matching purposes, so deletion does not change behaviour. If the user added the narrow rule deliberately as documentation, suggest a comment in their personal notes rather than keeping the dead rule.

## Pitfall 3: Allow/deny conflict

**Symptom.** An allow rule matches at least one log entry that is also matched by a deny rule. Per the precedence rules in `rule-syntax.md` (deny > ask > allow), the deny wins, and the allow rule's intent is silently overridden. Often the user does not realise the conflict exists.

**Detection.** For every pair (`A`, `D`) where `A` is in `permissions.allow` and `D` is in `permissions.deny`:

1. Compute `S_A` and `S_D` over the log window.
2. If `S_A ∩ S_D` is non-empty, report the pair along with a representative entry from the intersection.

Pairs where the intersection is empty over the log window may still overlap in principle — surface them as a lower-confidence concern only if the user asks for a deep check, not in the default report.

**Fix.** Tighten one rule, or remove the redundant one. The right answer depends on intent — surface the conflict and ask. Do not auto-resolve.

## Pitfall 4: Zero-match rules

**Symptom.** A rule has matched no log entry over the last 30 days. Candidate for removal if it was added speculatively and never used; candidate for keeping if it is preventive.

**Detection.** For each rule in any bucket:

1. Scan the log within the 30-day timestamp window.
2. If no entry matches the rule, mark it zero-match.

**Caveats.**

- Zero-match deny rules are usually preventive (e.g. `Bash(sudo:*)`, `Bash(rm -rf *)`) — they exist precisely so the matching calls never happen. Present them as low-confidence candidates with the framing "this deny rule has not fired in 30 days; was it preventive (keep) or experimental (remove)?". Default to keeping unless the user explicitly removes.
- Zero-match ask rules behave the same way under preventive interpretation.
- Zero-match allow rules are the strongest removal candidates: an allow rule that never fires costs nothing to keep but signals stale configuration. Still ask before removing.
- A short log (less than 7 days) makes zero-match unreliable — annotate the window length in the report and skip zero-match suggestions if the log spans less than 7 days.

**Fix.** Ask the user per-rule whether the rule was preventive (keep) or experimental (remove). Do not auto-remove.
