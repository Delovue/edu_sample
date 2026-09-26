"""Merge the remaining three 培养方案 volumes into course_catalog.csv.

What this fixes
---------------
The 51 majors that predate `extract_curriculum.py` were pulled with an earlier
extractor that silently dropped elective tables — the same defect later fixed
for the 医科/文科 volumes via `norm_header` (a second header variant without
序号 and with spaced characters) and prefix-normalised 课程性质 (选修（任选）
→ 选修). Those three volumes were never re-extracted, so their rows are still
truncated. Measured on the mechanical volume:

    工业工程                   必修26 选修1   ->  必修56 选修25
    机器人工程（科创实验班）    必修23 选修3   ->  必修48 选修52
    材料科学与工程             必修26 选修0   ->  必修52 选修26

Merge strategy: UNION, not replace
----------------------------------
The new extraction is essentially a superset — for the majors checked, the set
of "codes only in the old rows" was empty in two of three cases. But it was not
empty in the third: 材料科学与工程 kept MAX0072 (习近平新时代中国特色社会主义
思想概论) and PHY0201 (物理实验（下）) that the new pass missed, both of them
common courses that 117 and 40 other majors do carry — i.e. a page-break
truncation in the new pass, not a correction.

So neither side dominates, and replacing wholesale would lose real rows. Rows
are keyed on (major, course_code): new rows win on conflict (they carry the
complete 课程性质/学分), old-only rows are kept, and every kept row is
reported so the count is auditable rather than silent.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_curriculum import process_volume  # noqa: E402

FIELDS = ["course_code", "course_name", "course_type", "credits", "total_hours", "major"]

VOLUMES = {
    # 机械大类学科分册 and 机械大类学科分册-1 are byte-identical (same md5),
    # so only one is listed — extracting both would just be a no-op second pass.
    "理科": "HUST-RESOURCES/培养方案/（定稿）2025级培养方案-理科分册.pdf",
    "机械大类": "HUST-RESOURCES/培养方案/（定稿）2025级培养方案-机械大类学科分册.pdf",
    "土建环": "HUST-RESOURCES/培养方案/（定稿）2025级培养方案-土建环分册.pdf",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default="edu_sample/tables/course_catalog.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    catalog = Path(args.catalog)
    existing = list(csv.DictReader(catalog.open(encoding="utf-8")))
    index = {(r["major"], r["course_code"]): r for r in existing}
    before_majors = {r["major"] for r in existing}
    before_rows = len(existing)

    stats: list[tuple[str, str, int, int, int]] = []
    for volume, path in VOLUMES.items():
        rows, _ = process_volume(path)
        if not rows:
            raise AssertionError(f"{volume}: 抽取结果为空，拒绝写入")

        per_major: dict[str, list[dict]] = {}
        for row in rows:
            per_major.setdefault(row["major"], []).append(row)

        for major, mrows in sorted(per_major.items()):
            if not mrows:
                raise AssertionError(f"{volume}/{major}: 0 行，拒绝写入")
            old_codes = {c for (m, c) in index if m == major}
            new_codes = {r["course_code"] for r in mrows}
            for row in mrows:
                index[(major, row["course_code"])] = {k: row[k] for k in FIELDS}
            stats.append((volume, major, len(old_codes), len(new_codes),
                          len(old_codes - new_codes)))

    merged = sorted(index.values(), key=lambda r: (r["major"], r["course_code"]))
    kept_only_old = sum(s[4] for s in stats)

    print(f"专业 {len(before_majors)} -> {len({r['major'] for r in merged})}")
    print(f"行数 {before_rows} -> {len(merged)}")
    print(f"保留的仅旧有行: {kept_only_old}\n")

    by_volume = Counter(s[0] for s in stats)
    for volume in VOLUMES:
        subset = [s for s in stats if s[0] == volume]
        added = sum(1 for s in subset if s[2] == 0)
        print(f"  {volume}: {by_volume[volume]} 专业（新增 {added}），"
              f"保留仅旧有 {sum(s[4] for s in subset)} 行")

    grew = [s for s in stats if s[3] > s[2] > 0]
    print(f"\n  行数增长的已有专业: {len(grew)}")
    for _, major, old, new, _ in sorted(grew, key=lambda s: s[2] - s[3])[:8]:
        print(f"    {major}: {old} -> {new}")

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return 0

    with catalog.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(merged)
    print(f"\nwrote {catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
