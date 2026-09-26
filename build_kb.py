"""Rebuild edu_sample/kb_docs/ from the source 学籍管理细则 HTM.

Why this script exists
----------------------
The first conversion treated every <span> as a block element and inserted a
newline around it. But this document wraps each inline number in
`<span lang="EN-US">2</span>`, so every numeric value was torn out of its
sentence and left as an orphan paragraph:

    "一次申请最长休学时间为\n\n年，最多可申请\n\n次"   <- 2 and 2 lost

Measured damage: 43 of 94 clause numbers gone, numeric characters down from
386 to 156. The casualties were exactly the quantitative clauses (2/3, 1/2,
3/4 credit thresholds, year and attempt limits) that make the best anchors for
cross-surface tasks — the policy states the rule, the DB holds the numbers.

This version extracts text with inline spans flattened, so a clause stays one
paragraph. De-identification is applied to the flattened text.
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

# De-identification: this is a public document, but the workspace should not
# name a real institution.
SUBSTITUTIONS = [
    ("华中科技大学", "南湖大学"),
    ("华中大", "南湖大"),
    ("我校", "本校"),
    ("湖北省", "所在省"),
    (r"校本〔\d{4}〕\d+号", "校本规章"),
    (r"\(校本〔\d{4}〕\d+号\)", "（校本规章）"),
]

# Chapter heading -> output filename. Generic names only: nothing in a filename
# may encode which task uses it.
# Keys are the document's exact chapter titles (verified against the source,
# which has 12 chapters). Matching is exact after whitespace normalisation —
# fuzzy matching silently merged 学习纪律 and 附则 into the wrong files and gave
# course_and_credit_rules 32 clauses instead of its real 5.
CHAPTERS = [
    ("总则", "academic_records_general_provisions.md"),
    ("入学与注册", "enrollment_and_registration.md"),
    ("学习纪律", "academic_conduct.md"),
    ("课程与学分", "course_and_credit_rules.md"),
    ("选课、课程考核与成绩记载", "course_selection_and_grading.md"),
    ("转专业与转学", "major_transfer_and_school_transfer.md"),
    ("休学、复学与保留学籍", "leave_and_resumption.md"),
    ("学习年限与学籍处理", "study_duration_and_academic_standing.md"),
    ("毕业、结业、肄业与学位", "graduation_and_degree.md"),
    ("辅修和双学位", "minor_and_double_degree.md"),
    ("学业证书管理", "certificate_management.md"),
    ("附则", "supplementary_provisions.md"),
]

BLOCK_TAGS = ("p", "div", "tr", "br", "h1", "h2", "h3", "h4", "li")


def html_to_text(raw: str) -> str:
    """Flatten HTML to text, honouring only block-level boundaries.

    The bug being fixed: <span> must NOT introduce a line break. Only the tags
    in BLOCK_TAGS end a paragraph.
    """
    text = raw
    # Drop head/script/style wholesale.
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
    # Mark block boundaries with a sentinel before stripping tags.
    for tag in BLOCK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\x00", text)
    # Every remaining tag (span, font, a, b, ...) is inline: remove with no space.
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace(" ", " ")
    # Sentinels become paragraph breaks.
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\x00")]
    return "\n".join(ln for ln in lines if ln)


def deidentify(text: str) -> str:
    for pattern, repl in SUBSTITUTIONS:
        text = re.sub(pattern, repl, text) if "\\" in pattern or "〔" in pattern \
            else text.replace(pattern, repl)
    # Phone / QQ numbers, if any leak through.
    text = re.sub(r"(?<!\d)(\d{3}-?\d{8}|\d{11})(?!\d)", "（联系方式已移除）", text)
    return text


# Page chrome that sits *inside* the content container on the source site.
# The last chapter (附则) absorbs all of it, because there is no 第N章 heading
# after it to close the bucket — so 附则 ended up carrying the site footer:
# 上一篇/下一篇 navigation, the office address, and a Copyright line. Two
# reasons that matters beyond tidiness:
#   1. the footer leaked `bksy@hust.edu.cn`, which re-identifies the real
#      institution that every other substitution exists to hide;
#   2. 上一篇/下一篇 name *other policy documents* that are not in this KB,
#      which invites an agent to cite a source it cannot read.
PAGE_CHROME = re.compile(
    r"^(上一篇|下一篇)[:：]|^Copyright\s|^邮编[:：]|地址[:：].*办公室|"
    r"[\w.+-]+@[\w-]+\.[\w.]+"
)


def is_page_chrome(line: str) -> bool:
    return bool(PAGE_CHROME.search(line))


def split_chapters(lines: list[str]) -> dict[str, list[str]]:
    """Assign each paragraph to a chapter by the 第N章 headings."""
    chapter_names = [name for name, _ in CHAPTERS]
    buckets: dict[str, list[str]] = {name: [] for name in chapter_names}
    current: str | None = None
    for ln in lines:
        stripped = ln.strip()
        # A chapter heading looks like "第一章  总则"
        m = re.match(r"^第[一二三四五六七八九十]+章\s*(.+)$", stripped)
        if m:
            title = re.sub(r"\s+", "", m.group(1))
            if title not in buckets:
                raise ValueError(
                    f"未登记的章节标题 {title!r}；CHAPTERS 需与源文档一致"
                )
            current = title
            continue
        if current:
            if is_page_chrome(stripped):
                continue
            buckets[current].append(stripped)
    return buckets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="path to the 学籍管理细则 .htm")
    parser.add_argument("--out", default="edu_sample/kb_docs")
    parser.add_argument("--extra", action="append", default=[],
                        help="additional markdown to copy through verbatim")
    args = parser.parse_args()

    raw = Path(args.src).read_text(encoding="utf-8", errors="replace")
    text = deidentify(html_to_text(raw))
    lines = text.split("\n")
    buckets = split_chapters(lines)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for name, filename in CHAPTERS:
        body = buckets.get(name, [])
        if not body:
            written.append((filename, 0, 0, "EMPTY"))
            continue
        content = f"# {name}\n\n" + "\n\n".join(body) + "\n"
        (out_dir / filename).write_text(content, encoding="utf-8")
        clauses = len(re.findall(r"第[一二三四五六七八九十百]+条", content))
        digits = len(re.findall(r"\d", content))
        written.append((filename, clauses, digits, "ok"))

    total_clauses = sum(c for _, c, _, _ in written)
    total_digits = sum(d for _, _, d, _ in written)
    print(f"wrote {len([w for w in written if w[3]=='ok'])} docs to {out_dir}")
    for fn, c, d, status in written:
        flag = "" if status == "ok" else "  !! "
        print(f"  {flag}{fn:48s} 条={c:3d}  数字={d:4d}")
    print(f"  total: {total_clauses} clauses, {total_digits} digit chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
