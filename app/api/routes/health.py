"""Health check endpoint."""

from fastapi import APIRouter

from app.core.metrics import metrics

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "service": "ad-intelligence"}


@router.get("/metrics/summary")
async def metrics_summary():
    """Custom metrics endpoint."""
    return metrics.get_summary()
