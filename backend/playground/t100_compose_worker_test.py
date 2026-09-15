#!/usr/bin/env python
"""T100 验收：docker-compose 里的 worker 必须带 `--pool=solo`。

为什么这是"硬约束"而不是"优化建议"：
  compose 的 worker 原命令没带 `--pool=solo`，容器里默认走 prefork ——
  本机 16 核就是 16 个并发进程，同时消费同一条队列。
  而本项目"同一时刻只有一个任务在跑"是一堆设计的前提
  （T54 部分唯一索引、踩坑 #14、单 worker 强约束），容器里破掉它等于把前提挖了。

判据分四层，每层都能 FAIL：
  A 配置层：`docker-compose config` 解析出的 worker.command 必须含 `--pool=solo`
  B 自检  ：把"改前的命令"和"写错 pool 名的命令"喂进同一个判定函数，必须判 FAIL
            —— 证明 A 组判的不是一个恒真的摆设
  C 一致性：README / 对比文档里关于这件事的旧说法必须已同步
  D 容器层：真起容器。实验组 pool 必须是 solo **且容器里只有 1 个进程**；
            对照组（不带参数）必须是 prefork 且进程数 > 1
            —— 证明这参数真的改变了进程模型，不是写了个没人看的字符串。
            注意别拿启动横幅的 `concurrency: 16 (solo)` 当并发数：那是 Celery 的配置值，
            solo 下照样显示 CPU 核数；**进程数才是硬证据**。

D 组的安全设计（重要）：
  worker 起来时会跑 `_recover_orphans()`，把库里 pending/running 任务改成 interrupted。
  所以容器**只能**连隔离资源：redis db9 + 一个连不上的 DB。
  D0 会在起 worker 之前先只读地探明容器里的实际配置，确认不指向真库真队列才放行；
  探不通就中止 D 组 —— 宁可报错也不冒险改真数据。

用法：
  python playground/t100_compose_worker_test.py             # A/B/C
  python playground/t100_compose_worker_test.py --docker    # 追加 D 组（需先 build 镜像）
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
COMPOSE = ROOT / "docker-compose.yml"

IMAGE = "knowpilot-backend:t100"
NETWORK = "knowpilot_default"
BROKER_DB = 9  # 隔离用 db，绝不碰真队列 db0
BROKER_URL = f"redis://redis:6379/{BROKER_DB}"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# ------------------------------------------------------------------ 判定函数（B 组要复用它）
def judge_pool(command) -> tuple[bool, str]:
    """判定一条命令是不是"celery worker 且带了 --pool=solo"。

    command 可以是 str（compose 里的原样）或 list（config 解析后的形态）。
    """
    parts = list(command) if isinstance(command, list) else shlex.split(command)
    if not parts:
        return False, "命令为空"

    joined = " ".join(parts)
    if "worker" not in parts or "celery" not in joined:
        return False, f"不是 celery worker 命令：{joined[:60]}"

    pools: list[str] = []
    for i, p in enumerate(parts):
        if p.startswith("--pool="):
            pools.append(p.split("=", 1)[1])
        elif p == "--pool" and i + 1 < len(parts):
            pools.append(parts[i + 1])

    if not pools:
        return False, "没有 --pool 参数（容器内会退化成 prefork 多进程）"
    if pools[0] != "solo":
        return False, f"--pool={pools[0]}，不是 solo"
    return True, "--pool=solo"


def compose_cmd(*args: str) -> list[str]:
    if shutil.which("docker-compose"):
        return ["docker-compose", *args]
    return ["docker", "compose", *args]


# ------------------------------------------------------------------ A 组
def load_compose_json() -> dict | None:
    """让 docker 自己解析 compose 文件，返回解析结果（解析不了就是配置有问题）。"""
    r = subprocess.run(
        compose_cmd("-f", str(COMPOSE), "config", "--no-interpolate", "--format", "json"),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"    （docker-compose config 失败）{r.stderr.strip()[:200]}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print(f"    （JSON 解析失败）{e}")
        return None


def group_a(cfg: dict) -> None:
    print("\n=== A 组：compose 配置层 ===")
    services = cfg.get("services", {})
    check("A1", "worker" in services, f"worker 服务存在（共 {len(services)} 个服务）")
    worker = services.get("worker", {})
    backend = services.get("backend", {})

    cmd = worker.get("command", "")
    ok, why = judge_pool(cmd)
    check("A2", ok, f"worker.command：{why}")

    check(
        "A3",
        "app.services.task_queue.celery_app" in json.dumps(cmd),
        "worker 的 -A 指向 app.services.task_queue.celery_app（与 README 一致）",
    )
    ok_b, why_b = judge_pool(backend.get("command", ""))
    check("A4", not ok_b, f"backend（uvicorn）没被误加 --pool —— {why_b}")

    check(
        "A5",
        worker.get("build", {}).get("context", "").endswith("backend")
        or worker.get("build") == "./backend",
        "worker 仍从 ./backend 构建",
    )


# ------------------------------------------------------------------ B 组
def group_b() -> None:
    print("\n=== B 组：判据自检（证明它能 FAIL）===")
    old_cmd = "celery -A app.services.task_queue.celery_app worker --loglevel=info"
    ok, why = judge_pool(old_cmd)
    check("B1", not ok, f"改前的命令被判 FAIL —— {why}")

    ok, why = judge_pool(
        "celery -A app.services.task_queue.celery_app worker --pool=prefork -l info"
    )
    check("B2", not ok, f"--pool=prefork 被判 FAIL —— {why}")

    ok, why = judge_pool("celery -A app.services.task_queue.celery_app worker --pool solo -l info")
    check("B3", ok, "两段式写法 `--pool solo` 也能被正确识别（判定函数不是只认一种写法）")

    ok, why = judge_pool("uvicorn app.main:app --host 0.0.0.0 --port 8000")
    check("B4", not ok, f"uvicorn 命令被判 FAIL（不会把不相关服务当成 worker）—— {why}")


# ------------------------------------------------------------------ C 组
def group_c() -> None:
    print("\n=== C 组：文档说法已同步 ===")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("C1", "--pool=solo" in readme, "README 里本机启动 worker 的命令仍带 --pool=solo")

    stale = "没带 `--pool=solo`"
    check("C2", stale not in readme, f"README 不再说「compose 里的 worker {stale}」")

    doc = ROOT / "docs" / "能力对比与功能演进建议.md"
    text = doc.read_text(encoding="utf-8")
    check("C3", "未改，待定" not in text, "对比文档里这件事不再标「未改，待定」")

    pitfalls = (ROOT / "docs" / "踩坑记录.md").read_text(encoding="utf-8")
    check("C4", "### 14." in pitfalls and "--pool=solo" in pitfalls, "踩坑记录里 --pool=solo 的出处仍在")


# ------------------------------------------------------------------ D 组
def run_worker(name: str, extra: list[str], wait: int = 12) -> tuple[str, str | None]:
    """起一个容器 worker（连 db9），返回 (日志, 错误)。调用方负责最终清理。"""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    r = subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "--network", NETWORK,
            "-e", f"REDIS_URL={BROKER_URL}",
            IMAGE,
            "celery", "-A", "app.services.task_queue.celery_app", "worker",
            *extra, "--loglevel=info",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return "", r.stderr.strip()[:300]
    time.sleep(wait)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return logs.stdout + logs.stderr, None


def parse_banner(logs: str) -> tuple[int | None, str | None]:
    m = re.search(r"concurrency:\s*(\d+)\s*\((\w+)\)", logs)
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def count_procs(name: str) -> tuple[int, list[str]]:
    """数容器里在跑的进程个数（去掉表头）。

    这是"到底是不是单进程"的**硬证据**：横幅里的 concurrency 只是 Celery 的配置值
    （solo 下照样显示 CPU 核数 16），不能拿它判断实际并发。进程数骗不了人：
      solo    → 1 个进程
      prefork → 1 master + N 个 worker 子进程
    """
    r = subprocess.run(["docker", "top", name], capture_output=True, text=True)
    lines = [ln for ln in r.stdout.strip().splitlines()[1:] if ln.strip()]
    return len(lines), lines


def probe_container_config() -> tuple[str, str, str | None]:
    """起 worker 之前先探明容器里的实际配置——这是"别误伤真数据"的前置闸门。

    为什么必须有这道闸门：worker 起来时会跑 `_recover_orphans()`（task_queue.py:45-47），
    它把库里的 pending/running 任务改成 interrupted、parsing/embedding 文档改成 failed。
    容器**绝不能**连上真库，否则等于凭空改用户数据。
    所以先只读地打印出容器里 settings 的真实取值，确认无误再放行。

    返回 (database_url, redis_url, 错误)。
    """
    r = subprocess.run(
        [
            "docker", "run", "--rm", "--network", NETWORK,
            "-e", f"REDIS_URL={BROKER_URL}",
            IMAGE,
            "python", "-c",
            "from app.core.config import settings as s;"
            "print('DB=' + s.database_url);print('REDIS=' + s.redis_url)",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return "", "", (r.stderr or r.stdout).strip().splitlines()[-1][:200] if (r.stderr or r.stdout) else "探针失败"
    db = re.search(r"DB=(\S+)", r.stdout)
    rd = re.search(r"REDIS=(\S+)", r.stdout)
    if not db or not rd:
        return "", "", f"探针输出无法解析：{r.stdout.strip()[:150]}"
    return db.group(1), rd.group(1), None


def group_d() -> None:
    print("\n=== D 组：容器层实测（真起 worker 看进程模型）===")

    # D0 前置闸门：容器不能碰真库 / 真队列。不通过就直接不跑，宁可报错也不冒险。
    db_url, redis_url, err = probe_container_config()
    if err:
        check("D0", False, f"配置探针跑不起来（镜像没 build？）{err}")
        return
    safe_broker = redis_url.endswith(f"/{BROKER_DB}")
    safe_db = "postgres:5432" not in db_url  # 真库在 compose 网络里叫 postgres
    check(
        "D0",
        safe_broker and safe_db,
        f"容器连的是 DB={db_url} / REDIS={redis_url}"
        "（都不是真库真队列，worker 启动时的孤儿回收伤不到真数据）",
    )
    if not (safe_broker and safe_db):
        print("    ⚠️ 容器可能连上真资源，中止 D 组避免改到真实数据")
        return

    solo_name, ctrl_name = "kp-t100-solo", "kp-t100-prefork"
    try:
        logs, err = run_worker(solo_name, ["--pool=solo"])
        if err:
            check("D0", False, f"容器起不来（镜像没 build？）{err}")
            return

        # 安全闸：连的不是 db9 就立刻停手，绝不能让它接到真队列上
        m = re.search(r"transport:\s*(\S+)", logs)
        transport = m.group(1) if m else "<未打印>"
        safe = transport.endswith(f"/{BROKER_DB}")
        check("D1", safe, f"容器连的是 {transport}（隔离 db，没碰真队列）")
        if not safe:
            print("    ⚠️ 未确认隔离，中止 D 组，避免和本机 worker 抢真消息")
            return

        conc, pool = parse_banner(logs)
        check(
            "D2",
            pool == "solo",
            f"实验组启动横幅的 pool 是 solo（横幅里 concurrency={conc} 是 Celery 的**配置值**，"
            "solo 下照样显示 CPU 核数，拿它当并发数会误判）",
        )

        n_solo, rows = count_procs(solo_name)
        check("D3", n_solo == 1, f"实验组容器里只有 {n_solo} 个进程 —— solo 的硬证据")
        if n_solo != 1:
            for r in rows[:3]:
                print(f"        {r[:110]}")

        check("D4", "ready" in logs, "实验组日志出现 ready（命令合法、worker 真起来了）")
        noisy = [k for k in ("unrecognized arguments", "no such option", "invalid choice") if k in logs]
        check("D5", not noisy, f"日志里没有参数解析错误（{noisy or '无'}）")

        logs2, err2 = run_worker(ctrl_name, [])
        if err2:
            check("D6", False, f"对照组容器起不来：{err2}")
            return
        conc2, pool2 = parse_banner(logs2)
        n_prefork, _ = count_procs(ctrl_name)
        check(
            "D6",
            pool2 == "prefork" and n_prefork > 1,
            f"对照组（不带 --pool）：pool={pool2}、进程数 {n_prefork}"
            " —— 与实验组明显不同，证明这个参数真的改变了进程模型",
        )
    finally:
        for n in (solo_name, ctrl_name):
            subprocess.run(["docker", "rm", "-f", n], capture_output=True)
        print("    （已清理两个容器）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true", help="追加 D 组：真起容器实测")
    args = ap.parse_args()

    print(f"T100 验收：compose worker 的 --pool=solo\ncompose 文件：{COMPOSE}")
    cfg = load_compose_json()
    if cfg is None:
        check("A0", False, "docker-compose config 解析失败——先修配置，后面判据无意义")
    else:
        check("A0", True, "docker-compose config 解析成功")
        group_a(cfg)
    group_b()
    group_c()
    if args.docker:
        group_d()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 56}\n{passed}/{len(results)} PASS")
    if passed != len(results):
        print("FAIL 明细：")
        for n, ok, d in results:
            if not ok:
                print(f"  {n} — {d}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
