"""Dependency injection for API routes — DB sessions, Valkey client."""

from typing import AsyncGenerator

import valkey.asyncio as valkey_async
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import async_session_factory

# Global Valkey client (initialized on app startup)
_valkey_client: valkey_async.Valkey | None = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for request-scoped use."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_valkey() -> valkey_async.Valkey:
    """Initialize the global Valkey client."""
    global _valkey_client
    _valkey_client = valkey_async.from_url(
        settings.VALKEY_URL,
        decode_responses=True,
    )
    return _valkey_client


async def close_valkey() -> None:
    """Close the global Valkey client."""
    global _valkey_client
    if _valkey_client:
        await _valkey_client.aclose()
        _valkey_client = None


async def get_valkey() -> valkey_async.Valkey:
    """Get the global Valkey client instance."""
    if _valkey_client is None:
        raise RuntimeError("Valkey client not initialized. Call init_valkey() first.")
    return _valkey_client
