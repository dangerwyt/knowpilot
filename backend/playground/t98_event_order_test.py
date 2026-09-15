"""T98 验收：事件流的第一条提示不能冒用节点名（踩坑 #98）

背景
----
`task_queue._run()` 在 `graph.astream(...)` **之前**推一条"开工提示"，本意是
「开始干活了」。但它的 `step` 写成了 `"planner"`，于是前端时间线渲染成：

    任务已发起…
    [planner] 拆解调研目标          ← 15:54:37，早
    [probe]   知识库未检索到相关内容    ← 15:54:39，晚

而真实链路是 `probe → planner`。用户会以为"先拆章、再预检"，顺序读起来是反的；
而 probe 这条恰是"这单有没有资料支撑"的结论，最不该被挤到后面。

判据（都可证伪）
--------------
  O1 第一条 agent_step 的 step **不是**任何真实节点名（真实节点名 = 后面出现过 done 的 step）
     —— 改前第一条是 "planner"，在真实节点名集合里 ⇒ FAIL
  O2 probe 的 done 早于 planner 的 done（真实执行顺序没被弄乱）
  O3 不存在 `step="planner" and status="running"` 的组合（旧的冒名提示已消失）⇒ 改前 FAIL
  S1 **判据自检**：把一段「改前的旧事件流」喂给同一套判据，O1/O3 必须报 FAIL。
     否则说明判据抓不到旧行为 —— 那 O1/O3 的 PASS 一文不值（同类：#96 恒真判据）

跑法
----
  cd backend
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe playground/t98_event_order_test.py
  ... --fast     # 用假图（跳过 LLM，几秒出结果），只验 _run 的事件转发顺序
  ... --keep     # 保留造出来的任务/报告（默认跑完即清）

注意：本脚本**进程内直调 `_run()`**，绕开 Celery worker —— 改了 `task_queue.py`
不必等重启就能验逻辑。但它验的是逻辑，不是部署（部署层另看进程启动时间）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]   # .../knowpilot/backend
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from sqlalchemy import select, text  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.redis import redis_client  # noqa: E402
import app.core.redis as redis_mod  # noqa: E402
import app.services.agent.graph as graph_mod  # noqa: E402
from app.models import KnowledgeBase, Project, Task  # noqa: E402
from app.services import task_queue as tq  # noqa: E402

# 项目的 DB 配了 echo，跑一次会刷几百行 SQL —— 本脚本只关心事件顺序，整体禁掉 INFO 级
logging.disable(logging.INFO)

RESULTS: list[tuple[str, str, str]] = []

# 真实节点名：这些 step 值代表"图里某个节点真的跑了"，开工提示不能借用
REAL_NODES = {"probe", "planner", "retriever", "synthesizer", "critic"}

OBJECTIVE = "2026 年国内新能源汽车销量排名"   # 无资料目标，跑得快
RUN_TIMEOUT = 420


def head(t: str) -> None:
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


# ----------------------------------------------------------------- 事件解析

def parse_events(raw_events: list[tuple[str, str, dict]]) -> list[dict]:
    """`xrange_task_events()` 返回的就是 [(entry_id, event, data)]，已按 id 递增（即发生顺序）。"""
    out: list[dict] = []
    for mid, event, data in raw_events:
        if event != "agent_step":
            continue
        d = data or {}
        out.append({
            "id": mid,
            "step": d.get("step"),
            "status": d.get("status"),
            "detail": d.get("detail") or "",
        })
    return out


def check_stream(evs: list[dict]) -> dict[str, tuple[bool, str]]:
    """对一串 agent_step 事件做 O1/O2/O3 三条判断。返回 {cid: (ok, detail)}。

    抽成函数是为了让「伪造旧流」也能走同一套判据（S1 自检）。
    """
    if not evs:
        return {c: (False, "没有 agent_step 事件（可能没推出去，或 stream 已过期）")
                for c in ("O1", "O2", "O3")}

    res: dict[str, tuple[bool, str]] = {}
    done_steps = {e["step"] for e in evs if e["status"] == "done"}
    first = evs[0]

    # --- O1 第一条不能冒用真实节点名
    ok1 = first["step"] not in done_steps
    res["O1"] = (ok1,
                 f"第一条事件 step={first['step']!r}（detail「{first['detail'][:24]}」），"
                 + ("不在真实节点名集合里，是中性开工提示"
                    if ok1 else
                    f"但它是真实节点名（真实跑过的节点 {sorted(done_steps)}）"
                    " —— 开工提示冒用了节点名，时间线顺序会被读反"))

    # --- O2 probe 的 done 要早于 planner 的 done
    i_probe = next((i for i, e in enumerate(evs) if e["step"] == "probe" and e["status"] == "done"), None)
    i_plan = next((i for i, e in enumerate(evs) if e["step"] == "planner" and e["status"] == "done"), None)
    if i_probe is None or i_plan is None:
        res["O2"] = (False, f"缺少 probe done 或 planner done 事件（probe={i_probe} planner={i_plan}）")
    else:
        ok2 = i_probe < i_plan
        res["O2"] = (ok2,
                     f"probe done 在第 {i_probe + 1} 条、planner done 在第 {i_plan + 1} 条，"
                     + ("顺序正确（probe 先）" if ok2
                        else "顺序反了 —— 图是 probe→planner，事件流却把 planner 排前面"))

    # --- O3 不该存在 planner+running 这种冒名组合
    fakes = [e for e in evs if e["step"] == "planner" and e["status"] == "running"]
    res["O3"] = (not fakes,
                 "没有 `planner + running` 组合（旧的冒名开工提示已消失）" if not fakes
                 else f"仍有 {len(fakes)} 条 `planner + running`："
                      f"「{fakes[0]['detail'][:30]}」—— 这条不是 planner 的真实状态")

    return res


# 改前的事件流长这样（从 Redis XRANGE 实测抄下来的顺序），给 S1 自检用
FAKE_OLD_STREAM: list[tuple[str, str, dict]] = [
    ("1789458877777-0", "agent_step",
     {"step": "planner", "status": "running", "detail": "拆解调研目标"}),
    ("1789458879770-0", "agent_step",
     {"step": "probe", "status": "done", "detail": "知识库未检索到相关内容，仅凭模型知识拆章与撰写",
      "has_material": False}),
    ("1789458882793-0", "agent_step",
     {"step": "planner", "status": "done", "detail": "拆解完成 5 个章节"}),
]


# ----------------------------------------------------------------- 造数据 / 清理

async def pick_ctx():
    async with SessionLocal() as s:
        proj = (await s.execute(select(Project).limit(1))).scalars().first()
        kb = (await s.execute(select(KnowledgeBase).limit(1))).scalars().first()
        if proj is None or kb is None:
            raise SystemExit("前置不足：库里没有 project 或 knowledge_base")
        return str(proj.id), str(kb.id)


async def make_task(project_id: str, kb_id: str) -> str:
    async with SessionLocal() as s:
        t = Task(project_id=project_id, objective=OBJECTIVE, kb_ids=[kb_id], status="pending")
        s.add(t)
        await s.commit()
        return str(t.id)


async def read_stream(task_id: str):
    return await redis_mod.xrange_task_events(task_id)


async def cleanup(task_id: str) -> None:
    async with SessionLocal() as s:
        await s.execute(text(
            "delete from citations where report_id in "
            "(select id from reports where task_id = :t)"), {"t": task_id})
        await s.execute(text("delete from reports where task_id = :t"), {"t": task_id})
        await s.execute(text("delete from tasks where id = :t"), {"t": task_id})
        await s.commit()
    await redis_client.delete(f"task:stream:{task_id}")


class FakeGraph:
    """跳过 LLM 的假图，只用来快速验 _run 的事件转发顺序（--fast）。"""

    async def astream(self, state, stream_mode=None):
        for u in [{"probe": {"has_material": False, "material_count": 0}},
                  {"planner": {"plan": ["第一章"]}}]:
            yield u


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="用假图（跳过 LLM，秒级）")
    ap.add_argument("--keep", action="store_true", help="保留造出来的任务（默认清理）")
    args = ap.parse_args()

    # ---------------- S1 判据自检（先做：判据不可信的话后面全白搭）
    head("S 组 · 判据自检（用改前的旧事件流喂给同一套判据）")
    old_res = check_stream(parse_events(FAKE_OLD_STREAM))
    print("  伪造旧流下各判据结果：")
    for cid in ("O1", "O2", "O3"):
        ok, detail = old_res[cid]
        print(f"    {cid} -> {'PASS' if ok else 'FAIL'}  {detail}")
    caught = (not old_res["O1"][0]) and (not old_res["O3"][0])
    record("S1", caught,
           "判据能抓到旧行为（旧流下 O1/O3 都报 FAIL）—— 所以下面的 PASS 有意义"
           if caught else
           "判据**抓不到**旧行为：旧流下 O1/O3 竟然 PASS ⇒ 这两条判据是废的，下面的 PASS 不作数")

    # ---------------- 真跑一个任务，看真实事件流
    head("O 组 · 真事件流（进程内直调 _run，绕开 worker）")
    project_id, kb_id = await pick_ctx()
    task_id = await make_task(project_id, kb_id)
    print(f"  造的任务 {task_id}（objective「{OBJECTIVE}」，kb {kb_id[:8]}）")

    if args.fast:
        graph_mod.build_graph = lambda: FakeGraph()
        print("  --fast：已把 build_graph 换成假图（不调 LLM）")

    try:
        await asyncio.wait_for(tq._run(task_id), timeout=RUN_TIMEOUT)
        print("  _run 跑完")
    except asyncio.TimeoutError:
        record("O1", False, f"_run 超过 {RUN_TIMEOUT}s 没跑完，事件流不完整")
        record("O2", False, "见 O1")
        record("O3", False, "见 O1")
        return await _summary(task_id, keep=args.keep)
    except Exception as e:
        # --fast 的假图造不出完整 draft，收尾段会炸 —— 但事件早推完了，顺序照样能判
        print(f"  _run 抛了 {type(e).__name__}: {e}")
        print("  （收尾段失败不影响已推出的事件顺序，继续判）")

    raw = await read_stream(task_id)
    evs = parse_events(raw)
    print(f"\n  事件流 {len(evs)} 条 agent_step（按 XRANGE 顺序，id 前 13 位 = 毫秒时间戳）：")
    for i, e in enumerate(evs[:8]):
        print(f"    {i + 1}. [{e['step']}/{e['status']}] {e['detail'][:46]}")
    if len(evs) > 8:
        print(f"    …（其余 {len(evs) - 8} 条略）")

    res = check_stream(evs)
    print()
    for cid in ("O1", "O2", "O3"):
        ok, detail = res[cid]
        record(cid, ok, detail)

    return await _summary(task_id, keep=args.keep)


async def _summary(task_id: str, keep: bool) -> int:
    head("汇总")
    for cid, tag, detail in RESULTS:
        print(f"  {tag} {cid}  {detail}")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}")

    if keep:
        print(f"  --keep：任务 {task_id} 与它的报告保留在库里，需手工清理")
    else:
        await cleanup(task_id)
        print(f"  已清理：任务 {task_id} + 其报告/citations + Redis stream")
    return 1 if n_fail else 0


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
