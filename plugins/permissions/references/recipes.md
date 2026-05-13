# Common Permission Rule Recipes

Pre-built rule sets organized by workflow. Copy the relevant rules into `~/.claude/settings.json` (global) or `<project>/.claude/settings.json` (project-scoped) under `permissions.allow`, `permissions.deny`, or `permissions.ask`.

The rules below are shown as YAML for readability; settings.json uses the same pattern strings in JSON arrays.

## Git Workflow

### Allow rules
```yaml
- "Bash(git:*)"
- "Bash(gh:*)"
```

### Sandbox exclusions
```yaml
excludedCommands:
  - git
  - gh
```
Required because git/gh need `~/.ssh` access for remote operations.

### Ask rules (safety guardrails)
```yaml
- "Bash(git push --force*)"
- "Bash(git reset --hard*)"
- "Bash(git checkout -- *)"
- "Bash(git clean -f*)"
- "Bash(git branch -D *)"
```

### Deny rules
```yaml
- "Bash(git push --force origin main*)"
- "Bash(git push --force origin master*)"
```
Prevents force-pushing to protected branches.

## Node.js Development

### Allow rules
```yaml
- "Bash(node:*)"
- "Bash(npm:*)"
- "Bash(npx:*)"
- "Bash(pnpm:*)"
- "Bash(tsx:*)"
- "Bash(tsc:*)"
- "Bash(jest:*)"
- "Bash(vitest:*)"
- "Bash(eslint:*)"
- "Bash(prettier:*)"
```

### Sandbox exclusions
```yaml
excludedCommands:
  - npm
  - npx
  - pnpm
```
Package managers need network access for install and write access to `node_modules` and cache dirs.

### allowWrite additions
```yaml
allowWrite:
  - /tmp
  - /private/tmp
```
Build tools and temp file creation.

## Python Development

### Allow rules
```yaml
- "Bash(python:*)"
- "Bash(python3:*)"
- "Bash(pip:*)"
- "Bash(pip3:*)"
- "Bash(pytest:*)"
- "Bash(ruff:*)"
- "Bash(mypy:*)"
- "Bash(black:*)"
- "Bash(uv:*)"
```

### Sandbox exclusions
```yaml
excludedCommands:
  - pip
  - pip3
  - uv
```
Package managers need network + write to site-packages/venv.

### allowWrite additions
```yaml
allowWrite:
  - /tmp
  - /private/tmp
  - .venv
```

## Ruby / Rails Development

### Allow rules
```yaml
- "Bash(ruby:*)"
- "Bash(bundle:*)"
- "Bash(gem:*)"
- "Bash(rake:*)"
- "Bash(rails:*)"
- "Bash(rspec:*)"
- "Bash(rubocop:*)"
```

### Sandbox exclusions
```yaml
excludedCommands:
  - bundle
  - gem
```

## Docker Workflow

### Allow rules
```yaml
- "Bash(docker:*)"
- "Bash(docker-compose:*)"
- "Bash(docker compose:*)"
```

### Sandbox exclusions
```yaml
excludedCommands:
  - docker
  - docker-compose
```
Docker needs socket access (`/var/run/docker.sock`) and broad filesystem reads for build contexts.

### Ask rules (destructive operations)
```yaml
- "Bash(docker system prune*)"
- "Bash(docker volume rm *)"
```

## MCP Server Patterns

### Per-server wildcard (recommended)
```yaml
- "mcp__exa__*"
- "mcp__claude-in-chrome__*"
- "mcp__context7__*"
```
Allows all tools on the server. Appropriate when the entire server is trusted.

### Plugin-scoped MCP servers
Plugin MCP servers are prefixed with `plugin_<name>_`:
```yaml
- "mcp__plugin_find-docs_exa__*"
- "mcp__plugin_find-docs_context7__*"
- "mcp__plugin_bugsnag_bugsnag__*"
```

### Specific tool (narrow)
```yaml
- "mcp__exa__web_search_exa"
- "mcp__context7__query-docs"
```
Use when only certain tools on a server should be auto-approved.

### Atlassian integration
```yaml
- "mcp__claude_ai_Atlassian__*"
```
Or narrow to read-only operations:
```yaml
- "mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql"
- "mcp__claude_ai_Atlassian__getJiraIssue"
- "mcp__claude_ai_Atlassian__searchConfluenceUsingCql"
- "mcp__claude_ai_Atlassian__getConfluencePage"
```

## Read-Only Research

### Allow rules
```yaml
- "Read"
- "Grep"
- "Glob"
- "WebFetch"
- "WebSearch"
- "Agent"
```
These are safe to blanket-allow — they only read data.

## File Operations with Path Scoping

### Unrestricted (trust Claude with all files)
```yaml
- "Read"
- "Write"
- "Edit"
```

### Path-scoped (restrict to specific directories)
```yaml
- "Read(src/**)"
- "Write(src/**)"
- "Edit(src/**)"
- "Read(tests/**)"
- "Write(tests/**)"
- "Edit(tests/**)"
```

### Deny sensitive paths
```yaml
- "Read(~/.ssh/**)"
- "Read(~/.aws/**)"
- "Read(~/.gnupg/**)"
- "Read(**/.env)"
- "Read(**/.env.*)"
- "Edit(~/.bashrc)"
- "Edit(~/.zshrc)"
```

## System Utilities

### Safe read-only commands
```yaml
- "Bash(ls:*)"
- "Bash(find:*)"
- "Bash(cat:*)"
- "Bash(head:*)"
- "Bash(tail:*)"
- "Bash(wc:*)"
- "Bash(sort:*)"
- "Bash(uniq:*)"
- "Bash(diff:*)"
- "Bash(which:*)"
- "Bash(whoami:*)"
- "Bash(date:*)"
- "Bash(echo:*)"
- "Bash(printf:*)"
```

### Text processing
```yaml
- "Bash(jq:*)"
- "Bash(grep:*)"
- "Bash(awk:*)"
- "Bash(sed:*)"
- "Bash(cut:*)"
- "Bash(tr:*)"
- "Bash(xargs:*)"
```

### Process and system inspection
```yaml
- "Bash(ps:*)"
- "Bash(top:*)"
- "Bash(df:*)"
- "Bash(du:*)"
- "Bash(env:*)"
- "Bash(printenv:*)"
```

## Starter Rule Set

A minimal, security-conscious starting point:

```yaml
permissions:
  allow:
    - "Read"
    - "Edit"
    - "Write"
    - "Grep"
    - "Glob"
    - "WebFetch"
    - "WebSearch"
    - "Agent"
    - "Bash(git:*)"
    - "Bash(gh:*)"
    - "Bash(ls:*)"
    - "Bash(find:*)"
    - "Bash(cat:*)"
    - "Bash(head:*)"
    - "Bash(tail:*)"
    - "Bash(jq:*)"
    - "Bash(grep:*)"
    - "Bash(awk:*)"
    - "Bash(sed:*)"
    - "Bash(echo:*)"
    - "Bash(printf:*)"
    - "Bash(wc:*)"
    - "Bash(sort:*)"
    - "Bash(diff:*)"
    - "Bash(which:*)"
    - "Bash(whoami:*)"
    - "Bash(date:*)"
  deny:
    - "Bash(sudo:*)"
    - "Bash(shutdown:*)"
    - "Bash(reboot:*)"
    - "Bash(mkfs:*)"
    - "Bash(dd:*)"
    - "Bash(eval:*)"
    - "Read(~/.ssh/**)"
    - "Read(~/.aws/**)"
    - "Read(~/.gnupg/**)"
    - "Edit(~/.bashrc)"
    - "Edit(~/.zshrc)"
  ask:
    - "Bash(git push --force*)"
    - "Bash(rm -rf *)"
    - "Bash(rm -fr *)"
    - "Bash(git reset --hard*)"

sandbox:
  excludedCommands:
    - git
    - gh
  filesystem:
    allowWrite:
      - /tmp
      - /private/tmp
```

Grow from here using `/permissions-audit` analysis to identify what to add next. Run `/permissions-promote` to turn frequent classifier-hitters into narrow allow rules.
