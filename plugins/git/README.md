# git

Generic git and GitHub helper skills.

## Why

A shared home for narrow git/GitHub workflow skills, so they install as one plugin instead of one plugin each. Currently ships `gh-pending-review`; future git/GitHub skills are added as siblings under `skills/`.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `gh-pending-review` | skill | Adds inline comments to a GitHub PR that already has a pending review, using the GraphQL `addPullRequestReviewThread` mutation — the REST API rejects a second pending review and can orphan comments drafted elsewhere. |

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install git@claude-toolbelt
```

## Usage

`gh-pending-review` auto-applies when posting inline PR comments programmatically, or when a REST call fails with `"user_id can only have one pending review per pull request"`. It checks for an existing pending review, appends via GraphQL when one exists, and falls back to the REST endpoint when none does.

## Tests

```bash
bash plugins/git/tests/all.sh
```
