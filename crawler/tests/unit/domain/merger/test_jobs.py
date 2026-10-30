from __future__ import annotations

from crawler.domain.merger.jobs import CrawlJobs

ROOT = "https://a.hust.edu.vn/"
L1, L2, L3, L4 = (f"https://a.hust.edu.vn/{n}" for n in range(1, 5))


def test_start_queues_root() -> None:
    assert CrawlJobs().start(ROOT, "a.hust.edu.vn", 5, 0.0).queued == [ROOT]


def test_record_respects_budget() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", 3, 0.0)
    assert jobs.record(job, ROOT, [L1, L2, L3, L4], 1.0) == [L1, L2]
    assert jobs.record(job, L1, [L3], 2.0) == []
    assert job.queued == [ROOT, L1, L2]


def test_unlimited_budget_takes_every_link() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.record(job, ROOT, [L1, L2, L3, L4], 1.0) == [L1, L2, L3, L4]


def test_record_skips_already_queued() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.record(job, ROOT, [ROOT, L1, L1], 1.0) == [L1]
    assert jobs.record(job, L1, [ROOT, L1, L2], 2.0) == [L2]


def test_saved_counts_each_url_once() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, [], 1.0)
    jobs.record(job, ROOT, [], 2.0)
    assert job.saved == [ROOT]


def test_stop_blocks_new_links_and_marks_done() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, [L1], 1.0)
    assert jobs.done(job, 1.0) is False
    assert jobs.stop(job.id) is job
    assert jobs.record(job, L1, [L2], 2.0) == []
    assert jobs.done(job, 2.0) is True


def test_stop_unknown_job_is_none() -> None:
    assert CrawlJobs().stop("nope") is None


def test_done_when_all_queued_saved() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.done(job, 0.0) is False
    jobs.record(job, ROOT, [], 1.0)
    assert jobs.done(job, 1.0) is True


def test_done_after_idle() -> None:
    jobs = CrawlJobs(idle_seconds=60.0)
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, [L1], 10.0)  # L1 never comes back (the download failed)
    assert jobs.done(job, 69.0) is False
    assert jobs.done(job, 70.0) is True


def test_job_of_finds_newest_job_for_url() -> None:
    jobs = CrawlJobs()
    jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    second = jobs.start(ROOT, "a.hust.edu.vn", None, 1.0)
    assert jobs.job_of(ROOT) is second
    assert jobs.job_of("https://other/") is None
    assert jobs.get(second.id) is second
