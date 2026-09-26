"""Convert the two 国际学生 pages into KB policy documents.

Why these are worth adding
--------------------------
申请须知 states quantitative rules that are *tiered by field of study* — age
ceilings per degree level (本科 25 / 硕士 35 / 博士 40 / 普通进修 45 /
高级进修 50) and HSK level per discipline group (理工经管医 → HSK 4;
文学教育法学 and 临床医学研究生 → HSK 5). That shape — a policy that partitions
disciplines and attaches a threshold to each partition — is the same anchor
shape the 学分清理 double threshold provides, and there is no column anywhere in
the DB that encodes it.

Container note
--------------
These pages use a *third* container id: `<div id="vsb_content">`, distinct from
both `v_news_content` (学籍管理细则, 选课管理办法) and `vsb_content_4`
(重点学科). Three source pages from the same CMS, three different containers —
hence the explicit per-job marker rather than one shared regex.

De-identification is stricter here than elsewhere
-------------------------------------------------
This page carries a full contact block: street address, postcode, phone, fax,
and two live @hust.edu.cn addresses, plus an application-portal URL. The
standard 校名 substitutions do not touch any of them, and 邮编 430074 alone is
enough to re-identify the campus. They are dropped outright — none of it is
policy.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

SUBSTITUTIONS = [
    ("华中科技大学", "南湖大学"),
    # The footer uses a fourth abbreviation the other scripts never see.
    # Without it, "华科大国际教育" survives every other substitution.
    ("华科大", "南湖大"),
    ("华中大", "南湖大"),
    ("我校", "本校"),
    ("湖北省", "所在省"),
]

# Contact / navigation chrome. Dropped whole-line.
DROP_LINE = re.compile(
    r"^(地\s*址|邮\s*编|电\s*话|传\s*真|邮\s*箱|友情链接)\s*[:：]?"
    r"|[\w.+-]+@[\w-]+\.[\w.]+"
    r"|^(中华人民共和国教育部|国家留学网|中外语言交流合作中心)$"
    r"|武汉市|洪山区|珞喻路"
    r"|^关注我们$|^Copyright\s|版权所有|国际教育$"
)

# A bare URL must not take its whole line with it: the application procedure's
# step (1) is "登录在线申请系统：<url>", so dropping the line silently deleted
# the first step and left the KB with a procedure starting at (2). Strip the
# URL, keep the instruction.
URL = re.compile(r"https?://\S+")

BLOCK_TAGS = ("p", "div", "tr", "br", "h1", "h2", "h3", "h4", "li", "td", "th",
              "table", "tbody")


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    for tag in BLOCK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\x00", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\x00")]
    return [ln for ln in lines if ln]


def deidentify(line: str) -> str:
    line = URL.sub("（网址略）", line)
    for needle, repl in SUBSTITUTIONS:
        line = line.replace(needle, repl)
    line = re.sub(r"(?<!\d)(\d{3}-?\d{8}|\d{11})(?!\d)", "（联系方式已移除）", line)
    return line.strip()


def slice_container(raw: str, start: str, end: str) -> str:
    i = raw.find(start)
    if i < 0:
        raise ValueError(f"container not found: {start!r}")
    seg = raw[i + len(start):]
    j = seg.find(end)
    return seg[:j] if j >= 0 else seg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-dir", default="HUST-RESOURCES")
    ap.add_argument("--out", default="edu_sample/kb_docs")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    src, out = Path(args.src_dir), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Only 申请须知 is converted. 国际学生招生项目.htm is deliberately NOT a KB
    # document: its content is almost entirely a 学生类别 × 学科 × 学制 table,
    # and this CMS emits one <td> per cell with no row grouping, so flattening
    # it to paragraphs yields a column of orphan cells —
    #     本科生 / 理学、工学、... / 4-5 年 / 研究生 / 硕士 - 2年（英文授课）
    # — where nothing ties 4-5 年 to the row it came from. A reader (or an
    # agent) cannot recover the mapping, and a KB doc that looks authoritative
    # while being unreadable is worse than no doc. If those figures are wanted,
    # they belong in a table on the DB surface, parsed row-wise from the <tr>s.
    jobs = [
        {
            "path": src / "国际学生申请须知.htm",
            "title": "国际学生申请条件与流程",
            "filename": "international_admission_requirements.md",
            "preamble": "本文件规定国际学生各层次项目的学历、年龄与语言要求及申请流程。",
        },
    ]

    for job in jobs:
        raw = job["path"].read_text(encoding="utf-8", errors="replace")
        inner = slice_container(raw, '<div id="vsb_content">', "</div></div></div>")
        lines = [deidentify(ln) for ln in html_to_text(inner)
                 if not DROP_LINE.search(ln)]
        content = (f"# {job['title']}\n\n{job['preamble']}\n\n"
                   + "\n\n".join(lines) + "\n")
        if args.dump:
            print(content)
            print("-" * 60)
            continue
        (out / job["filename"]).write_text(content, encoding="utf-8")
        print(f"  {job['filename']:44s} 段={len(lines):3d} "
              f"数字={len(re.findall(r'[0-9]', content)):4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
