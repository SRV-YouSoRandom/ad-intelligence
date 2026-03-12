"""FastAPI application entry point with lifespan management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.dependencies import close_valkey, init_valkey
from app.api.routes import ads, brands, health, insights, jobs
from app.core.logging import setup_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    # Startup
    setup_logging()
    logger.info("ad_intelligence_starting", version="1.0.0")

    # Initialize Valkey connection
    await init_valkey()
    logger.info("valkey_connected")

    yield

    # Shutdown
    await close_valkey()
    logger.info("ad_intelligence_shutdown")


app = FastAPI(
    title="Ad Intelligence Platform",
    description="Fetches Meta ads, classifies them, scores performance, and generates AI-powered creative insights.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(brands.router, prefix="/api/v1", tags=["brands"])
app.include_router(ads.router, prefix="/api/v1", tags=["ads"])
app.include_router(insights.router, prefix="/api/v1", tags=["insights"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
