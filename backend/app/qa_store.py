"""question-SQL 对沉淀（M3）：成功轮次自动写入 LangSmith dataset，供回归评估。

去重：Redis SADD（question hash，30 天 TTL），同一问题不重复入库。
未配置 LANGSMITH_API_KEY 时 no-op；任何失败只 log，不阻断主流程。
"""
import hashlib
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

_SEEN_KEY = "chatbi:qa:seen"
_SEEN_TTL_SECONDS = 30 * 24 * 3600


def _langsmith_dataset():
    """返回 (client, dataset)；未配置 key 时返回 None。"""
    import os

    if not os.environ.get("LANGSMITH_API_KEY"):
        return None
    from langsmith import Client

    client = Client()
    name = get_settings().langsmith_dataset
    if not client.has_dataset(dataset_name=name):
        client.create_dataset(dataset_name=name, description="chat-bi question-SQL 回归样本（自动沉淀）")
    return client, name


def _seen_before(question: str) -> bool:
    """True = 该问题 30 天内已沉淀过。Redis 不可用时放行（宁可重复，不阻断）。"""
    try:
        import redis

        client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=5)
        digest = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
        added = client.sadd(_SEEN_KEY, digest)
        client.expire(_SEEN_KEY, _SEEN_TTL_SECONDS)
        return added == 0
    except Exception as e:
        logger.warning("QA 去重检查失败（放行）: %s", e)
        return False


def record_qa_pair(
    question: str, sql: str, session_id: str, healed: bool = False, retry_count: int = 0
) -> bool:
    """沉淀一条样本；返回是否实际写入。调用方须自行捕获异常（本函数不保证幂等重试）。"""
    ds = _langsmith_dataset()
    if ds is None:
        return False
    if _seen_before(question):
        return False
    client, name = ds
    client.create_example(
        inputs={"question": question},
        outputs={"sql": sql},
        dataset_name=name,
        metadata={"session_id": session_id, "healed": healed, "retry_count": retry_count},
    )
    return True
