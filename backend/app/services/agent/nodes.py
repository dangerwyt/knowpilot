from pydantic import BaseModel, model_validator
from app.core.config import settings
from app.services.agent.state import ResearchState
from langchain_core.prompts import ChatPromptTemplate
from app.core.redis import publish_task_event
from app.services.agent import generate_model, generate_review_model
import asyncio


class PlanState(BaseModel):
    plan: list[str]
    focus: list[str] = []

    @model_validator(mode="after")
    def fill_focus(self):
        # 容错：focus 缺失或不等长时，自动用 plan 标题填充，避免中断流程
        if len(self.focus) != len(self.plan):
            self.focus = [f"本章介绍：{p}" for p in self.plan]
        return self


class SectionContent(BaseModel):
    title: str
    content: str


class ReviewResult(BaseModel):
    score: int
    issues: list[str]
    feedback: str


model = generate_model()

review_model = generate_review_model()

review_template = ChatPromptTemplate.from_messages([
    ("system", "你是严格的调研报告评审专家。评估报告质量，从四个维度客观打分："
               "1) 完整度：所有规划章节都已覆盖，无缺失；"
               "2) 相关度：内容紧扣调研目标，无跑题；"
               "3) 事实性：关键论断是否基于检索证据（照资料写而非凭空捏造）；"
               "4) 结构规范：章节层次清晰、格式正确。"
               "打分规则：满分 100，按维度客观评分并合计。同时给出问题清单（具体到章节）和修改意见。"
               "评分校准（重要）：作者只能基于检索资料写作。若资料本身未覆盖某信息，"
               "报告如实说明'资料未覆盖'属正确行为，不应因此扣分；"
               "仅在资料已提供却未引用、或编造资料中不存在的内容时才判为事实性问题。"
               "章节间内容重复若源于可用资料有限，视为轻度问题，不作严重扣分。"),
    ("human", "调研目标：{objective}\n\n报告：\n{draft}"),
])


def critic(state: ResearchState) -> dict:
    """评审报告质量：通过则结束，不通过则带 feedback 回重写。"""
    objective = state["objective"]
    draft = state.get("draft", {})
    text = "\n\n".join(f"## {s['title']}\n{s['content']}" for s in draft.get("sections", []))

    chain = review_template | review_model.with_structured_output(ReviewResult)

    response = None
    for attempt in range(2):
        try:
            response = chain.invoke({"objective": objective, "draft": text})
            if response is not None:
                break

            print(f"[critic] 第 {attempt + 1} 次未得到有效结构化结果")

        except Exception as e:
            print(f"[critic] 第 {attempt + 1} 次评审调用异常：{e}")

    if response is None:
        print("[critic] 评审服务暂不可用，降级跳过本次质检")

        return {
            "reviewed": False,
            "passed": None,
            "score": None,
            "feedback": "",
            "issues": ["本次报告已生成，质检服务暂不可用，未执行自动评审。"],
            "retries": state.get("retries", 0) + 1,
        }

    print(f"[critic] score={response.score} issues={response.issues}")

    return {
        "reviewed": True,
        "passed": response.score >= settings.quality_pass_score,
        "score": response.score,
        "feedback": response.feedback,
        "retries": state.get("retries", 0) + 1,
        "issues": response.issues,
    }


def probe(state: ResearchState) -> dict:
    """planner 前的资料预检：用调研目标粗检知识库，供拆章参考。"""
    kb_ids = state.get("kb_ids") or []
    objective = state["objective"]
    if not kb_ids:
        return {"kb_overview": "（未关联知识库，仅凭模型知识拆章与撰写）"}

    try:
        from app.services.rag.milvus_client import search
        from app.services.rag import get_embedder

        embedder = get_embedder()
        vector = embedder.embed_query(objective)
        hits = search(vector, kb_ids, top_k=5, document_ids=state.get("ready_doc_ids"),
                      epochs=state.get("ready_doc_epochs"))

        # Milvus 只按 kb_id 过滤，没有相似度阈值 —— 资料完全不相关时也会返回 top_k 条噪声
        hits = [h for h in hits if (h.get("distance") or 0) >= settings.probe_min_score]

        if not hits:
            return {"kb_overview": "（知识库未检索到相关内容，仅凭模型知识拆章与撰写）", "has_material": False, "material_count": 0}

        overview = "\n---\n".join(h["content"][:200] for h in hits)
        return {"kb_overview": f"知识库可用资料（{len(hits)} 条相关片段，节选）：\n{overview}", "has_material": True, "material_count": len(hits)}

    except Exception as e:
        return {"kb_overview": f"（知识库检索失败：{str(e)}，仅凭模型知识拆章与撰写）"}


def planner(state: ResearchState) -> dict:
    template = ChatPromptTemplate.from_messages([
        ("system", "你是一个竞品分析师。把给定调研主题拆解成 3-5 个报告章节标题。"
                   "拆解原则：章节规划应贴合可用资料的范围——资料覆盖充分的方面可深入拆解，"
                   "资料明显未覆盖的方面不要硬拆（否则正文无据可写）。"
                   "资料为空或与主题不相关时，仍严格输出 3-5 章，按模型知识正常拆解即可，"
                   "不要为了覆盖更多方面而增加章节。"
                   "\n\n输出格式（必须严格遵守）："
                   "必须同时输出 plan 和 focus 两个数组，长度严格相等（一一对应）。"
                   "plan 是章节标题列表；focus 是每章对应的侧重角度（该章应覆盖什么、避开什么）。"
                   "任何一项缺失或不等长都视为失败。"
                   "\n\n可用资料概览："
                   "\n{kb_overview}"),
        ("human", "{objective}"),
    ])

    objective = state["objective"]
    kb_overview = state["kb_overview"]

    chain = template | model.with_structured_output(PlanState)

    response = chain.invoke({"objective": objective, "kb_overview": kb_overview})
    plan, focus = response.plan, response.focus
    # 兜底：实测无资料时模型出过 6-8 章，prompt 约束不住就裁剪
    if len(plan) > 5:
        plan, focus = plan[:5], focus[:5]
    return {
        "plan": plan,
        "focus": focus,
    }


def retriever(state: ResearchState) -> dict:
    if state.get("has_material") is False:
        return {"evidence": [{"question": p, "hits": []} for p in state.get("plan", [])]}
    kb_ids = state.get("kb_ids") or []
    if not kb_ids:
        return {
            "evidence": [],
        }
    from app.services.rag import get_embedder
    from app.services.rag.milvus_client import search

    embedder = get_embedder()
    plans = state["plan"]
    evidence = []
    for plan in plans:
        vector = embedder.embed_query(plan)
        hits = search(vector, kb_ids, top_k=3, document_ids=state.get("ready_doc_ids"),
                      epochs=state.get("ready_doc_epochs"))
        evidence.append({
            "question": plan,
            "hits": hits,
        })
    return {
        "evidence": evidence,
    }


async def synthesizer(state: ResearchState) -> dict:
    plans = state["plan"]
    objective = state["objective"]
    evidence = state.get("evidence", [])
    feedback = state.get("feedback")
    focuses = state.get("focus", [])

    template = ChatPromptTemplate.from_messages([
        ("system", """你是调研报告撰写者，负责撰写章节「{title}」的正文，围绕调研目标：{objective}。
            本章侧重：{focus}
            写作要求：只写本章职责范围内的内容；与产品定位等基础信息相关的背景，除非本章特有角度，否则不再重复铺陈。
            写作规则（必须遵守）：
            1. 【照资料写】只能基于下方内部资料撰写，禁止编造资料中不存在的数据、数字、结论。
               每个关键论断都必须在资料中有对应内容支撑；资料不足时明确写"该细节资料未覆盖"。
            2. 【事实性】不得虚构市场数据、客户案例、产品功能。
            3. 【简洁】紧扣章节主题，避免与其它章节重复表述。
            4. 【无标题】content 只写正文内容，不要以"## 标题"开头或包含章节标题——标题由系统在章节顶部单独展示。
            {context}
            {feedback_section}"""
        ),
        ("human", "{title}"),
    ])

    task_id = state.get("task_id")  # 运行上下文：无则不推进度（单测/直调兼容）
    total = len(plans)
    n_attempts = 2  # 单章最多尝试次数（LLM 偶发空响应/解析失败时重试）

    async def write_section(idx: int, plan: str, focus: str) -> tuple[int, SectionContent | None, Exception | None]:
        """单章写作：内部重试，绝不 raise——最终失败降级返回 (idx, None, err)，由调用方统一处理。"""
        related = next((e["hits"] for e in evidence if e["question"] == plan), [])
        context = "\n".join(f"[资料{i + 1}] {h['content']}" for i, h in enumerate(related))
        feedback_section = (
            "【评审意见（必须逐条修正，否则报告不合格）】\n" + feedback
            if feedback
            else "（无评审意见）"
        )
        last_err: Exception | None = None
        for attempt in range(1, n_attempts + 1):
            try:
                result = await (template | model.with_structured_output(SectionContent)).ainvoke({
                    "title": plan,
                    "objective": objective,
                    "context": context or "（无内部资料）",
                    "feedback_section": feedback_section,
                    "focus": focus,
                })
                return idx, result, None
            except Exception as exc:
                last_err = exc
                if task_id and attempt < n_attempts:
                    await publish_task_event(task_id, "agent_step", {
                        "step": "synthesizer", "status": "retry",
                        "detail": f"章节「{plan}」生成失败，第 {attempt}/{n_attempts} 次重试",
                    })
        return idx, None, last_err

    # T61：as_completed 聚合——每章完成即推进度；单章失败降级，不拖垮整个任务
    pending = [
        asyncio.create_task(write_section(i, plan, focus))
        for i, (plan, focus) in enumerate(zip(plans, focuses))
    ]
    results: list[SectionContent | None] = [None] * total  # 按序占位，最终章节顺序与 plans 一致
    errors: list[str] = []
    done = 0
    for fut in asyncio.as_completed(pending):
        idx, result, err = await fut
        done += 1
        plan = plans[idx]
        if err is not None:
            errors.append(f"章节「{plan}」生成失败：{str(err)[:200]}")
            if task_id:
                await publish_task_event(task_id, "agent_step", {
                    "step": "synthesizer", "status": "degraded",
                    "detail": f"已完成 {done}/{total} 章：{plan} 生成失败，已降级",
                })
        else:
            results[idx] = result
            if task_id:
                await publish_task_event(task_id, "agent_step", {
                    "step": "synthesizer", "status": "progress",
                    "detail": f"已完成 {done}/{total} 章：{plan}",
                })

    if len(errors) == total:
        # 全部章节失败 = 模型不可用，明确失败而非交付空报告
        raise RuntimeError(f"报告撰写失败：{total} 章全部生成失败（模型输出异常），首因：{errors[0]}")

    sections = []
    for plan, result in zip(plans, results):
        if result is None:
            sections.append({
                "title": plan,
                "content": "（本章生成失败：模型输出异常或资料不足。可补充资料后重新发起调研。）",
            })
        else:
            sections.append({"title": result.title, "content": result.content})

    evidence_used = []

    for plan in plans:
        related = next((e["hits"] for e in evidence if e["question"] == plan), [])
        docs = list({h["document_id"] for h in related if h.get("document_id")})
        snippets = [h["content"][:200] for h in related]
        evidence_used.append({"section_id": plan, "document_ids": docs, "snippets": snippets})

    draft = {"title": objective, "sections": sections}
    if errors:
        draft["section_errors"] = errors  # 写库段并入 quality.issues（随后 pop，不进报告 JSON）
    return {
        "draft": draft,
        "evidence_used": evidence_used
    }
