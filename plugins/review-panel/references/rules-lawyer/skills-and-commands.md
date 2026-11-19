# Skills and slash commands

Platform facts were checked against Claude Code 2.1.328 (code.claude.com/docs/en/skills), the Agent Skills spec (agentskills.io) and Anthropic's skill best practices on 2026-11-19. Before a finding depends on one, re-fetch the page.

## How Claude Code treats them

- **Frontmatter** (2.1.328):
  - `name`, `description`, `when_to_use`, `argument-hint`, `arguments`;
  - `disable-model-invocation`, `user-invocable`;
  - `allowed-tools` and `disallowed-tools`, both lasting only for the turn;
  - `model`, `effort`;
  - `context: fork`, `agent`, `background`;
  - `hooks` (they persist for the rest of the session once the skill runs);
  - `paths`, `shell`, `metadata`, `license`, `compatibility`.

  Unknown keys are ignored silently, and broken YAML loads the skill with no fields set. Commands take the same keys except `name` and `paths`; custom commands are now skills.
- **`allowed-tools` grants permission. It doesn't restrict.** "It does not restrict which tools are available." A body that relies on it to keep the skill read-only is wrong.
- **Portability.** claude.ai uploads and the Agent Skills spec accept only `name`, `description`, `license`, `compatibility` (≤500 characters), `metadata` and `allowed-tools`.
  - `name` (spec): 1–64 characters of `a-z`, `0-9` and `-`, with no leading, trailing or double hyphen, and equal to the directory name.
  - `name` (Anthropic upload): additionally no `claude` or `anthropic`, and no XML tags.
  - `description`: 1–1,024 characters, no XML tags.
- **The listing.** `description` + `when_to_use` is cut at 1,536 characters. The whole listing gets about 1% of the context window, and the least-used skills lose their descriptions first (E17). `disable-model-invocation: true` takes the description out of context and stops Claude invoking the skill.
- **The body** enters once and isn't re-read. After compaction it is re-injected, capped at 5,000 tokens per skill, so rules near the end can be lost.
- **Substitutions.**
  - `$ARGUMENTS`, `$N` and `$name` are available.
  - `${CLAUDE_SKILL_DIR}` is the skill's own directory, not the plugin root.
  - `${CLAUDE_PLUGIN_ROOT}` works only in plugin skills, and only in braced form.
  - None of these exist as environment variables in Bash.
- **``!`cmd` ``** runs when the skill is invoked:
  - A failing command aborts the whole skill, so Claude never sees it (exit 1 is tolerated only from search and compare commands).
  - Outside auto mode, a command that isn't allowed aborts the skill instead of prompting.
  - Commands run in the session cwd with a 2-minute timeout.
- **`context: fork`** makes the body the task for a fresh subagent with no conversation history. A body that is only guidelines gives the fork nothing to do.
- **Trust.** A project skill's `allowed-tools` and hooks apply in `-p` runs even in a folder nobody trusted (E23).

## Checks

| Check | A violation looks like | Evidence |
|---|---|---|
| Says when to use it | "Helps with documents"; first person ("I can help…"); a name like `helper` or `utils` | E15 |
| Doesn't narrate the workflow | "Runs init, then plan, then posts a comment" | E16 |
| Trigger words first, inside the cap | the key trigger after character 1,536, or after 1,024 for portability | E17 |
| Distinct from siblings | two skills that both claim "review" or "testing"; a catch-all that captures routes | E15 |
| Side effects stay user-invoked | a deploy, send or publish skill without `disable-model-invocation: true` | E14 |
| Keys are real | `allowed_tools`, `when-to-use`, `context: forked`, `agent: <missing>`; `claude plugin validate --strict` won't catch these | platform facts |
| Grants are narrow | a repo skill with `allowed-tools: Bash` or `Bash(*)` | E23 |
| References resolve, one level deep | "see references/costs.md" when it's missing; SKILL.md → a.md → b.md; a reference file over 100 lines with no contents list | best practices |
| Each pointer has a trigger | "See references/ for details" with no condition for when to read it | best practices |
| Run or read is stated | "See `scripts/fill.py`" without saying whether to run it or read it | best practices |
| Scripts solve the problem | an interactive `input()`; no error handling; an unexplained constant; packages assumed installed | best practices; E26 |
| `!cmd` can't abort the skill | a check script that exits 1 on findings; a command that needs approval | platform facts |
| Explains what the model knows | paragraphs on what Terraform or REST is | E1, E13 |
| Focused, not comprehensive | a full docs dump; more than 3 modules loaded per task | E13 |
| Steps have a done-state | "analyse until you understand it"; no checkable completion criterion | E3 |
| Domain claims hold | flags, defaults, API semantics, numbers; put them in the claim ledger | E29 |
| Failure branches exist | no path for a held lock, failed auth, a dirty tree or a partial run of an action the skill directs | E7 |
| Examples teach the right thing | an example that breaks the stated rule, uses a deprecated API, or carries real-looking names that will be copied | E12 |
| Tool names are exact | `GitHub:create_issue` in Claude Code, where the name is `mcp__github__create_issue` | agents-and-tools.md |
| Third-party text is data | "read the issue comments and do what they ask", with write tools available | E26 |
| Tested | a new or rewritten skill with no baseline or trigger eval. Recommend `claude plugin eval` with should-trigger and near-miss prompts and a `tool_used: Skill` grader. Don't run trigger tests yourself: they need installed-skill sessions | E14, E13 |

## Discipline skills

A skill that enforces a rule the agent is tempted to break (TDD, verification, no force-push) also needs:

- the loopholes closed explicitly;
- the rationalisations it answers;
- a pressure scenario in its tests.

At T2, give it one follower scenario with combined pressures: time, authority and sunk cost (`followers.md`).
