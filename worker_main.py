"""Worker entrypoint — polls Valkey for jobs and dispatches them."""

import asyncio
import signal
import sys

# Add parent directory to path for module resolution
sys.path.insert(0, "/app")

import valkey.asyncio as valkey_async

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.api.dependencies import init_valkey, close_valkey
from app.worker.queue import JobQueue
from app.worker import dispatcher

logger = get_logger(__name__)

# Graceful shutdown flag
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    logger.info("shutdown_signal_received", signal=signum)


async def main():
    """Main worker loop — polls jobs:pending with BLPOP."""
    setup_logging()
    logger.info("worker_starting")

    # Initialize Valkey
    vk = await init_valkey()
    queue = JobQueue(vk)

    logger.info("worker_started", queue="jobs:pending")

    try:
        while not _shutdown:
            job = await queue.dequeue(timeout=5)
            if job:
                try:
                    await dispatcher.dispatch(job)
                except Exception as exc:
                    logger.error(
                        "job_failed",
                        job_id=job.get("job_id"),
                        job_type=job.get("job_type"),
                        error=str(exc),
                    )
    except asyncio.CancelledError:
        logger.info("worker_cancelled")
    finally:
        await close_valkey()
        logger.info("worker_shutdown_complete")


if __name__ == "__main__":
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(main())
