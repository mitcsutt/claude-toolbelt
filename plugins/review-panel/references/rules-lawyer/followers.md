# Followers: testing a line by having fresh agents follow it

Reading a line and judging it "clear" is weak evidence: reading alone detects ambiguity poorly, and Claude rates Claude-style prose as clearer than it is (E30, E31). Watching several fresh agents act on the line is strong evidence. Followers are the agent's perspective, measured instead of imagined.

## When

- Tier T2 only. Budget: at most 3 scenarios × 3 runs, unless the brief sets another number. Spending the whole budget without settling a question is a result; report it as coverage.
- Pick lines whose misreading would be BLOCK or SHOULD and that the claim ledger and deterministic checks haven't already settled. A WRONG flag needs no follower to prove it wrong. Good candidates:
  - an ambiguous condition, a vague threshold or an unclear step order;
  - two rules that collide, where the question is which one wins;
  - a rule-enforcing line the agent may be tempted to break;
  - a hook block message, where the question is what the agent does next.
- One scenario can exercise several open lines. Prefer that to one scenario per line.
- Don't test trigger text (skill or agent descriptions) with followers. Triggering needs installed-skill sessions. Recommend `claude plugin eval` with should-trigger and near-miss prompts instead.
- Skip pure reference text, and anything whose effects can't be simulated on paper.

## Design each scenario before dispatching

Write the expected behaviour down before you dispatch. Deciding afterwards invites rationalising whatever came back. A scenario has:

- **The instructions**, exactly as the agent receives them:
  - a skill body as the content of an invoked skill;
  - CLAUDE.md lines as project instructions;
  - a hook as the tool result: "You ran `<command>`. The harness returned: `<hook message>`".
- **The task**: one realistic user request.
- **The environment**: the facts the agent would see, such as file names, branch and tool output.
- **The line under test**, and the dimension that matters (which command, whether it asks first, whether it stops).
- **For rule-enforcing text**, three combined pressures, such as a deadline, an authority figure who says to skip it, and sunk cost, plus a forced choice between concrete options.

## Dispatch

Send all runs in one message, each to `review-panel:instruction-follower`, with this prompt:

```
## Your instructions
<the artifact text, framed as the agent receives it>

## Your task
<the user request>

## What you can see
<environment facts>
```

- Followers start without the user, project and local CLAUDE.md files (`omitClaudeMd`), so a divergence comes from the artifact itself. Cross-file conflicts are your static job. Managed policy still loads, and so does a nested CLAUDE.md or path-scoped rule once a follower reads a file under it. Keep scenarios away from such directories, or note them alongside the result.
- Set `model` to the model the artifact runs on: the artifact's own `model:` if it pins one, otherwise leave it unset so the follower inherits.
- A divergence seen only on a smaller model counts as a finding when the artifact runs on that model. Otherwise it is a candidate to confirm.
- Wait for every follower to return before you score, and keep your turn open while any is still running. A subagent that ends its turn early loses the replies still on their way.
- If the Agent tool isn't available, report T2 as not run. List each scenario you designed, with its instructions, task, environment and expected behaviour, so someone can run it later, and finish the review at T1.

## Score

- **Diverged**: the runs disagree on the dimension that matters, or any run departs from the expected behaviour. Report k/n, quote each reading and the line that allowed it, and suggest the rewrite that closes it.
- **Held**: every run matches the expected behaviour. Record it under "Checked and fine". Same-family agreement is weaker evidence than disagreement, so say "held 3/3", not "unambiguous".
- **The follower's `guesses` and `would_ask_user`** fields often name the gap directly. Quote them.

## Optional baseline arm

Use it only when the question is whether the text changes anything at all. Run the same scenario without the artifact, which counts against the budget. If the behaviour is identical, the text is a no-op on this model, which makes it a CUT candidate (E4).
