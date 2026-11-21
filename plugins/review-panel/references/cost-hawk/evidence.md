# cost-hawk evidence

Verified on 2026-11-19 against the sources named per row; re-check live before a finding depends on a volatile fact (prices, platform behaviour).

Cite these IDs in cost-sheet rows and findings. Compiled 2026-11-19. Prices live in `prices.md` with their URLs; this table says why each rule exists. Owner measurements come from one machine and are a direction, not a constant.

Method evidence: ${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md. Hook mechanics: E20, E22 in ${CLAUDE_PLUGIN_ROOT}/references/rules-lawyer/evidence.md.

| ID | Finding | Source | Strength |
|---|---|---|---|
| CH1 | List prices per MTok (in / out): Fable 5.2 $8/$40, Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 (prompts ≤100k), Haiku 4.5 $1/$5. Input ratio Fable:Opus:Sonnet:Haiku 5.5 = 80:40:20:1. 4.x-era recall gives 5:1, wrong by 8×. | R13 F1; platform.claude.com/docs/en/about-claude/pricing; re-fetched 2026-11-19 and 2026-11-20, every row unchanged | high, volatile |
| CH2 | Haiku 5.5 is priced by prompt length: over 100,000 tokens (cache reads and writes included) it pays $0.50 in / $0.625 5m write / $1 1h write / $0.05 read / $2.50 out, 5×. "Each request is priced on its own." Other 4.6+ models are flat to 1M. | R13 F2; pricing page "Long context pricing" | high, volatile |
| CH3 | Cache: 5m write 1.25×, 1h write 2×, read 0.1× base, except Opus/Sonnet 5.5 at 0.05× and Fable 5.1/5.2 at 0.025×. On Opus/Sonnet 5.5 a 5m miss costs 25× a hit (1h: 40×). Break-even: 5m after one read, 1h after two. | R13 F3; pricing page footnotes 1–2 | high, volatile |
| CH4 | Batch −50% on in and out; fast mode Opus 5.5 $8/$40; US-only `inference_geo` 1.1×; web search $10 per 1,000 plus tokens; web fetch tokens only (~2,500 per 10 kB page). | R13 F3, §2; pricing page | high, volatile |
| CH5 | Claude 4.7+ tokenizer "produces approximately 30% more tokens for the same text". `claude plugin details` measured ≈2.8–2.9 bytes/token on two agent bodies. Rule: bytes ÷ 3 for English and markdown; ÷ 4 only for pre-4.7 models. | R13 F4; pricing page; local CC 2.1.328 | high (doc), medium (ratio) |
| CH6 | `POST /v1/messages/count_tokens` is free and model-exact ("an estimate … might differ by a small amount"), needs an API key, ignores caching. | R13 F5; platform.claude.com/docs/en/build-with-claude/token-counting | high |
| CH7 | Claude Code computes dollars locally at list price; contracted-rate bills differ; `Prompt cache (main)` covers the main conversation only. Subscription users don't pay per token: frame as plan-limit share. | R13 F6; code.claude.com/docs/en/costs | high |
| CH8 | Yardstick: about $13 per developer per active day, $150–250 per developer-month, under $30/day for 90% of users. | R13 F7; costs doc (vendor aggregate) | medium |
| CH9 | Idle spend: an all-day session draws usage for the whole conversation per message; the first message after a break past the cache TTL reprocesses full context; a session-scoped scheduled task sends full context each tick. | R13 F8; costs doc | high |
| CH10 | Owner's bill shape (512 transcripts, 6,150 responses): cache reads 45%, 1h writes 28%, 5m writes 6%, output 21%, uncached input ≈0%. Prompt per request median 135k, p90 317k. Recompute matched CC's `totalCostUSD` within 1%. | R13 F9 | measured, one machine, Opus/subscription-skewed |
| CH11 | Agent cost is driven by input and cache reads, not output; runs on one task differ up to 30×; frontier models "systematically underestimate real token costs". Review stages took 59.4% of tokens in one study. | R13 F10, F11; arXiv 2604.22750; arXiv 2601.14470 | high / medium |
| CH12 | Cache matching is an exact prefix: "a change anywhere in the prefix recomputes everything after it". A timestamp at the top of a prompt kills the hit rate. | R13 F12; code.claude.com/docs/en/prompt-caching; Manus blog 2025-07-18 | high |
| CH13 | A skill or command whose frontmatter `model` differs from the session's is a model switch: "the next request reads the entire conversation history with no cache hits. The session model resumes on your next prompt." Each `opusplan` plan-mode toggle is a switch. Plugin skills, agents and hooks never invalidate the cache. | R13 F13; prompt-caching doc | high |
| CH14 | TTL buckets: main conversation (incl. `-p`) gets 1h only on a subscription within plan usage, 5m on API key, credits or cloud; subagents, forks and compaction get 5m. `promptCacheTtl` / `CLAUDE_CODE_PROMPT_CACHE_TTL` override. | prompt-caching doc "Which TTL each request gets" | high |
| CH15 | Cache scope in Claude Code is effectively one machine and directory; sequential sessions share a prefix "only when the git status snapshot taken at startup matches"; parallel sessions in one directory can share. | prompt-caching doc "Cache scope"; closes R13 §6 open item on repeated `-p` runs | high |
| CH16 | Command hooks run outside the model: context cost "Zero, unless hook returns additional context". `type: "prompt"` hooks call a model on 13 events and `"agent"` hooks on 12 (all but PermissionRequest), PreToolUse, PostToolUse and Stop among them; the model "defaults to the model Claude Code uses for background functionality" (which version is UNVERIFIED per install) and an optional `model` field overrides it. Output caps: cite E22. | R13 F14; code.claude.com/docs/en/features-overview "Context cost by feature"; hooks doc "Prompt-based hooks" and prompt hook fields | high (doc); model resolution UNVERIFIED |
| CH17 | GitHub Actions per minute, rounded up per job: Linux 2-core $0.006, Windows $0.010, macOS 3/4-core $0.062 (10.3× Linux). Included usage is in dollars (Free $12, Pro/Team $18, Enterprise Cloud $300). Standard runners on public repos and self-hosted runners are $0; the $0.002 self-hosted fee is postponed. | R13 F16, F17; docs.github.com billing pages; GitHub changelog 2025-12-16 | high, volatile |
| CH18 | Cloud reference prices (us-east-1 and Cloudflare, dated): AWS egress $0.09/GB first 10 TB; S3 $0.023/GB-mo; public IPv4 $0.005/h; R2 $0.015/GB-mo with 10 GB, 1M Class A and 10M Class B free and no egress; Workers $5 min. | R13 F18, §2 | high, volatile |
| CH19 | Real cloud bills: 83% of container cost is idle capacity; cross-AZ transfer ≈ all other transfer combined (Datadog, 2023–24 data); self-reported waste 29% (Flexera 2026). Rank idle capacity and data transfer before hot-path code. | R13 F19 | medium |
| CH20 | Push beats polling (SQS long polling; Apple energy guide). systemd `AccuracySec=` defaults to 1min to coalesce wakeups; `1us` defeats it. | R13 F20 | high |
| CH21 | `claude plugin eval` JSON carries measured `costUsd`, `judgeCostUsd`, `durationSeconds`, `turns`. rules-lawyer arms cost $1.01–1.51 vs baseline $0.36–0.40. | R13 F21; local eval result JSON | measured |
| CH22 | Owner's one panel, beyond E41: about 44M cache-read tokens; 1.6M–16.9M tokens per agent including cache reads, so a "100k to 170k tokens" per-reviewer figure matches peak context, not consumption; the cheapest reviewer used 1/70th of the cache reads of the costliest. | R16 §2 totals, F10 | measured, n=1 |
| CH23 | Owner's cost episodes, used to order the checklists: a 50-minute panel ("Whats taking so long"); an expensive tier re-proving settled lines ($1.77, ~5× baseline); ≥14 false wakes, each a full agent turn; a Stop hook re-firing 3–4× per eval run. | R16 §5 cost-hawk table; R5 ep. 8b | measured episodes, small n |
| CH24 | Spend brakes for unattended runs: `--max-turns` and `--max-budget-usd` with `-p`, and a configured spend limit. | code.claude.com/docs/en/best-practices | high |

## Severity anchors in cost terms

The scale is in `${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md` §5. In cost terms:

- **BLOCK:** unbounded spend (a loop or retry with no effective cap); or a steady-state line ≥ the team's whole yardstick spend (≥$13 × devs per day, CH8); or a cost claim that decides go/no-go and is off by ≥5×.
- **SHOULD:** ≥$1 per developer per day, ≥10% of the artifact's own bill, or ≥$50/month for a single owner; or a saving claim off by ≥2× that a decision rests on.
- **COULD:** real but under those lines, above materiality.

## Checks not to make

- Lines under ~5% of the sheet's largest line or under $1/month per user (CH8, R13 §4). That is the materiality line.
- One-off costs: one eval run, one migration, one `/compact` (CH9).
- Standard runners on public repos; self-hosted runner minutes (CH17).
- "The Stop hook loops forever" when no tool call happens between blocks: it ends after 8 (or `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`) (E20).
- Pricing hook output above 10,000 chars at full size (E22).
- Command-hook execution as token cost (CH16).
- Output-token trimming in agent loops while context dominates (CH10, CH11).
- Run-to-run variance within ×2 as a defect (CH11).
- Any price without a table row, a fetch this session, or a date (CH1).

## Judgement calls

- **1h vs 5m TTL for bursty work.** 1h writes cost 2× base against 1.25×, so 1h pays off only when the gap between requests often exceeds 5 minutes and the prefix is read at least twice (CH14, CH3 break-even). Show both prices.
- **Subscription vs API framing.** On a subscription, tokens are a share of plan limits, not dollars; on an API key, credits or a cloud provider they are dollars at list price (CH7). Say which the artifact uses; if it doesn't say, price both.
