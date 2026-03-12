"""Valkey-backed job queue using RPUSH/BLPOP."""

import json
import uuid
from datetime import datetime, timezone

import valkey.asyncio as valkey_async

from app.core.logging import get_logger

logger = get_logger(__name__)


class JobQueue:
    """Simple Valkey-backed job queue."""

    QUEUE_KEY = "jobs:pending"

    def __init__(self, vk_client: valkey_async.Valkey):
        self.vk = vk_client

    async def enqueue(self, job_id: str, job_type: str, payload: dict) -> None:
        """Push a job to the pending queue."""
        job_data = json.dumps({
            "job_id": job_id,
            "job_type": job_type,
            "payload": payload,
        })
        await self.vk.rpush(self.QUEUE_KEY, job_data)

        # Set status hash for fast polling
        await self.vk.hset(f"jobs:status:{job_id}", mapping={
            "status": "PENDING",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("job_enqueued", job_id=job_id, job_type=job_type)

    async def dequeue(self, timeout: int = 5) -> dict | None:
        """
        Block-pop a job from the queue.

        Args:
            timeout: Seconds to wait before returning None

        Returns:
            Job dict or None if queue is empty after timeout
        """
        result = await self.vk.blpop(self.QUEUE_KEY, timeout=timeout)
        if result:
            _, raw = result
            job = json.loads(raw)
            logger.info("job_dequeued", job_id=job["job_id"], job_type=job["job_type"])
            return job
        return None

    async def update_status(self, job_id: str, status: str) -> None:
        """Update the fast-poll status hash in Valkey."""
        await self.vk.hset(f"jobs:status:{job_id}", mapping={
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
