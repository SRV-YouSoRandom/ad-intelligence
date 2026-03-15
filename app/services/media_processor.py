"""
Media processor — fetches real media from Meta snapshot pages.
Improved version with more reliable extraction of Meta CDN media.
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)

_download_semaphore: asyncio.Semaphore | None = None


SNAPSHOT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


# Improved regex (handles fbcdn video URLs without .mp4 extension)
FBCDN_VIDEO_RE = re.compile(r'(https://video[^"\'\\]+fbcdn\.net[^"\'\\]+)')
FBCDN_IMAGE_RE = re.compile(r'(https://scontent[^"\'\\]+fbcdn\.net[^"\'\\]+)')


def _get_semaphore() -> asyncio.Semaphore:
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)
    return _download_semaphore


@dataclass
class FrameMeta:
    path: str
    timestamp_sec: float
    scene_score: float
    index: int
    is_hook: bool


def _ensure_ad_dir(ad_archive_id: str) -> Path:
    ad_dir = Path(settings.MEDIA_STORAGE_PATH) / ad_archive_id
    ad_dir.mkdir(parents=True, exist_ok=True)
    return ad_dir


# ------------------------------------------------
# JSON MEDIA WALKER
# ------------------------------------------------

def _walk_json_for_media(data, depth=0):

    if depth > 10:
        return None, None

    image_url = None
    video_url = None

    VIDEO_KEYS = (
        "playable_url_quality_hd",
        "playable_url",
        "video_hd_url",
        "video_sd_url",
        "browser_native_hd_url",
        "browser_native_sd_url",
        "src",
    )

    IMAGE_KEYS = (
        "image_url",
        "original_image_url",
        "resized_image_url",
        "thumbnail_url",
        "uri",
    )

    if isinstance(data, dict):

        for key in VIDEO_KEYS:
            val = data.get(key)
            if isinstance(val, str) and "fbcdn" in val:
                video_url = val.replace("\\/", "/")
                break

        for key in IMAGE_KEYS:
            val = data.get(key)
            if isinstance(val, str) and "fbcdn" in val:
                image_url = val.replace("\\/", "/")
                break

        for v in data.values():
            if video_url and image_url:
                break

            sub_v, sub_i = _walk_json_for_media(v, depth + 1)

            if sub_v and not video_url:
                video_url = sub_v

            if sub_i and not image_url:
                image_url = sub_i

    elif isinstance(data, list):

        for item in data[:25]:

            sub_v, sub_i = _walk_json_for_media(item, depth + 1)

            if sub_v and not video_url:
                video_url = sub_v

            if sub_i and not image_url:
                image_url = sub_i

            if video_url and image_url:
                break

    return image_url, video_url


# ------------------------------------------------
# EXTRACTION METHODS
# ------------------------------------------------

def _extract_from_serverjs_json(html):

    image_url = None
    video_url = None

    scripts = re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )

    for blob in scripts[:10]:

        blob = blob.strip()

        if not blob.startswith("{"):
            continue

        try:
            data = json.loads(blob)
        except Exception:
            continue

        v, i = _walk_json_for_media(data)

        if v and not video_url:
            video_url = v

        if i and not image_url:
            image_url = i

        if video_url and image_url:
            break

    return image_url, video_url


def _extract_from_json_blobs(html):

    image_url = None
    video_url = None

    blobs = re.findall(r'\{.{200,50000}?"fbcdn\.net".{0,5000}?\}', html, re.DOTALL)

    for blob in blobs[:20]:

        try:
            data = json.loads(blob)
        except Exception:
            continue

        v, i = _walk_json_for_media(data)

        if v and not video_url:
            video_url = v

        if i and not image_url:
            image_url = i

        if video_url and image_url:
            break

    return image_url, video_url


def _extract_from_meta_tags(html):

    soup = BeautifulSoup(html, "html.parser")

    video_url = None
    og_video = soup.find("meta", property="og:video") or soup.find(
        "meta", property="og:video:url"
    )

    if og_video:
        video_url = og_video.get("content")

    image_url = None
    og_image = soup.find("meta", property="og:image")

    if og_image:
        image_url = og_image.get("content")

    return image_url, video_url


def _extract_from_regex(html):

    video_url = None
    image_url = None

    video_matches = FBCDN_VIDEO_RE.findall(html)

    if video_matches:
        video_url = max(video_matches, key=len)

    image_matches = FBCDN_IMAGE_RE.findall(html)

    if image_matches:

        full_size = [
            u for u in image_matches
            if "s60x60" not in u and "s40x40" not in u
        ]

        if full_size:
            image_url = max(full_size, key=len)

    return image_url, video_url


# ------------------------------------------------
# MAIN SNAPSHOT FETCHER
# ------------------------------------------------

async def fetch_media_from_snapshot(snapshot_url: str, ad_archive_id: str):

    async with _get_semaphore():

        try:

            async with httpx.AsyncClient(
                timeout=45,
                follow_redirects=True,
                headers=SNAPSHOT_HEADERS,
            ) as client:

                response = await client.get(snapshot_url)

                if response.status_code != 200:
                    logger.warning(
                        "snapshot_fetch_failed",
                        ad_id=ad_archive_id,
                        status=response.status_code,
                    )
                    return None

                html = response.text[:2_000_000]

            image_url, video_url = _extract_from_serverjs_json(html)
            method = "serverjs"

            if not image_url and not video_url:
                image_url, video_url = _extract_from_json_blobs(html)
                method = "json_blob"

            if not image_url and not video_url:
                image_url, video_url = _extract_from_meta_tags(html)
                method = "og_meta"

            if not image_url and not video_url:
                image_url, video_url = _extract_from_regex(html)
                method = "regex"

            if not image_url and not video_url:
                logger.warning(
                    "snapshot_no_media_found",
                    ad_id=ad_archive_id,
                    html_size=len(html),
                )
                return None

            logger.info(
                "snapshot_media_found",
                ad_id=ad_archive_id,
                has_video=bool(video_url),
                has_image=bool(image_url),
                method=method,
            )

            if video_url:

                result = await download_and_extract_frames(
                    video_url,
                    ad_archive_id,
                )

                if result:

                    frame_paths, frame_metadata = result

                    poster_path = None

                    if image_url:
                        poster_path = await download_image(
                            image_url,
                            ad_archive_id,
                            filename="poster.jpg",
                        )

                    return {
                        "media_local_path": poster_path or (
                            frame_paths[0] if frame_paths else None
                        ),
                        "frame_paths": frame_paths,
                        "frame_metadata": frame_metadata,
                    }

            if image_url:

                local_path = await download_image(
                    image_url,
                    ad_archive_id,
                )

                if local_path:

                    return {
                        "media_local_path": local_path,
                        "frame_paths": None,
                        "frame_metadata": None,
                    }

            return None

        except Exception as exc:

            logger.error(
                "snapshot_fetch_error",
                ad_id=ad_archive_id,
                error=str(exc),
            )

            return None


# ------------------------------------------------
# IMAGE DOWNLOAD
# ------------------------------------------------

async def download_image(url, ad_archive_id, filename="image.jpg"):

    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / filename

    try:

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
        ) as client:

            response = await client.get(
                url,
                headers={"User-Agent": SNAPSHOT_HEADERS["User-Agent"]},
            )

            response.raise_for_status()

            content_type = response.headers.get("content-type", "")

            if "text/html" in content_type:
                logger.warning(
                    "image_url_returned_html",
                    ad_id=ad_archive_id,
                )
                return None

            with open(output_path, "wb") as f:
                f.write(response.content)

            metrics.increment("images_downloaded")

            return str(output_path)

    except Exception as exc:

        logger.error(
            "image_download_failed",
            ad_id=ad_archive_id,
            error=str(exc),
        )

        return None


# ------------------------------------------------
# VIDEO DOWNLOAD
# ------------------------------------------------

async def download_video(video_url, ad_archive_id):

    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "video.mp4"

    if "fbcdn" in video_url:

        try:

            async with httpx.AsyncClient(
                timeout=120,
                follow_redirects=True,
            ) as client:

                async with client.stream(
                    "GET",
                    video_url,
                    headers={"User-Agent": SNAPSHOT_HEADERS["User-Agent"]},
                ) as response:

                    response.raise_for_status()

                    with open(output_path, "wb") as f:

                        async for chunk in response.aiter_bytes(
                            chunk_size=65536
                        ):
                            f.write(chunk)

            if output_path.exists() and output_path.stat().st_size > 10_000:

                metrics.increment("videos_downloaded_direct")

                return str(output_path)

        except Exception as exc:

            logger.warning(
                "direct_video_download_failed",
                ad_id=ad_archive_id,
                error=str(exc),
            )

    return None


# ------------------------------------------------
# FRAME EXTRACTION
# ------------------------------------------------

async def extract_frames(video_path, ad_archive_id):

    ad_dir = _ensure_ad_dir(ad_archive_id)

    frames_pattern = os.path.join(ad_dir, "frame_%03d.jpg")

    threshold = settings.SCENE_CHANGE_THRESHOLD

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"select='gte(scene,{threshold})',scale=1280:-1",
        "-vsync",
        "vfr",
        frames_pattern,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await process.communicate()

    frame_files = sorted(Path(ad_dir).glob("frame_*.jpg"))

    frames = []

    for i, frame in enumerate(frame_files):

        frames.append(
            FrameMeta(
                path=str(frame),
                timestamp_sec=float(i),
                scene_score=0.30,
                index=i,
                is_hook=i == 0,
            )
        )

    metrics.increment("video_frames_extracted", len(frames))

    return frames


async def download_and_extract_frames(video_url, ad_archive_id):

    video_path = await download_video(video_url, ad_archive_id)

    if not video_path:
        return None

    frames = await extract_frames(video_path, ad_archive_id)

    if not frames:
        return None

    frame_paths = [f.path for f in frames]

    frame_metadata = [
        {
            "path": f.path,
            "timestamp_sec": f.timestamp_sec,
            "scene_score": f.scene_score,
            "index": f.index,
            "is_hook": f.is_hook,
        }
        for f in frames
    ]

    try:
        os.remove(video_path)
    except OSError:
        pass

    return frame_paths, frame_metadata