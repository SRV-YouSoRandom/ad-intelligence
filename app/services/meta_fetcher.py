"""Meta Ads Library API fetcher with rate limiting and pagination."""

import asyncio
import hashlib
import json
import time
from typing import AsyncIterator

import httpx
import valkey.asyncio as valkey_async

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)


# Fields to request from the Meta Ads Library API
FIELDS_STRING = ",".join([
    "id",
    "ad_archive_id",
    "ad_creative_bodies",
    "ad_creative_link_captions",
    "ad_creative_link_descriptions",
    "ad_creative_link_titles",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",
    "ad_snapshot_url",
    "impressions",
    "reach",
    "spend",
    "publisher_platforms",
    "is_active",
    "ad_creatives{video_id,image_hash,thumbnail_url,video_sd_url,video_hd_url,image_url,body,call_to_action_type,link_url,title,description}",
])


class TokenBucketRateLimiter:
    """Valkey-backed token bucket rate limiter for Meta API calls."""

    def __init__(self, vk_client: valkey_async.Valkey, max_tokens: int = 200, refill_period: int = 3600):
        self.vk = vk_client
        self.max_tokens = max_tokens
        self.refill_rate = max_tokens / refill_period  # tokens per second
        token_hash = hashlib.sha256(settings.META_ACCESS_TOKEN.encode()).hexdigest()[:16]
        self.key = f"meta_api:rate_limit:{token_hash}"

    async def acquire(self) -> None:
        """Acquire a token, sleeping if necessary until one is available."""
        while True:
            now = time.time()

            # Lua script for atomic token bucket check-and-decrement
            lua_script = """
            local key = KEYS[1]
            local max_tokens = tonumber(ARGV[1])
            local refill_rate = tonumber(ARGV[2])
            local now = tonumber(ARGV[3])

            local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
            local tokens = tonumber(bucket[1])
            local last_refill = tonumber(bucket[2])

            if tokens == nil then
                tokens = max_tokens
                last_refill = now
            end

            -- Refill tokens based on elapsed time
            local elapsed = now - last_refill
            tokens = math.min(max_tokens, tokens + elapsed * refill_rate)

            if tokens >= 1 then
                tokens = tokens - 1
                redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
                redis.call('EXPIRE', key, 7200)
                return 1
            else
                redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
                redis.call('EXPIRE', key, 7200)
                -- Return wait time in milliseconds
                local wait = math.ceil((1 - tokens) / refill_rate * 1000)
                return -wait
            end
            """

            result = await self.vk.eval(lua_script, 1, self.key, self.max_tokens, self.refill_rate, now)

            if result > 0:
                return  # Token acquired
            else:
                wait_ms = abs(result)
                logger.info("rate_limit_waiting", wait_ms=wait_ms)
                await asyncio.sleep(wait_ms / 1000.0)


class MetaFetcher:
    """Fetches ads from the Meta Ads Library API with pagination and rate limiting."""

    def __init__(self, vk_client: valkey_async.Valkey):
        self.rate_limiter = TokenBucketRateLimiter(
            vk_client,
            max_tokens=settings.META_RATE_LIMIT_CALLS,
            refill_period=settings.META_RATE_LIMIT_PERIOD,
        )
        self.base_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/ads_archive"

    async def fetch_all_ads_for_brand(
        self,
        page_id: str,
        countries: list[str],
        status: str = "ALL",
    ) -> AsyncIterator[dict]:
        """
        Fetch all ads for a brand via cursor-based pagination.

        Args:
            page_id: Meta Page ID
            countries: List of country codes for ad_reached_countries
            status: 'ALL', 'ACTIVE', or 'INACTIVE'

        Yields:
            Individual ad dictionaries from the API response
        """
        params = {
            "search_page_ids": page_id,
            "ad_reached_countries": json.dumps(countries),
            "ad_active_status": status,
            "fields": FIELDS_STRING,
            "limit": 100,
            "access_token": settings.META_ACCESS_TOKEN,
        }
        url = self.base_url
        total_fetched = 0
        page_num = 0

        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                await self.rate_limiter.acquire()
                page_num += 1

                start_time = time.time()
                try:
                    response = await self._make_request_with_retry(client, url, params)
                    elapsed_ms = (time.time() - start_time) * 1000
                    metrics.record_timing("meta_api_call", elapsed_ms)

                    data = response.json()

                    # Check for API errors
                    if "error" in data:
                        error = data["error"]
                        error_code = error.get("code", 0)

                        # Auth error — raise immediately
                        if error_code == 190:
                            logger.error("meta_api_auth_error", error=error)
                            raise MetaAPIError(f"Authentication error: {error.get('message', 'Unknown')}")

                        logger.error("meta_api_error", error=error, page=page_num)
                        break

                    ads = data.get("data", [])
                    for ad in ads:
                        yield ad
                        total_fetched += 1

                    metrics.increment("meta_ads_fetched", len(ads))
                    logger.info(
                        "meta_page_fetched",
                        page=page_num,
                        ads_in_page=len(ads),
                        total=total_fetched,
                    )

                    # Navigate to the next page
                    next_page = data.get("paging", {}).get("next")
                    url = next_page
                    params = {}  # Next URL already has params embedded

                except MetaAPIError:
                    raise
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "meta_api_http_error",
                        status=exc.response.status_code,
                        page=page_num,
                    )
                    if exc.response.status_code in (500, 503):
                        logger.warning("meta_api_server_error_skipping", page=page_num)
                        break
                    raise
                except Exception as exc:
                    logger.error("meta_api_unexpected_error", error=str(exc), page=page_num)
                    raise

        logger.info("meta_fetch_complete", total_ads=total_fetched, pages=page_num)

    async def _make_request_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry on 429."""
        for attempt in range(max_retries + 1):
            response = await client.get(url, params=params if params else None)

            if response.status_code == 429:
                if attempt < max_retries:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    logger.warning("meta_api_rate_limited", attempt=attempt + 1, wait_sec=wait)
                    await asyncio.sleep(wait)
                    continue
                else:
                    response.raise_for_status()

            response.raise_for_status()
            return response

        # Should not reach here, but just in case
        return response


class MetaAPIError(Exception):
    """Custom exception for Meta API errors."""
    pass
