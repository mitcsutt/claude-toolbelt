// Port of Claude Code permission rule matching semantics.
// Semantics follow https://code.claude.com/docs/en/permissions#permission-rule-syntax
//
// Single export: matchRule(rule, entry) -> boolean
//   rule:  string like "Bash(git:*)", "Read(~/.ssh/**)", "Read", "mcp__exa__*"
//   entry: { tool: string, detail: string }
//
// Used by: lib/checks.mjs, lib/scan.mjs and `perm-scan check`.
// Keep this contract stable.

import { homedir } from "node:os";

// Strip leading env-var assignments from a Bash detail.
//   "FOO=bar NODE_ENV=test git status" -> "git status"
// Per the permissions docs: "Claude extracts the first word of the command after
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
// Bash mode: `*` matches any characters including "/" per the permissions docs
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

  // Literal: Bash(cmd) matches exactly "cmd" (no args). Per the permissions docs:
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

export { parseRule };

// ---------------------------------------------------------------------------
// Compound-command handling (P5: https://code.claude.com/docs/en/permissions)
// ---------------------------------------------------------------------------

// Split a shell command on && || ; | |& & newline, respecting simple quotes.
// `2>&1`, `&>` and `>&` are redirects, not separators.
export function splitCompound(cmd) {
  const s = typeof cmd === "string" ? cmd : "";
  const out = [];
  let cur = "";
  let quote = null;
  const flush = () => {
    const t = cur.trim();
    if (t) out.push(t);
    cur = "";
  };
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quote) {
      cur += c;
      if (c === "\\" && quote === '"' && i + 1 < s.length) cur += s[++i];
      else if (c === quote) quote = null;
      continue;
    }
    if (c === "\\" && i + 1 < s.length) {
      cur += c + s[++i];
      continue;
    }
    if (c === "'" || c === '"') {
      quote = c;
      cur += c;
      continue;
    }
    if (c === "\n" || c === ";") {
      flush();
    } else if (c === "&") {
      const prev = s[i - 1];
      const next = s[i + 1];
      if (next === "&") {
        flush();
        i++;
      } else if (prev === ">" || prev === "<" || next === ">") {
        cur += c; // redirect
      } else {
        flush();
      }
    } else if (c === "|") {
      flush();
      if (s[i + 1] === "|" || s[i + 1] === "&") i++;
    } else {
      cur += c;
    }
  }
  flush();
  return out;
}

const ENV_ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S)*\s+/;
const WRAPPER_RES = [
  /^timeout\s+(?:-\S+\s+)*\d[\w.]*\s+/,
  /^time\s+/,
  /^nice\s+(?:-n\s*-?\d+\s+|-\d+\s+)?/,
  /^nohup\s+/,
  /^stdbuf(?:\s+-[ioe]\s+\S+|\s+-[ioe]\S+)*\s+/,
  /^command\s+/,
  /^builtin\s+/,
  /^noglob\s+/,
  /^xargs\s+(?!-)/,
];

// Remove leading env assignments and transparent wrappers, repeatedly.
export function stripWrappers(cmd) {
  let s = (typeof cmd === "string" ? cmd : "").trim();
  for (let guard = 0; guard < 50; guard++) {
    let next = s.replace(ENV_ASSIGN, "");
    if (next === s) {
      for (const re of WRAPPER_RES) {
        const m = re.exec(s);
        if (m && s.slice(m[0].length).trim() !== "") {
          next = s.slice(m[0].length);
          break;
        }
      }
    }
    next = next.trim();
    if (next === s) break;
    s = next;
  }
  return s;
}

const MULTI_COMMAND_TOOLS = new Set([
  "git", "gh", "npm", "pnpm", "yarn", "bun", "npx", "docker", "kubectl",
  "cargo", "go", "uv", "pip", "pip3", "brew", "terraform", "aws", "gcloud", "make",
]);

// First word of the (wrapper-stripped) first segment, plus the subcommand for
// multi-command tools when the second word is not a flag.
export function commandHead(cmd) {
  const first = splitCompound(cmd)[0] ?? "";
  const words = stripWrappers(first).split(/\s+/).filter(Boolean);
  if (words.length === 0) return "";
  if (MULTI_COMMAND_TOOLS.has(words[0]) && words[1] && !words[1].startsWith("-")) {
    return `${words[0]} ${words[1]}`;
  }
  return words[0];
}
