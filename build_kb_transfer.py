"""Convert the 转专业 notice (转专业/2025.txt) into a KB policy document.

Why this belongs in the KB and not the DB
-----------------------------------------
`major_transfer_plan.csv` already holds the per-college quota table. What it
does NOT hold is the *rulebook* around it: who is barred from applying at all
(8 categories), the procedure and its deadlines, and the constraint that each
student may apply to exactly one receiving major. Those are qualitative rules,
which is exactly the KB/DB split this benchmark is built on — the policy states
the rule, the table holds the numbers.

Concretely, 资格限制 like "强基计划、中外合作办学专业录取的学生不得申请" cannot
be derived from any column of the quota table, and the quota table's numbers
cannot be derived from the notice. A task that needs both is cross-surface by
construction rather than by labelling.

Formatting problem this solves
------------------------------
The source is a single 1796-character line with runs of full-width spaces
standing in for every paragraph break — the web page's layout, flattened. Split
on the section markers (一、二、三…) and the numbered items, or the whole notice
becomes one unreadable KB chunk and retrieval returns the entire document for
any query that touches it.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

SUBSTITUTIONS = [
    ("华中科技大学", "南湖大学"),
    ("华中大", "南湖大"),
    ("我校", "本校"),
    ("湖北省", "所在省"),
]
# The notice carries the live HUB system URL and a browse counter; both
# re-identify the source site and neither is policy.
DROP_PATTERNS = [
    r"作者：\s*时间：[\d-]+\s*浏览：\d+",
    r"（网址：http[^）]*）",
    r"网址：http\S*",
]

SECTION = re.compile(r"(?=[一二三四五六七八九十]+、)")
NUMBERED = re.compile(r"(?=(?:\d+\.|（\d+）))")


def deidentify(text: str) -> str:
    for pattern in DROP_PATTERNS:
        text = re.sub(pattern, "", text)
    for needle, repl in SUBSTITUTIONS:
        text = text.replace(needle, repl)
    text = re.sub(r"(?<!\d)(\d{3}-?\d{8}|\d{11})(?!\d)", "（联系方式已移除）", text)
    return text


def segment(text: str) -> list[str]:
    # Full-width and regular whitespace runs are the only paragraph signal left.
    text = re.sub(r"[\s　]+", " ", text).strip()
    out: list[str] = []
    for section in SECTION.split(text):
        section = section.strip()
        if not section:
            continue
        for piece in NUMBERED.split(section):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="HUST-RESOURCES/转专业/2025.txt")
    ap.add_argument("--out", default="edu_sample/kb_docs/major_transfer_procedure.md")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    raw = Path(args.src).read_text(encoding="utf-8", errors="replace")
    paragraphs = segment(deidentify(raw))

    title = "本科生转专业工作办理规程"
    preamble = (
        "本文件为转专业工作的资格限定与办理程序规定，各院（系）的接收计划人数、"
        "报名条件与考核方案另见转专业信息一览表，二者不可相互推导。"
    )
    content = f"# {title}\n\n{preamble}\n\n" + "\n\n".join(paragraphs) + "\n"

    if args.dump:
        print(content)
        return 0

    Path(args.out).write_text(content, encoding="utf-8")
    clauses = len(re.findall(r"^\d+\.", content, re.M))
    print(f"  {Path(args.out).name:44s} 段={len(paragraphs):3d} 编号项={clauses:3d} "
          f"数字={len(re.findall(r'[0-9]', content)):4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
