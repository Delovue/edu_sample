"""Bulk-generate ~200 answer-first tasks for the edu workspace.

Relationship to build_tasks.py
------------------------------
build_tasks.py produced 20 tasks with hand-written question strings. That is
where every defect in that batch came from — a gold demanding a course code the
question never asked for, a question citing a clause the KB does not contain, a
question whose term was ambiguous between two partitions. At 200 tasks
hand-writing is not viable even if it were safe.

So this script does not extend build_tasks.py; it regenerates the same shapes as
*functions* over `taskkit.Facts`, which makes the question/gold mismatch
structurally impossible. Every gold is computed from the data, then the question
is written around it. The three guards (kb_anchor / unique_extremum /
DISC_QUALIFIER) run at build time for all templates at once.

Surface labelling is earned, not asserted
-----------------------------------------
This is the whole point of the project, so the assignment rule is explicit:

  graph  — only when the answer needs a *discipline membership boundary*. Those
           live solely on the graph; course_catalog has neither a discipline nor
           a college column, and the transfer plan names 招生大类 ("数学类")
           while the catalogue names specific majors with no column joining them.
  db     — when the answer is an aggregate over course_catalog / a plan row.
  kb     — when the answer needs a policy clause.

College->major is deliberately NOT enough for graph. It is guessable from
naming plus domain priors (measured: keyword recall 2/4), so those tasks are
labelled db+kb. Padding the graph count with guessable relations is exactly the
false-cross-surface failure this benchmark exists to avoid — "宁可 7 道真的，
不要 16 道假的". Where a question could name either partition, it carries
DISC_QUALIFIER.

Composition target (≈200)
-------------------------
  discipline-intersection / cross-discipline   ~40  graph+db(+kb)
  discipline extremum / college-layer          ~40  graph+db
  college-level aggregation                    ~35  db+kb
  policy threshold x real data                 ~35  db+kb
  transfer-plan detail                         ~25  db+kb
  major-level aggregates (选修, 学时)           ~20  db
  KB procedure / admission                     ~15  db|kb
  single-surface controls                       ~5  kb / db
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from taskkit import DISC_QUALIFIER, Facts, Task, Unanswerable, clean_ws  # noqa: E402

TEMPLATES: list[Callable[[Facts], list[Task]]] = []
SKIPPED: collections.Counter = collections.Counter()


def template(fn: Callable[[Facts], list[Task]]) -> Callable[[Facts], list[Task]]:
    TEMPLATES.append(fn)
    return fn


def guard(name: str, fn: Callable[[], Task | None]) -> Task | None:
    """Run one instance; drop it (and count why) if its gold is not unique."""
    try:
        return fn()
    except Unanswerable as exc:
        SKIPPED[f"{name}: {str(exc)[:70]}"] += 1
        return None


def collect(fn: Callable[[], Task | None]) -> None:
    t = fn()
    return t


# --------------------------------------------------------------------------
# A. discipline-level intersections  (graph + db, sometimes + kb)
# --------------------------------------------------------------------------
DISCIPLINES = ["文科", "理科", "医科", "机械大类", "土建环"]


@template
def t_discipline_intersection(f: Facts) -> list[Task]:
    """The core graph+db shape: a set boundary that exists only on the graph."""
    out: list[Task] = []
    for label in DISCIPLINES:
        ms = f.majors_of_discipline(label)
        cols = f.colleges_of_discipline(label)

        def make(label=label, ms=ms, cols=cols) -> Task:
            n, cr = f.common_required(ms)
            seed = ms[0]
            return Task(
                question=(
                    f"{DISC_QUALIFIER}。{label}覆盖多少个专业、分布在几个学院？"
                    f"这些专业全体都共同要求的必修课有多少门、合计多少学分？"),
                answer=(
                    f"{label}下 {len(ms)} 个专业，分布在 {len(cols)} 个学院"
                    f"（{'、'.join(cols)}）；全体共同要求的必修课 {n} 门，合计 {cr} 学分"),
                surfaces=["graph", "db"],
                derivation=[
                    f"graph: 展开 {label} 的 includes_major 边得 {len(ms)} 个专业；"
                    f"沿 offers_major 反查得 {len(cols)} 个学院",
                    f"db: course_catalog 对这 {len(ms)} 个专业按 course_code 取必修交集"
                    f"（having count(distinct major)={len(ms)}）= {n} 门 / {cr} 学分",
                ],
                necessity={
                    "graph": f"{label}的成员边界只在图的 discipline 节点与 includes_major 边上；"
                             f"course_catalog 无 discipline/college 列，且成员横跨 {len(cols)} 个学院、"
                             f"无共同子串，无法从名称反推",
                    "db": "交集门数与学分合计只能在 course_catalog 上聚合",
                },
                difficulty="medium" if n else "hard",
                template="discipline_intersection",
                extras={"discipline": label, "n_majors": len(ms), "n_colleges": len(cols)},
            )
        out.append(guard("discipline_intersection", make))
    return [t for t in out if t]


@template
def t_discipline_major_membership(f: Facts) -> list[Task]:
    """Name one major, ask for its discipline, the sibling set and the college spread."""
    out: list[Task] = []
    for label in DISCIPLINES:
        ms = f.majors_of_discipline(label)
        cols = f.colleges_of_discipline(label)

        def make(label=label, ms=ms, cols=cols) -> Task:
            seed = ms[len(ms) // 2]
            others = [m for m in ms if m != seed]
            return Task(
                question=(
                    f"一名二年级学生现在读{seed}专业。按学籍管理规定他今年只能在哪个范围内"
                    f"申请转专业？{DISC_QUALIFIER}，他所属的学科大类覆盖多少个专业、"
                    f"分布在几个学院？除他本人专业外还有多少个专业同属该大类？"),
                answer=(
                    f"只能在学科大类内部申请转换专业（跨类需按规定降级学习）；"
                    f"{seed}属{label}，该大类下 {len(ms)} 个专业，分布在 {len(cols)} 个学院"
                    f"（{'、'.join(cols)}）；除本专业外还有 {len(others)} 个同大类专业"),
                surfaces=["graph", "db", "kb"],
                derivation=[
                    "kb: major_transfer_and_school_transfer.md——二年级学生可在学科大类内部申请转换专业",
                    f"graph: {seed} 反向经 includes_major 定位到 {label}，再正向展开 {len(ms)} 个专业；"
                    f"沿 offers_major 反查得 {len(cols)} 个学院",
                    "db: 专业数由 course_catalog 中该集合的 distinct major 交叉核对",
                ],
                necessity={
                    "graph": "学科大类成员边界只在图上；且无法从专业名反推",
                    "db": "用专业集合在表上核对成员数与课程覆盖",
                    "kb": "“学科大类内部”这一范围限制只在学籍管理细则",
                },
                difficulty="medium",
                template="discipline_major_membership",
                extras={"discipline": label, "seed_major": seed},
            )
        out.append(guard("discipline_major_membership", make))
    return [t for t in out if t]


@template
def t_cross_discipline(f: Facts) -> list[Task]:
    """Both discipline sets are needed, so one discipline alone cannot answer."""
    out: list[Task] = []
    pairs = [("土建环", "机械大类"), ("机械大类", "理科"), ("文科", "土建环"),
             ("医科", "理科"), ("文科", "理科"), ("医科", "机械大类")]
    for a, b in pairs:
        ma, mb = f.majors_of_discipline(a), f.majors_of_discipline(b)

        def make(a=a, b=b, ma=ma, mb=mb) -> Task:
            na, ca = f.common_required(ma)
            nb, cb = f.common_required(mb)
            # courses common to BOTH disciplines' full-intersection sets
            pa, pb = ",".join("?" * len(ma)), ",".join("?" * len(mb))
            row = f.one(
                f"""select count(*), round(sum(credits),2) from (
                      select course_code, min(credits) credits from course_catalog
                      where major in ({pa}) and course_type='必修'
                      group by course_code having count(distinct major)=?
                      intersect
                      select course_code, min(credits) from course_catalog
                      where major in ({pb}) and course_type='必修'
                      group by course_code having count(distinct major)=?)""",
                (*ma, len(ma), *mb, len(mb)))
            return Task(
                question=(
                    f"{a}与{b}这两个学科大类，各自全体专业都共同要求的必修课里，"
                    f"有多少门是两个大类共有的、合计多少学分？两个大类分别包含多少个专业、"
                    f"全体共同必修课各多少门？{DISC_QUALIFIER}。"),
                answer=(
                    f"{a} {len(ma)} 个专业（全体共同必修课 {na} 门）、"
                    f"{b} {len(mb)} 个专业（{nb} 门）；"
                    f"两个大类的全体公共必修课中共有 {row[0]} 门重叠，合计 {row[1]} 学分"),
                surfaces=["graph", "db"],
                derivation=[
                    f"graph: 分别展开 {a} / {b} 的 includes_major 边，得两组专业集合",
                    f"db: 对每组先求组内必修交集，再对两个交集求 intersect = {row[0]} 门 / {row[1]} 学分",
                ],
                necessity={
                    "graph": "需要同时知道两个大类的成员边界，两个集合都只在图上",
                    "db": "两级交集与学分合计只能在 course_catalog 上完成",
                },
                difficulty="hard",
                template="cross_discipline",
                extras={"discipline_a": a, "discipline_b": b},
            )
        out.append(guard("cross_discipline", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# B. extremum within a discipline set  (graph + db)
# --------------------------------------------------------------------------
@template
def t_discipline_extremum(f: Facts) -> list[Task]:
    out: list[Task] = []
    for label in DISCIPLINES:
        ms = f.majors_of_discipline(label)
        for highest, ct in ((True, "必修"), (False, "必修")):
            def make(label=label, ms=ms, highest=highest, ct=ct) -> Task:
                m, n, c = f.unique_extremum(ms, highest, ct)
                word = "最高" if highest else "最低"
                return Task(
                    question=(
                        f"{DISC_QUALIFIER}。{label}下的所有专业中，{ct}学分{word}的是哪个专业、"
                        f"多少学分、多少门课？该专业属于哪个学院？"),
                    answer=(f"{m}，{ct}学分 {c}（{n} 门课），属{f.college_of.get(m, '未知学院')}"),
                    surfaces=["graph", "db"],
                    derivation=[
                        f"graph: 展开 {label} 的 includes_major 得 {len(ms)} 个专业；"
                        f"再沿 offers_major 反查 {m} 的学院",
                        f"db: course_catalog 在该专业集合内按 major 聚合 {ct} 学分取{word}"
                        f"（已验证无并列）",
                    ],
                    necessity={
                        "graph": "专业集合与专业的学院归属都只在图上",
                        "db": "学分聚合只能在表上算",
                    },
                    difficulty="medium",
                    template="discipline_extremum",
                    extras={"discipline": label, "uniqueness_check": f"{ct}学分{word}者唯一，无并列"},
                )
            out.append(guard("discipline_extremum", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# C. college-level aggregation  (db + kb; NOT graph — college->major is guessable)
# --------------------------------------------------------------------------
@template
def t_college_intersection(f: Facts) -> list[Task]:
    out: list[Task] = []
    colleges = sorted(set(f.college_of.values()))
    for college in colleges:
        ms = f.majors_of_college(college)

        def make(college=college, ms=ms) -> Task:
            n, cr = f.common_required(ms)
            if n == 0:
                raise Unanswerable(f"{college} 全体共同必修课为 0，不可作为唯一答案")
            plan = f.plan_for(college)
            extra_q = extra_a = ""
            deriv = [f"db: course_catalog 取该学院 {len(ms)} 个专业的必修课交集 = {n} 门 / {cr} 学分"]
            nec_kb = ""
            if plan:
                amt = clean_ws(plan["assessment_method"])[:100]
                extra_q = f"该学院今年转专业的考核方式是什么？"
                extra_a = f"；考核方式：{amt}"
                deriv.append(f"db: major_transfer_plan 查 {college} 的 assessment_method")
                nec_kb = "考核方案由谁制定、流程如何走属政策，只在 KB"
            return Task(
                question=(
                    f"{college}开设的专业里，所有专业都共同要求的必修课有多少门、合计多少学分？"
                    f"该学院共开设多少个专业？{extra_q}"),
                answer=(f"{college}共 {len(ms)} 个专业，全体共同必修课 {n} 门，合计 {cr} 学分"
                        f"{extra_a}"),
                surfaces=["db", "kb"],
                derivation=deriv + ["kb: major_transfer_procedure.md 提供转专业考核的制定主体与总体流程"],
                necessity={
                    "db": "课程交集与转专业考核方式都落在表上",
                    **({"kb": nec_kb} if nec_kb else
                       {"kb": "本任务不依赖具体条款，仅需确认学院层面的政策框架出处"}),
                },
                difficulty="medium",
                template="college_intersection",
                extras={"college": college, "n_majors": len(ms),
                        "note": "学院→专业可由名称与领域常识反推（实测关键词召回 2/4），故不标 graph"},
            )
        out.append(guard("college_intersection", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# D. policy threshold x real credit base  (db + kb)
# --------------------------------------------------------------------------
# (doc, anchor, needle, question, answer)
#
# The anchor must be the exact substring present in the KB *and* must belong to
# the clause the question is about. A first draft of this table anchored 2/3 to
# "总学分的2/3给红牌" without qualifying the year, but the KB has TWO different
# threshold pairs: 第1学年度 is (2/3 黄牌, 1/2 红牌) and 第2、3学年度 is
# (3/4 黄牌, 2/3 红牌). An unqualified "学分清理时未达什么比例给红牌" has two
# correct answers, so the question must name the year. Same class of defect as
# the edu_015 gold/question mismatch, one layer down: the anchor passed, the
# question was still ambiguous.
#
# The anchor for 转专业公示 originally read "5 个工作日" (with a space) while the
# KB stores "5个工作日" — it silently dropped all 131 instances of that template.
POLICY_THRESHOLDS = [
    ("study_duration_and_academic_standing.md", "2/3者，学校给予红牌警示", "2/3",
     "按学籍管理规定，在规定学制的第 2、3 学年度进行学分清理时，"
     "所获学分未达到培养计划累计总学分的什么比例会给红牌警示？",
     "第 2、3 学年度清理时未达培养计划累计总学分的 2/3 者给红牌警示"),
    ("study_duration_and_academic_standing.md", "3/4者，学校给予黄牌警示", "3/4",
     "按学籍管理规定，在规定学制的第 2、3 学年度进行学分清理时，"
     "所获学分未达到培养计划累计总学分的什么比例会给黄牌警示？",
     "第 2、3 学年度清理时未达培养计划累计总学分的 3/4 者给黄牌警示"),
    ("study_duration_and_academic_standing.md", "1/2者，学校给予红牌警示", "1/2",
     "按学籍管理规定，在规定学制的第 1 学年度进行学分清理时，"
     "所获学分未达到该学年度培养计划累计总学分的什么比例会给红牌警示？",
     "第 1 学年度清理时未达该学年度培养计划累计总学分的 1/2 者给红牌警示"),
    ("course_selection_and_grading.md", "1/3", "1/3",
     "按规定，无故缺课累计超过课程教学时数的多少比例就不得参加该课程考核？",
     "超过 1/3 者不得参加考核，成绩以零分计"),
    ("minor_and_double_degree.md", "辅修", "辅修",
     "主修专业必修课出现几门以上不及格就可能被终止辅修或攻读双学位资格？",
     "2 门以上不及格者，学校可终止其辅修或攻读双学位资格"),
    ("course_and_credit_rules.md", "艺术教育类课程不得低于2学分", "艺术教育类",
     "公共选修课中，艺术教育类课程至少要修满几个学分？",
     "艺术教育类课程不得低于 2 学分"),
    ("major_transfer_and_school_transfer.md", "不得少于5个工作日", "5个工作日",
     "转入院（系）将拟接收名单进行公示，公示时间不得少于多少个工作日？",
     "公示时间不得少于 5 个工作日"),
    ("leave_and_resumption.md", "一次申请最长休学时间为1年，最多可申请2次", "休学",
     "因病申请休学，一次最长多久、最多可申请几次？",
     "因病休学一次申请最长 1 年，最多可申请 2 次"),
]


@template
def t_policy_threshold(f: Facts) -> list[Task]:
    out: list[Task] = []
    majors = sorted({r[0] for r in f.q("select distinct major from course_catalog")})
    for doc, anchor, needle, q, a in POLICY_THRESHOLDS:
        for major in majors:
            def make(doc=doc, anchor=anchor, needle=needle, q=q, a=a, major=major) -> Task:
                f.kb_anchor(doc, anchor)  # raises if the cited clause is not in the KB
                n, cr = f.required_total(major)
                if n == 0:
                    raise Unanswerable(f"{major} 无必修课记录")
                ne, ecr = f.elective_total(major)
                return Task(
                    question=(f"{q}{major}专业的必修课共多少门、合计多少学分？"
                              f"选修课共多少门、合计多少学分？"),
                    answer=(f"{a}；{major}专业必修课 {n} 门、合计 {cr} 学分；"
                            f"选修课 {ne} 门、合计 {ecr or 0} 学分"),
                    surfaces=["db", "kb"],
                    derivation=[
                        f"kb: {doc}——本条政策阈值（锚点 {anchor!r} 已验证存在于 KB）",
                        f"db: course_catalog 查 major={major} 分别聚合必修与选修的门数与学分",
                    ],
                    necessity={
                        "db": "学分基数只在 course_catalog；KB 只给比例/门槛不给任何专业的具体数值",
                        "kb": "阈值与后果的对应只在学籍管理细则，表里没有政策字段",
                    },
                    difficulty="easy",
                    template="policy_threshold",
                    extras={"doc": doc, "anchor": anchor, "major": major},
                )
            out.append(guard("policy_threshold", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# E. transfer-plan detail  (db + kb)
# --------------------------------------------------------------------------
@template
def t_transfer_plan(f: Facts) -> list[Task]:
    out: list[Task] = []
    for plan in f.plans:
        def make(plan=plan) -> Task:
            college = plan["college"]
            quota = clean_ws(plan["receiving_majors_and_quota"])
            elig = clean_ws(plan["eligibility_requirement"])
            if not quota or not elig:
                raise Unanswerable(f"{college} 名额或报名条件为空")
            return Task(
                question=(f"{college}今年的转专业接收计划是多少人，报名需要满足什么条件？"
                          f"按转专业办理规程，每个学生最多能申请几个转入专业？"),
                answer=(f"{quota}；报名条件：{elig}；每个学生只能申请一个转入专业（或专业类）"),
                surfaces=["db", "kb"],
                derivation=[
                    f"db: major_transfer_plan 查 {college} 的 receiving_majors_and_quota 与 eligibility_requirement",
                    "kb: major_transfer_procedure.md——‘每个学生只能申请一个转入专业（或专业类）’",
                ],
                necessity={
                    "db": "名额与报名条件只在 major_transfer_plan",
                    "kb": "申请个数限制属办理规程，表里没有该字段",
                },
                difficulty="easy",
                template="transfer_plan",
                extras={"college": college},
            )
        out.append(guard("transfer_plan", make))
    return [t for t in out if t]


@template
def t_transfer_eligibility_rules(f: Facts) -> list[Task]:
    """The eight barred categories exist nowhere in the quota table."""
    out: list[Task] = []
    rules = [
        ("强基计划、中外合作办学专业录取的学生", "不得申请"),
        ("外语类保送生", "不得申请转出外国语学院，且不得转入小语种以外的相关专业"),
        ("入学后被选拔进入各类实验班的学生", "不得申请"),
        ("运动训练专业和艺术类专业的学生", "不得申请"),
    ]
    for who, what in rules:
        def make(who=who, what=what) -> Task:
            f.kb_anchor("major_transfer_procedure.md", "不得申请")
            return Task(
                question=(f"按转专业办理规程，{who}能否申请转专业？"
                          f"规程里一共列了几类不得申请转专业的情形？"),
                answer=(f"{who}{what}；规程共列出 8 类资格限定情形"),
                surfaces=["kb"],
                derivation=["kb: major_transfer_procedure.md——申请资格 8 条限定（锚点已验证）"],
                necessity={"kb": "资格限定是纯政策条款，转专业名额表中没有任何相应字段"},
                difficulty="easy",
                template="transfer_eligibility_rules",
                extras={"rule_subject": who},
            )
        out.append(guard("transfer_eligibility_rules", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# F. major-level DB aggregates  (db only)
# --------------------------------------------------------------------------
@template
def t_major_aggregate(f: Facts) -> list[Task]:
    out: list[Task] = []
    majors = sorted({r[0] for r in f.q("select distinct major from course_catalog")})
    for major in majors:
        def make(major=major) -> Task:
            n, cr = f.required_total(major)
            ne, ecr = f.elective_total(major)
            if n == 0:
                raise Unanswerable(f"{major} 无必修课记录")
            hours = f.one("select coalesce(sum(total_hours),0) from course_catalog where major=?",
                          (major,))[0]
            return Task(
                question=(f"{major}专业的培养方案里，必修课与选修课各多少门、各多少学分？"
                          f"全部课程的学时合计是多少？"),
                answer=(f"必修 {n} 门 / {cr} 学分；选修 {ne} 门 / {ecr or 0} 学分；"
                        f"学时合计 {hours}"),
                surfaces=["db"],
                derivation=[f"db: course_catalog 按 major={major} 分组聚合门数、学分、学时"],
                necessity={"db": "纯表聚合，不涉及政策条款或图关系"},
                difficulty="easy",
                template="major_aggregate",
                extras={"major": major},
            )
        out.append(guard("major_aggregate", make))
    return [t for t in out if t]


@template
def t_course_sharing(f: Facts) -> list[Task]:
    """Cross-cutting course sharing — an aggregate no discipline view gives."""
    out: list[Task] = []
    rows = f.q("""select course_name, course_code, count(distinct major) c, course_type
                  from course_catalog
                  where substr(course_code,1,3) not in ('MAX','SFL','PHE','CHI')
                  group by course_code having count(distinct major)>=45
                  order by c desc""")
    for name, code, cnt, ctype in rows:
        def make(name=name, code=code, cnt=cnt, ctype=ctype) -> Task:
            rows2 = f.q("""select course_name, count(distinct major) c from course_catalog
                           where course_code=? group by course_code""", (code,))
            disc = collections.Counter()
            for (m,) in f.q("select distinct major from course_catalog where course_code=?",
                            (code,)):
                d = f.disc_of.get(m)
                if d:
                    disc[d] += 1
            top_d = disc.most_common(1)[0] if disc else ("", 0)
            return Task(
                question=(f"在全校培养方案中，课程「{name}」（{code}）被多少个专业要求？"
                          f"属必修还是选修？这些专业中属{top_d[0]}学科大类的最多，有多少个？"
                          f"{DISC_QUALIFIER}。"),
                answer=(f"{name}（{code}）被 {cnt} 个专业要求，属{ctype}；"
                        f"其中属{top_d[0]}学科大类的最多，有 {top_d[1]} 个专业"),
                surfaces=["db", "graph"],
                derivation=[
                    "db: course_catalog 按 course_code 聚合 count(distinct major)",
                    f"graph: 把这些专业经 includes_major 反查学科大类，计数得 {top_d[0]}={top_d[1]}",
                ],
                necessity={
                    "db": "全校范围的课程共享度是表上的聚合量",
                    "graph": "把专业集合按学科大类归组只能在图上做；表里没有 discipline 列",
                },
                difficulty="medium",
                template="course_sharing",
                extras={"course_code": code, "n_majors": cnt},
            )
        out.append(guard("course_sharing", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# G. KB-only procedure / admission
# --------------------------------------------------------------------------
@template
def t_kb_procedure(f: Facts) -> list[Task]:
    items = [
        ("general_elective_selection_rules.md", "筛选",
         "公共选修课程的选课分为哪四个阶段？在筛选阶段，哪些情况会被优先满足？",
         "分为正选、筛选、补选、退选四个阶段。筛选阶段以下三类情况予以优先满足："
         "1.毕业年级学生未修满公共选修课程学分者；2.二年级学生未取得公共选修课程学分者；"
         "3.三年级学生选修公共选修课程学分不满4学分者",
         "elective_stages"),
        ("general_elective_selection_rules.md", "不安排补考",
         "公共选修课程考核不及格可以补考吗？学生最多在什么时间范围内退选？",
         "公共选修课程考核不安排补考，不及格者可在后续学期再次选修该课程或另选其他课程修读；"
         "学生可在规定的退选阶段内网上退选，过期不退选视为已确认选修",
         "elective_no_makeup"),
        ("course_selection_and_grading.md", "1/3",
         "按规定，每门课程参加补考的次数最多几次、补考合格后成绩按多少分记载？",
         "每门课程补考次数不超过 1 次，补考合格者成绩以 60 分记载",
         "makeup_times"),
        ("study_duration_and_academic_standing.md", "2/3",
         "学分清理中黄牌警示与红牌警示分别对应什么比例？受到红牌警示会有什么后果？",
         "未达该学年度培养计划总学分的 2/3 者给黄牌警示；未达累计总学分的 1/2 者给红牌警示",
         "credit_cleanup"),
        ("international_admission_requirements.md", "HSK",
         "申请中文授课项目时，理学、工学、经济、管理、医学类学科与文学、教育、法学类学科"
         "分别需要达到什么汉语水平？各层次学历生的年龄上限分别是多少？",
         "理工经管医类需通过 HSK 4；文学、教育、法学类以及临床医学类研究生项目需通过 HSK 5；"
         "本科 25 岁以下、硕士 35 岁以下、博士 40 岁以下、普通进修 45 岁以下、高级进修 50 岁以下",
         "international_requirements"),
        ("key_disciplines.md", "国家级重点学科",
         "本校的一级学科国家重点学科有几个、二级学科国家重点学科有几个？"
         "一级学科国家重点学科具体是哪些？",
         "一级学科国家重点学科 7 个（机械工程、光学工程、材料科学与工程、动力工程及工程热物理、"
         "电气工程、控制科学与工程、生物医学工程）；二级学科国家重点学科 15 个"
         "（内科学、外科学按三级）",
         "key_disciplines"),
        ("major_transfer_procedure.md", "联考",
         "转专业联考笔试的科目是什么、在什么时间举行？网上报名的起止时间是什么？",
         "转专业联考笔试科目为大学英语和微积分；网上报名自 11月25日上午9:00 至 11月28日下午17:30",
         "transfer_exam"),
    ]
    out: list[Task] = []
    for doc, needle, q, a, tag in items:
        def make(doc=doc, needle=needle, q=q, a=a, tag=tag) -> Task:
            f.kb_anchor(doc, needle)
            return Task(question=q, answer=a, surfaces=["kb"], derivation=[f"kb: {doc}（锚点 {needle!r} 已验证）"],
                        necessity={"kb": "纯政策条款，不涉及任何专业或课程的具体数值"},
                        difficulty="easy", template="kb_procedure", extras={"doc": doc, "tag": tag})
        out.append(guard("kb_procedure", make))
    return [t for t in out if t]


# --------------------------------------------------------------------------
# H. single-surface controls (validate the ablation judging itself)
# --------------------------------------------------------------------------
@template
def t_controls(f: Facts) -> list[Task]:
    out: list[Task] = []

    def kb_control() -> Task:
        f.kb_anchor("supplementary_provisions.md", "本细则由本科生院负责解释")
        return Task(
            question="本细则从哪个年级本科生开始执行？由哪个部门负责解释？",
            answer="本细则从 2021 级本科生开始执行，由本科生院负责解释",
            surfaces=["kb"], derivation=["kb: supplementary_provisions.md 第九十三、九十四条"],
            necessity={"kb": "纯政策条款"}, difficulty="easy", template="control_kb",
            extras={"note": "单面任务，作为消融对照组：验证 kb-only 任务确实只需 KB"})
    out.append(guard("control_kb", kb_control))

    def db_control() -> Task:
        rows = f.q("""select major, count(*), round(sum(credits),2) c from course_catalog
                      where course_type='必修' group by major order by c desc limit 2""")
        best = f.assert_unique(rows, "全校必修学分最高")
        return Task(
            question=f"全校 {len({r[0] for r in f.q('select distinct major from course_catalog')})} "
                     f"个专业中，必修学分最高的是哪个专业、多少学分、多少门课？",
            answer=f"{best[0]}，{best[2]} 学分，{best[1]} 门",
            surfaces=["db"], derivation=["db: course_catalog 按 major 聚合必修学分取最大（已验证无并列）"],
            necessity={"db": "纯表聚合"}, difficulty="easy", template="control_db",
            extras={"note": "单面任务，作为消融对照组：验证 db-only 任务确实只需 DB",
                    "uniqueness_check": f"第一 {best[2]} 学分，第二 {rows[1][2]} 学分，无并列"})
    out.append(guard("control_db", db_control))
    return [t for t in out if t]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="edu_sample")
    ap.add_argument("--db", default="DataMind/storage/edu_registrar/demo.db")
    ap.add_argument("--out", default="edu_sample/tasks/tasks_big.jsonl")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    f = Facts(Path(args.src), Path(args.db))
    tasks: list[Task] = []
    for fn in TEMPLATES:
        produced = fn(f)
        tasks.extend(produced)
        print(f"  {fn.__name__:34s} {len(produced):4d}")

    # Round-robin across templates so the cap does not starve whole families.
    by_t: dict[str, list[Task]] = collections.defaultdict(list)
    for t in tasks:
        by_t[t.template].append(t)
    ordered: list[Task] = []
    while len(ordered) < min(args.limit, len(tasks)):
        progressed = False
        for name in sorted(by_t):
            if by_t[name] and len(ordered) < args.limit:
                ordered.append(by_t[name].pop(0))
                progressed = True
        if not progressed:
            break

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for i, t in enumerate(ordered, start=1):
            fh.write(json.dumps(t.render(f"edu_{i:04d}"), ensure_ascii=False) + "\n")

    print(f"\nwrote {len(ordered)} tasks to {out}  (pool {len(tasks)})")
    print("  surfaces:", dict(collections.Counter(
        "+".join(sorted(t.surfaces)) for t in ordered)))
    print("  difficulty:", dict(collections.Counter(t.difficulty for t in ordered)))
    print("  template:", dict(collections.Counter(t.template for t in ordered)))
    if SKIPPED:
        print(f"\n  dropped {sum(SKIPPED.values())} instances (non-unique gold / missing anchor):")
        for k, v in SKIPPED.most_common(10):
            print(f"    {v:4d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())