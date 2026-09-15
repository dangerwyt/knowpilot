"""仓库体检：提交前跑一遍，确认 .gitignore 正反向都对、没有死引用。

判据分四组：
  G 组（gitignore 正反向）：该排的排掉、该留的留下
  W 组（白名单）：被点名引用的 playground 工具确实进了仓库；没被点名的一个都没混进来
  D 组（死引用）：**入库文件里引用的 playground 文件必须都在仓库里**
  C 组（内容卫生）：无 .env、无大文件、无依赖目录、关键业务文件齐全

D 组是核心判据 —— 仓库里存不存在「叫你去跑一个不存在的文件」的情况。
只在「会进仓库的文件」里搜引用，因为不入库的文件自己引用不到也不影响别人。

两个坑（都实测踩过，见踩坑 #94）：
  ① **必须排除脚本自身**：本脚本的判据列表里写着一堆 playground 文件名，
     不排除的话 D1 会把它们全当成"死引用"（那些未入库的探针名尤其明显）。
  ② **要排除文档里举例用的占位符**（`backend/playground/xxx.py`）：
     文档天然会写示例，判据把它们当真实引用就会假红。

用法（任意目录直接跑）：
    backend/.venv/Scripts/python.exe backend/playground/t83_git_repo_check.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # playground → backend → knowpilot

results: list[tuple[str, bool, str]] = []
skipped: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def check_skip(name: str, detail: str = "") -> None:
    skipped.append(name)
    print(f"[SKIP] {name}" + (f"  {detail}" if detail else ""))


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout


def tracked_or_pending() -> list[str]:
    """已提交 + 待提交的全部文件（相对仓库根的 posix 路径）。"""
    out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(x for x in out.split("\0") if x)


# ---------------------------------------------------------------- G 组
def g_group() -> None:
    def ignored(path: str) -> bool:
        p = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        return p.returncode == 0

    must_ignore = [
        ".env",
        "backend/.env",
        "backend/.venv/pyvenv.cfg",
        "frontend/node_modules/vue/index.js",
        "backend/storage/some-kb/some-doc.md",
        "backend/playground/t78_epoch_test.py",      # 验收脚本（未点名引用）
        "backend/playground/t78probe/t78_epoch_probe.md",
        "backend/playground/__pycache__/x.pyc",
        "frontend/dist/index.js",
        "frontend/vite.config.js",
        "frontend/tsconfig.app.tsbuildinfo",
        "backend/migrations/APPLIED.log",
    ]
    bad = [p for p in must_ignore if not ignored(p)]
    check("G1 [正向] 敏感文件/依赖目录/运行时产物全部被忽略", not bad,
          f"共 {len(must_ignore)} 项；漏网={bad or '无'}")

    must_keep = [
        "backend/app/main.py",
        "frontend/src/main.ts",
        ".env.example",
        "frontend/.env.development",
        "backend/migrations/0005_t81_drop_chunks.sql",
        "backend/migrations/README.md",
        "backend/playground/schema_drift.py",
        "docs/PRD-知研-AI研究工作台.md",
        "docker-compose.yml",
    ]
    bad2 = [p for p in must_keep if ignored(p)]
    check("G2 [反向] 源码/示例配置/文档/白名单工具未被误拦", not bad2,
          f"共 {len(must_keep)} 项；误拦={bad2 or '无'}")


# ---------------------------------------------------------------- W 组
WHITELIST = [
    "backend/playground/schema_drift.py",
    "backend/playground/drift_check.py",
    "backend/playground/cleanup_orphans.py",
    "backend/playground/rebuild_vectors.py",
    "backend/playground/reload_vectors.py",
    "backend/playground/t82_real_env_test.py",
    "backend/playground/t82_storage_cleanup_test.py",
    "backend/playground/t86_probe_threshold_test.py",
    "backend/playground/t86_e2e_task_test.py",
    "backend/playground/t87_retriever_material_test.py",
    "backend/playground/t83_git_repo_check.py",
    "backend/playground/t87_langgraph_key_probe.py",
    "backend/playground/t88_critic_dimensions_test.py",
    "backend/playground/t88_frontend_dimensions_test.js",
    "backend/playground/t89a_has_material_test.py",
    "backend/playground/t89a_frontend_has_material_test.js",
    "backend/playground/t89b_precheck_test.py",
    "backend/playground/t89b_frontend_precheck_test.js",
    "backend/playground/t98_event_order_test.py",
    "backend/playground/check_worker_start.py",
    "backend/playground/t99_fixes_test.py",
    "backend/playground/t99_frontend_icon_test.js",
    "backend/playground/t100_compose_worker_test.py",
]


def w_group(pending: list[str]) -> None:
    missing = [p for p in WHITELIST if p not in pending]
    check(f"W1 [白名单] {len(WHITELIST)} 个被引用的 playground 工具都进了仓库", not missing,
          f"缺={missing or '无'}")

    # W2 反向：未被文档点名的一次性探针**必须仍然被忽略**（否则白名单会变成"全收"）
    still_ignored = [
        "backend/playground/t78_epoch_test.py",
        "backend/playground/t81_drop_chunks_test.py",
        "backend/playground/t85_planner_kb_aware_test.py",
    ]
    leaked = [p for p in still_ignored if p in pending]
    check("W2 [反向] 未被点名的一次性探针仍被忽略", not leaked, f"漏放={leaked or '无'}")


# ---------------------------------------------------------------- D 组
REF_RE = re.compile(r"playground/([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|md|sql|sh))")
# 文档里举例说明写法用的占位符，不是"叫你去跑某个文件"的引用（如 #85 的 `!backend/playground/xxx.py`）
PLACEHOLDER_RE = re.compile(r"(xxx|yyy|zzz|foo|bar|example|placeholder)", re.I)


def d_group(pending: list[str]) -> None:
    pending_set = set(pending)
    try:
        self_rel = Path(__file__).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        self_rel = "backend/playground/t83_git_repo_check.py"
    dead: list[str] = []
    refs_found = 0
    scanned = 0

    for rel in pending:
        if rel == ".gitignore":
            continue          # 白名单规则本身写了这些文件名，不算"引用"
        if rel == self_rel:
            continue          # 自扫：本脚本的判据列表里写着一堆 playground 文件名
        p = ROOT / rel
        if not p.is_file():
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in REF_RE.finditer(text):
            if PLACEHOLDER_RE.search(m.group(1)):
                continue      # 文档里的示例占位符（`!backend/playground/xxx.py`），不是真引用
            name = "backend/playground/" + m.group(1)
            if name == rel:
                continue      # 自引用
            refs_found += 1
            if name not in pending_set:
                line = text[:m.start()].count("\n") + 1
                dead.append(f"{rel}:{line} → {name}")

    check("D1 [死引用] 入库文件引用的 playground 文件全部真实存在", not dead,
          f"扫 {scanned} 个入库文件、{refs_found} 处引用；死引用={len(dead)}" +
          ("" if not dead else "\n           " + "\n           ".join(sorted(set(dead)))))


# ---------------------------------------------------------------- C 组
def c_group(pending: list[str]) -> None:
    secrets = [p for p in pending if re.search(r"(^|/)\.env$", p)]
    check("C1 [卫生] 仓库里没有任何 .env（密钥文件）", not secrets,
          f"命中={secrets or '无'}")

    dep_dirs = [p for p in pending if ".venv" in p or "node_modules" in p]
    check("C2 [卫生] 无依赖目录混入", not dep_dirs,
          f"命中={len(dep_dirs)} 条")

    runtime = [p for p in pending if p.startswith("backend/storage/")]
    check("C3 [卫生] storage/（用户上传文件）未入库", not runtime,
          f"命中={len(runtime)} 条")

    locks = [p for p in pending if p.endswith(("package-lock.json", "pnpm-lock.yaml", "yarn.lock"))]
    check("C4 [卫生] 前端只有一套锁文件", len(locks) == 1, f"命中={locks}")

    big = []
    for p in pending:
        fp = ROOT / p
        if fp.is_file() and fp.stat().st_size > 500_000:
            big.append(f"{p}({fp.stat().st_size // 1024}K)")
    check("C5 [卫生] 无超大文件（>500K）", not big, f"命中={big or '无'}")

    # 关键业务文件抽查
    key = [
        "backend/app/services/agent/nodes.py",
        "backend/app/services/agent/graph.py",
        "backend/app/services/agent/state.py",
        "backend/app/services/task_queue.py",
        "backend/app/services/rag/milvus_client.py",
        "backend/app/models/__init__.py",
        "backend/app/core/redis.py",
        "backend/app/core/storage.py",
        "backend/app/api/v1/endpoints/kb.py",
        "frontend/src/views/WorkbenchView.vue",
        "frontend/src/composables/useDocPolling.ts",
    ]
    miss = [p for p in key if p not in pending]
    check("C6 [完整] 关键业务文件一个不少", not miss, f"缺={miss or '无'}")


def main() -> int:
    print(f"仓库根：{ROOT}")
    print(f"分支  ：{subprocess.run(['git','branch','--show-current'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}")
    print()

    pending = tracked_or_pending()
    g_group()
    w_group(pending)
    d_group(pending)
    c_group(pending)

    print()
    print("-" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"结果：{passed}/{len(results)} PASS" + (f"，{len(skipped)} SKIP" if skipped else ""))
    for name, ok, d in results:
        if not ok:
            print(f"  FAIL {name}  {d}")
    print("-" * 78)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
