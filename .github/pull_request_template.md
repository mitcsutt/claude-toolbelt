## Summary

<!-- What does this PR do, in one or two sentences? -->

## What changed

<!-- Bullet list of the concrete changes. Call out which plugin(s), or which
     repo-wide script/doc, this touches. -->

-

## Test plan

<!-- Paste the tail of `bash scripts/test-all.sh` output, or confirm it's
     green. See docs/testing.md for what that command covers. -->

```
$ bash scripts/test-all.sh
```

## Checklist

- [ ] `plugin.json` version bumped for any behaviour change (see
      [`docs/authoring-plugins.md`](../docs/authoring-plugins.md#versioning))
- [ ] The plugin's own `README.md` updated to match
- [ ] `bash scripts/test-all.sh` passes
- [ ] New plugin only: added to the root `README.md` table, install block,
      and "Plugin details" section
- [ ] Prompt-shaped plugin only (`cutthroat`, `postmortem`, `find-docs`): ran
      `claude plugin eval` locally before bumping its version — this is a
      manual step that costs money per run and is never run in CI
