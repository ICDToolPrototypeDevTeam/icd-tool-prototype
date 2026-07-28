import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from app.models import JobStatus


class Job:
    def __init__(self, kind: Literal["v3", "v4"] = "v3"):
        self.job_id: str = str(uuid.uuid4())
        self.kind: Literal["v3", "v4"] = kind
        self.status: JobStatus = JobStatus.PENDING
        self.message: Optional[str] = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.result: Optional[dict] = None

    def update(self, status: JobStatus, message: Optional[str] = None):
        self.status = status
        if message is not None:
            self.message = message
        self.updated_at = datetime.now(timezone.utc)


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create_job(self, kind: Literal["v3", "v4"] = "v3") -> Job:
        """Create a new Job. `kind="v3"` 默认；V4 路由显式传 `kind="v4"` 以做分发。"""
        job = Job(kind=kind)
        self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())


job_manager = JobManager()
