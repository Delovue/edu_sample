"""Rebuild edu_sample/graph/surface_graph.json from the source tables.

Why this script exists
----------------------
The first version of this graph was generated ad-hoc from course_catalog.csv
alone. Running the ablation exposed the problem: every attribute on the graph
was a column of that CSV, so the graph carried no information the DB did not
already have, and the agent answered "graph-required" tasks with plain SQL.

The fix is not to weaken the DB. It is to put on the graph the one relation
the two tables genuinely cannot express:

    college --offers_major--> major

`major_transfer_plan` names *admission categories* ("数学类", "计算机类"),
`course_catalog` names *specific majors* ("数学与应用数学", "统计学"). No
column in either table joins the two. The mapping comes from a third source:
the table of contents of each 培养方案 volume, where majors are listed
underneath their college heading. That hierarchy is extracted separately into
sources/college_major_hierarchy.json (each row keeps its `source` provenance).

`discipline` likewise lives only on the major node. It was removed from
course_catalog because a curriculum volume assignment is a property of the
*major*, not of each of the 3606 course rows, and duplicating it there made
the graph redundant.

Node ids stay natural entity names (`数学与应用数学`, `PHY0191`) — nothing in
an identifier encodes which task it belongs to.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


# Discipline node ids use the real volume names the university publishes
# (理科分册 / 土建环分册 / ...), not the internal ASCII codes, so that nothing
# in an identifier is synthetic. The code is kept as a node attribute.
DISCIPLINE_LABEL = {
    "Science": "理科",
    "Mechanical": "机械大类",
    "CivilEnv": "土建环",
    "Humanities": "文科",
    "Medicine": "医科",
}


def load_hierarchy(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [r for r in rows if r.get("in_catalog")]


def load_catalog(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_plans(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build(src: Path) -> dict[str, Any]:
    hierarchy = load_hierarchy(src / "sources" / "college_major_hierarchy.json")
    catalog = load_catalog(src / "tables" / "course_catalog.csv")
    plans = load_plans(src / "tables" / "major_transfer_plan.csv")

    # major -> (college, discipline), from the curriculum TOC hierarchy
    major_info = {r["major"]: (r["college"], r["discipline"]) for r in hierarchy}

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(nid: str, **attrs: Any) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, **attrs}
        else:
            nodes[nid].update({k: v for k, v in attrs.items() if v is not None})

    # ---- disciplines, colleges, majors (hierarchy is the only source) ------
    # The discipline is a *node*, not just an attribute on each major. Keeping
    # it only as an attribute made "which majors are in this discipline?"
    # unanswerable: the graph tools search entities by name, not by attribute
    # value, so an agent could reach 土木工程.discipline but had no way to walk
    # back out to its 11 siblings — it resorted to guessing major names and got
    # 6 of 12. As a node with includes_major edges it is one ordinary hop.
    for major, (college, discipline) in sorted(major_info.items()):
        node(college, type="college")
        node(major, type="major", discipline=discipline)
        edges.append({"from": college, "to": major, "rel": "offers_major"})
        if discipline:
            label = DISCIPLINE_LABEL.get(discipline, discipline)
            node(label, type="discipline", code=discipline)
            edges.append({"from": label, "to": major, "rel": "includes_major"})

    # ---- courses and curriculum requirements ------------------------------
    skipped_majors: set[str] = set()
    for row in catalog:
        major, code = row["major"], row["course_code"]
        if major not in major_info:
            skipped_majors.add(major)
            continue
        node(code, type="course", name=row["course_name"])
        edges.append({
            "from": major, "to": code, "rel": "requires_course",
            "course_type": row["course_type"],
            "credits": float(row["credits"]) if row["credits"] else None,
        })

    # ---- transfer plans ---------------------------------------------------
    # The college on a plan row is the *receiving* college. Keeping the plan as
    # its own node (rather than an attribute) lets a query walk
    # college -> plan and college -> major in the same traversal.
    for i, row in enumerate(plans, 1):
        pid = f"transfer_plan_{i}"
        assessment = (row.get("assessment_method") or "").strip()
        node(pid, type="transfer_plan", assessment=assessment or None)
        college = (row.get("college") or "").strip()
        if college:
            node(college, type="college")
            edges.append({"from": college, "to": pid,
                          "rel": "publishes_transfer_plan"})

    graph = {"nodes": list(nodes.values()), "edges": edges}
    graph["_stats"] = {
        "majors_skipped_no_hierarchy": sorted(skipped_majors),
    }
    return graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="edu_sample")
    args = parser.parse_args()

    src = Path(args.src)
    graph = build(src)
    stats = graph.pop("_stats")

    out = src / "graph" / "surface_graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    import collections
    by_type = collections.Counter(n.get("type") for n in graph["nodes"])
    by_rel = collections.Counter(e["rel"] for e in graph["edges"])
    print(f"wrote {out}")
    print(f"  nodes: {len(graph['nodes'])}  {dict(by_type)}")
    print(f"  edges: {len(graph['edges'])}  {dict(by_rel)}")
    if stats["majors_skipped_no_hierarchy"]:
        print(f"  !! majors with no college in hierarchy "
              f"({len(stats['majors_skipped_no_hierarchy'])}): "
              f"{stats['majors_skipped_no_hierarchy'][:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
