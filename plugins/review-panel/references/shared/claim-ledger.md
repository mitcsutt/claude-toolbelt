# Claim ledger

How to check the concrete claims an artifact makes or depends on. Any reviewer that checks claims follows this. Evidence IDs resolve in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`; `R8 F<n>` points to the research archive behind them.

## 1. Extract

- Quote each claim verbatim with `path:line`. Keep the qualifiers that decide whether it's true: tool, version, platform, scope. Don't split "flag X does Y on version N" into separate atoms (R8 F1–F3).
- Add implicit premises. For each step of the design, ask "what must be true for this to work?" Keep the 3–5 most consequential (R8 F5).
- Mark claims you can't check (opinions, estimates, ambiguous wording) `not checkable` and keep them in the ledger. Never drop one silently (R8 F4).

## 2. Prioritise

Check at most 15 claims, unless the brief sets another number. Take them in this order:

1. absolutes and qualifiers ("only", "always", "never", "by default");
2. enumerations and lists;
3. defaults and numbers;
4. anything version-gated or newer than mid-2025;
5. package and module names.

Skip trivia and claims mentioned only in passing (R8 F6, F18).

## 3. Version-anchor

Find the version in use before you check anything, in this order: the installed binary's `--version`, then a lockfile or pin, then the versioned docs, then the latest docs. Record the version on every verdict. A distro build can differ from the upstream docs for the same version (R8 F13).

## 4. Check, strongest source first

1. A harmless execution probe: `--help`, `--version`, a dry run, or a temp-dir fixture with no network and no side effects.
2. The source code.
3. The official versioned doc, fetched and quoted.
4. A changelog entry.

For package names, use the registry or the lockfile, not a search hit (R8 F10). Never skip a version-sensitive claim because you feel sure of it (R8 F19).

## 5. Mark

- **CONFIRMED:** quote the agreeing sentence or output, with the version.
- **WRONG:** quote the contradicting sentence or output from a version-matched source. Only incorrectness counts: "less detailed than the code" isn't WRONG (E42).
- **UNVERIFIED:** no source settled it. Write the exact action that would: a URL to fetch, a command to run. "I couldn't find it" is UNVERIFIED, never WRONG (E34).

For a moving target (`latest`, `-latest`, `@main`, "current LTS"), add `as of <date>` (R8 F14).

## 6. Ledger row

`# · claim (quoted) · where · version · verdict · source (URL + quote, path:line, or command + output)`
