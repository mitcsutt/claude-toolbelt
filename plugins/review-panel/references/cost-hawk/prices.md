# cost-hawk price table

Verified on 2026-11-19 against the sources named per row; re-check live before a finding depends on a volatile fact (prices, platform behaviour). Every row was re-checked against its live page on 2026-11-20 with no change.

Prices change. Before a dollar figure decides a finding's severity, WebFetch the row's URL and quote the row in Evidence. If the page can't be fetched, show the arithmetic with the price below and write `price as of 2026-11-19`. A price that is in neither this table nor a page fetched this session is UNVERIFIED.

Why each rule exists: `evidence.md` (CH1–CH4 Claude API, CH17 GitHub Actions, CH18 cloud). MTok is one million tokens. Claude Code's cost figures use these list prices unless an admin set `modelPricing`, which changes the figure shown, not the bill (CH7).

| item | unit | price | source URL | fetched |
|---|---|---|---|---|
| Fable 5.2 in / 5m write / 1h write / read / out | MTok | $8 / $10 / $16 / $0.20 / $40 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Opus 5.5 in / 5m write / 1h write / read / out | MTok | $4 / $5 / $8 / $0.20 / $20 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Sonnet 5.5 in / 5m write / 1h write / read / out | MTok | $2 / $2.50 / $4 / $0.10 / $10 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Haiku 5.5, prompt ≤100k tokens: in / 5m write / 1h write / read / out | MTok | $0.10 / $0.125 / $0.20 / $0.01 / $0.50 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Haiku 5.5, prompt >100k tokens (cache reads and writes count): in / 5m write / 1h write / read / out | MTok | $0.50 / $0.625 / $1 / $0.05 / $2.50 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Haiku 4.5 in / 5m write / 1h write / read / out (still listed) | MTok | $1 / $1.25 / $2 / $0.10 / $5 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Sonnet 5 in / out (footnote 3: the $3/$15 rise "will not occur") | MTok | $2 / $10 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Cache multipliers | × base input | 5m write 1.25; 1h write 2; read 0.1 (Opus/Sonnet 5.5: 0.05; Fable 5.1/5.2: 0.025) | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Minimum cacheable prompt (shorter prompts are silently not cached) | tokens | 512 for Fable 5.2, Opus 5.5, Sonnet 5.5, Haiku 5.5; 1,024 Sonnet 5; 4,096 Haiku 4.5 | https://platform.claude.com/docs/en/build-with-claude/prompt-caching | 2026-11-19 |
| Batch API | discount | 50% on in and out (Opus 5.5 $2/$10; Haiku 5.5 ≤100k $0.05/$0.25, >100k $0.25/$1.25) | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Fast mode Opus 5.5 / Fable 5.2 | MTok in / out | $8 / $40; $16 / $80 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| US-only `inference_geo` (4.6+ models) | multiplier | 1.1× on every token category | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Web search tool | per 1,000 searches | $10 plus tokens | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Web fetch tool | — | no extra charge, tokens only ("Average webpage (10 kB): ~2,500 tokens") | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Code execution (without web search/fetch) | container-hour | 1,550 free h/org/month, then $0.05; 5-minute minimum | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Managed Agents runtime | session-hour | $0.08 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-11-19 |
| Token counting endpoint | request | free (RPM-limited) | https://platform.claude.com/docs/en/build-with-claude/token-counting | 2026-11-19 |
| Claude Code average spend (vendor aggregate) | dev-day | ≈$13 per active day; <$30 for 90% of users; $150–250 per dev-month | https://code.claude.com/docs/en/costs | 2026-11-19 |
| GH Actions Linux 2-core x64 / arm64 / 1-core | minute, rounded up per job | $0.006 / $0.005 / $0.002 | https://docs.github.com/en/billing/reference/actions-runner-pricing | 2026-11-19 |
| GH Actions Windows 2-core | minute | $0.010 | https://docs.github.com/en/billing/reference/actions-runner-pricing | 2026-11-19 |
| GH Actions macOS 3/4-core; macOS 12-core; M2 Pro 5-core | minute | $0.062; $0.077; $0.102 | https://docs.github.com/en/billing/reference/actions-runner-pricing | 2026-11-19 |
| GH Actions Linux 4 / 8 / 16-core x64 | minute | $0.012 / $0.022 / $0.042 | https://docs.github.com/en/billing/reference/actions-runner-pricing | 2026-11-19 |
| GH included compute (private repos) | month, in dollars | Free $12; Pro $18; Team $18; Enterprise Cloud $300; standard runners on public repos and self-hosted runners don't draw on it | https://docs.github.com/en/billing/concepts/product-billing/github-actions | 2026-11-19 |
| GH Actions storage overage (private repos) | GB-month, billed hourly | artifacts and Packages (shared) $0.25; Actions cache $0.07 | https://docs.github.com/en/billing/concepts/product-billing/github-actions | 2026-11-20 |
| GH cache storage | repo | 10 GB included | https://docs.github.com/en/billing/concepts/product-billing/github-actions | 2026-11-19 |
| GH self-hosted platform fee | minute | $0 (the $0.002 fee is postponed) | https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/ | 2026-11-19 |
| AWS egress to internet, us-east-1 | GB | $0.09 first 10 TB/month; $0.085 next 40 TB; $0.07 next 100 TB; $0.05 over 150 TB | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSDataTransfer/current/us-east-1/index.json | 2026-11-19 |
| AWS S3 Standard storage, us-east-1 | GB-month | $0.023 (first 50 TB) | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json | 2026-11-19 |
| AWS S3 requests, us-east-1 | per 1,000 | PUT/COPY/POST/LIST $0.005; GET $0.0004 | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json | 2026-11-19 |
| AWS Lambda x86 / Arm, us-east-1 | GB-second; request | $0.0000166667 / $0.0000133334 (first tier); $0.20 per 1M requests | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/us-east-1/index.json | 2026-11-19 |
| AWS EC2 t4g.nano / micro / small; t3.micro, us-east-1 Linux | hour | $0.0042 / $0.0084 / $0.0168; $0.0104 (t4g.small ≈ $12.26/month) | https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json | 2026-11-19 |
| AWS public IPv4, idle or in use | hour | $0.005 (≈ $3.65/month) | https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/us-east-1/index.json | 2026-11-19 |
| Cloudflare Workers Paid | month; requests; CPU | $5 minimum; 10M requests included then $0.30/M; 30M CPU-ms included then $0.02/M; no egress charge | https://developers.cloudflare.com/workers/platform/pricing/ | 2026-11-19 |
| Cloudflare R2 Standard | GB-month; ops | $0.015; Class A $4.50/M; Class B $0.36/M; egress free; free tier 10 GB-month, 1M Class A, 10M Class B | https://developers.cloudflare.com/r2/pricing/ | 2026-11-19 |

## Not in this table

Fetch before citing; until then a figure that uses them is UNVERIFIED:

- AWS NAT gateway, hourly and per-GB processing (not in the offer files fetched).

## URLs to fetch

One per line. The AWS offer files are JSON (1–2 MB); read them only for the one row you need.

https://platform.claude.com/docs/en/about-claude/pricing.md
https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
https://docs.github.com/en/billing/reference/actions-runner-pricing
https://docs.github.com/en/billing/concepts/product-billing/github-actions
https://developers.cloudflare.com/r2/pricing/
https://developers.cloudflare.com/workers/platform/pricing/
https://code.claude.com/docs/en/costs.md
https://code.claude.com/docs/en/prompt-caching.md
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSDataTransfer/current/us-east-1/index.json
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-1/index.json
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/us-east-1/index.json
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/us-east-1/index.json

WebFetch prompt to use: "Quote verbatim the table row(s) for <item>, with column headers."
