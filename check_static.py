"""Static leakage checks for the edu_sample workspace.

These checks are necessary but NOT sufficient. The project's central finding is
that all four original static checks passed while agents still answered
db+graph tasks from DB alone — only dynamic ablation (>=3 same-condition runs)
proves surface necessity. Keep that framing: a green run here means "no obvious
leak", never "the surfaces are necessary".

Two traps this script exists to avoid, both hit for real:

1. **Substring false positives.** Searching the KB for the discipline name
   理科 matches 管[理科]学与工程 — a completely unrelated word. A raw
   `in` test reported FAIL on clean data. Discipline names are therefore
   matched on word-ish boundaries, not as bare substrings.

2. **Same-name-different-scheme hits are not leaks.** key_disciplines.md is a
   degree-authorisation catalogue; 14 of its entries (化学, 法学, 建筑学,
   麻醉学 ...) are also undergraduate major names, and several (妇产科学,
   生物物理学) are also course names. These are different objects that share a
   label, which is why that document carries an explicit scope preamble. They
   are reported separately as INFO, not counted as failures.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

DEIDENT_TERMS = ["华中科技大学", "华中大", "我校", "湖北省", "hust", "HUST"]
DISCIPLINES = ["文科", "理科", "医科", "机械大类", "土建环"]
CHROME = re.compile(r"^(上一篇|下一篇)[:：]|^Copyright\s|[\w.+-]+@[\w-]+\.[\w.]+", re.M)

# Documents whose subject matter legitimately overlaps other surfaces by name.
# Hits here are reported but do not fail the run (see module docstring).
SCOPED_DOCS = {"key_disciplines.md"}


def discipline_hit(text: str, name: str) -> bool:
    """True only when `name` stands alone, not as a substring of a longer term.

    理科 inside 管理科学与工程 must not count.
    """
    for m in re.finditer(re.escape(name), text):
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        if not re.match(r"[一-鿿]", before or " ") and \
           not re.match(r"[一-鿿]", after or " "):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="edu_sample")
    args = ap.parse_args()
    root = Path(args.root)

    kb_files = sorted((root / "kb_docs").glob("*.md"))
    texts = {f.name: f.read_text(encoding="utf-8") for f in kb_files}
    rows = list(csv.DictReader((root / "tables" / "course_catalog.csv")
                               .open(encoding="utf-8")))
    codes = {r["course_code"] for r in rows}
    course_names = {r["course_name"] for r in rows}
    majors = {r["major"] for r in rows}
    graph = json.loads((root / "graph" / "surface_graph.json")
                       .read_text(encoding="utf-8"))

    failures: list[str] = []

    def report(label: str, bad: list) -> None:
        status = "PASS" if not bad else f"FAIL ({len(bad)})"
        print(f"  [{status:8s}] {label}")
        for b in bad[:6]:
            print(f"              {b}")
        if bad:
            failures.append(label)

    print(f"KB {len(kb_files)} docs | DB {len(rows)} rows / {len(majors)} majors "
          f"| graph {len(graph['nodes'])} nodes\n")

    print("KB leakage")
    report("课程代码不出现在 KB",
           [(n, c) for n, t in texts.items() for c in codes if c in t])
    report("文件名不编码 task_id",
           [f.name for f in kb_files if re.search(r"edu_?\d{3}|task", f.name, re.I)])
    report("去标识化无残留",
           [(n, x) for n, t in texts.items() for x in DEIDENT_TERMS if x in t])
    report("无网页噪声（导航/版权/邮箱）",
           [n for n, t in texts.items() if CHROME.search(t)])
    report("学科大类名不出现在 KB",
           [(n, d) for n, t in texts.items() for d in DISCIPLINES
            if discipline_hit(t, d)])

    print("\nDB shape")
    report("course_catalog 无派生列 discipline_category",
           ["discipline_category"] if rows and "discipline_category" in rows[0] else [])

    gm = {n["id"] for n in graph["nodes"] if n["type"] == "major"}
    report("图/DB 专业集一致", sorted((gm - majors) | (majors - gm)))

    print("\nINFO — 同名不同口径（非失败项，见文档口径声明）")
    for name, text in texts.items():
        if name not in SCOPED_DOCS:
            continue
        overlap_major = sorted(m for m in majors if m in text)
        overlap_course = sorted(c for c in course_names if len(c) >= 4 and c in text)
        print(f"  {name}: 与专业名重合 {len(overlap_major)}，与课程名重合 {len(overlap_course)}")

    print("\nNOTE 静态检查通过 ≠ surface 必要。必须跑 ≥3 次同条件消融才算验证。")
    if failures:
        print(f"\n{len(failures)} 项未通过")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
