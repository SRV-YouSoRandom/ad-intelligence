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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.facebook.com/ads/library/",
    "Origin": "https://www.facebook.com",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

FBCDN_VIDEO_RE = re.compile(r"https://[^\"']+\.mp4[^\"']*")
FBCDN_IMAGE_RE = re.compile(r"https://[^\"']+\.(?:jpg|jpeg|png|webp)[^\"']*")

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


# ---------------------------------------------------------
# JSON extractor (MOST RELIABLE)
# ---------------------------------------------------------

def _extract_bbox_media(html: str):

    video_url = None
    image_url = None

    matches = re.findall(r'__bbox\s*,\s*(\{.*?\})\s*\)', html, re.DOTALL)

    for blob in matches[:5]:
        try:
            data = json.loads(blob)
        except Exception:
            continue

        def walk(obj):
            nonlocal video_url, image_url

            if isinstance(obj, dict):
                for k, v in obj.items():

                    if isinstance(v, str):

                        if "video" in k and ".mp4" in v:
                            if not video_url:
                                video_url = v.replace("\\/", "/")

                        if "image" in k and "fbcdn" in v:
                            if not image_url:
                                image_url = v.replace("\\/", "/")

                    walk(v)

            elif isinstance(obj, list):
                for i in obj:
                    walk(i)

        walk(data)

    return image_url, video_url


# ---------------------------------------------------------
# HTML extractor
# ---------------------------------------------------------

def _extract_media_candidates(html: str):

    soup = BeautifulSoup(html, "html.parser")

    video_url = None
    image_url = None

    video = soup.find("video")

    if video:
        if video.get("src"):
            video_url = video["src"]

        source = video.find("source")
        if not video_url and source and source.get("src"):
            video_url = source["src"]

        if video.get("poster"):
            image_url = video["poster"]

    if not image_url:
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            if "scontent" in src and "t39.35426" in src:
                image_url = src
                break

    if not video_url:
        m = FBCDN_VIDEO_RE.search(html)
        if m:
            video_url = m.group(0)

    if not image_url:
        m = FBCDN_IMAGE_RE.search(html)
        if m:
            image_url = m.group(0)

    return image_url, video_url


# ---------------------------------------------------------
# MAIN MEDIA FETCH
# ---------------------------------------------------------

async def fetch_media_from_snapshot(snapshot_url: str, ad_archive_id: str):

    async with _get_semaphore():

        try:

            async with httpx.AsyncClient(
                timeout=45,
                follow_redirects=True,
                headers=SNAPSHOT_HEADERS
            ) as client:

                response = await client.get(snapshot_url, headers=SNAPSHOT_HEADERS)

                if response.status_code != 200:
                    logger.warning(
                        "snapshot_fetch_failed",
                        ad_id=ad_archive_id,
                        status=response.status_code,
                    )
                    return None

                html = response.text

            logger.info(
                "snapshot_debug",
                ad_id=ad_archive_id,
                html_size=len(html),
                contains_fbcdn=("fbcdn" in html),
                contains_video_tag=("<video" in html),
                contains_img_tag=("<img" in html),
            )

            # ---- Try JSON first ----
            image_url, video_url = _extract_bbox_media(html)

            # ---- fallback to HTML ----
            if not image_url and not video_url:
                image_url, video_url = _extract_media_candidates(html)

            if not image_url and not video_url:
                logger.warning(
                    "snapshot_no_media_found",
                    ad_id=ad_archive_id
                )
                return None

            logger.info(
                "snapshot_media_found",
                ad_id=ad_archive_id,
                has_video=bool(video_url),
                has_image=bool(image_url),
            )

            # ------------------------------------------------
            # VIDEO
            # ------------------------------------------------

            if video_url:

                result = await download_and_extract_frames(video_url, ad_archive_id)

                if result:

                    frame_paths, frame_metadata = result

                    poster_path = None

                    if image_url:
                        poster_path = await download_image(
                            image_url,
                            ad_archive_id,
                            filename="poster.jpg"
                        )

                    return {
                        "media_local_path": poster_path or frame_paths[0],
                        "frame_paths": frame_paths,
                        "frame_metadata": frame_metadata,
                    }

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            if image_url:

                ext = image_url.split("?")[0].split(".")[-1]
                filename = f"image.{ext}"

                local_path = await download_image(
                    image_url,
                    ad_archive_id,
                    filename=filename
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


# ---------------------------------------------------------
# IMAGE DOWNLOAD
# ---------------------------------------------------------

async def download_image(url: str, ad_archive_id: str, filename="image.jpg"):

    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / filename

    try:

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:

            r = await client.get(url)

            if r.status_code != 200:
                return None

            with open(output_path, "wb") as f:
                f.write(r.content)

        metrics.increment("images_downloaded")

        return str(output_path)

    except Exception as e:

        logger.error("image_download_failed", error=str(e))
        return None


# ---------------------------------------------------------
# VIDEO DOWNLOAD
# ---------------------------------------------------------

async def download_video(video_url: str, ad_archive_id: str):

    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "video.mp4"

    try:

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:

            async with client.stream("GET", video_url) as r:

                if r.status_code != 200:
                    return None

                with open(output_path, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)

        if output_path.exists():
            metrics.increment("videos_downloaded_direct")
            return str(output_path)

        return None

    except Exception as e:

        logger.error("video_download_error", error=str(e))
        return None


# ---------------------------------------------------------
# FRAME EXTRACTION
# ---------------------------------------------------------

async def extract_frames(video_path: str, ad_archive_id: str):

    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_dir = str(ad_dir)

    frames_pattern = os.path.join(output_dir, "frame_%03d.jpg")

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        "select='gt(scene,0.3)',scale=1280:-1",
        "-vsync",
        "vfr",
        frames_pattern,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await process.communicate()

    frames = []

    frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))

    for i, f in enumerate(frame_files):

        frames.append(
            FrameMeta(
                path=str(f),
                timestamp_sec=float(i),
                scene_score=0.3,
                index=i,
                is_hook=i == 0,
            )
        )

    metrics.increment("video_frames_extracted", len(frames))

    return frames


async def download_and_extract_frames(video_url: str, ad_archive_id: str):

    video_path = await download_video(video_url, ad_archive_id)

    if not video_path:
        return None

    frames = await extract_frames(video_path, ad_archive_id)

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