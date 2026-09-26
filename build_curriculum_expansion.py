#!/usr/bin/env python3
"""Expand edu_sample/tables/course_catalog.csv with 医科/文科 majors.

The original course_catalog.csv (51 majors: 理科/机械大类/土建环) was built
from PDF tables by an earlier session with no surviving extraction script.
This script covers the two volumes added later -- 医科分册 (27 majors) and
文科分册 (42 majors) -- using extract_curriculum.process_volume(), which
parses each volume's table-of-contents to get per-major page ranges, then
pulls every table matching the canonical course-listing header
(['序号','课程名称','课程代码','课程性质','学分','学时'], or the header-less
variant used for pure elective menus).

Idempotent: majors already present in the target CSV are skipped, so
re-running after a partial merge (or after regenerating the base 51 some
other way) will not duplicate rows.

Disambiguation: 信息管理与信息系统 is offered by both 管理学院 (文科分册)
and 医药卫生管理学院 (医科分册) with mostly disjoint course codes (14 shared
out of ~57-79 each -- almost certainly just the campus-wide required
courses, e.g. 思想道德与法治). Since `major` is the only join key in this
table, merging them under one name would silently mix two curricula. They
are disambiguated with the offering college, consistent with this file's
existing convention of parenthesized variant suffixes (化学（强基计划实验
班） etc). See sources/major_catalog_full.json for the raw college->major
mapping (one row per college a major is offered under).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from extract_curriculum import process_volume

RENAME = {
    ("医科", "信息管理与信息系统"): "信息管理与信息系统（医药卫生管理学院）",
    ("文科", "信息管理与信息系统"): "信息管理与信息系统（管理学院）",
}

FIELDS = ["course_code", "course_name", "course_type", "credits", "total_hours", "major"]

VOLUMES = {
    "医科": "HUST-RESOURCES/培养方案/（定稿）2025级培养方案-医科分册.pdf",
    "文科": "HUST-RESOURCES/培养方案/（定稿）2025级培养方案-文科分册.pdf",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="edu_sample/tables/course_catalog.csv")
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    with catalog_path.open(encoding="utf-8") as f:
        existing_rows = list(csv.DictReader(f))
    existing_majors = set(r["major"] for r in existing_rows)
    print(f"existing majors: {len(existing_majors)}  existing rows: {len(existing_rows)}")

    new_rows = []
    for label, path in VOLUMES.items():
        rows, summary = process_volume(path)
        zero_row_majors = [m for m, n, _, _ in summary if n == 0]
        if zero_row_majors:
            raise AssertionError(f"{label}: majors with zero extracted rows: {zero_row_majors}")

        added_majors = set()
        for r in rows:
            major = RENAME.get((label, r["major"]), r["major"])
            if major in existing_majors:
                print(f"  skip (already present): {major}")
                continue
            added_majors.add(major)
            new_rows.append({
                "course_code": r["course_code"],
                "course_name": r["course_name"],
                "course_type": r["course_type"],
                "credits": r["credits"],
                "total_hours": r["total_hours"],
                "major": major,
            })
        print(f"{label}: {len(added_majors)} majors added, {sum(1 for r in new_rows if r['major'] in added_majors)} rows")
        existing_majors |= added_majors

    if not new_rows:
        print("nothing to add (already merged)")
        return 0

    all_rows = existing_rows + new_rows
    with catalog_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    all_majors = set(r["major"] for r in all_rows)
    print(f"\nwrote {len(all_rows)} rows / {len(all_majors)} majors to {catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
