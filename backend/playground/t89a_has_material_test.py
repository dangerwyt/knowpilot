"""
T89a 验收：把 probe 的「有没有资料」结论落进报告 JSON

为什么做
--------
probe 判「知识库没相关资料」这个结论，现在只活在前端时间线里一行字，没落库。
报告页隔天打开完全不知道这份报告是「仅凭模型知识」写的。
这个结论必须能存下来，前端才能提示。

判据分两层
----------
A 组（静态，不依赖服务在跑）
  A1 task_queue.py 的 quality 字典里有 has_material 键
  A2 task_queue.py 的 quality 字典里有 material_count 键
  A3 report.ts 的 IQuality 有 has_material 字段
  A4 report.ts 的 IQuality 有 material_count 字段
  A5 ReportView.vue 用严格比较 has_material === false（三态，不能用真值判断）
  A6 ReportView.vue 有可定位的警告条锚点 .no-material-alert
  A7 反向：ReportView.vue 不出现 !quality?.has_material 这类真值判断

B 组（端到端，真发任务 → 落库 → 从接口读回）
  B1 有资料那份报告 quality.has_material is True
  B2 无资料那份报告 quality.has_material is False
  B3 值是 JSON 布尔（不是字符串 "false"）
  B4 有资料那份 material_count > 0
  B5 无资料那份 material_count == 0
  B6 库里 jsonb_typeof(quality.has_material) == 'boolean'（接口和落库一致）
  B7 对照：改动前生成的老报告里没有该键，且仍能正常读出来（旧数据不炸）

跑法
----
  backend/.venv/Scripts/python.exe backend/playground/t89a_has_material_test.py --static
  backend/.venv/Scripts/python.exe backend/playground/t89a_has_material_test.py            # 发 2 个真任务
  backend/.venv/Scripts/python.exe backend/playground/t89a_has_material_test.py --reuse <taskA> <taskB>
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
QUEUE = BACKEND / "app" / "services" / "task_queue.py"
TYPING = FRONTEND / "src" / "typing" / "report.ts"
REPORT_VIEW = FRONTEND / "src" / "views" / "ReportView.vue"

API = "http://localhost:8000/api/v1"
EMAIL, PWD = "kptest@example.com", "test123456"
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
WAIT_LIMIT_S = 600
POLL_S = 5

OBJECTIVE_WITH = "知研 KnowPilot 是什么？"          # 与知识库相关 → 应该有资料
OBJECTIVE_WITHOUT = "2026 年国内新能源汽车销量排名"   # 与知识库无关 → 应该判无资料

RESULTS: list[tuple[str, str, str]] = []


def record(cid: str, ok: bool | None, detail: str) -> None:
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((cid, tag, detail))
    print(f"[{tag}] {cid}  {detail}")


def head(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def psql(sql: str) -> str:
    """用参数数组调 psql（不经 shell）——拼字符串会让 SQL 里的 -> / || 被 cmd 吃掉。"""
    r = subprocess.run(
        ["docker", "exec", "knowpilot-postgres-1", "psql", "-U", "knowpilot",
         "-d", "knowpilot", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败：{r.stderr.strip()[:300]}")
    return r.stdout.strip()


# ---------------------------------------------------------------- A 组：静态

def quality_dict_keys(path: Path) -> set[str]:
    """AST 找 `xxx["quality"] = {...}` 这个字典字面量的所有键。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "quality"):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant):
                        keys.add(str(k.value))
    return keys


def run_static() -> None:
    head("A 组 · 静态判据（文件里改到位没有）")

    qkeys = quality_dict_keys(QUEUE)
    print(f"  task_queue.py 的 quality 键 = {sorted(qkeys)}")
    record("A1", "has_material" in qkeys,
           "quality 字典里有 has_material" if "has_material" in qkeys
           else f"quality 字典里没有 has_material（现有键 {sorted(qkeys)}）")
    record("A2", "material_count" in qkeys,
           "quality 字典里有 material_count" if "material_count" in qkeys
           else "quality 字典里没有 material_count")

    ts = TYPING.read_text(encoding="utf-8")
    has_f = re.search(r"has_material\s*\??\s*:\s*boolean", ts) is not None
    has_c = re.search(r"material_count\s*\??\s*:\s*number", ts) is not None
    print(f"  report.ts: has_material 字段 = {has_f}；material_count 字段 = {has_c}")
    record("A3", has_f, "IQuality 声明了 has_material"
           if has_f else "report.ts 里没有 has_material?: boolean 字段")
    record("A4", has_c, "IQuality 声明了 material_count"
           if has_c else "report.ts 里没有 material_count?: number 字段")

    rv = REPORT_VIEW.read_text(encoding="utf-8")
    tpl = rv.split("<template>", 1)[1] if "<template>" in rv else rv
    strict = re.search(r"has_material\s*===\s*false", tpl) is not None
    anchor = "no-material-alert" in tpl
    truthy = sorted(set(re.findall(r"!\s*quality\s*\??\.\s*(?:has_material|material_count)", rv)))
    print(f"  ReportView.vue: 严格比较 = {strict}；警告条锚点 = {anchor}；真值判断 = {truthy or '（无）'}")
    record("A5", strict,
           "模板里用 has_material === false 严格比较"
           if strict else "模板里没找到 has_material === false（三态必须严格比较）")
    record("A6", anchor,
           "警告条带 .no-material-alert 锚点，可被脚本定位"
           if anchor else "模板里没有 no-material-alert 这个 class，测试脚本没法定位警告条")
    record("A7", not truthy,
           "没有用真值判断（正确）"
           if not truthy else f"出现真值判断 {truthy} —— has_material 是三态，会把 null 误判成无资料")


# ---------------------------------------------------------- B 组：端到端落库

def wait_terminal(client: httpx.Client, h: dict, task_id: str) -> dict:
    """等到终态；超时一律抛错，绝不把中途状态当结果返回。"""
    t0 = time.time()
    seen: list[str] = []
    while time.time() - t0 < WAIT_LIMIT_S:
        r = client.get(f"/tasks/{task_id}", headers=h)
        r.raise_for_status()
        t = r.json()
        st = t["status"]
        if not seen or seen[-1] != st:
            seen.append(st)
            print(f"    [{time.time() - t0:6.1f}s] status={st}")
        if st in TERMINAL:
            t["_elapsed"] = round(time.time() - t0, 1)
            return t
        time.sleep(POLL_S)
    raise TimeoutError(f"任务 {task_id} 等 {WAIT_LIMIT_S}s 仍未到终态（最后 {seen[-1] if seen else '?'}）")


def run_case(client: httpx.Client, h: dict, project_id: str, kb_id: str,
             objective: str, label: str) -> dict:
    print(f"\n▶ 发任务【{label}】objective = {objective}")
    r = client.post("/tasks", headers=h, json={
        "project_id": project_id, "kb_ids": [kb_id], "objective": objective, "title": label,
    })
    if r.status_code != 201:
        raise RuntimeError(f"创建任务失败 HTTP {r.status_code}: {r.text[:200]}")
    tid = r.json()["id"]
    print(f"    task_id = {tid}")
    t = wait_terminal(client, h, tid)
    print(f"    终态 {t['status']}，耗时 {t.get('_elapsed')}s，report_id = {t.get('report_id')}")
    rep = client.get(f"/reports/{t['report_id']}", headers=h).json()
    return {"task": t, "report_id": t["report_id"], "quality": (rep.get("content") or {}).get("quality") or {}}


def by_id(client: httpx.Client, h: dict, tid: str) -> dict:
    t = client.get(f"/tasks/{tid}", headers=h).json()
    rep = client.get(f"/reports/{t['report_id']}", headers=h).json()
    return {"task": t, "report_id": t["report_id"], "quality": (rep.get("content") or {}).get("quality") or {}}


def judge(a: dict, b: dict) -> int:
    head("B 组 · 端到端（报告 JSON 里真有这个结论吗）")
    qa, qb = a["quality"], b["quality"]
    print(f"  有资料那份：quality.has_material = {qa.get('has_material')!r}，"
          f"material_count = {qa.get('material_count')!r}")
    print(f"  无资料那份：quality.has_material = {qb.get('has_material')!r}，"
          f"material_count = {qb.get('material_count')!r}")

    # 键是否存在 —— 与「值是 None」要分开报，否则字段没加会伪装成「值不对」
    ka = "has_material" in qa
    record("B1a", ka, "有资料那份的 quality 里有 has_material 这个键"
           if ka else "有资料那份的 quality 里没有 has_material 键（字段没落库）")
    kb_ = "has_material" in qb
    record("B1b", kb_, "无资料那份的 quality 里有 has_material 这个键"
           if kb_ else "无资料那份的 quality 里没有 has_material 键（字段没落库）")

    record("B2", qa.get("has_material") is True and qb.get("has_material") is False,
           f"三态落对：有资料=True、无资料=False（实际 {qa.get('has_material')!r} / {qb.get('has_material')!r}）")

    # 布尔 vs 字符串：接口解出来 str 就说明写成字符串了，前端 === false 会失效
    types = {type(qa.get("has_material")).__name__, type(qb.get("has_material")).__name__}
    record("B3", types == {"bool"},
           f"值是 JSON 布尔（类型 {sorted(types)}）" if types == {"bool"}
           else f"值不是布尔而是 {sorted(types)} —— 写成字符串后前端 === false 永远不成立")

    ca, cb = qa.get("material_count"), qb.get("material_count")
    record("B4", isinstance(ca, int) and ca > 0, f"有资料那份 material_count = {ca}（应 > 0）")
    record("B5", cb == 0, f"无资料那份 material_count = {cb}（应 == 0）")

    # 库里的真身（接口读到的可能被序列化修饰，这里直接看 jsonb）
    raw = psql("select coalesce(jsonb_typeof(content->'quality'->'has_material'),'<缺>') "
               "from reports where id in ("
               f"'{a['report_id']}','{b['report_id']}') order by id;")
    kinds = [x for x in raw.splitlines() if x.strip()]
    record("B6", kinds == ["boolean", "boolean"],
           f"库里 jsonb_typeof 两份都是 boolean（实际 {kinds}）" if kinds == ["boolean", "boolean"]
           else f"库里类型 = {kinds}（应为两个 boolean）")

    # 对照：改动前的老报告没有这个键，且仍能读出来（证明旧数据没被弄坏）
    old_cnt = int(psql("select count(*) from reports where not (content->'quality' ? 'has_material');"))
    if old_cnt == 0:
        record("B7", None, "库里没有「改动前的老报告」了（前端对照判据 F4 会改走临时造数据）")
    else:
        old_id = psql("select id from reports where not (content->'quality' ? 'has_material') "
                      "order by created_at limit 1;").splitlines()[0]
        ok_read = client_get_ok(old_id)
        record("B7", ok_read,
               f"对照：库里 {old_cnt} 份老报告没有该键，接口仍能读出来（{old_id[:8]} → "
               f"{'HTTP 200' if ok_read else '读取失败'}）")
    return _summary()


def client_get_ok(report_id: str) -> bool:
    """老报告还能不能正常读出来（HTTP 200）。"""
    with httpx.Client(base_url=API, timeout=30) as c:
        r = c.post("/auth/login", json={"email": EMAIL, "password": PWD})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        return c.get(f"/reports/{report_id}", headers=h).status_code == 200


def _summary() -> int:
    head("汇总")
    for cid, tag, detail in RESULTS:
        print(f"  {tag:4} {cid}  {detail[:110]}")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    n_skip = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    return 1 if n_fail else 0


def main() -> int:
    head("T89a 验收：probe 的「有没有资料」落进报告 JSON")

    run_static()
    if "--static" in sys.argv:
        return _summary()

    reuse: list[str] = []
    if "--reuse" in sys.argv:
        i = sys.argv.index("--reuse")
        reuse = sys.argv[i + 1:]

    with httpx.Client(base_url=API, timeout=60) as c:
        try:
            r = c.get("/health")
            healthy = r.status_code == 200 and r.json().get("status") == "ok"
        except Exception as e:
            record("E1", False, f"连不上后端 {API}：{type(e).__name__}（uvicorn 没起？）")
            return _summary()
        if not healthy:
            record("E1", False, "后端 /health 不 ok")
            return _summary()

        r = c.post("/auth/login", json={"email": EMAIL, "password": PWD})
        if r.status_code != 200:
            record("E1", False, f"登录失败 HTTP {r.status_code}")
            return _summary()
        h = {"Authorization": f"Bearer {r.json()['token']}"}

        if reuse:
            if len(reuse) < 2:
                record("E1", False, "复用模式要 2 个 task_id（有资料 / 无资料）")
                return _summary()
            record("E1", True, f"复用模式：不发新任务，直接验已有 {len(reuse)} 个任务")
            return judge(by_id(c, h, reuse[0]), by_id(c, h, reuse[1]))

        projects = c.get("/projects", headers=h).json()
        kbs = {k["name"]: k["id"] for k in c.get("/knowledge-bases", headers=h).json()}
        tasks = c.get("/tasks", headers=h).json()
        active = [t for t in tasks if t["status"] in ("pending", "running")]
        prod_kb = kbs.get("产品文档库")
        if not projects or not prod_kb:
            record("E1", False, f"前置不足：项目={len(projects)} 产品文档库={bool(prod_kb)}")
            return _summary()
        record("E1", not active,
               f"后端健康；已有任务 {len(tasks)} 个，进行中 {len(active)} 个（必须为 0）")
        if active:
            return _summary()

        head("任务 A · 有资料")
        a = run_case(c, h, projects[0]["id"], prod_kb, OBJECTIVE_WITH, "T89a-A-有资料")
        head("任务 B · 无资料")
        b = run_case(c, h, projects[0]["id"], prod_kb, OBJECTIVE_WITHOUT, "T89a-B-无资料")

    return judge(a, b)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
