#!/usr/bin/env bash
# A one-word README typo fix (CHANGE.diff). The control: no panel is
# printed, because the change is trivial and no signal applies.
set -euo pipefail

# --- case files -------------------------------------------------------------
cat > README.md <<'EOF'
# tidy-csv

A small command-line tool that normalises CSV files: trims whitespace,
fixes line endings and rewrites quoting so every file round-trips cleanly.

## Instalation

    npm install -g tidy-csv

## Usage

    tidy-csv input.csv > output.csv

Options:

- `--delimiter <c>`: the field separator (default `,`)
- `--crlf`: write Windows line endings

## Licence

MIT
EOF

cat > CHANGE.diff <<'EOF'
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -4,7 +4,7 @@ A small command-line tool that normalises CSV files: trims whitespace,
 fixes line endings and rewrites quoting so every file round-trips cleanly.

-## Instalation
+## Installation

     npm install -g tidy-csv

EOF
