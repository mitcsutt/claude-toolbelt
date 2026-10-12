// Port of Claude Code permission rule matching semantics.
// Semantics follow https://code.claude.com/docs/en/permissions#permission-rule-syntax
//
// Main export: matchRule(rule, entry) -> boolean
//   rule:  string like "Bash(git:*)", "Read(~/.ssh/**)", "Read", "mcp__exa__*"
//   entry: { tool, detail, anyEnv?, cwd?, home?, root? }
//     anyEnv: strip ANY leading VAR=val before matching (deny/ask semantics).
//             Default strips only known-safe vars (allow semantics).
//     cwd/home: path-rule resolution context (default process.cwd(), os.homedir()).
//     root: anchor for "/path" rules: project settings -> cwd; user settings -> ~/.claude.
//
// Used by: lib/checks.mjs, lib/scan.mjs and `perm-scan check`.
// Keep this contract stable.

import { homedir } from "node:os";
import { posix } from "node:path";

// ---------------------------------------------------------------------------
// Env assignments
// ---------------------------------------------------------------------------

// Known-safe leading environment assignments. The permissions docs say Claude
// Code "strips a leading assignment of certain known-safe environment variables"
// and that "an allow rule won't match past an assignment of any other variable.
// A deny or ask rule matches past any leading assignment." The docs do not
// publish the list (only NODE_ENV is named as an example), so this is a
// deliberately conservative guess: display/locale/CI/log-level knobs that cannot
// change which program runs. Anything else (PATH, LD_*, PYTHON*, NODE_OPTIONS,
// HOME, ...) is NOT stripped on the allow side.
const SAFE_ENV = new Set([
  "LANG", "LANGUAGE", "LC_ALL", "TZ", "NO_COLOR", "FORCE_COLOR", "CI", "DEBUG",
  "NODE_ENV", "RUST_LOG", "TERM",
]);

export function isSafeEnvName(name) {
  return SAFE_ENV.has(name) || /^LC_[A-Z_]+$/.test(name);
}

// ---- low-level shell scanning helpers --------------------------------------

// i = index just after the opening backtick; returns index of the closing one or -1.
function skipBacktick(s, i) {
  for (; i < s.length; i++) {
    if (s[i] === "\\") i++;
    else if (s[i] === "`") return i;
  }
  return -1;
}

// i = index just after an opening double quote; returns the index AFTER the
// closing quote, or -1. Understands \ escapes, $( ) and backticks inside.
function skipDouble(s, i) {
  for (; i < s.length; i++) {
    const c = s[i];
    if (c === "\\") i++;
    else if (c === '"') return i + 1;
    else if (c === "$" && s[i + 1] === "(") {
      const j = skipParen(s, i + 2);
      if (j < 0) return -1;
      i = j;
    } else if (c === "`") {
      const j = skipBacktick(s, i + 1);
      if (j < 0) return -1;
      i = j;
    }
  }
  return -1;
}

// Heredoc operator at s[i] ("<<", not "<<<"): { delim, quoted, dash, end } or null.
function parseHeredocOp(s, i) {
  if (s[i] !== "<" || s[i + 1] !== "<" || s[i + 2] === "<") return null;
  let j = i + 2;
  const dash = s[j] === "-";
  if (dash) j++;
  while (s[j] === " " || s[j] === "\t") j++;
  let quoted = false;
  let delim = "";
  if (s[j] === "'" || s[j] === '"') {
    const q = s[j];
    const k = s.indexOf(q, j + 1);
    if (k < 0) return null;
    delim = s.slice(j + 1, k);
    quoted = true;
    j = k + 1;
  } else {
    if (s[j] === "\\") {
      quoted = true;
      j++;
    }
    const m = /^[^\s;&|<>()'"]+/.exec(s.slice(j));
    if (!m) return null;
    delim = m[0];
    j += delim.length;
  }
  return { delim, quoted, dash, end: j };
}

// i = index just after the newline that ends the line holding the heredoc
// operator(s). Consumes one body per pending op. Returns { next, bodies }.
function skipHeredocBodies(s, i, pending) {
  const bodies = [];
  for (const op of pending) {
    let body = "";
    let found = false;
    while (i < s.length) {
      let e = s.indexOf("\n", i);
      if (e < 0) e = s.length;
      const line = s.slice(i, e);
      i = Math.min(e + 1, s.length);
      const cmp = op.dash ? line.replace(/^\t+/, "") : line;
      if (cmp === op.delim) {
        found = true;
        break;
      }
      body += line + "\n";
    }
    bodies.push({ body, quoted: op.quoted, found });
  }
  return { next: i, bodies };
}

// i = index just after an opening "(" (of $( ( <( >( ). Returns the index of the
// matching ")" or -1.
function skipParen(s, i, depthGuard = 0) {
  if (depthGuard > 20) return -1;
  let depth = 1;
  const pending = [];
  for (; i < s.length; i++) {
    const c = s[i];
    if (c === "\\") i++;
    else if (c === "'") {
      const j = s.indexOf("'", i + 1);
      if (j < 0) return -1;
      i = j;
    } else if (c === '"') {
      const j = skipDouble(s, i + 1);
      if (j < 0) return -1;
      i = j - 1;
    } else if (c === "`") {
      const j = skipBacktick(s, i + 1);
      if (j < 0) return -1;
      i = j;
    } else if (c === "<" && s[i + 1] === "<" && s[i + 2] !== "<") {
      const op = parseHeredocOp(s, i);
      if (op) {
        pending.push(op);
        i = op.end - 1;
      }
    } else if (c === "\n" && pending.length) {
      const r = skipHeredocBodies(s, i + 1, pending);
      pending.length = 0;
      i = r.next - 1;
    } else if (c === "(") depth++;
    else if (c === ")") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// Leading `NAME=value` at the start of s, value consumed whole (quotes, $( ),
// backticks, escapes). Returns { name, value, rest } where rest is the text after
// the following whitespace, or null when the assignment is the whole string.
// Returns null when s does not start with an assignment or is unparseable.
function readEnvAssign(s) {
  const m = /^([A-Za-z_][A-Za-z0-9_]*)=/.exec(s);
  if (!m) return null;
  let i = m[0].length;
  const start = i;
  while (i < s.length) {
    const c = s[i];
    if (/\s/.test(c)) break;
    if (c === "\\") {
      i += 2;
    } else if (c === "'") {
      const j = s.indexOf("'", i + 1);
      if (j < 0) return null;
      i = j + 1;
    } else if (c === '"') {
      const j = skipDouble(s, i + 1);
      if (j < 0) return null;
      i = j;
    } else if (c === "`") {
      const j = skipBacktick(s, i + 1);
      if (j < 0) return null;
      i = j + 1;
    } else if (c === "$" && s[i + 1] === "(") {
      const j = skipParen(s, i + 2);
      if (j < 0) return null;
      i = j + 1;
    } else if (";&|<>()".includes(c)) {
      break;
    } else i++;
  }
  const value = s.slice(start, Math.min(i, s.length));
  if (i >= s.length) return { name: m[1], value, rest: null };
  if (!/\s/.test(s[i])) return { name: m[1], value, rest: null }; // followed by an operator
  return { name: m[1], value, rest: s.slice(i).replace(/^\s+/, "") };
}

// A safe assignment must also have a plain value: FOO=$(x) executes x.
function assignIsSafe(a) {
  return isSafeEnvName(a.name) && !/[$`]/.test(a.value);
}

function stripEnv(detail, anyEnv) {
  let s = (detail ?? "").replace(/^\s+/, "");
  for (let guard = 0; guard < 50; guard++) {
    const a = readEnvAssign(s);
    if (!a || a.rest == null || a.rest === "") break;
    if (!anyEnv && !assignIsSafe(a)) break;
    s = a.rest;
  }
  return s;
}

// ---------------------------------------------------------------------------
// Glob helpers
// ---------------------------------------------------------------------------

// Bash mode: `*` matches any characters including "/" and spaces.
// Path mode: `**/` = zero or more dirs, `/**` = everything below, `*` = one level.
function globToRegex(glob, { bash = false } = {}) {
  let out = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      if (!bash && glob[i + 2] === "/") {
        out += "(?:.*/)?";
        i += 2;
      } else {
        out += ".*";
        i++;
      }
    } else if (c === "*") {
      out += bash ? ".*" : "[^/]*";
    } else if ("[](){}.+^$|\\?".includes(c)) {
      out += "\\" + c;
    } else {
      out += c;
    }
  }
  return new RegExp("^" + out + "$", "s");
}

// ---------------------------------------------------------------------------
// Rule parsing and tool matching
// ---------------------------------------------------------------------------

//   "Bash(git:*)"            -> { tool: "Bash", arg: "git:*" }
//   "Read"                   -> { tool: "Read", arg: null }
// Returns null for malformed input.
function parseRule(rule) {
  if (typeof rule !== "string" || rule.length === 0) return null;
  const parenIdx = rule.indexOf("(");
  if (parenIdx === -1) return { tool: rule, arg: null };
  if (!rule.endsWith(")")) return null;
  const tool = rule.slice(0, parenIdx);
  const arg = rule.slice(parenIdx + 1, -1);
  if (tool.length === 0) return null;
  return { tool, arg };
}

// Edit rules cover the other file-writing tools (the docs apply one rule to all
// built-in tools that edit files), and Write(...) rules are the same thing.
const EDIT_FAMILY = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"]);
const normTool = (t) => (EDIT_FAMILY.has(t) ? "Edit" : t);

function toolMatches(ruleTool, entryTool) {
  if (ruleTool.includes("*")) return globToRegex(ruleTool).test(entryTool);
  if (normTool(ruleTool) === normTool(entryTool)) return true;
  // "mcp__puppeteer matches any tool provided by the puppeteer server"
  if (ruleTool.startsWith("mcp__")) {
    const rest = ruleTool.slice(5);
    if (rest && !rest.includes("__")) return entryTool.startsWith(ruleTool + "__");
  }
  return false;
}

// Bash-specific arg matching.
function matchBashArg(arg, detail) {
  if (arg.endsWith(":*")) {
    const prefix = arg.slice(0, -2);
    return detail === prefix || detail.startsWith(prefix + " ");
  }
  if (arg.includes("*")) {
    if (globToRegex(arg, { bash: true }).test(detail)) return true;
    // "A * at the end, with a space before it, also matches the bare command"
    // -- only when that trailing " *" is the rule's only wildcard.
    if (arg.endsWith(" *")) {
      const prefix = arg.slice(0, -2);
      if (!prefix.includes("*") && detail === prefix) return true;
    }
    return false;
  }
  return detail === arg;
}

// ---- path rules --------------------------------------------------------------
// Docs (permissions#read-and-edit): `//path` absolute; `~/path` home; `/path`
// relative to the settings source (project/local settings: the primary working
// directory; user settings: ~/.claude); `path` and `./path` relative to cwd.
// Patterns without a slash (`*.env`, `.env`) match at any depth below cwd.

function pathCtx(entry) {
  const cwd = (entry && entry.cwd) || process.cwd();
  const home = (entry && entry.home) || homedir();
  const root = (entry && entry.root) || cwd;
  return { cwd: cwd.replace(/\/+$/, "") || "/", home: home.replace(/\/+$/, ""), root: root.replace(/\/+$/, "") };
}

// Resolve a rule argument to { glob } (absolute glob) or { anyDepth }.
export function resolvePathPattern(arg, ctx) {
  const p = arg ?? "";
  if (p.startsWith("//")) return { glob: p.slice(1) };
  if (p === "~" || p.startsWith("~/")) return { glob: ctx.home + p.slice(1) };
  if (p.startsWith("/")) return { glob: ctx.root + p };
  if (p.startsWith("./")) return { glob: posix.normalize(ctx.cwd + "/" + p.slice(2)) };
  if (!p.includes("/")) return { anyDepth: p };
  return { glob: posix.normalize(ctx.cwd + "/" + p) };
}

function resolveDetail(detail, ctx) {
  let d = detail ?? "";
  if (d === "~" || d.startsWith("~/")) d = ctx.home + d.slice(1);
  if (!d.startsWith("/")) d = posix.normalize(ctx.cwd + "/" + d);
  return d;
}

function matchPathArg(arg, detail, entry) {
  const ctx = pathCtx(entry);
  const target = resolveDetail(detail, ctx);
  const pat = resolvePathPattern(arg, ctx);
  if (pat.anyDepth !== undefined) {
    return globToRegex(`${ctx.cwd === "/" ? "" : ctx.cwd}/**/${pat.anyDepth}`).test(target);
  }
  return globToRegex(pat.glob).test(target);
}

export function matchRule(rule, entry) {
  if (!entry || typeof entry.tool !== "string") return false;
  const parsed = parseRule(rule);
  if (!parsed) return false;
  if (!toolMatches(parsed.tool, entry.tool)) return false;
  if (parsed.arg === null) return true;
  if (entry.tool === "Bash") {
    return matchBashArg(parsed.arg, stripEnv(entry.detail, entry.anyEnv));
  }
  if (parsed.arg === "") return false;
  return matchPathArg(parsed.arg, entry.detail ?? "", entry);
}

export { parseRule };

// ---------------------------------------------------------------------------
// Compound-command handling (https://code.claude.com/docs/en/permissions)
// ---------------------------------------------------------------------------

// Core splitter. Splits on && || ; | |& & newline at top level only: never inside
// quotes, $( ), ( ), backticks, comments or heredoc bodies. `2>&1`, `&>`, `>&`
// and `>|` are redirects. Heredoc bodies are removed from the segments; bodies
// of unquoted heredocs go to `heredocs` (they can contain $( ) expansions).
function splitInternal(s) {
  const segs = [];
  const heredocs = [];
  let unsure = false;
  let cur = "";
  const pending = [];
  const flush = () => {
    const t = cur.trim();
    if (t) segs.push(t);
    cur = "";
  };
  const take = (j) => {
    // j = index of the closing delimiter, or -1 when unterminated
    return j;
  };
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === "\\") {
      if (s[i + 1] === "\n") {
        i++;
        cur += " ";
      } else if (i + 1 < s.length) {
        cur += c + s[++i];
      } else cur += c;
      continue;
    }
    if (c === "'") {
      const j = s.indexOf("'", i + 1);
      if (j < 0) {
        unsure = true;
        cur += s.slice(i);
        break;
      }
      cur += s.slice(i, j + 1);
      i = j;
      continue;
    }
    if (c === '"') {
      const j = skipDouble(s, i + 1);
      if (j < 0) {
        unsure = true;
        cur += s.slice(i);
        break;
      }
      cur += s.slice(i, j);
      i = j - 1;
      continue;
    }
    if (c === "`") {
      const j = take(skipBacktick(s, i + 1));
      if (j < 0) {
        unsure = true;
        cur += s.slice(i);
        break;
      }
      cur += s.slice(i, j + 1);
      i = j;
      continue;
    }
    if ((c === "$" || c === "<" || c === ">") && s[i + 1] === "(") {
      const j = skipParen(s, i + 2);
      if (j < 0) {
        unsure = true;
        cur += s.slice(i);
        break;
      }
      cur += s.slice(i, j + 1);
      i = j;
      continue;
    }
    if (c === "(") {
      const j = skipParen(s, i + 1);
      if (j < 0) {
        unsure = true;
        cur += s.slice(i);
        break;
      }
      cur += s.slice(i, j + 1);
      i = j;
      continue;
    }
    if (c === "<" && s[i + 1] === "<" && s[i + 2] !== "<") {
      const op = parseHeredocOp(s, i);
      if (op) {
        pending.push(op);
        cur += s.slice(i, op.end);
        i = op.end - 1;
        continue;
      }
    }
    if (c === "#" && (cur === "" || /\s/.test(cur[cur.length - 1]))) {
      const j = s.indexOf("\n", i);
      i = j < 0 ? s.length : j - 1;
      continue;
    }
    if (c === "\n") {
      if (pending.length) {
        const r = skipHeredocBodies(s, i + 1, pending);
        for (const b of r.bodies) {
          if (!b.found) unsure = true;
          if (!b.quoted) heredocs.push(b.body);
        }
        pending.length = 0;
        i = r.next - 1;
      }
      flush();
      continue;
    }
    if (c === ";") {
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
      if (s[i - 1] === ">") {
        cur += c; // >| clobber redirect
      } else {
        flush();
        if (s[i + 1] === "|" || s[i + 1] === "&") i++;
      }
    } else {
      cur += c;
    }
  }
  flush();
  return { segs, heredocs, unsure };
}

export function splitCompound(cmd) {
  return splitInternal(typeof cmd === "string" ? cmd : "").segs;
}

// Commands nested inside a segment: $( ), backticks, <( ), >( ), and a leading
// ( ) subshell. Returns { inner: string[], unsure }.
export function nestedCommands(seg, { asText = false } = {}) {
  const inner = [];
  let unsure = false;
  const s = seg;
  if (!asText && s.startsWith("(")) {
    const j = skipParen(s, 1);
    if (j < 0) unsure = true;
    else inner.push(s.slice(1, j));
  }
  const scan = (from, dq) => {
    for (let i = from; i < s.length; i++) {
      const c = s[i];
      if (c === "\\") {
        i++;
      } else if (!dq && !asText && c === "'") {
        const j = s.indexOf("'", i + 1);
        if (j < 0) {
          unsure = true;
          return s.length;
        }
        i = j;
      } else if (!dq && !asText && c === '"') {
        i = scan(i + 1, true);
      } else if (dq && c === '"') {
        return i;
      } else if (c === "`") {
        const j = skipBacktick(s, i + 1);
        if (j < 0) {
          unsure = true;
          return s.length;
        }
        inner.push(s.slice(i + 1, j));
        i = j;
      } else if ((c === "$" || (!dq && (c === "<" || c === ">"))) && s[i + 1] === "(") {
        const j = skipParen(s, i + 2);
        if (j < 0) {
          unsure = true;
          return s.length;
        }
        inner.push(s.slice(i + 2, j));
        i = j;
      }
    }
    return s.length;
  };
  scan(0, false);
  return { inner, unsure };
}

// `timeout [opts] DURATION cmd…` parsed token by token: the equivalent regex had
// overlapping option alternatives and backtracked exponentially (CodeQL js/redos).
const TIMEOUT_ARG_OPTS = new Set(["-s", "-k", "--signal", "--kill-after"]);
const timeoutWrapper = {
  exec(s) {
    const m = /^timeout\s+/.exec(s);
    if (!m) return null;
    let i = m[0].length;
    for (;;) {
      const tok = /^(\S+)(\s+)/.exec(s.slice(i));
      if (!tok) return null;
      if (TIMEOUT_ARG_OPTS.has(tok[1])) {
        i += tok[0].length;
        const arg = /^\S+\s+/.exec(s.slice(i));
        if (!arg) return null;
        i += arg[0].length;
      } else if (tok[1].startsWith("-")) {
        i += tok[0].length;
      } else if (/^\d[\w.]*$/.test(tok[1])) {
        return [s.slice(0, i + tok[0].length)];
      } else {
        return null;
      }
    }
  },
};

const WRAPPER_RES = [
  timeoutWrapper,
  /^time\s+/,
  /^nice\s+(?:-n\s*-?\d+\s+|-\d+\s+)?/,
  /^nohup\s+/,
  /^stdbuf(?:\s+-[ioe]\s+\S+|\s+-[ioe]\S+)*\s+/,
  /^command\s+(?!-[vV]\b)/,
  /^builtin\s+/,
  /^noglob\s+/,
  /^xargs\s+(?!-)/,
];

// Remove leading env assignments and transparent wrappers, repeatedly.
// Default strips ANY leading assignment (deny/ask semantics and head extraction).
// { forAllow: true } strips only known-safe assignments (allow semantics).
export function stripWrappers(cmd, opts = {}) {
  const forAllow = !!opts.forAllow;
  let s = (typeof cmd === "string" ? cmd : "").trim();
  for (let guard = 0; guard < 50; guard++) {
    let next = s;
    const a = readEnvAssign(s);
    if (a) {
      if (a.rest != null && a.rest !== "" && (!forAllow || assignIsSafe(a))) next = a.rest;
    } else {
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

const CONTROL_PREFIX = /^(?:do|then|else|elif|if|while|until|!|\{)\s+/;
const CONTROL_ONLY = /^(?:done|fi|esac|\}|do|then|else)$/;
const CONTROL_HEADER = /^(?:for|select|case|function)\b/;

// Peel control-flow keywords so `do rm x` -> `rm x`. Returns { text, control }
// where text is null for pure keyword/header segments.
export function stripControl(seg) {
  let t = seg.trim();
  let control = false;
  for (let g = 0; g < 10; g++) {
    const n = t.replace(CONTROL_PREFIX, "");
    if (n === t) break;
    t = n.trim();
    control = true;
  }
  if (CONTROL_ONLY.test(t) || CONTROL_HEADER.test(t)) return { text: null, control: true };
  return { text: t, control };
}

const unquote = (s) => {
  const t = s.trim();
  const m = /^(['"])([\s\S]*)\1$/.exec(t);
  return m ? m[2] : t;
};

// Flatten a command into every simple command the permission system would see,
// including those nested in substitutions, subshells, control-flow bodies,
// `eval` and `sh -c` strings. `unsure` is true whenever any such construct (or a
// parse failure) means a static answer cannot be fully trusted.
export function analyzeCommand(cmd, depth = 0) {
  const out = { items: [], unsure: false, reasons: [] };
  const mark = (r) => {
    out.unsure = true;
    if (!out.reasons.includes(r)) out.reasons.push(r);
  };
  if (depth > 4) {
    mark("nesting too deep");
    return out;
  }
  const sp = splitInternal(typeof cmd === "string" ? cmd : "");
  if (sp.unsure) mark("unbalanced quote or substitution");
  const addInner = (list) => {
    for (const c of list) {
      const sub = analyzeCommand(c, depth + 1);
      out.items.push(...sub.items);
      if (sub.unsure) mark(sub.reasons[0] ?? "nested command");
    }
  };
  for (const body of sp.heredocs) {
    const n = nestedCommands(body, { asText: true });
    if (n.unsure) mark("unbalanced substitution");
    if (n.inner.length) {
      mark("command substitution");
      addInner(n.inner);
    }
  }
  for (const raw of sp.segs) {
    const n = nestedCommands(raw);
    if (n.unsure) mark("unbalanced quote or substitution");
    if (n.inner.length) {
      mark(raw.startsWith("(") ? "subshell" : "command substitution");
      addInner(n.inner);
    }
    const { text, control } = stripControl(raw);
    if (control) mark("control flow");
    if (text === null) continue;
    if (!raw.startsWith("(")) out.items.push(text);
    const stripped = stripWrappers(text);
    let m = /^eval\s+([\s\S]+)$/.exec(stripped);
    if (!m) m = /^(?:sh|bash|zsh|dash)\s+(?:-\w+\s+)*-\w*c\w*\s+([\s\S]+)$/.exec(stripped);
    if (m) {
      mark("eval or shell -c string");
      addInner([unquote(m[1])]);
    }
  }
  return out;
}

const MULTI_COMMAND_TOOLS = new Set([
  "git", "gh", "gh-axi", "npm", "pnpm", "yarn", "bun", "npx", "docker", "kubectl",
  "cargo", "go", "uv", "pip", "pip3", "brew", "terraform", "aws", "gcloud", "make",
]);

// Heads that only set the stage for the real command.
const SKIP_HEADS = new Set([
  "cd", "pushd", "popd", "export", "unset", "set", "true", "false", ":", "mkdir",
  "test", "[", "[[", "local", "declare", "readonly", "wait", "exit",
]);

// First word of the first "real" command (skipping cd/export/VAR=… setup
// segments), plus the subcommand for multi-command tools when the second word is
// not a flag.
export function commandHead(cmd) {
  const segs = splitCompound(cmd);
  const heads = [];
  for (const seg of segs) {
    const { text } = stripControl(seg);
    if (!text) continue;
    const s = stripWrappers(text);
    if (!s) continue;
    const a = readEnvAssign(s);
    if (a && a.rest == null) continue; // bare assignment
    const words = s.split(/\s+/).filter(Boolean);
    if (words.length === 0) continue;
    heads.push(words);
  }
  const pick = heads.find((w) => !SKIP_HEADS.has(w[0])) ?? heads[0];
  if (!pick) return "";
  if (MULTI_COMMAND_TOOLS.has(pick[0]) && pick[1] && !pick[1].startsWith("-")) {
    return `${pick[0]} ${pick[1]}`;
  }
  return pick[0];
}
