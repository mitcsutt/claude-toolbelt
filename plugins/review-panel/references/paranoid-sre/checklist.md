# paranoid-sre checklist

Verified on 2026-11-19; re-check live before a finding depends on a volatile fact.

Sweep in this order. The order follows SR1/SR6 (error handling dominates incidents) and the owner's episodes (SR19). Every command is read-only. Run it against the changed paths (`git diff --name-only <base>...HEAD`) or, for a plan, against the files it names.

**Allowed T1 commands, all read-only:** `git diff`, `git show`, `git log`, `git grep`; `grep`, `find`, `wc`; `bash -n <script>`; `shellcheck -S warning -x <script>`, only if `command -v shellcheck` finds it (never install it); `man <section> <page> | col -b | grep -n …`; `--version` of bash, node, python3, systemctl, docker and `docker compose version` (client-only output; nothing else that talks to a daemon); `ln --help`. Nothing else executes.

1. **Error paths (SR1, SR6, SR19 hook-127 episode).**
   - `git grep -nE '\|\|[[:space:]]*(true|:)|2>[[:space:]]*/dev/null|set \+e|catch[[:space:]]*(\([^)]*\))?[[:space:]]*\{[[:space:]]*\}|except[^:]*:[[:space:]]*(pass)?$|ignore_errors|continue-on-error|failed_when:[[:space:]]*false' -- <paths>`
   - For each hit on a state-changing path, write "error X arrives here → handler does Y → outcome". Skip display-only code.
   - Env-dependent commands (a tool that may be missing in cron, hooks or CI) that fail with 127 and are swallowed.
2. **`set -e` holes (SR11).** `grep -nE '^[[:space:]]*(if|while|until)[[:space:]]+!?[[:space:]]*[a-z_][a-z0-9_]*[[:space:]]*(;|$)|(local|export|readonly|declare)[[:space:]]+[A-Za-z_]+=\$\(' <scripts>`. Hand-check that each `if f` hit is a function call, then read the function body: everything in it runs with `-e` off.
3. **Partial apply and re-run (SR2, SR16, SR17, SR19 pulumi episode).** For each multi-step apply (install, deploy, IaC apply, migration, backfill): what is true after step k of n dies? Does the next run detect it (marker, `IF NOT EXISTS`, a checksum) and is that detection correct? Specific traps: `ln -s` without `-n` onto an existing link (SR16); `mkdir` without `-p`; `>>` appends that duplicate on re-run; `CREATE INDEX CONCURRENTLY IF NOT EXISTS` re-run after failure (SR17); a cloud apply that creates a credential and then fails before storing it.
4. **Supervisors and startup (SR13, SR14, SR19 power-cut and PipeWire episodes).**
   - `git grep -nE '^(Restart|RestartSec|StartLimit[A-Za-z]*|Type|ExecStartPre|After|Requires|BindsTo|PartOf)=' -- '*.service' '*.socket' '*.timer'`
   - `git grep -nE 'restart:|healthcheck:|depends_on:|condition:|start_period' -- '*compose*.y*ml'`
   - Questions: what happens when a dependency is down at boot? After 5 fast failures (SR14)? Does anything restart or re-attach dependents when a dependency restarts? Does any consumer act on `unhealthy` (SR13)?
5. **"Up" is not "working"; rollout and rollback (SR4, SR5, SR19 restart-claimed-as-fix and never-deployed episodes).** What does the healthcheck exercise? What proves the change is live: is a deploy step tied to the commit? Can the rollback run on its own, and who would notice a silent failure?
6. **Locks, PIDs, concurrency (SR15, R12 S4).** `git grep -nE 'flock|\.pid\b|\.lock\b|kill -0|O_EXCL|mkdir [^|;]*lock' -- <paths>`. A PID file checked then created is a race; flock is not stale after a crash, but an fd inherited by a daemon keeps the lock held. Name both actors.
7. **Crash-consistent writes (SR2, SR12, R12 S3).** `git grep -nE 'writeFileSync|writeFile\(|open\([^)]*["'"'"'][wa]|>[[:space:]]*"\$' -- <paths>`. For each: temp file in the same directory, then rename? fsync of the file and of the directory? A temp name unique per writer? `mv` from `/tmp` into the repo is copy + delete when the two are on different filesystems. Skip regenerable caches and say so under Checked and fine.
8. **Retries and timeouts (SR3, SR7, SR9, SR10, R12 S8).** `git grep -nE 'requests\.(get|post|put|patch|delete)\(|fetch\(|axios|urlopen|retry|retries|backoff|max_attempts|setTimeout|time\.sleep|sleep [0-9]' -- <paths>`. For each call: timeout set (and the default if not, SR10)? How many retry layers, and what is their product? Backoff and jitter? Is the retried side effect idempotent (key, dedupe)? Read wrappers before flagging; they may set defaults centrally.
9. **Time (SR8, SR15).** `git grep -nE 'time\.time\(\)|Date\.now\(\)|new Date\(\)|datetime\.now\(|date \+%s|CRON_TZ|TZ=' -- <paths>` and every crontab or timer. Wall clock used for durations, leases or TTLs; local-time schedules near DST; cron jobs relying on a login-shell environment.

**Checks not to make** (each fact is in facts.md):

- "rename isn't atomic" on one filesystem. It is for observers; the gap is durability (SR12).
- "`Restart=always` will fight `systemctl stop`". It won't: a stop isn't restarted (SR14).
- "Stale lock after a crash" for `flock`. Do flag an fd inherited by a daemon (SR15).
- "Add retries/backoff" where a lower layer already retries. Adding a layer multiplies attempts (SR9).
- "No healthcheck" on a one-shot or batch container, or a healthcheck nobody consumes, raised as a fix (SR13).
- "The cron job is skipped on DST" for a fixed-time Debian/Vixie cron job. It runs soon after the change. Do flag catch-up logic built on that belief, and "yesterday = now − 24 h" (SR15).
- A race with no named second actor (SR1; step 7 of the agent).
- `fsync` demands on caches or files that are regenerated on start (SR12).
- Lock level, table rewrites or old/new reader compatibility of a migration: contract-keeper's (SR17 covers only the re-run).
- "Add monitoring" without naming the signal and who acts on it (shared rule: name the specific missing thing).
