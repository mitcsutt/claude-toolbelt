# Permission Rule Syntax Reference

Claude Code permissions live in `settings.json` under `permissions.allow`, `permissions.deny`, and `permissions.ask`. All three use the same pattern syntax.

## Precedence

**deny > ask > allow**

If a tool call matches both a deny rule and an allow rule, the deny rule wins. If it matches both ask and allow, allow wins (the tool is auto-approved). If it matches only ask, the user is prompted.

## Rule Scope

Rules can be defined at multiple levels (merged at runtime):

| File | Scope | Shared via git? |
|------|-------|-----------------|
| `~/.claude/settings.json` | Global (all projects) | No |
| `<project>/.claude/settings.json` | Project | Yes |
| `<project>/.claude/settings.local.json` | Project (personal) | No (gitignored) |

Project-level rules are merged with global rules. Deny rules from any level take precedence.

## Rule Pattern Types

### Bash Commands

```
Bash(cmd:*)          # Matches cmd with any arguments (most common)
Bash(cmd)            # Matches cmd with NO arguments
Bash(cmd *)          # Matches cmd followed by space and anything
Bash(cmd arg:*)      # Matches cmd arg with any further arguments
Bash(cmd --flag*)    # Matches cmd --flag with any suffix
```

**Examples:**
```json
"Bash(git:*)"           // git status, git push, git log, etc.
"Bash(jq:*)"            // jq '.name', jq -r '.[]', etc.
"Bash(npm run:*)"       // npm run build, npm run test, etc.
"Bash(git push --force*)" // git push --force, git push --force-with-lease
```

**Command extraction:** Claude extracts the first word of the command after stripping leading environment variable assignments (`FOO=bar cmd ...` → `cmd`). Shell metacharacters (`;`, `&&`, `|`, etc.) terminate the command word.

### Standard Tools

Direct name match — no pattern syntax needed:

```json
"Read"          // All file reads
"Write"         // All file writes
"Edit"          // All file edits
"Grep"          // All grep searches
"Glob"          // All glob searches
"WebFetch"      // All web fetches
"WebSearch"     // All web searches
"Agent"         // All agent spawns
```

### Path-Scoped Tool Rules

Restrict file tools to specific paths using glob patterns:

```json
"Read(~/.config/**)"       // Read only from ~/.config and subdirs
"Write(src/**)"            // Write only within src/
"Edit(src/**/*.ts)"        // Edit only TypeScript files in src/
"Read(~/.ssh/**)"          // Read SSH keys (dangerous — typically denied)
```

Path patterns use glob syntax: `*` matches within a directory, `**` matches across directories.

### MCP Tool Rules

MCP tools follow the naming convention `mcp__<server>__<tool>`:

```json
"mcp__exa__web_search_exa"           // Specific tool on specific server
"mcp__exa__*"                        // All tools on the exa server
"mcp__plugin_find-docs_context7__*"  // All tools on a plugin-scoped server
"mcp__claude-in-chrome__*"           // All Chrome automation tools
```

**Wildcard patterns:** Use `*` at the end to match all tools on a server. The `*` expands to match any suffix.

**Plugin-scoped MCP servers:** Plugins prefix their MCP servers with `plugin_<name>_`, resulting in patterns like `mcp__plugin_find-docs_exa__*`.

## Deny Rules

Same syntax as allow. Common deny patterns:

```json
"Bash(sudo:*)"            // Block privilege escalation
"Bash(shutdown:*)"        // Block system shutdown
"Bash(rm -rf *)"          // Block recursive force delete
"Read(~/.ssh/**)"         // Block SSH key access
"Read(~/.aws/**)"         // Block AWS credential access
"Edit(~/.bashrc)"         // Block shell config modification
```

## Ask Rules

Same syntax. The user is prompted to approve or deny each matching call:

```json
"Bash(git push --force*)"  // Confirm force pushes
"Bash(rm -rf *)"           // Confirm recursive deletes
"Bash(git reset --hard*)"  // Confirm hard resets
"Read(**/.env)"            // Confirm .env file access
"Write(**/.env)"           // Confirm .env file writes
```

## Pattern Matching Details

- Patterns are matched against the full tool invocation, not just the command name
- `*` in Bash patterns matches any characters (including spaces) — `Bash(git:*)` matches `git push origin main`
- Path patterns in Read/Write/Edit use glob rules: `*` matches within one directory level, `**` crosses levels
- MCP wildcards: `mcp__server__*` matches `mcp__server__any_tool_name`
- Matching is case-sensitive
- No regex support — only the glob-like patterns documented above

## Common Pitfalls

1. **Redundant rules:** `Bash(git push:*)` is unnecessary if `Bash(git:*)` exists (the broader rule already covers it)
2. **Dead allow rules:** An allow rule that also matches a deny pattern is effectively dead (deny wins)
3. **Dead ask rules:** An ask rule that also matches an allow pattern is dead (allow wins, no prompt shown)
4. **Compound commands:** Claude Code matches allow rules per sub-command. `Bash(git:*)` covers `git status` but not `git status && echo done` — the second part (`echo done`) needs its own allow rule or it falls through to Auto mode's classifier. Broad rules like `Bash(echo:*)` help here.
