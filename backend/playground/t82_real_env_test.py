"""T82 真实环境验收：让 uvicorn 进程（不受本机沙箱影响）执行删除，验目录行为。

为什么要单独写这一份
    WorkBuddy 的沙箱会拦截 Python 的 os.rmdir / unlink，把它改写成"安全删除"，
    而该实现是按整棵目录树删的 —— 于是在沙箱里跑 os.rmdir(非空目录) 会
    "成功"并把里面的文件一起带走。这会让 t82_storage_cleanup_test.py 的
    R3（反向验证：目录非空时必须保留）产生假 FAIL。

    os.rmdir 的真实语义（Docker 沙箱外实测）：
        rmdir 非空目录 → "Directory not empty"，exit=1，文件完好

    所以本脚本不自己调 delete_original，而是 **通过 HTTP 让后端进程去删** →
    后端不在沙箱里，行为即生产的真实行为。脚本只做只读断言。

判据
    A [前置] 同一个 KB 上传两个文档，两个文件都真的落盘
    B [核心] 删掉其中一个 → 该文件消失，但**另一个文件仍在、目录仍在**
            （这一条就是 R3，之前被沙箱误判为 FAIL）
    C [核心] 删掉最后一个 → 目录消失（T82 要修的目标行为）
    D [隔离] 别的 KB 的目录不受影响
    E [收尾] 清理干净（文档、KB、目录都不留）

用法：
    cd backend && .venv/Scripts/python.exe playground/t82_real_env_test.py
"""
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]   # .../knowpilot/backend
sys.path.insert(0, str(BACKEND))

from app.core.storage import STORAGE_DIR         # noqa: E402

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

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def snap() -> set[str]:
    """当前 storage 全部文件的相对路径快照（只读）。"""
    return {str(p.relative_to(STORAGE_DIR)).replace("\\", "/")
            for p in STORAGE_DIR.rglob("*") if p.is_file()}


def main() -> int:
    print("=" * 78)
    print("T82 真实环境验收：由后端进程执行删除，验 storage 目录行为")
    print("=" * 78)
    print(f"storage 根：{STORAGE_DIR}\n")

    before = snap()
    print(f"跑前 storage 文件数：{len(before)}")

    headers: dict[str, str] = {}
    kb_id = other_kb_id = None
    doc_ids: list[str] = []
    kb_dir = other_dir = None

    try:
        with httpx.Client(base_url=API, timeout=60) as client:
            r = client.post("/auth/login", json={"email": EMAIL, "password": PWD})
            if r.status_code != 200:
                check("A [前置] 登录 + 建 KB + 上传两个文档", False,
                      f"登录失败 HTTP {r.status_code} —— uvicorn 没跑？")
                return 1
            headers = {"Authorization": f"Bearer {r.json()['token']}"}

            # ---------- 建 KB 并上传两个文档 ----------
            r = client.post("/knowledge-bases", json={"name": f"t82-real-{int(time.time())}"},
                            headers=headers)
            kb_id = r.json()["id"]
            kb_dir = STORAGE_DIR / kb_id

            for i in (1, 2):
                r = client.post(
                    f"/knowledge-bases/{kb_id}/documents",
                    files={"file": (f"t82_real_{i}.md", f"# t82 real {i}\n\nbody {i}".encode(),
                                    "text/markdown")},
                    headers=headers,
                )
                if r.status_code != 201:
                    check("A [前置] 登录 + 建 KB + 上传两个文档", False,
                          f"第 {i} 个上传失败 HTTP {r.status_code} {r.text[:120]}")
                    return 1
                doc_ids.append(r.json()["id"])

            landed = sorted(p.name for p in kb_dir.iterdir()) if kb_dir.is_dir() else []
            check("A [前置] 两个文件都真的落盘", len(landed) == 2,
                  f"storage/{kb_id[:8]}… 里 {len(landed)} 个文件：{landed}")

            # 记下 B 阶段要保住的"另一个文件"
            keep_doc = doc_ids[1]
            keep_files = {p.name for p in kb_dir.iterdir()} if kb_dir.is_dir() else set()

            # ---------- B：删第一个，目录必须留下 ----------
            r = client.delete(f"/knowledge-bases/{kb_id}/documents/{doc_ids[0]}", headers=headers)
            deleted_ok = r.status_code == 204
            now_files = {p.name for p in kb_dir.iterdir()} if kb_dir.is_dir() else set()
            survivors = keep_files & now_files
            check("B [核心] 删一个文档后：文件少一个，另一个文件与目录都必须保留",
                  deleted_ok and kb_dir.is_dir() and len(now_files) == 1 and len(survivors) == 1,
                  f"删除 HTTP {r.status_code}；目录仍在={kb_dir.is_dir()}；"
                  f"剩 {len(now_files)} 个文件（应 1 个，即未删那个）；"
                  f"保留下来的={sorted(survivors)}")

            # ---------- D：另一个 KB 的目录不受影响 ----------
            r = client.post("/knowledge-bases", json={"name": f"t82-real-other-{int(time.time())}"},
                            headers=headers)
            other_kb_id = r.json()["id"]
            other_dir = STORAGE_DIR / other_kb_id
            r = client.post(
                f"/knowledge-bases/{other_kb_id}/documents",
                files={"file": ("t82_other.md", b"# other\n\nx", "text/markdown")},
                headers=headers,
            )
            other_landed = other_dir.is_dir() and len(list(other_dir.iterdir())) == 1

            # 现在删掉原 KB 的最后一个文档 → 目录应消失
            r = client.delete(f"/knowledge-bases/{kb_id}/documents/{keep_doc}", headers=headers)
            dir_gone = not kb_dir.exists()
            check("C [核心] 删掉最后一个文档后，storage/<kb_id> 目录消失",
                  r.status_code == 204 and dir_gone,
                  f"删除 HTTP {r.status_code}，目录已清={dir_gone}"
                  + ("" if dir_gone else "  ← T82 要修的问题仍在"))

            other_intact = other_dir.is_dir() and len(list(other_dir.iterdir())) == 1
            check("D [隔离] 另一个 KB 的目录与文件毫发无损",
                  other_landed and other_intact,
                  f"对方落盘={other_landed}，现在仍在={other_intact}")

            # ---------- E：清理 ----------
            client.delete(f"/knowledge-bases/{kb_id}", headers=headers)
            client.delete(f"/knowledge-bases/{other_kb_id}", headers=headers)

        after = snap()
        leaked = after - before
        check("E [收尾] storage 回到跑前状态（无残留）", not leaked,
              f"跑前 {len(before)} 个文件，跑后 {len(after)} 个；多出来={sorted(leaked) or '无'}")

    except Exception as e:  # noqa: BLE001
        check("A [前置] 登录 + 建 KB + 上传两个文档", False,
              f"异常 {type(e).__name__}: {e}")
        raise

    print()
    print("-" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"结果：{passed}/{len(results)} PASS")
    for name, ok, d in results:
        if not ok:
            print(f"  FAIL {name}  {d}")
    print("-" * 78)
    if kb_dir:
        print(f"现场：storage/{kb_dir.name[:8]}… 存在={kb_dir.exists()}")
    if other_dir:
        print(f"      storage/{other_dir.name[:8]}… 存在={other_dir.exists()}")
    print(f"      storage 一级目录 {sum(1 for p in STORAGE_DIR.iterdir() if p.is_dir())} 个，"
          f"文件 {len(snap())} 个")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
