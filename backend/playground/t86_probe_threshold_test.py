"""T86 验收：probe 相关性阈值 + 无资料分支章节数收敛

被测对象
    backend/app/core/config.py             新增 probe_min_score 配置
    backend/app/services/agent/nodes.py    probe() 按分数过滤；planner() 章节数约束

背景
    T85 复验发现两个问题：
    1) probe 没有相关性阈值 —— objective 与资料完全不相关时，仍把 5 条低分噪声
       当"可用资料"喂给 planner（"过滤结果为空"才算无资料，低分命中照收）。
    2) prompt 写死"3-5 个章节"，但无资料分支守不住（实测 6/7/7/8 章）。

阈值怎么定的（数据来源：.workbuddy/tmp/t86_threshold_probe.py 实测 top1 分数）
    真相关 10 条：0.4591 ~ 0.7959
    真无关  7 条：0.1912 ~ 0.3643
    → 可分区间 (0.3643, 0.4591)，取中点 0.41
    沾边 2 条（0.4284 / 0.4581）落在区间内，判哪边都说得通，故不设判据。

判据分组
    R 组  实现与落点 —— 阈值配置存在、probe 真的做了过滤、阈值落在可分区间内
    B 组  行为对照   —— 无关 objective 必须被判"无资料"；相关 objective 必须仍判"有资料"
    C 组  章节数     —— 无资料 / 有资料两条路径都要落在 3-5 章

可证伪性
    改成"阈值过滤"之前跑本脚本，B1 / C1 应为 FAIL（这就是基线）。
    改完再跑应全 PASS。B2 是反向判据：防止把阈值调得过严，误伤真相关。

运行
    cd backend && ./.venv/Scripts/python.exe playground/t86_probe_threshold_test.py
"""
from __future__ import annotations

import ast
import asyncio
import sys
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.core.config import settings  # noqa: E402
from app.services.agent.graph import build_graph  # noqa: E402
from app.services.agent.nodes import probe  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []

# 阈值可选区间（由实测分数分布得到，见文件头）
LO, HI = 0.3643, 0.4591

# 真无关：库里确实没有对应内容
UNRELATED = [
    "2026 年国内新能源汽车销量排名",
    "红烧肉的家常做法步骤",
    "NBA 季后赛最新赛况",
    "量子计算在密码学中的应用",
    "上海二手房交易税费怎么算",
    "杭州亚运会志愿者招募条件",
    "2026 年国内 AI 客服领域头部玩家的市场份额与定价策略",
]
# 真相关：库里有内容支撑（最低 0.4591 = "任务执行流程是怎样的？"）
RELATED = [
    "知研 KnowPilot 是什么？",
    "KnowPilot 的技术架构用了哪些框架？",
    "KnowPilot 的核心能力和差异化优势",
    "任务执行流程是怎样的？",
    "智能点餐中台的产品定位",
    "点餐中台怎么配置门店？",
    "这个工作台能帮研究者做什么",
    "后端和前端分别用的什么技术",
    "怎么添加新的门店信息",
    "怎么提高团队撰写行业报告的工作效率",
]
# 沾边：语义相近但库里不覆盖，判哪边都合理 —— 只观察不判
GRAY = [
    "企业知识管理平台的选型对比与采购建议",
    "主流 RAG 框架的性能基准测试结果",
]

NO_KB_TXT = "未关联知识库"
NO_HIT_TXT = "未检索到相关内容"
HAS_TXT = "知识库可用资料"


def record(cid: str, ok: bool | None, detail: str, warn: bool = False) -> None:
    if ok is None:
        tag = "SKIP"
    elif ok:
        tag = "PASS"
    else:
        tag = "WARN" if warn else "FAIL"
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------- 前置数据

async def load_kbs() -> list[tuple[str, str, list[dict]]]:
    """一次取回 (库名, 库id, ready 文档列表)。

    注意：必须在同一个 event loop 内取完 —— 分两次 asyncio.run 会让 asyncpg
    连接跨 loop 复用，报 "'NoneType' object has no attribute 'send'"。
    """
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Document, KnowledgeBase

    async with SessionLocal() as s:
        kbs = (await s.execute(select(KnowledgeBase.id, KnowledgeBase.name))).all()
        docs = (await s.execute(
            select(Document.id, Document.kb_id, Document.status, Document.vector_epoch)
        )).all()
    by_kb: dict[str, list[dict]] = {}
    for i, kb_id, status, epoch in docs:
        by_kb.setdefault(str(kb_id), []).append(
            {"id": str(i), "status": status, "epoch": int(epoch)}
        )
    return [(name, str(kid), by_kb.get(str(kid), [])) for kid, name in kbs]


def milvus_top1(kb_id: str, query: str, top_k: int = 5) -> float | None:
    """只读取 top1 分数，用于 R3 独立复核阈值落点。"""
    from app.services.rag import get_embedder
    from app.services.rag.milvus_client import search

    hits = search(get_embedder().embed_query(query), [kb_id], top_k=top_k)
    if not hits:
        return None
    return float(hits[0].get("distance") or 0)


# ---------------------------------------------------------------- R 组

def classify(ov: str) -> str:
    if NO_KB_TXT in ov:
        return "no_kb"
    if NO_HIT_TXT in ov:
        return "no_hit"
    if HAS_TXT in ov:
        return "has_hit"
    return "other"


def run_r_group(prod_kb: str) -> None:
    # R1 阈值配置项存在，且落在实测可分区间内
    thr = getattr(settings, "probe_min_score", None)
    ok_cfg = isinstance(thr, (int, float)) and LO < float(thr) < HI
    record("R1", ok_cfg,
           f"settings.probe_min_score = {thr!r}（需落在实测可分区间 "
           f"({LO}, {HI}) 内）")

    # R2 probe 源码里真的做了阈值过滤（不能只加配置不用）
    src = (BACKEND / "app/services/agent/nodes.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    uses_thr, filters_hits = False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "probe":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == "probe_min_score":
                    uses_thr = True
                # 找形如 [h for h in hits if ...] 的列表推导：过滤后再用
                if isinstance(sub, ast.ListComp) and any(
                    isinstance(g.iter, ast.Name) and g.iter.id == "hits"
                    for g in sub.generators
                ):
                    filters_hits = True
    record("R2", uses_thr and filters_hits,
           f"probe() 读 settings.probe_min_score={uses_thr}；"
           f"对 hits 做列表推导过滤={filters_hits}")

    # R3 阈值落点独立复核：真无关的最高分 < 阈值 < 真相关的最低分
    if isinstance(thr, (int, float)):
        thr_f = float(thr)
        rel_tops, unrel_tops = [], []
        for q in RELATED:
            s = milvus_top1(prod_kb, q)
            if s is not None:
                rel_tops.append(s)
        for q in UNRELATED:
            s = milvus_top1(prod_kb, q)
            if s is not None:
                unrel_tops.append(s)
        if rel_tops and unrel_tops:
            r_lo, u_hi = min(rel_tops), max(unrel_tops)
            record("R3", u_hi < thr_f < r_lo,
                   f"真无关最高 {u_hi:.4f} < 阈值 {thr_f:.4f} < 真相关最低 {r_lo:.4f}"
                   f"（余量 下 {thr_f-u_hi:+.3f} / 上 {r_lo-thr_f:+.3f}）")
        else:
            record("R3", None, "Milvus 取分失败，跳过")


# ---------------------------------------------------------------- B 组

def run_b_group(facts: dict) -> None:
    prod_kb = facts["prod_kb"]
    base = {
        "kb_ids": [prod_kb],
        "ready_doc_ids": [d["id"] for d in facts["prod_docs"]],
        "ready_doc_epochs": {d["id"]: d["epoch"] for d in facts["prod_docs"]},
    }

    # B1 真无关 objective —— 必须被判"无资料"（改前这里 FAIL：会报"可用资料 5 条"）
    missed = []
    for q in UNRELATED:
        cls = classify(probe({"objective": q, **base}).get("kb_overview", ""))
        if cls != "no_hit":
            missed.append((q, cls))
    record("B1", not missed,
           f"真无关 {len(UNRELATED)} 条中，未判「无资料」的 = {len(missed)} 条"
           + (f" → {missed[:2]}" if missed else ""))

    # B2 反向验证：真相关 objective 不能被误杀（阈值调过头会在这里 FAIL）
    dropped = []
    for q in RELATED:
        cls = classify(probe({"objective": q, **base}).get("kb_overview", ""))
        if cls != "has_hit":
            dropped.append((q, cls))
    record("B2", not dropped,
           f"真相关 {len(RELATED)} 条中，被误判「无资料」的 = {len(dropped)} 条"
           + (f" → {dropped[:2]}" if dropped else ""))

    # B3 沾边样本（观察项，不判 PASS/FAIL）
    print("  [B3 观察项] 沾边 objective 落在哪一侧：")
    for q in GRAY:
        ov = probe({"objective": q, **base}).get("kb_overview", "")
        s = milvus_top1(prod_kb, q)
        print(f"      {classify(ov):7s} top1={s if s is None else round(s,4)}  {q}")

    # B4 空 kb 仍走原分支（回归，不能被阈值改动带坏）
    ov = probe({"objective": "随便什么", "kb_ids": []}).get("kb_overview", "")
    record("B4", NO_KB_TXT in ov, f"未关联知识库时仍走原分支：{ov[:36]!r}")


# ---------------------------------------------------------------- C 组

async def run_to_planner(initial: dict) -> tuple[dict, dict | None]:
    graph = build_graph()
    probe_out: dict = {}
    plan_out: dict | None = None
    async for step in graph.astream(initial, stream_mode="updates"):
        for node, update in step.items():
            if node == "probe":
                probe_out = update
            elif node == "planner":
                plan_out = update
        if plan_out is not None:
            break
    return probe_out, plan_out


async def run_c_group(facts: dict) -> None:
    prod_kb = facts["prod_kb"]

    # C1 无资料（白名单置空）—— 改前实测 6/7/7/8 章，故跑 3 次看是否稳定收敛
    empty = {"objective": "知研 KnowPilot 是什么？", "kb_ids": [prod_kb],
             "ready_doc_ids": [], "ready_doc_epochs": {}}
    counts = []
    for _ in range(3):
        _, plan_out = await run_to_planner(empty)
        counts.append(len((plan_out or {}).get("plan", [])))
    record("C1", all(3 <= n <= 5 for n in counts),
           f"无资料（白名单置空）跑 3 次章节数 = {counts}（prompt 要求 3-5）")

    # C2 无资料（阈值过滤为空）—— 真实场景：传了资料但 objective 与资料不相关
    gray_obj = "2026 年国内 AI 客服领域头部玩家的市场份额与定价策略"
    base = {"objective": gray_obj, "kb_ids": [prod_kb],
            "ready_doc_ids": [d["id"] for d in facts["prod_docs"]],
            "ready_doc_epochs": {d["id"]: d["epoch"] for d in facts["prod_docs"]}}
    probe_out, plan_out = await run_to_planner(base)
    ov = probe_out.get("kb_overview", "")
    plan = (plan_out or {}).get("plan", [])
    focus = (plan_out or {}).get("focus", [])
    record("C2", classify(ov) == "no_hit" and 3 <= len(plan) <= 5 and len(focus) == len(plan),
           f"无关 objective 走真实链路：预检={classify(ov)} 章节数={len(plan)} "
           f"focus 等长={len(focus) == len(plan)}")
    print("      plan =", plan)

    # C3 有资料（回归）
    base2 = {"objective": "知研 KnowPilot 是什么？", "kb_ids": [prod_kb],
             "ready_doc_ids": [d["id"] for d in facts["prod_docs"]],
             "ready_doc_epochs": {d["id"]: d["epoch"] for d in facts["prod_docs"]}}
    probe_out2, plan_out2 = await run_to_planner(base2)
    plan2 = (plan_out2 or {}).get("plan", [])
    focus2 = (plan_out2 or {}).get("focus", [])
    record("C3", classify(probe_out2.get("kb_overview", "")) == "has_hit"
                  and 3 <= len(plan2) <= 5 and len(focus2) == len(plan2),
           f"有资料回归：预检={classify(probe_out2.get('kb_overview',''))} "
           f"章节数={len(plan2)} focus 等长={len(focus2) == len(plan2)}")
    print("      plan =", plan2)


# ---------------------------------------------------------------- main

def main() -> int:
    head("T86 验收：probe 相关性阈值 + 章节数收敛")
    kbs = asyncio.run(load_kbs())
    print("知识库:", {n: len(d) for n, _, d in kbs})

    # 选 ready 文档最多的库作为被测库
    prod_kb, prod_docs = None, []
    for _name, kb_id, docs in kbs:
        ready = [d for d in docs if d["status"] == "ready"]
        if len(ready) > len(prod_docs):
            prod_kb, prod_docs = kb_id, ready
    if not prod_kb:
        record("R0", None, "没找到有 ready 文档的知识库，跳过")
        return _summary()

    record("R0", True, f"被测库 kb_id={prod_kb}，ready 文档 {len(prod_docs)} 篇")

    head("R 组 · 实现与阈值落点")
    run_r_group(prod_kb)

    head("B 组 · 行为对照（核心）")
    run_b_group({"prod_kb": prod_kb, "prod_docs": prod_docs})

    head("C 组 · 章节数收敛（真实链路，调 LLM）")
    asyncio.run(run_c_group({"prod_kb": prod_kb, "prod_docs": prod_docs}))

    return _summary()


def _summary() -> int:
    head("汇总")
    for cid, tag, detail in RESULTS:
        print(f"  {tag:4} {cid}  {detail[:100]}")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    n_warn = sum(1 for _, t, _ in RESULTS if t == "WARN")
    n_skip = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}  WARN={n_warn}  SKIP={n_skip}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
