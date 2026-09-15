"""T87 验收：retriever 复用 probe 的「无资料」结论 + probe 事件文案

背景
----
T86 端到端发现：probe 判出「资料与主题不相关」，但这个结论没传给 retriever，
retriever 照样按 kb_id 检索，把无关片段当证据交给 synthesizer，
报告于是变成「资料未覆盖」+「大段无关产品介绍」的混合体（质检自己指控跑题）。

期望改动（三处）
----------------
1. probe() 两个分支各返回 has_material: True/False
2. state.py 加 has_material 字段
3. retriever() 开头：has_material 为 False 时直接给空证据（长度与 plan 齐平）

本脚本验的是「改动真的生效」，不是「代码看起来对」。做法：
进程内直调 retriever，构造两个只在「有无资料」上不同的 state，看检索行为是否分岔。

判据（改前应 FAIL，改后应 PASS）
--------------------------------
R1 静态：retriever 短路用的状态字段名，与 probe 实际产出的字段名一致
R2 语义：int 0 / None 都不是 False 的同一对象（`is False` 的取值实测）
B1 对照：has_material=True → 真检索、有 hits（证明没误伤正常路径）
B2 实验：has_material=False（首次链路形态，retries 键缺失）→ 必须空 hits
B3 实验：has_material=False（质检回退后形态，retries=0）→ 必须空 hits
P1 probe 返回字段是否包含事件文案所需的键
P2 事件文案实测：模拟 task_queue 里那几行，打印 detail

不改任何数据，不发任务，纯只读 + 进程内直调。
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[str, str, str]] = []


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


NODES = BACKEND / "app/services/agent/nodes.py"


# ---------------------------------------------------------------- 前置数据
async def load_facts() -> dict:
    """从 PG 取真实 kb / ready 文档（不硬编码 UUID）。"""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Document, KnowledgeBase

    async with SessionLocal() as s:
        kbs = (await s.execute(select(KnowledgeBase.id, KnowledgeBase.name))).all()
        docs = (await s.execute(
            select(Document.id, Document.kb_id, Document.status, Document.vector_epoch)
        )).all()

    by_kb: dict[str, list[dict]] = {}
    for did, kb_id, status, epoch in docs:
        by_kb.setdefault(str(kb_id), []).append(
            {"id": str(did), "status": status, "epoch": int(epoch)}
        )

    best_id, best_docs, best_name = None, [], None
    for kid, name in kbs:
        ready = [d for d in by_kb.get(str(kid), []) if d["status"] == "ready"]
        if len(ready) > len(best_docs):
            best_id, best_docs, best_name = str(kid), ready, name
    return {"kb_id": best_id, "kb_name": best_name, "docs": best_docs}


def build_state(facts: dict, *, has_material, with_retries: bool) -> dict:
    """构造一份最小可用 state。plan 给 2 章，方便看 evidence 是否与 plan 齐平。"""
    st = {
        "objective": "知研 KnowPilot 是什么？",
        "kb_ids": [facts["kb_id"]],
        "plan": ["产品定位与目标用户", "技术架构与核心能力"],
        "ready_doc_ids": [d["id"] for d in facts["docs"]],
        "ready_doc_epochs": {d["id"]: d["epoch"] for d in facts["docs"]},
    }
    if has_material is not None:
        st["has_material"] = has_material
    if with_retries:
        st["retries"] = 0
    return st


# ---------------------------------------------------------------- R 组：静态
def run_static(facts: dict) -> None:
    head("R 组 · 短路条件是否指向 probe 真正产出的字段")

    src = NODES.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def returned_keys(fname: str) -> set[str]:
        """收集某个函数里所有 return {..} 字典字面量的键。"""
        keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fname:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                        for k in sub.value.keys:
                            if isinstance(k, ast.Constant):
                                keys.add(k.value)
        return keys

    def short_circuit_fields(fname: str) -> set[str]:
        """收集函数里出现在 `state.get("X") is False` / `is True` 中的 X。"""
        fields: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fname:
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Compare):
                        continue
                    left = sub.left
                    if not isinstance(left, ast.Call):
                        continue
                    if not (isinstance(left.func, ast.Attribute) and left.func.attr == "get"):
                        continue
                    if not left.args or not isinstance(left.args[0], ast.Constant):
                        continue
                    # 右侧是比较对象，必须含 Constant True/False 才算「布尔短路」
                    if any(isinstance(c, ast.Constant) and isinstance(c.value, bool)
                           for c in sub.comparators):
                        fields.add(left.args[0].value)
        return fields

    probe_keys = returned_keys("probe")
    short_fields = short_circuit_fields("retriever")

    print(f"  probe() 返回的键            = {sorted(probe_keys)}")
    print(f"  retriever() 布尔短路用的字段 = {sorted(short_fields) or '（无）'}")

    mismatch = sorted(f for f in short_fields if f not in probe_keys)
    ok = bool(short_fields) and not mismatch
    if not short_fields:
        record("R1", False, "retriever 里找不到「布尔短路」写法（state.get(X) is True/False）")
    elif mismatch:
        record("R1", False,
               f"短路字段 {mismatch} 不在 probe 产出的键里 ⇒ probe 永远传不出这个值，短路不可达")
    else:
        record("R1", True, f"短路字段 {sorted(short_fields)} 确实由 probe 产出")


def run_semantics() -> None:
    head("R 组 · `is False` 的取值实测（短路条件可达性）")

    cases = [
        ("state.get('retries') 键缺失 → None", None),
        ("critic 写回后 → int 0", 0),
        ("critic 写回后 → int 1", 1),
    ]
    print("  被测写法：if state.get(<字段>) is False:")
    for label, val in cases:
        print(f"    {label:34s} → (val is False) = {val is False}")

    v_missing, v_zero = None, 0            # 用变量避免 `0 is False` 的字面量语法警告
    record("R2", (v_missing is False) is False and (v_zero is False) is False,
           "int 0 与 None 都 `is not False`（is 比身份，False 是 bool 单例；0 == False 但 0 is not False）")


# ---------------------------------------------------------------- B 组：行为
def run_behavior(facts: dict) -> None:
    head("B 组 · retriever 会不会因为「无资料」而停止检索（进程内直调）")

    from app.services.agent.nodes import retriever

    # 对照：有资料 → 期望真检索、有 hits（先证明这条路没被误伤）
    st_a = build_state(facts, has_material=True, with_retries=False)
    out_a = retriever(st_a)
    hits_a = [len(e.get("hits") or []) for e in (out_a.get("evidence") or [])]
    print(f"  对照 A（has_material=True）              evidence 条数={len(out_a.get('evidence') or [])} 各章 hits={hits_a}")
    record("B1", len(out_a.get("evidence") or []) == len(st_a["plan"]) and sum(hits_a) > 0,
           f"有资料时正常检索（{len(st_a['plan'])} 章齐平、hits 合计={sum(hits_a)}）")

    # 实验 B：has_material=False，且 retries 键缺失（= 首次链路的真实形态）
    st_b = build_state(facts, has_material=False, with_retries=False)
    out_b = retriever(st_b)
    ev_b = out_b.get("evidence") or []
    hits_b = [len(e.get("hits") or []) for e in ev_b]
    print(f"  实验 B（has_material=False, 无 retries）  evidence 条数={len(ev_b)} 各章 hits={hits_b}")
    record("B2", len(ev_b) == len(st_b["plan"]) and sum(hits_b) == 0,
           f"无资料时必须空证据却拿到 hits 合计={sum(hits_b)}" if sum(hits_b) else
           f"无资料时给空证据、长度与 plan 齐平（{len(ev_b)} 条）")

    # 实验 C：has_material=False + retries=0（critic 回退后的形态）
    st_c = build_state(facts, has_material=False, with_retries=True)
    out_c = retriever(st_c)
    hits_c = [len(e.get("hits") or []) for e in (out_c.get("evidence") or [])]
    print(f"  实验 C（has_material=False, retries=0）   各章 hits={hits_c}")
    record("B3", sum(hits_c) == 0,
           f"retries=0 时仍检索（hits 合计={sum(hits_c)}）" if sum(hits_c) else "retries=0 时也给空证据")


# ---------------------------------------------------------------- P 组：事件
QUEUE = BACKEND / "app/services/task_queue.py"


def run_event(facts: dict) -> None:
    head("P 组 · probe 事件（跨文件键名一致性 + 文案与实际是否相符）")

    from app.services.agent.nodes import probe

    # --- 静态：task_queue 里 probe 分支读的键，必须由 probe 产出（踩坑 #90 的通用检验）
    qtree = ast.parse(QUEUE.read_text(encoding="utf-8"))
    read_keys: set[str] = set()
    for node in ast.walk(qtree):
        if not isinstance(node, ast.If):
            continue
        # 这个 if 的条件里是否出现 node_name == "probe"
        is_probe_branch = any(
            isinstance(c, ast.Compare) and isinstance(c.left, ast.Name)
            and c.left.id == "node_name"
            and any(isinstance(x, ast.Constant) and x.value == "probe" for x in c.comparators)
            for c in ast.walk(node.test)
        )
        if not is_probe_branch:
            continue
        # 只扫本分支的语句体（node.body）—— ast.walk(node) 会把 orelse 里的
        # elif 链（planner/retriever/critic…）一起扫进来，导致误收别人的键
        for call in [c for stmt in node.body for c in ast.walk(stmt)]:
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                    and call.func.attr == "get" and isinstance(call.func.value, ast.Name) \
                    and call.func.value.id == "update" and call.args \
                    and isinstance(call.args[0], ast.Constant):
                read_keys.add(call.args[0].value)

    # 动态：probe 实际返回的键（用真实 kb 跑一次，走「有资料」分支）
    st = build_state(facts, has_material=None, with_retries=False)
    out = probe(st)
    print(f"  probe() 实际返回的键              = {sorted(out.keys())}")
    print(f"  task_queue 的 probe 分支读的键     = {sorted(read_keys)}")

    missing = sorted(read_keys - set(out.keys()))
    record("P1", bool(read_keys) and not missing,
           f"task_queue 读了 probe 不产出的键 {missing} ⇒ 默认值静默兜底" if missing
           else f"两端键名一致（{sorted(read_keys)}）")

    # --- 动态：probe 声称的条数 vs 实际命中条数
    import re as _re
    claimed = _re.search(r"可用资料（(\d+) 条相关片段", out.get("kb_overview", ""))
    claimed_n = int(claimed.group(1)) if claimed else None
    actual_n = out.get("material_count")
    print(f"  kb_overview 里写的条数 = {claimed_n}   material_count = {actual_n}   has_material = {out.get('has_material')}")
    record("P2", claimed_n is not None and claimed_n == actual_n and (actual_n or 0) > 0,
           f"条数不一致：文案说 {claimed_n} 条、字段说 {actual_n} 条" if claimed_n != actual_n
           else f"文案与字段一致（{actual_n} 条），且 >0")

    # --- 三态文案模拟（照 task_queue 的逻辑）
    print("  三态文案模拟：")
    for flag, tag in ((True, "有资料"), (False, "明确无资料"), (None, "降级(未关联kb/检索失败)")):
        n = out.get("material_count", 0) if flag is True else 0
        if flag is True:
            detail = f"知识库可用资料（{n} 条相关片段）"
        elif flag is False:
            detail = "知识库未检索到相关内容，仅凭模型知识拆章与撰写"
        else:
            detail = "资料预检未完成（未关联知识库或检索失败）"
        print(f"    has_material={str(flag):5s}（{tag}）→ {detail}")


def main() -> int:
    print("T87 验收：retriever 复用 probe 的「无资料」结论 + probe 事件文案")
    facts = asyncio.run(load_facts())
    if not facts["kb_id"]:
        print("找不到有 ready 文档的知识库，无法验收")
        return 2
    print(f"被测库：{facts['kb_name']}（{facts['kb_id']}），ready 文档 {len(facts['docs'])} 篇")

    run_static(facts)
    run_semantics()
    run_behavior(facts)
    run_event(facts)

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
