# Checks: threat-modeller (T1)

Verified on 2026-11-19 (Claude Code 2.1.328 docs; tools as in TM25). Re-check live before a finding depends on a volatile fact (marked ⟳).

Rules for every row:

- Run from the repo root. Run `git ls-files` first and skip rows whose file types don't exist.
- Patterns are POSIX ERE only: no `\s`, `\d` or `\b` (TM11). A literal `|` is written `[|]`.
- A hit is a candidate, never a finding. It becomes a finding only through the kill chain in `method.md`.
- **A secret-bearing pattern runs only in `-c`, `-l` or `-o` mode, or the Grep tool's `count`/`files_with_matches` mode** (TM25). Never widen an `-o` pattern past the end of a key name.
- Rows that print whole lines (`-n` without `-o`) match code shapes, not secrets. If a printed line still holds a credential, quote it in the report with the value replaced by `<redacted>`.
- Each runnable command sits on one line in a `sh` block; the plugin's tests run every one against a planted fake secret and fail if its value is printed.
- No Bash: use the Grep tool form. It is a ripgrep search; if a search returns nothing under dot-directories (`.claude`, `.github`), search them again with `path` set to that directory before you clear the row.
- Order follows the owner's real failures: credential scope and binds first (TM24).

## 1. Credential scope

```sh
git grep -n -o -E '(permissions:|scopes?:|Scripts? ?(Write|Edit)|contents: *write|id-token: *write)' -- .github '*.tf' '*.ts' '*.py' '*.yaml' '*.yml'
```

Grep tool: same pattern, `output_mode: content` (these keys hold no secret values). FP trap: read the resource the token touches; a one-resource job with an account-wide scope is a finding. TM18, TM24.

## 2. Binds

```sh
git grep -n -E "0\.0\.0\.0|INADDR_ANY|--host[= ]0|host *[:=] *['\"]?::['\"]?"
git grep -n -E 'listen\(|\.run\(|serve\('
```

Grep tool: same patterns. A port or host in a variable is a blind spot (R16 §5): read the call. FP trap: a container behind a `127.0.0.1:` port mapping (read the compose `ports:`). TM15, TM24.

## 3. Secret-shaped strings

```sh
git grep -c -E 'AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-|sk-ant-|sk_live_'
```

Grep tool: `output_mode: count`. FP trap: fixtures, docs, `.example` files and `EXAMPLE`-suffixed AWS documentation keys (TM21, TM22). The prefix list's recall is UNVERIFIED: recommend gitleaks under Coverage. TM19 (CWE-798).

## 4. Fallback secrets

```sh
git grep -n -o -E 'process\.env\.[A-Z_]*(SECRET|KEY|TOKEN|PASS)[A-Z_]* *([|][|]|[?][?])'
git grep -n -o -E "environ\.get\(['\"][A-Z_]*(SECRET|KEY|TOKEN|PASS)[A-Z_]*['\"] *,"
```

Grep tool: same patterns, `output_mode: content` is safe only because the match ends before the value; never widen it. FP trap: `env['K']` crashes when unset, so there is no fallback (TM11); check whether the unit or compose file sets `K` before claiming the fallback runs. TM11.

## 5. Localhost API without Origin, Host or token check

```sh
for f in $(git grep -l -E "listen\(|createServer|HTTPServer|app\.(post|put|delete)|do_POST"); do git grep -n -o -i -E "origin|host|allowedHosts|authorization|token" -- "$f"; done
```

Grep tool: one `files_with_matches` call for the server pattern, then one `count` call per file for the check pattern (these lines often hold the token itself, so never `content`). FP trap: read-only idempotent endpoints. TM15.

## 6. CORS wildcard

```sh
git grep -n -E "(Access-Control-Allow-Origin|allow_origins|allowedOrigins|cors).{0,80}\*"
```

Grep tool: same pattern. FP trap: public, credential-less assets. TM19.

## 7. Code, command and template sinks

```sh
git grep -n -E "(^|[^A-Za-z_.])eval\(|new Function\(|shell=True|os\.system\(|execSync\(|child_process|render_template_string|subprocess\.(run|Popen|call)\("
```

Grep tool: same pattern. FP trap: constant arguments; input only the repo's own code produces (TM10). TM19.

## 8. Fetched-and-executed code

```sh
git grep -n -E "(curl|wget)[^|]*[|] *(sudo +)?(ba|z)?sh|npx +-y|uvx +[a-z]|pip install +git\+|@latest"
```

Grep tool: same pattern. FP trap: a version pin with a checksum beside it. TM19, E26.

## 9. npm install scripts

```sh
node -e 'const fs=require("fs");for(const f of process.argv.slice(1)){const s=JSON.parse(fs.readFileSync(f,"utf8")).scripts||{};for(const k of ["preinstall","install","postinstall","prepare"])if(s[k])console.log(f,k)}' $(git ls-files '*package.json')
```

Grep tool: read each `package.json`. FP trap: `prepare` for a local build. TM19.

## 10. GitHub Actions pwn request

```sh
git grep -n -E "pull_request_target|workflow_run" -- .github/workflows
for f in $(git grep -l -E "pull_request_target|workflow_run" -- .github/workflows); do git grep -n -E "head\.(sha|ref)|pull_request\.head|refs/pull/|allow-unsafe-pr-checkout|gh pr checkout|git fetch" -- "$f"; done
```

Grep tool: same patterns. FP trap: the head checked out as data only, with no build, install or agent step. actions/checkout's default protection refuses fork head refs unless `allow-unsafe-pr-checkout: true`: check the checkout version and that input before claiming link (b) ⟳. TM18.

## 11. GitHub Actions untrusted interpolation, including into prompts

```sh
git grep -n -E '\$\{\{ *github\.event\.(issue|pull_request|comment|review|discussion)[a-z_.]*\.(title|body)|github\.head_ref' -- .github/workflows
```

Grep tool: same pattern. FP trap: the value is passed via `env:` to a consumer that is neither a shell nor an agent. TM14, TM18.

## 12. Unpinned actions

```sh
git grep -n -E 'uses: *[^ @]+@' -- .github/workflows | grep -v -E '@[0-9a-f]{40}'
```

Grep tool: same pattern, then read the hits. FP trap: first-party `actions/*` at a major tag is COULD. TM18.

## 13. Committed agent config (names only)

```sh
git grep -n -o -E 'enableAllProjectMcpServers|enabledMcpjsonServers|ANTHROPIC_BASE_URL|ANTHROPIC_API_KEY|apiKeyHelper|"hooks"|bypassPermissions|skipDangerousModePermissionPrompt|autoApprove' -- '.claude/*' '.mcp.json' '.vscode/*' '*.code-workspace'
```

Grep tool: `output_mode: files_with_matches` (the Grep tool has no `-o`), then `Read` only the files not named like secrets. FP trap: the effect depends on context and version (TM12, TM13 ⟳). TM12, TM13, TM16, E23.

## 14. Agent in CI

```sh
git grep -n -E 'claude +-p|claude-code-action|anthropics/claude|gemini-cli|codex' -- .github .gitlab-ci.yml
```

Grep tool: same pattern. FP trap: `--setting-sources user` or a read-only token. `--bare` clears hooks and `.mcp.json` but not a committed `env` (for example `ANTHROPIC_BASE_URL`). TM12.

## 15. Hidden Unicode

```sh
perl -CSD -ne 'print "$ARGV:$.\n" if /[\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{2064}\x{2066}-\x{2069}\x{FEFF}\x{E0000}-\x{E007F}]/; close ARGV if eof' $(git ls-files)
```

Grep tool: pattern `[\x{E0000}-\x{E007F}\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{2064}\x{2066}-\x{2069}\x{FEFF}]`, `output_mode: files_with_matches` (TM25), then the dot-directories by `path`. Report file, line and code-point range; never copy the characters. FP trap: a BOM at file start; RTL locale strings. TM19, E26.
