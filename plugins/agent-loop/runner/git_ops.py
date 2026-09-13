"""Every git operation the harness performs. Models never run git.

Containment lives here: after a Worker returns (or is killed), the harness
compares the working tree against the contract's allow_list and reverts
anything outside it, so a killed phase can never leave the tree ambiguous.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
from typing import Dict, List

from . import util

_IMPORT_RE = re.compile(r"^(import|from|#include|use|require|using)\b")


class GitError(RuntimeError):
    """A git invocation failed and the caller cannot proceed."""


def _git(cwd: str, args: List[str], check: bool = True):
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise GitError("git %s: %s" % (" ".join(args), exc))
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise GitError("git %s failed (rc=%d): %s"
                       % (" ".join(args), proc.returncode,
                          proc.stderr.decode("utf-8", "replace").strip()))
    return proc.returncode, out


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of paths containing spaces or specials.

    Git escapes non-ASCII bytes as octal (`\\NNN`), one escape per raw UTF-8
    byte. `str.decode("unicode_escape")` turns each `\\NNN` into a single
    Latin-1 code point rather than a UTF-8 byte, so it must be re-encoded as
    Latin-1 to recover the original bytes before decoding those as UTF-8.
    """
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        try:
            return (path[1:-1].encode("utf-8").decode("unicode_escape")
                    .encode("latin-1").decode("utf-8", "replace"))
        except (UnicodeDecodeError, UnicodeEncodeError):
            return path[1:-1]
    return path


def changed_paths(cwd: str) -> List[str]:
    """Every path the working tree differs on, untracked files included.

    A rename reports BOTH sides, not just the destination: the source can be
    a stray on its own if it falls outside the allow_list (the Worker
    deleted a file it was never permitted to touch), independently of
    whether the destination is in-scope.
    """
    _, out = _git(cwd, ["status", "--porcelain", "-uall"])
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:]
        if " -> " in rest:                     # a rename: both names are ours
            src, dst = rest.split(" -> ", 1)
            paths.append(_unquote(src))
            paths.append(_unquote(dst))
        else:
            paths.append(_unquote(rest))
    return paths


def _covered(path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
        base = pat.rstrip("/")
        if path.startswith(base + "/"):
            return True
    return False


def strays(changed: List[str], allow_list: List[str]) -> List[str]:
    """The changed paths the contract did not sanction."""
    return [p for p in changed if not _covered(p, allow_list)]


def _unsafe(path: str) -> bool:
    """An absolute path or a `..` component escapes `cwd`; never touch it."""
    if os.path.isabs(path):
        return True
    return ".." in path.split("/")


def revert(cwd: str, paths: List[str]) -> None:
    """Restore every path to its HEAD state; delete anything HEAD never had.

    `git checkout -- <path>` resolves against the INDEX, not HEAD, so a
    staged rename (the index already holds the new name) would silently
    survive a revert. Checking each path against HEAD instead — and
    unstaging + deleting whatever HEAD never had — undoes a rename
    correctly: the old name is in HEAD and comes back, the new name is not
    and is removed. An unborn branch (no HEAD yet) makes every path "not in
    HEAD" automatically, since `cat-file -e HEAD:<path>` fails there too.

    **A directory-shaped path is refused, not emptied.** `git status
    --porcelain -uall` still reports a nested repository as ONE entry ending
    in `/`, because git will not descend into another repo; a dirty submodule
    reports as a single modified entry that `checkout HEAD --` does not
    recurse into. Recursively deleting either destroys a whole repository's
    uncommitted work to contain a stray, which is a far bigger hammer than the
    problem justifies. This function therefore never removes a directory, and
    the caller is expected to RE-READ the tree and report what survived —
    `os.remove` raising on a directory was being swallowed here, so the only
    record of the failure was a log line claiming the opposite.
    """
    for path in paths:
        if _unsafe(path):
            continue
        rc, _ = _git(cwd, ["cat-file", "-e", "HEAD:" + path], check=False)
        if rc == 0:
            _git(cwd, ["checkout", "HEAD", "--", path], check=False)
            continue
        full = os.path.join(cwd, path.rstrip("/"))
        if os.path.isdir(full) and not os.path.islink(full):
            continue
        _git(cwd, ["rm", "-f", "--cached", "--", path], check=False)
        try:
            os.remove(full)
        except OSError:
            continue
        parent = os.path.dirname(full)
        while parent and parent != cwd:
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)


def head_sha(cwd: str) -> str:
    rc, out = _git(cwd, ["rev-parse", "HEAD"], check=False)
    return out.strip() if rc == 0 else ""


def commit(cwd: str, paths: List[str], subject: str, trailers: Dict[str, str]) -> str:
    """Stage exactly `paths`, commit, return the sha (or "" when nothing staged)."""
    if not paths:
        return ""
    _git(cwd, ["add", "--"] + list(paths))
    rc, _ = _git(cwd, ["diff", "--cached", "--quiet"], check=False)
    if rc == 0:
        return ""                              # staged set is identical to HEAD
    message = subject
    body = "\n".join("%s: %s" % (k, v) for k, v in trailers.items())
    if body:
        message = subject + "\n\n" + body
    _git(cwd, ["commit", "-q", "-m", message])
    return head_sha(cwd)


def diff_text(cwd: str, base_sha: str, paths: List[str],
              max_chars: int = 200000) -> str:
    """The working tree's diff against `base_sha`, bounded.

    `add -N` first so a file the Worker created shows as a diff rather than as
    nothing at all — an Evaluator handed an empty diff for a new component is
    exactly how an unreviewed change slips through.
    """
    if paths:
        _git(cwd, ["add", "-N", "--"] + list(paths), check=False)
    args = ["diff"]
    if base_sha:
        args.append(base_sha)
    if paths:
        args.append("--")
        args.extend(paths)
    _, out = _git(cwd, args, check=False)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n[... diff truncated at %d characters ...]\n" % max_chars
    return out


def _normalize(text: str) -> List[str]:
    """Stripped, blank-free lines with the import block sorted to the front."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    imports = sorted(ln for ln in lines if _IMPORT_RE.match(ln))
    rest = [ln for ln in lines if not _IMPORT_RE.match(ln)]
    return imports + rest


def similarity(src_path: str, dst_path: str) -> float:
    """difflib ratio over normalized lines: 1.0 identical, 0.0 nothing in common.

    This is what makes a "copy" task provable. The 2026-09-11 run's copy task
    passed with a six-line TODO comment because its verification grepped for
    that comment; this ratio would have scored it under 0.05.
    """
    a = _normalize(util.read_text(src_path))
    b = _normalize(util.read_text(dst_path))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
