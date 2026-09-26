"""Generate edu_sample/tasks/tasks.jsonl — answer-first.

Every gold answer here is computed from the imported data by the code below,
then the question is written around it. Never the other way round: an earlier
hand-written task asked about "手册里的晋升标准" and a grep showed the KB had no
such clause, so the task was unanswerable by construction.

Design rules enforced (see docs/surface_necessity_rules.md for the evidence):

- A task may only claim `graph` if the fact it needs is a discipline-level or
  cross-college set boundary. Those live only on the graph: `course_catalog`
  has neither a discipline nor a college column, and the admission plan names
  招生大类 ("数学类") while the catalogue names specific majors, with no column
  joining the two.
- Discipline membership must not be guessable from names. 土建环 spans
  建筑学 / 给排水科学与工程 / 交通工程 across 3 colleges with no shared
  substring, so a `LIKE` sweep plus domain priors cannot reconstruct it.
  College->major *is* guessable (Chinese universities share a department
  layout), so a task resting only on that is marked db+kb, not graph.
- Where a word in the question could name two different partitions (the
  graph's 学科大类 vs the plan table's 招生专业类), the question says which one
  it means. Without that qualifier the agent silently answered on the wrong
  partition, 0/6 runs touching the graph; with it, 6/6.
- No "which is the most/least X" phrasing unless a tie check passed. Every
  extremum below is verified tie=1 before being used.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


class Facts:
    """Everything the golds are derived from, loaded once."""

    def __init__(self, src: Path, db_path: Path):
        graph = json.loads((src / "graph" / "surface_graph.json").read_text(encoding="utf-8"))
        self.disc_of: dict[str, str] = {}
        self.disc_label: dict[str, str] = {}
        for n in graph["nodes"]:
            if n.get("type") == "major":
                self.disc_of[n["id"]] = n.get("discipline")
            elif n.get("type") == "discipline":
                self.disc_label[n.get("code")] = n["id"]
        self.includes: dict[str, list[str]] = collections.defaultdict(list)
        self.college_of: dict[str, str] = {}
        for e in graph["edges"]:
            if e["rel"] == "includes_major":
                self.includes[e["from"]].append(e["to"])
            elif e["rel"] == "offers_major":
                self.college_of[e["to"]] = e["from"]
        with (src / "tables" / "major_transfer_plan.csv").open(encoding="utf-8") as fh:
            self.plans = list(csv.DictReader(fh))
        self.db = sqlite3.connect(db_path)

    # -- helpers ---------------------------------------------------------
    def q(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.db.execute(sql, params).fetchall()

    def majors_of_discipline(self, label: str) -> list[str]:
        return sorted(self.includes[label])

    def colleges_of_discipline(self, label: str) -> list[str]:
        return sorted({self.college_of[m] for m in self.includes[label]
                       if m in self.college_of})

    def majors_of_college(self, college: str) -> list[str]:
        return sorted(m for m, c in self.college_of.items() if c == college)

    def common_required(self, majors: list[str]) -> tuple[int, float | None]:
        """Courses every one of `majors` requires, and their credit total."""
        ph = ",".join("?" * len(majors))
        row = self.q(
            f"""select count(*), round(sum(credits), 2) from (
                  select course_code, min(credits) credits from course_catalog
                  where major in ({ph}) and course_type='必修'
                  group by course_code having count(distinct major)=?)""",
            (*majors, len(majors)),
        )[0]
        return row[0], row[1]

    def required_total(self, major: str) -> tuple[int, float]:
        return self.q(
            "select count(*), round(sum(credits),2) from course_catalog "
            "where major=? and course_type='必修'", (major,))[0]

    def plan_for(self, college: str) -> dict[str, str] | None:
        return next((p for p in self.plans if p["college"] == college), None)

    def extremum(self, majors: list[str], highest: bool) -> tuple[str, int, float]:
        """Major with most/least required credits. Asserts no tie."""
        ph = ",".join("?" * len(majors))
        rows = self.q(
            f"""select major, count(*), round(sum(credits),2) c from course_catalog
                where course_type='必修' and major in ({ph})
                group by major order by c {'desc' if highest else 'asc'}""", majors)
        best = rows[0]
        ties = [r for r in rows if r[2] == best[2]]
        if len(ties) > 1:
            raise AssertionError(f"答案不唯一: {[t[0] for t in ties]} 同为 {best[2]} 学分")
        return best[0], best[1], best[2]


def build(f: Facts) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []

    # IDs are assigned in call order. They used to be passed in by hand and a
    # hardcoded "edu_004" silently overwrote the second loop-generated task,
    # leaving 19 tasks with edu_003 missing.
    def add(question: str, answer: str, surfaces: list[str],
            derivation: list[str], necessity: dict[str, str],
            difficulty: str, **extra: Any) -> None:
        meta: dict[str, Any] = {
            "required_surfaces": surfaces,
            "gold_derivation": derivation,
            "answer_type": "string",
            "difficulty": difficulty,
        }
        meta.update({f"{k}_necessity": v for k, v in necessity.items()})
        meta.update(extra)
        tasks.append({"task_id": f"edu_{len(tasks) + 1:03d}", "question": question,
                      "reference_answer": answer, "metadata": meta})

    DISC_QUALIFIER = ("以培养方案分册所划分的学科大类为准"
                      "（注意这与转专业名额表中按招生口径划分的“专业类”不是同一种分组）")

    # ================= graph + db + kb : discipline-level =================
    # 1-3: the same shape over all three disciplines with a real answer.
    for i, (label, seed) in enumerate(
            [("机械大类", "工业工程"), ("土建环", "土木工程")], start=1):
        ms = f.majors_of_discipline(label)
        cols = f.colleges_of_discipline(label)
        n, cr = f.common_required(ms)
        add(
            f"一名二年级学生现在读{seed}专业。按学籍管理规定他今年只能在哪个范围内申请转专业？"
            f"{DISC_QUALIFIER}，他所属的学科大类覆盖了多少个专业、这些专业分布在几个学院？"
            f"这些专业全体都共同要求的必修课有多少门、合计多少学分？",
            f"只能在学科大类内部申请转换专业（跨类需按规定降级学习）；{seed}属{label}，"
            f"该大类下 {len(ms)} 个专业，分布在 {len(cols)} 个学院（{'、'.join(cols)}）；"
            f"全体共同必修课 {n} 门，合计 {cr} 学分",
            ["graph", "db", "kb"],
            [f"kb: major_transfer_and_school_transfer.md 第四十七条——二年级学生可在学科大类内部申请转换专业",
             f"graph: {seed} 反向经 includes_major 定位到 {label}，再正向展开 {len(ms)} 个专业；"
             f"沿 offers_major 反查得 {len(cols)} 个学院",
             f"db: course_catalog 对这 {len(ms)} 个专业取 course_type=必修 的交集 = {n} 门 / {cr} 学分"],
            {"graph": f"学科大类成员边界只在图的 discipline 节点与 includes_major 边上。"
                      f"course_catalog 无 discipline/college 列；该集合也无法从名称反推——"
                      f"成员横跨 {len(cols)} 个学院且无共同子串",
             "db": "交集门数与学分合计只能在 course_catalog 上聚合",
             "kb": "“学科大类内部”这一范围限制只在学籍管理细则第四十七条"},
            "hard",
        )

    # 4: cross-discipline intersection — needs BOTH discipline sets.
    ma, mb = f.majors_of_discipline("土建环"), f.majors_of_discipline("机械大类")
    pa, pb = ",".join("?" * len(ma)), ",".join("?" * len(mb))
    row = f.q(
        f"""select count(*), round(sum(credits),2) from (
              select course_code, min(credits) credits from course_catalog
              where major in ({pa}) and course_type='必修'
              group by course_code having count(distinct major)=?
              intersect
              select course_code, min(credits) from course_catalog
              where major in ({pb}) and course_type='必修'
              group by course_code having count(distinct major)=?)""",
        (*ma, len(ma), *mb, len(mb)))[0]
    add(
        f"土建环与机械大类这两个学科大类，各自全体专业都共同要求的必修课里，"
        f"有多少门是两个大类共有的、合计多少学分？{DISC_QUALIFIER}。"
        f"两个大类分别包含多少个专业？",
        f"土建环 {len(ma)} 个专业、机械大类 {len(mb)} 个专业；两个大类各自的全体公共必修课中，"
        f"共有 {row[0]} 门重叠，合计 {row[1]} 学分",
        ["graph", "db"],
        ["graph: 分别展开两个 discipline 节点的 includes_major 边，得两组专业集合",
         "db: 对每组先求组内必修课交集，再对两个交集求交集"],
        {"graph": "需要同时知道两个大类的成员边界，这两个集合都只在图上",
         "db": "两级交集与学分合计只能在 course_catalog 上完成"},
        "hard",
    )

    # 5: discipline extremum — tie-checked.
    label = "机械大类"
    ms = f.majors_of_discipline(label)
    hi_m, hi_n, hi_c = f.extremum(ms, highest=True)
    lo_m, lo_n, lo_c = f.extremum(ms, highest=False)
    add(
        f"{DISC_QUALIFIER}。机械大类下的所有专业中，必修学分最高和最低的分别是哪个专业、"
        f"各多少学分、相差多少？这两个专业分属哪个学院？",
        f"最高为{hi_m}（{hi_c} 学分，{f.college_of[hi_m]}），"
        f"最低为{lo_m}（{lo_c} 学分，{f.college_of[lo_m]}），"
        f"相差 {round(hi_c - lo_c, 2)} 学分",
        ["graph", "db"],
        [f"graph: 展开 {label} 的 includes_major 得 {len(ms)} 个专业；再沿 offers_major 反查学院",
         "db: course_catalog 按专业聚合必修学分，取最高最低并相减（已验证无并列）"],
        {"graph": "大类成员集合与专业的学院归属都只在图上",
         "db": "学分聚合与差值只能在表上算"},
        "medium",
        uniqueness_check=f"最高 {hi_c} 与最低 {lo_c} 各自唯一，无并列",
    )

    # ================= db + kb : policy threshold x real credit base =====
    # 6-9: policy gives the ratio, DB gives the base. Neither alone suffices.
    for i, major in enumerate(["物理学", "化学", "土木工程", "建筑学"], start=6):
        n, cr = f.required_total(major)
        add(
            f"按学籍管理规定，规定学制第 2、3 学年度学分清理时，所获学分达不到培养计划累计总学分的"
            f"什么比例会被给黄牌警示、什么比例会被给红牌警示？"
            f"{major}专业的必修课共多少门、合计多少学分？",
            f"第 2、3 学年度清理时，未达累计总学分 3/4 者给黄牌警示，未达 2/3 者给红牌警示；"
            f"{major}专业必修课 {n} 门，合计 {cr} 学分",
            ["db", "kb"],
            ["kb: study_duration_and_academic_standing.md 第 2、3 学年度的双阈值条款（3/4 黄牌、2/3 红牌）",
             f"db: course_catalog 查 major={major} 且 course_type=必修 的门数与学分合计"],
            {"db": "学分基数只在 course_catalog；KB 只给比例不给任何专业的具体数值",
             "kb": "比例阈值与警示等级的对应只在学籍管理细则，表里没有任何政策字段"},
            "easy" if i <= 7 else "medium",
        )

    # 10-13: transfer plan detail x policy procedure.
    for i, college in enumerate(
            ["物理学院", "化学与化工学院", "航空航天学院", "船舶与海洋工程学院"], start=10):
        plan = f.plan_for(college)
        quota = re.sub(r"\s+", "", plan["receiving_majors_and_quota"])
        elig = re.sub(r"\s+", " ", plan["eligibility_requirement"]).strip()
        add(
            f"{college}今年的转专业接收计划是多少人，报名需要满足什么条件？"
            f"按学籍管理规定，转入院系公示拟接收名单的时间不得少于多少个工作日？",
            f"{quota}；报名条件：{elig}；公示时间不得少于 5 个工作日",
            ["db", "kb"],
            [f"db: major_transfer_plan 查 {college} 的 receiving_majors_and_quota 与 eligibility_requirement",
             "kb: major_transfer_and_school_transfer.md 第四十九条第 6 项——公示时间不得少于 5 个工作日"],
            {"db": "名额与报名条件只在 major_transfer_plan",
             "kb": "公示时长属办理程序，只在学籍管理细则"},
            "easy",
        )

    # 14-15: college-level aggregation. College->major IS name-guessable, so
    # these are deliberately labelled db+kb rather than claiming graph.
    for i, college in enumerate(["物理学院", "化学与化工学院"], start=14):
        ms = f.majors_of_college(college)
        n, cr = f.common_required(ms)
        plan = f.plan_for(college)
        add(
            f"{college}开设的专业里，所有专业都共同要求的必修课有多少门、合计多少学分？"
            f"该学院今年转专业的考核方式是什么？",
            f"{college}的 {len(ms)} 个专业全体共同必修课 {n} 门，合计 {cr} 学分；"
            f"考核方式：{re.sub(r'（联系方式已移除）', '', re.sub(r'\\s+', ' ', plan['assessment_method'])).strip()[:120]}",
            ["db", "kb"],
            [f"db: course_catalog 取该学院 {len(ms)} 个专业的必修课交集",
             f"db: major_transfer_plan 查 {college} 的 assessment_method",
             "kb: major_transfer_procedure.md 提供转专业考核的制定主体与总体流程"],
            {"db": "课程交集与考核方式都在表上",
             "kb": "考核方案由谁制定、流程如何走属政策，只在 KB"},
            "medium",
            note="学院→专业可由名称与领域常识反推（实测关键词召回 2/4），故不标 graph",
        )

    # 16-17: single-course sharing breadth (pure DB aggregate) x policy.
    rows = f.q("""select course_name, course_code, count(distinct major) c, course_type
                  from course_catalog
                  where substr(course_code,1,3) not in ('MAX','SFL','PHE','CHI')
                  group by course_code order by c desc limit 2""")
    top = rows[0]
    if rows[1][2] == top[2]:
        raise AssertionError(f"最多专业共享课并列: {top} / {rows[1]}")
    add(
        # The gold carries the course code, so the question has to ask for it.
        # It did not, and all 3 runs answered the substance correctly (课程名 /
        # 41 专业 / 必修) while omitting the code — scored 0/3 against a gold
        # that asked for something the question never requested.
        "在全校培养方案中，除思政、英语、体育、语文类公共课之外，被最多专业共同要求的课程是哪一门"
        "（请给出课程名称与课程代码）、被多少个专业要求、属必修还是选修？"
        "按规定，无故缺课累计超过课程教学时数的多少比例就不得参加该课程考核？",
        f"{top[0]}（{top[1]}），被 {top[2]} 个专业要求，{top[3]}；"
        f"无故缺课累计超过课程教学时数的 1/3 者不得参加考核，成绩以零分计",
        ["db", "kb"],
        ["db: course_catalog 按 course_code 聚合 count(distinct major)，排除 MAX/SFL/PHE/CHI 前缀",
         "kb: course_selection_and_grading.md 第三十四条——缺课超 1/3 不得参加考核"],
        {"db": "全校范围的课程共享度是表上的聚合量",
         "kb": "缺课比例与后果的规定只在细则"},
        "medium",
        uniqueness_check=f"第一 {top[2]} 专业，第二 {rows[1][2]} 专业，无并列",
    )
    add(
        "按规定，每门课程参加补考的次数最多几次、补考合格后成绩按多少分记载？"
        "另外，主修专业必修课出现几门以上不及格就可能被终止辅修或双学位资格？",
        "每门课程补考次数不超过 1 次，补考合格者成绩以 60 分记载；"
        "主修专业必修课出现 2 门以上不及格者，学校可终止其辅修或攻读双学位资格",
        ["kb"],
        ["kb: course_selection_and_grading.md 第三十九条（补考次数与记分）",
         "kb: minor_and_double_degree.md 第八十一条（2 门以上不及格）"],
        {"kb": "两条都是纯政策条款，不涉及任何专业或课程的具体数值"},
        "easy",
        note="单面任务，作为消融对照组：验证 kb-only 任务确实只需 KB",
    )

    # 18: pure DB, control group for the other direction.
    hi = f.q("""select major, count(*), round(sum(credits),2) c from course_catalog
                where course_type='必修' group by major order by c desc limit 2""")
    if hi[0][2] == hi[1][2]:
        raise AssertionError("全校必修学分最高并列")
    add(
        "全校 51 个专业中，必修学分最高的是哪个专业、多少学分、多少门课？",
        f"{hi[0][0]}，{hi[0][2]} 学分，{hi[0][1]} 门",
        ["db"],
        ["db: course_catalog 按 major 聚合必修学分取最大（已验证无并列）"],
        {"db": "纯表聚合"},
        "easy",
        note="单面任务，作为消融对照组：验证 db-only 任务确实只需 DB",
        uniqueness_check=f"第一 {hi[0][2]} 学分，第二 {hi[1][2]} 学分，无并列",
    )

    # 19-20: leave/duration policy x a discipline set (graph) x credits (db).
    label = "土建环"
    ms = f.majors_of_discipline(label)
    hi_m, hi_n, hi_c = f.extremum(ms, highest=True)
    add(
        f"因病申请休学，一次最长多久、最多可申请几次？最长学习年限比学制规定年限最多长几年？"
        f"{DISC_QUALIFIER}，土建环大类下必修学分最高的专业是哪个、多少学分？",
        f"因病休学一次最长 1 年，最多可申请 2 次；最长学习年限不得超过学制规定年限 2 年"
        f"（含休学与延长时间）；土建环大类下必修学分最高的是{hi_m}，{hi_c} 学分",
        ["graph", "db", "kb"],
        ["kb: leave_and_resumption.md 第五十二条（因病休学 1 年 / 2 次）",
         "kb: study_duration_and_academic_standing.md 第六十四条（最长年限 +2 年）",
         f"graph: 展开 {label} 的 includes_major 得 {len(ms)} 个专业",
         "db: course_catalog 在该集合内按专业聚合必修学分取最大"],
        {"graph": "土建环的成员边界只在图上，且跨 3 个学院无法从名称反推",
         "db": "学分聚合只能在表上",
         "kb": "休学年限与最长学习年限的规定只在细则"},
        "hard",
    )
    # A discipline whose full intersection is empty. The zero is the answer, and
    # locating why requires the college layer — 生命科学与技术学院 alone has 10
    # majors (incl. 留学生 / 中外合作办学 variants) with no shared required course.
    sci = f.majors_of_discipline("理科")
    sci_cols = f.colleges_of_discipline("理科")
    n_all, _ = f.common_required(sci)
    ph_sci = ",".join("?" * len(sci))
    near = f.q(
        f"""select count(*) from (
              select course_code from course_catalog
              where major in ({ph_sci}) and course_type='必修'
              group by course_code having count(distinct major)>=?)""",
        (*sci, len(sci) - 1))[0][0]
    culprits = []
    for c in sci_cols:
        mm = sorted(m for m in sci if f.college_of.get(m) == c)
        k, _ = f.common_required(mm)
        if k == 0:
            culprits.append((c, len(mm)))
    add(
        f"{DISC_QUALIFIER}。理科大类覆盖多少个专业、分布在几个学院？"
        f"这些专业全体都共同要求的必修课有多少门？如果把门槛放宽到"
        f"“被该大类中除一个专业以外的所有专业要求”，又有多少门？",
        f"理科大类下 {len(sci)} 个专业，分布在 {len(sci_cols)} 个学院"
        f"（{'、'.join(sci_cols)}）；全体共同要求的必修课为 {n_all} 门"
        f"（{culprits[0][0]}内部 {culprits[0][1]} 个专业本身就没有共同必修课）；"
        f"放宽到被其中 {len(sci) - 1} 个专业要求时有 {near} 门",
        ["graph", "db"],
        [f"graph: 展开理科 discipline 节点得 {len(sci)} 个专业，沿 offers_major 反查得 {len(sci_cols)} 个学院",
         f"db: 全体交集 having count(distinct major)={len(sci)} → {n_all} 门；"
         f"放宽为 >={len(sci) - 1} → {near} 门"],
        {"graph": "理科大类的成员边界与学院归属只在图上；本题答案为 0 门，"
                  "必须先确定完整成员集合才能确认这个 0 不是漏查导致的",
         "db": "两种门槛下的聚合都只能在 course_catalog 上做"},
        "hard",
        note="全体交集为 0 是真实事实，非数据缺失；放宽门槛的对比使答案可自证",
    )

    n, cr = f.common_required(ms)
    add(
        f"按规定，学生因违背学业和学术诚信规定受到几次及以上处分就不授予学士学位？"
        f"毕业时艺术教育类公共选修课至少要修满几个学分？"
        f"{DISC_QUALIFIER}，土建环大类全体专业共同要求的必修课合计多少学分？",
        f"因违背学业和学术诚信规定受到两次及以上处分者不授予学士学位；"
        f"艺术教育类公共选修课至少 2 个学分；"
        f"土建环大类 {len(ms)} 个专业全体共同必修课合计 {cr} 学分（{n} 门）",
        ["graph", "db", "kb"],
        ["kb: graduation_and_degree.md（两次及以上处分不授学位；第七十二条艺术类 2 学分）",
         f"graph: 展开 {label} 得 {len(ms)} 个专业",
         "db: course_catalog 求该集合的必修课交集与学分合计"],
        {"graph": "大类成员集合只在图上",
         "db": "交集与学分合计只能在表上算",
         "kb": "学位授予的限制条款与艺术类学分要求只在细则"},
        "hard",
    )
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="edu_sample")
    parser.add_argument("--db", default="DataMind/storage/edu_registrar/demo.db")
    parser.add_argument("--out", default="edu_sample/tasks/tasks.jsonl")
    args = parser.parse_args()

    f = Facts(Path(args.src), Path(args.db))
    tasks = build(f)

    ids = [t["task_id"] for t in tasks]
    if len(set(ids)) != len(ids):
        dupes = [i for i, c in collections.Counter(ids).items() if c > 1]
        raise AssertionError(f"重复 task_id: {dupes}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for t in sorted(tasks, key=lambda x: x["task_id"]):
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    by_surface = collections.Counter(
        "+".join(sorted(t["metadata"]["required_surfaces"])) for t in tasks)
    by_diff = collections.Counter(t["metadata"]["difficulty"] for t in tasks)
    print(f"wrote {len(tasks)} tasks to {out}")
    print(f"  surfaces: {dict(by_surface)}")
    print(f"  difficulty: {dict(by_diff)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
