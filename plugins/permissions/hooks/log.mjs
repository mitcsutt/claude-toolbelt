import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname } from "node:path";
import { sanitizeDetail } from "../lib/detail-sanitize.mjs";

const LOG_PATH = `${process.env.HOME}/.claude/permission-log.jsonl`;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString("utf-8");

try {
  const input = JSON.parse(raw);
  const toolName = input.tool_name ?? "unknown";
  const toolInput = input.tool_input ?? {};

  let rawDetail = "";
  let sandboxDisabled;

  if (toolName === "Bash") {
    rawDetail = typeof toolInput.command === "string" ? toolInput.command : "";
    sandboxDisabled = toolInput.dangerouslyDisableSandbox === true;
  } else if (toolName === "Read" || toolName === "Write" || toolName === "Edit") {
    rawDetail = typeof toolInput.file_path === "string" ? toolInput.file_path : "";
  } else {
    rawDetail = Object.keys(toolInput).slice(0, 3).join(", ");
  }

  const detail = sanitizeDetail(rawDetail);
  const detailSha =
    rawDetail && rawDetail !== detail
      ? createHash("sha1").update(rawDetail).digest("hex").slice(0, 12)
      : undefined;

  const entry = {
    timestamp: new Date().toISOString(),
    session_id: input.session_id ?? "unknown",
    tool: toolName,
    detail,
    cwd: input.cwd ?? "",
  };
  if (detailSha) entry.detail_sha = detailSha;
  if (sandboxDisabled !== undefined) entry.sandbox_disabled = sandboxDisabled;

  const dir = dirname(LOG_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  appendFileSync(LOG_PATH, JSON.stringify(entry) + "\n");
} catch {
  // Logging must never block a hook — swallow all errors.
}
