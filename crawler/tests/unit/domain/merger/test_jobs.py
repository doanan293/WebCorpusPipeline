from __future__ import annotations

from crawler.domain.merger.jobs import CrawlJobs

ROOT = "https://a.hust.edu.vn/"
L1, L2, L3, L4 = (f"https://a.hust.edu.vn/{n}" for n in range(1, 5))


def test_start_queues_root() -> None:
    assert CrawlJobs().start(ROOT, "a.hust.edu.vn", 5, 0.0).queued == [ROOT]


def test_record_respects_budget() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", 3, 0.0)
    assert jobs.record(job, ROOT, ROOT, [L1, L2, L3, L4], 1.0) == [L1, L2]
    assert jobs.record(job, L1, L1, [L3], 2.0) == []
    assert job.queued == [ROOT, L1, L2]


def test_unlimited_budget_takes_every_link() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.record(job, ROOT, ROOT, [L1, L2, L3, L4], 1.0) == [L1, L2, L3, L4]


def test_record_skips_already_queued() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.record(job, ROOT, ROOT, [ROOT, L1, L1], 1.0) == [L1]
    assert jobs.record(job, L1, L1, [ROOT, L1, L2], 2.0) == [L2]


def test_saved_counts_each_url_once() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [], 1.0)
    jobs.record(job, ROOT, ROOT, [], 2.0)
    assert job.saved == [ROOT]


def test_stop_blocks_new_links_and_marks_done() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [L1], 1.0)
    assert jobs.done(job, 1.0) is False
    assert jobs.stop(job.id) is job
    assert jobs.record(job, L1, L1, [L2], 2.0) == []
    assert jobs.done(job, 2.0) is True


def test_stop_unknown_job_is_none() -> None:
    assert CrawlJobs().stop("nope") is None


def test_done_when_all_queued_saved() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.done(job, 0.0) is False
    jobs.record(job, ROOT, ROOT, [], 1.0)
    assert jobs.done(job, 1.0) is True


def test_done_after_idle() -> None:
    jobs = CrawlJobs(idle_seconds=60.0)
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [L1], 10.0)  # L1 never comes back (the download failed)
    assert jobs.done(job, 69.0) is False
    assert jobs.done(job, 70.0) is True


def test_job_of_finds_newest_job_for_url() -> None:
    jobs = CrawlJobs()
    jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    second = jobs.start(ROOT, "a.hust.edu.vn", None, 1.0)
    assert jobs.job_of(ROOT) is second
    assert jobs.job_of("https://other/") is None
    assert jobs.get(second.id) is second


def test_job_with_retries_spent_is_done_a_minute_after_its_last_page() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [L1], 0.0)  # L1 never comes back (the site blocks it)
    assert [urls for _, urls in jobs.stalled(15.0)] == [[L1]]
    assert [urls for _, urls in jobs.stalled(30.0)] == [[L1]]
    assert jobs.done(job, 59.0) is False
    assert jobs.done(job, 60.0) is True


def test_redirected_pages_count_as_answered() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, L1, [], 1.0)  # ROOT redirected to L1
    assert job.saved == [L1]
    assert jobs.done(job, 1.0) is True


def test_stalled_job_retries_unanswered_urls_a_limited_number_of_times() -> None:
    jobs = CrawlJobs(idle_seconds=60.0, retry_seconds=15.0, max_retries=2)
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [L1, L2], 1.0)
    jobs.record(job, L1, L1, [], 2.0)
    assert jobs.stalled(16.0) == []
    assert jobs.stalled(17.0) == [(job, [L2])]
    assert jobs.stalled(18.0) == []  # the retry counts as activity
    assert jobs.stalled(32.0) == [(job, [L2])]
    assert jobs.stalled(100.0) == []  # out of retries
    assert jobs.done(job, 92.0) is True


def test_stalled_skips_finished_and_stopped_jobs() -> None:
    jobs = CrawlJobs(retry_seconds=15.0)
    finished = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(finished, ROOT, ROOT, [], 1.0)
    stopped = jobs.start(L1, "a.hust.edu.vn", None, 0.0)
    jobs.stop(stopped.id)
    assert jobs.stalled(50.0) == []


def test_link_back_to_a_redirected_page_is_not_queued_again() -> None:
    jobs = CrawlJobs()
    job = jobs.start("http://a.hust.edu.vn/", "a.hust.edu.vn", 2, 0.0)
    fresh = jobs.record(job, "http://a.hust.edu.vn/", ROOT, [ROOT, L1], 1.0)
    assert fresh == [L1]


def test_done_is_final_even_if_a_record_comes_late() -> None:
    jobs = CrawlJobs(idle_seconds=30.0)
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    assert jobs.done(job, 30.0) is True
    assert jobs.record(job, ROOT, ROOT, [L1], 31.0) == []
    assert job.saved == [ROOT]
    assert jobs.done(job, 31.0) is True


def test_unanswered_lists_urls_still_out() -> None:
    jobs = CrawlJobs()
    job = jobs.start(ROOT, "a.hust.edu.vn", None, 0.0)
    jobs.record(job, ROOT, ROOT, [L1, L2], 1.0)
    jobs.record(job, L1, L1, [], 2.0)
    assert jobs.unanswered(job) == [L2]
