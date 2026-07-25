import logging
import os
from typing import Any

from db import get_db

logger = logging.getLogger(__name__)

BEDROCK_LIMIT_MESSAGE = (
    "Daily AI investigation limit reached — please try again tomorrow "
    "(or contact an admin to raise BEDROCK_DAILY_LIMIT)."
)


class BedrockDailyLimitExceeded(RuntimeError):
    def __init__(self, call_count: int, daily_limit: int):
        self.call_count = call_count
        self.daily_limit = daily_limit
        super().__init__(BEDROCK_LIMIT_MESSAGE)


def get_bedrock_daily_limit() -> int:
    try:
        return max(1, int(os.environ.get("BEDROCK_DAILY_LIMIT", "100")))
    except ValueError:
        return 100


def check_and_increment_bedrock_usage() -> int:
    conn = get_db()
    if not conn:
        logger.warning("Skipping Bedrock usage guard because the DB is unavailable")
        return 0

    old_autocommit = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bedrock_usage (usage_date, call_count)
                VALUES (CURRENT_DATE, 1)
                ON CONFLICT (usage_date)
                DO UPDATE SET call_count = bedrock_usage.call_count + 1
                RETURNING call_count
                """
            )
            row = cur.fetchone()
        call_count = int(row[0]) if row else 0
    finally:
        conn.autocommit = old_autocommit

    daily_limit = get_bedrock_daily_limit()
    if call_count > daily_limit:
        raise BedrockDailyLimitExceeded(call_count=call_count, daily_limit=daily_limit)
    return call_count


class GuardedBedrockLLM:
    # A single shared counter covers both chat completions and embeddings so the
    # demo has one simple daily ceiling across all Bedrock-powered behavior.
    def __init__(self, inner: Any):
        self._inner = inner

    async def ainvoke(self, *args, **kwargs):
        check_and_increment_bedrock_usage()
        return await self._inner.ainvoke(*args, **kwargs)

    def invoke(self, *args, **kwargs):
        check_and_increment_bedrock_usage()
        return self._inner.invoke(*args, **kwargs)

    def bind_tools(self, *args, **kwargs):
        return GuardedBedrockLLM(self._inner.bind_tools(*args, **kwargs))

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def wrap_bedrock_llm(llm: Any) -> GuardedBedrockLLM:
    return llm if isinstance(llm, GuardedBedrockLLM) else GuardedBedrockLLM(llm)
