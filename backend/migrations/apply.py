"""迁移执行器：把 migrations/ 下的 .sql 喂给容器里的 psql，成功后在 APPLIED.log 记一行。

为什么要有这个脚本（而不是手敲 docker exec）：
  1. 手敲很容易漏 `-v ON_ERROR_STOP=1`。漏了的话，psql 遇到报错**继续往下跑、
     最后返回 0** —— 你会以为迁移成功了，实际只跑了一半，这是最坑的一种失败。
  2. 执行记录自动落到 APPLIED.log，下次知道哪些跑过了。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe migrations/apply.py --list                 # 看执行记录
    ./.venv/Scripts/python.exe migrations/apply.py 0003_t73_citations_fk.sql
    ./.venv/Scripts/python.exe migrations/apply.py 0003_...sql --force    # 重复执行（慎用）

环境变量 KP_PG_CONTAINER 可覆盖容器名（默认 knowpilot-postgres-1）。
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "APPLIED.log"
CONTAINER = os.environ.get("KP_PG_CONTAINER", "knowpilot-postgres-1")
PG_USER, PG_DB = "knowpilot", "knowpilot"


def applied() -> list[str]:
    """返回已执行过的迁移文件名（APPLIED.log 里每行倒数第二个字段是文件名）。"""
    if not LOG.exists():
        return []
    names = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):  # 允许 # 开头的备注行
            continue
        for tok in line.split():
            if tok.endswith(".sql"):       # 取第一个 .sql 结尾的词，后面的备注随便写
                names.append(tok)
                break
    return names


def show_list() -> int:
    done = applied()
    files = sorted(p.name for p in HERE.glob("*.sql"))
    print("迁移文件：")
    for f in files:
        print(f"  [{'已执行' if f in done else '未执行'}] {f}")
    print(f"\n记录文件：{LOG}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv

    if "--list" in sys.argv or not args:
        return show_list()

    name = args[0]
    path = HERE / name
    if not path.exists():
        print(f"找不到迁移文件：{path}")
        return 2

    if name in applied() and not force:
        print(f"{name} 已经执行过（APPLIED.log 有记录）。确实要再跑一次就加 --force。")
        return 3

    print(f"执行 {name} → 容器 {CONTAINER} / 库 {PG_DB}")
    print("-" * 70)
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
         "-v", "ON_ERROR_STOP=1", "-f", "-"],
        stdin=path.open("rb"), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    print((p.stdout or "").rstrip())
    if p.stderr.strip():
        print("--- stderr ---")
        print(p.stderr.rstrip())
    print("-" * 70)

    if p.returncode != 0:
        print(f"失败（exit={p.returncode}），没有写入执行记录。修好 SQL 再重跑。")
        return p.returncode

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{ts}  apply.py  {name}\n")
    print(f"成功。已记入 {LOG.name}。")
    print("下一步：跑 playground/schema_drift.py 确认『模型 vs 现库』0 漂移。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
