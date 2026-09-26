#!/usr/bin/env python3
"""Generic 培养方案 PDF -> course rows extractor.

Strategy:
1. Parse the TOC page(s) to get an ordered list of (major_name, start_page).
   TOC lines look like "专业名 ······· 123" (dot leaders + page number).
   College header lines (no dot leader, no trailing number) are skipped but
   recorded for provenance.
2. For each major, scan its page range [start_page, next_start_page) and
   pull every table whose header matches the canonical 6-column format:
   ['序号','课程名称','课程代码','课程性质','学分','学时'].
3. Emit rows: course_code, course_name, course_type, credits, total_hours, major.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber

TOC_LINE = re.compile(r"^(.+?)[·.]{4,}\s*(\d+)\s*$", re.MULTILINE)


def norm_header(h):
    return [ (c or "").replace("\n", "").replace(" ", "").strip() for c in h ]


TARGET_HEADER = ["序号", "课程名称", "课程代码", "课程性质", "学分", "学时"]
ALT_HEADER = ["课程名称", "课程代码", "课程性质", "学分", "学时"]


def parse_toc(pdf, toc_pages):
    """Return list of (major_name, print_page_number) in TOC order."""
    entries = []
    for pi in toc_pages:
        text = pdf.pages[pi].extract_text() or ""
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("目") or line in ("·I·",) or re.match(r"^·[IVX]+·$", line):
                continue
            m = TOC_LINE.match(line)
            if m:
                name = m.group(1).strip()
                page = int(m.group(2))
                entries.append((name, page))
    return entries


def find_toc_pages(pdf, max_scan=8):
    pages = []
    started = False
    for i in range(max_scan):
        text = pdf.pages[i].extract_text() or ""
        if "目 录" in text or "目录" in text:
            started = True
        if started:
            pages.append(i)
            # Heuristic stop: page has no dot-leader lines and we already collected some
            if pages and not TOC_LINE.search(text) and len(pages) > 1:
                pages.pop()
                break
    return pages


def print_page_to_pdf_index(pdf, toc_pages):
    """Build a mapping from the document's printed page number (footer ·N·)
    to the actual 0-indexed pdfplumber page, by scanning footers."""
    mapping = {}
    for i, page in enumerate(pdf.pages):
        if i <= toc_pages[-1]:
            continue
        text = page.extract_text() or ""
        m = re.search(r"·\s*(\d+)\s*·\s*$", text.strip())
        if m:
            mapping[int(m.group(1))] = i
    return mapping


def extract_major_courses(pdf, start_idx, end_idx, major_name):
    rows = []
    seen_codes = set()
    for i in range(start_idx, min(end_idx, len(pdf.pages))):
        page = pdf.pages[i]
        try:
            tables = page.extract_tables()
        except Exception:
            continue
        for t in tables:
            if not t:
                continue
            header = norm_header(t[0])
            if header == TARGET_HEADER:
                offset = 1
            elif header == ALT_HEADER:
                offset = 0
            else:
                continue
            for r in t[1:]:
                cells = [ (c or "").strip() for c in r ]
                if len(cells) < 5 + offset:
                    continue
                if offset:
                    _, name, code, ctype, credits, hours = cells[:6]
                else:
                    name, code, ctype, credits, hours = cells[:5]
                if not code or not name:
                    continue
                if ctype.startswith("必修"):
                    ctype = "必修"
                elif ctype.startswith("选修"):
                    ctype = "选修"
                else:
                    continue
                key = (code, major_name)
                if key in seen_codes:
                    continue
                seen_codes.add(key)
                name = name.lstrip("*").strip()
                rows.append({
                    "course_code": code,
                    "course_name": name,
                    "course_type": ctype,
                    "credits": credits,
                    "total_hours": hours,
                    "major": major_name,
                })
    return rows


def process_volume(pdf_path, verbose=False):
    with pdfplumber.open(pdf_path) as pdf:
        toc_pages = find_toc_pages(pdf)
        toc = parse_toc(pdf, toc_pages)
        if verbose:
            print(f"TOC pages(0-idx): {toc_pages}, entries: {len(toc)}")
        page_map = print_page_to_pdf_index(pdf, toc_pages)
        if verbose:
            sample = list(page_map.items())[:3]
            print(f"page_map sample: {sample}, total mapped: {len(page_map)}")

        all_rows = []
        summary = []
        for idx, (major, printed_start) in enumerate(toc):
            if printed_start not in page_map:
                # fallback: find nearest mapped page >= printed_start
                candidates = [p for p in page_map if p >= printed_start]
                if not candidates:
                    if verbose:
                        print(f"  !! could not map start page for {major} ({printed_start})")
                    continue
                printed_start_use = min(candidates)
            else:
                printed_start_use = printed_start
            start_idx = page_map[printed_start_use]

            if idx + 1 < len(toc):
                next_printed = toc[idx + 1][1]
                candidates = [p for p in page_map if p >= next_printed]
                end_idx = page_map[min(candidates)] if candidates else len(pdf.pages)
            else:
                end_idx = len(pdf.pages)

            rows = extract_major_courses(pdf, start_idx, end_idx, major)
            n_req = sum(1 for r in rows if r["course_type"] == "必修")
            n_opt = sum(1 for r in rows if r["course_type"] == "选修")
            summary.append((major, len(rows), n_req, n_opt))
            all_rows.extend(rows)
        return all_rows, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rows, summary = process_volume(args.pdf, verbose=args.verbose)
    print(f"\n=== {args.pdf} ===")
    print(f"Majors found: {len(summary)}  Total course rows: {len(rows)}")
    for major, n, req, opt in summary:
        flag = "" if n > 0 else "  !! ZERO ROWS"
        print(f"  {major:45s} rows={n:3d} (必修={req} 选修={opt}){flag}")


if __name__ == "__main__":
    main()
