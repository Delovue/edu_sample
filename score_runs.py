"""Score collected trajectories against task golds, and report stability.

Two things this script exists to get right:

**Numeric comparison, not substring matching.** A first pass used
`gold_number in answer_text` and flagged 6 of 20 correct answers as wrong:
the gold said "135.0 学分" and the agent wrote "135 学分", so the literal
"135.0" was absent. Tokens are parsed to values here — 135.0 == 135, and
ratios like 3/4 compare as Fractions. A false negative in a scorer is worse
than no scorer, because it sends you off editing tasks that were never broken.

**Surface routing and answer correctness are separate axes.** A task can route
through exactly the intended surfaces and still get the arithmetic wrong at the
end (long graph→DB aggregations do this), and that is a difficulty finding, not
a design failure. They are reported as two columns, never merged into one
pass/fail.

Stability needs >= 3 runs of the *same* configuration. Runs taken before and
after a data change are not replicates — comparing those once led to the wrong
conclusion that tasks were nondeterministic, when the data had changed
underneath them.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"\d+/\d+|\d+\.\d+|\d+")


def numeric_tokens(text: str) -> set[tuple[str, Any]]:
    """Comparable numeric values, so 135.0 and 135 are the same fact."""
    out: set[tuple[str, Any]] = set()
    for tok in TOKEN.findall(text or ""):
        if "/" in tok:
            try:
                out.add(("ratio", Fraction(tok)))
            except (ValueError, ZeroDivisionError):
                continue
        else:
            out.add(("num", float(tok)))
    return out


def fmt(value: tuple[str, Any]) -> str:
    kind, v = value
    if kind == "ratio":
        return str(v)
    return str(int(v)) if float(v).is_integer() else str(v)


def load(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            rows[r["task_id"]] = r
    return rows


def score_run(rows: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for tid, r in rows.items():
        required = set(r.get("metadata", {}).get("required_surfaces", []))
        used = set(r.get("surfaces_used", []))
        gold = numeric_tokens(r.get("reference_answer", ""))
        ans = numeric_tokens(r.get("answer") or "")
        out[tid] = {
            "routing_ok": required.issubset(used),
            "missing_surfaces": sorted(required - used),
            "used": sorted(used),
            "required": sorted(required),
            "gold_n": len(gold),
            "hit_n": len(gold & ans),
            "missing_values": sorted(gold - ans, key=str),
            "steps": len(r.get("tool_trace", [])),
            "failure": r.get("failure"),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", help="trajectory jsonl files (same config)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    scored = {Path(p).stem: score_run(load(Path(p))) for p in args.runs}
    labels = list(scored)
    tasks = sorted({t for s in scored.values() for t in s})

    print(f"runs: {len(labels)}  tasks: {len(tasks)}\n")
    header = f"{'task':9s} {'required':14s} {'routing':>9s} {'answer':>10s}  steps"
    print(header)
    print("-" * len(header))

    routing_stable = answer_stable = 0
    for tid in tasks:
        per = [scored[l][tid] for l in labels if tid in scored[l]]
        if not per:
            continue
        req = "+".join(per[0]["required"])
        r_ok = sum(1 for p in per if p["routing_ok"])
        a_ok = sum(1 for p in per if p["gold_n"] and p["hit_n"] == p["gold_n"])
        steps = ",".join(str(p["steps"]) for p in per)
        if r_ok == len(per):
            routing_stable += 1
        if a_ok == len(per):
            answer_stable += 1
        flag = "" if r_ok == len(per) else "  <-- routing"
        print(f"{tid:9s} {req:14s} {r_ok:>5d}/{len(per):<3d} {a_ok:>6d}/{len(per):<3d}  {steps}{flag}")
        if args.verbose:
            for lab, p in zip(labels, per):
                if p["missing_values"] or not p["routing_ok"]:
                    miss = ", ".join(fmt(v) for v in p["missing_values"])
                    print(f"            {lab}: used={'+'.join(p['used'])}"
                          + (f" 缺surface={p['missing_surfaces']}" if p["missing_surfaces"] else "")
                          + (f" 缺值={miss}" if miss else ""))

    n = len(tasks)
    print(f"\nrouting 全部run一致且正确: {routing_stable}/{n}")
    print(f"answer  全部run数值全中  : {answer_stable}/{n}")

    graph_tasks = [t for t in tasks
                   if "graph" in scored[labels[0]].get(t, {}).get("required", [])]
    gstable = [t for t in graph_tasks
               if all(scored[l][t]["routing_ok"] for l in labels if t in scored[l])]
    print(f"标 graph 的任务: {len(graph_tasks)}  每轮都真的走了 graph: {len(gstable)}")

    fails = [(l, t) for l in labels for t in scored[l]
             if scored[l][t]["failure"]]
    if fails:
        print(f"\n!! 有 failure 的运行: {fails}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
