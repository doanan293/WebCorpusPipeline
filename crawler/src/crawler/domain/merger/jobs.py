from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class CrawlJob:
    """One crawl asked for by hand: a start URL, its host, and how far it may spread."""

    id: str
    url: str
    host: str
    max_pages: int | None  # counts the start URL; None: no limit
    started_at: float
    last_activity: float
    queued: list[str] = field(default_factory=list)
    answered: set[str] = field(default_factory=set)  # queued URLs that came back
    saved: list[str] = field(default_factory=list)  # where they ended up, after redirects
    stopped: bool = False
    retries: int = 0


class CrawlJobs:
    """The jobs of this run (kept in memory: a restart of the merger forgets them).

    A URL belongs to the newest job that queued it, so crawling a link again starts over.
    A download that fails never comes back: after ``retry_seconds`` with nothing new the URLs still
    out are worth asking for again (``stalled``), and after ``idle_seconds`` the job is done.
    """

    def __init__(
        self, idle_seconds: float = 60.0, retry_seconds: float = 15.0, max_retries: int = 2
    ) -> None:
        self.idle_seconds = idle_seconds
        self.retry_seconds = retry_seconds
        self.max_retries = max_retries
        self.jobs: dict[str, CrawlJob] = {}
        self.owners: dict[str, CrawlJob] = {}

    def start(self, url: str, host: str, max_pages: int | None, now: float) -> CrawlJob:
        job = CrawlJob(uuid4().hex[:12], url, host, max_pages, now, now, queued=[url])
        self.jobs[job.id] = job
        self.owners[url] = job
        return job

    def get(self, job_id: str) -> CrawlJob | None:
        return self.jobs.get(job_id)

    def job_of(self, url: str) -> CrawlJob | None:
        return self.owners.get(url)

    def record(
        self, job: CrawlJob, queued_url: str, saved_url: str, links: Sequence[str], now: float
    ) -> list[str]:
        """Note a page of the job as saved; return its links to queue, within the budget."""
        job.last_activity = now
        job.answered.add(queued_url)
        if saved_url not in job.saved:
            job.saved.append(saved_url)
        if job.stopped:
            return []
        known = set(job.queued)
        fresh = [link for link in dict.fromkeys(links) if link not in known]
        if job.max_pages is not None:
            fresh = fresh[: max(0, job.max_pages - len(job.queued))]
        job.queued.extend(fresh)
        for link in fresh:
            self.owners[link] = job
        return fresh

    def stop(self, job_id: str) -> CrawlJob | None:
        job = self.jobs.get(job_id)
        if job is not None:
            job.stopped = True
        return job

    def stalled(self, now: float) -> list[tuple[CrawlJob, list[str]]]:
        """Jobs quiet for ``retry_seconds`` with retries left, and the URLs to ask for again."""
        found = []
        for job in self.jobs.values():
            if (
                self.done(job, now)
                or job.retries >= self.max_retries
                or now - job.last_activity < self.retry_seconds
            ):
                continue
            job.retries += 1
            job.last_activity = now
            found.append((job, [url for url in job.queued if url not in job.answered]))
        return found

    def done(self, job: CrawlJob, now: float) -> bool:
        return (
            job.stopped
            or len(job.answered) >= len(job.queued)
            or now - job.last_activity >= self.idle_seconds
        )
