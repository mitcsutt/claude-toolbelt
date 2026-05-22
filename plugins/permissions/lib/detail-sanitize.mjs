const MAX_LEN = 200;
const HEREDOC_RE = /<<-?\s*'?([A-Z_][A-Z0-9_]*)'?[\s\S]*?\n\s*\1\s*$/gm;

export function sanitizeDetail(input) {
  if (!input) return "";
  let s = String(input);

  // Replace heredoc bodies with marker
  s = s.replace(HEREDOC_RE, (_m, tag) => `<<'${tag}' …`);

  const lines = s.split("\n");
  const first = lines.find((l) => l.trim().length > 0) ?? "";
  const extras = lines.length - 1;
  let collapsed = extras > 0 ? `${first} …(+${extras} lines)` : first;

  if (collapsed.length > MAX_LEN) {
    collapsed = collapsed.slice(0, MAX_LEN) + "…";
  }
  return collapsed;
}
