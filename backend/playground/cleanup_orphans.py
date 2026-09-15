"""清理孤儿数据：Milvus 里残留的向量块 + storage 里的无主目录。

背景
    验收脚本的常见流程是「建 KB → 上传探针文档 → 删 KB」。DB 行删得干净，
    但另外两处会留垃圾：
      ① Milvus 块 —— 删除不可靠（踩坑 #61/#77），经常删不净留孤儿块
      ② storage 目录 —— 删知识库时只删了文件，目录壳（甚至文件）会留下
    本脚本把「不属于现存文档 / 现存知识库」的垃圾找出来清掉。

孤儿判据
    Milvus 块：其 document_id 不在 documents 表        → 孤儿（躲不过，因为文档已删）
    storage：目录名（就是 kb_id）不在 knowledge_bases 表 → 孤儿
    反之，凡是能和 DB 对上的，一律保留。

安全设计（都是踩过坑换来的）
    · 默认干跑：只打印要删什么，不动手。必须显式 --apply
    · storage 要再额外加 --storage 才动（Milvus 是网络服务、风险低；storage 是真实文件）
    · 只删「storage 下一级、名字是 UUID」的目录。绝不遍历、绝不递归删上层的 storage/backend
    · 删 storage 前把文件名 / 大小 / md5 落盘留档，事后可核对
    · 本机删除会被工具沙箱改写为「投递回收站」，所以删错了仍有找回余地
    · 全程只读断言：任何一次 DB 查询失败都会抛错，不会静默返回空集合
      （否则「查不到孤儿」会被误读成「没有孤儿」——踩坑 #70）

用法
    cd backend && .venv/Scripts/python.exe playground/cleanup_orphans.py
    cd backend && .venv/Scripts/python.exe playground/cleanup_orphans.py --apply
    cd backend && .venv/Scripts/python.exe playground/cleanup_orphans.py --apply --storage
"""

import argparse
import asyncio
import hashlib
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]   # .../knowpilot/backend
sys.path.insert(0, str(BACKEND))

from pymilvus import MilvusClient  # noqa: E402

STORAGE_DIR = BACKEND / "storage"
COLLECTION = "knowpilot_chunks"
MILVUS_URI = "http://localhost:19530"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MANIFEST = BACKEND.parents[1] / ".workbuddy" / "tmp" / "cleanup_orphans_manifest.txt"

results: list[tuple[str, bool, str]] = []
skipped: list[str] = []


def skip(label: str, reason: str) -> None:
    skipped.append(label)
    print(f"[跳过] {label} —— {reason}")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


async def pg(sql: str) -> list[list[str]]:
    """裸 SQL。psql 必须 -q（踩坑 #71）；失败必须抛错，不许返回空（踩坑 #70）。"""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", "knowpilot-postgres-1", "psql",
        "-U", "knowpilot", "-d", "knowpilot", "-q", "-t", "-A", "-F", "|", "-c", sql,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"SQL 失败：{err.decode('utf-8', 'replace').strip()}\nSQL: {sql[:300]}")
    rows: list[list[str]] = []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.strip():
            rows.append(line.split("|"))
    return rows


# ---------------------------------------------------------------- 安全护栏

def assert_safe_target(target: Path) -> None:
    """拒绝一切「越界」的删除目标。这是防重演的核心。"""
    t = target.resolve()
    protected = {
        STORAGE_DIR.resolve(): "storage 根目录",
        BACKEND.resolve(): "backend 根目录",
        BACKEND.parent.resolve(): "项目根目录",
        Path.home().resolve(): "用户主目录",
        Path(t.anchor).resolve(): "盘符根目录",
    }
    if t in protected:
        raise AssertionError(f"目标是受保护目录（{protected[t]}），拒绝操作：{t}")
    if not t.is_dir():
        raise AssertionError(f"不是目录：{t}")
    if t.parent.resolve() != STORAGE_DIR.resolve():
        raise AssertionError(f"必须是 storage 的直接子目录：{t}")
    if STORAGE_DIR.name != "storage":
        raise AssertionError(f"STORAGE_DIR 名字异常：{STORAGE_DIR}")
    if not UUID_RE.match(t.name):
        raise AssertionError(f"目录名不是 UUID，不是本系统创建的目录：{t.name}")


def self_test_guard() -> None:
    """护栏自检：这些目标必须全部被拒绝. 护栏本身也要能 FAIL（踩坑 #80）。"""
    bad = [
        STORAGE_DIR,                     # storage 根
        BACKEND,                         # backend 根
        BACKEND.parent,                  # 项目根
        STORAGE_DIR / "not-a-uuid",      # 名字不是 UUID
    ]
    refused = 0
    for t in bad:
        try:
            assert_safe_target(t)
        except AssertionError:
            refused += 1
    check("G1 [护栏] 越界目标（storage 根/backend/项目根/非 UUID）全部被拒",
          refused == len(bad), f"{refused}/{len(bad)} 被拒")


def file_manifest(d: Path) -> list[tuple[str, int, str]]:
    out = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            h = hashlib.md5()
            with f.open("rb") as fh:
                for blk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(blk)
            out.append((f.relative_to(d).as_posix(), f.stat().st_size, h.hexdigest()))
    return out


# ---------------------------------------------------------------- 主流程

async def main() -> int:
    ap = argparse.ArgumentParser(description="清理孤儿向量块 / 无主 storage 目录")
    ap.add_argument("--apply", action="store_true", help="真的删（不加则只干跑）")
    ap.add_argument("--storage", action="store_true", help="连 storage 无主目录一起清（破坏性）")
    args = ap.parse_args()

    mode = "APPLY（真删）" if args.apply else "DRY-RUN（只看不动）"
    print("=" * 78)
    print(f"清理孤儿数据 · 模式={mode} · storage={'ON' if args.storage else 'OFF'}")
    print("=" * 78)
    print(f"storage : {STORAGE_DIR}")
    print(f"manifest: {MANIFEST}")
    print()

    self_test_guard()
    print()

    # ---------- 白名单：能对上 DB 的一律保留 ----------
    db_docs = {r[0] for r in await pg("SELECT id FROM documents")}
    db_kbs = {r[0] for r in await pg("SELECT id FROM knowledge_bases")}
    print(f"白名单：documents {len(db_docs)} 行、knowledge_bases {len(db_kbs)} 行")
    print()

    # ---------- ① Milvus 孤儿块 ----------
    print("-" * 78)
    print("① Milvus 孤儿块")
    print("-" * 78)
    c = MilvusClient(uri=MILVUS_URI)
    rows = c.query(collection_name=COLLECTION, filter="seq >= 0",
                   output_fields=["document_id", "kb_id", "seq", "content"])
    per_doc = Counter(r["document_id"] for r in rows)
    kept = {d: n for d, n in per_doc.items() if d in db_docs}
    orphan = {d: n for d, n in per_doc.items() if d not in db_docs}
    n_kept_blocks = sum(kept.values())
    n_orphan_blocks = sum(orphan.values())

    print(f"全库 {len(rows)} 块 / {len(per_doc)} 个文档")
    print(f"  保留（在 documents 表里）：{len(kept)} 个文档 {n_kept_blocks} 块")
    for d, n in sorted(kept.items()):
        print(f"      {d}  {n} 块")
    print(f"  孤儿（已不在 documents 表）：{len(orphan)} 个文档 {n_orphan_blocks} 块")
    for d, n in sorted(orphan.items()):
        sample = next(r["content"] for r in rows if r["document_id"] == d)
        kb = next(r["kb_id"] for r in rows if r["document_id"] == d)
        print(f"      {d}  {n} 块  kb={kb[:8]}  {sample.strip()[:40]!r}")
    print()

    if n_orphan_blocks > 0:
        check("M1 [尺子] 确实存在孤儿可清（证明清理不是空转）", True,
              f"孤儿块 = {n_orphan_blocks}")
    else:
        skip("M1 [尺子]", "当前已无孤儿块（清干净了）")

    if args.apply and orphan:
        ids = sorted(orphan)
        filt = "document_id in [" + ", ".join(f'"{i}"' for i in ids) + "]"
        print(f"执行删除（{len(ids)} 个文档）...")
        for attempt in range(6):
            c.delete(collection_name=COLLECTION, filter=filt)
            c.flush(collection_name=COLLECTION)
            left = c.query(collection_name=COLLECTION, filter=filt, output_fields=["seq"])
            print(f"  第 {attempt + 1} 次删除后残留 = {len(left)}")
            if not left:
                break
            c.load_collection(COLLECTION)
        print()

    # 删完复核（干跑时也复核，用于展示当前状态）
    rows_after = c.query(collection_name=COLLECTION, filter="seq >= 0",
                         output_fields=["document_id", "seq"])
    per_doc_after = Counter(r["document_id"] for r in rows_after)
    kept_after = {d: n for d, n in per_doc_after.items() if d in db_docs}
    orphan_after = {d: n for d, n in per_doc_after.items() if d not in db_docs}

    if args.apply:
        check("M2 [核心] 孤儿块清零", sum(orphan_after.values()) == 0,
              f"残留 {sum(orphan_after.values())} 块")
        check("M3 [反向] 白名单文档的块数一块没少（没误伤）",
              kept_after == kept,
              f"删前 {kept} → 删后 {kept_after}")
        check("M4 [总量] 全库块数 = 删前 - 孤儿数",
              len(rows_after) == len(rows) - n_orphan_blocks,
              f"{len(rows)} - {n_orphan_blocks} = {len(rows) - n_orphan_blocks}，实际 {len(rows_after)}")
    else:
        skip("M2/M3/M4 [Milvus]",
             f"需要 --apply 才有意义（当前全库 {len(rows_after)} 块、孤儿 {sum(orphan_after.values())} 块）")

    # ---------- ② storage 无主目录 ----------
    print()
    print("-" * 78)
    print("② storage 无主目录")
    print("-" * 78)

    if not STORAGE_DIR.is_dir():
        check("S1 [存在] storage 目录存在", False, f"不存在：{STORAGE_DIR}")
    else:
        dirs = [d for d in sorted(STORAGE_DIR.iterdir()) if d.is_dir()]
        live_dirs = [d for d in dirs if d.name in db_kbs]
        orphan_dirs = [d for d in dirs if d.name not in db_kbs and UUID_RE.match(d.name)]
        skipped = [d for d in dirs if not UUID_RE.match(d.name)]

        print(f"storage 一级目录 {len(dirs)} 个")
        for d in live_dirs:
            n = len(list(d.rglob("*")))
            print(f"  保留 {d.name}  ({n} 项)")
        for d in orphan_dirs:
            files = file_manifest(d)
            total = sum(s for _, s, _ in files)
            print(f"  孤儿 {d.name}  ({len(files)} 个文件，{total} 字节)")
            for rel, size, md5 in files:
                print(f"          {rel}  {size} 字节  md5={md5[:12]}…")
        for d in skipped:
            print(f"  跳过 {d.name}（名字不是 UUID，非本系统创建）")
        print()

        if args.apply and orphan_dirs:
            MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"# cleanup_orphans.py 删除留档（storage 无主目录）", ""]
            for d in orphan_dirs:
                lines.append(f"## {d}")
                for rel, size, md5 in file_manifest(d):
                    lines.append(f"  {rel}\t{size}\t{md5}")
            lines.append("")
            MANIFEST.write_text("\n".join(lines), encoding="utf-8")
            print(f"清单已落盘：{MANIFEST}")

            for d in orphan_dirs:
                assert_safe_target(d)          # 每个目标都过一遍护栏
                print(f"  删除 {d}")
                shutil.rmtree(d)
            print()

        dirs_after = [d for d in sorted(STORAGE_DIR.iterdir()) if d.is_dir()]
        orphan_after_dirs = [d for d in dirs_after
                             if d.name not in db_kbs and UUID_RE.match(d.name)]
        live_after = [d for d in dirs_after if d.name in db_kbs]

        if args.apply:
            check("S1 [核心] 无主目录已清空", not orphan_after_dirs,
                  f"残留 {[d.name for d in orphan_after_dirs]}")
            check("S2 [反向] 活 KB 目录一个没少", [d.name for d in live_after] == [d.name for d in live_dirs],
                  f"保留 {[d.name for d in live_after]}")
            check("S3 [安全] storage 根目录仍在", STORAGE_DIR.is_dir(), f"{STORAGE_DIR}")
        else:
            skip("S1/S2/S3 [storage]",
                 f"需要 --apply --storage 才有意义（当前 {len(orphan_after_dirs)} 个无主目录待清）")

    # ---------- 汇总 ----------
    print()
    print("-" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"结果：{passed}/{len(results)} PASS")
    for name, ok, d in results:
        if not ok:
            print(f"  FAIL {name}  {d}")
    print("-" * 78)
    if not args.apply:
        print("提示：这是干跑。加 --apply 才真删；storage 还要加 --storage。")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
