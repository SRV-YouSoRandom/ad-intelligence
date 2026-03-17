"""
Meta Ads Library API fetcher with rate limiting and pagination.

FIELD NOTES — what the API actually returns vs what we were missing:
  - disclaimer       → present = political/issue ad (paid for by disclosure)
  - bylines          → "Paid for by" text, political ads only  
  - beneficiary_payers → EU political ads: who paid, who benefits
  - estimated_audience_size → range object {lower_bound, upper_bound}
  - delivery_by_region → region-level reach breakdown (political + EU)
  - demographic_distribution → age/gender breakdown (political + EU)
  - languages        → detected languages in the ad
  - currency         → currency of spend figures

  NOTE: ad_type is a SEARCH FILTER PARAMETER (what you pass in), not a
  response field. You cannot ask the API "what type is this ad?" — you
  infer it from disclaimer presence and what search you ran.
  
  To correctly identify political ads, we fetch with ad_type=ALL so we
  get everything, then use disclaimer/bylines presence in the response
  to classify as political. This is the correct approach per Meta docs.
"""

import asyncio
import hashlib
import time
from typing import AsyncIterator

import httpx
import valkey.asyncio as valkey_async

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)


# All fields the Ads Library API actually returns.
# Grouped by category for clarity.
FIELDS_STRING = ",".join([
    # Identity
    "id",
    "ad_archive_id",
    "page_id",
    "page_name",

    # Creative content
    "ad_creative_bodies",
    "ad_creative_link_captions",
    "ad_creative_link_descriptions",
    "ad_creative_link_titles",
    "ad_snapshot_url",
    "languages",

    # Status & timing
    "is_active",
    "ad_delivery_start_time",
    "ad_delivery_stop_time",

    # Platforms
    "publisher_platforms",

    # Performance metrics (EU + political)
    "impressions",
    "reach",
    "spend",
    "currency",
    "estimated_audience_size",

    # Political/issue ad specific fields
    "disclaimer",        # "Paid for by X" — PRESENCE = political ad
    "bylines",           # same as disclaimer but different format
    "beneficiary_payers",# EU political: {beneficiary, payer}

    # Demographic & geographic breakdowns (political + EU)
    "delivery_by_region",
    "demographic_distribution",
])


class TokenBucketRateLimiter:
    """Valkey-backed token bucket rate limiter for Meta API calls."""

    def __init__(self, vk_client: valkey_async.Valkey, max_tokens: int = 200, refill_period: int = 3600):
        self.vk = vk_client
        self.max_tokens = max_tokens
        self.refill_rate = max_tokens / refill_period
        token_hash = hashlib.sha256(settings.META_ACCESS_TOKEN.encode()).hexdigest()[:16]
        self.key = f"meta_api:rate_limit:{token_hash}"

    async def acquire(self) -> None:
        while True:
            now = time.time()
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
                local wait = math.ceil((1 - tokens) / refill_rate * 1000)
                return -wait
            end
            """
            result = await self.vk.eval(lua_script, 1, self.key, self.max_tokens, self.refill_rate, now)
            if result > 0:
                return
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

        We always fetch with ad_type=ALL so we capture both commercial
        and political/issue ads. Political classification is done
        post-fetch by checking the `disclaimer` field presence.
        """
        params = {
            "search_page_ids": page_id,
            "ad_reached_countries": '["' + '","'.join(countries) + '"]',
            "ad_active_status": status,
            "ad_type": "ALL",   # fetch everything; classify by disclaimer field
            "fields": FIELDS_STRING,
            "limit": 100,
            "access_token": settings.META_ACCESS_TOKEN,
        }
        url = self.base_url
        total_fetched = 0
        page_num = 0

        logger.info("meta_fetch_starting", page_id=page_id, countries=countries, status=status)

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

                    if page_num == 1:
                        logger.info(
                            "meta_first_page_response",
                            status_code=response.status_code,
                            has_data="data" in data,
                            has_error="error" in data,
                            data_count=len(data.get("data", [])),
                        )

                    if "error" in data:
                        error = data["error"]
                        error_code = error.get("code", 0)
                        if error_code == 190:
                            raise MetaAPIError(f"Authentication error: {error.get('message', 'Unknown')}")
                        logger.error("meta_api_error", error=error, page=page_num)
                        raise MetaAPIError(f"Meta API Error ({error_code}): {error.get('message', 'Unknown')}")

                    if response.status_code >= 400 and "data" not in data:
                        raise MetaAPIError(f"HTTP {response.status_code}: {response.text}")

                    ads = data.get("data", [])
                    for ad in ads:
                        yield ad
                        total_fetched += 1

                    metrics.increment("meta_ads_fetched", len(ads))
                    logger.info("meta_page_fetched", page=page_num, ads_in_page=len(ads), total=total_fetched)

                    next_page = data.get("paging", {}).get("next")
                    url = next_page
                    params = {}

                except MetaAPIError:
                    raise
                except httpx.HTTPStatusError as exc:
                    logger.error("meta_api_http_error", status=exc.response.status_code, page=page_num)
                    if exc.response.status_code in (500, 503):
                        break
                    raise MetaAPIError(f"HTTP Error {exc.response.status_code}: {exc.response.text}")
                except Exception as exc:
                    logger.error("meta_api_unexpected_error", error=str(exc), page=page_num)
                    raise

        logger.info("meta_fetch_complete", total_ads=total_fetched, pages=page_num)

    async def _make_request_with_retry(self, client, url, params, max_retries=3):
        for attempt in range(max_retries + 1):
            response = await client.get(url, params=params if params else None)
            if response.status_code == 429:
                if attempt < max_retries:
                    wait = 2 ** attempt * 5
                    logger.warning("meta_api_rate_limited", attempt=attempt + 1, wait_sec=wait)
                    await asyncio.sleep(wait)
                    continue
            return response
        return response


class MetaAPIError(Exception):
    pass