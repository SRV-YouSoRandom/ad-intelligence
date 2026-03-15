"""
Media processor — fetches real media from Meta snapshot pages.

WHY THE OLD APPROACH FAILED:
The snapshot URL (render_ad/?id=...&access_token=...) renders a React app.
BeautifulSoup sees the server-side HTML shell — the <video> and <img> tags
are injected by JavaScript AFTER the page loads. A plain httpx GET never
sees them.

THE CORRECT APPROACH:
The snapshot URL already contains the access token as a query parameter.
We fetch it with proper browser-like headers and a longer timeout, then
look for the CDN URLs two ways:

  1. JSON data embedded in <script> tags (Facebook embeds ad data as
     __bbox / ServerJS JSON before React hydrates — this is the reliable path)
  2. og:image / og:video meta tags (populated server-side, always present)
  3. Direct regex scan for fbcdn.net URLs in the raw HTML

The CDN URLs (scontent.fccu31-X.fna.fbcdn.net) are signed with expiry
tokens in the query string — they're directly downloadable without any
session cookie, for as long as the token hasn't expired (~hours to days).

For videos we download the full file then extract scene-change frames.
For images we download directly.
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

# Headers that make the request look like a real browser visit.
# Without these, Facebook returns a minimal non-JS fallback page.
SNAPSHOT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "no-cache",
}

# Regex to find signed fbcdn URLs anywhere in raw HTML
FBCDN_VIDEO_RE = re.compile(r'(https://[^"\'\\]*fbcdn[^"\'\\]+\.(?:mp4|mov)[^"\'\\]*)')
FBCDN_IMAGE_RE = re.compile(r'(https://[^"\'\\]*scontent[^"\'\\]+\.(?:jpg|jpeg|png|webp)[^"\'\\]*)')


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


# ── Core: extract media URLs from snapshot HTML ────────────────────────────────

def _extract_from_serverjs_json(html: str) -> tuple[str | None, str | None]:
    """
    Extract media from ServerJS JSON blobs. This is the most reliable method
    for extracting Meta ad media metadata as it reads the initial hydration state.
    """
    image_url = None
    video_url = None

    # Pattern 1: Find <script type="application/json"> elements usually carrying data-sjs
    script_matches = re.finditer(r'<script[^>]*type=(?:"|\')application/json(?:"|\')[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    for match in script_matches:
        content = match.group(1).strip()
        if not content:
            continue
        try:
            data = json.loads(content)
            v, i = _walk_json_for_media(data)
            if v and not video_url:
                video_url = v
            if i and not image_url:
                image_url = i
            if video_url and image_url:
                return image_url, video_url
        except (json.JSONDecodeError, RecursionError):
            continue

    # Pattern 2: Scripts with data-sjs directly
    sjs_matches = re.finditer(r'<script[^>]*data-sjs[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    for match in sjs_matches:
        content = match.group(1).strip()
        if not content:
            continue
        try:
            data = json.loads(content)
            v, i = _walk_json_for_media(data)
            if v and not video_url:
                video_url = v
            if i and not image_url:
                image_url = i
            if video_url and image_url:
                return image_url, video_url
        except (json.JSONDecodeError, RecursionError):
            continue

    return image_url, video_url


def _extract_from_json_blobs(html: str) -> tuple[str | None, str | None]:
    """
    Facebook embeds ad creative data as JSON in <script> tags before
    React hydrates. Look for video_sd_url, video_hd_url, image_url etc.
    This is the most reliable extraction path.
    """
    image_url = None
    video_url = None

    # Pattern 1: __bbox JSON blobs (server-side data)
    bbox_matches = re.findall(r'__bbox\s*,\s*(\{.{100,50000}?\})\s*\)', html, re.DOTALL)
    for blob in bbox_matches[:5]:  # check first 5 blobs
        try:
            data = json.loads(blob)
            v, i = _walk_json_for_media(data)
            if v and not video_url:
                video_url = v
            if i and not image_url:
                image_url = i
            if video_url and image_url:
                break
        except (json.JSONDecodeError, RecursionError):
            continue

    # Pattern 2: require("VideoPlayer") or similar embedded JSON
    if not video_url:
        video_matches = re.findall(r'"playable_url(?:_quality_hd)?"\s*:\s*"([^"]+)"', html)
        if video_matches:
            video_url = video_matches[0].replace("\\u0025", "%").replace("\\/", "/")

    # Pattern 3: poster/thumbnail URLs
    if not image_url:
        poster_matches = re.findall(r'"poster"\s*:\s*"(https://scontent[^"]+)"', html)
        if poster_matches:
            image_url = poster_matches[0].replace("\\/", "/")

    return image_url, video_url


def _walk_json_for_media(data, depth=0) -> tuple[str | None, str | None]:
    """Recursively walk a JSON structure looking for video/image URLs."""
    if depth > 8:
        return None, None

    image_url = None
    video_url = None

    if isinstance(data, dict):
        # Direct key hits
        for vkey in ("playable_url_quality_hd", "video_hd_url", "playable_url", "video_sd_url", "src"):
            val = data.get(vkey)
            if isinstance(val, str) and "fbcdn" in val and not video_url:
                video_url = val.replace("\\/", "/").replace("\\u0025", "%")
                break

        for ikey in ("image_url", "original_image_url", "resized_image_url", "uri"):
            val = data.get(ikey)
            if isinstance(val, str) and ("fbcdn" in val or "scontent" in val) and not image_url:
                image_url = val.replace("\\/", "/").replace("\\u0025", "%")
                break

        if not video_url or not image_url:
            for v in data.values():
                sub_v, sub_i = _walk_json_for_media(v, depth + 1)
                if sub_v and not video_url:
                    video_url = sub_v
                if sub_i and not image_url:
                    image_url = sub_i
                if video_url and image_url:
                    break

    elif isinstance(data, list):
        for item in data[:20]:  # limit list traversal
            sub_v, sub_i = _walk_json_for_media(item, depth + 1)
            if sub_v and not video_url:
                video_url = sub_v
            if sub_i and not image_url:
                image_url = sub_i
            if video_url and image_url:
                break

    return image_url, video_url


def _extract_from_meta_tags(html: str) -> tuple[str | None, str | None]:
    """
    og:image and og:video are populated server-side — always present
    even without JavaScript. Reliable fallback.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    video_url = None
    og_video = soup.find("meta", property="og:video") or soup.find("meta", property="og:video:url")
    if og_video:
        video_url = og_video.get("content")

    image_url = None
    og_image = soup.find("meta", property="og:image")
    if og_image:
        image_url = og_image.get("content")

    return image_url, video_url


def _extract_from_regex(html: str) -> tuple[str | None, str | None]:
    """
    Last resort: raw regex scan for signed fbcdn URLs.
    Catches cases where URLs are in inline JS strings.
    """
    video_url = None
    video_matches = FBCDN_VIDEO_RE.findall(html)
    if video_matches:
        # Prefer longer URLs (more complete, less likely truncated)
        video_url = max(video_matches, key=len)

    image_url = None
    image_matches = FBCDN_IMAGE_RE.findall(html)
    if image_matches:
        # Filter out tiny profile pictures (s60x60 in URL = small icon)
        full_size = [u for u in image_matches if "s60x60" not in u and "s40x40" not in u]
        if full_size:
            image_url = max(full_size, key=len)

    return image_url, video_url


async def fetch_media_from_snapshot(snapshot_url: str, ad_archive_id: str) -> dict | None:
    """
    Fetch media from a Meta ad snapshot URL.

    Tries three extraction strategies in order:
    1. JSON blobs embedded in <script> tags (most reliable, gets video)
    2. og:image / og:video meta tags (server-side, always present)
    3. Regex scan for fbcdn CDN URLs (brute force fallback)

    Returns dict with media_local_path, frame_paths, frame_metadata, or None.
    """
    async with _get_semaphore():
        try:
            async with httpx.AsyncClient(
                timeout=45,
                follow_redirects=True,
                headers=SNAPSHOT_HEADERS,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            ) as client:
                response = await client.get(snapshot_url)

                if response.status_code != 200:
                    logger.warning(
                        "snapshot_fetch_failed",
                        ad_id=ad_archive_id,
                        status=response.status_code,
                    )
                    return None

                html = response.text

            if len(html) < 20_000:
                logger.warning("snapshot_html_too_small", ad_id=ad_archive_id, size=len(html))
                return None

            image_url, video_url = None, None
            method = "none"

            # --- Strategy 1: ServerJS JSON ---
            image_url, video_url = _extract_from_serverjs_json(html)
            method = "serverjs_json"

            # --- Strategy 2: JSON blobs ---
            if not image_url and not video_url:
                image_url, video_url = _extract_from_json_blobs(html)
                method = "json_blob"

            # --- Strategy 3: og meta tags ---
            if not image_url and not video_url:
                image_url, video_url = _extract_from_meta_tags(html)
                method = "og_meta"

            # --- Strategy 4: regex ---
            if not image_url and not video_url:
                image_url, video_url = _extract_from_regex(html)
                method = "regex"

            if not image_url and not video_url:
                logger.warning("snapshot_no_media_found", ad_id=ad_archive_id, html_size=len(html))
                return None

            logger.info(
                "snapshot_media_found",
                ad_id=ad_archive_id,
                has_video=bool(video_url),
                has_image=bool(image_url),
                method=method,
            )

            # Process video first (more valuable for analysis)
            if video_url:
                result = await download_and_extract_frames(video_url, ad_archive_id)
                if result:
                    frame_paths, frame_metadata = result
                    # Also save the poster/thumbnail if we have it
                    poster_path = None
                    if image_url:
                        poster_path = await download_image(image_url, ad_archive_id, filename="poster.jpg")
                    return {
                        "media_local_path": poster_path or (frame_paths[0] if frame_paths else None),
                        "frame_paths": frame_paths,
                        "frame_metadata": frame_metadata,
                    }

            # Static image
            if image_url:
                local_path = await download_image(image_url, ad_archive_id)
                if local_path:
                    return {
                        "media_local_path": local_path,
                        "frame_paths": None,
                        "frame_metadata": None,
                    }

            return None

        except Exception as exc:
            logger.error("snapshot_fetch_error", ad_id=ad_archive_id, error=str(exc))
            return None


# ── Image Download ─────────────────────────────────────────────────────────────

async def download_image(url: str, ad_archive_id: str, filename: str = "image.jpg") -> str | None:
    """Download a static image from a signed CDN URL."""
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / filename

    try:
        async with httpx.AsyncClient(
            timeout=30, 
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            response = await client.get(url, headers={"User-Agent": SNAPSHOT_HEADERS["User-Agent"]})
            response.raise_for_status()

            # Sanity check: make sure we got an image, not HTML
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type:
                logger.warning("image_url_returned_html", ad_id=ad_archive_id, url=url[:80])
                return None

            with open(output_path, "wb") as f:
                f.write(response.content)

            metrics.increment("images_downloaded")
            logger.info("image_downloaded", ad_id=ad_archive_id, size=len(response.content))
            return str(output_path)
    except Exception as exc:
        logger.error("image_download_failed", ad_id=ad_archive_id, error=str(exc))
        return None


# ── Video Download + Frame Extraction ─────────────────────────────────────────

async def download_video(video_url: str, ad_archive_id: str) -> str | None:
    """
    Download a video from a signed fbcdn CDN URL.
    Uses httpx directly (not yt-dlp) since fbcdn URLs are direct MP4 links.
    Falls back to yt-dlp for non-direct URLs.
    """
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "video.mp4"

    # Try direct download first (fbcdn URLs are direct MP4s)
    if "fbcdn.net" in video_url or "fbcdn.com" in video_url:
        try:
            async with httpx.AsyncClient(
                timeout=120, 
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            ) as client:
                async with client.stream("GET", video_url, headers={"User-Agent": SNAPSHOT_HEADERS["User-Agent"]}) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "video" not in content_type and "octet-stream" not in content_type:
                        logger.warning("video_url_wrong_content_type", content_type=content_type)
                        # Fall through to yt-dlp
                    else:
                        with open(output_path, "wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=1024 * 64):
                                f.write(chunk)
                        if output_path.exists() and output_path.stat().st_size > 10_000:
                            metrics.increment("videos_downloaded_direct")
                            logger.info("video_downloaded_direct", ad_id=ad_archive_id)
                            return str(output_path)
        except Exception as exc:
            logger.warning("direct_video_download_failed", ad_id=ad_archive_id, error=str(exc))
            # Fall through to yt-dlp

    # yt-dlp fallback
    try:
        process = await asyncio.create_subprocess_exec(
            "yt-dlp", "--no-warnings",
            "-o", str(output_path),
            "--format", "best[ext=mp4]/best",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        if process.returncode != 0:
            logger.error("yt_dlp_failed", ad_id=ad_archive_id, stderr=stderr.decode()[:200])
            return None
        if output_path.exists() and output_path.stat().st_size > 10_000:
            metrics.increment("videos_downloaded_ytdlp")
            return str(output_path)
        return None
    except asyncio.TimeoutError:
        logger.error("video_download_timeout", ad_id=ad_archive_id)
        return None
    except Exception as exc:
        logger.error("video_download_error", ad_id=ad_archive_id, error=str(exc))
        return None


async def get_video_duration(video_path: str) -> float | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        data = json.loads(stdout.decode())
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                duration = stream.get("duration")
                if duration:
                    return float(duration)
        return None
    except Exception as exc:
        logger.error("ffprobe_error", video_path=video_path, error=str(exc))
        return None


async def extract_frames(video_path: str, ad_archive_id: str) -> list[FrameMeta]:
    """Extract scene-change-aware frames from a video using ffmpeg."""
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_dir = str(ad_dir)
    threshold = settings.SCENE_CHANGE_THRESHOLD

    frames_pattern = os.path.join(output_dir, "frame_%03d.jpg")
    scene_log_path = os.path.join(output_dir, "scene_log.txt")

    try:
        with open(scene_log_path, "w") as log_f:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", video_path,
                "-vf", f"select='gte(scene,{threshold})',scale=1280:-1,showinfo",
                "-vsync", "vfr", "-frame_pts", "true",
                frames_pattern,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_f,
            )
            await asyncio.wait_for(process.communicate(), timeout=120)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.error("frame_extraction_error", ad_id=ad_archive_id, error=str(exc))

    # Parse extracted frames
    frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))
    frames: list[FrameMeta] = []

    if frame_files:
        try:
            with open(scene_log_path, "r", errors="replace") as f:
                log_content = f.read()
            pts_times = re.findall(r"pts_time:\s*([\d.]+)", log_content)
            for i, frame_file in enumerate(frame_files):
                ts = float(pts_times[i]) if i < len(pts_times) else float(i)
                frames.append(FrameMeta(
                    path=str(frame_file), timestamp_sec=ts,
                    scene_score=0.30, index=i, is_hook=ts < 2.0,
                ))
        except Exception:
            frames = [
                FrameMeta(path=str(f), timestamp_sec=float(i), scene_score=0.30, index=i, is_hook=i == 0)
                for i, f in enumerate(frame_files)
            ]

    # Always ensure we have a hook frame (first frame)
    if not any(f.timestamp_sec < 0.5 for f in frames):
        hook_path = os.path.join(output_dir, "frame_000_hook.jpg")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", video_path,
                "-vf", "select='eq(n,0)',scale=1280:-1", "-vframes", "1", hook_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if os.path.exists(hook_path):
                frames.insert(0, FrameMeta(path=hook_path, timestamp_sec=0.0, scene_score=1.0, index=0, is_hook=True))
        except Exception:
            pass

    # Fallback to uniform sampling if scene detection yielded nothing
    if len(frames) < 2:
        duration = await get_video_duration(video_path)
        if duration:
            for i, pct in enumerate([0.0, 0.5, 0.9]):
                ts = duration * pct
                fp = os.path.join(output_dir, f"frame_uniform_{i:03d}.jpg")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                        "-vf", "scale=1280:-1", "-vframes", "1", fp,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=15)
                    if os.path.exists(fp):
                        frames.append(FrameMeta(path=fp, timestamp_sec=round(ts, 2), scene_score=0.0, index=i, is_hook=ts < 2.0))
                except Exception:
                    pass

    frames = frames[:settings.MAX_FRAMES]
    for i, f in enumerate(frames):
        f.index = i

    metrics.increment("video_frames_extracted", len(frames))
    logger.info("frames_extracted", ad_id=ad_archive_id, count=len(frames))
    return frames


async def download_and_extract_frames(video_url: str, ad_archive_id: str) -> tuple[list[str], list[dict]] | None:
    """Full pipeline: download video → extract frames → delete source video."""
    video_path = await download_video(video_url, ad_archive_id)
    if not video_path:
        return None

    frames = await extract_frames(video_path, ad_archive_id)
    if not frames:
        return None

    frame_paths = [f.path for f in frames]
    frame_metadata = [
        {"path": f.path, "timestamp_sec": f.timestamp_sec, "scene_score": f.scene_score,
         "index": f.index, "is_hook": f.is_hook}
        for f in frames
    ]

    # Delete source video to save disk space
    try:
        os.remove(video_path)
    except OSError:
        pass

    return frame_paths, frame_metadata