"""核对：Celery worker 进程是不是在代码改动之后才启动的？

为什么不能只看「有没有比代码新的 python 进程」
------------------------------------------------
2026-09-15 实测踩到：本机 WorkBuddy 沙箱自己会拉起
`python -c "from multiprocessing.spawn import ..."` 之类的辅助进程，
它的启动时间几乎总是晚于代码修改。旧判据只筛「进程名以 python 开头」，
于是报出「已重启 worker」的**假 PASS** —— 而真正的 worker
（PID 20884，15:50:03）其实比代码（16:08:39）旧，线上跑的是旧逻辑。
（当时是靠真任务的事件流里还带着旧文案才发现的：行为差异比进程时间更可靠。）

修法：判据只认**命令行确实是 celery worker** 的进程（celery + task_queue）；
拿不到命令行、或压根没有 worker 进程时，一律报「无法判定」，绝不退化成通过。

用法（在本目录下运行）
--------------------
    python check_worker_start.py                # 默认比对 app/services/task_queue.py
    python check_worker_start.py <源文件路径>     # 比对该文件的 mtime
    python check_worker_start.py --selftest      # 判据自检（伪造数据，不碰真实进程）

退出码：0 = worker 比代码新；1 = 旧代码/无法判定（都不该当成通过）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = BACKEND / "app/services/task_queue.py"

# 命令行里同时出现这两个词，才认作本项目的 celery worker
WORKER_MARKERS = ("celery", "task_queue")

PS_QUERY = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Select-Object ProcessId,CommandLine,"
    "@{n='Started';e={$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')}} | "
    "ConvertTo-Json -Compress"
)


def list_python_procs():
    """返回 [{'pid','cmd','started'}]；拿不到就返回 None（调用方必须当成「无法判定」）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90,
        )
    except Exception:
        return None
    raw = (out.stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if isinstance(data, dict):
        data = [data]
    return [
        {"pid": d.get("ProcessId"), "cmd": d.get("CommandLine") or "",
         "started": d.get("Started") or ""}
        for d in data
    ]


def is_worker(cmd: str) -> bool:
    c = (cmd or "").lower()
    return all(m in c for m in WORKER_MARKERS)


def _ts(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def judge(procs, code_mtime: datetime):
    """纯函数，便于自检。返回 (verdict, lines)。

    verdict:
      'ok'      worker 最新启动时间 > 代码 mtime ⇒ 跑的是新代码
      'stale'   worker 启动时间 <= 代码 mtime    ⇒ 跑的是旧代码，必须重启
      'unknown' 拿不到命令行 / 没有 worker 进程  ⇒ 无法判定（**不是通过**）
    """
    if procs is None:
        return "unknown", ["拿不到进程清单（PowerShell 查询失败）⇒ 无法判定"]

    workers = [p for p in procs if is_worker(p["cmd"])]
    if not workers:
        return "unknown", [
            "没找到 celery worker 进程（命令行需同时含 celery 和 task_queue）",
            "⇒ 无法判定 worker 新旧（worker 可能没在跑，也可能读不到命令行）",
        ]

    lines, parsed = [], []
    for p in workers:
        lines.append(f"  PID {p['pid']}  启动于 {p['started']}  {(p['cmd'] or '')[:90]}")
        t = _ts(p["started"])
        if t:
            parsed.append((t, p))
    if not parsed:
        return "unknown", lines + ["worker 进程的启动时间读不出来 ⇒ 无法判定"]

    newest_t, _ = max(parsed, key=lambda x: x[0])
    if newest_t > code_mtime:
        lines.append(f"⇒ worker 最新一次启动 {newest_t:%H:%M:%S} 晚于代码修改 "
                     f"{code_mtime:%H:%M:%S}：跑的是新代码")
        return "ok", lines
    lines.append(f"⇒ worker 启动 {newest_t:%H:%M:%S} 不晚于代码修改 "
                 f"{code_mtime:%H:%M:%S}：**跑的是旧代码，改完要重启 worker**")
    return "stale", lines


def selftest() -> int:
    """用伪造数据验判据本身：用例 1/3 专挑「旧判据会假 PASS」的场景。"""
    code = datetime(2026, 9, 15, 16, 8, 39)

    def p(pid, cmd, started):
        return {"pid": pid, "cmd": cmd, "started": started}

    WORKER = (r'"C:\x\.venv\Scripts\python.exe" -m celery -A '
              r'app.services.task_queue.celery_app worker --pool=solo')
    SANDBOX = (r'"C:\x\.workbuddy\binaries\python\versions\3.13.12\python.exe" '
               r'"-c" "from multiprocessing.spawn import spawn_main"')

    cases = [
        ("本次真实情况：真 worker（15:50:03）比代码旧，同时有个比代码新的沙箱辅助进程",
         [p(20884, WORKER, "2026-09-15 15:50:03"),
          p(8636, SANDBOX, "2026-09-15 16:28:26")],
         "stale"),
        ("worker 确实重启过（比代码新）",
         [p(20884, WORKER, "2026-09-15 16:30:00")],
         "ok"),
        ("只有无关 python 进程、没有 worker（旧判据在这里必然假 PASS）",
         [p(8636, SANDBOX, "2026-09-15 16:28:26")],
         "unknown"),
        ("拿不到进程清单", None, "unknown"),
    ]

    print("=" * 74)
    print("判据自检：伪造数据，不碰真实进程")
    print("=" * 74)
    bad = 0
    for i, (name, procs, want) in enumerate(cases, 1):
        got, lines = judge(procs, code)
        ok = got == want
        bad += 0 if ok else 1
        print(f"\n[{'PASS' if ok else 'FAIL'}] 用例 {i}：{name}")
        print(f"        期望 {want}，实际 {got}")
        for ln in lines:
            print(f"        {ln}")
    print(f"\n  自检{'全过' if not bad else f'有 {bad} 条不符'}；"
          f"用例 1 是真实数据、用例 3 是旧判据的漏洞场景。")
    return 0 if not bad else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_TARGET
    if not target.is_absolute():
        target = (BACKEND / target).resolve()
    if not target.exists():
        print(f"找不到要比对的源文件：{target}")
        return 2

    code_mtime = datetime.fromtimestamp(target.stat().st_mtime)
    print(f"比对的源文件：{target}")
    print(f"  最后修改：{code_mtime:%Y-%m-%d %H:%M:%S}")
    print(f"  当前时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n")

    procs = list_python_procs()
    print(f"本机 python 进程数：{'未知' if procs is None else len(procs)}")
    print("celery worker 候选：")
    verdict, lines = judge(procs, code_mtime)
    for ln in lines:
        print(ln)

    print()
    print({
        "ok": "结论：PASS —— worker 比代码新，跑的是新代码",
        "stale": "结论：FAIL —— worker 跑的是旧代码，请重启 Celery worker 后再验部署层",
        "unknown": "结论：无法判定（**不要当成通过**）—— 确认 worker 是否在跑、命令行能否读到",
    }[verdict])
    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
