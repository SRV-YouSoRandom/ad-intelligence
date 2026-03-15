"""
Static media serving — serves downloaded ad creative files.
The frontend uses this to display ad creative images directly in the UI.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter()


@router.get("/media/{path:path}")
async def serve_media(path: str):
    """
    Serve a downloaded media file by relative path.
    Path is relative to MEDIA_STORAGE_PATH.
    
    Security: path is validated to be within MEDIA_STORAGE_PATH only.
    """
    # Resolve and validate the path to prevent directory traversal
    base = Path(settings.MEDIA_STORAGE_PATH).resolve()
    target = (base / path).resolve()

    # Security check: ensure target is within base
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Path not allowed")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")

    # Determine content type
    suffix = target.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(target),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )