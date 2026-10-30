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
    finished: bool = False  # once done, late records no longer spread: nobody is watching
    retries: int = 0
    given_up: set[str] = field(default_factory=set)  # never came back, do not count in the budget
    waiting: list[str] = field(default_factory=list)  # links over the budget, for their places
    refills: int = 0


class CrawlJobs:
    """The jobs of this run (kept in memory: a restart of the merger forgets them).

    A URL belongs to the newest job that queued it, so crawling a link again starts over.
    A download that fails never comes back: after ``retry_seconds`` with nothing new the URLs still
    out are worth asking for again (``stalled``), then they give their places in the budget to links
    waiting (``max_refills`` times, so a site blocking every page still ends). After
    ``idle_seconds`` with nothing new at all the job is done.
    """

    def __init__(
        self,
        idle_seconds: float = 30.0,
        retry_seconds: float = 15.0,
        max_retries: int = 2,
        max_refills: int = 3,
    ) -> None:
        self.idle_seconds = idle_seconds
        self.retry_seconds = retry_seconds
        self.max_retries = max_retries
        self.max_refills = max_refills
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
        finished = self.done(job, now)  # before this answer, which may be the last one out
        job.last_activity = now
        job.answered.add(queued_url)
        job.given_up.discard(queued_url)
        if saved_url not in job.saved:
            job.saved.append(saved_url)
        if finished:
            return []
        known = {*job.queued, *job.saved, *job.waiting}  # a redirect's target may be linked back to
        job.waiting.extend(link for link in dict.fromkeys(links) if link not in known)
        return self._take_waiting(job)

    def _take_waiting(self, job: CrawlJob) -> list[str]:
        """Queue the links waiting, as far as the budget has room."""
        room = len(job.waiting)
        if job.max_pages is not None:
            room = max(0, job.max_pages - (len(job.queued) - len(job.given_up)))
        known = {*job.queued, *job.saved}
        fresh = [link for link in job.waiting[:room] if link not in known]
        del job.waiting[:room]
        job.queued.extend(fresh)
        for link in fresh:
            self.owners[link] = job
        return fresh

    def unanswered(self, job: CrawlJob) -> list[str]:
        return [url for url in job.queued if url not in job.answered and url not in job.given_up]

    def stop(self, job_id: str) -> CrawlJob | None:
        job = self.jobs.get(job_id)
        if job is not None:
            job.stopped = True
        return job

    def stalled(self, now: float) -> list[tuple[CrawlJob, list[str]]]:
        """Jobs quiet for ``retry_seconds``, and the URLs to ask for: again, or instead."""
        found = []
        for job in self.jobs.values():
            if self.done(job, now) or now - job.last_activity < self.retry_seconds:
                continue
            if job.retries < self.max_retries:
                job.retries += 1
                job.last_activity = now
                found.append((job, self.unanswered(job)))
            elif job.waiting and job.refills < self.max_refills:
                job.refills += 1
                job.retries = 0
                job.last_activity = now
                job.given_up.update(self.unanswered(job))
                if fresh := self._take_waiting(job):
                    found.append((job, fresh))
        return found

    def done(self, job: CrawlJob, now: float) -> bool:
        job.finished = job.finished or (
            job.stopped
            or len(job.answered | job.given_up) >= len(job.queued)
            or now - job.last_activity >= self.idle_seconds
        )
        return job.finished
