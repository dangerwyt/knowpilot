from typing import Any
from app.core.config import settings
from app.services.agent.state import ResearchState
from langgraph.graph import StateGraph, START, END
from app.services.agent.nodes import probe, planner, synthesizer, retriever, critic

MAX_RETRIES = 2
PASS_SCORE = settings.quality_pass_score  # 质检通过线（LLM 报告客观可达的务实阈值）



def route_after_critic(state) -> str:
    if state.get("reviewed") is False:
        return "end"

    if state.get("score", 0) >= PASS_SCORE or state.get("retries", 0) >= MAX_RETRIES:
        return "end"

    return "rewrite"


def build_graph() -> Any:
    """构建 LangGraph StateGraph 并编译。M2 填充节点实现与条件边。"""

    graph = StateGraph(ResearchState)  # 1. 声明状态类型
    graph.add_node('probe', probe)  # 2.资料预检摘要
    graph.add_node("planner", planner)  # 3. 注册节点（名字 + 函数）
    graph.add_node("retriever", retriever)  # 4. 知识库检索
    graph.add_node("synthesizer", synthesizer)  # 5. 报告撰写
    graph.add_node("critic", critic)  # 6. 质检

    graph.add_edge(START, "probe")  # 1. 连边：起点 → probe
    graph.add_edge('probe', "planner")  # 2. 连边：probe → planner
    graph.add_edge("planner", "retriever")  # 3. 连边：planner → retriever
    graph.add_edge("retriever", "synthesizer")  # 4. 连边：retriever → synthesizer
    graph.add_edge("synthesizer", "critic")  # 5. 连边：synthesizer → critic
    # 6. 连边：critic → rewrite or end
    graph.add_conditional_edges("critic", route_after_critic, {"rewrite": "synthesizer", "end": END})
    return graph.compile()


async def run_research(task_id: str) -> None:
    """由 Celery 调用。M2 实现。"""
    raise NotImplementedError("M2 里程碑实现：run_research")
