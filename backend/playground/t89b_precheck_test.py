"""
T89b 验收：发起前预检（把 probe 的检索逻辑抽成公共函数 + 新接口 + 前端确认）

为什么做
--------
probe 判「没资料」这个结论，任务跑完才知道（T89a 已经能落库+提示了）。
但用户最想要的时点是**点「开始调研」之前** —— 早知道没资料，就能先去补资料，
而不是等 1 分钟拿到一份「全篇说没资料」的报告。

改动分三块
----------
1. 后端：把 probe() 里的检索逻辑抽成 `probe_material()`，新增 `POST /tasks/precheck`
2. 后端：probe 事件的 data 里带结构化字段 has_material（前端按字段判，别匹配中文）
3. 前端：提交前预检，判无资料时弹确认框

判据
----
A 组 静态（文件里改到位没有）
  A1 nodes.py 有 probe_material 函数
  A2 probe() 调用了 probe_material（而不是自己再写一份检索）
  A3 tasks.py 有 POST /precheck 路由
  A4 schemas 有 PrecheckIn / PrecheckOut 且字段齐全
  A5 task_queue.py 的 probe 事件 data 里带 has_material
  A6 WorkbenchView.vue 用 === false 判无资料
  A7 前端有 precheck 的 API 封装且被调用
  A8 反向：WorkbenchView 的 agent_step 分支不靠中文字串判断
  A9 反向：WorkbenchView 里没有 !has_material 这类真值判断

B 组 接口（真调 HTTP）
  B1 有资料 → has_material=true 且 material_count>0
  B2 无资料 → has_material=false 且 material_count=0
  B3 kb_ids 为空 → null（三态！不是 false）
  B4 对拍：接口结论 == T89a 真任务落库的结论（跨实现一致性）
  B5 值类型是 JSON 布尔 / null（不是字符串）
  B6 未登录访问被拒
  B7 反向：precheck 是只读的，调用前后 tasks 表行数不变

C 组 进程内（能造接口造不出的场景）
  C1 probe_material 空 kb_ids → (None, 0, 文案)
  C2 检索抛异常 → (None, 0, 文案含「检索失败」)，**不是 False**
  C3 probe() 在降级（无 kb / 检索失败）时 has_material 不能是 False
  C4 probe() 与 probe_material() 三态一致（证明节点真的用了抽出来的函数）

跑法（C 组要在 backend 目录下跑）
  backend/.venv/Scripts/python.exe backend/playground/t89b_precheck_test.py --static
  backend/.venv/Scripts/python.exe backend/playground/t89b_precheck_test.py            # A+B+C
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
sys.path.insert(0, str(BACKEND))

NODES = BACKEND / "app" / "services" / "agent" / "nodes.py"
QUEUE = BACKEND / "app" / "services" / "task_queue.py"
ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "tasks.py"
SCHEMAS = BACKEND / "app" / "schemas" / "__init__.py"
API_TS = FRONTEND / "src" / "api" / "tasks.ts"
WORKBENCH = FRONTEND / "src" / "views" / "WorkbenchView.vue"

API = "http://localhost:8000/api/v1"
import os

# 测试账号从环境变量读，别把凭据写进公开仓库
# （t88/t89a/t89b 三个前端脚本 2026-09-16 已按此整改，后端这几个没跟上）
EMAIL = os.environ.get("KP_TEST_EMAIL")
PWD = os.environ.get("KP_TEST_PASSWORD")
if not (EMAIL and PWD):
    raise SystemExit(
        "缺少 KP_TEST_EMAIL / KP_TEST_PASSWORD。用法：\n"
        "  KP_TEST_EMAIL=<账号> KP_TEST_PASSWORD=<口令> python <本脚本>"
    )
KB_NAME = "产品文档库"
OBJ_WITH = "知研 KnowPilot 是什么？"
OBJ_WITHOUT = "2026 年国内新能源汽车销量排名"

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
    r = subprocess.run(
        ["docker", "exec", "knowpilot-postgres-1", "psql", "-U", "knowpilot",
         "-d", "knowpilot", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败：{r.stderr.strip()[:300]}")
    return r.stdout.strip()


# ---------------------------------------------------------------- A 组：静态

def func_body(src: str, name: str) -> str:
    """取出某个模块级函数的源码（AST 定位，比正则可靠）。"""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def dict_keys_in_call(src: str, call_name: str) -> set[str]:
    """收集 `call_name(... data={...})` 里那个 data 字典的全部键。"""
    tree = ast.parse(src)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if fname != call_name:
            continue
        for kw in node.keywords:
            if kw.arg == "data" and isinstance(kw.value, ast.Dict):
                for k in kw.value.keys:
                    if isinstance(k, ast.Constant):
                        keys.add(str(k.value))
    return keys


def run_static() -> None:
    head("A 组 · 静态判据")

    nodes_src = NODES.read_text(encoding="utf-8")
    has_fn = "def probe_material(" in nodes_src
    print(f"  nodes.py 有 probe_material = {has_fn}")
    record("A1", has_fn, "nodes.py 定义了 probe_material"
           if has_fn else "nodes.py 里没有 def probe_material（检索逻辑没抽出来）")

    probe_src = func_body(nodes_src, "probe")
    calls_it = "probe_material(" in probe_src
    record("A2", calls_it,
           "probe() 复用了 probe_material（没有写第二份检索逻辑）" if calls_it
           else "probe() 里没调 probe_material —— 抽了函数但节点没用，两份逻辑会各自漂移")

    ep_src = ENDPOINT.read_text(encoding="utf-8")
    has_route = re.search(r'@router\.post\(\s*[\'"]/precheck[\'"]', ep_src) is not None
    record("A3", has_route, "tasks.py 有 POST /precheck"
           if has_route else "tasks.py 里没有 POST /precheck 路由")

    sc_src = SCHEMAS.read_text(encoding="utf-8")
    has_in = "class PrecheckIn" in sc_src and "kb_ids" in sc_src and "objective" in sc_src
    has_out = "class PrecheckOut" in sc_src and "has_material" in sc_src and "material_count" in sc_src
    record("A4", has_in and has_out,
           "schemas 有 PrecheckIn / PrecheckOut 且字段齐全" if has_in and has_out
           else f"schemas 缺 {'PrecheckIn ' if not has_in else ''}{'PrecheckOut' if not has_out else ''}")

    q_keys = dict_keys_in_call(QUEUE.read_text(encoding="utf-8"), "publish_task_event")
    has_flag = "has_material" in q_keys
    print(f"  task_queue.py 的事件 data 键 = {sorted(q_keys)}")
    record("A5", has_flag, "probe 事件的 data 里带了 has_material"
           if has_flag else f"probe 事件没带 has_material（现有键 {sorted(q_keys)}）")

    wb = WORKBENCH.read_text(encoding="utf-8")
    strict = re.search(r"has_material\s*===\s*false", wb) is not None
    record("A6", strict, "Workbench 用 === false 判无资料"
           if strict else "Workbench 里没找到 has_material === false")

    api_src = API_TS.read_text(encoding="utf-8")
    has_api = re.search(r"export function precheck\w*", api_src) is not None
    imported = re.search(r"precheck\w*", wb) is not None
    record("A7", has_api and imported,
           "api/tasks.ts 有 precheck 封装且 Workbench 调用了" if has_api and imported
           else f"前端缺 {'API 封装' if not has_api else '调用处'}")

    cn_guess = re.findall(r'detail[^\n]{0,40}\.includes\(\s*[\'"][^\'"]*[\u4e00-\u9fff]', wb)
    record("A8", not cn_guess,
           "agent_step 分支没有用中文字串判类型（正确）" if not cn_guess
           else f"用中文字串判断事件类型：{cn_guess[:2]} —— 文案一改就失效（踩坑 #92/#93）")

    truthy = sorted(set(re.findall(r"!\s*\w*(?:pre|res|r|result|precheck)\w*\.\s*has_material", wb)))
    record("A9", not truthy,
           "没有用真值判断（正确）" if not truthy
           else f"出现真值判断 {truthy} —— has_material 是三态，会把 null 误判成无资料")


# ---------------------------------------------------------------- B 组：接口

def precheck(client: httpx.Client, h: dict | None, kb_ids, objective):
    return client.post("/tasks/precheck", headers=h or {},
                       json={"kb_ids": kb_ids, "objective": objective})


def run_api() -> None:
    head("B 组 · 接口（真调 HTTP）")
    with httpx.Client(base_url=API, timeout=60) as c:
        try:
            if c.get("/health").status_code != 200:
                record("B1", False, "后端 /health 不 ok")
                return
        except Exception as e:
            record("B1", False, f"连不上后端 {API}：{type(e).__name__}")
            return

        r = c.post("/auth/login", json={"email": EMAIL, "password": PWD})
        if r.status_code != 200:
            record("B1", False, f"登录失败 HTTP {r.status_code}")
            return
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        kb_id = {k["name"]: k["id"] for k in c.get("/knowledge-bases", headers=h).json()}.get(KB_NAME)
        if not kb_id:
            record("B1", False, f"找不到知识库「{KB_NAME}」")
            return

        n_tasks_before = int(psql("select count(*) from tasks;"))

        r1 = precheck(c, h, [kb_id], OBJ_WITH)
        r2 = precheck(c, h, [kb_id], OBJ_WITHOUT)
        r3 = precheck(c, h, [], OBJ_WITH)
        for cid, r, label in (("B1", r1, f"有资料「{OBJ_WITH}」"),
                              ("B2", r2, f"无资料「{OBJ_WITHOUT}」"),
                              ("B3", r3, "kb_ids 为空")):
            if r.status_code != 200:
                record(cid, False, f"{label} HTTP {r.status_code}: {r.text[:120]}")
                continue
            body = r.json()
            print(f"  {cid} {label} → {json.dumps(body, ensure_ascii=False)}")

        def body_of(r):
            try:
                return r.json()
            except Exception:
                return {}

        b1, b2, b3 = body_of(r1), body_of(r2), body_of(r3)
        record("B1", b1.get("has_material") is True and (b1.get("material_count") or 0) > 0,
               f"有资料 → has_material=True, material_count={b1.get('material_count')}")
        record("B2", b2.get("has_material") is False and b2.get("material_count") == 0,
               f"无资料 → has_material=False, material_count={b2.get('material_count')}")
        record("B3", b3.get("has_material") is None,
               f"空 kb_ids → has_material=None（三态：没关联知识库不等于没资料）"
               if b3.get("has_material") is None
               else f"空 kb_ids 返回 {b3.get('has_material')!r} —— 必须是 null，不能是 false")

        types = {type(b1.get("has_material")).__name__, type(b2.get("has_material")).__name__,
                 type(b3.get("has_material")).__name__}
        record("B5", types == {"bool", "NoneType"},
               f"值类型是 JSON 布尔 / null（{sorted(types)}）" if types == {"bool", "NoneType"}
               else f"值类型异常：{sorted(types)}（写成字符串的话前端 === false 永远不成立）")

        # B4 跨实现对拍：拿 T89a 真任务落库的结论，用同样的 objective + kb_ids 问接口
        rows = psql(
            "select t.objective, t.kb_ids::text, r.content->'quality'->>'has_material' "
            "from reports r join tasks t on t.id = r.task_id "
            "where r.content->'quality' ? 'has_material' order by r.created_at desc limit 2;"
        ).splitlines()
        if len(rows) < 2:
            record("B4", None, "库里没有带 has_material 的报告（先跑 t89a_has_material_test.py）")
        else:
            mismatches = []
            for line in rows:
                obj, kbs_text, flag = line.split("|")
                kbs = json.loads(kbs_text)
                got = body_of(precheck(c, h, kbs, obj)).get("has_material")
                want = (flag == "true")
                print(f"  对拍 objective「{obj[:20]}」落库={flag} 接口={got}")
                if got != want:
                    mismatches.append(f"{obj[:16]}: 落库={flag} 接口={got}")
            record("B4", not mismatches,
                   f"接口结论与 {len(rows)} 个真任务落库的结论一致（跨实现对拍）" if not mismatches
                   else f"对拍不一致：{mismatches}")

        # B6 权限
        rr = precheck(c, None, [kb_id], OBJ_WITH)
        record("B6", rr.status_code in (401, 403),
               f"未登录访问被拒（HTTP {rr.status_code}）" if rr.status_code in (401, 403)
               else f"未登录也能调：HTTP {rr.status_code}")

        # B7 只读
        n_tasks_after = int(psql("select count(*) from tasks;"))
        record("B7", n_tasks_before == n_tasks_after,
               f"precheck 是只读的（tasks 行数 {n_tasks_before} → {n_tasks_after}）" if n_tasks_before == n_tasks_after
               else f"调用 precheck 后 tasks 行数变了：{n_tasks_before} → {n_tasks_after}（有副作用！）")


# ---------------------------------------------------------- C 组：进程内三态

def run_inproc() -> None:
    head("C 组 · 进程内（造接口造不出的场景）")
    try:
        from app.services.agent.nodes import probe, probe_material
    except ImportError as e:
        for cid in ("C1", "C2", "C3", "C4"):
            record(cid, False, f"import 失败：{e}（probe_material 还没抽出来？）")
        return

    def call(*a, **kw):
        out = probe_material(*a, **kw)
        if not (isinstance(out, tuple) and len(out) == 3):
            raise AssertionError(f"probe_material 应返回三元组 (has_material, count, overview)，实际 {out!r}")
        return out

    # C1 空 kb
    flag, count, note = call("随便一个目标", [])
    print(f"  C1 空 kb → ({flag!r}, {count}, {note[:40]!r})")
    record("C1", flag is None and count == 0,
           f"空 kb_ids → (None, 0)（三态的 None）" if flag is None and count == 0
           else f"空 kb_ids 返回 flag={flag!r} —— 应为 None，不能是 False")

    # C2 检索抛异常 —— 把 milvus_client.search 打成抛错（probe_material 里是函数内 import，
    #    每次都重新取模块属性，所以 patch 模块属性就生效）
    import app.services.rag.milvus_client as mc
    orig = mc.search

    def boom(*a, **kw):
        raise RuntimeError("T89b 探针：故意让检索失败")

    mc.search = boom
    try:
        flag2, count2, note2 = call(OBJ_WITH, ["11111111-1111-1111-1111-111111111111"])
    finally:
        mc.search = orig
    print(f"  C2 检索异常 → ({flag2!r}, {count2}, {note2[:60]!r})")
    record("C2", flag2 is None and "检索失败" in note2,
           "检索失败 → None，且文案明说是检索失败（不是「没资料」）" if flag2 is None and "检索失败" in note2
           else f"检索失败返回 ({flag2!r}, {count2}, {note2[:50]!r}) —— 不能当成「明确无资料」")

    # C3 probe() 降级时不能给 False
    out_no_kb = probe({"objective": OBJ_WITH, "kb_ids": []})
    mc.search = boom
    try:
        out_err = probe({"objective": OBJ_WITH, "kb_ids": ["11111111-1111-1111-1111-111111111111"]})
    finally:
        mc.search = orig
    bad = []
    for label, out in (("无 kb", out_no_kb), ("检索失败", out_err)):
        if out.get("has_material") is False:
            bad.append(label)
    print(f"  无 kb → { {k: v for k, v in out_no_kb.items() if k != 'kb_overview'} }")
    print(f"  检索失败 → { {k: v for k, v in out_err.items() if k != 'kb_overview'} }")
    record("C3", not bad,
           "probe() 两种降级路径都没给 has_material=False（三态保住）" if not bad
           else f"降级被当成「明确无资料」：{bad} —— T87 那个 has_material is False 守卫会被误触发")

    # C4 节点与公共函数三态一致
    import app.services.rag as rag
    kb_id = None
    try:
        with httpx.Client(base_url=API, timeout=30) as c:
            r = c.post("/auth/login", json={"email": EMAIL, "password": PWD})
            h = {"Authorization": f"Bearer {r.json()['token']}"}
            kb_id = {k["name"]: k["id"] for k in c.get("/knowledge-bases", headers=h).json()}.get(KB_NAME)
    except Exception as e:
        print(f"  （拿 kb_id 失败：{type(e).__name__}）")
    if not kb_id:
        record("C4", None, "拿不到 kb_id，跳过对拍")
    else:
        pairs = []
        for obj in (OBJ_WITH, OBJ_WITHOUT):
            f1, c1, _ = call(obj, [kb_id])
            f2 = probe({"objective": obj, "kb_ids": [kb_id]}).get("has_material")
            pairs.append((obj[:14], f1, f2))
            print(f"  C4「{obj[:14]}」probe_material={f1!r} probe()={f2!r}")
        same = all(a == b or (a is None and b is None) for _o, a, b in pairs)
        record("C4", same, "probe() 与 probe_material() 三态一致（确认节点真的用了抽出来的函数）"
               if same else f"两者结论不一致：{pairs}")


def _summary() -> int:
    head("汇总")
    for cid, tag, detail in RESULTS:
        print(f"  {tag:4} {cid}  {detail[:112]}")
    n_pass = sum(1 for _, t, _ in RESULTS if t == "PASS")
    n_fail = sum(1 for _, t, _ in RESULTS if t == "FAIL")
    n_skip = sum(1 for _, t, _ in RESULTS if t == "SKIP")
    print(f"\n  PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    return 1 if n_fail else 0


def main() -> int:
    head("T89b 验收：发起前预检")
    run_static()
    if "--static" in sys.argv:
        return _summary()
    run_api()
    run_inproc()
    return _summary()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
