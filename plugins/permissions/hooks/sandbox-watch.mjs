// plugins/permissions/hooks/sandbox-watch.mjs
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import { classifyDenial } from "../lib/classify-denial.mjs";
import { sanitizeDetail } from "../lib/detail-sanitize.mjs";

const LOG_PATH = `${process.env.HOME}/.claude/sandbox-denials.jsonl`;
const SETTINGS_PATH = `${process.env.HOME}/.claude/settings.json`;

function loadExcludedCommands() {
  try {
    const s = JSON.parse(readFileSync(SETTINGS_PATH, "utf-8"));
    return s?.sandbox?.excludedCommands ?? [];
  } catch {
    return [];
  }
}

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString("utf-8");

try {
  const input = JSON.parse(raw);
  if (input.tool_name !== "Bash") process.exit(0);

  const command = input.tool_input?.command ?? "";
  const result = input.tool_response ?? input.tool_result ?? {};
  const output = String(result.stderr ?? "") + "\n" + String(result.stdout ?? "");

  const denial = classifyDenial({
    command,
    stderr: output,
    excludedCommands: loadExcludedCommands(),
  });
  if (!denial) process.exit(0);

  const entry = {
    timestamp: new Date().toISOString(),
    session_id: input.session_id ?? "unknown",
    cwd: input.cwd ?? "",
    command: sanitizeDetail(command),
    sandbox_disabled: input.tool_input?.dangerouslyDisableSandbox === true,
    ...denial,
  };

  const dir = dirname(LOG_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  appendFileSync(LOG_PATH, JSON.stringify(entry) + "\n");
} catch {
  // Never block on logging failure.
}
