"""t116：自助注册开关（settings.allow_registration）验收。

改动内容：线上可通过环境变量 ALLOW_REGISTRATION=false 关闭 POST /auth/register。
本脚本验证四件事 —— 每条都问过"什么情况会红"：

  R1 config.py 有 allow_registration，且**默认必须是 True**
     （若默认 False ⇒ 本地开发与 playground 的前端测试脚本 t88/t89a/t89b 全部失效）
  R2 环境变量原文 "false" 能被解析成 **布尔 False**
     （最容易错的一环：pydantic 若把它当字符串，`if not "false"` 恒为假 ⇒ 开关形同虚设）
  R3 403 分支必须在**查库之前**（否则关闭时仍需连库，无 DB 环境下会 500 而非 403）
  R4 真实请求验证两个方向：
     关 → POST /auth/register 返回 **403**
     开 → **不是 403**（不误伤正常注册路径）

用法：
    python playground/t116_register_switch_check.py
    python playground/t116_register_switch_check.py --selftest   # 只验判定函数自身的红绿
"""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}" + (f"  -- {detail}" if detail else ""))


# ─────────────────────────── 判定函数（纯函数，可喂构造数据） ───────────────────────────
def verdict_default_true(value) -> bool:
    """R1：默认值必须严格为 True（不是 1、不是 'true'、不是 None）。"""
    return value is True


def verdict_env_false(parsed, raw: str) -> bool:
    """R2：原文是 false 系写法时，解析结果必须是布尔 False。

    必须同时满足两条：① 解析出 False；② 原文确实表达"假"。
    只看 ① 会漏掉"原文是 true 但解析成 False"这种反向错。
    """
    falsy_text = ("false", "0", "no", "off")
    return parsed is False and raw.strip().lower() in falsy_text


def verdict_order(src: str) -> bool:
    """R3：403 判定的位置必须早于第一次 db.scalar（且两者都存在）。"""
    pos_switch = src.find("allow_registration")
    pos_db = src.find("db.scalar")
    return pos_switch != -1 and pos_db != -1 and pos_switch < pos_db


# ─────────────────────────── 自检：判定函数本身能红能绿 ───────────────────────────
def run_selftest() -> int:
    print("=== 自检：判定函数在构造数据上的红绿表现 ===")
    cases = [
        # (说明, 实际值, 期望的判定结果)
        ("R1 True  -> 绿", verdict_default_true(True), True),
        ("R1 False -> 红", verdict_default_true(False), False),
        ("R1 None  -> 红", verdict_default_true(None), False),
        ("R1 'true'字符串 -> 红", verdict_default_true("true"), False),
        ("R2 false/False -> 绿", verdict_env_false(False, "false"), True),
        ("R2 FALSE 大写 -> 绿", verdict_env_false(False, "FALSE"), True),
        ("R2 未转换的字符串'False' -> 红", verdict_env_false("False", "false"), False),
        ("R2 解析成了 True -> 红", verdict_env_false(True, "false"), False),
        ("R2 原文 true 却解析 False -> 红", verdict_env_false(False, "true"), False),
        ("R3 开关在查库前 -> 绿",
         verdict_order("if not s.allow_registration:\n    ...\nawait db.scalar(x)"), True),
        ("R3 开关在查库后 -> 红",
         verdict_order("await db.scalar(x)\nallow_registration"), False),
        ("R3 缺开关 -> 红", verdict_order("await db.scalar(x)"), False),
        ("R3 缺查库 -> 红", verdict_order("if not s.allow_registration:"), False),
    ]
    bad = 0
    for desc, got, expect in cases:
        ok = bool(got) == expect
        if not ok:
            bad += 1
        print(f"  [{'ok' if ok else 'BAD'}] {desc}   (期望 {expect}, 实得 {bool(got)})")
    print()
    if bad:
        print(f"[SELFTEST FAIL] {bad}/{len(cases)} 条判定函数行为与预期不符")
        return 1
    print(f"[SELFTEST OK] {len(cases)}/{len(cases)} 条判定函数红绿方向正确")
    return 0


# ─────────────────────────── 主流程 ───────────────────────────
def main() -> int:
    print("=== t116：自助注册开关验收 ===")
    print()

    # ── R1：默认值 ──────────────────────────────────────────────
    from app.core.config import Settings, settings

    default_val = Settings.model_fields["allow_registration"].default
    rec("R1 config.py 存在 allow_registration 字段", "allow_registration" in Settings.model_fields)
    rec("R1 默认值为 True（本地/测试脚本不受影响）",
        verdict_default_true(default_val),
        f"实际默认值 = {default_val!r}")
    rec("R1 当前进程内生效值（应受本地 .env 影响）",
        isinstance(settings.allow_registration, bool),
        f"settings.allow_registration = {settings.allow_registration!r}")

    # ── R2：环境变量字符串 → 布尔 ────────────────────────────────
    # 重建一个"不读 .env"的实例，隔离出环境变量这一条路径
    os.environ["ALLOW_REGISTRATION"] = "false"
    try:
        isolated = Settings(_env_file=None)
        parsed = isolated.allow_registration
    finally:
        os.environ.pop("ALLOW_REGISTRATION", None)
    rec("R2 环境变量 'false' 被解析成布尔 False（不是字符串）",
        verdict_env_false(parsed, "false"),
        f"解析结果 = {parsed!r} (type={type(parsed).__name__})")

    os.environ["ALLOW_REGISTRATION"] = "true"
    try:
        parsed_true = Settings(_env_file=None).allow_registration
    finally:
        os.environ.pop("ALLOW_REGISTRATION", None)
    rec("R2 反向对照：'true' 被解析成 True", parsed_true is True, f"解析结果 = {parsed_true!r}")

    # ── R3：源码里的顺序 ────────────────────────────────────────
    src = (BACKEND / "app" / "api" / "v1" / "endpoints" / "auth.py").read_text(encoding="utf-8")
    rec("R3 403 分支早于查库（关闭时不依赖 DB）", verdict_order(src))

    # ── R4：真实请求的两个方向 ──────────────────────────────────
    try:
        from fastapi.testclient import TestClient
        from app.core.db import get_db
        from app.main import app as fastapi_app
    except Exception as e:  # noqa: BLE001
        rec("R4 导入 TestClient / app 成功", False, f"{type(e).__name__}: {e}")
        return summarize()

    # 假 DB：绝不碰真库，但要把 register() 用到的每个方法都补齐 ——
    # 否则"开关开启"那一路只能跑到 AttributeError(500)，
    # 判据就分不清「开关生效」和「请求根本没走进函数体」（第一版正是栽在这里）。
    from unittest.mock import AsyncMock, MagicMock

    fake_db = MagicMock()
    fake_db.scalar = AsyncMock(return_value=None)   # 邮箱不存在 ⇒ 不抛 409
    fake_db.flush = AsyncMock()
    fake_db.commit = AsyncMock()
    fake_db.add = MagicMock()

    async def _fake_refresh(obj):
        # User.id = UUID(as_uuid=False) ⇒ 字符串；create_token 直接把它塞进 JWT 的 sub
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid.uuid4())

    fake_db.refresh = _fake_refresh

    async def _fake_db_dep():
        yield fake_db

    fastapi_app.dependency_overrides[get_db] = _fake_db_dep
    client = TestClient(fastapi_app, raise_server_exceptions=False)

    # ⚠️ 邮箱必须用 EmailStr 认可的域名：`.invalid` / `.test` 这类保留 TLD 会被 422 拦在
    # 函数体之前，导致两个方向都返回同一个码、开关根本没被测到（第一版判据失效的根因）。
    body = {"email": "t116_probe@example.com", "password": "not-a-real-secret-x9", "name": "t116"}

    original = settings.allow_registration
    try:
        # 方向 1：关闭 ⇒ 必须 403
        settings.allow_registration = False
        r_off = client.post("/api/v1/auth/register", json=body)
        rec("R4 开关关闭 ⇒ 403", r_off.status_code == 403,
            f"实际 {r_off.status_code}  body={r_off.text[:70]}")

        # 方向 2：开启 ⇒ 必须能走完整个注册逻辑（201）。只断言"不是 403"太弱 ——
        # 那样 500/422 都会算过，无法证明请求真的越过了开关。
        settings.allow_registration = True
        r_on = client.post("/api/v1/auth/register", json=body)
        rec("R4 开关开启 ⇒ 201 完整走通（假 DB，未落库）", r_on.status_code == 201,
            f"实际 {r_on.status_code}  body={r_on.text[:70]}")

        # 方向 3：请求体校验早于开关（说明 403 不是万能兜底，非法入参仍走 422）
        r_bad = client.post("/api/v1/auth/register",
                            json={"email": "not-an-email", "password": "x"})
        rec("R4 非法请求体 ⇒ 422（body 校验在开关之前）", r_bad.status_code == 422,
            f"实际 {r_bad.status_code}")
    finally:
        settings.allow_registration = original
        fastapi_app.dependency_overrides.pop(get_db, None)

    # 反向：关掉开关后，已有账号的登录路径不该受影响（登录接口不读这个开关）
    login_src = src[src.find("def login"):] if "def login" in src else ""
    rec("R4 登录接口不含该开关（关注册不影响登录）", "allow_registration" not in login_src)

    return summarize()


def summarize() -> int:
    print()
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"===== {passed}/{total} PASS =====")
    if failed:
        print("未通过：")
        for n in failed:
            print(f"  - {n}")
        return 1
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    raise SystemExit(main())
