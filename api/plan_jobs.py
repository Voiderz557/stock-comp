"""Bounded, owner-scoped live planning jobs for a single free API instance.

Jobs hold no credentials in their public state. Results are saved by the normal
version-checked writer, not by polling. Restart loses job progress, not accounts.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException

LOGGER = logging.getLogger(__name__)
JOB_TIMEOUT_SECONDS = 600
JOB_RETENTION_SECONDS = 900
MAX_RETAINED_JOBS = 50


@dataclass
class PlanJob:
    owner: str
    request_id: str
    fingerprint: tuple
    id: str = field(default_factory=lambda: str(uuid4()))
    started: float = field(default_factory=monotonic)
    status: str = "RUNNING"
    stage: str = "Starting market scan"
    detail: str = ""
    error: str | None = None
    finished: float | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self):
        return {"id": self.id, "status": self.status, "stage": self.stage,
                "detail": self.detail, "elapsed_seconds": round((self.finished or monotonic()) - self.started, 1),
                "error": self.error}


class PlanJobs:
    def __init__(self, timeout=JOB_TIMEOUT_SECONDS):
        self.timeout = timeout
        self.jobs: dict[str, PlanJob] = {}
        self.active: PlanJob | None = None

    def get(self, owner, job_id):
        job = self.jobs.get(str(job_id))
        if job is None or job.owner != owner:
            raise HTTPException(404, "Scan progress expired or the service restarted. Refresh saved plans before starting another scan.")
        return job

    def start(self, owner, request_id, fingerprint, execute):
        now = monotonic()
        self.jobs = {key: job for key, job in self.jobs.items()
                     if job.finished is None or now - job.finished < JOB_RETENTION_SECONDS}
        for job in self.jobs.values():
            if job.owner == owner and job.request_id == request_id:
                if job.fingerprint != fingerprint:
                    raise HTTPException(409, "This scan retry identifier was already used with different settings.")
                return job
        if self.active is not None:
            if self.active.owner == owner and self.active.fingerprint == fingerprint and self.active.status == "RUNNING":
                return self.active
            raise HTTPException(429, "A scan is already using the free service. Wait for it to finish before starting another.")
        if len(self.jobs) >= MAX_RETAINED_JOBS:
            oldest = min(self.jobs.values(), key=lambda job: job.started)
            self.jobs.pop(oldest.id)
        job = PlanJob(owner, request_id, fingerprint)
        self.jobs[job.id] = job
        self.active = job
        job.task = asyncio.create_task(self._run(job, execute))
        return job

    async def _run(self, job, execute):
        loop = asyncio.get_running_loop()

        def progress(stage, detail=""):
            def update():
                if job.status == "RUNNING":
                    job.stage, job.detail = str(stage)[:120], str(detail)[:300]
            loop.call_soon_threadsafe(update)

        try:
            # execute owns the non-cancellable price thread. On timeout it waits
            # for that thread to drain without saving, keeping the capacity slot.
            await execute(progress, self.timeout, job)
            job.status, job.stage = "SUCCEEDED", "Plan saved"
            job.detail = "Reloading the saved account under your current session."
        except asyncio.CancelledError:
            job.status, job.stage = "FAILED", "Service interrupted"
            job.error = "The service stopped before completion was confirmed. Refresh saved plans before retrying."
            raise
        except HTTPException as error:
            job.status, job.stage = "FAILED", "Scan stopped"
            job.error = str(error.detail)[:600]
        except ValueError as error:
            job.status, job.stage = "FAILED", "Market data unavailable"
            job.error = str(error)[:600]
        except Exception as error:
            LOGGER.error("plan_job_failed category=%s", type(error).__name__)
            job.status, job.stage = "FAILED", "Scan failed"
            job.error = "The scan could not be completed. No result was confirmed; refresh saved plans before retrying."
        finally:
            job.finished = job.finished or monotonic()
            if self.active is job:
                self.active = None
