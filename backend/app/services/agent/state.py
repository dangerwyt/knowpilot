from typing import NotRequired, TypedDict


class ResearchState(TypedDict):
    task_id: NotRequired[str]  # 运行上下文（非调研状态）：供节点内推 SSE 进度
    objective: str  # 调研目标
    kb_overview: str  # 资料预检摘要（prob 产出）
    plan: list[str]  # 章节计划（planner 产出）
    kb_ids: list[str]  # 要检索的知识库
    evidence: list[dict]  # 检索证据（M3 用，先占位）
    evidence_used: list[dict]
    ready_doc_ids: list[str]  # 该任务可检索的文档白名单（PG 真相源）
    ready_doc_epochs: dict[str, int]  # 每个文档当前代次，与 ready_doc_ids 同源
    draft: dict  # 章节树（synthesizer 产出）
    report_id: str | None  # 落库后的报告 id
    status: str  # running / completed / failed
    reviewed: bool  # 是否执行过质检（False = 评审服务降级跳过）
    passed: bool | None  # 质检是否通过
    score: int | None  # 质检分数
    retries: int  # 质检回退次数
    feedback: str | None  # 人工干预指令
    issues: list[str]  # 质检问题列表
    focus: list[str]  # 每章写作侧重
