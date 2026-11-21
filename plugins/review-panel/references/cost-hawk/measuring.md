# cost-hawk measuring

Verified on 2026-11-19 against the sources named per row; re-check live before a finding depends on a volatile fact (prices, platform behaviour).

## Token arithmetic (CH5)

- Count bytes with `wc -c FILE` (bytes, not `wc -m`, which counts characters and differs for UTF-8).
- Tokens ≈ bytes ÷ 3 on Claude 4.7 and later (every 5.x model). Use ÷ 4 only for pre-4.7 models.
- English and markdown: about ±15% (inferred). Code, JSON and non-English text: give a ±30% range.
- Label the figure `estimated (bytes÷3)`.

## When to measure instead (CH10, CH11)

Measure from a past run rather than estimate when:

- the figure decides a BLOCK or SHOULD and the workflow has already run on this machine;
- the text is mostly code or JSON;
- it is any multi-request agent run: estimates run low.

Agent-run figures are ranges. A ×2 difference between runs of the same task is normal.

## Agent-run formula (CH3, CH10)

Price agent work as context × requests, not as output:

`Σ over requests (cache_read × read + 5m_write × 1.25·base + 1h_write × 2·base + uncached × base + output × out)`

Use the read multiplier of the model in `prices.md` (0.05× on Opus/Sonnet 5.5, not 0.1×). On Haiku 5.5, choose the price tier per request by its total prompt tokens, cache reads and writes included (CH2). A subagent, fork or compaction request gets the 5m TTL; the main conversation gets 1h only on a subscription within plan usage (CH14).

## Labels

- `measured (<file or command>)`: you read the number from a file or a command's output this session.
- `estimated (<assumption>)`: anything else, with the assumption named.
- A total mixing both is `estimated`.
- Dollars from Claude Code's `/usage`, status line or transcripts are measured tokens × list price, not a bill (CH7).

## Allowed commands

Read-only. Anything else goes under `## Coverage` with the command to run later.

- `wc -c <files>`; `du -sh --max-depth=1 <dir>` (macOS `du -sh -d 1 <dir>`). Never on network mounts.
- `find <dir> -maxdepth 3 -newermt '-1 day' -type f -exec wc -c {} +` for growth per day.
- `grep -rnc` / `grep -rn` for call sites: `fetch(`, `curl`, `claude -p`, `/v1/messages`, `"matcher"`, `"type": "prompt"`, `model:`, `runs-on`, `matrix:`, `cron:`, `OnUnitActiveSec`, `AccuracySec`.
- `git log`, `git show`, `git diff` (read-only).
- `jq` or `node -e` aggregations over `~/.claude/projects/<proj>/*.jsonl` and `…/subagents/*.jsonl`: dedupe by `message.id`; sum `message.usage.{input_tokens, output_tokens, cache_read_input_tokens, cache_creation.ephemeral_5m_input_tokens, cache_creation.ephemeral_1h_input_tokens}` by `message.model`; per session, `type:"cost-state"` `totalCostUSD`. Print only numbers. The format is undocumented and may change (R13 §3).
- Read `claude plugin eval` result JSON: `costUsd`, `judgeCostUsd`, `durationSeconds`, `turns` (CH21).
- `claude plugin details <plugin>`: always-on and per-invoke context estimate. It shows hooks as "harness-only — no model context cost" and MCP tool schemas as "not counted", so price hook-injected text and prompt or agent hooks separately (CH16).
- `systemctl list-timers --all` and `systemctl cat <unit>`, only when the artifact is this host's own config.
- `gh run list --json durationMs,conclusion,name --limit 50` when `gh` is authenticated (read-only).
- `/usr/bin/time -v <cmd>` (macOS `-l`), only on a side-effect-free command you have read.

## Never

- `claude -p`, `claude plugin eval`: both spend money.
- `POST /v1/messages/count_tokens` (CH6): it sends the artifact's text off the machine. List it under Coverage instead, as `curl -s https://api.anthropic.com/v1/messages/count_tokens -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" -d @body.json`, and note "needs ANTHROPIC_API_KEY".
- Installs, load tests, and anything that writes outside a `mktemp -d` directory.
