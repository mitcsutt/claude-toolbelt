---
name: gh-pending-review
description: This skill should be used when adding inline comments to a GitHub pull request that may already have a pending review, when posting PR review comments via `gh api`, when the user asks to "add comments to a PR", "add inline comments", "comment on this PR", "add review comments", or when a GitHub REST API call fails with "user_id can only have one pending review per pull request". Auto-applies whenever posting inline PR comments programmatically.
---

# GitHub Pending Review Comments

Add inline comments to a GitHub pull request, handling the case where the authenticated user already has a pending review. The GitHub REST API rejects new comments and new reviews when a pending review exists. This skill uses GraphQL to append comments to the existing pending review instead.

## Why This Exists

The GitHub REST API has three failure modes when a pending review exists:

1. `POST /repos/{owner}/{repo}/pulls/{number}/comments` fails with `"user_id can only have one pending review per pull request"`
2. `POST /repos/{owner}/{repo}/pulls/{number}/reviews` with `event: "PENDING"` fails with `"Variable $event of type PullRequestReviewEvent was provided invalid value"` (PENDING is not a valid event)
3. Same endpoint without `event` field fails with `"User can only have one pending review per pull request"`

The only reliable path is the GraphQL `addPullRequestReviewThread` mutation targeting the existing pending review's node ID.

## Workflow

### Step 1: Check for existing pending review

Before posting any inline comments, query for a pending review owned by the authenticated user.

```bash
gh api graphql -f query='
  query($owner:String!,$repo:String!,$number:Int!) {
    repository(owner:$owner,name:$repo) {
      pullRequest(number:$number) {
        reviews(states:PENDING,first:1) {
          nodes { id databaseId }
        }
      }
    }
  }' -F owner=OWNER -F repo=REPO -F number=NUMBER
```

Two outcomes:
- **Pending review exists** → extract the GraphQL node ID (e.g. `PRR_kwDO...`). Proceed to Step 2.
- **No pending review** → safe to use REST API. Skip to Step 3.

### Step 2: Add comments to existing pending review (GraphQL)

For each comment, use the `addPullRequestReviewThread` mutation with the pending review's node ID:

```bash
gh api graphql -f query='
  mutation($reviewId:ID!,$path:String!,$line:Int!,$side:DiffSide!,$body:String!) {
    addPullRequestReviewThread(input:{
      pullRequestReviewId: $reviewId
      path: $path
      line: $line
      side: $side
      body: $body
    }) {
      thread { id }
    }
  }' \
  -F reviewId="PRR_kwDO..." \
  -F path="src/foo.ts" \
  -F line=42 \
  -F side="RIGHT" \
  -F body="Comment text here"
```

**Parameters:**
- `pullRequestReviewId` — the GraphQL node ID from Step 1 (NOT the numeric `databaseId`)
- `path` — file path relative to repo root
- `line` — line number in the diff (use RIGHT side line numbers for new/modified lines)
- `side` — `RIGHT` for additions/modifications, `LEFT` for deletions
- `body` — the comment text (supports GitHub markdown)

**Multi-line comments:** To highlight a range, add `startLine` and `startSide`:

```bash
-F startLine=40 \
-F startSide="RIGHT" \
-F line=45 \
-F side="RIGHT"
```

Comments added this way appear in the user's pending review alongside any comments they've already drafted (e.g. from VS Code's GitHub PR extension). The user submits the review themselves.

### Step 3: No pending review — use REST API

When no pending review exists, the standard REST endpoint works:

```bash
gh api repos/OWNER/REPO/pulls/NUMBER/comments \
  -f body="Comment text" \
  -f commit_id="FULL_SHA" \
  -f path="src/foo.ts" \
  -F line=42 \
  -f side="RIGHT"
```

This creates a standalone review comment that's immediately visible.

Alternatively, to create a new pending review with comments (for batch submission):

```bash
gh api repos/OWNER/REPO/pulls/NUMBER/reviews \
  --input payload.json
```

Where `payload.json` contains:

```json
{
  "commit_id": "abc123...",
  "comments": [
    { "path": "src/foo.ts", "line": 42, "side": "RIGHT", "body": "..." }
  ]
}
```

Omit the `event` field entirely to keep the review pending. Valid `event` values are `APPROVE`, `REQUEST_CHANGES`, and `COMMENT` (not `PENDING`).

## Sandbox Rules

Every `gh api` and `gh pr` call MUST use `dangerouslyDisableSandbox: true`. The sandbox blocks `~/.ssh` read access, which breaks GitHub authentication. Go straight to disabled sandbox — do not try sandboxed first.

## Error Recovery

If a REST call fails with the pending review error after Step 1 reported no pending review, the review may have been created between the check and the call (e.g. by a concurrent VS Code session). Re-run Step 1 and fall back to Step 2.

## Preserving Existing Comments

**Never create a new review object when a pending review exists.** This was the original failure mode that led to this skill — creating a new review can overwrite or orphan comments the user drafted in VS Code or another tool. Always append to the existing pending review via GraphQL.

## Quick Reference

| Scenario | Method |
|---|---|
| Pending review exists | GraphQL `addPullRequestReviewThread` (Step 2) |
| No pending review, want immediate comments | REST `POST pulls/{n}/comments` (Step 3) |
| No pending review, want batch pending | REST `POST pulls/{n}/reviews` without `event` (Step 3) |
| REST fails with pending review error | Fall back to Step 1 → Step 2 |
