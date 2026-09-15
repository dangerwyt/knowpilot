"""结构漂移体检：把「模型声明的表结构」和「现库里真实的结构」逐列比一遍。

为什么要比：本项目建表靠 `init_db()` 的 `create_all`，而 `create_all`
**不会改已存在的表** —— 所以「模型」和「现库」是两套独立演进的结构，
只能手工 SQL 同步（T54 加索引 / T72 加列 / T73 改类型都是这么来的）。
两者一旦不一致，就会出现「新环境建出来的库」和「你正在用的库」不一样。

用法（backend/ 目录下）：
    ./.venv/Scripts/python.exe playground/schema_drift.py
退出码：0 = 无漂移，1 = 有漂移（可当验收脚本用）。

已知的两类「假阳性」（已在代码里排除，改这个脚本时别踩回去）：
  1. `unique=True` 生成的 UNIQUE 约束 → 反射时多出一个同名影子索引
     （靠反射结果里的 `duplicates_constraint` 字段识别）。
  2. 整型自增主键：模型的 `autoincrement` 在 PG 里就是 `nextval(...)`，
     而模型侧 `server_default` 是 None —— 同一件事的两种写法。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from app.core.db import engine
from app.models import Base

# engine 开了 echo：会把每条 SQL 打到 stdout，让输出没法看。
# 注意设 logger level 没用（echo 是设在子 logger `sqlalchemy.engine.Engine` 上的），
# 直接关 engine 自己的开关。
engine.echo = False

D = postgresql.dialect()


def col_type(c) -> str:
    """两边都用同一个 dialect 编译，字符串才能直接比。

    模型侧拿到的是 `Column` 对象（用 `.type`），反射侧拿到的是 dict（用 `["type"]`）。
    """
    t = c["type"] if isinstance(c, dict) else c.type
    return str(t.compile(dialect=D)).upper()


def default_of(v) -> str:
    """把 server_default 归一化成字符串（None -> '-'）。"""
    if v is None:
        return "-"
    s = str(getattr(v, "arg", v))
    return s.strip()


async def live_schema() -> dict:
    async with engine.connect() as conn:
        def load(sync_conn):
            insp = inspect(sync_conn)
            out = {}
            for t in insp.get_table_names():
                out[t] = {
                    "cols": {c["name"]: c for c in insp.get_columns(t)},
                    # duplicates_constraint 有值的索引 = UNIQUE 约束自动生成的影子索引
                    # （模型里写 unique=True 就会生成），不算漂移，排除掉
                    "indexes": {i["name"] for i in insp.get_indexes(t)
                                if not i.get("duplicates_constraint")},
                    "fks": {fk["name"]: fk for fk in insp.get_foreign_keys(t)},
                }
            return out
        return await conn.run_sync(load)


def model_schema() -> dict:
    out = {}
    for t in Base.metadata.sorted_tables:
        out[t.name] = {
            "cols": {c.name: c for c in t.columns},
            "indexes": {i.name for i in t.indexes},
            "fks": {fk.name or f"{t.name}_{list(fk.columns)[0].name}_fkey"
                    for fk in t.foreign_key_constraints},
        }
    return out


async def main() -> int:
    live = await live_schema()
    model = model_schema()

    print("=" * 78)
    print("结构漂移体检：模型 vs 现库")
    print("=" * 78)

    only_model = sorted(set(model) - set(live))
    only_live = sorted(set(live) - set(model))
    if only_model:
        print(f"\n[表] 模型有、现库没有：{only_model}")
    if only_live:
        print(f"\n[表] 现库有、模型没有：{only_live}")

    issues = 0
    issues += len(only_model) + len(only_live)
    for tname in sorted(set(model) & set(live)):
        m, l = model[tname], live[tname]
        lines = []

        for cname in sorted(set(m["cols"]) | set(l["cols"])):
            if cname not in m["cols"]:
                lines.append(f"    列 {cname}: 现库有、模型没有（模型里已删？）")
                continue
            if cname not in l["cols"]:
                lines.append(f"    列 {cname}: 模型有、现库没有（缺迁移 SQL？）")
                continue
            mc, lc = m["cols"][cname], l["cols"][cname]
            mt, lt = col_type(mc), col_type(lc)
            md, ld = default_of(mc.server_default), default_of(lc.get("default"))
            if mt != lt:
                lines.append(f"    列 {cname}: 类型 模型={mt} 现库={lt}")
            if mc.nullable != lc["nullable"]:
                lines.append(f"    列 {cname}: nullable 模型={mc.nullable} 现库={lc['nullable']}")
            # 整型主键：模型的 autoincrement 在 PG 里就是 nextval(...)，而模型侧
            # server_default 是 None ——「同一件事的两种写法」，不是漂移。
            serial_pk = mc.primary_key and md == "-" and ld.startswith("nextval(")
            if md != ld and not serial_pk:
                lines.append(f"    列 {cname}: 默认值 模型={md} 现库={ld}")

        miss_ix = m["indexes"] - l["indexes"]
        extra_ix = l["indexes"] - m["indexes"] - {f"{tname}_pkey"}
        if miss_ix:
            lines.append(f"    索引 模型有、现库没有：{sorted(miss_ix)}")
        if extra_ix:
            lines.append(f"    索引 现库有、模型没有：{sorted(extra_ix)}（从零建库会缺！）")

        miss_fk = m["fks"] - set(l["fks"])
        if miss_fk:
            lines.append(f"    外键 模型有、现库没有：{sorted(miss_fk)}")

        if lines:
            issues += len(lines)
            print(f"\n  表 {tname}：")
            for x in lines:
                print(x)

    print()
    print("=" * 78)
    if issues:
        print(f"汇总：发现 {issues} 处漂移（表 {len(model)} 张）")
    else:
        print(f"汇总：无漂移，模型与现库一致（表 {len(model)} 张）")
    print("提示：漂移意味着「从零新建的库」和「你正在用的库」不一样。")
    print("=" * 78)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
