"""检索质量评测：给「检索到底准不准」装一把尺子。

背景（为什么需要它）
--------------------
`quality_score`（质检分）是**模型自己评的**。改了切片参数、换了 prompt
之后分数涨了 5 分 —— 到底是真变好了，还是这次模型心情好？现在答不上来。
这个脚本回答的是**客观**问题：

    给定调研目标，期望的那份文档有没有被检索进前 3 / 前 5 名（hit@3 / hit@5）？

它走的是**真实链路**（与 `probe()` / `retriever()` 同一个 `search()` 调用、同一份
PG 白名单、同样的 `epochs` 过滤），不是另写一套"看起来对"的检索 ——
判据必须建在实际行为上，不能建在配置回显上。

判据分五组
----------
P 前置    —— 评测集条目数 / 白名单非空。不满足记 SKIP 或**中止**（读不到证据 ≠ 证据是通过）
L 定位自检 —— 先证明"要验的对象是对的"：期望文档真在白名单里、命中数不是全 0
S 判据自检 —— 拿构造数据喂判据函数，证明它既能算对 True 也能算对 False
C 负样例   —— 库里**确实没有**的主题：它的 top1 **相似度**必须 < `probe_min_score`（低于下限才叫挡住）
R 度量    —— 真正的数字：hit@3 / hit@5 / 期望文档名次 + 逐条明细
             + R1 可分性（两类分得开吗）＋ R2 阈值有效性（当前阈值落在空隙里吗）

⚠️ 负样例这一组的设计过程（值得记住）
------------------------------------
第一版我用「期望文档写一个**不存在的 UUID**」当哨兵，跑之前才发现它是**恒真**的：
不存在的 id 永远不可能出现在命中结果里 —— 哪怕 filter 完全失效、把整个集合都返回了，
返回的也都是真实文档 id。也就是说这条判据**永远 PASS**，等于没写。

第二版换成「无关主题 + 阈值比较」，能 FAIL 了，但**方向写反了**（09-18 当天就抓到）：
把 `top1 >= threshold` 判成"被挡住"，而生产代码 `nodes.py:130` 恰恰是
`hits = [h for h in hits if h["distance"] >= probe_min_score]` —— **≥ 阈值 = 有资料**。
根因是照着字段名 `distance` 想当然，没回生产代码核对方向。修正后：
**负样例 top1 相似度必须 < 阈值**。

⚠️ 一个必须记住的命名坑：Milvus 返回的 `distance` 字段，在 **COSINE 度量下存的是余弦相似度**
（越大越相似），不是距离。本脚本的 `top1_distance` 沿用字段名，但**语义是相似度**。

一句话记法：**判据的价值不在跑出 PASS，在于它能 FAIL；写下判据前先问"什么情况它会红"。
而"它会红"之后还要再问一句"红的方向对不对"** —— 方向反的判据比没有判据更危险，
因为它把真问题伪装成通过。

用法
----
    # 1. 看有哪些文档可选（列出 file_name + doc_id + Milvus 实际块数）
    backend/.venv/Scripts/python.exe backend/playground/eval_retrieval.py --list-docs

    # 2. 人工写 eval_set.jsonl（格式见下；expect_doc_ids 必须人工确认，不能"检索到啥就标啥"）

    # 3. 改代码**之前**跑基线
    backend/.venv/Scripts/python.exe backend/playground/eval_retrieval.py --tag baseline

    # 4. 改完再跑，跟基线对比（名次变化比命中率更敏感）
    backend/.venv/Scripts/python.exe backend/playground/eval_retrieval.py --tag after \
        --diff backend/playground/eval_runs/<基线文件>.json

    # 不依赖 PG / Milvus 的判据自检（改判据时先跑这个）
    backend/.venv/Scripts/python.exe backend/playground/eval_retrieval.py --selftest

eval_set.jsonl 每行一条（JSONL，空行与 `#` 开头的行跳过）：
    # 正向：期望文档必须被检索进 top-3 / top-5
    {"objective": "某系统的某模块有哪些功能", "expect_doc_ids": ["<doc-uuid>"], "note": "答案只在这份手册里"}
    # 负样例：库里确实没有这个主题，要求 top1 相似度**低于**阈值（否则 probe 会误判"有资料"）
    {"objective": "量子纠缠实验的数据采集方案", "expect_doc_ids": [], "negative": true, "note": "库里没有相关主题"}

退出码：0 = 全部判据 PASS；1 = 有 FAIL；2 = 中止（前置不满足，如链路没跑通）。
不改任何数据：只读 PG + Milvus。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HERE = Path(__file__).resolve().parent
EVAL_SET = HERE / "eval_set.jsonl"
RUNS_DIR = HERE / "eval_runs"

MIN_ENTRIES = 15        # 少于这个数，命中率不足以说明问题（前置不满足 ⇒ 记 SKIP，不 FAIL）
DEFAULT_TOPK = 15       # 多取一些只为记录期望文档的"名次"；hit@3 / hit@5 从同一份排序里算
HIT3, HIT5 = 3, 5
# ⚠️ **生产口径**：retriever 取 `top_k=6` 条进 prompt（`nodes.py:198`）⇒ `hit@6` 才是
#    「答案到底有没有进 prompt」的真相。**只报 hit@3 会把结论推反** —— 见 `docs/踩坑记录.md` #111：
#    09-22 就是只看 hit@3 + 均名，把 qwen3-rerank 选了上去，而它的 hit@6 比基准**少一条**。
HIT_PROMPT = 6

RESULTS: list[tuple[str, str, str]] = []


class Abort(Exception):
    """前置不满足 ⇒ 中止整组。

    这类情况记 PASS 会虚增"验过了"的量，记 FAIL 会虚增"待修条目"数、误导观察者。
    中止才是诚实的做法。
    """


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def slim(text, n: int = 30) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def summary() -> int:
    print()
    print("-" * 78)
    passed = sum(1 for _, t, _ in RESULTS if t == "PASS")
    skipped = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    failed = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    line = f"判据：{passed}/{len(RESULTS)} PASS"
    if skipped:
        line += f"，{skipped} SKIP"
    if failed:
        line += f"，{failed} FAIL"
    print(line)
    for cid, tag, d in RESULTS:
        if tag == "FAIL":
            print(f"  FAIL {cid}  {d}")
    print("-" * 78)
    return 1 if failed else 0


# ---------------------------------------------------------------- 判据本体（纯函数）
def hit_at_k(hit_doc_ids: list[str], expect: list[str], k: int) -> bool:
    """期望文档**任意一条**出现在前 k 位 ⇒ 命中。纯函数，--selftest 直接喂构造数据。"""
    return any(d in hit_doc_ids[:k] for d in expect)


def rank_of(hit_doc_ids: list[str], expect: list[str]) -> int | None:
    """期望文档的最高名次（1 起）；一条都不在 ⇒ 返回 None。

    取不到值就用 None，**不要用 0 或 -1 兜底** —— 兜底值会让"没取到"和"取到第 0 名"不可区分。
    """
    for i, d in enumerate(hit_doc_ids):
        if d in expect:
            return i + 1
    return None


def negative_verdict(top1: float | None, threshold: float) -> tuple[bool, str]:
    """负样例判据：无关主题的 top1 **相似度**必须 < 阈值（低于下限 ⇒ probe 判"无资料"）。

    ⚠️ 这里的 top1 取自 Milvus 返回的 `distance` 字段，但 **COSINE 度量下该字段存的是
    余弦相似度（越大越相似）**，不是距离 —— Milvus 把不同度量的结果统一叫 distance，
    是命名坑。判断方向必须回到生产代码去核对，不能按字段名猜：

        nodes.py:130  `hits = [h for h in hits if (h.get("distance") or 0) >= probe_min_score]`
                      ⇒ **≥ 阈值 = 有资料**（保留），< 阈值 = 无资料（丢掉）

    所以负样例（库里确实没有的主题）必须**低于**阈值才算被挡住。
    2026-09-18 修：本函数第一版把方向写反了（写成 `>= threshold` ⇒ PASS），
    结果 C 组输出整个颠倒 —— 真会被误判的（相似度高的负样例）显示 PASS，
    真正被挡住的显示 FAIL。根因是照着字段名 `distance` 想当然。

    top1 为 None（检索一条都没返回）时记 PASS 并在说明里标注"没有证据"——
    它确实没被误判，但也说明不了相似度分布；这种情况不该当成有力的证据。
    """
    if top1 is None:
        return True, "无关主题没返回任何结果 ⇒ 未误判（注意：也没拿到相似度证据）"
    if top1 < threshold:
        return True, f"top1 相似度 {top1:.4f} < 阈值 {threshold} ⇒ 被挡住（probe 判无资料）"
    return False, (f"top1 相似度 {top1:.4f} ≥ 阈值 {threshold} ⇒ 无关主题会被 probe 判成「有资料」，"
                   "于是白烧一轮检索 + 写作，报告还容易跑题（阈值偏松，见 R2）")


def negative_state(top1: float | None, thr: float, pos_min: float | None) -> str:
    """负样例三态 —— 抽成纯函数，好让 `--selftest` 喂构造数据验证（S6 组）。

        "blocked"    被挡住（top1 < 阈值，或根本没返回结果）⇒ 符合预期
        "unfixable"  过了阈值，但 top1 ≥ 正例最低分 ⇒ **挡它必误伤正例**，单一阈值极限（#114）
        "risky"      过了阈值，且 top1 < 正例最低分 ⇒ 明明调阈值就能挡住却没挡 ⇒ 可修，记 FAIL

    区分后两者的意义：把"数据极限"和"配置偏松"分开，否则前者会变成恒红的假红。
    """
    if top1 is None or top1 < thr:
        return "blocked"
    if pos_min is not None and top1 >= pos_min:
        return "unfixable"
    return "risky"


def threshold_verdict(pos_d: list[float], neg_d: list[float], thr: float
                      ) -> list[tuple[str, bool | None, str]]:
    """R1 可分性 + R2 阈值有效性 —— 抽成**纯函数**，好让 `--selftest` 喂构造数据验证。

    返回 [(判据号, True/False/None, 说明)]，None = SKIP（不判）。

    R1 可分性：正例最差 > 负例最好 ⇒ PASS（调阈值有救）；否则 SKIP。
      ⚠️ 重叠时**不记 FAIL**：本语料的重叠是已知数据事实（#114）—— 12 条负例里有 8 条是
      "主题技术性很强、但答案不在语料里"，与正例语义同域，不是标注标错。恒红的判据会
      淹没真正可动的 FAIL，也让脚本退出码失去意义。

    R2 阈值有效性：可分 ⇒ 阈值必须落在空隙 (负例最好, 正例最差) 内，两侧都满足；
      重叠 ⇒ 空隙不存在，只判可行动的那一侧「阈值 ≤ 正例最差」（不误伤正例）。
      ⚠️ 重叠时若照套"落在空隙内"，会算出**反向区间**并打印出来（实测出现过
      "建议上调到 0.67~0.53"这种自相矛盾的结论），而且恒红。
    """
    lo, hi = max(neg_d), min(pos_d)
    if hi > lo:
        r1: tuple[str, bool | None, str] = (
            "R1", True,
            f"可分性：正例最差 {hi:.4f} > 负例最好 {lo:.4f} ⇒ 两类分得开，调阈值有救")
    else:
        r1 = ("R1", None,
              f"可分性：正例最差 {hi:.4f} ≤ 负例最好 {lo:.4f} ⇒ 两类重叠，**不存在零错阈值**"
              "（已知数据事实，不是标注错误，见 #114）：再调阈值只能二选一，换判据才有救")

    if hi > lo:
        ok = lo < thr < hi
        r2: tuple[str, bool | None, str] = (
            "R2", ok,
            f"阈值有效性：probe_min_score={thr} 必须落在空隙 ({lo:.4f}, {hi:.4f}) 内；"
            + ("实际落在里面 ⇒ 既挡得住负例，又不误伤正例" if ok
               else (f"**低于下沿 {lo:.4f}** ⇒ 挡不住负例：相似度 {lo:.4f} 的无关主题会被判成"
                     f"「有资料」（建议上调到 {lo:.2f}~{hi:.2f} 之间）" if thr <= lo
                     else f"**高于上沿 {hi:.4f}** ⇒ 会误伤正例：明明有资料却判「没资料」")))
    else:
        ok = thr <= hi
        r2 = ("R2", ok,
              f"阈值有效性（两类重叠，无零错阈值）：正例最差 {hi:.4f} ≤ 负例最好 {lo:.4f} ⇒ "
              f"只判可行动一侧 probe_min_score={thr} ≤ {hi:.4f}；"
              + ("通过 ⇒ 不误伤正例（剩下多少负例过线由数据决定，不是配置问题）" if ok
                 else f"**超过上沿 {hi:.4f}** ⇒ 会误伤正例：明明有资料却判「没资料」"))
    return [r1, r2]


# ---------------------------------------------------------------- S 组：判据自检
def selftest() -> int:
    head("S 组：判据自检（把构造数据喂给同一套判据 —— 证明它不恒真也不恒假）")
    hits = ["A", "B", "C", "D", "E", "F"]        # 假装这是检索返回的 document_id 列表
    cases = [
        ("S1a", ["A"], 3, True, "期望文档排第 1 ⇒ hit@3 应命中"),
        ("S1b", ["C"], 3, True, "排第 3（边界内）⇒ hit@3 应命中"),
        ("S1c", ["D"], 3, False, "排第 4（边界外）⇒ hit@3 不能命中"),
        ("S1d", ["F"], 5, False, "排第 6 ⇒ hit@5 不能命中"),
        ("S1e", ["B", "Z"], 5, True, "多条期望取任一 ⇒ 命中"),
        ("S1f", ["Z"], 5, False, "一条都没出现 ⇒ 不命中"),
        ("S1g", ["F"], HIT_PROMPT, True, "排第 6（= 进 prompt 的边界内）⇒ hit@6 应命中"),
        ("S1h", ["Z"], HIT_PROMPT, False, "期望文档不在返回里 ⇒ hit@6 不能命中"),
    ]
    for cid, expect, k, want, why in cases:
        got = hit_at_k(hits, expect, k)
        record(cid, got == want, f"{why}（期望 {want}，实得 {got}）")

    # S4：口径漂移守卫 —— HIT_PROMPT 必须与生产 retriever 的 top_k 一致。
    #   不等 ⇒ 报出来的 hit@6 根本不是"进 prompt 的命中率"，会重演 #111 的推反结论。
    #   注意 nodes.py 有**两处** top_k（probe 的 top_k=5 / retriever 的 top_k=6），
    #   所以锚定 retriever 那行独有的 `document_ids=state` 后缀，避免匹到 probe。
    nodes_py = HERE.parent / "app" / "services" / "agent" / "nodes.py"
    m = re.search(r"top_k\s*=\s*(\d+)\s*,\s*document_ids\s*=\s*state", nodes_py.read_text(encoding="utf-8"))
    prod_k = int(m.group(1)) if m else None
    record("S4a", prod_k == HIT_PROMPT,
           f"口径守卫：HIT_PROMPT={HIT_PROMPT} 应等于生产 retriever 的 top_k={prod_k}"
           + ("" if prod_k == HIT_PROMPT else " ⇒ 尺子与生产口径**已脱钩**，报出来的 hit@6 不可信"))

    r5 = rank_of(hits, ["E"])
    record("S2a", r5 == 5, f"名次计算：E 在第 5 位（实得 {r5!r}）")
    r_none = rank_of(hits, ["Z"])
    record("S2b", r_none is None, f"取不到名次时返回 None 而不是 0（实得 {r_none!r}）")

    near_ok, _ = negative_verdict(0.10, 0.41)     # 完全无关：相似度低 ⇒ 应 PASS（被挡住）
    far_ok, _ = negative_verdict(0.90, 0.41)      # 相似度太高 ⇒ 应 FAIL（没挡住）
    none_ok, _ = negative_verdict(None, 0.41)     # 没结果 ⇒ 不误判，记 PASS
    record("S3a", near_ok is True, f"负样例判据：相似度 0.10 < 0.41 ⇒ PASS（实得 {near_ok}）")
    record("S3b", far_ok is False, f"负样例判据：相似度 0.90 ≥ 0.41 ⇒ 能 FAIL（实得 {far_ok}）"
                                    " —— 这条能红，下面的 C 组结论才有意义")
    record("S3c", none_ok is True, f"负样例判据：无结果 ⇒ PASS 且标注无证据（实得 {none_ok}）")

    # S5：R1 / R2 阈值判定 —— 喂四种构造数据，证明它既不恒真也不恒假。
    #    ⚠️ 本语料实测形态是「两类重叠 + 阈值 0.5」，即 S5a；S5b 是它的反面（必须能红）。
    for cid, pos, neg, thr, want_r2, want_r1, why in [
        ("S5a", [0.53, 0.70], [0.20, 0.67], 0.50, True, None,
         "重叠 + 阈值 ≤ 正例最差 ⇒ R2 绿、R1 记 SKIP（当前配置的形态）"),
        ("S5b", [0.53, 0.70], [0.20, 0.67], 0.90, False, None,
         "重叠 + 阈值过高 ⇒ R2 必须红（这条能红，R2 才有意义）"),
        ("S5c", [0.60, 0.70], [0.20, 0.50], 0.55, True, True,
         "可分 + 阈值落在空隙内 ⇒ R1 / R2 都绿"),
        ("S5d", [0.60, 0.70], [0.20, 0.50], 0.45, False, True,
         "可分 + 阈值低于下沿 ⇒ R2 必须红（挡不住负例）"),
    ]:
        v = {c: o for c, o, _ in threshold_verdict(pos, neg, thr)}
        record(cid, v["R2"] == want_r2 and v["R1"] == want_r1,
               f"{why}（期望 R2={want_r2}／R1={want_r1!r}，实得 R2={v['R2']}／R1={v['R1']!r}）")

    # S6：C1 负样例三态 —— 关键是「结构性不可挡」和「该挡没挡」必须分开，
    #     否则前者会变成恒红的假红（#114 的坑）；S6c 证明"该挡没挡"这条真的能红。
    for cid, top1, thr, pos_min, want, why in [
        ("S6a", 0.30, 0.50, 0.53, "blocked", "top1 < 阈值 ⇒ 被挡住"),
        ("S6b", 0.60, 0.50, 0.53, "unfixable", "过阈值且 ≥ 正例最低分 ⇒ 结构性不可挡（记 SKIP）"),
        ("S6c", 0.45, 0.40, 0.53, "risky", "过阈值但 < 正例最低分 ⇒ 该挡没挡（能红）"),
        ("S6d", None, 0.50, 0.53, "blocked", "没返回结果 ⇒ 未误判"),
    ]:
        got = negative_state(top1, thr, pos_min)
        record(cid, got == want, f"C1 三态：{why}（期望 {want}，实得 {got}）")
    return summary()


# ---------------------------------------------------------------- 数据装载
def load_eval_set(path: str | Path | None = None) -> list[dict]:
    # 同时接受 str 与 Path：调用方（含 rag/评测脚本）两种都可能传
    src = Path(path) if path else EVAL_SET
    if not src.is_file():
        raise Abort(
            f"评测集不存在：{src}\n"
            "         先跑 --list-docs 看有哪些文档可选，再按脚本头部注释的格式写 eval_set.jsonl"
        )
    entries: list[dict] = []
    for no, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as exc:
            raise Abort(f"{src.name} 第 {no} 行不是合法 JSON：{exc}") from exc
        if not isinstance(obj, dict):
            raise Abort(f"{src.name} 第 {no} 行不是 JSON 对象")
        obj["_line"] = no
        entries.append(obj)
    return entries


def is_filled(entry: dict) -> bool:
    """有效条目 = 目标写了 + （正向条目要有期望文档 / 负样例要有 negative 标记）。"""
    obj = str(entry.get("objective") or "")
    if not obj.strip() or "TODO" in obj:
        return False
    if entry.get("negative") is True:
        return True
    return bool(entry.get("expect_doc_ids"))


async def load_whitelist(kb_filter: list[str] | None) -> dict:
    """取「可检索文档白名单」—— **口径与 `task_queue._run` 完全一致**：

        select id, vector_epoch from documents where kb_id in (...)

    刻意**不按 status 过滤**：生产就是这么查的。评测必须反映真实链路，
    不能顺手"优化"成比生产更干净的口径 —— 否则量出来的不是线上行为。
    （`--list-docs` 会把 status 和 Milvus 实际块数打出来，failed/0 块的文档肉眼可见。）
    """
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Document, KnowledgeBase

    try:
        async with SessionLocal() as s:
            kb_rows = (await s.execute(select(KnowledgeBase.id, KnowledgeBase.name))).all()
            doc_rows = (await s.execute(
                select(Document.id, Document.kb_id, Document.file_name, Document.status,
                       Document.chunk_count, Document.vector_epoch)
            )).all()
    except Exception as exc:      # noqa: BLE001
        # 连不上库 = 读不到证据，必须中止（不是"白名单为空"，两者的修法完全不同）
        raise Abort(f"连不上 PostgreSQL（本机要先起 docker compose 的 postgres）：{exc}") from exc

    kb_name = {str(k): n for k, n in kb_rows}
    docs = [
        {
            "id": str(did), "kb_id": str(kid), "kb_name": kb_name.get(str(kid), "?"),
            "file_name": fname, "status": status,
            "chunk_count": int(chunks or 0), "epoch": int(epoch or 0),
        }
        for did, kid, fname, status, chunks, epoch in doc_rows
    ]

    if kb_filter:
        chosen: list[str] = []
        for key in kb_filter:
            kid = _match_kb(docs, key)
            if kid is None:
                raise Abort(f"--kb {key!r} 既不是知识库名也不是 id 前缀（库里有 {len(kb_name)} 个知识库）")
            chosen.append(kid)
        keep = set(chosen)
        docs = [d for d in docs if d["kb_id"] in keep]

    return {
        "docs": docs,
        "ids": [d["id"] for d in docs],
        "epochs": {d["id"]: d["epoch"] for d in docs},
        "kb_ids": sorted({d["kb_id"] for d in docs}),
    }


def _match_kb(docs: list[dict], key: str) -> str | None:
    k = key.strip().lower()
    for d in docs:
        if d["kb_id"].lower() == k or d["kb_id"].lower().startswith(k):
            return d["kb_id"]
    for d in docs:
        if d["kb_name"].lower() == k:
            return d["kb_id"]
    return None


# ---------------------------------------------------------------- 模式一：列文档
async def list_docs(kb_filter: list[str] | None) -> int:
    wl = await load_whitelist(kb_filter)
    if not wl["docs"]:
        raise Abort("PG 里这批知识库一篇文档都没有 —— 先上传并解析完文档再来")

    from app.services.rag.milvus_client import list_chunks_by_document

    head("可评测文档（挑 expect_doc_ids 用；Milvus 块数 = 0 的文档别当期望文档）")
    print(f"白名单 {len(wl['docs'])} 篇 / {len(wl['kb_ids'])} 个知识库"
          f"（口径与生产一致：该 kb 下全部文档 + 各自 vector_epoch）\n")
    for d in wl["docs"]:
        try:
            chunks = len(list_chunks_by_document(d["id"]))
        except Exception as exc:      # noqa: BLE001
            chunks = f"查询失败({slim(exc, 20)})"
        print(f"  {d['kb_name']}  [{d['status']:<8}] epoch={d['epoch']}  "
              f"Milvus 块数={chunks}  {d['file_name']}")
        print(f"    doc_id = {d['id']}   PG chunk_count = {d['chunk_count']}")

    print("\n挑好之后往 eval_set.jsonl 里写，一条一行（正向条目 + 至少一条负样例）：")
    print('  {"objective": "<调研目标>", "expect_doc_ids": ["<doc_id>"], "note": "<为什么这份文档算标准答案>"}')
    return 0


# ---------------------------------------------------------------- 模式二：跑评测
async def run_eval(args) -> int:
    from app.core.config import settings          # 只在真的跑评测时才需要

    entries = load_eval_set(Path(args.set) if args.set else None)
    filled = [e for e in entries if is_filled(e)]
    unfilled = len(entries) - len(filled)

    head("P 组：前置")
    if len(filled) < MIN_ENTRIES:
        record("P1", None,
               f"有效条目 {len(filled)} 条，少于要求的 {MIN_ENTRIES} 条"
               + (f"（另有 {unfilled} 条未填）" if unfilled else "")
               + " —— 样本不足，下面的命中率只能当冒烟看（要真实结论得先把语料和条目补够）")
    else:
        record("P1", True, f"有效条目 {len(filled)} 条（要求 ≥{MIN_ENTRIES}）"
                           + (f"；另有 {unfilled} 条未填" if unfilled else ""))
    if not filled:
        raise Abort("评测集里一条有效条目都没有 —— 先把 eval_set.jsonl 填上再跑")

    wl = await load_whitelist(args.kb)
    if not wl["docs"]:
        raise Abort("可检索白名单为空（PG 里这批知识库没文档）—— 检索不可能命中，先入库文档")
    record("P2", True, f"白名单 {len(wl['docs'])} 篇文档 / {len(wl['kb_ids'])} 个知识库")
    wl_ids = set(wl["ids"])
    doc_of = {d["id"]: d for d in wl["docs"]}

    # ---------- L 组：定位自检（先证明"要验的对象"是对的，再谈命中）
    head("L 组：定位自检（期望文档必须真的可被检索到，否则永远 miss）")
    runnable: list[dict] = []
    skipped = 0
    for e in filled:
        line = e["_line"]
        expect = [str(x) for x in (e.get("expect_doc_ids") or [])]
        if e.get("negative") is True:
            if expect:
                record(f"L1#{line}", False,
                       f"负样例（第 {line} 行）不该带 expect_doc_ids（现在有 {expect}）"
                       " —— 要么它是正向条目，要么期望文档写错了位置")
            runnable.append(e)
            continue
        if not expect:
            record(f"L0#{line}", None, f"第 {line} 行没有 expect_doc_ids，也无法当负样例 ⇒ 记 SKIP")
            skipped += 1
            continue
        outside = [d for d in expect if d not in wl_ids]
        if outside:
            record(f"L0#{line}", None,
                   f"第 {line} 行：期望文档 {outside} 不在可检索白名单里 ⇒ 该条记 SKIP，不计入命中率"
                   "（先确认：是不是别的 kb 的文档 / 还没解析完 / vector_epoch 变了）")
            skipped += 1
            continue
        runnable.append(e)

    if not runnable:
        raise Abort("没有一条可跑的条目（全被判 SKIP）—— 先按上面的 L0 提示修 eval set")

    # ---------- R 组前半：真实检索
    head(f"R 组：真实检索（embed_query + search，top_k={args.top_k}；与 probe/retriever 同一条链路）")
    from app.services.rag import get_embedder
    from app.services.rag.milvus_client import search

    embedder = get_embedder()
    kb_ids = args.kb or wl["kb_ids"]
    rows: list[dict] = []
    for e in runnable:
        objective = str(e["objective"])
        expect = [str(x) for x in (e.get("expect_doc_ids") or [])]
        neg = e.get("negative") is True
        try:
            vec = embedder.embed_query(objective)
            hits = search(vec, kb_ids, top_k=args.top_k,
                          document_ids=wl["ids"], epochs=wl["epochs"])
        except Exception as exc:      # noqa: BLE001
            raise Abort(f"检索调用本身失败了（不是「没命中」，是链路断了）：{exc}") from exc

        # top1 是 COSINE 相似度（越大越相关）—— C 组负样例判据量的就是它。
        # 字段名叫 distance 是 Milvus 的坑，语义是「相似度」，别按名字猜方向（见文件头）。
        top1 = hits[0].get("distance") if hits else None
        hit_ids = [h.get("document_id") for h in hits]
        rows.append({
            "line": e["_line"], "objective": objective, "expect": expect, "negative": neg,
            "note": e.get("note"),
            "hit_ids": hit_ids,
            "top1_distance": top1,
            "top_files": [(doc_of.get(d, {}).get("file_name") or d) for d in hit_ids[:HIT5]],
            "hit3": (not neg) and hit_at_k(hit_ids, expect, HIT3),
            "hit5": (not neg) and hit_at_k(hit_ids, expect, HIT5),
            "hit_prompt": (not neg) and hit_at_k(hit_ids, expect, HIT_PROMPT),
            "rank": rank_of(hit_ids, expect) if not neg else None,
        })

    # 0 命中会伪装成"全都没命中" —— 这是链路故障的指纹，必须先挡住
    positives = [r for r in rows if not r["negative"]]
    if positives and all(not r["hit_ids"] for r in positives):
        raise Abort(
            "所有正向条目都返回 0 条命中 ⇒ 这是**链路没跑通**的指纹，不是「检索质量差」。\n"
            "         依次查：① --list-docs 里 Milvus 块数是不是 0；"
            "② 白名单 / vector_epoch 是否与 Milvus 里的 metadata.epoch 一致；"
            "③ filter 里的 kb_id 是否拼对"
        )
    empty_rows = [r["line"] for r in positives if not r["hit_ids"]]
    record("L2", True,
           f"正向条目 {len(positives)} 条各取 top-{args.top_k}，其中 "
           f"{len(positives) - len(empty_rows)} 条有命中"
           + (f"；返回 0 条的是第 {empty_rows} 行（单条为空可以有，全都为空不行）" if empty_rows else ""))

    # ---------- C 组：负样例（这一组是唯一能证明"命中率不是恒真"的东西）
    # （组标题挪到判定处再打：判定要用正例分数，见下面的「C 组逐条判定」）
    negatives = [r for r in rows if r["negative"]]
    if not negatives:
        record("C1", None,
               "评测集里没有负样例 ⇒ 无法排除「检索什么都返回、命中率虚高」。补一条："
               '{"objective":"<库里没有的主题>", "expect_doc_ids":[], "negative":true}')
    # ⚠️ 负样例的逐条判定**不在这里**：判「结构性不可挡」要用正例最低分做基准，
    #    所以整段挪到下面「正例 / 负例相似度分布」算完之后（搜「C 组逐条判定」）。

    if skipped:
        print(f"[提示] 另有 {skipped} 条因期望文档不在白名单被记 SKIP，未计入命中率")
    if positives and all(r["rank"] is None for r in positives):
        print("[观察] 所有正向条目都没命中，但检索**确实返回了内容** ⇒ 机器分不清"
              "是「检索质量真的差」还是「期望标注标错了」，看下表逐条人工判断"
              "（这也是不把它写成 FAIL 的原因：两种原因的修法完全相反）")

    # ---------- R 组后半：数字
    head("R 组：数字（这就是「尺子」；改前改后各跑一次，比的是这两组数字）")
    print(f"{'目标':<32}{'hit@3':<7}{'hit@5':<7}{'hit@6*':<7}{'名次':<6}实际命中的前 5 篇")
    print(f"{'':<32}{'排序':<7}{'':<7}{'进prompt':<7}")
    for r in rows:
        if r["negative"]:
            continue
        mark3 = "✅" if r["hit3"] else "❌"
        mark5 = "✅" if r["hit5"] else "❌"
        markp = "✅" if r["hit_prompt"] else "❌"
        rank = r["rank"] if r["rank"] is not None else f">{args.top_k}"
        tail = ("｜".join(slim(f, 14) for f in r["top_files"])) if not r["hit5"] else ""
        print(f"  {slim(r['objective'], 30):<32}{mark3:<7}{mark5:<7}{markp:<7}{str(rank):<6}{tail}")

    n = len(positives) or 1
    h3 = sum(1 for r in positives if r["hit3"])
    h5 = sum(1 for r in positives if r["hit5"])
    hp = sum(1 for r in positives if r["hit_prompt"])
    ranks = [r["rank"] for r in positives if r["rank"] is not None]
    avg_rank = round(sum(ranks) / len(ranks), 2) if ranks else None
    print()
    print(f"  hit@3 = {h3}/{len(positives)} = {h3 / n:.0%}      "
          f"hit@5 = {h5}/{len(positives)} = {h5 / n:.0%}      平均名次 = {avg_rank}")
    print(f"  ⭐ hit@6（* 进 prompt 的生产口径，retriever top_k={HIT_PROMPT}）= {hp}/{len(positives)} = {hp / n:.0%}"
          f"   ← **报质量必须带上它**，它掉 1 条就是真丢资料")

    # 相似度分布：块很少的语料上 hit@k 会全是 100%（查什么都命中），这时只有相似度有分辨率 ——
    # 它同时回答"probe 的阈值调得对不对"：正例的最差相似度必须比负例的最好相似度更高。
    pos_d = [r["top1_distance"] for r in positives if r["top1_distance"] is not None]
    neg_d = [r["top1_distance"] for r in negatives if r["top1_distance"] is not None]
    if pos_d:
        mid = sorted(pos_d)[len(pos_d) // 2]
        line = (f"  top1 相似度分布（COSINE，越大越相关；字段名叫 distance 是 Milvus 的坑）："
                f"正例 最低 {min(pos_d):.4f}／中位 {mid:.4f}／最高 {max(pos_d):.4f}")
        if neg_d:
            line += f"；负例 最高 {max(neg_d):.4f}（阈值 {settings.probe_min_score}）"
        print(line)

    if len(pos_d) >= 3 and len(neg_d) >= 3:
        # R1 / R2 的具体判定在 threshold_verdict() 里（纯函数，可被 --selftest 喂构造数据验红）
        for cid, ok, msg in threshold_verdict(pos_d, neg_d, settings.probe_min_score):
            record(cid, ok, msg)
    else:
        record("R1", None,
               f"相似度可分性判据需要正例 / 负例各 ≥3 条（现在 {len(pos_d)}/{len(neg_d)}）"
               " —— 样本不够时这个数字看着好看也不作数")
        record("R2", None, "阈值有效性判据需要正例 / 负例各 ≥3 条，样本不够不判")

    # ---------- C 组逐条判定（放在这里：判「结构性不可挡」要用正例最低分做基准）
    # 负样例三态：
    #   PASS  top1 < 阈值 ⇒ 被 probe 挡住，符合预期
    #   FAIL  top1 ≥ 阈值，且 **< 正例最低分** ⇒ 明明调阈值就能挡住却没挡 ⇒ 阈值偏松，可修
    #   SKIP  top1 ≥ 阈值，且 **≥ 正例最低分** ⇒ 挡它必误伤正例 —— 单一阈值的数学极限
    #         （#114），不是配置错误，修法是换判据（见 probe_sep_lab.py）。
    # ⚠️ SKIP ≠ "没问题"：它照实记下"probe 确实会把这 N 条误判成有资料"这个真实缺陷，
    #    只是这个洞调阈值补不上 —— 记 FAIL 只会得到一个恒红的判据，
    #    淹没真正可动的 FAIL，也让脚本退出码失去意义（`--selftest` 的 S3b 守着它不恒真）。
    if negatives:
        head(f"C 组：负样例（无关主题的相似度必须低于阈值；阈值 = probe_min_score = {settings.probe_min_score}）")
        pos_min_d = min(pos_d) if pos_d else None
        blocked, stuck, risky = 0, 0, 0
        for r in negatives:
            top1 = r["top1_distance"]
            state = negative_state(top1, settings.probe_min_score, pos_min_d)
            _, why = negative_verdict(top1, settings.probe_min_score)
            if state == "blocked":
                blocked += 1
                record(f"C1#{r['line']}", True, why)
            elif state == "unfixable":
                stuck += 1
                record(f"C1#{r['line']}", None,
                       f"top1 相似度 {top1:.4f} ≥ 阈值 {settings.probe_min_score} ⇒ probe 会把它判成"
                       f"「有资料」（**真实缺陷，非误报**）；但它同时 ≥ 正例最低分 {pos_min_d:.4f}"
                       f" ⇒ 挡它必误伤正例，属单一阈值极限（#114）：调阈值救不了，只能换判据")
            else:
                risky += 1
                record(f"C1#{r['line']}", False, why)
        record("C1", risky == 0,
               f"负样例汇总：{len(negatives)} 条 = 被挡住 {blocked} + 结构性不可挡 {stuck}"
               f" + **该挡没挡 {risky}**（前两类都会让 probe 误判，区别只在后者调阈值能救）")

    # ---------- 存档（改前改后对比要的是事实，不是回忆）
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{args.tag}.json"
    out.write_text(json.dumps({
        "when": datetime.now().isoformat(timespec="seconds"),
        "tag": args.tag, "top_k": args.top_k, "kbs": wl["kb_ids"],
        "whitelist_size": len(wl["docs"]),
        "probe_min_score": settings.probe_min_score,
        "hit3": f"{h3}/{len(positives)}", "hit5": f"{h5}/{len(positives)}",
        "hit6": f"{hp}/{len(positives)}", "hit_prompt_k": HIT_PROMPT,
        "avg_rank": avg_rank,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果存档：{out}")

    if args.diff:
        print()
        diff(Path(args.diff), out)
    return summary()


# ---------------------------------------------------------------- 模式三：对比两轮
def diff(before_path: Path, after_path: Path) -> None:
    head(f"对比：{before_path.name} → {after_path.name}")
    if not before_path.is_file():
        record("D0", False, f"基线文件不存在：{before_path}")
        return
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    old = {r["objective"]: r for r in before.get("rows", []) if not r.get("negative")}
    new = {r["objective"]: r for r in after.get("rows", []) if not r.get("negative")}

    both = [o for o in new if o in old]
    record("D1", bool(both), f"两轮共有的正向目标 {len(both)} 条（口径一致才能比）")
    if not both:
        return

    print(f"\n  {'目标':<30}{'名次变化':<14}{'hit@5 变化'}")
    up = down = same = 0
    for o in both:
        b, a = old[o], new[o]
        br, ar = b.get("rank"), a.get("rank")
        if br is None and ar is None:
            arrow, same = "都未命中", same + 1
        elif br is None:
            arrow, up = f">{before['top_k']} → {ar}", up + 1
        elif ar is None:
            arrow, down = f"{br} → >{after['top_k']}", down + 1
        elif ar < br:
            arrow, up = f"{br} → {ar} ↑", up + 1
        elif ar > br:
            arrow, down = f"{br} → {ar} ↓", down + 1
        else:
            arrow, same = f"{br} → {ar}", same + 1
        b5, a5 = "✅" if b.get("hit5") else "❌", "✅" if a.get("hit5") else "❌"
        print(f"  {slim(o, 28):<30}{arrow:<14}{b5} → {a5}")

    print(f"\n  hit@3：{before.get('hit3')} → {after.get('hit3')}"
          f"      hit@5：{before.get('hit5')} → {after.get('hit5')}"
          f"      平均名次：{before.get('avg_rank')} → {after.get('avg_rank')}")
    if before.get("hit6") or after.get("hit6"):
        print(f"  ⭐ hit@6（进 prompt 的生产口径）：{before.get('hit6') or '（旧存档无此字段）'}"
              f" → {after.get('hit6') or 'n/a'}   ← 这一列掉了才是真丢资料")
    print(f"  名次改善 {up} 条 / 变差 {down} 条 / 不变 {same} 条")
    print("  ⇒ 只看总命中率会漏掉「名次从 8 提到 4」这类改善，两列一起看")


# ---------------------------------------------------------------- 入口
def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量评测（hit@3 排序口径 / hit@6 生产口径）")
    parser.add_argument("--list-docs", action="store_true", help="列出可评测文档（挑期望文档用）")
    parser.add_argument("--selftest", action="store_true", help="只跑判据自检（不连 PG / Milvus）")
    parser.add_argument("--tag", default="run", help="本次结果标签（存档文件名用）")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOPK, help=f"检索条数（默认 {DEFAULT_TOPK}）")
    parser.add_argument("--kb", action="append", default=None, help="只评测某知识库（名字或 id 前缀，可重复）")
    parser.add_argument("--set", default=None,
                        help="用哪份评测集（默认 eval_set.jsonl —— 2026-09-22 起为 65 条正本）；"
                             "传 eval_set.v1.jsonl 可跑历史 24 条基线做对照")
    parser.add_argument("--diff", default=None, help="跑完后与某份历史结果对比（传 eval_runs/*.json）")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    async def _main() -> int:
        if args.list_docs:
            return await list_docs(args.kb)
        return await run_eval(args)

    try:
        return asyncio.run(_main())
    except Abort as exc:
        print(f"\n[ABORT] {exc}")
        print("-" * 78)
        return 2


if __name__ == "__main__":
    sys.exit(main())
