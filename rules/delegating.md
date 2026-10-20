# Delegating to subagents

These apply to every delegation, from the main session or from inside a
subagent. They shape how work is split, not which model or agent does it.

- **Brief = objective, output (format + file path), tools/sources,
  boundaries.** Boundaries always say whether the worker may spawn its own
  subagents; default is no. *Reason: workers act on inherited instructions
  literally, so a brief that doesn't pin the role can be read as licence to
  re-run the whole fan-out.*
- **Fetch shared data once.** When parallel workers need the same dataset (CI
  history, logs, search results, API pages), fetch it once to a file, then give
  each worker the path and a disjoint slice. Never have N workers each pull the
  full set and filter it.
- **Bulk data goes to a file, not into context.** Any pull that paginates or
  needs more than ~20 tool calls: save results to a file, extract with `jq` or
  a script, and read only the extract. *Reason: every result left in context
  is re-read on every later turn.*
- **Fan-out uses fresh subagents, not forks.** A fork copies the whole
  conversation, including any fan-out plan in it, and re-reads all of it every
  turn.
