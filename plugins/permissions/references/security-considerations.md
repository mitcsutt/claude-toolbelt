# Security Considerations for Permission Rules

When suggesting permission rules, balance convenience against security. Every allow rule is a trust decision — auto-approving a tool means Claude can use it without human review.

## Risk Classification

Classify every suggested rule before presenting it:

### LOW Risk
Read-only operations and safe data-processing commands. Auto-approval is almost always appropriate.

**Examples:**
- `Read`, `Grep`, `Glob` — file reading and searching
- `Bash(ls:*)`, `Bash(cat:*)`, `Bash(head:*)`, `Bash(tail:*)` — read-only file inspection
- `Bash(jq:*)`, `Bash(grep:*)`, `Bash(awk:*)`, `Bash(sed:*)` — text processing (sed can write with -i, but Claude uses the Edit tool for that)
- `Bash(wc:*)`, `Bash(sort:*)`, `Bash(uniq:*)`, `Bash(diff:*)` — data analysis
- `Bash(date:*)`, `Bash(echo:*)`, `Bash(printf:*)` — output commands
- `Bash(git status:*)`, `Bash(git log:*)`, `Bash(git diff:*)` — read-only git

### MEDIUM Risk
Write operations with limited scope, network tools with known targets, or commands that modify local state.

**Examples:**
- `Write`, `Edit` — file modification (constrained to project by sandbox)
- `Write(src/**)`, `Edit(src/**)` — path-scoped file modification
- `Bash(git:*)` — includes push/commit (modifies remote state)
- `Bash(npm run:*)` — runs arbitrary scripts defined in package.json
- `Bash(curl:*)` — network access to arbitrary URLs
- `WebFetch`, `WebSearch` — controlled network access
- `mcp__server__*` — all tools on a specific MCP server (scope depends on server)

### HIGH Risk
Broad write access, unconstrained execution, or commands that modify system state.

**Examples:**
- `Bash(docker:*)` — container management, image builds, volume mounts
- `Bash(npm install:*)` — downloads and executes arbitrary packages
- `Bash(pip install:*)` — same for Python
- `Bash(make:*)` — runs arbitrary Makefile targets
- `Bash(chmod:*)`, `Bash(chown:*)` — permission changes
- Broad MCP wildcards for servers with write capabilities

### CRITICAL — Never Auto-Suggest
These should remain in deny or ask lists. Flag them for manual review only.

**Examples:**
- `Bash(sudo:*)` — privilege escalation
- `Bash(rm -rf:*)`, `Bash(rm -fr:*)` — recursive force delete
- `Bash(ssh:*)` — remote system access
- `Bash(eval:*)` — arbitrary code execution
- `Bash(shutdown:*)`, `Bash(reboot:*)` — system control
- `Bash(dd:*)`, `Bash(mkfs:*)` — disk/partition operations
- Any rule that overlaps with an existing deny rule
- `Read(~/.ssh/**)`, `Read(~/.aws/**)` — credential file access

## Rules That Should Never Be Auto-Suggested

1. **Anything matching an existing deny rule** — deny rules exist for a reason. Never suggest allow rules that would conflict.
2. **Credential access** — `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.docker/config.json`, `.env` files
3. **Shell config modification** — `~/.bashrc`, `~/.zshrc`, `~/.profile`
4. **Broad execution** — `Bash(python:*)`, `Bash(node:*)`, `Bash(bash:*)` (too broad — any script could run)
5. **System modification** — `sudo`, `shutdown`, `reboot`, `mkfs`, `dd`

## Ask Rules as Middle Ground

For operations that are sometimes necessary but warrant human review, suggest `ask` rules instead of `allow`:

**Good candidates for ask:**
- `Bash(git push --force*)` — force push (destructive to remote)
- `Bash(rm -rf *)` — recursive delete (confirm the path)
- `Bash(git reset --hard*)` — discard uncommitted work
- `Read(**/.env)`, `Write(**/.env)` — environment file access
- `Bash(docker rm:*)` — container deletion

**When to suggest promoting ask → allow:** If the log shows a pattern matching an ask rule that is called >20 times and the user has consistently approved, mention it as a candidate for promotion to allow. Include the security trade-off in the suggestion.

**When to suggest demoting ask → deny:** If an ask rule covers an action the user always denies (inferred from a low completion rate on subsequent calls), suggest moving it to deny.

## Principle of Least Privilege

Always suggest the narrowest rule that covers the observed friction:

| Friction Pattern | Narrow Rule (Preferred) | Broad Rule (Avoid) |
|-----------------|------------------------|-------------------|
| `docker build -t app .` | `Bash(docker build:*)` | `Bash(docker:*)` |
| `npm run test` | `Bash(npm run:*)` | `Bash(npm:*)` |
| `git push origin main` | `Bash(git:*)` | Already minimal |
| MCP tool: `mcp__exa__web_search_exa` | `mcp__exa__*` | Already appropriate |

Exception: if 3+ subcommands of the same tool appear in the friction log (e.g., `docker build`, `docker run`, `docker ps`), the broader rule is justified.

## Project vs Global Scoping

- **Project-specific tools** (terraform, kubectl, rails, rake) should go in `<project>/.claude/settings.json`, not global
- **Universal tools** (git, jq, ls, grep) belong in global `~/.claude/settings.json`
- **Sensitive project overrides** (custom .env paths, project-specific credentials) should go in `<project>/.claude/settings.local.json` (gitignored)

Suggest project-level rules when >80% of friction for a tool comes from a single working directory.

## Auditing Rules via `/permissions-audit`

Periodically audit for:
1. **Dead allow rules** — allow rules that never matched any log entry (candidates for removal)
2. **Dead ask rules** — ask rules that match an allow pattern (allow wins, ask never triggers)
3. **Redundant rules** — specific rules subsumed by broader ones (`Bash(git push:*)` under `Bash(git:*)`)
4. **Overly broad rules** — rules that could be narrowed based on actual usage
5. **Classifier pressure** — patterns hitting Auto mode's classifier repeatedly that should be promoted

Use the `/permissions-audit` skill to read the log and surface these patterns.
