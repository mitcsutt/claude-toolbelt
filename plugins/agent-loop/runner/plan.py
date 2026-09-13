"""LOOP_PLAN.md — the only file that says what work exists and what is left.

The grammar is exactly the one serve.py greps (`w10` §3): a task row is
`- [<glyph>] <rest>`, a segment is a line starting `## `, an id is the first
`T<digits>` token. This module adds the v3 row metadata (`| no-ui`,
`| copy_of:`, `| blocked_by:`, `| split_of:`), none of which changes the glyph
set or the id shape — so a v2 plan parses unchanged and the dashboard keeps
counting. Ids are never widened past `\bT\d+\b`: a `T60a` would be invisible to
serve.py's grep, so split sub-tasks take the next free numbers instead.

The harness is the ONLY writer. Every edit is in place on the existing row:
flip the glyph, append at most one suffix. The description text is immutable
after the Planner writes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import util

TASK_RE = re.compile(r"^(\s*)- \[(.)\] (.*)$")
ID_RE = re.compile(r"\bT\d+\b")                      # serve.py's, unchanged
ROW_TITLE_RE = re.compile(r"^(\s*- \[.\] )(?:T\d+[a-z]?:\s*)?(.*)$")
REVIEWED_RE = re.compile(r"^Reviewed:\s*(\S+)\s*$")
SHA_RE = re.compile(r"done\(\+([0-9a-fA-F]+)\)")
MODEL_RE = re.compile(r"\|\s*model:\s*(\S+)")
DEPENDS_RE = re.compile(r"\|\s*depends_on:\s*([^|]+)")
BLOCKED_BY_RE = re.compile(r"\|\s*blocked_by:\s*([^|]+)")
CLONE_RE = re.compile(r"\|\s*clone_of:\s*(T\d+)")
COPY_RE = re.compile(r"\|\s*copy_of:\s*(T\d+)")
SPLIT_OF_RE = re.compile(r"\|\s*split_of:\s*(T\d+)")
NO_UI_RE = re.compile(r"\|\s*no-ui\b")
CLASS_RE = re.compile(r"\|\s*(mechanical|complex)\b")

GLYPH_STATE = {" ": "pending", "~": "doing", "x": "done", "!": "blocked", "-": "skipped"}
STATE_GLYPH = {"pending": " ", "doing": "~", "done": "x", "blocked": "!",
               "skipped": "-", "blocked-upstream": " "}
DONE_STATES = ("done", "skipped")
# Not a glyph: the plan has exactly five and serve.py maps all of them. A
# blocked-upstream task stays `[ ]` (it IS still outstanding work) and carries
# this marker, which plan.py reads back as a state and eligible() skips.
BLOCKED_UPSTREAM = "[blocked-upstream]"


@dataclass
class Task:
    id: str
    segment: str
    title: str
    state: str
    sha: Optional[str] = None
    class_flag: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    clone_of: Optional[str] = None
    copy_of: Optional[str] = None
    split_of: Optional[str] = None
    blocked_by: List[str] = field(default_factory=list)
    no_ui: bool = False
    model: Optional[str] = None
    line_no: int = -1
    raw: str = ""


@dataclass
class Segment:
    name: str
    line_no: int
    reviewed_sha: Optional[str] = None
    tasks: List[Task] = field(default_factory=list)


def _csv(value: str) -> List[str]:
    return [tok.strip() for tok in value.split(",") if tok.strip()]


def _retitle(row: str, new_id: str) -> str:
    """Put `new_id` in the row's id position, replacing whatever was proposed.

    Matching on the row's shape rather than on the first `T<n>` token anywhere in
    it: a proposed `- [ ] T4a: half | depends_on: T3` has no valid id of its own,
    and a token search would rewrite the DEPENDENCY instead of the title.
    """
    m = ROW_TITLE_RE.match(row)
    if not m:
        return "- [ ] %s: %s" % (new_id, row.strip().lstrip("- ").lstrip())
    return "%s%s: %s" % (m.group(1), new_id, m.group(2))


def _parse_task(line: str, line_no: int, segment: str) -> Optional[Task]:
    m = TASK_RE.match(line)
    if not m:
        return None
    glyph, rest = m.group(2), m.group(3)
    state = GLYPH_STATE.get(glyph, "pending")
    if state == "pending" and BLOCKED_UPSTREAM in rest:
        state = "blocked-upstream"
    id_m = ID_RE.search(rest)
    title = rest.split(" | ")[0]
    title = re.sub(r"^T\d+[a-z]?:\s*", "", title).strip()
    dep_m = DEPENDS_RE.search(rest)
    blk_m = BLOCKED_BY_RE.search(rest)
    cls_m = CLASS_RE.search(rest)
    mdl_m = MODEL_RE.search(rest)
    cln_m = CLONE_RE.search(rest)
    cpy_m = COPY_RE.search(rest)
    spl_m = SPLIT_OF_RE.search(rest)
    sha_m = SHA_RE.search(rest)
    return Task(
        id=id_m.group(0) if id_m else "",
        segment=segment,
        title=title,
        state=state,
        sha=sha_m.group(1) if sha_m else None,
        class_flag=cls_m.group(1) if cls_m else None,
        depends_on=_csv(dep_m.group(1)) if dep_m else [],
        clone_of=cln_m.group(1) if cln_m else None,
        copy_of=cpy_m.group(1) if cpy_m else None,
        split_of=spl_m.group(1) if spl_m else None,
        blocked_by=_csv(blk_m.group(1)) if blk_m else [],
        no_ui=bool(NO_UI_RE.search(rest)),
        model=mdl_m.group(1) if mdl_m else None,
        line_no=line_no,
        raw=line,
    )


class Plan(object):
    """An in-memory LOOP_PLAN.md. `lines` is the file split on "\\n"."""

    def __init__(self, path: str, lines: List[str]):
        self.path = path
        self.lines = lines

    @classmethod
    def load(cls, path: str) -> "Plan":
        return cls(path, util.read_text(path).split("\n"))

    # ---------------------------------------------------------------- reading
    def _scan(self):
        """(segments, tasks) rebuilt from self.lines. Cheap; always current."""
        segments: List[Segment] = []
        tasks: List[Task] = []
        current: Optional[Segment] = None
        name = ""
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                name = line[3:].strip()
                current = Segment(name=name, line_no=i)
                segments.append(current)
                continue
            t = _parse_task(line, i, name)
            if t is not None:
                tasks.append(t)
                if current is not None:
                    current.tasks.append(t)
                continue
            if current is not None and current.reviewed_sha is None:
                rm = REVIEWED_RE.match(line.strip())
                if rm:
                    current.reviewed_sha = rm.group(1)
        return segments, tasks

    def segments(self) -> List[Segment]:
        return self._scan()[0]

    def tasks(self) -> List[Task]:
        return self._scan()[1]

    def task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks():
            if t.id == task_id:
                return t
        return None

    def eligible(self) -> List[Task]:
        """Pending tasks whose dependencies and blockers are all settled."""
        tasks = self.tasks()
        settled = set(t.id for t in tasks if t.state in DONE_STATES)
        out = []
        for t in tasks:
            if t.state != "pending":
                continue
            if any(d not in settled for d in t.depends_on):
                continue
            if any(b not in settled for b in t.blocked_by):
                continue
            out.append(t)
        return out

    def dependents(self, task_id: str) -> List[Task]:
        """Every task that transitively depends on `task_id`, in plan order."""
        tasks = self.tasks()
        frontier = set([task_id])
        found = set()
        changed = True
        while changed:
            changed = False
            for t in tasks:
                if t.id in found or t.id == task_id:
                    continue
                if any(d in frontier for d in t.depends_on):
                    found.add(t.id)
                    frontier.add(t.id)
                    changed = True
        return [t for t in tasks if t.id in found]

    def next_review_segment(self) -> Optional[Segment]:
        for s in self.segments():
            if s.tasks and not s.reviewed_sha and all(t.state in DONE_STATES for t in s.tasks):
                return s
        return None

    def next_plan_segment(self) -> Optional[Segment]:
        for s in self.segments():
            if not s.tasks:
                return s
        return None

    def mode(self) -> str:
        """review | plan | execute | done | stuck (spec §4.1).

        Review wins over everything: a finished segment must be graded before
        the next one is written. Execute wins over plan: there is no reason to
        write more rows while eligible ones are waiting.
        """
        if self.next_review_segment() is not None:
            return "review"
        eligible = self.eligible()
        unplanned = self.next_plan_segment()
        if unplanned is not None and not eligible:
            return "plan"
        if eligible:
            return "execute"
        open_tasks = [t for t in self.tasks() if t.state not in DONE_STATES]
        if not open_tasks and unplanned is None:
            return "done"
        return "stuck"

    # ---------------------------------------------------------------- writing
    def _segment(self, name: str) -> Segment:
        for s in self.segments():
            if s.name == name:
                return s
        raise KeyError("no segment named %r" % name)

    def _segment_end(self, seg: Segment) -> int:
        """Index one past the segment's last non-blank line."""
        end = len(self.lines)
        for i in range(seg.line_no + 1, len(self.lines)):
            if self.lines[i].startswith("## "):
                end = i
                break
        while end - 1 > seg.line_no and not self.lines[end - 1].strip():
            end -= 1
        return end

    def set_state(self, task_id: str, state: str, sha: Optional[str] = None) -> None:
        """Flip one row's glyph in place; append ` done(+sha)` at most once.

        Never rewrites the description — an earlier bug inserted the sha
        mid-row and repeated the whole (verbose) description, doubling it.
        """
        if state not in STATE_GLYPH:
            raise ValueError("unknown task state %r" % state)
        t = self.task(task_id)
        if t is None:
            raise KeyError("no task %r in %s" % (task_id, self.path))
        m = TASK_RE.match(self.lines[t.line_no])
        indent, rest = m.group(1), m.group(3).rstrip()
        rest = rest.replace(" " + BLOCKED_UPSTREAM, "").rstrip()
        if state == "blocked-upstream":
            rest = rest + " " + BLOCKED_UPSTREAM
        if sha and not SHA_RE.search(rest):
            rest = rest + " done(+%s)" % sha
        self.lines[t.line_no] = "%s- [%s] %s" % (indent, STATE_GLYPH[state], rest)

    def set_blocked_by(self, task_id: str, blockers: List[str]) -> bool:
        """Add semantic blockers to a task row's `| blocked_by:` tag.

        Returns True when the row changed. Spec §7: the Judge's `blocks:` are
        blockers IN ADDITION to `depends_on`, and `eligible()` already refuses a
        task whose `blocked_by` names anything unfinished.
        """
        t = self.task(task_id)
        if t is None:
            return False
        merged = list(t.blocked_by)
        for b in blockers or []:
            if b and b not in merged:
                merged.append(b)
        if merged == list(t.blocked_by):
            return False
        line = self.lines[t.line_no]
        tag = " | blocked_by: %s" % ",".join(merged)
        m = re.search(r"\s*\|\s*blocked_by:\s*[^|]*", line)
        if m:
            line = line[:m.start()] + tag + line[m.end():]
        else:
            line = line.rstrip() + tag
        self.lines[t.line_no] = line
        return True

    def append_tasks(self, segment_name: str, rows: List[str]) -> None:
        """Insert rows at the end of the named segment's block."""
        seg = self._segment(segment_name)
        at = self._segment_end(seg)
        self.lines[at:at] = list(rows)

    def stamp_reviewed(self, segment_name: str, sha: str) -> None:
        """Write `Reviewed: <sha>` directly beneath the heading (plain text,
        never a `- [ ]` row — a task-shaped marker would corrupt the
        denominator every progress reading uses)."""
        seg = self._segment(segment_name)
        end = self._segment_end(seg)
        for i in range(seg.line_no + 1, end):
            if REVIEWED_RE.match(self.lines[i].strip()):
                self.lines[i] = "Reviewed: %s" % sha
                return
        self.lines[seg.line_no + 1:seg.line_no + 1] = ["Reviewed: %s" % sha]

    def next_ids(self, count: int) -> List[str]:
        """The next `count` free numeric ids, continuing the plan's numbering."""
        used = []
        for t in self.tasks():
            if t.id.startswith("T") and t.id[1:].isdigit():
                used.append(int(t.id[1:]))
        start = (max(used) + 1) if used else 1
        return ["T%d" % (start + i) for i in range(max(0, int(count)))]

    def split(self, task_id: str, sub_rows: List[str]) -> List[str]:
        """Mark the parent `[-] … split→T74,T75` and insert the renumbered sub rows.

        The harness owns numbering, not the caller: plan C's Judge proposes
        `changes.sub_rows` with ids it invented, and every one of them is
        rewritten to the next free NUMERIC id here. A lettered `T60a` would not
        match serve.py's `\\bT\\d+\\b`, so the dashboard would stop counting the
        work the moment a task was split.
        """
        t = self.task(task_id)
        if t is None:
            raise KeyError("no task %r in %s" % (task_id, self.path))
        ids = self.next_ids(len(sub_rows))
        rows = []
        for new_id, row in zip(ids, sub_rows):
            rows.append(_retitle(row, new_id).rstrip() + " | split_of: %s" % task_id)
        self.set_state(task_id, "skipped")
        self.lines[t.line_no] = self.lines[t.line_no] + " split→%s" % ",".join(ids)
        self.lines[t.line_no + 1:t.line_no + 1] = rows
        return ids

    def save(self) -> None:
        util.atomic_write(self.path, "\n".join(self.lines))
