"""Pydantic schemas for job-related responses."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    """Response body for GET /api/v1/jobs/{job_id}/status."""
    job_id: uuid.UUID
    job_type: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
