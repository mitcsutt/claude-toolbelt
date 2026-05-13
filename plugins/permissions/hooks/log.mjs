import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const LOG_PATH = `${process.env.HOME}/.claude/permission-log.jsonl`;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString("utf-8");

try {
  const input = JSON.parse(raw);
  const toolName = input.tool_name ?? "unknown";
  const toolInput = input.tool_input ?? {};

  let detail = "";
  let sandboxDisabled;

  if (toolName === "Bash") {
    detail = typeof toolInput.command === "string" ? toolInput.command : "";
    sandboxDisabled = toolInput.dangerouslyDisableSandbox === true;
  } else if (toolName === "Read" || toolName === "Write" || toolName === "Edit") {
    detail = typeof toolInput.file_path === "string" ? toolInput.file_path : "";
  } else {
    detail = Object.keys(toolInput).slice(0, 3).join(", ");
  }

  const entry = {
    timestamp: new Date().toISOString(),
    session_id: input.session_id ?? "unknown",
    tool: toolName,
    detail,
    cwd: input.cwd ?? "",
  };
  if (sandboxDisabled !== undefined) entry.sandbox_disabled = sandboxDisabled;

  const dir = dirname(LOG_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  appendFileSync(LOG_PATH, JSON.stringify(entry) + "\n");
} catch {
  // Logging must never block a hook — swallow all errors.
}
