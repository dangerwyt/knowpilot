"""T82 验收：删文档 / 删 KB 后，storage/<kb_id>/ 的空目录是否被一起收掉

背景
    原始文件落在 storage/<kb_id>/<doc_id>.<ext>。删文档时只 unlink 文件，
    父目录空了没人管 → 空目录永久堆积（实测 23 个一级目录、21 个空）。

判据
    R1 [尺子]   脚本能检测到空目录（造一个 → 扫得到）——证明"尺子"看得见
    R2 [核心]   删掉目录里最后一个文件后，父目录应一起消失
    R3 [反向]   目录里还有别的文件时，父目录必须保留（不能拆别人的家）
    R4 [边界]   delete_original(None) 安全：不抛异常、不建 storage/None
    R5 [边界]   object_key 指向的文件本就不存在 → 不抛异常、不炸目录
    R6 [安全]   单级 object_key 绝不能把 storage/ 根目录本身删掉
    R7 [端到端] API 建 KB → 上传 → 删文档，storage/<kb_id>/ 应消失
    R8 [隔离]   删 A 目录里的文件，不影响 B 目录

改前预期：R2 FAIL、R7 FAIL，其余 PASS（目录永远不删是"没做"，不是"做错"）
改后预期：本机沙箱下 7 PASS + R3 SKIP（见下方「重要」一节）；无沙箱时 8/8 PASS

为什么 R6 要用隔离的临时目录
    直接在真 storage/ 里验"根目录会不会被误删"是验不出来的 —— 真 storage 里
    还有 20 多个目录，rmdir 必然抛"目录非空"，无论实现对不对都是 PASS。
    只有在"目录里最后一个文件被删掉、目录确实空了"的场景下，写错的实现
    （拿 path.parent 去 rmdir）才会真的把根删掉。所以这里把 STORAGE_DIR
    临时替换成一个空目录来做这个实验。

【重要】本脚本的局限：它的 os.rmdir 可能不是真的 os.rmdir
    在 WorkBuddy 这类带「安全删除」拦截层的环境里，Python 的 os.rmdir / unlink
    会被改写：rmdir 一个**非空**目录竟然会「成功」，并连同目录内容一起删掉。
    真实文件系统的语义是抛 OSError（实测 Docker 沙箱外：exit=1 "Directory not empty"，
    文件完好）。所以在本机沙箱里跑，R3 会得到**假 FAIL**。

    脚本启动时会自动探测这一点（见 sandbox_delete_detected），命中就把受影响的
    判据标 SKIP 并提示改用：
        playground/t82_real_env_test.py   ← 通过 HTTP 让后端进程执行删除，
                                            后端不在沙箱内，行为即真实行为

安全设计
    只操作脚本自己创建的 `t82_*` 命名的目录/文件 + 临时目录；
    绝不遍历删除真实 storage 内容，也不碰真实 KB 的数据。

用法：
    cd backend && .venv/Scripts/python.exe playground/t82_storage_cleanup_test.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]   # .../knowpilot/backend
sys.path.insert(0, str(BACKEND))

import app.core.storage as storage_mod          # noqa: E402
from app.core.storage import delete_original    # noqa: E402

STORAGE_DIR = storage_mod.STORAGE_DIR
API = "http://localhost:8000/api/v1"
EMAIL, PWD = "kptest@example.com", "test123456"
PREFIX = "t82_"

results: list[tuple[str, bool | None, str]] = []   # ok=None 表示 SKIP


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def skip(name: str, detail: str = "") -> None:
    results.append((name, None, detail))
    print(f"[SKIP] {name}" + (f"  {detail}" if detail else ""))


def sandbox_delete_detected() -> tuple[bool, str]:
    """探测本机 Python 的 os.rmdir 是否被「安全删除」层改写。

    真实语义：rmdir 一个含文件的目录 → 抛 OSError，目录和文件都还在。
    被改写后：返回"成功"，目录连同里面的文件一起消失。

    判据只落在"抛不抛错"上，不依赖目录是否真的还在（那正是被测行为）。

    【关键】探针必须建在 **被测的那个目录里**（STORAGE_DIR），不能用临时目录：
    拦截层的生效范围是按路径的 —— 实测同一个 os.rmdir，在系统临时目录里正常抛错，
    在 storage/ 里却"成功"并吃掉目录内容。探针放错地方就会给出"没问题"的假结论。
    """
    root = STORAGE_DIR / f"{PREFIX}rmdir_probe"
    shutil.rmtree(root, ignore_errors=True)
    d = root / "nonempty"
    d.mkdir(parents=True, exist_ok=True)
    (d / "keep.txt").write_text("k", encoding="utf-8")
    try:
        os.rmdir(str(d))
        return True, (f"os.rmdir(非空目录) 在 {PREFIX}rmdir_probe 下返回成功 → "
                      f"本机删除被拦截层改写，判据不可信")
    except OSError as e:
        return False, f"os.rmdir(非空目录) 正常抛错 {type(e).__name__} → 可用"
    finally:
        shutil.rmtree(root, ignore_errors=True)


SANDBOXED, SANDBOX_WHY = sandbox_delete_detected()


def scan_empty_top_dirs() -> list[str]:
    """列出 storage 下"一个条目都没有"的一级目录。"""
    out: list[str] = []
    for d in sorted(STORAGE_DIR.iterdir()):
        try:
            if d.is_dir() and not any(d.iterdir()):
                out.append(d.name)
        except OSError:
            pass
    return out


def cleanup_probes() -> None:
    """兜底清理：只删脚本自己造的 t82_* 目录/文件。"""
    for p in sorted(STORAGE_DIR.iterdir()):
        if not p.name.startswith(PREFIX):
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- R1 尺子
def r1_ruler() -> None:
    probe = STORAGE_DIR / f"{PREFIX}probe_empty_dir"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        hit = probe.name in scan_empty_top_dirs()
        check("R1 [尺子] 造一个空目录 → 扫描能看见它", hit,
              f"扫描结果含 {probe.name}={hit}（看不见就说明这脚本量不出东西）")
    finally:
        try:
            probe.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------- R2 核心
def r2_last_file_removed() -> None:
    kb = STORAGE_DIR / f"{PREFIX}r2_kb"
    try:
        kb.mkdir(parents=True, exist_ok=True)
        f = kb / "only.md"
        f.write_text("t82 r2", encoding="utf-8")

        delete_original(f"{PREFIX}r2_kb/only.md")

        file_gone = not f.exists()
        dir_gone = not kb.exists()
        check("R2 [核心] 删最后一个文件后父目录消失", file_gone and dir_gone,
              f"文件已删={file_gone}，父目录已清={dir_gone}"
              + ("" if dir_gone else "  ← 目录还在，就是本任务要修的问题"))
    finally:
        shutil.rmtree(kb, ignore_errors=True)


# ---------------------------------------------------------------- R3 反向
def r3_keep_when_not_empty() -> None:
    if SANDBOXED:
        # 本机 rmdir 被改写：删 a.md 时 rmdir 会"成功"并顺带删掉 b.md，
        # 于是正确实现也会判 FAIL。这不是代码问题，是测量工具坏了 —— 标记 SKIP，
        # 由 t82_real_env_test.py 的 B 项（后端进程执行）来验同一条判据。
        skip("R3 [反向] 目录里还有别的文件 → 只能删文件、不能删目录",
             "本机 os.rmdir 被拦截层改写，此项无法在本脚本内验证；"
             "改跑 playground/t82_real_env_test.py 的 B 项（已 5/5 PASS）")
        return
    kb = STORAGE_DIR / f"{PREFIX}r3_kb"
    try:
        kb.mkdir(parents=True, exist_ok=True)
        a, b = kb / "a.md", kb / "b.md"
        a.write_text("a", encoding="utf-8")
        b.write_text("b", encoding="utf-8")

        delete_original(f"{PREFIX}r3_kb/a.md")

        ok = (not a.exists()) and b.exists() and kb.exists()
        check("R3 [反向] 目录里还有别的文件 → 只能删文件、不能删目录", ok,
              f"a 已删={not a.exists()}，b 仍在={b.exists()}，目录仍在={kb.exists()}")
    finally:
        shutil.rmtree(kb, ignore_errors=True)


# ---------------------------------------------------------------- R4 边界
def r4_none_safe() -> None:
    before = {p.name for p in STORAGE_DIR.iterdir()}
    raised = ""
    try:
        delete_original(None)
        delete_original("")
    except Exception as e:  # noqa: BLE001
        raised = f"{type(e).__name__}: {e}"
    after = {p.name for p in STORAGE_DIR.iterdir()}
    no_none_dir = not (STORAGE_DIR / "None").exists()
    check("R4 [边界] delete_original(None/\"\") 安全，不建 storage/None", not raised and before == after and no_none_dir,
          f"异常={raised or '无'}，storage 条目未变={before == after}，无 storage/None={no_none_dir}")


# ---------------------------------------------------------------- R5 边界
def r5_missing_safe() -> None:
    raised = ""
    try:
        delete_original(f"{PREFIX}r5_not_exist_kb/nope.md")
    except Exception as e:  # noqa: BLE001
        raised = f"{type(e).__name__}: {e}"
    no_dir_created = not (STORAGE_DIR / f"{PREFIX}r5_not_exist_kb").exists()
    check("R5 [边界] 文件和父目录都不存在 → 不抛异常、不凭空建目录", not raised and no_dir_created,
          f"异常={raised or '无'}，未新建目录={no_dir_created}")


# ---------------------------------------------------------------- R6 安全
def r6_never_delete_root() -> None:
    """把 STORAGE_DIR 临时换成空目录，验单级 object_key 不会把根删掉。

    关键：这里必须用"空目录"当根，否则 rmdir 会因"目录非空"失败，
    无论实现对错都会 PASS，等于没验。
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="t82_isolated_"))
    orig = storage_mod.STORAGE_DIR
    try:
        storage_mod.STORAGE_DIR = tmp_root
        single = tmp_root / "single_level.md"
        single.write_text("x", encoding="utf-8")

        raised = ""
        try:
            delete_original("single_level.md")      # 单级：没有 kb_id 那一层
        except Exception as e:  # noqa: BLE001
            raised = f"{type(e).__name__}: {e}"

        file_gone = not single.exists()
        root_alive = tmp_root.is_dir()
        check("R6 [安全] 单级 object_key 删除后，storage 根目录必须还在", file_gone and root_alive,
              f"文件已删={file_gone}，根目录健在={root_alive}"
              + ("" if root_alive else "  ← 实现在拿 path.parent 去 rmdir，退过头了！")
              + f"，异常={raised or '无'}")
    finally:
        storage_mod.STORAGE_DIR = orig
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------- R8 隔离
def r8_isolation() -> None:
    d_a = STORAGE_DIR / f"{PREFIX}r8_a"
    d_b = STORAGE_DIR / f"{PREFIX}r8_b"
    try:
        d_a.mkdir(parents=True, exist_ok=True)
        d_b.mkdir(parents=True, exist_ok=True)
        (d_a / "x.md").write_text("x", encoding="utf-8")
        (d_b / "y.md").write_text("y", encoding="utf-8")

        delete_original(f"{PREFIX}r8_a/x.md")

        a_gone = not d_a.exists()
        b_intact = (d_b / "y.md").exists() and d_b.exists()
        check("R8 [隔离] 删 A 目录的文件，B 目录毫发无损", a_gone and b_intact,
              f"A 已清={a_gone}，B 及其文件完好={b_intact}")
    finally:
        shutil.rmtree(d_a, ignore_errors=True)
        shutil.rmtree(d_b, ignore_errors=True)


# ---------------------------------------------------------------- R7 端到端
def r7_e2e(client: httpx.Client, token: str, kb_name: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    kb_id = doc_id = None
    try:
        r = client.post("/knowledge-bases", json={"name": kb_name}, headers=headers)
        if r.status_code != 201:
            check("R7 [端到端] 建 KB → 上传 → 删文档后目录消失", False,
                  f"建 KB 失败 HTTP {r.status_code} {r.text[:120]}")
            return
        kb_id = r.json()["id"]

        r = client.post(
            f"/knowledge-bases/{kb_id}/documents",
            files={"file": ("t82_probe.md", b"# t82 probe\n\ncontent", "text/markdown")},
            headers=headers,
        )
        if r.status_code != 201:
            check("R7 [端到端] 建 KB → 上传 → 删文档后目录消失", False,
                  f"上传失败 HTTP {r.status_code} {r.text[:120]}")
            return
        doc_id = r.json()["id"]

        # 前置条件：文件真的落盘了（否则后面删不出"目录变空"的场景，验了也不算数）
        doc_dir = STORAGE_DIR / kb_id
        landed = doc_dir.is_dir() and any(doc_dir.iterdir())
        if not landed:
            check("R7 [端到端] 建 KB → 上传 → 删文档后目录消失", False,
                  f"前置条件不成立：上传后 storage/{kb_id} 里没有文件，"
                  f"（可能 object_key 落盘失败）无法验证删除行为")
            return

        r = client.delete(f"/knowledge-bases/{kb_id}/documents/{doc_id}", headers=headers)
        deleted_ok = r.status_code == 204
        dir_gone = not doc_dir.exists()
        check("R7 [端到端] API 建 KB → 上传 → 删文档后目录消失", deleted_ok and dir_gone,
              f"删除 HTTP {r.status_code}，storage/{kb_id[:8]}… 已清={dir_gone}"
              + ("" if dir_gone else "  ← 接口返回成功但目录留着"))
    finally:
        # 清理：KB 若还在就删掉（顺带清 documents 行）
        if kb_id:
            try:
                client.delete(f"/knowledge-bases/{kb_id}", headers=headers)
            except Exception:  # noqa: BLE001
                pass
        if kb_id:
            shutil.rmtree(STORAGE_DIR / kb_id, ignore_errors=True)


def main() -> int:
    print("=" * 78)
    print("T82 验收：storage 空目录是否随文件删除一起被收掉")
    print("=" * 78)
    print(f"storage 根：{STORAGE_DIR}")
    print(f"删除语义探测：{SANDBOX_WHY}")
    if SANDBOXED:
        print()
        print("!" * 78)
        print("注意：本机 Python 的 os.rmdir 被「安全删除」层改写，涉及目录保留/删除的")
        print("      判据不可信（会假红）。受影响的判据已标 SKIP —— 那不是代码的问题。")
        print("      请用 playground/t82_real_env_test.py 复验（后端进程执行删除，5/5 PASS）。")
        print("!" * 78)
    empty_before = scan_empty_top_dirs()
    print(f"跑前现状：一级目录 {sum(1 for p in STORAGE_DIR.iterdir() if p.is_dir())} 个，"
          f"其中空目录 {len(empty_before)} 个\n")

    cleanup_probes()   # 先清上次跑挂留下的痕迹

    r1_ruler()
    r2_last_file_removed()
    r3_keep_when_not_empty()
    r4_none_safe()
    r5_missing_safe()
    r6_never_delete_root()
    r8_isolation()

    # R7 需要服务在跑
    kb_name = f"t82-probe-{int(time.time())}"
    try:
        with httpx.Client(base_url=API, timeout=30) as client:
            r = client.post("/auth/login", json={"email": EMAIL, "password": PWD})
            token = r.json().get("token") if r.status_code == 200 else None
            if not token:
                check("R7 [端到端] API 建 KB → 上传 → 删文档后目录消失", False,
                      f"登录失败 HTTP {r.status_code} —— uvicorn 没跑？后续端到端跳过")
            else:
                r7_e2e(client, token, kb_name)
    except Exception as e:  # noqa: BLE001
        check("R7 [端到端] API 建 KB → 上传 → 删文档后目录消失", False,
              f"连不上 {API}：{type(e).__name__} —— 服务没起则此项无法验证")

    cleanup_probes()

    empty_after = scan_empty_top_dirs()
    print()
    print("-" * 78)
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    skipped = sum(1 for _, ok, _ in results if ok is None)
    print(f"结果：{passed} PASS / {failed} FAIL / {skipped} SKIP（共 {len(results)} 项）")
    for name, ok, d in results:
        if ok is False:
            print(f"  FAIL {name}  {d}")
        elif ok is None:
            print(f"  SKIP {name}  {d}")
    print("-" * 78)
    print(f"跑后空目录：{len(empty_after)} 个（历史遗留的不会被本脚本清理，"
          f"那是单独一步；本脚本只清自己的 {PREFIX}* 探针）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
