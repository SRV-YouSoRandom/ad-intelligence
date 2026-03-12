"""Media processor — downloads images, videos, and extracts scene-change-aware frames."""

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import metrics

logger = get_logger(__name__)

# Semaphore for concurrent downloads
_download_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)
    return _download_semaphore


@dataclass
class FrameMeta:
    """Metadata for a single extracted video frame."""
    path: str
    timestamp_sec: float
    scene_score: float
    index: int
    is_hook: bool  # True if timestamp < 2.0 seconds


def _ensure_ad_dir(ad_archive_id: str) -> Path:
    """Create and return the media storage directory for an ad."""
    ad_dir = Path(settings.MEDIA_STORAGE_PATH) / ad_archive_id
    ad_dir.mkdir(parents=True, exist_ok=True)
    return ad_dir


async def download_image(url: str, ad_archive_id: str) -> str | None:
    """
    Download a static image for an ad.

    Args:
        url: Image URL to download
        ad_archive_id: Unique ad identifier for directory naming

    Returns:
        Local file path or None on failure
    """
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "image.jpg"

    async with _get_semaphore():
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


async def download_video(video_url: str, ad_archive_id: str) -> str | None:
    """
    Download a video using yt-dlp.

    Args:
        video_url: Video URL (video_sd_url or video_hd_url from Meta)
        ad_archive_id: Unique ad identifier

    Returns:
        Local file path or None on failure
    """
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_path = ad_dir / "video.mp4"

    async with _get_semaphore():
        try:
            process = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "--no-warnings",
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
                logger.info("video_downloaded", ad_id=ad_archive_id, size=output_path.stat().st_size)
                return str(output_path)
            else:
                logger.error("video_download_no_file", ad_id=ad_archive_id)
                return None

        except asyncio.TimeoutError:
            logger.error("video_download_timeout", ad_id=ad_archive_id)
            return None
        except Exception as exc:
            logger.error("video_download_error", ad_id=ad_archive_id, error=str(exc))
            return None


async def get_video_duration(video_path: str) -> float | None:
    """Get video duration in seconds using ffprobe."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
    """
    Parse ffmpeg showinfo output to extract scene-change frames with timestamps.

    The showinfo filter writes lines like:
    [Parsed_showinfo_1 @ ...] n: 123 pts: 12345 pts_time:4.567 ...
    """
    frames = []
    frame_files = sorted(Path(output_dir).glob("frame_*.jpg"))

    if not frame_files:
        return frames

    try:
        with open(scene_log_path, "r", errors="replace") as f:
            log_content = f.read()

        # Extract pts_time values from showinfo output
        pts_times = re.findall(r"pts_time:\s*([\d.]+)", log_content)

        for i, frame_file in enumerate(frame_files):
            timestamp = float(pts_times[i]) if i < len(pts_times) else 0.0
            frames.append(FrameMeta(
                path=str(frame_file),
                timestamp_sec=timestamp,
                scene_score=0.30,  # we used 0.30 threshold, so all frames have at least this
                index=i,
                is_hook=timestamp < 2.0,
            ))
    except Exception as exc:
        logger.warning("scene_log_parse_error", error=str(exc))
        # Fall back to just listing frames without timestamps
        for i, frame_file in enumerate(frame_files):
            frames.append(FrameMeta(
                path=str(frame_file),
                timestamp_sec=0.0,
                scene_score=0.30,
                index=i,
                is_hook=i == 0,
            ))

    return frames


async def _extract_first_frame(video_path: str, output_dir: str) -> FrameMeta | None:
    """Extract the very first frame at t=0 (the hook)."""
    frame_path = os.path.join(output_dir, "frame_000_hook.jpg")
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", "select='eq(n,0)',scale=1280:-1",
            "-vframes", "1",
            frame_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=30)

        if os.path.exists(frame_path):
            return FrameMeta(
                path=frame_path,
                timestamp_sec=0.0,
                scene_score=1.0,
                index=0,
                is_hook=True,
            )
    except Exception as exc:
        logger.error("first_frame_extraction_error", error=str(exc))
    return None


async def _uniform_fallback(video_path: str, output_dir: str, points: list[float] = None) -> list[FrameMeta]:
    """Fall back to uniform sampling at specific percentage points."""
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
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", video_path,
                "-vf", "scale=1280:-1",
                "-vframes", "1",
                frame_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=15)

            if os.path.exists(frame_path):
                frames.append(FrameMeta(
                    path=frame_path,
                    timestamp_sec=round(timestamp, 2),
                    scene_score=0.0,
                    index=i,
                    is_hook=timestamp < 2.0,
                ))
        except Exception as exc:
            logger.warning("uniform_frame_error", timestamp=timestamp, error=str(exc))

    return frames


async def extract_frames(video_path: str, ad_archive_id: str) -> list[FrameMeta]:
    """
    Extract scene-change-aware frames from a video.

    Uses ffmpeg's scene change detection (threshold=0.30) to capture
    semantically meaningful frames. Falls back to uniform sampling if
    fewer than 2 scene changes are detected.

    Args:
        video_path: Path to the video file
        ad_archive_id: Unique ad identifier

    Returns:
        List of FrameMeta objects, ordered by timestamp, capped at MAX_FRAMES
    """
    ad_dir = _ensure_ad_dir(ad_archive_id)
    output_dir = str(ad_dir)
    scene_log_path = os.path.join(output_dir, "scene_log.txt")
    threshold = settings.SCENE_CHANGE_THRESHOLD

    try:
        # Run ffmpeg scene detection
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"select='gte(scene,{threshold})',scale=1280:-1,showinfo",
            "-vsync", "vfr",
            "-frame_pts", "true",
            os.path.join(output_dir, "frame_%03d.jpg"),
            stdout=asyncio.subprocess.PIPE,
            stderr=open(scene_log_path, "w"),
        )
        await asyncio.wait_for(process.communicate(), timeout=120)

    except asyncio.TimeoutError:
        logger.error("frame_extraction_timeout", ad_id=ad_archive_id)
    except Exception as exc:
        logger.error("frame_extraction_error", ad_id=ad_archive_id, error=str(exc))

    # Parse results
    frames = _parse_scene_log(scene_log_path, output_dir)

    # Always prepend frame at t=0 (the hook) if not already captured
    has_hook = any(f.timestamp_sec < 0.5 for f in frames)
    if not has_hook:
        hook_frame = await _extract_first_frame(video_path, output_dir)
        if hook_frame:
            frames.insert(0, hook_frame)

    # Cap at MAX_FRAMES (naturally weights toward hook/opening)
    frames = frames[:settings.MAX_FRAMES]

    # If too few frames, fall back to uniform sampling
    if len(frames) < 2:
        logger.info("scene_detection_sparse_fallback", ad_id=ad_archive_id, frames=len(frames))
        frames = await _uniform_fallback(video_path, output_dir, [0.0, 0.5, 0.9])

    # Re-index
    for i, frame in enumerate(frames):
        frame.index = i

    metrics.increment("video_frames_extracted", len(frames))
    return frames


async def download_and_extract_frames(video_url: str, ad_archive_id: str) -> tuple[list[str], list[dict]] | None:
    """
    Full video pipeline: download → extract frames → delete video.

    Returns:
        Tuple of (frame_paths, frame_metadata_dicts) or None on failure
    """
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

    # Delete source video to save storage — frames are all the model needs
    try:
        os.remove(video_path)
        logger.info("video_deleted_after_extraction", ad_id=ad_archive_id)
    except OSError:
        pass

    return frame_paths, frame_metadata
