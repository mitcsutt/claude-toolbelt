# Break rules by interface type

Verified on 2026-11-19 against the sources in evidence.md; re-check live before a finding depends on a volatile fact (marked [V]).

What counts as breaking, per interface type, borrowed from the tool that defines it so a finding can cite `tool: rule id`. A rule's verdict is a candidate. Severity comes from the named consumer (agent file, step 5). Evidence IDs resolve in `evidence.md`.

## Universal test

oasdiff (CK8): "A change is breaking if a consumer that followed the old contract can stop working under the new one." Decide the direction first: who sends the element, who reads it.

| change | request / input side | response / output side |
|---|---|---|
| field added, optional | safe | safe (oasdiff `response-optional-property-added` INFO); a candidate only with a quoted strict consumer (`additionalProperties: false`, strict deserialiser) |
| field added, required | breaking, even with a default (CK8: "whether or not the property has a default") | safe |
| field removed | safe for senders unless the server now rejects it | breaking (`response-required-property-removed` ERR) |
| became nullable | safe | breaking (`response-property-became-nullable` ERR) |
| enum value added | safe (`request-property-enum-value-added` INFO) | candidate (`response-property-enum-value-added` ERR for `enum`; an `x-extensible-enum` list is not flagged); see "Enums" below |
| enum value removed | breaking (`request-property-enum-value-removed` ERR) | narrowing; breaking only with a round-trip consumer (see "Enums") |
| type changed | breaking unless widened (oasdiff `request-property-type-generalized` INFO) | breaking unless narrowed (`response-property-type-specialized` INFO); the commonest break in CK4 |

A schema used on both sides (a `$ref` in a request body and a response) takes the stricter column.

## Enums

- **Removal.** Response side: narrowing, safe for readers, unless a quoted consumer sends the value back (a GET then PUT round-trip) or the same enum is also a request type, where `request-property-enum-value-removed` is ERR. A client that still sends the old value (old mobile builds stay installed) breaks on the request side. (CK8, R12 §7)
- **Addition.** Request side: safe. Response side: a candidate (oasdiff ERR; Kubernetes "*not* a compatible change"; protobuf "a compilation break for any code with an exhaustive switch"). BLOCK or SHOULD only with a quoted consumer that throws or mis-handles an unknown value. An extensible contract plus a default branch in every named consumer is a "don't flag". (CK8, CK9, CK10, CK13)

## OpenAPI and JSON Schema

(CK4, CK8)

- The direction table above.
- Path or operation removed without a deprecation.
- `$ref` changes hide behind refs: resolve refs before diffing (`commands.md`, C2).
- Repo already uses the tool (`.oasdiff*`, a CI step)? Recommend: `oasdiff breaking <base>:<spec> <spec>`.

## Protobuf

(CK9)

- Field number changed or reused: "Changing field numbers for any existing field is not safe."
- Field deleted without `reserved` for its number and name (`FIELD_NO_DELETE_UNLESS_NUMBER_RESERVED`).
- Cardinality changed (`FIELD_SAME_CARDINALITY`), JSON name changed (`FIELD_SAME_JSON_NAME`), enum value deleted (`ENUM_VALUE_NO_DELETE`).
- Enum value added: a compile break for exhaustive switches in generated-code consumers.
- int32 → int64: wire-compatible, but an int32 reader discards the high bits.
- Added fields are safe; proto3 preserves unknown fields.
- The minimum check level is WIRE_JSON ("the recommended minimum level"); FILE is buf's default.
- Repo already uses the tool (`buf.yaml` with a `breaking:` block)? Recommend: `buf breaking --against '.git#ref=<base>'` [V]; a base older than the clone depth needs `,depth=<n>`.

## GraphQL

(CK10)

- Breaking (graphql-js `findBreakingChanges`, v16.9.0): `TYPE_REMOVED`, `TYPE_CHANGED_KIND`, `TYPE_REMOVED_FROM_UNION`, `VALUE_REMOVED_FROM_ENUM`, `REQUIRED_INPUT_FIELD_ADDED`, `IMPLEMENTED_INTERFACE_REMOVED`, `FIELD_REMOVED`, `FIELD_CHANGED_KIND`, `REQUIRED_ARG_ADDED`, `ARG_REMOVED`, `ARG_CHANGED_KIND`, and the directive changes `DIRECTIVE_REMOVED`, `DIRECTIVE_ARG_REMOVED`, `REQUIRED_DIRECTIVE_ARG_ADDED`, `DIRECTIVE_REPEATABLE_REMOVED`, `DIRECTIVE_LOCATION_REMOVED`.
- Dangerous: `VALUE_ADDED_TO_ENUM`, `TYPE_ADDED_TO_UNION`, `OPTIONAL_INPUT_FIELD_ADDED`, `OPTIONAL_ARG_ADDED`, `IMPLEMENTED_INTERFACE_ADDED`, `ARG_DEFAULT_VALUE_CHANGE`.
- Removing a field is breaking even after it was deprecated: Inspector "Every removal of a deprecated field is considered a breaking change" by default.
- Repo already uses GraphQL Inspector? Recommend its `diff` command against the base schema.

## Events, Avro and Schema Registry

(CK11)

- Name the subject's compatibility mode. BACKWARD (the default) only promises that new-schema consumers read old data, so old consumers reading new events mid-rollout are unprotected. FORWARD is the reverse; FULL allows only adding or removing optional fields.
- Avro: a reader field with no default that the writer lacks is an error.
- No registry: walk old producer → new consumer and new producer → old consumer by hand.

## Library exports

(CK2, CK3, CK12)

- Removed or renamed exports, changed signatures, added required parameters, narrowed return types.
- `export *` re-exports widen or shrink the surface without the file naming the symbol.
- Rust: added enum variants or fields on an all-public struct are the commonest semver violation.
- Minor and patch releases break dependents too (CK2), so a non-major version is no defence.
- API Extractor report (`etc/<pkg>.api.md`) present? Its diff is the surface change. japicmp and cargo-semver-checks: recommend only, under Coverage.

## CLI flags

(CK5, CK23)

- Removed or renamed flags, changed defaults, changed exit codes, changed output that scripts parse.
- A renamed name that other tools still pass and that "passes silently" (a package filter that matches nothing and exits 0) is worse than one that errors.

## Env vars and config keys

(CK5, CK19)

- Removals, renames and default changes all break readers; renames and default changes are the ones that pass silently.
- Readers outside the process: cron (which sets only SHELL, LOGNAME, HOME and a minimal PATH, so a cron job sees only what its line or a sourced file adds), compose `environment:` and `env_file:`, systemd `Environment=`, `.env.example`, CI variables, docs.

## Persisted data, file formats and storage keys

(CK7, CK23)

- Ordinal or index encodings: an enum member inserted mid-list shifts every later member's stored number (HDFS-15624). Append, or store explicit codes.
- Renamed storage keys: census all of them (`localStorage`, cache keys, file names), not only the ones the plan names.
- A new consumer of an old format: a parser stricter than the writer fails on data already on disk.
- Ask both questions: what did old code write that new code reads, and what will new code write that old code (mid-deploy or after rollback) reads?

## Semver

(CK20)

A break in a MAJOR bump, with a deprecation window and consumers pinned to the old major, is not a finding.
