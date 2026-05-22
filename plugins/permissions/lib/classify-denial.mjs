import { matchSignature } from "./signatures.mjs";

const PATH_RE = /'([^']+)'/;

function commandHead(cmd) {
  const stripped = (cmd ?? "").replace(/^([A-Z_]+=[^\s]+\s+)+/, "");
  return stripped.split(/\s+/)[0] ?? "";
}

export function classifyDenial({ command, stderr, excludedCommands = [] }) {
  const sig = matchSignature(stderr);
  if (!sig || sig.suppress) return null;

  let matched_path = null;
  const m = stderr.match(PATH_RE);
  if (m) matched_path = m[1];

  let category = sig.category;
  const head = commandHead(command);
  if (excludedCommands.includes(head)) category = "macos-posix";

  return {
    signature: sig.id,
    category,
    fix: sig.fix,
    matched_path,
    command_head: head,
  };
}
