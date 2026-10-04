from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from core.config import Settings


@asynccontextmanager
async def redis_lifespan(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=5)
    try:
        await client.ping()
        yield client
    finally:
        await client.aclose()
