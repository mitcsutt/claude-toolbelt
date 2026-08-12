#!/usr/bin/env node
// SubagentStart: extend cutthroat's report discipline to subagents.
//
// The output style never reaches a subagent — styles ride the main loop's
// system prompt. Without this, subagents pad their final messages with
// preamble and restated instructions. That padding is a recurring cost on
// the PARENT: a subagent's final message lands in the main conversation as
// a tool result and is re-sent with every later API call in the session.
//
// No agent-type gating: a report is exactly what a read-only agent
// produces, so the discipline applies most there.

import { readFileSync } from "node:fs";

const BRIEF =
  "Your final message is consumed by the calling agent as a tool result, not read as chat: " +
  "return the findings themselves — data, file:line references, identifiers, verbatim error text — " +
  "in complete sentences, with no preamble, no restating of your instructions, and no offers of " +
  "further help. Do not compress the findings themselves; compress only the packaging around them. " +
  "Emit no text between tool calls: nobody reads it, so a progress update has no audience. " +
  "Chain the calls and put everything in that one final message.";

function main() {
  if (process.env.CUTTHROAT_SUBAGENT === "off") return;

  // Consume stdin per the hook contract. Content is not used, so any
  // read or parse failure is irrelevant — swallow it and still emit.
  try {
    readFileSync(0, "utf-8");
  } catch {
    /* fail-open */
  }

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "SubagentStart",
        additionalContext: BRIEF,
      },
    })
  );
}

try {
  main();
} catch {
  process.exit(0);
}
