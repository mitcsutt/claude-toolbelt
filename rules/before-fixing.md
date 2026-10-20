# Before fixing a "broken" / "flaky" / "failing" thing

User reports broken → **verify world's current state before forming theory.** Your branch may be stale; someone may already be on it.

- **Check master, not your branch**, for file's current state: `git show origin/master:<path>`. Reported flaky test may already be `.skip`'d, quarantined, or rewritten
- **Search for in-flight work** on same file before starting: `gh pr list --search "<filename>" --state open`. Teammate has open PR → raise it before duplicating
- **Read recent commits for status markers** (`skip`, `disable`, `revert`, `quarantine`, `WIP`): `git log -10 --oneline -- <path>`. Signals situation already moved
- **CI failure data has date.** Race conditions often date/timezone/day-of-month dependent. Failure unreproducible locally today may have manifested only on specific date — and vice versa. Don't conflate "I reproduced _a_ failure" with "I reproduced _the_ failure being reported"
- **Local repro doesn't match CI failure mode → stop and reconcile** before writing fix. Two flake modes coexist; fixing wrong one looks like progress, isn't
