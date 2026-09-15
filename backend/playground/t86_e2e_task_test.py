"""T86 端到端验收：probe 相关性阈值在真实部署链路上是否生效

为什么要端到端
    进程内直调（t86_probe_threshold_test.py）验的是**逻辑**，绕过了 uvicorn / celery。
    部署层是否真的跑新代码、改完的探针在「HTTP 发任务 → Celery 执行 → 落库报告」
    这条完整链路上表现如何，只能发真任务看。

两个真任务（同一项目串行，单 worker + 项目级唯一活跃任务约束）

    A 有资料：objective「知研 KnowPilot 是什么？」   kb = 产品文档库（里面就有这份说明书）
    B 无关：  objective「2026 年国内新能源汽车销量排名」 kb = 产品文档库（与主题无关）

判据（v2 —— v1 有三条判据取错了观测点，见下）
    E1 [前置] 服务健康 + 该项目无进行中任务
    E2 [A] 跑到终态且 completed
    E3 [A] 报告章节数 3-5
    E4 [A] 报告出现「只有资料里才有」的专有名词 —— 正面证据：报告确实基于资料
    E5 [B] 跑到终态且 completed
    E6 [B] 报告章节数 3-5
    E7 [B] 报告**明说**资料与主题无关 / 未覆盖 —— 诚实行为的正面判据
    E8 [B] 质检 issues 里**不应有**「跑题 / 与调研目标无直接关联」的指控
           —— 反面判据：不能把无关资料当目标主题的内容来写
    E9 [观察] probe 的判定在部署层能否观测到

v1 的三条判据取错了观测点（已修）
    · E3/E6 原本读 `task.plan` —— 但该列**从未被写入**（代码只写 report_id/status，
      历史任务同样是 NULL，前端也不读它）。改用报告的 content.sections。
    · E7 原本只判「报告里有没有出现库内词」—— 太粗：模型**声明**「现有资料仅涉及
      知研 KnowPilot，与新能源车无关」时会命中，但那是诚实行为，不是拿它当依据。
      改判「是否明说资料不覆盖」，并把「有没有把它当依据」交给 E8 用质检结论来判。

运行
    cd backend && ./.venv/Scripts/python.exe playground/t86_e2e_task_test.py
    # 复用已有任务（不发新任务，只跑判据）：
    ./.venv/Scripts/python.exe playground/t86_e2e_task_test.py --reuse <taskA_id> <taskB_id>
    （要 uvicorn:8000 + celery worker 都在跑）
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "http://localhost:8000/api/v1"
EMAIL, PWD = "kptest@example.com", "test123456"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
WAIT_LIMIT_S = 600
POLL_S = 5

LIB_SPECIFIC = ["KnowPilot", "知研", "Agentic RAG", "LangGraph", "Milvus", "Element Plus"]
HONEST = ["未覆盖", "未涉及", "未提及", "资料中未", "无直接关联", "不包含", "资料未提供"]
OFFTOPIC = ["跑题", "无直接关联", "与调研目标无", "偏离主题"]

# redis 客户端会缓存连接/loop；每次 asyncio.run 新建 loop 会让第二次调用炸在
# "Event loop is closed"（本项目老坑），所以复用一个常驻 loop
_LOOP = asyncio.new_event_loop()

RESULTS: list[tuple[str, str, str]] = []


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def wait_terminal(client: httpx.Client, h: dict, task_id: str) -> dict:
    """等到终态；超时或拿到预料外的状态一律抛错（绝不把中途状态当结果返回）。"""
    t0 = time.time()
    seen: list[str] = []
    while time.time() - t0 < WAIT_LIMIT_S:
        r = client.get(f"/tasks/{task_id}", headers=h)
        r.raise_for_status()
        t = r.json()
        st = t["status"]
        if not seen or seen[-1] != st:
            seen.append(st)
            print(f"    [{time.time()-t0:6.1f}s] status={st}")
        if st in TERMINAL:
            t["_elapsed"] = round(time.time() - t0, 1)
            t["_status_path"] = " → ".join(seen)
            return t
        time.sleep(POLL_S)
    raise TimeoutError(f"任务 {task_id} 等 {WAIT_LIMIT_S}s 仍未到终态（最后 {seen[-1] if seen else '?'}）")


def load_events(task_id: str) -> list[tuple]:
    from app.core.redis import xrange_task_events

    try:
        return _LOOP.run_until_complete(xrange_task_events(task_id, after_id="-"))
    except Exception as e:
        print(f"    （读事件失败，跳过时间线：{type(e).__name__}: {e}）")
        return []


def collect(client: httpx.Client, h: dict, task: dict) -> dict:
    """把任务 → 报告 → 引用 串起来，产出判据需要的全部素材。"""
    content: dict = {}
    n_cites = -1
    if task.get("report_id"):
        r = client.get(f"/reports/{task['report_id']}", headers=h)
        if r.status_code == 200:
            content = r.json().get("content") or {}
        rc = client.get(f"/reports/{task['report_id']}/citations", headers=h)
        n_cites = len(rc.json()) if rc.status_code == 200 else -1

    secs = content.get("sections") or []
    parts: list[str] = []
    for s in secs:
        if isinstance(s, dict):
            parts.append(str(s.get("title") or ""))
            parts.append(str(s.get("content") or ""))
    text = "\n".join(parts)
    quality = content.get("quality") or {}
    return {
        "task": task, "task_id": task["id"], "content": content, "sections": secs,
        "text": text, "n_cites": n_cites, "quality": quality,
        "issues": quality.get("issues") or [],
    }


def show(r: dict, label: str) -> None:
    t = r["task"]
    print(f"    终态 {t['status']}，耗时 {t.get('_elapsed')}s，状态路径 {t.get('_status_path')}")
    if t.get("error"):
        print(f"    error = {str(t['error'])[:180]}")
    print(f"    报告 {t.get('report_id')}：{len(r['sections'])} 章 / {len(r['text'])} 字 / "
          f"citations {r['n_cites']} / 质检 {r['quality'].get('score')} 分")
    for i, s in enumerate(r["sections"], 1):
        print(f"      {i}. {s.get('title')}")
    print(f"    库内专有词 = {[w for w in LIB_SPECIFIC if w in r['text']]}")
    if r["issues"]:
        print("    质检 issues:")
        for it in r["issues"]:
            print(f"      - {str(it)[:150]}")


def run_case(client: httpx.Client, h: dict, project_id: str, kb_id: str,
             objective: str, label: str) -> dict:
    print(f"\n▶ 发任务【{label}】objective = {objective}")
    r = client.post("/tasks", headers=h, json={
        "project_id": project_id, "kb_ids": [kb_id], "objective": objective, "title": label,
    })
    if r.status_code != 201:
        raise RuntimeError(f"创建任务失败 HTTP {r.status_code}: {r.text[:200]}")
    print(f"    task_id = {r.json()['id']}")

    t = wait_terminal(client, h, r.json()["id"])
    evs = load_events(t["id"])
    for eid, etype, data in evs:
        d = data if isinstance(data, dict) else {}
        print(f"    · {etype:14s} {str(d.get('detail') or d.get('step') or '')[:62]}")

    out = collect(client, h, t)
    out["events"] = [d for _i, _t, d in evs if isinstance(d, dict)]
    out["_events_loaded"] = True
    show(out, label)
    return out


def relevance_penalty(r: dict) -> tuple[int | None, str]:
    """从质检 issues 里读「相关度」这一条扣了几分。

    质检那段文字有两种写法，都要兼容：
      改前：「相关度（扣6分）：……属于跑题内容」      → 扣 6
      改后：「相关度（20/20）：各章节均紧扣……未出现跑题」→ 扣 0

    ⚠️ 不要用「跑题 这个词是否出现」当判据：改后的原话是「**未出现跑题**」，
       裸子串匹配会把否定句算成命中（本次实测踩到）。
    """
    import re as _re

    for it in r.get("issues") or []:
        s = str(it)
        if "相关度" not in s:
            continue
        m = _re.search(r"相关度[（(]\s*扣\s*(\d+)\s*分", s)
        if m:
            return int(m.group(1)), s
        m = _re.search(r"相关度[（(]\s*(\d+)\s*/\s*(\d+)", s)
        if m:
            return int(m.group(2)) - int(m.group(1)), s
        return None, s
    return None, ""


def by_id(client: httpx.Client, h: dict, task_ids: list[str]) -> list[dict]:
    outs = []
    for tid in task_ids:
        r = client.get(f"/tasks/{tid}", headers=h)
        if r.status_code != 200:
            raise RuntimeError(f"任务 {tid} 查不到：HTTP {r.status_code}")
        t = r.json()
        ev = load_events(tid)
        if ev:
            print(f"  [{tid[:8]}] 事件时间线 {len(ev)} 条")
        out = collect(client, h, t)
        out["events"] = [d for _i, _t, d in ev if isinstance(d, dict)]
        show(out, t.get("title") or tid)
        outs.append(out)
    return outs


def judge(a: dict, b: dict) -> int:
    head("判据")

    # A —— 有资料
    record("E2", a["task"]["status"] == "completed",
           f"A 终态={a['task']['status']} 耗时={a['task'].get('_elapsed')}s")
    n_a = len(a["sections"])
    record("E3", 3 <= n_a <= 5, f"A 报告章节数={n_a}（3-5）")
    hit_a = [w for w in LIB_SPECIFIC if w in a["text"]]
    record("E4", len(hit_a) >= 3,
           f"A 报告命中资料专有名词 {len(hit_a)}/{len(LIB_SPECIFIC)} 个 = {hit_a}")

    # B —— 无关
    record("E5", b["task"]["status"] == "completed",
           f"B 终态={b['task']['status']} 耗时={b['task'].get('_elapsed')}s")
    n_b = len(b["sections"])
    record("E6", 3 <= n_b <= 5, f"B 报告章节数={n_b}（3-5）")

    honest_b = [w for w in HONEST if w in b["text"]]
    record("E7", bool(honest_b),
           f"B 报告明说资料不覆盖/与主题无关（命中 {honest_b}）—— 期望：有（诚实行为）")

    # E8 「无关资料有没有被当成目标主题的依据」—— 判据只用**结构化产物**：
    #     ① 报告引用数  ② 正文里出现几个库内专有词
    #     不用质检文本里的「相关度」分项：那是模型自由生成的，格式会变
    #     （实测三次分别是「扣6分」「20/20」「根本没有这一项」），当判据必脆。
    cross = [w for w in LIB_SPECIFIC if w in b["text"]]
    pen, sent = relevance_penalty(b)
    print(f"\n  [E8] 结构化证据：B citations={b['n_cites']}、正文库内专有词={cross}（改前是 5 条引用 + ['KnowPilot','知研']）")
    print(f"       质检「相关度」条目={sent[:80]!r}（扣分={pen}）—— 仅作参考，不当判据")
    record("E8", b["n_cites"] == 0 and not cross,
           f"B 报告仍有「把无关资料当依据」的证据：{b['n_cites']} 条引用 + {len(cross)} 个库内词 {cross}（期望都是 0）"
           if (b["n_cites"] or cross) else
           "B 报告 0 引用 + 0 库内专有词 ⇒ 无关资料没被当成目标主题的依据")

    # 参考项：质检对新版 B 报告的攻击点（无资料时的「内容空」代价）
    rounds = lambda r: len([d for d in (r.get("events") or [])
                            if "章节撰写完成" in str(d.get("detail") or "")])
    print(f"  [E8b 参考] B 质检总分={b['quality'].get('score')}，issues {len(b['issues'])} 条")
    print(f"  [E13 参考] 撰写轮数 A={rounds(a)} B={rounds(b)}（>1 说明质检没过、触发了回退重写）")

    # E9 probe 事件（T87 新增）：部署层终于能直接观测「资料预检」这一步
    def step_map(r: dict) -> dict[str, list[str]]:
        m: dict[str, list[str]] = {}
        for d in r.get("events") or []:
            if d.get("step"):
                m.setdefault(d["step"], []).append(str(d.get("detail") or ""))
        return m

    ma, mb = step_map(a), step_map(b)
    pa = (ma.get("probe") or [""])[0]
    pb = (mb.get("probe") or [""])[0]
    print(f"\n  [E9] probe 事件：A={pa!r}  B={pb!r}")
    record("E9", bool(ma.get("probe")) and bool(mb.get("probe")),
           f"事件流里有 probe 步（A={bool(ma.get('probe'))} B={bool(mb.get('probe'))}）")
    import re as _re
    na = _re.search(r"（(\d+) 条相关片段）", pa)
    record("E10", bool(na) and int(na.group(1)) > 0 and "未检索到相关内容" in pb,
           f"A 预检说有资料（{na.group(1) if na else '?'} 条）、B 预检说未检索到 —— 对岔正确"
           if (na and int(na.group(1)) > 0 and "未检索到相关内容" in pb)
           else f"A={pa!r} B={pb!r} ⇒ 两次预检没对岔")

    # E11 无资料 ⇒ retriever 不该给出证据 ⇒ 报告不该有引用（本改动的直接产物）
    record("E11", b["n_cites"] == 0,
           f"B 报告 citations={b['n_cites']}（应 0：probe 判无资料 → retriever 给空证据 → 无引用）")
    record("E12", a["n_cites"] > 0,
           f"A 报告 citations={a['n_cites']}（应 >0：有资料路径未被改动误伤）")

    print("\n  B 章节：", [s.get("title") for s in b["sections"]][:5])

    return _summary()


def main() -> int:
    reuse = []
    if "--reuse" in sys.argv:
        i = sys.argv.index("--reuse")
        reuse = sys.argv[i + 1:]
    head("T86 端到端验收：probe 阈值在真实链路（uvicorn + celery）是否生效")

    with httpx.Client(base_url=API, timeout=60) as c:
        try:
            r = c.get("/health")
            healthy = r.status_code == 200 and r.json().get("status") == "ok"
        except Exception as e:
            record("E1", False, f"连不上后端 {API}：{type(e).__name__}（uvicorn 没起？）")
            return _summary()

        r = c.post("/auth/login", json={"email": EMAIL, "password": PWD})
        if r.status_code != 200:
            record("E1", False, f"登录失败 HTTP {r.status_code}")
            return _summary()
        h = {"Authorization": f"Bearer {r.json()['token']}"}

        if reuse:
            record("E1", healthy, f"复用模式：不发新任务，直接验已有 {len(reuse)} 个任务")
            outs = by_id(c, h, reuse)
            if len(outs) < 2:
                print("复用模式需要 2 个 task_id（A 有资料、B 无关）")
                return _summary()
            return judge(outs[0], outs[1])

        projects = c.get("/projects", headers=h).json()
        kbs = {k["name"]: k["id"] for k in c.get("/knowledge-bases", headers=h).json()}
        tasks = c.get("/tasks", headers=h).json()
        active = [t for t in tasks if t["status"] in ("pending", "running")]
        prod_kb = kbs.get("产品文档库")

        if not projects or not prod_kb:
            record("E1", False, f"前置不足：项目={len(projects)} 产品文档库={bool(prod_kb)}")
            return _summary()
        record("E1", healthy and not active,
               f"后端健康={healthy}；已有任务 {len(tasks)} 个，进行中 {len(active)} 个（必须为 0）")
        if active:
            return _summary()

        head("任务 A · 有资料（objective 与知识库相关）")
        a = run_case(c, h, projects[0]["id"], prod_kb, "知研 KnowPilot 是什么？", "T86-A-有资料")
        head("任务 B · 无关（objective 与知识库不相关）")
        b = run_case(c, h, projects[0]["id"], prod_kb, "2026 年国内新能源汽车销量排名", "T86-B-无关")

    return judge(a, b)


def _summary() -> int:
    head("汇总")
    for cid, tag, detail in RESULTS:
        print(f"  {tag:4} {cid}  {detail[:108]}")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    n_skip = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
