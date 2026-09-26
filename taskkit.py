"""Helpers for writing answer-first tasks against the edu workspace.

Why this module exists
----------------------
The first 20 tasks were built with hand-written question strings and hand-written
gold answers, and that is where every defect in that batch came from:

  * edu_015's gold demanded a course code the question never asked for — three
    runs answered the substance correctly and scored 0/3 against it.
  * A question mentioned "手册里的晋升标准"; grep showed the KB has no such
    clause, so the task was unanswerable by construction.
  * edu_001's question had to be patched with a qualifier naming which partition
    "学科大类" meant, because without it every run silently answered on the
    transfer plan's 招生专业类 instead.

At ~200 tasks, hand-writing is no longer viable even if it were safe. So this
module is the answer-first machinery: a template is a *function* that computes
the gold from the data and emits the question around it, which makes the
question/gold mismatch above structurally impossible rather than merely
discouraged.

Three things every generated task must carry:

1. **A verifiable KB anchor.** `Facts.kb_anchor()` searches the indexed KB the
   same way an agent would and fails the build if the clause it wants is not
   retrievable. A policy number written into a question but absent from the KB
   is exactly the edu_015 class of defect, one layer down.

2. **A tie check on any extremum.** "Which major requires the most credits" is
   only answerable if the maximum is unique; the merge to 131 majors added
   eleven variants (机器人工程（智能机器人启明实验班）…) that can plausibly
   equal an existing major's total.

3. **A stated reason for every claimed surface.** Labels are earned from where
   the fact lives, never asserted. In particular graph is claimed only when the
   answer needs a discipline membership boundary — college→major is
   name-guessable and must not be labelled graph (measured: keyword recall 2/4).

Refusal
-------
`Template` construction takes an explicit `weight`; templates whose data
dependency turns out not to be unique raise `Unanswerable` at build time and the
caller drops them, rather than emitting a task with a non-unique gold.
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Unanswerable(RuntimeError):
    """Raised when a template's data dependency has no unique answer.

    Dropping the task is the correct response — a non-unique gold is a defect
    that review has to catch, and at 200 tasks review is exactly what is thin.
    """


# Shared across every discipline-level question. Without this exact qualifier
# the agent answers on the transfer plan's 招生专业类 (measured 0/6 runs
# touching the graph); with it, 6/6.
DISC_QUALIFIER = ("以培养方案分册所划分的学科大类为准"
                  "（注意这与转专业名额表中按招生口径划分的“专业类”不是同一种分组）")

PROTECTED_PREFIXES = ("MAX", "SFL", "PHE", "CHI")


@dataclass
class Facts:
    """Everything the golds are derived from, loaded once."""

    src: Path
    db_path: Path
    datamind: Path | None = None

    def __post_init__(self) -> None:
        graph = json.loads((self.src / "graph" / "surface_graph.json")
                           .read_text(encoding="utf-8"))
        self.disc_of: dict[str, str] = {}
        for n in graph["nodes"]:
            if n.get("type") == "major":
                self.disc_of[n["id"]] = n.get("discipline")
        self.includes: dict[str, list[str]] = collections.defaultdict(list)
        self.college_of: dict[str, str] = {}
        for e in graph["edges"]:
            if e["rel"] == "includes_major":
                self.includes[e["from"]].append(e["to"])
            elif e["rel"] == "offers_major":
                self.college_of[e["to"]] = e["from"]
        with (self.src / "tables" / "major_transfer_plan.csv").open(encoding="utf-8") as fh:
            import csv
            self.plans = list(csv.DictReader(fh))
        self.db = sqlite3.connect(self.db_path)
        self.kb_text = self._load_kb_text()
        self._kb_service = None

    # -- KB ---------------------------------------------------------------
    def _load_kb_text(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8")
                for p in (self.src / "kb_docs").glob("*.md")}

    def kb_anchor(self, doc: str, needle: str) -> str:
        """Assert `needle` is really in `doc`, and return the doc name.

        This is the edu_015 guard: a question may only cite a policy clause that
        the KB actually contains. Cheap, offline, and it runs at build time for
        all ~200 tasks at once.
        """
        if doc not in self.kb_text:
            raise Unanswerable(f"KB 无此文档: {doc}")
        if needle not in self.kb_text[doc]:
            raise Unanswerable(f"{doc} 中找不到锚点: {needle!r}")
        return doc

    def kb_search(self, query: str, top_k: int = 5) -> list[dict]:
        """Vector search, same path the agent's kb_search tool takes.

        Optional: only meaningful when the index is built and HF cache is
        reachable. Callers that need it should guard with `kb_available`.
        """
        if self._kb_service is None:
            import os
            os.environ.setdefault("DATAMIND__DATA__PROFILE", "edu_registrar")
            from datamind.capabilities.kb import build_kb_service
            from datamind.config import Settings
            self._kb_service = build_kb_service(Settings())
        import asyncio
        return asyncio.run(self._kb_service.search(query, top_k=top_k))

    # -- queries ----------------------------------------------------------
    def q(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.db.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> tuple:
        return self.q(sql, params)[0]

    def majors_of_discipline(self, label: str) -> list[str]:
        return sorted(self.includes[label])

    def colleges_of_discipline(self, label: str) -> list[str]:
        return sorted({self.college_of[m] for m in self.includes[label]
                       if m in self.college_of})

    def majors_of_college(self, college: str) -> list[str]:
        return sorted(m for m, c in self.college_of.items() if c == college)

    def common_required(self, majors: list[str]) -> tuple[int, float | None]:
        ph = ",".join("?" * len(majors))
        return self.one(
            f"""select count(*), round(sum(credits), 2) from (
                  select course_code, min(credits) credits from course_catalog
                  where major in ({ph}) and course_type='必修'
                  group by course_code having count(distinct major)=?)""",
            (*majors, len(majors)))

    def required_total(self, major: str) -> tuple[int, float]:
        return self.one(
            "select count(*), round(sum(credits),2) from course_catalog "
            "where major=? and course_type='必修'", (major,))

    def elective_total(self, major: str) -> tuple[int, float]:
        return self.one(
            "select count(*), round(sum(credits),2) from course_catalog "
            "where major=? and course_type='选修'", (major,))

    def plan_for(self, college: str) -> dict[str, str] | None:
        return next((p for p in self.plans if p["college"] == college), None)

    # -- uniqueness guards -------------------------------------------------
    def unique_extremum(self, majors: list[str], highest: bool,
                        course_type: str = "必修") -> tuple[str, int, float]:
        """Major with most/least credits in `majors`, asserting the answer is unique."""
        ph = ",".join("?" * len(majors))
        rows = self.q(
            f"""select major, count(*), round(sum(credits),2) c from course_catalog
                where course_type=? and major in ({ph})
                group by major order by c {'desc' if highest else 'asc'}""",
            (course_type, *majors))
        if not rows:
            raise Unanswerable(f"{majors[:3]}... 无 {course_type} 记录")
        best = rows[0]
        ties = [r for r in rows if r[2] == best[2]]
        if len(ties) > 1:
            raise Unanswerable(
                f"{course_type}学分极值并列: {[t[0] for t in ties]} 同为 {best[2]}")
        return best[0], best[1], best[2]

    def unique_top_course(self, exclude_prefixes: tuple[str, ...] = PROTECTED_PREFIXES,
                          where: str = "", params: tuple = ()) -> tuple[str, str, int, str]:
        """Most widely required course, asserting no tie for first place."""
        pre = ",".join(f"'{p}'" for p in exclude_prefixes)
        extra = f" and {where}" if where else ""
        rows = self.q(
            f"""select course_name, course_code, count(distinct major) c, course_type
                from course_catalog
                where substr(course_code,1,3) not in ({pre}){extra}
                group by course_code order by c desc limit 2""", params)
        if len(rows) < 2:
            raise Unanswerable("课程共享度排名不足 2 条")
        if rows[0][2] == rows[1][2]:
            raise Unanswerable(f"共享度第一并列: {rows[0][0]} / {rows[1][0]} 同为 {rows[0][2]}")
        return rows[0]

    def unique_top_shared(self, majors: list[str]) -> tuple[str, str, int]:
        """Course required by the most majors *within* a given major set."""
        ph = ",".join("?" * len(majors))
        rows = self.q(
            f"""select course_name, course_code, count(distinct major) c
                from course_catalog
                where major in ({ph}) and course_type='必修'
                group by course_code order by c desc limit 2""", majors)
        if len(rows) < 2:
            raise Unanswerable(f"{majors[:2]}... 课程共享排名不足")
        if rows[0][2] == rows[1][2]:
            raise Unanswerable(f"集合内共享度并列: {rows[0][0]} / {rows[1][0]}")
        return rows[0]

    def assert_unique(self, rows: list[tuple], why: str) -> tuple:
        if len(rows) < 2:
            raise Unanswerable(f"{why}: 结果不足 2 条，无法判定唯一性")
        if rows[0][-1] == rows[1][-1]:
            raise Unanswerable(f"{why}: {rows[0][0]} 与 {rows[1][0]} 并列")
        return rows[0]


@dataclass
class Task:
    question: str
    answer: str
    surfaces: list[str]
    derivation: list[str]
    necessity: dict[str, str]
    difficulty: str
    template: str
    extras: dict[str, Any] = field(default_factory=dict)

    def render(self, task_id: str) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "required_surfaces": self.surfaces,
            "gold_derivation": self.derivation,
            "answer_type": "string",
            "difficulty": self.difficulty,
            "template": self.template,
        }
        meta.update({f"{k}_necessity": v for k, v in self.necessity.items()})
        meta.update(self.extras)
        return {"task_id": task_id, "question": self.question,
                "reference_answer": self.answer, "metadata": meta}


def clean_ws(text: str) -> str:
    """Collapse whitespace runs the way the source tables store them."""
    return re.sub(r"\s+", " ", text).strip()