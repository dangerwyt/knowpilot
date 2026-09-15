"""T88 验收：critic 输出结构化的分项分数

背景
----
踩坑 #93：critic 的 prompt 一直写着「从四个维度客观打分」，但 `ReviewResult`
只有 `score / issues / feedback` —— 分项分数**没有字段承载**，只能散在
`issues` 的自然语言里。实测同一个 objective 跑三次、三种版式：

    改前  ：相关度（扣6分）：…属于跑题内容
    上一轮：相关度（20/20）：…未出现跑题
    本轮  ：第一、二、三、四、六节（"…总体格局"章节）：…（没有分项标题）

⇒ 判据没法挂（正则追不上措辞变化，#92/#93）、前端也没法展示分项。

期望改动（后端 3 处 + 前端 2 处）
--------------------------------
1. nodes.py：新增 `DimensionScore(name, score, comment)`；`ReviewResult` 加
   `dimensions: list[DimensionScore] = []`；prompt 改成「四个维度各按 0-25 分」；
   `critic()` **两个 return 分支**（正常 / 降级）都带 `dimensions`
2. state.py：加 `dimensions: list[dict]`  ← 漏了会被 LangGraph 静默丢弃（踩坑 #91）
3. task_queue.py：`draft["quality"]` 加 `dimensions`
4. typing/report.ts：`IQuality` 加 `dimensions?: IDimensionScore[]`
5. ReportView.vue：评审弹窗里展示四行分项

判据（改前应 FAIL，改后应 PASS）
--------------------------------
R1 DimensionScore 模型存在且字段齐全
R2 ReviewResult 带 dimensions 字段
R3 prompt 文案里四个维度名齐全 + 出现 25 分制
R4 state.py 声明了 dimensions（**白名单**，漏了就是白改）
R5 critic() 每个 return 分支都带 dimensions（不只正常分支）
R6 task_queue 落库 quality 时带上 dimensions
R7 前端 typing 有 IDimensionScore / dimensions
R8 前端 ReportView 引用了 dimensions
R9 通用：所有节点 return 的键都在 ResearchState 声明里（防下一次又漏）
B1 直调 critic（真 LLM）返回含 dimensions
B2 四项齐全
B3 每项 score ∈ [0, 25]
B4 四个维度名全覆盖
B5 分项之和 == 总分（模型算错就该拦住）
D1 降级分支（真模拟 LLM 连续失败）也要带 dimensions 键

只读文件 + 进程内直调，不改任何数据、不发任务。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[str, str, str]] = []

DIMENSIONS = ["完整度", "相关度", "事实性", "结构规范"]
MAX_PER_DIM = 25
NODES = BACKEND / "app/services/agent/nodes.py"
STATE = BACKEND / "app/services/agent/state.py"
QUEUE = BACKEND / "app/services/task_queue.py"
FRONT = BACKEND.parent / "frontend/src"
TYPING = FRONT / "typing/report.ts"
REPORT_VIEW = FRONT / "views/ReportView.vue"


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# ---------------------------------------------------------------- AST 工具
def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def returned_keys(tree: ast.Module, fname: str) -> tuple[set[str], bool]:
    """收集某函数里所有 `return {..}` 字典字面量的**顶层**键。

    返回 (键集合, 是否含 ** 展开)。`{**x}` 的 key 是 None，无法静态确定。
    """
    keys: set[str] = set()
    dynamic = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == fname):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                for k in sub.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                    else:
                        dynamic = True
    return keys, dynamic


def per_return_keys(tree: ast.Module, fname: str) -> list[set[str]]:
    """逐个 return 分支收集键 —— 用来发现「只改了一个分支」这种漏改。"""
    out: list[set[str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == fname):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                ks = {k.value for k in sub.value.keys
                      if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                out.append(ks)
    return out


def class_fields(tree: ast.Module, cname: str) -> set[str]:
    """收集某个类（pydantic 模型 / TypedDict）里声明的字段名。"""
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cname:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
    return fields


def module_strings(tree: ast.Module, assign_name: str) -> str:
    """把模块级某个赋值里出现的所有字符串常量拼起来（用来读 prompt 文案）。"""
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == assign_name for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    parts.append(sub.value)
    return "\n".join(parts)


# ---------------------------------------------------------------- R 组 · 静态
def run_static() -> None:
    head("R 组 · 静态：模型 / prompt / 白名单 / 落库 / 前端五处是否都改到")

    nodes_tree = parse(NODES)
    review_prompt = module_strings(nodes_tree, "review_template")

    # --- R1 DimensionScore 模型
    dim_fields = class_fields(nodes_tree, "DimensionScore")
    print(f"  DimensionScore 字段 = {sorted(dim_fields) or '（类不存在）'}")
    need = {"name", "score"}
    record("R1", need.issubset(dim_fields),
           f"DimensionScore 字段齐全（{sorted(dim_fields)}）" if need.issubset(dim_fields)
           else f"缺字段：{sorted(need - dim_fields)}（当前 {sorted(dim_fields) or '无此类'}）")

    # --- R2 ReviewResult 带 dimensions
    rr_fields = class_fields(nodes_tree, "ReviewResult")
    print(f"  ReviewResult 字段 = {sorted(rr_fields)}")
    record("R2", "dimensions" in rr_fields,
           "ReviewResult 已带 dimensions" if "dimensions" in rr_fields
           else f"ReviewResult 仍只有 {sorted(rr_fields)} ⇒ 分项分数没有字段承载")

    # --- R3 prompt 文案
    missing_dim = [d for d in DIMENSIONS if d not in review_prompt]
    has_scale = "25" in review_prompt
    print(f"  prompt 缺的维度名 = {missing_dim or '（无）'}；出现 25 分制 = {has_scale}")
    r3_reasons = []
    if missing_dim:
        r3_reasons.append(f"缺维度名 {missing_dim}")
    if not has_scale:
        r3_reasons.append("没写明 25 分制（模型不知道每维满分多少，分数会乱）")
    record("R3", not r3_reasons,
           "prompt 四维度齐全且写明 25 分制" if not r3_reasons else "prompt " + "；".join(r3_reasons))

    # --- R4 state.py 白名单
    state_tree = parse(STATE)
    declared = class_fields(state_tree, "ResearchState")
    print(f"  ResearchState 声明的键 = {sorted(declared)}")
    record("R4", "dimensions" in declared,
           "state.py 已声明 dimensions" if "dimensions" in declared
           else "state.py 没声明 dimensions ⇒ 节点返回了也会被 LangGraph 静默丢弃（踩坑 #91）")

    # --- R5 critic 每个 return 分支都带 dimensions
    branches = per_return_keys(nodes_tree, "critic")
    bad = [i for i, ks in enumerate(branches) if "dimensions" not in ks]
    print(f"  critic() 的 return 分支数 = {len(branches)}，缺 dimensions 的分支 = {bad or '（无）'}")
    for i, ks in enumerate(branches):
        print(f"    分支{i}: {sorted(ks)}")
    record("R5", bool(branches) and not bad,
           f"critic() 共 {len(branches)} 个 return 分支，全部带 dimensions"
           if branches and not bad else
           f"有 {len(bad)} 个 return 分支漏了 dimensions（下标 {bad}）⇒ 降级路径前端拿到 undefined")

    # --- R6 task_queue 落库
    qtree = parse(QUEUE)
    quality_keys: set[str] = set()
    for node in ast.walk(qtree):
        # draft["quality"] = { ... }  —— 找 Subscript 赋值右侧的字典字面量
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.value, ast.Name) and tgt.value.id == "draft"
                        and isinstance(tgt.slice, ast.Constant) and tgt.slice.value == "quality"):
                    quality_keys = {k.value for k in node.value.keys
                                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    print(f"  draft[\"quality\"] 落库的键 = {sorted(quality_keys) or '（没找到赋值点）'}")
    record("R6", "dimensions" in quality_keys,
           "quality 落库带上 dimensions" if "dimensions" in quality_keys
           else "quality 落库没有 dimensions ⇒ 前端永远读不到分项")

    # --- R7 前端 typing
    typing_src = TYPING.read_text(encoding="utf-8")
    has_iface = "IDimensionScore" in typing_src
    has_field = "dimensions" in typing_src
    print(f"  typing: 有 IDimensionScore = {has_iface}；有 dimensions 字段 = {has_field}")
    r7_reasons = []
    if not has_iface:
        r7_reasons.append("没有 IDimensionScore 接口")
    if not has_field:
        r7_reasons.append("IQuality 没加 dimensions 字段")
    record("R7", not r7_reasons,
           "前端 typing 已加 IDimensionScore + dimensions"
           if not r7_reasons else "typing " + "、".join(r7_reasons))

    # --- R8 前端展示
    view_src = REPORT_VIEW.read_text(encoding="utf-8")
    record("R8", "dimensions" in view_src,
           "ReportView 已引用 dimensions"
           if "dimensions" in view_src else "ReportView 没有引用 dimensions ⇒ 后端有分项、页面看不到")

    # --- R9 通用白名单检查（防下一次又漏声明）
    undeclared: dict[str, list[str]] = {}
    for fname in ("probe", "planner", "retriever", "synthesizer", "critic"):
        keys, dynamic = returned_keys(nodes_tree, fname)
        miss = sorted(k for k in keys if k not in declared)
        if miss:
            undeclared[fname] = miss
    print(f"  未声明就被返回的键 = {undeclared or '（无）'}")
    record("R9", not undeclared,
           "所有节点返回的键都在 ResearchState 里声明过"
           if not undeclared else
           f"这些键没声明、会被静默丢弃：{undeclared}（注：draft 内部的键不在此列，只查顶层）")


# ---------------------------------------------------------------- B 组 · 行为
FAKE_DRAFT = {
    "title": "知研 KnowPilot 是什么",
    "sections": [
        {"title": "产品定位与目标用户",
         "content": "知研 KnowPilot 是一款面向研究团队与产品经理的 AI 研究工作台。它把"
                    "「资料搜集—拆章规划—撰写—质检」整条链路交给 Agent 自动完成：用户只需给出"
                    "调研目标并关联内部知识库，系统即可产出结构化报告。"},
        {"title": "技术架构与核心能力",
         "content": "后端基于 FastAPI 与 LangGraph 构建 Agent 状态图，节点依次为 probe、"
                    "planner、retriever、synthesizer、critic；检索侧用 Milvus 做向量召回、"
                    "PostgreSQL 存关系数据；前端为 Vue 3 与 Element Plus。"},
        {"title": "与通用问答工具的差异",
         "content": "通用问答工具多为「一问一答」，KnowPilot 强调可追溯：报告中的关键论断会绑定"
                    "知识库中的原始文档片段，点击即可溯源到具体段落。该细节资料未覆盖竞品定价策略。"},
    ],
}


def run_behavior() -> None:
    head("B 组 · 行为：直调 critic（真调 LLM 一次），看分项分数是否真能拿到")

    from app.services.agent.nodes import critic

    state = {"objective": "知研 KnowPilot 是什么？", "draft": FAKE_DRAFT}
    out = critic(state)

    print(f"  critic() 返回的键 = {sorted(out.keys())}")
    dims = out.get("dimensions")
    print(f"  dimensions = {dims}")

    record("B1", "dimensions" in out,
           f"返回含 dimensions（{len(dims or [])} 项）" if "dimensions" in out
           else "返回值没有 dimensions 键 ⇒ 分项分数根本没出来")

    if not dims:
        # 没有分项就无从判"分项的质量"——记 SKIP 而不是 FAIL，避免虚增 FAIL 数、
        # 也避免观察者误以为"改完还有 4 条要修"（B1 已经明确报了根因）
        for cid in ("B2", "B3", "B4", "B5"):
            record(cid, None, "前置条件不满足：dimensions 为空，等 B1 通过后再判")
        return

    # B2 四项齐全
    record("B2", len(dims) == 4,
           f"四项齐全（{len(dims)} 项）" if len(dims) == 4 else f"应有 4 项，实际 {len(dims)} 项")

    # B3 分数区间
    scores = [d.get("score") for d in dims]
    in_range = all(isinstance(s, int) and 0 <= s <= MAX_PER_DIM for s in scores)
    print(f"  各维度 score = {scores}（应 ∈ [0,{MAX_PER_DIM}]）")
    record("B3", in_range,
           f"分数都在 [0,{MAX_PER_DIM}] 区间（{scores}）" if in_range
           else f"越界分数：{[s for s in scores if not isinstance(s, int) or not 0 <= s <= MAX_PER_DIM]}")

    # B4 维度名覆盖
    names = [d.get("name") for d in dims]
    miss = [d for d in DIMENSIONS if d not in names]
    print(f"  维度名 = {names}")
    record("B4", not miss,
           "四个维度名全部覆盖" if not miss else f"缺维度名 {miss}（实际 {names}）")

    # B5 分项之和 == 总分
    total = out.get("score")
    ssum = sum(s for s in scores if isinstance(s, int))
    print(f"  分项之和 = {ssum}，总分 score = {total}")
    record("B5", total == ssum,
           f"分项之和 == 总分（{ssum}）" if total == ssum
           else f"对不上：分项之和 {ssum} vs 总分 {total} ⇒ 模型算错，需代码兜底或收紧 prompt")


# ---------------------------------------------------------------- D 组 · 降级
def run_degraded() -> None:
    head("D 组 · 降级路径：LLM 连续失败时，critic 是否仍返回 dimensions 键")

    from langchain_core.runnables import RunnableLambda

    import app.services.agent.nodes as nodes

    class _FailingReviewModel:
        """模拟评审服务不可用：with_structured_output 返回一个 invoke 必抛的 Runnable。

        注意 nodes.py 第 58 行 `chain = review_template | review_model.with_structured_output(...)`
        在 try **外面**，所以这里必须返回真正的 Runnable，否则报错会冒泡出 critic、
        测不到降级分支。
        """

        def with_structured_output(self, schema, **kwargs):
            def _boom(_):
                raise RuntimeError("simulated review service failure")

            return RunnableLambda(_boom)

    original = nodes.review_model
    nodes.review_model = _FailingReviewModel()
    try:
        out = nodes.critic({"objective": "任意目标", "draft": FAKE_DRAFT})
    finally:
        nodes.review_model = original

    print(f"  降级返回的键 = {sorted(out.keys())}")
    print(f"  reviewed = {out.get('reviewed')}  issues = {out.get('issues')}")
    ok = out.get("reviewed") is False and "dimensions" in out and not out.get("dimensions")
    record("D1", ok,
           f"降级分支带 dimensions 键（值 {out.get('dimensions')}）⇒ 前端不用判 undefined" if ok
           else f"降级分支 {'缺少 dimensions 键' if 'dimensions' not in out else '的 dimensions 非空'}，"
                f"前端会拿到 undefined / 脏值")


def main() -> int:
    static_only = "--static" in sys.argv  # 只跑 R 组（不调 LLM），改后端时可秒出结果

    print("T88 验收：critic 输出结构化的分项分数")
    print("被测文件：" + " / ".join(p.name for p in (NODES, STATE, QUEUE, TYPING, REPORT_VIEW)))
    if static_only:
        print("（--static：只跑静态判据，跳过真调 LLM）")

    run_static()
    if static_only:
        run_degraded()   # 不碰 LLM，只是把 mock 换成必抛的 Runnable
    else:
        run_behavior()
        run_degraded()

    head("汇总")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    n_skip = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    for cid, tag, detail in RESULTS:
        print(f"  [{tag}] {cid}  {detail}")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
