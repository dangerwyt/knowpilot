"""T99 三处修复的验收脚本。

修的是什么：
  A. frontend/package.json 漏声明 element-plus（代码在用、依赖里没写）
  B. frontend/src/main.ts 建了两个 app 实例（图标注册在没挂载的那个上）
  C. task_queue.py 写 citation 时 kb_id 写死 kb_ids[0]（多知识库会记错）

每组判据都满足：
  · 改前必须 FAIL（脚本自带"改前快照"自检来证明它真有 FAIL 的能力）
  · 判据看的是"本质证据"（代码用了没声明 / 实例个数 / 落库的 kb_id），不是"没报错"
  · C 组跑完自己清理，且双向核对（引用归零 + 白名单没少）

用法：
    python playground/t99_fixes_test.py            # 全部（C 组进程内直调，不依赖 worker）
    python playground/t99_fixes_test.py --skip-db  # 只跑静态判据（A/B1/C1）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"

RESULTS: list[tuple[str, bool, str]] = []


def check(cid: str, ok: bool, detail: str) -> None:
    RESULTS.append((cid, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {cid}  {detail}")


# ---------------------------------------------------------------- A 组：依赖声明

IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[\w*{}\s,$]+\s+from\s+)?["']([^"']+)["']"""
    r"""|import\s*\(\s*["']([^"']+)["']\s*\)""",
    re.M,
)


def pkg_name(spec: str) -> str | None:
    """从 import 路径取出包名；本地/别名/样式子路径不算包。"""
    if not spec or spec.startswith((".", "/", "@/")):
        return None
    if spec.startswith("~") or ":" in spec.split("/")[0]:  # virtual: / node: 之类
        return None
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def declared_packages(pkg_json: Path) -> set[str]:
    data = json.loads(pkg_json.read_text(encoding="utf-8"))
    return set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))


def scan_imports(src_dir: Path) -> dict[str, set[str]]:
    """返回 {包名: {用到它的文件}}。"""
    used: dict[str, set[str]] = {}
    for f in list(src_dir.rglob("*.ts")) + list(src_dir.rglob("*.vue")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in IMPORT_RE.finditer(text):
            spec = m.group(1) or m.group(2)
            name = pkg_name(spec or "")
            if name:
                used.setdefault(name, set()).add(str(f.relative_to(src_dir)))
    return used


def undeclared(used: dict[str, set[str]], declared: set[str]) -> dict[str, set[str]]:
    """代码里用到、但 package.json 里没声明的包 —— 这就是本次 bug 的形态。"""
    return {k: v for k, v in used.items() if k not in declared}


def group_a() -> None:
    print("\n=== A 组：前端依赖声明（package.json 是否覆盖代码里用到的包）===")
    pkg_json = FRONTEND / "package.json"
    declared = declared_packages(pkg_json)
    used = scan_imports(SRC)
    bad = undeclared(used, declared)

    check("A1", "element-plus" in declared, f"dependencies 含 element-plus：{sorted(declared)}")
    check(
        "A2",
        not bad,
        "代码里用到但没声明的包：{}".format(bad if bad else "无"),
    )
    lock = FRONTEND / "pnpm-lock.yaml"
    lock_text = lock.read_text(encoding="utf-8") if lock.exists() else ""
    check("A3", "element-plus@" in lock_text, "pnpm-lock.yaml 里有 element-plus 条目")

    # S1 判据自检：把 element-plus 从声明里摘掉，A2 的检查必须报出来
    # （证明它不是恒 PASS 的摆设）。改前真实状态就是 element-plus 未声明。
    self_check = undeclared(used, declared - {"element-plus"})
    check(
        "S1",
        "element-plus" in self_check,
        "自检：从声明里摘掉 element-plus 后，检查确实报出它未声明",
    )


# ---------------------------------------------------------------- 判据函数（便于喂坏样本自检）

def judge_main_ts(text: str) -> tuple[bool, bool, int, int]:
    """B1: createApp(App) 恰好 1 次；B2: createApp( 整体只出现 1 次。

    为什么用"次数"而不是"变量名是否相等"：入口是链式调用且被格式化到多行
    （app\\n  .use(...)\\n  .mount(...)），靠正则取 mount 的接收者很脆弱。
    本次 bug 的本质就是"多建了一个实例"，数次数既简单又抓得准，
    而且比只看 createApp(App) 更严——连 createApp(别的东西) 也算。
    """
    n_app = len(re.findall(r"createApp\s*\(\s*App\s*\)", text))
    n_any = len(re.findall(r"createApp\s*\(", text))
    return n_app == 1, n_any == 1, n_app, n_any


def judge_citation_block(text: str) -> bool:
    """C1: session.add(Citation(...)) 的构造里不出现 kb_ids[0]。"""
    block = re.search(r"session\.add\(Citation\(([\s\S]*?)\)\)", text)
    return "kb_ids[0]" not in (block.group(1) if block else "")


# 改前的真实写法（用于证明判据有 FAIL 的能力）
BAD_MAIN_TS = """
const app = createApp(App);
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component);
}
createApp(App).use(createPinia()).use(router).mount("#app");
"""

BAD_CITATION = """
                session.add(Citation(
                    report_id=report.id,
                    section_id=evidence["section_id"],
                    kb_id=kb_ids[0],
                    document_id=doc_id,
                ))
"""


def group_s() -> None:
    print("\n=== S 组：判据自检（喂改前的写法，必须报 FAIL）===")
    b1_bad, b2_bad, n_app, n_any = judge_main_ts(BAD_MAIN_TS)
    check("S2", not b1_bad and not b2_bad,
          f"自检：改前的 main.ts 写法被 B1/B2 判为 FAIL（createApp(App) {n_app} 次 / createApp( {n_any} 次）")
    check("S3", not judge_citation_block(BAD_CITATION), "自检：改前的 Citation 写法被 C1 判为 FAIL（含 kb_ids[0]）")


# ---------------------------------------------------------------- B 组：main.ts

def group_b() -> None:
    print("\n=== B 组：main.ts 只创建一个 app 实例 ===")
    text = (SRC / "main.ts").read_text(encoding="utf-8")
    ok1, ok2, n_app, n_any = judge_main_ts(text)
    check("B1", ok1, f"createApp(App) 出现 {n_app} 次（应为 1）")
    check("B2", ok2, f"createApp( 总共出现 {n_any} 次（多一次就说明有个实例建了却没被挂载）")


# ---------------------------------------------------------------- C 组：citation.kb_id

async def group_c() -> None:
    print("\n=== C 组：citation.kb_id 取自文档实际所属库 ===")

    # ---- C1 静态：Citation 构造里不许再出现 kb_ids[0]
    tq = (BACKEND / "app" / "services" / "task_queue.py").read_text(encoding="utf-8")
    check("C1", judge_citation_block(tq), "Citation(...) 构造里没有 kb_ids[0]")

    # ---- C2 动态：假装图跑完，让引用同时来自两个库，看落库的 kb_id 对不对
    sys.path.insert(0, str(BACKEND))
    from app.core.db import SessionLocal
    from app.models import Citation, Document, KnowledgeBase, Project, Report, Task
    from sqlalchemy import select, text as sql_text
    from uuid import uuid4

    async with SessionLocal() as s:
        # C3 基线：库里可能已有真实任务（历史手工测试 / 端到端冒烟），
        # 不能假设空库 —— 写死 (0,0,0) 会让判据在正常库上恒红（2026-09-22 实测）。
        baseline = tuple((await s.execute(sql_text(
            "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM reports), (SELECT count(*) FROM citations)"
        ))).one())
        kbs = (await s.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at))).all()
        if len(kbs) < 2:
            check("C2", False, f"需要至少 2 个知识库才能构造跨库场景，当前 {len(kbs)} 个")
            return
        kb_first, kb_second = kbs[0], kbs[1]
        docs = (await s.scalars(
            select(Document).where(Document.status == "ready").order_by(Document.created_at)
        )).all()
        doc_a = next((d for d in docs if d.kb_id == kb_first.id), None)   # 第一个库的文档
        doc_b = next((d for d in docs if d.kb_id == kb_second.id), None)  # 第二个库的文档
        if not (doc_a and doc_b):
            check("C2", False, "两个库都需要至少 1 个 ready 文档")
            return
        project = (await s.scalars(select(Project).limit(1))).first()
        if project is None:
            check("C2", False, "库里没有 project，无法建任务")
            return

        task = Task(
            id=str(uuid4()),
            project_id=project.id,
            objective="T99 验收：跨库引用 kb_id 归属",
            kb_ids=[str(kb_first.id), str(kb_second.id)],   # 第一个库在前 —— 旧写法会取它
            status="pending",
        )
        s.add(task)
        await s.commit()
        task_id = task.id
        expect = {doc_a.id: str(kb_first.id), doc_b.id: str(kb_second.id)}

    # 假图：不调 LLM，直接产出"这一章引用了两个库各一篇文档"
    class FakeGraph:
        async def astream(self, state, stream_mode=None):
            yield {"probe": {"kb_overview": "x", "has_material": True, "material_count": 2}}
            yield {"planner": {"plan": ["第一章"], "focus": ["f"]}}
            yield {"retriever": {"evidence": [{"question": "第一章", "hits": []}]}}
            yield {
                "synthesizer": {
                    "draft": {"title": "t", "sections": [{"title": "第一章", "content": "正文"}]},
                    "evidence_used": [{
                        "section_id": "第一章",
                        "documents": [
                            {"document_id": doc_a.id, "snippet": "片段A"},
                            {"document_id": doc_b.id, "snippet": "片段B"},
                        ],
                    }],
                }
            }
            yield {"critic": {"reviewed": True, "passed": True, "score": 80,
                              "issues": [], "dimensions": [], "retries": 1}}

    import app.services.agent.graph as graph_mod
    orig = graph_mod.build_graph
    graph_mod.build_graph = lambda: FakeGraph()   # _run 内是函数级 import，patch 模块属性有效
    try:
        from app.services.task_queue import _run
        await _run(task_id)
    finally:
        graph_mod.build_graph = orig

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Citation.document_id, Citation.kb_id).where(Citation.report_id.in_(
                select(Report.id).where(Report.task_id == task_id)
            ))
        )).all()

    if not rows:
        check("C2", False, "落库后没有引用记录，场景没构造成功")
    else:
        wrong = [(str(d), str(k)) for d, k in rows if expect.get(str(d)) != str(k)]
        check("C2", not wrong, f"每条 citation.kb_id == 文档所属库；不符：{wrong or '无'}（共 {len(rows)} 条）")
        # 对照组：必须存在"kb_id != kb_ids[0]"的那条，否则场景压根没碰到旧 bug
        has_second = any(str(k) == str(kb_second.id) for _, k in rows)
        check("C2b", has_second, "对照：确实存在来自第二个库的引用（场景能暴露旧写法）")

    # ---- 清理 + 双向核对
    async with SessionLocal() as s:
        await s.execute(sql_text(
            "DELETE FROM citations WHERE report_id IN (SELECT id FROM reports WHERE task_id = :t)"
        ), {"t": task_id})
        await s.execute(sql_text("DELETE FROM reports WHERE task_id = :t"), {"t": task_id})
        await s.execute(sql_text("DELETE FROM tasks WHERE id = :t"), {"t": task_id})
        await s.commit()
        left = (await s.execute(sql_text(
            "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM reports), (SELECT count(*) FROM citations)"
        ))).one()
        kbn = (await s.execute(sql_text("SELECT count(*) FROM knowledge_bases"))).scalar()
        docn = (await s.execute(sql_text("SELECT count(*) FROM documents"))).scalar()

    check("C3", left == baseline, f"清理后回到基线 {tuple(baseline)}（实际 {tuple(left)}）")
    check("C4", kbn >= 2 and docn >= 2, f"清理没误伤：knowledge_bases={kbn} documents={docn}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-db", action="store_true", help="只跑静态判据")
    args = ap.parse_args()

    group_a()
    group_b()
    group_s()
    if args.skip_db:
        print("\n=== C 组：跳过（--skip-db）===")
    else:
        asyncio.run(group_c())

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 60}\n{passed}/{total} PASS")
    for cid, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED {cid}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
