"""Redis 客户端（与 Celery broker 共用实例）：Agent 事件流（Redis Streams）读写。"""
import json
import redis.asyncio as redis
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
redis_client = redis.from_url(settings.redis_url, decode_responses=True)

TASK_STREAM = "task:stream:{}"  # task:stream:<task_id> 事件日志流
REDIS_EXPIRE_TIME = 600

def _payload(event: str, data: dict) -> str:
    return json.dumps({"event": event, "data": data}, ensure_ascii=False)


async def publish_task_event(task_id: str, event: str, data: dict) -> None:
    """发布一条 Agent 事件到任务流（XADD + 续期 600s）。

    事件是「通知」不是「数据」：任务真状态在 PG，SSE 流只是推送通道。
    所以这里吞掉 Redis 异常 —— 通知发不出去顶多让用户看不到实时进度
    （刷新页面仍能看到真实状态），但不能让整个调研任务因此失败。
    """
    stream = TASK_STREAM.format(task_id)
    try:
        await redis_client.xadd(stream, {"payload": _payload(event, data)})
        await redis_client.expire(stream, REDIS_EXPIRE_TIME)
    except Exception as e:
        logger.warning("事件发布失败（不影响任务）：task=%s event=%s %s", task_id, event, e)


async def xrange_task_events(task_id: str, after_id: str = "-") -> list[tuple[str, str, dict]]:
    stream = TASK_STREAM.format(task_id)
    # ① 算 start：after_id 为 "-"（无游标）直接传；否则拼 f"({after_id}"（开区间）
    start = after_id if after_id == "-" else f"({after_id}"
    # ② xrange 拿 [(id, fields)]
    result = await redis_client.xrange(stream, start)
    # ③ 逐个 json.loads(fields["payload"]) 拆出 event/data，重组为 [(id, event, data)]
    events = []
    for entry_id, fields in result:
        payload = json.loads(fields["payload"])
        events.append((entry_id, payload["event"], payload["data"]))
    return events


async def xread_task_events(task_id: str, cursor: str, block_ms: int = 5000) -> list[tuple[str, str, dict]] | None:
    stream = TASK_STREAM.format(task_id)
    result = await redis_client.xread(
        streams={
            stream: cursor,
        },
        block=block_ms,
    )
    if not result:
        return None
    items = result[0][1]

    events = []
    for entry_id, fields in items:
        payload = json.loads(fields["payload"])
        events.append((entry_id, payload["event"], payload["data"]))
    return events
