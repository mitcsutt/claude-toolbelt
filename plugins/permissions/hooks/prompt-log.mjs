// plugins/permissions/hooks/prompt-log.mjs
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const LOG_PATH = `${process.env.HOME}/.claude/prompt-log.jsonl`;
const EXCERPT_LEN = 200;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString("utf-8");

try {
  const input = JSON.parse(raw);
  const prompt = String(input.user_prompt ?? "");
  const excerpt = prompt.length > EXCERPT_LEN
    ? prompt.slice(0, EXCERPT_LEN) + "…"
    : prompt;

  const entry = {
    timestamp: new Date().toISOString(),
    session_id: input.session_id ?? "unknown",
    cwd: input.cwd ?? "",
    prompt_excerpt: excerpt,
    prompt_len: prompt.length,
  };

  const dir = dirname(LOG_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  appendFileSync(LOG_PATH, JSON.stringify(entry) + "\n");
} catch {
  // Logging must not block.
}
