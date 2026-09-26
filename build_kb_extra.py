"""Convert the two remaining HUST-RESOURCES policy pages into kb_docs markdown.

Why a sibling script instead of extending build_kb.py
-----------------------------------------------------
build_kb.py is shaped around one document: it splits on 第N章 headings and maps
each chapter to a fixed filename. The two pages here have no 章 structure at
all — the 选课管理办法 is 五章/十六条 in a different container, and 重点学科 is
pure heading+table content with no clauses. Forcing them through
split_chapters() would raise on the unregistered headings.

They also live in different HTML containers:
    选课管理办法  -> <div class="v_news_content">
    重点学科      -> <div id="vsb_content_4">
build_kb.py's extractor only knows the first, so the second silently yields
nothing.

Table cells matter here. 重点学科 stores every discipline name in a <td>, so td
and tr must be block boundaries or all 7+15+... names collapse into one line.
Empty cells are rendered as <br> and must be dropped, not kept as blank rows.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

# Same de-identification as build_kb.py. Kept as a literal copy rather than an
# import so each script can be run standalone.
SUBSTITUTIONS = [
    ("华中科技大学", "南湖大学"),
    ("华中大", "南湖大"),
    ("我校", "本校"),
    ("湖北省", "所在省"),
]
REGEX_SUBSTITUTIONS = [
    (r"本科生院〔\d{4}〕\d+号", "本科生院规章"),
    (r"校本〔\d{4}〕\d+号", "校本规章"),
]

BLOCK_TAGS = ("p", "div", "tr", "br", "h1", "h2", "h3", "h4", "li", "td", "th",
              "table", "tbody")


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    for tag in BLOCK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\x00", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\x00")]
    return "\n".join(ln for ln in lines if ln)


def deidentify(text: str) -> str:
    for needle, repl in SUBSTITUTIONS:
        text = text.replace(needle, repl)
    for pattern, repl in REGEX_SUBSTITUTIONS:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"(?<!\d)(\d{3}-?\d{8}|\d{11})(?!\d)", "（联系方式已移除）", text)
    return text


def slice_container(raw: str, start_marker: str, end_marker: str) -> str:
    i = raw.find(start_marker)
    if i < 0:
        raise ValueError(f"container not found: {start_marker!r}")
    seg = raw[i + len(start_marker):]
    j = seg.find(end_marker)
    if j < 0:
        raise ValueError(f"container end not found: {end_marker!r}")
    return seg[:j]


def extract(path: Path, start_marker: str, end_marker: str) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    inner = slice_container(raw, start_marker, end_marker)
    return deidentify(html_to_text(inner)).split("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-dir", default="HUST-RESOURCES")
    ap.add_argument("--out", default="edu_sample/kb_docs")
    ap.add_argument("--dump", action="store_true",
                    help="print extracted lines instead of writing files")
    args = ap.parse_args()

    src = Path(args.src_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    jobs = [
        {
            "name": "公共选修课选课管理办法",
            "path": src / "关于素质教育通识选修课程（公共选修课程）的选课管理办法.htm",
            "start": '<div class="v_news_content">',
            "end": "</div></div></div>",
            "title": "素质教育通识选修课程选课管理办法",
            "filename": "general_elective_selection_rules.md",
        },
        {
            "name": "重点学科",
            "path": src / "重点学科-华中科技大学.htm",
            "start": '<div id="vsb_content_4">',
            "end": "</div></div></div>",
            "title": "重点学科名录",
            "filename": "key_disciplines.md",
            # 口径声明。这里的"学科"是学位授权学科目录口径（一级/二级学科），
            # 与 DB 的本科专业名、图上的学科大类都不是同一套。实测 63 个重点
            # 学科名里有 14 个与 course_catalog.major 字面重名（化学、法学、
            # 建筑学、麻醉学……）。不写明口径，出题时 agent 会把"重点学科"
            # 当成本科专业去 DB 里查——这正是 v5 那次概念替换的失败模式。
            "preamble": (
                "本名录为学位授权学科目录口径的重点学科，按一级学科、二级学科分级，"
                "与本科招生专业目录、培养方案学科大类分册均非同一套口径，名称字面"
                "相同者不代表同一对象。"
            ),
        },
    ]

    for job in jobs:
        lines = extract(job["path"], job["start"], job["end"])
        if args.dump:
            print(f"===== {job['name']} ({len(lines)} lines) =====")
            print("\n".join(lines))
            print()
            continue
        content = f"# {job['title']}\n\n"
        if job.get("preamble"):
            content += job["preamble"] + "\n\n"
        content += "\n\n".join(lines) + "\n"
        target = out / job["filename"]
        target.write_text(content, encoding="utf-8")
        clauses = len(re.findall(r"第[一二三四五六七八九十百]+条", content))
        digits = len(re.findall(r"\d", content))
        print(f"  {job['filename']:44s} 段={len(lines):3d} 条={clauses:3d} 数字={digits:4d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
