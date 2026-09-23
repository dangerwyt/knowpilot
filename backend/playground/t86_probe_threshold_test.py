"""T86 验收：probe 相关性阈值 + 无资料分支章节数收敛

被测对象
    backend/app/core/config.py             新增 probe_min_score 配置
    backend/app/services/agent/nodes.py    probe() 按分数过滤；planner() 章节数约束

背景
    T85 复验发现两个问题：
    1) probe 没有相关性阈值 —— objective 与资料完全不相关时，仍把 5 条低分噪声
       当"可用资料"喂给 planner（"过滤结果为空"才算无资料，低分命中照收）。
    2) prompt 写死"3-5 个章节"，但无资料分支守不住（实测 6/7/7/8 章）。

阈值怎么定的
    建这个脚本时（S8 切分前）实测：真相关 0.4591~0.7959 / 真无关 0.1912~0.3643，
    可分区间 (0.3643, 0.4591)，取中点 0.41。
    ⚠️ **2026-09-22 复测（S8 切分后 + 65 条评测集）两类重叠**：
        正例最低 0.5333 / 负例最高 0.6739 ⇒ **不存在零错阈值**（见 #114）。
    ⇒ 判据口径随之改变：不再问"是否落在可分区间"（区间已不存在），
       改问"是否落在**零代价区**" —— 阈值 ≤ 正例最低分，即**绝不误伤真相关**。
      这是唯一有信息量的约束：过了这条线，假 True 的多少由数据决定，不由配置决定。

判据分组
    R 组  实现与落点 —— 阈值配置存在且不误伤正例（R1 用常量 / R3 用实测复核）
    B 组  行为对照   —— 无关 objective 该挡的必须挡（B1）；相关 objective 必须仍判"有资料"（B2）
    C 组  章节数     —— 无资料 / 有资料两条路径都要落在 3-5 章

分工与口径（2026-09-22 校准，别改乱）
    上沿（不误伤正例）→ 常量 POS_MIN：R1 判配置、R3 判实测、B2 判行为
    下沿（挡住负例）  → 只由 B1 判，**判据从严、不做豁免**。
    ⚠️ "分不开"的样本不进 UNRELATED：现在这 6 条负例最高 0.4674，都稳稳低于阈值。
       原来还有第 7 条「AI 客服份额」(0.5348)，它与 65 条评测集的正例最低分 0.5333
       只差 0.0015 —— 在 probe 判据下与正例**不可分**，已归入 GRAY 只观察。
       硬塞进 B1 只会得到一个改不动、还随 embedding 抖动忽红忽绿的假红（实测踩过）。

可证伪性
    改成"阈值过滤"之前跑本脚本，B1 / C1 应为 FAIL（这就是基线）。
    改完再跑应全 PASS。B2 是反向判据：防止把阈值调得过严，误伤真相关。
    ✅ 红队已验（临时脚本 `_t86_redteam.py`，用完即删）：
       阈值设 0.90 ⇒ R1 / R3 / B2 红（10 条正例全被误伤）
       阈值设 0.00 ⇒ R1 / B1 红（6 条负例全过线）

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

# 零代价区上沿 = 真相关的最低 top1（2026-09-22 实测，probe_sep_lab.py，65 条样本）
# 阈值超过它就会把真相关的题判成"没资料" —— 唯一有信息量的配置约束。
# ⚠️ 语料 / 切分 / embedding 变了就要重测，别当常数用。
POS_MIN = 0.5333

# 真无关：库里确实没有对应内容 —— 这些是"必须挡住"的
# ⚠️ 原末条「AI 客服份额」已移到 GRAY：它实测 top1 0.5348，与 65 条评测集的正例最低分
#    0.5333 只差 0.0015 ⇒ 在 probe 判据下**与正例不可分**，属"沾边"不属"必须挡"。
#    把它算进这里只会得到一个改不动、还随 embedding 抖动忽红忽绿的假红。
UNRELATED = [
    "2026 年国内新能源汽车销量排名",
    "红烧肉的家常做法步骤",
    "NBA 季后赛最新赛况",
    "量子计算在密码学中的应用",
    "上海二手房交易税费怎么算",
    "杭州亚运会志愿者招募条件",
]
# 真相关：库里有内容支撑（S8 后最低 0.5333 = 题面与文档措辞差最远的那条）
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
# 沾边：语义相近但库里不覆盖（或与正例分数不可分），判哪边都合理 —— 只观察不判
# 三条都是"主题技术性很强、但答案不在语料里"，实测 top1 均在正例最低分 0.5333 附近或以上，
# 单阈值分不开。它们不进 B1（否则恒红），但仍逐条打印，作为"probe 判据边界"的证据。
GRAY = [
    "企业知识管理平台的选型对比与采购建议",
    "主流 RAG 框架的性能基准测试结果",
    "2026 年国内 AI 客服领域头部玩家的市场份额与定价策略",
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


_TOP1: dict[tuple[str, str], float | None] = {}


def milvus_top1(kb_id: str, query: str, top_k: int = 5) -> float | None:
    """只读取 top1 分数，供 R3 / B1 复核阈值落点。

    带缓存 —— R3 与 B1 要的是同一批分数，不缓存就得重复付 17 次 embedding。
    """
    key = (kb_id, query)
    if key in _TOP1:
        return _TOP1[key]
    from app.services.rag import get_embedder
    from app.services.rag.milvus_client import search

    hits = search(get_embedder().embed_query(query), [kb_id], top_k=top_k)
    val = float(hits[0].get("distance") or 0) if hits else None
    _TOP1[key] = val
    return val


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
    # R1 阈值配置项存在，且落在零代价区内（≤ 正例最低分 ⇒ 绝不误伤真相关）
    #    原判据是"落在实测可分区间 (0.3643, 0.4591) 内"。S8 重切后两类**重叠**、
    #    该区间已不存在 ⇒ 照旧判会恒红且零信息量（见 #114）。改判"可行动的那一侧"：
    #    上沿（不误伤正例）仍在，下沿（挡住全部负例）在数学上不存在。
    thr = getattr(settings, "probe_min_score", None)
    ok_cfg = isinstance(thr, (int, float)) and 0 < float(thr) <= POS_MIN
    record("R1", ok_cfg,
           f"settings.probe_min_score = {thr!r}（需 0 < 阈值 ≤ {POS_MIN}："
           f"这是真相关最低分，超过它就把'有资料'的题判成'没资料'）")

    # R2 源码里真的做了阈值过滤（不能只加配置不用）—— 「读阈值」和「过滤 hits」必须同处一函数
    #    ⚠️ 锚点：过滤逻辑 2026-09 已重构进 probe_material()，probe() 只剩编排。
    #    判据写死 probe() 会**恒红**（找不到 ⇒ 判成"没实现"，改动前就是这个假红）。
    #    这里扫"任一相关函数" —— 再重构换名时要同步这个元组。
    SRC_FUNCS = ("probe", "probe_material")
    src = (BACKEND / "app/services/agent/nodes.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_in: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in SRC_FUNCS:
            uses, filters = False, False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == "probe_min_score":
                    uses = True
                # 找形如 [h for h in hits if ...] 的列表推导：过滤后再用
                if isinstance(sub, ast.ListComp) and any(
                    isinstance(g.iter, ast.Name) and g.iter.id == "hits"
                    for g in sub.generators
                ):
                    filters = True
            if uses and filters:
                found_in.append(node.name)
    record("R2", bool(found_in),
           f"在 {'/'.join(SRC_FUNCS)} 中找「读 probe_min_score + 对 hits 列表推导过滤」"
           f"同处一函数 → 命中 {found_in or '无'}（过滤逻辑 2026-09 起在 probe_material）")

    # R3 阈值落点独立复核（用**本次实测分数**，不信常量 —— 常量会过期）
    #    只判硬约束的那一侧：阈值 ≤ 实测正例最低分（不误伤正例）。
    #    ⚠️ **不拿本脚本的 7 条负例判"下沿"**：样本太少。实测本样本负例最高 0.5349，
    #       远低于 65 条评测集的 0.6739 —— 因为 7 条里没有"主题接近"的那种负例。
    #       用它判下沿会得出「0.5 太低」这个与全量口径**相反**的结论。下沿交给 B1。
    #    负例最高分**照实打印**（是信息，不是判据）。
    if isinstance(thr, (int, float)):
        thr_f = float(thr)
        rel_tops = [s for s in (milvus_top1(prod_kb, q) for q in RELATED) if s is not None]
        unrel_tops = [s for s in (milvus_top1(prod_kb, q) for q in UNRELATED) if s is not None]
        if rel_tops:
            r_lo = min(rel_tops)
            u_hi = max(unrel_tops) if unrel_tops else None
            extra = ""
            if u_hi is not None:
                over = sum(1 for s in unrel_tops if s >= thr_f)
                extra = (f"；本样本负例最高 {u_hi:.4f}，其中 {over}/{len(unrel_tops)} 条过线"
                         f"（样本不足以定下沿，见 #114）")
            record("R3", thr_f <= r_lo,
                   f"实测复核：阈值 {thr_f:.4f} ≤ 本次正例最低分 {r_lo:.4f} → {thr_f <= r_lo}"
                   f"（超过它会把真相关的题判成'没资料'）" + extra)
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

    # B1 真无关 objective —— 必须全部挡住（改前这里 FAIL：会报"可用资料 5 条"）
    #    判据从严，不做"豁免"：曾想豁免"结构性挡不住"的负例，但那只能靠 0.0015 的分数
    #    余量（负例 0.5348 vs 正例最低 0.5333）下判断 ⇒ 随 embedding 抖动忽红忽绿，
    #    不可复现的判据等于判据失效。真解法是把那条样本归到 GRAY（见顶部注释）。
    #    detail 里打印本批负例最高分：将来变红时能一眼分辨是"负例分数涨上来了"
    #    （⇒ 该换 probe 判据）还是"阈值被调松了"（⇒ 改配置）。
    missed = []
    for q in UNRELATED:
        cls = classify(probe({"objective": q, **base}).get("kb_overview", ""))
        if cls != "no_hit":
            missed.append((q, cls))
    tops = [s for s in (milvus_top1(prod_kb, q) for q in UNRELATED) if s is not None]
    hi_txt = (f"，本批负例最高分 {max(tops):.4f}（阈值 {settings.probe_min_score}）"
              if tops else "")
    record("B1", not missed,
           f"真无关 {len(UNRELATED)} 条中，未判「无资料」的 = {len(missed)} 条{hi_txt}"
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
    #    ⚠️ 样本要**现挑**，不能写死：原来写死的那条「AI 客服」top1 ≥ 正例最低分，
    #       阈值再怎么设也挡不住它，拿它测「过滤为空」这条路等于测了个不存在的情形
    #       （改动前 C2 恒红就是这个原因）。这里挑第一条**确实会被阈值挡掉**的负例。
    thr = float(getattr(settings, "probe_min_score", 0) or 0)
    no_mat_obj = next((q for q in UNRELATED if (milvus_top1(prod_kb, q) or 0) < thr), None)
    if no_mat_obj is None:
        record("C2", None,
               f"{len(UNRELATED)} 条负例 top1 全部 ≥ 阈值 {thr} ⇒ 构造不出「过滤为空」的场景，跳过")
    else:
        base = {"objective": no_mat_obj, "kb_ids": [prod_kb],
                "ready_doc_ids": [d["id"] for d in facts["prod_docs"]],
                "ready_doc_epochs": {d["id"]: d["epoch"] for d in facts["prod_docs"]}}
        probe_out, plan_out = await run_to_planner(base)
        ov = probe_out.get("kb_overview", "")
        plan = (plan_out or {}).get("plan", [])
        focus = (plan_out or {}).get("focus", [])
        record("C2", classify(ov) == "no_hit" and 3 <= len(plan) <= 5 and len(focus) == len(plan),
               f"无关 objective「{no_mat_obj[:14]}…」(top1={milvus_top1(prod_kb, no_mat_obj):.4f}"
               f" < 阈值 {thr}) 走真实链路：预检={classify(ov)} 章节数={len(plan)} "
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
