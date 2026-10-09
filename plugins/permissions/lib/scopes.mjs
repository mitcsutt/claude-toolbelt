// Locate and parse Claude Code settings scopes, then compute the effective
// rule set. Pure node, no deps. Never throws on bad input.
//
// Precedence (P3, https://code.claude.com/docs/en/settings#settings-precedence):
//   managed > projectLocal > projectShared > user; permission arrays concatenate.
// P4: project-scope defaultMode auto/bypassPermissions is ignored.
// Env VALUES are never stored: env/headers objects are redacted in `data`.

import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { platform } from "node:os";

const PRECEDENCE = ["managed", "projectLocal", "projectShared", "user"]; // high -> low
const PROJECT_SCOPES = new Set(["projectShared", "projectLocal"]);
const IGNORED_PROJECT_MODES = new Set(["auto", "bypassPermissions"]);

export function defaultManagedPaths() {
  if (platform() === "darwin") {
    return ["/Library/Application Support/ClaudeCode/managed-settings.json"];
  }
  if (platform() === "linux") return ["/etc/claude-code/managed-settings.json"];
  return [];
}

function redact(value, key) {
  if (Array.isArray(value)) return value.map((v) => redact(v));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (key === "env" || key === "headers") {
        out[k] = "[redacted]";
      } else {
        out[k] = redact(v, k);
      }
    }
    return out;
  }
  return value;
}

function readScope(name, file) {
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch (e) {
    if (e && e.code === "ENOENT") return { name, file, exists: false, data: null, error: null };
    return { name, file, exists: true, data: null, error: String(e && e.message) };
  }
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { name, file, exists: true, data: null, error: "settings root is not a JSON object" };
    }
    return { name, file, exists: true, data: redact(parsed), error: null };
  } catch (e) {
    return { name, file, exists: true, data: null, error: String(e && e.message) };
  }
}

function managedScopes(managedPaths) {
  const out = [];
  for (const p of managedPaths) {
    out.push(readScope("managed", p));
    const dir = join(dirname(p), "managed-settings.d");
    if (existsSync(dir)) {
      let names = [];
      try {
        names = readdirSync(dir).filter((n) => n.endsWith(".json")).sort();
      } catch {
        names = [];
      }
      for (const n of names) out.push(readScope("managed", join(dir, n)));
    }
  }
  return out;
}

function ruleList(data, key) {
  const arr = data && data.permissions && data.permissions[key];
  return Array.isArray(arr) ? arr.filter((r) => typeof r === "string") : [];
}

export function loadScopes({ cwd, home, managedPaths } = {}) {
  const paths = managedPaths ?? defaultManagedPaths();
  const userFile = join(home, ".claude", "settings.json");
  const sharedFile = join(cwd, ".claude", "settings.json");
  const localFile = join(cwd, ".claude", "settings.local.json");

  const scopes = [...managedScopes(paths)];
  if (scopes.length === 0) scopes.push({ name: "managed", file: null, exists: false, data: null, error: null });
  scopes.push(readScope("user", userFile));
  // cwd === home: projectShared would be the same file as user; do not double count.
  if (sharedFile === userFile) {
    scopes.push({ name: "projectShared", file: sharedFile, exists: false, data: null, error: null });
  } else {
    scopes.push(readScope("projectShared", sharedFile));
  }
  scopes.push(readScope("projectLocal", localFile));

  return { scopes, effective: computeEffective(scopes) };
}

export function computeEffective(scopes) {
  const eff = {
    allow: [],
    deny: [],
    ask: [],
    defaultMode: null,
    scalars: {},
    env: [],
    sandbox: { enabled: false, scope: null, raw: {} },
    additionalDirectories: [],
    flags: { skipDangerousModePermissionPrompt: null },
  };

  const live = scopes.filter((s) => s.data);
  for (const s of live) {
    for (const k of ["allow", "deny", "ask"]) {
      for (const rule of ruleList(s.data, k)) eff[k].push({ rule, scope: s.name, file: s.file });
    }
    if (s.data.env && typeof s.data.env === "object") {
      for (const key of Object.keys(s.data.env)) eff.env.push({ key, scope: s.name, file: s.file });
    }
    const dirs = [
      ...(Array.isArray(s.data.permissions?.additionalDirectories) ? s.data.permissions.additionalDirectories : []),
      ...(Array.isArray(s.data.additionalDirectories) ? s.data.additionalDirectories : []),
    ];
    for (const path of dirs) if (typeof path === "string") eff.additionalDirectories.push({ path, scope: s.name });
  }

  // Scalars + defaultMode: highest precedence wins.
  const byPrecedence = PRECEDENCE.flatMap((n) => live.filter((s) => s.name === n));
  const lowToHigh = [...byPrecedence].reverse();

  for (const s of byPrecedence) {
    const dm = s.data.permissions?.defaultMode ?? s.data.defaultMode;
    if (typeof dm === "string" && !(PROJECT_SCOPES.has(s.name) && IGNORED_PROJECT_MODES.has(dm))) {
      eff.defaultMode = { value: dm, scope: s.name };
      break;
    }
  }

  for (const s of lowToHigh) {
    for (const [k, v] of Object.entries(s.data)) {
      if (v === null || typeof v !== "object") eff.scalars[k] = { value: v, scope: s.name };
    }
    if (s.data.sandbox && typeof s.data.sandbox === "object") {
      eff.sandbox.raw = { ...eff.sandbox.raw, ...s.data.sandbox };
      if (typeof s.data.sandbox.enabled === "boolean") {
        eff.sandbox.enabled = s.data.sandbox.enabled;
        eff.sandbox.scope = s.name;
      }
    }
  }
  for (const s of byPrecedence) {
    if (typeof s.data.skipDangerousModePermissionPrompt === "boolean") {
      eff.flags.skipDangerousModePermissionPrompt = {
        value: s.data.skipDangerousModePermissionPrompt,
        scope: s.name,
      };
      break;
    }
  }
  return eff;
}
