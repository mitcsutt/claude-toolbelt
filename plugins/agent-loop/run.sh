#!/usr/bin/env bash
# agent-loop harness entry point. No logic lives here: the loop is runner/run.py.
#
# The shim stays for two reasons. The dashboard spawns the harness as
# `Popen(["bash", run_sh], ...)`, and every liveness check in the system is
# "pid is alive AND `ps -o command= -p <pid>` contains run.sh" — so the exec'd
# python must keep that token on its command line, which `--shim` does.
# `-m` rather than a file path: runner/ uses package-relative imports.
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$PLUGIN_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"
