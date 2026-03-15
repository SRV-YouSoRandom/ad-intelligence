"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import close_valkey, init_valkey
from app.api.routes import ads, brands, health, insights, jobs
from app.core.logging import setup_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("ad_intelligence_starting", version="1.1.0")
    await init_valkey()
    logger.info("valkey_connected")
    yield
    await close_valkey()
    logger.info("ad_intelligence_shutdown")


app = FastAPI(
    title="Ad Intelligence Platform",
    description="Fetches Meta ads, classifies them, scores performance, and generates AI-powered creative insights. Supports both commercial and political ad analysis.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.routes import media

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(brands.router, prefix="/api/v1", tags=["brands"])
app.include_router(ads.router, prefix="/api/v1", tags=["ads"])
app.include_router(insights.router, prefix="/api/v1", tags=["insights"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
app.include_router(media.router, tags=["media"])  # no /api/v1 prefix for direct serving