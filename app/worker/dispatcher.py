"""Job dispatcher — routes jobs to the correct task handler."""

from app.core.logging import get_logger
from app.worker.tasks.fetch_brand_ads import run_fetch_brand_ads
from app.worker.tasks.generate_insights import run_generate_insights

logger = get_logger(__name__)


async def dispatch(job: dict) -> None:
    """
    Route a job to its handler based on job_type.

    Args:
        job: Dict with 'job_id', 'job_type', and 'payload' keys.
    """
    job_type = job.get("job_type")
    job_id = job.get("job_id")

    logger.info("dispatching_job", job_id=job_id, job_type=job_type)

    try:
        if job_type == "fetch_brand_ads":
            await run_fetch_brand_ads(job_id=job_id, payload=job["payload"])
        elif job_type == "generate_insights":
            await run_generate_insights(job_id=job_id, payload=job["payload"])
        else:
            logger.error("unknown_job_type", job_id=job_id, job_type=job_type)
    except Exception as exc:
        logger.error("job_dispatch_error", job_id=job_id, job_type=job_type, error=str(exc))
        raise
