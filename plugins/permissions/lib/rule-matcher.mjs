// Port of Claude Code permission rule matching semantics.
// Canonical spec: plugins/permissions/references/rule-syntax.md
//
// Single export: matchRule(rule, entry) -> boolean
//   rule:  string like "Bash(git:*)", "Read(~/.ssh/**)", "Read", "mcp__exa__*"
//   entry: { tool: string, detail: string }
//
// Used by: permissions-audit, permissions-lint, permissions-promote,
// permissions-bootstrap-project. Keep this contract stable.

import { homedir } from "node:os";

// Strip leading env-var assignments from a Bash detail.
//   "FOO=bar NODE_ENV=test git status" -> "git status"
// Per rule-syntax.md: "Claude extracts the first word of the command after
// stripping leading environment variable assignments."
function stripEnv(detail) {
  return (detail ?? "").replace(/^([A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)+/, "");
}

// Compile a glob to an anchored RegExp.
//
// Path mode (default, for Read/Write/Edit and MCP tool names):
//   "**"   -> ".*"   (crosses directory levels)
//   "*"    -> "[^/]*" (within one directory level)
//
// Bash mode: `*` matches any characters including "/" per rule-syntax.md
// ("`*` in Bash patterns matches any characters (including spaces)").
//
// Regex metacharacters are escaped. This is intentionally minimal — no brace
// expansion, no character classes, no extglob.
function globToRegex(glob, { bash = false } = {}) {
  let out = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      out += ".*";
      i++;
    } else if (c === "*") {
      out += bash ? ".*" : "[^/]*";
    } else if ("[](){}.+^$|\\?".includes(c)) {
      out += "\\" + c;
    } else {
      out += c;
    }
  }
  return new RegExp("^" + out + "$");
}

// Expand a leading "~/" to the user's home directory.
function expandHome(p) {
  if (typeof p !== "string") return p;
  if (p === "~") return homedir();
  if (p.startsWith("~/")) return homedir() + p.slice(1);
  return p;
}

// Parse a rule into { tool, arg }.
//   "Bash(git:*)"            -> { tool: "Bash", arg: "git:*" }
//   "Read"                   -> { tool: "Read", arg: null }
//   "mcp__plugin_foo_bar_*"  -> { tool: "mcp__plugin_foo_bar_*", arg: null }
// Returns null for malformed input (unbalanced parens, empty tool, etc).
function parseRule(rule) {
  if (typeof rule !== "string" || rule.length === 0) return null;

  const parenIdx = rule.indexOf("(");
  if (parenIdx === -1) {
    // No paren: whole string is the tool name (may contain a trailing glob).
    return { tool: rule, arg: null };
  }

  if (!rule.endsWith(")")) return null; // unbalanced
  const tool = rule.slice(0, parenIdx);
  const arg = rule.slice(parenIdx + 1, -1);
  if (tool.length === 0) return null;
  return { tool, arg };
}

// Does the tool name in the rule match the tool name in the entry?
// Supports trailing wildcards for MCP rules (e.g. "mcp__exa__*").
function toolMatches(ruleTool, entryTool) {
  if (ruleTool.includes("*")) {
    return globToRegex(ruleTool).test(entryTool);
  }
  return ruleTool === entryTool;
}

// Bash-specific arg matching.
//   "prefix:*"     -> detail === "prefix" OR detail.startsWith("prefix ")
//   "prefix *"     -> detail starts with "prefix " then glob over the rest
//   "prefix"       -> literal exact match
//   contains "*"   -> glob over detail
function matchBashArg(arg, detail) {
  // Colon-suffix prefix form: Bash(cmd:*)
  if (arg.endsWith(":*")) {
    const prefix = arg.slice(0, -2);
    return detail === prefix || detail.startsWith(prefix + " ");
  }

  // Other glob form (space-prefix or embedded *): Bash(rm -rf *), Bash(git push --force*)
  if (arg.includes("*")) {
    return globToRegex(arg, { bash: true }).test(detail);
  }

  // Literal: Bash(cmd) matches exactly "cmd" (no args). Per rule-syntax.md:
  // "Bash(cmd) Matches cmd with NO arguments".
  return detail === arg;
}

// Path-tool arg matching (Read/Write/Edit/etc). Expand ~ on both sides so
// "Read(~/.ssh/**)" matches a detail like "/Users/m/.ssh/id_rsa".
function matchPathArg(arg, detail) {
  const pattern = expandHome(arg);
  const target = expandHome(detail);
  if (pattern.includes("*")) {
    return globToRegex(pattern).test(target);
  }
  return pattern === target;
}

export function matchRule(rule, entry) {
  if (!entry || typeof entry.tool !== "string") return false;

  const parsed = parseRule(rule);
  if (!parsed) return false;

  if (!toolMatches(parsed.tool, entry.tool)) return false;

  // Bare tool-name rule (no parens): tool match is sufficient.
  if (parsed.arg === null) return true;

  if (entry.tool === "Bash") {
    const detail = stripEnv(entry.detail);
    return matchBashArg(parsed.arg, detail);
  }

  return matchPathArg(parsed.arg, entry.detail ?? "");
}
