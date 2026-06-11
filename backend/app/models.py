from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'


class AnalyzeResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobResultSummary(BaseModel):
    requirement_count: int
    difference_count: int


class JobOutputs(BaseModel):
    requirements_docx: bool
    difference_report_docx: bool


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    summary: JobResultSummary
    outputs: JobOutputs