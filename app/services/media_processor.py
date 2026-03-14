"""
Media processor — fetches real media from Meta snapshot HTML pages,
downloads images, extracts scene-change-aware frames from videos.

WHY THE SNAPSHOT APPROACH:
The Meta Ads Library API does not return direct image or video URLs.
The `ad_snapshot_url` points to a rendered HTML preview page at:
  https://www.facebook.com/ads/archive/render_ad/?id=...&access_token=...

This page contains the actual <img> and <video> elements with real CDN URLs.
We parse this HTML to extract those URLs — this is the only sanctioned way
to access ad creative media through the Ads Library system.
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


# ── Snapshot HTML Parsing ──────────────────────────────────────────────────────

async def fetch_media_from_snapshot(snapshot_url: str, ad_archive_id: str) -> dict | None:
    """
    Fetch the snapshot HTML page and extract real media URLs from it.

    The snapshot page renders the ad creative. We parse the HTML for:
    - <img> tags with high-res src (for static ads)
    - <video> tags with src (for video ads)
    - og:image meta tags as fallback

    Returns:
        Dict with media_local_path, frame_paths, frame_metadata keys, or None on failure.
    """
    async with _get_semaphore():
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; AdIntelligence/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            }

            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(snapshot_url, headers=headers)

                if response.status_code != 200:
                    logger.warning(
                        "snapshot_fetch_failed",
                        ad_id=ad_archive_id,
                        status=response.status_code,
                    )
                    return None

                html = response.text

            # Parse HTML for media URLs
            image_url, video_url = _extract_media_urls(html, snapshot_url)

            if video_url:
                logger.info("snapshot_video_found", ad_id=ad_archive_id, url=video_url[:80])
                result = await download_and_extract_frames(video_url, ad_archive_id)
                if result:
                    frame_paths, frame_metadata = result
                    return {
                        "media_local_path": frame_paths[0] if frame_paths else None,
                        "frame_paths": frame_paths,
                        "frame_metadata": frame_metadata,
                    }

            if image_url:
                logger.info("snapshot_image_found", ad_id=ad_archive_id, url=image_url[:80])
                local_path = await download_image(image_url, ad_archive_id)
                if local_path:
                    return {
                        "media_local_path": local_path,
                        "frame_paths": None,
                        "frame_metadata": None,
                    }

            logger.warning("snapshot_no_media_found", ad_id=ad_archive_id)
            return None

        except Exception as exc:
            logger.error("snapshot_fetch_error", ad_id=ad_archive_id, error=str(exc))
            return None


def _extract_media_urls(html: str, base_url: str) -> tuple[str | None, str | None]:
    """
    Parse snapshot HTML to extract image and video URLs.

    Priority order:
    1. <video src="..."> or <video><source src="..."> — for video ads
    2. <img> with largest dimensions or data-src — for static ads
    3. og:image meta tag — fallback for static ads

    Returns:
        Tuple of (image_url, video_url). Either may be None.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Look for video elements
    video_url = None
    video_tags = soup.find_all("video")
    for video in video_tags:
        src = video.get("src") or ""
        if src and src.startswith("http"):
            video_url = src
            break
        # Check <source> children
        source = video.find("source")
        if source:
            src = source.get("src") or ""
            if src and src.startswith("http"):
                video_url = src
                break

    # Also check JSON-LD or inline scripts for video URLs (Facebook often embeds these)
    if not video_url:
        scripts = soup.find_all("script", type="application/json")
        for script in scripts:
            try:
                data = json.loads(script.string or "")
                video_url = _find_video_url_in_json(data)
                if video_url:
                    break
            except Exception:
                pass

    # 2. Look for image elements
    image_url = None

    # og:image is often the best quality
    og_image = soup.find("meta", property="og:image")
    if og_image:
        image_url = og_image.get("content")

    # If no og:image, look for the largest <img> that isn't a profile pic/icon
    if not image_url:
        imgs = soup.find_all("img")
        candidates = []
        for img in imgs:
            src = img.get("src") or img.get("data-src") or ""
            if not src or not src.startswith("http"):
                continue
            # Skip small icons and profile pictures (usually contain 's60x60' or similar)
            if any(x in src for x in ["s60x60", "s40x40", "emoji", "static/images"]):
                continue
            # Prefer larger images from fbcdn (Meta's CDN)
            if "fbcdn" in src or "cdninstagram" in src:
                width = img.get("width", 0)
                try:
                    candidates.append((int(width), src))
                except (ValueError, TypeError):
                    candidates.append((0, src))

        if candidates:
            candidates.sort(reverse=True)
            image_url = candidates[0][1]

    return image_url, video_url


def _find_video_url_in_json(data, depth=0) -> str | None:
    """Recursively search JSON structure for video URLs."""
    if depth > 10:
        return None
    if isinstance(data, str):
        if data.startswith("https://") and any(ext in data for ext in [".mp4", ".mov", "video"]):
            return data
    elif isinstance(data, dict):
        for v in data.values():
            result = _find_video_url_in_json(v, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_video_url_in_json(item, depth + 1)
            if result:
                return result
    return None


# ── Image Download ─────────────────────────────────────────────────────────────

async def download_image(url: str, ad_archive_id: str) -> str | None:
    """Download a static image."""
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "image.jpg"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
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
    """Download a video using yt-dlp."""
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "video.mp4"

    try:
        process = await asyncio.create_subprocess_exec(
            "yt-dlp", "--no-warnings",
            "-o", str(output_path),
            "--format", "best[ext=mp4]/best",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        if process.returncode != 0:
            logger.error("video_download_failed", ad_id=ad_archive_id, stderr=stderr.decode())
            return None
        if output_path.exists():
            metrics.increment("videos_downloaded")
            return str(output_path)
        return None
    except asyncio.TimeoutError:
        logger.error("video_download_timeout", ad_id=ad_archive_id)
        return None
    except Exception as exc:
        logger.error("video_download_error", ad_id=ad_archive_id, error=str(exc))
        return None


async def get_video_duration(video_path: str) -> float | None:
    """Get video duration using ffprobe."""
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


def _parse_scene_log(scene_log_path: str, output_dir: str) -> list[FrameMeta]:
    """Parse ffmpeg showinfo output to extract scene-change frames with timestamps."""
    frames = []
    frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))
    if not frame_files:
        return frames
    try:
        with open(scene_log_path, "r", errors="replace") as f:
            log_content = f.read()
        pts_times = re.findall(r"pts_time:\s*([\d.]+)", log_content)
        for i, frame_file in enumerate(frame_files):
            timestamp = float(pts_times[i]) if i < len(pts_times) else 0.0
            frames.append(FrameMeta(
                path=str(frame_file), timestamp_sec=timestamp,
                scene_score=0.30, index=i, is_hook=timestamp < 2.0,
            ))
    except Exception as exc:
        logger.warning("scene_log_parse_error", error=str(exc))
        for i, frame_file in enumerate(frame_files):
            frames.append(FrameMeta(
                path=str(frame_file), timestamp_sec=0.0,
                scene_score=0.30, index=i, is_hook=i == 0,
            ))
    return frames


async def _extract_first_frame(video_path: str, output_dir: str) -> FrameMeta | None:
    """Extract the very first frame (hook frame)."""
    frame_path = os.path.join(output_dir, "frame_000_hook.jpg")
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vf", "select='eq(n,0)',scale=1280:-1", "-vframes", "1", frame_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=30)
        if os.path.exists(frame_path):
            return FrameMeta(path=frame_path, timestamp_sec=0.0, scene_score=1.0, index=0, is_hook=True)
    except Exception as exc:
        logger.error("first_frame_extraction_error", error=str(exc))
    return None


async def _uniform_fallback(video_path: str, output_dir: str, points: list[float] = None) -> list[FrameMeta]:
    """Fallback: extract frames at fixed percentage points."""
    if points is None:
        points = [0.0, 0.5, 0.9]
    duration = await get_video_duration(video_path)
    if not duration or duration <= 0:
        return []
    frames = []
    for i, pct in enumerate(points):
        timestamp = duration * pct
        frame_path = os.path.join(output_dir, f"frame_uniform_{i:03d}.jpg")
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path,
                "-vf", "scale=1280:-1", "-vframes", "1", frame_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=15)
            if os.path.exists(frame_path):
                frames.append(FrameMeta(
                    path=frame_path, timestamp_sec=round(timestamp, 2),
                    scene_score=0.0, index=i, is_hook=timestamp < 2.0,
                ))
        except Exception as exc:
            logger.warning("uniform_frame_error", timestamp=timestamp, error=str(exc))
    return frames


async def extract_frames(video_path: str, ad_archive_id: str) -> list[FrameMeta]:
    """Extract scene-change-aware frames from a video."""
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_dir = str(ad_dir)
    scene_log_path = os.path.join(output_dir, "scene_log.txt")
    threshold = settings.SCENE_CHANGE_THRESHOLD

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"select='gte(scene,{threshold})',scale=1280:-1,showinfo",
            "-vsync", "vfr", "-frame_pts", "true",
            os.path.join(output_dir, "frame_%03d.jpg"),
            stdout=asyncio.subprocess.PIPE,
            stderr=open(scene_log_path, "w"),
        )
        await asyncio.wait_for(process.communicate(), timeout=120)
    except asyncio.TimeoutError:
        logger.error("frame_extraction_timeout", ad_id=ad_archive_id)
    except Exception as exc:
        logger.error("frame_extraction_error", ad_id=ad_archive_id, error=str(exc))

    frames = _parse_scene_log(scene_log_path, output_dir)

    has_hook = any(f.timestamp_sec < 0.5 for f in frames)
    if not has_hook:
        hook_frame = await _extract_first_frame(video_path, output_dir)
        if hook_frame:
            frames.insert(0, hook_frame)

    frames = frames[:settings.MAX_FRAMES]

    if len(frames) < 2:
        logger.info("scene_detection_sparse_fallback", ad_id=ad_archive_id)
        frames = await _uniform_fallback(video_path, output_dir, [0.0, 0.5, 0.9])

    for i, frame in enumerate(frames):
        frame.index = i

    metrics.increment("video_frames_extracted", len(frames))
    return frames


async def download_and_extract_frames(video_url: str, ad_archive_id: str) -> tuple[list[str], list[dict]] | None:
    """Full video pipeline: download → extract frames → delete source video."""
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
    try:
        os.remove(video_path)
        logger.info("video_deleted_after_extraction", ad_id=ad_archive_id)
    except OSError:
        pass
    return frame_paths, frame_metadata