---
name: idea
description: Use when the user wants an idea, problem or "we should probably..." thought logged as a GitHub issue - "log this idea", "file an idea", "park this", "idea:", or "research/flesh out this idea". Repo-local skill for this repo: files using the idea issue form; log-only by default.
allowed-tools: Bash(gh issue list:*), Bash(gh issue view:*), Bash(gh label list:*), WebSearch, WebFetch
---

# Idea

File the user's idea as a GitHub issue in this repo (public), matching the form at `.github/ISSUE_TEMPLATE/idea.yml`. Capture, do not design.

## 1. Pick the mode

- **Log only** - the user says "just log", "park", "note this", or gives no research signal. Default when ambiguous; end the draft step with one line offering research.
- **Log + research** - the user says "research", "flesh out", "look into", "what exists for".

## 2. Dedupe

Run `gh issue list --repo mitcsutt/claude-toolbelt --label idea --state all --search "<2-3 keywords>" --json number,title,state,url`. Try a second keyword set before concluding there is no match. On a plausible match, show it and ask: link to it, comment on it, or file anyway.

## 3. Draft

Title: `idea: <short noun phrase>`. Body: these `###` headings, in this order, exactly as the form renders them. Use `_No response_` for any field the user did not address.

```
### Problem
### Proposed direction
### Constraints and requirements
### Open questions
### Success criteria
### Research status
### Public repo check
```

- **Problem** keeps the user's wording verbatim. Tidy typos only.
- Every other field holds what the user stated. A requirement, criterion or direction they did not say stays out; unknowns go under **Open questions** as questions.
- Solution-open ideas leave **Proposed direction** as `_No response_`.
- **Research status** is `Logged only` or `Researched`.
- **Public repo check** is `- [x] No secrets or private information in this issue`. Replace secrets, tokens, customer data, private repo/project names and personal paths with `[redacted]`, and tell the user what was redacted.

## 4. Research (research mode only)

Follow [`references/research-section.md`](references/research-section.md). Cap at about 8 searches/fetches. Append a `### Research` section after the form headings, with every claim linked to a source. The user's fields above stay untouched; research never edits them.

## 5. Approve, then file

Show the full title and body. Filing publishes to a public repo, so wait for an explicit yes; apply edits and re-show.

On yes:

```bash
gh issue create --repo mitcsutt/claude-toolbelt --label idea --title "idea: ..." --body-file - <<'BODY'
...
BODY
```

If the `idea` label is missing, stop and tell the user; do not create it.

Reply with the issue URL and nothing else to do. Run no follow-up work unless asked.
