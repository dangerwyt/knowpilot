"""最小复现：LangGraph 会静默丢掉「未在 state schema 里声明的返回键」吗？

背景（T87，见踩坑 #91）：task_queue 的 probe 事件读 `update.get("material_count", 0)`，
事件里却恒显示「可用资料（0 条）」，而 probe() 在同一份数据上进程内直调返回的是 4 条。
两者唯一的差别：`has_material` 在 ResearchState 里声明过，`material_count` 没有。

结论：**会丢，而且不报错、不警告。**

    stream_mode='updates' → {'n1': {'a': 1, 'has_material': True}}
                                 ↑ material_count 与 unknown_key 都消失了

⇒ 给 LangGraph 节点新增返回字段时，必须同时写进 state schema（TypedDict）。
   进程内直调用的是完整 dict（看不出问题），**只有经过 graph 之后才丢**。

用法：backend/.venv/Scripts/python.exe backend/playground/t87_langgraph_key_probe.py
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    a: int
    has_material: bool          # 声明过的键


def n1(state: S) -> dict:
    # 一个声明过的键 + 两个没声明过的键
    return {"a": 1, "has_material": True, "material_count": 7, "unknown_key": "x"}


def main() -> None:
    print("langgraph 版本 =", pkg_version("langgraph"))

    g = StateGraph(S)
    g.add_node("n1", n1)
    g.add_edge(START, "n1")
    g.add_edge("n1", END)
    app = g.compile()

    print("\n=== stream_mode='updates'（task_queue 用的就是这个）===")
    for step in app.stream({"a": 0, "has_material": False}, stream_mode="updates"):
        print(" ", step)

    print("\n=== invoke 拿到的最终 state ===")
    print(" ", app.invoke({"a": 0, "has_material": False}))

    print("\n⇒ 未声明的 material_count / unknown_key 都没了 —— schema 是硬白名单。")


if __name__ == "__main__":
    main()
