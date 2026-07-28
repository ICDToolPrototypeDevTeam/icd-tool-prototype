# -*- coding: utf-8 -*-
"""In-memory job lifecycle management for CLI pipeline runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job:
    """A single pipeline execution job."""

    def __init__(self):
        self.job_id: str = str(uuid.uuid4())
        self.status: JobStatus = JobStatus.PENDING
        self.message: Optional[str] = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.result: Optional[dict] = None

    def update(self, status: JobStatus, message: Optional[str] = None) -> None:
        self.status = status
        if message is not None:
            self.message = message
        self.updated_at = datetime.now(timezone.utc)


class JobManager:
    """In-memory job registry. CLI runs typically produce one job."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(self) -> Job:
        job = Job()
        self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())


# Module-level singleton
job_manager = JobManager()
