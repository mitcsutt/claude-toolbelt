# GitHub API Error Patterns

Error messages that indicate a pending review conflict. When any of these appear, fall back to the GraphQL workflow in the main skill.

## REST API Errors

### Creating individual comment (POST pulls/{n}/comments)

```json
{
  "message": "Validation Failed",
  "errors": [{
    "resource": "PullRequestReview",
    "code": "custom",
    "field": "user_id",
    "message": "user_id can only have one pending review per pull request"
  }]
}
```

HTTP 422. Occurs when the authenticated user has a pending review and tries to create a standalone comment. GitHub forces all comments through the pending review.

### Creating new review with PENDING event (POST pulls/{n}/reviews)

```json
{
  "message": "Unprocessable Entity",
  "errors": ["Variable $event of type PullRequestReviewEvent was provided invalid value"]
}
```

HTTP 422. `PENDING` is not a valid `event` value. Valid values: `APPROVE`, `REQUEST_CHANGES`, `COMMENT`. To create a pending review, omit the `event` field entirely.

### Creating new review without event field (POST pulls/{n}/reviews)

```json
{
  "message": "Unprocessable Entity",
  "errors": ["User can only have one pending review per pull request"]
}
```

HTTP 422. A pending review already exists. Cannot create another. Must use GraphQL `addPullRequestReviewThread` to append to the existing one.

## GraphQL Mutations Reference

### addPullRequestReviewThread

Adds a new comment thread to an existing review. Requires the review's GraphQL node ID (starts with `PRR_`).

Input fields:
- `pullRequestReviewId` (ID!, required) — GraphQL node ID of the review
- `path` (String!, required) — file path relative to repo root
- `body` (String!, required) — comment body (markdown)
- `line` (Int!, required) — end line in diff
- `side` (DiffSide) — RIGHT or LEFT
- `startLine` (Int) — start line for multi-line comments
- `startSide` (DiffSide) — side for start line

### Query to find pending review node ID

```graphql
query($owner:String!,$repo:String!,$number:Int!) {
  repository(owner:$owner,name:$repo) {
    pullRequest(number:$number) {
      reviews(states:PENDING,first:1) {
        nodes {
          id          # GraphQL node ID (PRR_kwDO...)
          databaseId  # REST API numeric ID
        }
      }
    }
  }
}
```

Note: `reviews(states:PENDING)` only returns reviews owned by the authenticated user. Other users' pending reviews are not visible.
