# orchestrate

Token-frugal orchestration doctrine for an expensive top-level model.

## Why

An expensive top-level model burns budget when it does token-heavy bounded work itself. This skill reserves the expensive tier for judgment and routes scan/reduce/mechanical work to the cheapest capable subagent tier, demanding compact, traceable returns and vetting them before acting.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/orchestrate` | skill | Doctrine for deciding whether to orchestrate, routing each slice to the cheapest capable tier, structuring handoff packets, requiring compact traceable returns, and gating completion on verification. Model- and harness-agnostic; no pre-baked workers. |

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install orchestrate@claude-toolbelt
```

## Usage

Invoke when a token-heavy task has independent, parallelisable slices. The skill walks Step 0 (decide whether to orchestrate at all), tier routing by judgment demand, the five-part handoff packet, compact-return rules with provenance, parallel-width limits, and a verification gate before claiming done.

## Tests

```bash
bash plugins/orchestrate/tests/all.sh
```
