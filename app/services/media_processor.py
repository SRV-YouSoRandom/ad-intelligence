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
# Loosened to be escaping-aware (handles https:\/\/ and http:\/\/)
FBCDN_VIDEO_RE = re.compile(r'(https?:\\?/\\?/[^"\'\s]+\.(?:mp4|mov|m4v)[^"\'\s]*)')
FBCDN_IMAGE_RE = re.compile(r'(https?:\\?/\\?/[^"\'\s]+\.(?:jpg|jpeg|png|webp|gif)[^"\'\s]*)')

# Patterns that strongly suggest a profile picture or non-ad icon
PROBABLE_PROFILE_PIC_PATTERNS = [
    "profile", "avatar", "icon", "logo", "s60x60", "s40x40", "s32x32", "s120x120",
    "t1.0-1", "p50x50", "p100x100"
]

# Keys in JSON that usually point to page/actor assets rather than the ad creative
PROFILE_JSON_KEYS = {
    "page_profile_picture", "profile_photo", "actor_photo", "profile_pic",
    "logo_url", "owner_image_url"
}

# Keys that strongly indicate the actual ad creative
CREATIVE_JSON_KEYS = {
    "playable_url", "playable_url_quality_hd", "video_sd_url", "video_hd_url",
    "image_url", "original_image_url", "resized_image_url"
}


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

def rank_url(url: str, is_video: bool) -> float:
    """
    Ranks URLs based on probability of being the primary ad creative.
    - Meta uses v-type suffixes: -1 (profile/icon), -6/-7 (images), -2 (videos).
    - HD versions and longer URLs (with full signatures) are preferred.
    """
    if not url: return -100000.0
    url_l = url.lower()
    score = 0.0

    # 1. Fatal Penalties (Profile pics, icons, small thumbnails)
    if any(p in url_l for p in PROBABLE_PROFILE_PIC_PATTERNS):
        score -= 50000.0
    
    # Meta's specific v-type suffix check (-1 = profile/small icon)
    if "-1" in url_l and "/v/" in url_l:
        score -= 20000.0

    # 2. Type Boosts
    if is_video:
        score += 10000.0
        if any(ext in url_l for ext in [".mp4", ".mov", ".m4v"]):
            score += 5000.0
    
    # 3. Quality/Creative Boosts
    if "hd" in url_l or "original" in url_l:
        score += 2000.0
    if "-6" in url_l or "-7" in url_l or "-2" in url_l:
        score += 3000.0 # Creative v-types
    
    if "scontent" in url_l:
        score += 500.0

    # 4. Length as a tie-breaker (longer = more signature parameters)
    score += len(url) / 10.0

    return score


def _walk_json_for_media(data, depth=0) -> tuple[set[str], set[str]]:
    """Recursively find ALL potential media URLs in a JSON blob."""
    v_urls = set()
    i_urls = set()
    if depth > 40: return v_urls, i_urls

    if isinstance(data, dict):
        # Skip strictly profile-related branches
        for pk in PROFILE_JSON_KEYS:
            if pk in data and len(data) < 4:
                return v_urls, i_urls

        for k, v in data.items():
            if k in CREATIVE_JSON_KEYS and isinstance(v, str):
                if "fbcdn" in v or "scontent" in v:
                    cleaned = v.replace("\\/", "/").replace("\\u0025", "%")
                    if any(ext in cleaned.lower() for ext in [".mp4", ".mov", ".m4v"]):
                        v_urls.add(cleaned)
                    else:
                        i_urls.add(cleaned)
            else:
                sub_v, sub_i = _walk_json_for_media(v, depth + 1)
                v_urls.update(sub_v)
                i_urls.update(sub_i)

    elif isinstance(data, list):
        for item in data[:30]:
            sub_v, sub_i = _walk_json_for_media(item, depth + 1)
            v_urls.update(sub_v)
            i_urls.update(sub_i)

    return v_urls, i_urls


def _extract_media_candidates(html: str) -> tuple[str | None, str | None]:
    """Exhaustive collection and ranking of all media candidates."""
    all_videos = set()
    all_images = set()

    # 1. JSON blobs
    bbox_matches = re.findall(r'__bbox\s*,\s*(\{.{100,55000}?\})\s*\)', html, re.DOTALL)
    for blob in bbox_matches[:10]:
        try:
            data = json.loads(blob)
            v, i = _walk_json_for_media(data)
            all_videos.update(v)
            all_images.update(i)
        except (json.JSONDecodeError, RecursionError):
            continue

    # Raw string scan for creative keys (fallback for malformed JSON)
    raw_v = re.findall(r'"playable_url(?:_quality_hd)?"\s*:\s*"([^"]+)"', html)
    all_videos.update([r.replace("\\/", "/").replace("\\u0025", "%") for r in raw_v])
    raw_i = re.findall(r'"(image_url|original_image_url)"\s*:\s*"([^"]+)"', html)
    all_images.update([r[1].replace("\\/", "/").replace("\\u0025", "%") for r in raw_i])

    # 2. HTML Tags
    soup = BeautifulSoup(html, "html.parser")
    for v_tag in soup.find_all("video"):
        src = v_tag.get("src")
        if src: all_videos.add(src)
        for source in v_tag.find_all("source"):
            if source.get("src"): all_videos.add(source.get("src"))
        poster = v_tag.get("poster")
        if poster: all_images.add(poster)

    for img_tag in soup.find_all("img"):
        src = img_tag.get("src")
        if src: all_images.add(src)

    # 3. Meta Tags
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "")
        if prop and "og:video" in prop: all_videos.add(meta.get("content"))
        if prop and "og:image" in prop: all_images.add(meta.get("content"))

    # 4. Regex fallback
    all_videos.update([r.replace("\\/", "/").replace("\\u0025", "%") for r in FBCDN_VIDEO_RE.findall(html)])
    all_images.update([r.replace("\\/", "/").replace("\\u0025", "%") for r in FBCDN_IMAGE_RE.findall(html)])

    # --- RANKING ---
    best_video = None
    if all_videos:
        valid_v = [v for v in all_videos if v and ("fbcdn" in v or "scontent" in v)]
        if valid_v:
            best_video = max(valid_v, key=lambda x: rank_url(x, True))
            if rank_url(best_video, True) < -10000: best_video = None

    best_image = None
    if all_images:
        valid_i = [i for i in all_images if i and ("fbcdn" in i or "scontent" in i)]
        if valid_i:
            best_image = max(valid_i, key=lambda x: rank_url(x, False))
            if rank_url(best_image, False) < -10000: best_image = None

    return best_image, best_video


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

            # --- Exhaustive Candidate Extraction & Ranking ---
            image_url, video_url = _extract_media_candidates(html)
            method = "ranked_candidates"

            if not image_url and not video_url:
                # Diagnostic logging: log snippet of the HTML to help debug "no media" states
                snippet = html[:1000].replace("\n", " ")
                
                # Check if keywords even exist in the raw text
                has_keywords = "fbcdn" in html or "scontent" in html or "video" in html
                
                logger.warning(
                    "snapshot_no_media_found", 
                    ad_id=ad_archive_id, 
                    html_size=len(html),
                    has_media_keywords=has_keywords,
                    html_snippet=snippet
                )
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
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
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
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
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