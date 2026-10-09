// Finding catalogue (spec §4) as pure functions over loadScopes() output, plus
// decide() for advisor mode. Severity is fixed per id; no judgement calls.

import { matchRule, parseRule, splitCompound, stripWrappers } from "./rule-matcher.mjs";

const D = "https://code.claude.com/docs/en";
const DOC = {
  modes: `${D}/permission-modes#eliminate-prompts-with-auto-mode`,
  modesP4: `${D}/permission-modes`,
  perms: `${D}/permissions`,
  mcp: `${D}/permissions#mcp`,
  settings: `${D}/settings#settings-precedence`,
  settingsRef: `${D}/settings`,
  pluginEnv: `${D}/plugins-reference#environment-variables`,
};

const SEVERITY = {
  R1: "high", R2: "high", R3: "high", R4: "high", R5: "high",
  R6: "med", R7: "med", R8: "med", R9: "med", R10: "med",
  R11: "low", R12: "info",
  D1: "dead", D2: "dead", D3: "dead", D4: "dead", D5: "dead", D6: "dead", D7: "dead", D8: "dead",
  L2: "med", L3: "info",
};

const WRAPPERS = new Set(["command", "time", "timeout", "nice", "nohup", "stdbuf", "builtin", "noglob", "xargs"]);
const INTERPRETERS = new Set(["python", "python3", "node", "ruby", "perl", "deno", "bun", "php"]);
const SHELLS = new Set(["bash", "sh", "zsh"]);
const SECRET_KEY = /KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL/i;
const WRITE_VERBS = /(create|update|delete|post|send|comment|transition|run|execute|click|type|navigate|evaluate|fill|upload)/i;
const FILE_READERS = new Set(["cat", "head", "tail", "sed", "tee"]);

// ---- helpers --------------------------------------------------------------

function finding(id, entry, rule, why, extra = {}) {
  return {
    id,
    severity: extra.severity ?? SEVERITY[id],
    scope: entry.scope ?? null,
    file: entry.file ?? null,
    rule,
    why: why.length > 140 ? why.slice(0, 137) + "..." : why,
    doc: extra.doc,
  };
}

// Bash rule -> { body, wild, arg } or null (non-Bash or bare "Bash").
function bashRule(rule) {
  const p = parseRule(rule);
  if (!p || p.tool !== "Bash" || p.arg === null) return null;
  const arg = p.arg;
  let body = arg;
  let wild = false;
  if (arg.endsWith(":*")) {
    body = arg.slice(0, -2);
    wild = true;
  } else if (arg.endsWith(" *")) {
    body = arg.slice(0, -2);
    wild = true;
  } else if (arg.endsWith("*")) {
    body = arg.slice(0, -1);
    wild = true;
  }
  return { body: body.trim(), wild, arg };
}

const firstWord = (s) => (s.trim().split(/\s+/)[0] ?? "");

function isBareBash(rule) {
  const p = parseRule(rule);
  return !!p && p.tool === "Bash" && (p.arg === null || p.arg === "*" || p.arg === "");
}

// Server name from an MCP rule/tool name without parentheses; null if not a server-level rule.
function mcpServerWildcard(rule) {
  if (!rule.startsWith("mcp__") || rule.includes("(")) return null;
  let rest = rule.slice(5);
  if (rest.endsWith("__*")) rest = rest.slice(0, -3);
  if (!rest || rest.includes("__") || rest.includes("*")) return null;
  return rest;
}

function mcpServerOf(name) {
  if (!name.startsWith("mcp__")) return null;
  return name.slice(5).split("__")[0] || null;
}

const tokens = (s) => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

const WIDTH = { managed: 3, user: 2, projectShared: 1, projectLocal: 0 };

// Does rule B cover everything rule A matches (B != A)?
function covers(b, a) {
  const pb = parseRule(b);
  const pa = parseRule(a);
  if (!pb || !pa || pb.tool !== pa.tool) return false;
  if (pb.arg === null) return true;
  if (pa.arg === null) return false;
  if (pb.tool === "Bash") {
    const ba = bashRule(a);
    const bb = bashRule(b);
    if (!ba || !bb) return false;
    if (!bb.wild) return false;
    return matchRule(b, { tool: "Bash", detail: ba.body });
  }
  if (pa.arg.includes("*") && !pb.arg.includes("*")) return false;
  return matchRule(b, { tool: pa.tool, detail: pa.arg });
}

// ---- checks ---------------------------------------------------------------

export function runChecks(loaded, opts = {}) {
  const { seenTools, home = "", pluginDataDirs, selfDataDir } = opts;
  const eff = loaded.effective;
  const scopes = loaded.scopes ?? [];
  const out = [];
  const push = (f) => out.push(f);

  const allow = eff.allow;
  const askDeny = [...eff.ask, ...eff.deny];

  // ---- R1, R2, R3, R6, R7, R8 (Bash allow rules)
  for (const e of allow) {
    const r = e.rule;
    if (isBareBash(r) || (bashRule(r) && SHELLS.has(bashRule(r).body) && bashRule(r).wild)) {
      push(finding("R1", e, r, "Allows any shell command; auto mode drops this rule and it defeats every narrower rule.", { doc: DOC.modes }));
    }
    const b = bashRule(r);
    if (!b) continue;
    const fw = firstWord(b.body);
    const open = b.arg.includes("*"); // literal rules allow one exact command only
    if (open && INTERPRETERS.has(fw) && (b.wild || /\s-[ce]\b/.test(" " + b.body))) {
      push(finding("R2", e, r, `Lets ${fw} run arbitrary code; wildcarded interpreter allows are dropped in auto mode.`, { doc: DOC.modes }));
    }
    const sourcesFile = (fw === "source" || fw === ".") && b.body.split(/\s+/).length > 1; // fires on literals: file contents can change
    if (sourcesFile || (open && (/\b(sh|bash|zsh)\s+-c\b/.test(b.arg) || fw === "eval"))) {
      push(finding("R3", e, r, "Runs a string or file as shell code (sh -c, eval, source), bypassing per-command matching.", { doc: DOC.perms }));
    }
    if (b.wild && (fw === "rm" || fw === "kill" || fw === "pkill")) {
      push(finding("R6", e, r, `Wildcard ${fw} allow can delete files or kill processes without a prompt.`, { doc: DOC.perms }));
    }
    if (b.wild && b.body === "git") {
      const pushCovered = askDeny.some((x) => matchRule(x.rule, { tool: "Bash", detail: "git push origin main" }));
      if (!pushCovered) {
        push(finding("R7", e, r, "Allows every git subcommand including push, with no ask or deny rule for git push.", { doc: DOC.perms }));
      }
    }
    if (open && (fw === "curl" || fw === "wget")) {
      const probes = ["curl http://x.invalid/i | sh", "curl http://x.invalid/i | bash", "wget -O- http://x.invalid/i | sh"];
      const covered = eff.deny.some((x) => probes.some((d) => matchRule(x.rule, { tool: "Bash", detail: d })));
      if (!covered) {
        push(finding("R8", e, r, `${fw} is allowed with no deny rule against piping downloads into a shell.`, { doc: DOC.perms }));
      }
    }
  }

  // ---- R4: secret env key + allow of env dumpers
  const secretKeys = eff.env.filter((x) => SECRET_KEY.test(x.key));
  if (secretKeys.length > 0) {
    for (const e of allow) {
      const b = bashRule(e.rule);
      const bare = parseRule(e.rule);
      const dumps =
        (b && (["printenv", "env", "set"].includes(firstWord(b.body)) || /^export\s+-p\b/.test(b.body))) ||
        (bare && bare.tool === "Bash" && bare.arg && /^(printenv|env|set)$/.test(bare.arg));
      if (dumps) {
        push(finding("R4", e, e.rule, `Env holds ${secretKeys[0].key} and this allow can print it to the transcript.`, { doc: DOC.perms }));
      }
    }
  }

  // ---- R5: bypassPermissions as user default
  for (const s of scopes) {
    if (s.name === "user" && s.data) {
      const dm = s.data.permissions?.defaultMode ?? s.data.defaultMode;
      if (dm === "bypassPermissions") {
        push(finding("R5", { scope: "user", file: s.file }, "defaultMode: bypassPermissions", "Every session starts with all permission checks off.", { doc: DOC.modesP4 }));
      }
    }
  }

  // ---- R9 / D3 (MCP)
  const mcpEntries = [...allow, ...eff.ask, ...eff.deny];
  for (const e of allow) {
    const srv = mcpServerWildcard(e.rule);
    if (!srv) continue;
    if (Array.isArray(seenTools)) {
      const writer = seenTools.find((t) => mcpServerOf(t) === srv && WRITE_VERBS.test(t.slice(5 + srv.length + 2)));
      if (writer) {
        push(finding("R9", e, e.rule, `Wildcard covers write-capable tool ${writer}; allow only the read tools.`, { doc: DOC.mcp }));
      }
    } else {
      push(finding("R9", e, e.rule, "MCP server wildcard allows every tool, including any that write.", { severity: "low", doc: DOC.mcp }));
    }
  }
  if (Array.isArray(seenTools)) {
    const seenServers = new Set(seenTools.map(mcpServerOf).filter(Boolean));
    for (const e of mcpEntries) {
      if (!e.rule.startsWith("mcp__") || e.rule.includes("(")) continue;
      const srv = e.rule.slice(5).split("__")[0].replace(/\*$/, "");
      if (!srv || seenServers.has(srv)) continue;
      const st = tokens(srv);
      const lookalike = [...seenServers].find((o) => {
        const ot = tokens(o);
        return st.length > 0 && st.every((t) => ot.includes(t));
      });
      if (lookalike) {
        push(finding("D3", e, e.rule, `Server "${srv}" never seen, but "${lookalike}" was; the rule likely never matches.`, { doc: DOC.mcp }));
      }
    }
  }

  // ---- R10
  const homeNorm = home.replace(/\/+$/, "");
  const wideDir = (p) => {
    const q = p.replace(/\/+$/, "");
    return ["~", "$HOME", "~/.claude", "$HOME/.claude", homeNorm, `${homeNorm}/.claude`].filter(Boolean).includes(q);
  };
  const widePaths = eff.additionalDirectories.filter((d) => wideDir(d.path));
  if (widePaths.length > 0) {
    for (const e of allow) {
      if (e.rule === "Edit" || e.rule === "Write") {
        push(finding("R10", e, e.rule, `Unscoped ${e.rule} plus additionalDirectories ${widePaths[0].path} can rewrite settings and hooks.`, { doc: DOC.perms }));
      }
    }
  }

  // ---- R11
  for (const e of allow) {
    if (e.rule === "WebFetch") push(finding("R11", e, e.rule, "Bare WebFetch allows any domain; prefer WebFetch(domain:example.com).", { doc: DOC.perms }));
  }

  // ---- R12
  const skip = eff.flags?.skipDangerousModePermissionPrompt;
  if (skip && skip.value === true) {
    const s = scopes.find((x) => x.name === skip.scope && x.data);
    push(finding("R12", { scope: skip.scope, file: s?.file }, "skipDangerousModePermissionPrompt: true", "The bypass-mode warning is suppressed; fine for automation, risky on a daily machine.", { doc: DOC.perms }));
  }

  // ---- D1 (all lists)
  for (const list of ["allow", "ask", "deny"]) {
    for (const e of eff[list]) {
      const b = bashRule(e.rule);
      if (!b) continue;
      const fw = firstWord(b.body);
      if (WRAPPERS.has(fw) && (fw !== "xargs" || b.body === "xargs")) {
        push(finding("D1", e, e.rule, `"${fw}" is stripped before matching, so this ${list} rule never matches.`, { doc: DOC.perms }));
      }
    }
  }

  // ---- D2
  for (const list of ["allow", "ask", "deny"]) {
    for (const e of eff[list]) {
      if (e.rule.startsWith("mcp__") && e.rule.includes("(")) {
        push(finding("D2", e, e.rule, "MCP rules cannot take parentheses; Claude Code skips this rule.", { doc: DOC.mcp }));
      }
    }
  }
  for (const e of allow) {
    if (e.rule === "mcp__*") push(finding("D2", e, e.rule, "Allow mcp__* is skipped with a warning; name each server.", { doc: DOC.mcp }));
  }

  // ---- D4
  for (const s of scopes) {
    if (!s.data || (s.name !== "projectShared" && s.name !== "projectLocal")) continue;
    const dm = s.data.permissions?.defaultMode ?? s.data.defaultMode;
    if (dm === "auto" || dm === "bypassPermissions") {
      push(finding("D4", { scope: s.name, file: s.file }, `defaultMode: ${dm}`, "Project settings cannot set auto or bypass mode; Claude Code ignores this.", { doc: DOC.modesP4 }));
    }
    if (s.data.autoMode !== undefined) {
      push(finding("D4", { scope: s.name, file: s.file }, "autoMode", "autoMode is read only from user, managed or --settings; project value is ignored.", { doc: DOC.modesP4 }));
    }
  }

  // ---- D5 (dormant sandbox = info; permissions.sandbox = dead)
  for (const s of scopes) {
    if (!s.data) continue;
    const sb = s.data.sandbox;
    if (sb && typeof sb === "object" && Object.keys(sb).some((k) => k !== "enabled") && eff.sandbox.enabled !== true) {
      push(finding("D5", { scope: s.name, file: s.file }, "sandbox.*", "Sandbox settings are inert while sandbox.enabled is not true.", { severity: "info", doc: DOC.settingsRef }));
    }
    if (s.data.permissions && s.data.permissions.sandbox !== undefined) {
      push(finding("D5", { scope: s.name, file: s.file }, "permissions.sandbox", "Sandbox keys belong at top-level sandbox, not under permissions; this is ignored.", { doc: DOC.settingsRef }));
    }
  }

  // ---- D6
  for (const list of ["allow", "ask", "deny"]) {
    const seen = new Map();
    for (const e of eff[list]) {
      if (seen.has(e.rule)) {
        const first = seen.get(e.rule);
        push(finding("D6", e, e.rule, `Duplicate of the same ${list} rule in ${first.scope} scope.`, { doc: DOC.settings }));
      } else seen.set(e.rule, e);
    }
  }
  for (const a of allow) {
    const shadow = eff.deny.find((d) => d.rule !== a.rule && covers(d.rule, a.rule));
    if (shadow) {
      push(finding("D6", a, a.rule, `Shadowed by deny rule ${shadow.rule} (${shadow.scope}); deny always wins.`, { doc: DOC.settings }));
      continue;
    }
    const broader = allow.find((b) => b.rule !== a.rule && (WIDTH[b.scope] ?? 0) >= (WIDTH[a.scope] ?? 0) && covers(b.rule, a.rule));
    if (broader) {
      push(finding("D6", a, a.rule, `Already covered by broader allow ${broader.rule} (${broader.scope}).`, { doc: DOC.settings }));
    }
  }

  // ---- D7
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  for (const e of allow) {
    const r = e.rule;
    let reason = null;
    const tmp = r.match(/\/(?:private\/)?tmp\/[\w.\-/]*[\w.\-]/);
    if (/\bkill\s+(?:-\w+\s+)?\d+/.test(r)) reason = "a hard-coded PID";
    else if (tmp && r[tmp.index + tmp[0].length] !== "*") reason = "a specific /tmp path";
    else if (UUID.test(r)) reason = "a UUID";
    else if (/\/\.claude\/(?:projects|sessions|session-env|todos)\//.test(r)) reason = "a session path";
    else if (r.includes("\n")) reason = "a multi-line command";
    if (reason) push(finding("D7", e, r, `One-off rule containing ${reason}; it will never match again.`, { doc: DOC.perms }));
  }

  // ---- D8 (P6: Read/Edit denies already cover cat/head/tail/sed/tee)
  const fileDenies = eff.deny.filter((d) => /^(Read|Edit)\(/.test(d.rule));
  for (const e of eff.deny) {
    const b = bashRule(e.rule);
    if (!b) continue;
    const words = b.body.split(/\s+/);
    if (!FILE_READERS.has(words[0]) || words.length < 2) continue;
    const target = words[words.length - 1];
    if (!/^[~/.]/.test(target)) continue;
    const cover = fileDenies.find((d) =>
      matchRule(d.rule, { tool: parseRule(d.rule).tool, detail: target }),
    );
    if (cover) {
      push(finding("D8", e, e.rule, `Redundant: ${cover.rule} already blocks ${words[0]} on this path.`, { doc: DOC.perms }));
    }
  }

  // ---- L2
  const removed = /^Skill\((?:permissions:)?permissions-(?:audit|promote|lint|seed|bootstrap-project|sandbox-fix)(?::\*|\s\*)?\)$/;
  for (const s of scopes) {
    if (s.data && s.data.permissions && s.data.permissions.sandbox !== undefined) {
      push(finding("L2", { scope: s.name, file: s.file }, "permissions.sandbox", "Written by the removed sandbox-fix skill into the wrong key; move to top-level sandbox or delete.", { doc: DOC.settingsRef }));
    }
  }
  for (const list of ["allow", "ask", "deny"]) {
    for (const e of eff[list]) {
      if (removed.test(e.rule)) {
        push(finding("L2", e, e.rule, "Refers to a skill removed in permissions 3.0.", { doc: DOC.settingsRef }));
      } else if (list === "allow" && /\/plugins\/cache\/[^/]+\/permissions\//.test(e.rule)) {
        push(finding("L2", e, e.rule, "Points into an old permissions plugin cache directory that changes every version.", { doc: DOC.settingsRef }));
      }
    }
  }

  // ---- L3
  if (Array.isArray(pluginDataDirs)) {
    for (const name of pluginDataDirs) {
      if (/^(permissions|permission-advisor)-/.test(name) && name !== selfDataDir) {
        push(finding("L3", { scope: "user", file: `${homeNorm}/.claude/plugins/data/${name}` }, name, `Orphaned data dir from an earlier install; delete with rm -r ~/.claude/plugins/data/${name}`, { doc: DOC.pluginEnv }));
      }
    }
  }

  return out;
}

// ---- decide() ---------------------------------------------------------------

function firstMatch(list, entry) {
  for (const e of list) {
    if (matchRule(e.rule, entry)) return e;
  }
  return null;
}

// P6: Read/Edit rules also apply to cat/head/tail/sed/tee args and redirect targets.
function fileTargets(segment) {
  const words = segment.split(/\s+/).filter(Boolean);
  const reads = [];
  if (FILE_READERS.has(words[0])) {
    let skippedScript = words[0] !== "sed";
    for (const w of words.slice(1)) {
      if (w.startsWith("-")) continue;
      if (!skippedScript) {
        skippedScript = true;
        continue;
      }
      reads.push(w.replace(/^['"]|['"]$/g, ""));
    }
  }
  const writes = [];
  for (const m of segment.matchAll(/(?:^|[\s\d])>>?\s*([^\s>&|]+)/g)) writes.push(m[1].replace(/^['"]|['"]$/g, ""));
  if (words[0] === "tee") writes.push(...reads);
  return { reads, writes };
}

function matchBashSegment(list, segment) {
  const direct = firstMatch(list, { tool: "Bash", detail: segment });
  if (direct) return direct;
  const { reads, writes } = fileTargets(segment);
  for (const e of list) {
    const p = parseRule(e.rule);
    if (!p || p.arg === null) continue;
    if (p.tool === "Read" && reads.some((t) => matchRule(e.rule, { tool: "Read", detail: t }))) return e;
    if (p.tool === "Edit" && (writes.some((t) => matchRule(e.rule, { tool: "Edit", detail: t })) || (/^(sed|tee)$/.test(segment.split(/\s+/)[0]) && reads.some((t) => matchRule(e.rule, { tool: "Edit", detail: t }))))) return e;
  }
  return null;
}

export function decide(cmdOrToolCall, effective) {
  const call = typeof cmdOrToolCall === "string" ? { tool: "Bash", detail: cmdOrToolCall } : cmdOrToolCall;
  const hit = (decision, e) => ({ decision, rule: e.rule, scope: e.scope });
  if (!call || typeof call.tool !== "string") return { decision: "unmatched" };

  if (call.tool !== "Bash") {
    for (const [decision, list] of [["deny", effective.deny], ["ask", effective.ask], ["allow", effective.allow]]) {
      const e = firstMatch(list, call);
      if (e) return hit(decision, e);
    }
    return { decision: "unmatched" };
  }

  const segments = splitCompound(call.detail ?? "").map(stripWrappers).filter(Boolean);
  if (segments.length === 0) return { decision: "unmatched" };
  for (const [decision, list] of [["deny", effective.deny], ["ask", effective.ask]]) {
    for (const seg of segments) {
      const e = matchBashSegment(list, seg);
      if (e) return hit(decision, e);
    }
  }
  let first = null;
  for (const seg of segments) {
    const e = firstMatch(effective.allow, { tool: "Bash", detail: seg });
    if (!e) return { decision: "unmatched" };
    first = first ?? e;
  }
  return hit("allow", first);
}
