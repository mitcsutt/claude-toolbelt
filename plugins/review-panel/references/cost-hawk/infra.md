# cost-hawk checklist: CI, cloud, timers and local processes

Verified on 2026-11-19 against the sources named per row; re-check live before a finding depends on a volatile fact (prices, platform behaviour).

Prices: `prices.md`. Free tiers and allowances come off before you price anything.

## CI (CH17)

- Jobs = `runs-on` × matrix size × triggers per day. Minutes = each job's duration rounded **up** to a whole minute. Dollars at the runner's OS rate.
- Compare the month's total with the plan's dollar allowance (Free $12, Pro/Team $18, Enterprise Cloud $300).
- Standard runners on public repos and self-hosted runners cost $0: don't flag them.
- Look for `on: push` with no branch filter, matrices on lint-only jobs, and macOS legs (10.3× Linux).

## Cloud (CH18, CH19)

- Idle capacity and data transfer first, hot-path code after.
- Egress at $0.09/GB (AWS us-east-1, first 10 TB); a public IPv4 address $3.65/month each.
- R2 and Workers charge no egress.
- Free tiers before pricing: R2 gives 10 GB-month, 1M Class A and 10M Class B ops a month.

## Wakeups (CH20)

- `OnUnitActiveSec` or `OnCalendar` frequency, `AccuracySec`, cron frequency.
- `AccuracySec` defaults to 1min, which is fine; `1us` defeats wakeup coalescing.
- Report wakeups per day and the work each one does; when each does paid work (an LLM call, an API request), price it per run × runs per day.
- Name the push or long-poll alternative.

## Growth

- Logs without rotation, caches without eviction, queues without a consumer.
- `du` now plus growth per day gives time to fill.

## Looks expensive, isn't

- Nightly multi-GB backups to R2 with retention: storage only, no egress, ops inside the free tier.
- CI on a public repo's standard runners.
- A daily timer with the 1min default accuracy.
